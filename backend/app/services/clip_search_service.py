"""
CLIP Search Service for VisionAI.

Provides the core business logic for CLIP-powered natural language video
search, including frame indexing, text/image search with pgvector,
search history tracking, and indexing status reporting.

All database operations are async and use the project's standard
``AsyncSession`` pattern.
"""

from __future__ import annotations

import io
import time
import uuid
from datetime import datetime, timezone
from typing import Any, Optional

import cv2
import numpy as np
import structlog
from sqlalchemy import and_, delete, func, select, text
from sqlalchemy.ext.asyncio import AsyncSession

from app.config import get_settings
from app.models.camera import Camera
from app.models.clip_search import FrameEmbedding, SearchQuery
from app.models.recording import Recording

logger = structlog.stdlib.get_logger(__name__)
settings = get_settings()


# ── Singleton encoder accessor ────────────────────────────────────────────

def _get_encoder():
    """Lazily import and return the CLIPEncoder singleton.

    Deferred import prevents loading ONNX models at module import time,
    which would fail in migration or CLI contexts.
    """
    from app.cv.recognizers.clip_encoder import get_clip_encoder
    return get_clip_encoder()


# ── Frame Indexing ────────────────────────────────────────────────────────


async def index_frame(
    db: AsyncSession,
    org_id: uuid.UUID,
    camera_id: uuid.UUID,
    frame: np.ndarray,
    timestamp: datetime,
    recording_id: uuid.UUID,
    frame_number: int,
    metadata: dict[str, Any] | None = None,
) -> FrameEmbedding:
    """Encode a single frame and store its embedding in pgvector.

    Generates a CLIP embedding for the frame, creates a thumbnail in
    MinIO, and inserts the embedding record into the database.

    Args:
        db: Async database session.
        org_id: Organization that owns the camera.
        camera_id: Camera that captured the frame.
        frame: BGR image as a NumPy array.
        timestamp: When the frame was captured.
        recording_id: Recording this frame belongs to.
        frame_number: Sequential frame number within the recording.
        metadata: Optional dict with detected objects, scene info, etc.

    Returns:
        The created FrameEmbedding instance.
    """
    encoder = _get_encoder()

    # Encode frame to CLIP embedding
    embedding = encoder.encode_image(frame)

    # Generate and upload thumbnail
    thumbnail_path = await _generate_thumbnail(
        frame=frame,
        org_id=org_id,
        camera_id=camera_id,
        timestamp=timestamp,
    )

    frame_embedding = FrameEmbedding(
        id=uuid.uuid4(),
        org_id=org_id,
        camera_id=camera_id,
        recording_id=recording_id,
        frame_number=frame_number,
        timestamp=timestamp,
        embedding=embedding.tolist(),
        thumbnail_path=thumbnail_path,
        metadata_json=metadata,
    )

    db.add(frame_embedding)
    await db.flush()

    logger.debug(
        "clip_search.frame_indexed",
        camera_id=str(camera_id),
        frame_number=frame_number,
        timestamp=timestamp.isoformat(),
    )

    return frame_embedding


async def index_recording(
    db: AsyncSession,
    recording_id: uuid.UUID,
    fps: float = 1.0,
    batch_size: int = 8,
) -> dict[str, Any]:
    """Extract frames from a recording at the specified FPS and index them.

    Opens the recording video file, extracts frames at the target rate,
    encodes them in batches using the CLIP visual encoder, and bulk-inserts
    the embeddings into pgvector.

    Args:
        db: Async database session.
        recording_id: Recording to index.
        fps: Frames per second to extract (default 1.0).
        batch_size: Number of frames per encoding batch.

    Returns:
        Dict with indexing statistics.

    Raises:
        ValueError: If the recording is not found.
    """
    # Load recording
    result = await db.execute(
        select(Recording).where(Recording.id == recording_id)
    )
    recording = result.scalar_one_or_none()
    if recording is None:
        raise ValueError(f"Recording {recording_id} not found")

    encoder = _get_encoder()
    video_path = recording.file_path

    # Open video file
    cap = cv2.VideoCapture(video_path)
    if not cap.isOpened():
        logger.error(
            "clip_search.video_open_failed",
            recording_id=str(recording_id),
            path=video_path,
        )
        return {
            "recording_id": str(recording_id),
            "status": "error",
            "message": f"Could not open video file: {video_path}",
            "frames_indexed": 0,
        }

    video_fps = cap.get(cv2.CAP_PROP_FPS) or 25.0
    total_frames = int(cap.get(cv2.CAP_PROP_FRAME_COUNT))
    frame_interval = max(1, int(video_fps / fps))

    logger.info(
        "clip_search.indexing_recording",
        recording_id=str(recording_id),
        video_fps=video_fps,
        total_frames=total_frames,
        frame_interval=frame_interval,
    )

    frames_batch: list[np.ndarray] = []
    frame_numbers: list[int] = []
    timestamps: list[datetime] = []
    frames_indexed = 0
    frame_idx = 0

    start_time = recording.start_time or datetime.now(timezone.utc)

    try:
        while True:
            ret, frame = cap.read()
            if not ret:
                break

            if frame_idx % frame_interval == 0:
                frame_ts = start_time + __import__("datetime").timedelta(
                    seconds=frame_idx / video_fps
                )
                frames_batch.append(frame)
                frame_numbers.append(frame_idx)
                timestamps.append(frame_ts)

                # Process batch when full
                if len(frames_batch) >= batch_size:
                    count = await _index_frame_batch(
                        db=db,
                        encoder=encoder,
                        org_id=recording.org_id,
                        camera_id=recording.camera_id,
                        recording_id=recording_id,
                        frames=frames_batch,
                        frame_numbers=frame_numbers,
                        timestamps=timestamps,
                    )
                    frames_indexed += count
                    frames_batch = []
                    frame_numbers = []
                    timestamps = []

            frame_idx += 1

        # Process remaining frames
        if frames_batch:
            count = await _index_frame_batch(
                db=db,
                encoder=encoder,
                org_id=recording.org_id,
                camera_id=recording.camera_id,
                recording_id=recording_id,
                frames=frames_batch,
                frame_numbers=frame_numbers,
                timestamps=timestamps,
            )
            frames_indexed += count

    finally:
        cap.release()

    await db.flush()

    logger.info(
        "clip_search.recording_indexed",
        recording_id=str(recording_id),
        frames_indexed=frames_indexed,
        total_video_frames=total_frames,
    )

    return {
        "recording_id": str(recording_id),
        "status": "completed",
        "frames_indexed": frames_indexed,
        "total_video_frames": total_frames,
        "fps_used": fps,
    }


async def _index_frame_batch(
    db: AsyncSession,
    encoder: Any,
    org_id: uuid.UUID,
    camera_id: uuid.UUID,
    recording_id: uuid.UUID,
    frames: list[np.ndarray],
    frame_numbers: list[int],
    timestamps: list[datetime],
) -> int:
    """Encode and insert a batch of frames.

    Args:
        db: Database session.
        encoder: CLIPEncoder instance.
        org_id: Organization ID.
        camera_id: Camera ID.
        recording_id: Recording ID.
        frames: List of BGR frame arrays.
        frame_numbers: Corresponding frame numbers.
        timestamps: Corresponding timestamps.

    Returns:
        Number of frames successfully indexed.
    """
    try:
        embeddings = encoder.encode_images_batch(frames)
    except Exception as exc:
        logger.error(
            "clip_search.batch_encode_failed",
            error=str(exc),
            batch_size=len(frames),
        )
        return 0

    count = 0
    for i, (embedding, frame_num, ts) in enumerate(
        zip(embeddings, frame_numbers, timestamps)
    ):
        thumbnail_path = await _generate_thumbnail(
            frame=frames[i],
            org_id=org_id,
            camera_id=camera_id,
            timestamp=ts,
        )

        frame_emb = FrameEmbedding(
            id=uuid.uuid4(),
            org_id=org_id,
            camera_id=camera_id,
            recording_id=recording_id,
            frame_number=frame_num,
            timestamp=ts,
            embedding=embedding.tolist(),
            thumbnail_path=thumbnail_path,
            metadata_json=None,
        )
        db.add(frame_emb)
        count += 1

    return count


# ── Search Operations ─────────────────────────────────────────────────────


async def search_by_text(
    db: AsyncSession,
    org_id: uuid.UUID,
    user_id: uuid.UUID,
    query_text: str,
    cameras: list[uuid.UUID] | None = None,
    date_from: datetime | None = None,
    date_to: datetime | None = None,
    time_of_day_start: str | None = None,
    time_of_day_end: str | None = None,
    top_k: int = 50,
    min_similarity: float = 0.15,
) -> dict[str, Any]:
    """Search video frames using a natural language text query.

    Encodes the query text into a CLIP embedding, then performs a
    pgvector cosine similarity search against all indexed frame
    embeddings with optional camera and date filters.

    Args:
        db: Async database session.
        org_id: Organization to search within.
        user_id: User performing the search (for history).
        query_text: Natural language search query.
        cameras: Optional list of camera IDs to filter.
        date_from: Optional start date filter.
        date_to: Optional end date filter.
        time_of_day_start: Optional time-of-day start filter (HH:MM).
        time_of_day_end: Optional time-of-day end filter (HH:MM).
        top_k: Maximum number of results.
        min_similarity: Minimum similarity threshold.

    Returns:
        Dict containing search results, query metadata, and timing.
    """
    search_start = time.monotonic()

    encoder = _get_encoder()
    query_embedding = encoder.encode_text(query_text)
    embedding_list = query_embedding.tolist()

    # Build pgvector cosine similarity query
    # cosine_distance = 1 - cosine_similarity
    # We use the <=> operator for cosine distance
    similarity_expr = (
        1 - FrameEmbedding.embedding.cosine_distance(embedding_list)
    )

    query = (
        select(
            FrameEmbedding,
            similarity_expr.label("similarity"),
            Camera.name.label("camera_name"),
        )
        .join(Camera, Camera.id == FrameEmbedding.camera_id)
        .where(FrameEmbedding.org_id == org_id)
    )

    # Apply filters
    if cameras:
        query = query.where(FrameEmbedding.camera_id.in_(cameras))

    if date_from:
        query = query.where(FrameEmbedding.timestamp >= date_from)

    if date_to:
        query = query.where(FrameEmbedding.timestamp <= date_to)

    if time_of_day_start and time_of_day_end:
        query = query.where(
            func.to_char(FrameEmbedding.timestamp, "HH24:MI").between(
                time_of_day_start, time_of_day_end
            )
        )
    elif time_of_day_start:
        query = query.where(
            func.to_char(FrameEmbedding.timestamp, "HH24:MI") >= time_of_day_start
        )
    elif time_of_day_end:
        query = query.where(
            func.to_char(FrameEmbedding.timestamp, "HH24:MI") <= time_of_day_end
        )

    # Order by similarity (descending) and limit
    query = query.order_by(similarity_expr.desc()).limit(top_k)

    result = await db.execute(query)
    rows = result.all()

    # Filter by minimum similarity and build results
    results = []
    for row in rows:
        frame_emb = row[0]
        similarity = float(row[1])
        camera_name = row[2]

        if similarity < min_similarity:
            continue

        metadata = frame_emb.metadata_json or {}
        detected_objects = metadata.get("detected_objects", [])
        scene_description = metadata.get("scene_description")

        # Build thumbnail URL
        thumbnail_url = None
        if frame_emb.thumbnail_path:
            thumbnail_url = _build_thumbnail_url(frame_emb.thumbnail_path)

        results.append({
            "frame_id": str(frame_emb.id),
            "camera_id": str(frame_emb.camera_id),
            "camera_name": camera_name,
            "recording_id": str(frame_emb.recording_id),
            "frame_number": frame_emb.frame_number,
            "timestamp": frame_emb.timestamp.isoformat(),
            "similarity_score": round(similarity, 4),
            "thumbnail_url": thumbnail_url,
            "detected_objects": detected_objects,
            "scene_description": scene_description,
        })

    search_duration_ms = round((time.monotonic() - search_start) * 1000, 2)

    # Log search query for history
    search_query = SearchQuery(
        id=uuid.uuid4(),
        org_id=org_id,
        user_id=user_id,
        query_text=query_text,
        query_type="text",
        query_embedding=embedding_list,
        result_count=len(results),
        search_duration_ms=search_duration_ms,
        filters_json={
            "cameras": [str(c) for c in cameras] if cameras else None,
            "date_from": date_from.isoformat() if date_from else None,
            "date_to": date_to.isoformat() if date_to else None,
            "time_of_day_start": time_of_day_start,
            "time_of_day_end": time_of_day_end,
            "top_k": top_k,
            "min_similarity": min_similarity,
        },
    )
    db.add(search_query)

    logger.info(
        "clip_search.text_search",
        query=query_text,
        results=len(results),
        duration_ms=search_duration_ms,
        org_id=str(org_id),
    )

    return {
        "results": results,
        "query": query_text,
        "total_matches": len(results),
        "search_duration_ms": search_duration_ms,
    }


async def search_by_image(
    db: AsyncSession,
    org_id: uuid.UUID,
    user_id: uuid.UUID,
    image: np.ndarray,
    cameras: list[uuid.UUID] | None = None,
    date_from: datetime | None = None,
    date_to: datetime | None = None,
    top_k: int = 50,
    min_similarity: float = 0.15,
) -> dict[str, Any]:
    """Search video frames using a query image.

    Encodes the query image into a CLIP embedding and performs pgvector
    cosine similarity search against indexed frame embeddings.

    Args:
        db: Async database session.
        org_id: Organization to search within.
        user_id: User performing the search.
        image: BGR query image as NumPy array.
        cameras: Optional list of camera IDs to filter.
        date_from: Optional start date filter.
        date_to: Optional end date filter.
        top_k: Maximum number of results.
        min_similarity: Minimum similarity threshold.

    Returns:
        Dict containing search results and timing metadata.
    """
    search_start = time.monotonic()

    encoder = _get_encoder()
    query_embedding = encoder.encode_image(image)
    embedding_list = query_embedding.tolist()

    similarity_expr = (
        1 - FrameEmbedding.embedding.cosine_distance(embedding_list)
    )

    query = (
        select(
            FrameEmbedding,
            similarity_expr.label("similarity"),
            Camera.name.label("camera_name"),
        )
        .join(Camera, Camera.id == FrameEmbedding.camera_id)
        .where(FrameEmbedding.org_id == org_id)
    )

    if cameras:
        query = query.where(FrameEmbedding.camera_id.in_(cameras))
    if date_from:
        query = query.where(FrameEmbedding.timestamp >= date_from)
    if date_to:
        query = query.where(FrameEmbedding.timestamp <= date_to)

    query = query.order_by(similarity_expr.desc()).limit(top_k)

    result = await db.execute(query)
    rows = result.all()

    results = []
    for row in rows:
        frame_emb = row[0]
        similarity = float(row[1])
        camera_name = row[2]

        if similarity < min_similarity:
            continue

        metadata = frame_emb.metadata_json or {}

        thumbnail_url = None
        if frame_emb.thumbnail_path:
            thumbnail_url = _build_thumbnail_url(frame_emb.thumbnail_path)

        results.append({
            "frame_id": str(frame_emb.id),
            "camera_id": str(frame_emb.camera_id),
            "camera_name": camera_name,
            "recording_id": str(frame_emb.recording_id),
            "frame_number": frame_emb.frame_number,
            "timestamp": frame_emb.timestamp.isoformat(),
            "similarity_score": round(similarity, 4),
            "thumbnail_url": thumbnail_url,
            "detected_objects": metadata.get("detected_objects", []),
            "scene_description": metadata.get("scene_description"),
        })

    search_duration_ms = round((time.monotonic() - search_start) * 1000, 2)

    # Log search query
    search_query = SearchQuery(
        id=uuid.uuid4(),
        org_id=org_id,
        user_id=user_id,
        query_text=None,
        query_type="image",
        query_embedding=embedding_list,
        result_count=len(results),
        search_duration_ms=search_duration_ms,
        filters_json={
            "cameras": [str(c) for c in cameras] if cameras else None,
            "date_from": date_from.isoformat() if date_from else None,
            "date_to": date_to.isoformat() if date_to else None,
            "top_k": top_k,
            "min_similarity": min_similarity,
        },
    )
    db.add(search_query)

    logger.info(
        "clip_search.image_search",
        results=len(results),
        duration_ms=search_duration_ms,
        org_id=str(org_id),
    )

    return {
        "results": results,
        "query": "[image query]",
        "total_matches": len(results),
        "search_duration_ms": search_duration_ms,
    }


# ── Search History ────────────────────────────────────────────────────────


async def get_search_history(
    db: AsyncSession,
    user_id: uuid.UUID,
    limit: int = 50,
) -> dict[str, Any]:
    """Retrieve the user's recent search queries.

    Args:
        db: Async database session.
        user_id: User whose history to retrieve.
        limit: Maximum number of history items.

    Returns:
        Dict with queries list and total count.
    """
    count_result = await db.execute(
        select(func.count()).select_from(SearchQuery).where(
            SearchQuery.user_id == user_id
        )
    )
    total = count_result.scalar() or 0

    result = await db.execute(
        select(SearchQuery)
        .where(SearchQuery.user_id == user_id)
        .order_by(SearchQuery.created_at.desc())
        .limit(limit)
    )
    queries = result.scalars().all()

    items = [
        {
            "id": str(q.id),
            "query": q.query_text,
            "query_type": q.query_type,
            "timestamp": q.created_at.isoformat(),
            "result_count": q.result_count,
            "search_duration_ms": q.search_duration_ms,
        }
        for q in queries
    ]

    return {
        "queries": items,
        "total": total,
    }


# ── Indexing Status ───────────────────────────────────────────────────────


async def get_indexing_status(
    db: AsyncSession,
    org_id: uuid.UUID,
) -> dict[str, Any]:
    """Get per-camera indexing progress for the organization.

    Returns the number of indexed frames, last indexed timestamp,
    and whether indexing is currently active for each camera.

    Args:
        db: Async database session.
        org_id: Organization to check.

    Returns:
        Dict with per-camera status and totals.
    """
    # Get all cameras for the org
    cameras_result = await db.execute(
        select(Camera).where(Camera.org_id == org_id, Camera.is_active.is_(True))
    )
    cameras = cameras_result.scalars().all()

    # Get frame counts and last indexed per camera
    stats_result = await db.execute(
        select(
            FrameEmbedding.camera_id,
            func.count(FrameEmbedding.id).label("frame_count"),
            func.max(FrameEmbedding.timestamp).label("last_indexed"),
            func.count(func.distinct(FrameEmbedding.recording_id)).label("recording_count"),
        )
        .where(FrameEmbedding.org_id == org_id)
        .group_by(FrameEmbedding.camera_id)
    )
    stats_rows = stats_result.all()

    stats_map: dict[uuid.UUID, dict] = {}
    for row in stats_rows:
        stats_map[row.camera_id] = {
            "frame_count": row.frame_count,
            "last_indexed": row.last_indexed,
            "recording_count": row.recording_count,
        }

    # Check Redis for active indexing tasks
    indexing_cameras = await _get_active_indexing_cameras()

    camera_statuses = []
    total_frames = 0

    for camera in cameras:
        stats = stats_map.get(camera.id, {})
        frame_count = stats.get("frame_count", 0)
        total_frames += frame_count

        # Estimate coverage hours: 1 frame per second = 3600 frames per hour
        coverage_hours = round(frame_count / 3600.0, 1)

        camera_statuses.append({
            "camera_id": str(camera.id),
            "camera_name": camera.name,
            "frames_indexed": frame_count,
            "recordings_indexed": stats.get("recording_count", 0),
            "last_indexed": (
                stats["last_indexed"].isoformat()
                if stats.get("last_indexed")
                else None
            ),
            "is_indexing": str(camera.id) in indexing_cameras,
            "coverage_hours": coverage_hours,
        })

    return {
        "cameras": camera_statuses,
        "total_frames": total_frames,
        "total_cameras": len([c for c in camera_statuses if c["frames_indexed"] > 0]),
    }


async def _get_active_indexing_cameras() -> set[str]:
    """Check Redis for cameras with active indexing tasks.

    Returns:
        Set of camera ID strings currently being indexed.
    """
    try:
        import redis.asyncio as aioredis
        from app.dependencies import get_redis

        redis_client = await get_redis()
        keys = []
        async for key in redis_client.scan_iter(
            match="visionai:clip:indexing:*", count=100
        ):
            camera_id = key.split(":")[-1] if isinstance(key, str) else key.decode().split(":")[-1]
            keys.append(camera_id)
        return set(keys)
    except Exception:
        return set()


# ── Index Deletion ────────────────────────────────────────────────────────


async def delete_index(
    db: AsyncSession,
    camera_id: uuid.UUID,
    org_id: uuid.UUID,
) -> dict[str, Any]:
    """Delete all frame embeddings for a camera.

    Args:
        db: Async database session.
        camera_id: Camera whose embeddings to delete.
        org_id: Organization that owns the camera.

    Returns:
        Dict with deletion statistics.
    """
    count_result = await db.execute(
        select(func.count()).select_from(FrameEmbedding).where(
            FrameEmbedding.camera_id == camera_id,
            FrameEmbedding.org_id == org_id,
        )
    )
    total = count_result.scalar() or 0

    await db.execute(
        delete(FrameEmbedding).where(
            FrameEmbedding.camera_id == camera_id,
            FrameEmbedding.org_id == org_id,
        )
    )
    await db.flush()

    logger.info(
        "clip_search.index_deleted",
        camera_id=str(camera_id),
        frames_deleted=total,
    )

    return {
        "camera_id": str(camera_id),
        "frames_deleted": total,
    }


# ── Thumbnail Generation ─────────────────────────────────────────────────


async def _generate_thumbnail(
    frame: np.ndarray,
    org_id: uuid.UUID,
    camera_id: uuid.UUID,
    timestamp: datetime,
    max_size: int = 320,
) -> str | None:
    """Generate a JPEG thumbnail and upload it to MinIO.

    Resizes the frame to fit within ``max_size`` pixels (preserving aspect
    ratio), encodes as JPEG, and uploads to the snapshots bucket.

    Args:
        frame: BGR image as NumPy array.
        org_id: Organization ID for the storage path.
        camera_id: Camera ID for the storage path.
        timestamp: Timestamp for the filename.
        max_size: Maximum dimension in pixels.

    Returns:
        MinIO object path, or None if upload fails.
    """
    try:
        h, w = frame.shape[:2]
        scale = min(max_size / w, max_size / h)
        if scale < 1.0:
            new_w = int(w * scale)
            new_h = int(h * scale)
            thumbnail = cv2.resize(frame, (new_w, new_h), interpolation=cv2.INTER_AREA)
        else:
            thumbnail = frame

        _, jpeg_bytes = cv2.imencode(
            ".jpg", thumbnail, [cv2.IMWRITE_JPEG_QUALITY, 80]
        )

        ts_str = timestamp.strftime("%Y%m%d_%H%M%S")
        unique_id = uuid.uuid4().hex[:8]
        object_name = (
            f"clip-thumbnails/{org_id}/{camera_id}/"
            f"{timestamp.strftime('%Y-%m-%d')}/{ts_str}_{unique_id}.jpg"
        )

        # Upload to MinIO
        from minio import Minio

        minio_client = Minio(
            settings.MINIO_ENDPOINT,
            access_key=settings.MINIO_ACCESS_KEY,
            secret_key=settings.MINIO_SECRET_KEY,
            secure=settings.MINIO_SECURE,
        )

        bucket = settings.MINIO_BUCKET_SNAPSHOTS
        if not minio_client.bucket_exists(bucket):
            minio_client.make_bucket(bucket)

        data = io.BytesIO(jpeg_bytes.tobytes())
        data_length = data.getbuffer().nbytes

        minio_client.put_object(
            bucket,
            object_name,
            data,
            length=data_length,
            content_type="image/jpeg",
        )

        return f"{bucket}/{object_name}"

    except Exception as exc:
        logger.debug(
            "clip_search.thumbnail_upload_failed",
            error=str(exc),
        )
        return None


def _build_thumbnail_url(thumbnail_path: str) -> str:
    """Build an accessible URL from a MinIO thumbnail path.

    Args:
        thumbnail_path: Path in format ``bucket/object_name``.

    Returns:
        Full URL to the thumbnail.
    """
    scheme = "https" if settings.MINIO_SECURE else "http"
    return f"{scheme}://{settings.MINIO_ENDPOINT}/{thumbnail_path}"
