"""
VisionAI Person Re-Identification Celery Tasks.

Provides background tasks for computing OSNet appearance embeddings,
cross-camera person matching, gallery management, and stale track
cleanup.  All tasks use the synchronous Celery interface with internal
async bridges for database and model access.
"""

from __future__ import annotations

import asyncio
import uuid
from datetime import datetime, timedelta, timezone
from typing import Any

import numpy as np
import structlog
from sqlalchemy import and_, delete, select, update

from app.config import get_settings
from app.workers.celery_app import celery_app

logger = structlog.stdlib.get_logger(__name__)
settings = get_settings()


def _run_async(coro):
    """Run an async coroutine from a synchronous Celery task context."""
    loop = asyncio.new_event_loop()
    try:
        return loop.run_until_complete(coro)
    finally:
        loop.close()


def _load_reid_encoder():
    """Load and return a singleton ReIDEncoder instance.

    The ONNX model is loaded lazily on first call and cached for the
    lifetime of the worker process.
    """
    import onnxruntime as ort
    from pathlib import Path

    from app.cv.recognizers.reid_encoder import ReIDEncoder

    model_path = Path(settings.MODEL_DIR) / "osnet_x1_0.onnx"
    if not model_path.exists():
        raise FileNotFoundError(
            f"OSNet ReID model not found at {model_path}. "
            f"Place the ONNX model in the MODEL_DIR directory."
        )

    providers = ["CUDAExecutionProvider", "CPUExecutionProvider"]
    if settings.INFERENCE_DEVICE == "cpu":
        providers = ["CPUExecutionProvider"]

    session = ort.InferenceSession(str(model_path), providers=providers)
    return ReIDEncoder(session)


# Module-level cache for the encoder (lazy init)
_encoder_cache: dict[str, Any] = {}


def _get_encoder():
    """Return the cached ReIDEncoder, loading it on first use."""
    if "encoder" not in _encoder_cache:
        _encoder_cache["encoder"] = _load_reid_encoder()
    return _encoder_cache["encoder"]


# ── Task: Compute ReID Embedding ─────────────────────────────────────────


@celery_app.task(
    name="app.workers.reid_tasks.compute_reid_embedding_task",
    bind=True,
    max_retries=3,
    default_retry_delay=15,
    queue="video",
)
def compute_reid_embedding_task(
    self,
    person_track_id: str,
    image_path: str,
) -> dict[str, Any]:
    """Extract an OSNet ReID embedding for a person track.

    Loads the person crop image from MinIO storage, runs it through the
    OSNet encoder, and persists the resulting 512-dimensional embedding
    vector in the ``person_tracks`` table.

    Args:
        person_track_id: UUID of the PersonTrack record.
        image_path: MinIO object key for the person crop image.

    Returns:
        Dict with the track ID, embedding dimension, and L2 norm.
    """
    log = logger.bind(
        person_track_id=person_track_id,
        image_path=image_path,
        task_id=self.request.id,
    )
    log.info("Computing ReID embedding for person track")

    try:
        result = _run_async(
            _compute_embedding_async(person_track_id, image_path)
        )
        log.info(
            "ReID embedding computed",
            embedding_dim=result.get("embedding_dim"),
            l2_norm=result.get("l2_norm"),
        )
        return result

    except FileNotFoundError as exc:
        log.error("ReID model or image not found", error=str(exc))
        raise

    except Exception as exc:
        log.error("ReID embedding computation failed", error=str(exc))
        raise self.retry(exc=exc)


async def _compute_embedding_async(
    person_track_id: str,
    image_path: str,
) -> dict[str, Any]:
    """Perform the async embedding computation within a database session."""
    import cv2

    from app.database import get_db_context
    from app.models.reid import PersonTrack
    from app.utils.storage import download_file_bytes

    encoder = _get_encoder()

    # Download person crop from MinIO
    image_bytes = await download_file_bytes(image_path)
    if not image_bytes:
        raise FileNotFoundError(f"Person crop image not found: {image_path}")

    # Decode image
    nparr = np.frombuffer(image_bytes, np.uint8)
    image = cv2.imdecode(nparr, cv2.IMREAD_COLOR)
    if image is None:
        raise ValueError(f"Failed to decode image: {image_path}")

    # Extract embedding
    embedding = encoder.extract_features(image)
    l2_norm = float(np.linalg.norm(embedding))

    # Persist to database
    async with get_db_context() as session:
        track_result = await session.execute(
            select(PersonTrack).where(
                PersonTrack.id == uuid.UUID(person_track_id)
            )
        )
        track = track_result.scalar_one_or_none()
        if track is None:
            raise ValueError(f"PersonTrack not found: {person_track_id}")

        track.embedding = embedding.tolist()
        track.thumbnail_path = image_path
        await session.flush()

    return {
        "person_track_id": person_track_id,
        "image_path": image_path,
        "embedding_dim": len(embedding),
        "l2_norm": round(l2_norm, 6),
        "status": "completed",
    }


# ── Task: Match Across Cameras ───────────────────────────────────────────


@celery_app.task(
    name="app.workers.reid_tasks.match_across_cameras_task",
    bind=True,
    max_retries=3,
    default_retry_delay=15,
    queue="video",
)
def match_across_cameras_task(
    self,
    embedding_list: list[float],
    org_id: str,
    threshold: float = 0.6,
    top_k: int = 10,
) -> dict[str, Any]:
    """Search for a matching person across all camera feeds.

    Takes a 512-dimensional embedding vector, queries all active
    person tracks in the organization, and returns the top-K matches
    ranked by cosine similarity.

    Args:
        embedding_list: The 512-dimensional query embedding as a list of floats.
        org_id: Organization UUID to scope the search.
        threshold: Maximum Euclidean distance threshold for a valid match.
        top_k: Maximum number of ranked results to return.

    Returns:
        Dict containing ranked match results with similarity scores.
    """
    log = logger.bind(
        org_id=org_id,
        threshold=threshold,
        top_k=top_k,
        task_id=self.request.id,
    )
    log.info("Matching person across cameras")

    try:
        query_embedding = np.array(embedding_list, dtype=np.float32)
        if query_embedding.shape != (512,):
            raise ValueError(
                f"Expected 512-dim embedding, got shape {query_embedding.shape}"
            )

        result = _run_async(
            _match_across_cameras_async(query_embedding, org_id, threshold, top_k)
        )
        log.info(
            "Cross-camera matching completed",
            matches_found=result.get("matches_found", 0),
        )
        return result

    except Exception as exc:
        log.error("Cross-camera matching failed", error=str(exc))
        raise self.retry(exc=exc)


async def _match_across_cameras_async(
    query_embedding: np.ndarray,
    org_id: str,
    threshold: float,
    top_k: int,
) -> dict[str, Any]:
    """Perform the async cross-camera matching within a database session."""
    from app.database import get_db_context
    from app.models.reid import PersonTrack

    encoder = _get_encoder()

    async with get_db_context() as session:
        # Fetch all active tracks with embeddings for this org
        tracks_q = select(PersonTrack).where(
            PersonTrack.org_id == uuid.UUID(org_id),
            PersonTrack.is_active.is_(True),
            PersonTrack.embedding.is_not(None),
        )
        result = await session.execute(tracks_q)
        tracks = result.scalars().all()

        if not tracks:
            return {
                "matches_found": 0,
                "matches": [],
                "total_tracks_searched": 0,
            }

        # Build gallery
        gallery: list[tuple[str, np.ndarray]] = []
        for track in tracks:
            emb = np.array(track.embedding, dtype=np.float32)
            if emb.shape == (512,):
                gallery.append((str(track.id), emb))

        # Rank matches
        ranked = encoder.rank_gallery(
            query_feat=query_embedding,
            gallery=gallery,
            top_k=top_k,
        )

        # Filter by distance threshold
        matches = []
        for m in ranked:
            if m["distance"] <= threshold:
                # Enrich with track metadata
                track_id = m["person_id"]
                track_obj = next(
                    (t for t in tracks if str(t.id) == track_id), None
                )
                match_info = {
                    "track_id": track_id,
                    "similarity": m["similarity"],
                    "distance": m["distance"],
                    "rank": m["rank"],
                }
                if track_obj:
                    match_info.update({
                        "camera_id": str(track_obj.camera_id),
                        "global_person_id": str(track_obj.global_person_id),
                        "first_seen": (
                            track_obj.first_seen.isoformat()
                            if track_obj.first_seen
                            else None
                        ),
                        "last_seen": (
                            track_obj.last_seen.isoformat()
                            if track_obj.last_seen
                            else None
                        ),
                        "thumbnail_path": track_obj.thumbnail_path,
                    })
                matches.append(match_info)

        return {
            "matches_found": len(matches),
            "matches": matches,
            "total_tracks_searched": len(gallery),
            "threshold": threshold,
        }


# ── Task: Build ReID Gallery ─────────────────────────────────────────────


@celery_app.task(
    name="app.workers.reid_tasks.build_reid_gallery_task",
    bind=True,
    max_retries=2,
    default_retry_delay=60,
    queue="video",
)
def build_reid_gallery_task(
    self,
    org_id: str,
) -> dict[str, Any]:
    """Build or rebuild the ReID gallery from all enrolled persons.

    Iterates over all person tracks in the organization that have valid
    embeddings, groups them by ``global_person_id``, computes centroid
    embeddings for each identity, and caches the gallery in Redis for
    fast runtime matching.

    Args:
        org_id: Organization UUID to build the gallery for.

    Returns:
        Dict with gallery size and build statistics.
    """
    log = logger.bind(org_id=org_id, task_id=self.request.id)
    log.info("Building ReID gallery")

    try:
        result = _run_async(_build_gallery_async(org_id))
        log.info(
            "ReID gallery built",
            gallery_size=result.get("gallery_size", 0),
            identities=result.get("unique_identities", 0),
        )
        return result

    except Exception as exc:
        log.error("ReID gallery build failed", error=str(exc))
        raise self.retry(exc=exc)


async def _build_gallery_async(org_id: str) -> dict[str, Any]:
    """Perform the async gallery build within a database session."""
    import json
    import redis.asyncio as aioredis

    from app.database import get_db_context
    from app.models.reid import PersonTrack

    async with get_db_context() as session:
        # Load all tracks with embeddings
        tracks_q = select(PersonTrack).where(
            PersonTrack.org_id == uuid.UUID(org_id),
            PersonTrack.embedding.is_not(None),
        )
        result = await session.execute(tracks_q)
        tracks = result.scalars().all()

        if not tracks:
            return {
                "gallery_size": 0,
                "unique_identities": 0,
                "tracks_processed": 0,
                "status": "completed",
            }

        # Group embeddings by global_person_id
        identity_embeddings: dict[str, list[np.ndarray]] = {}
        for track in tracks:
            pid = str(track.global_person_id)
            emb = np.array(track.embedding, dtype=np.float32)
            if emb.shape == (512,):
                if pid not in identity_embeddings:
                    identity_embeddings[pid] = []
                identity_embeddings[pid].append(emb)

        # Compute centroid embedding for each identity
        gallery: dict[str, list[float]] = {}
        for pid, embeddings in identity_embeddings.items():
            if embeddings:
                centroid = np.mean(embeddings, axis=0).astype(np.float32)
                # L2 normalize
                norm = np.linalg.norm(centroid)
                if norm > 1e-6:
                    centroid = centroid / norm
                gallery[pid] = centroid.tolist()

        # Cache in Redis
        try:
            redis_url = settings.REDIS_URL
            redis_client = aioredis.from_url(redis_url, decode_responses=False)
            cache_key = f"visionai:reid_gallery:{org_id}"

            # Serialize gallery as JSON
            gallery_serialized = json.dumps(gallery)
            await redis_client.set(
                cache_key,
                gallery_serialized,
                ex=86400,  # 24 hours TTL
            )
            await redis_client.close()

            logger.info(
                "ReID gallery cached in Redis",
                org_id=org_id,
                cache_key=cache_key,
                gallery_size=len(gallery),
            )
        except Exception as redis_exc:
            logger.warning(
                "Failed to cache ReID gallery in Redis",
                org_id=org_id,
                error=str(redis_exc),
            )

        return {
            "gallery_size": len(gallery),
            "unique_identities": len(gallery),
            "tracks_processed": len(tracks),
            "tracks_with_valid_embeddings": sum(
                len(v) for v in identity_embeddings.values()
            ),
            "status": "completed",
        }


# ── Task: Cleanup Stale Tracks ───────────────────────────────────────────


@celery_app.task(
    name="app.workers.reid_tasks.cleanup_stale_tracks",
    bind=True,
    max_retries=2,
    default_retry_delay=120,
    queue="maintenance",
)
def cleanup_stale_tracks(
    self,
    hours: int = 24,
) -> dict[str, Any]:
    """Remove stale tracking data older than the specified retention period.

    Marks inactive tracks whose ``last_seen`` timestamp is older than
    ``hours`` ago, and deletes delivery records for tracks that have
    been inactive for more than twice the retention period.

    Args:
        hours: Retention period in hours. Tracks inactive for longer
            than this are deactivated. Defaults to 24.

    Returns:
        Dict with counts of deactivated and deleted records.
    """
    log = logger.bind(hours=hours, task_id=self.request.id)
    log.info("Starting stale track cleanup")

    try:
        result = _run_async(_cleanup_stale_tracks_async(hours))
        log.info(
            "Stale track cleanup completed",
            deactivated=result.get("deactivated", 0),
            deleted=result.get("deleted", 0),
        )
        return result

    except Exception as exc:
        log.error("Stale track cleanup failed", error=str(exc))
        raise self.retry(exc=exc)


async def _cleanup_stale_tracks_async(hours: int) -> dict[str, Any]:
    """Perform the async stale track cleanup within a database session."""
    from app.database import get_db_context
    from app.models.reid import PersonTrack

    cutoff = datetime.now(timezone.utc) - timedelta(hours=hours)
    delete_cutoff = datetime.now(timezone.utc) - timedelta(hours=hours * 2)

    async with get_db_context() as session:
        # Deactivate stale active tracks
        deactivate_q = (
            update(PersonTrack)
            .where(
                PersonTrack.is_active.is_(True),
                PersonTrack.last_seen < cutoff,
            )
            .values(is_active=False)
        )
        deactivate_result = await session.execute(deactivate_q)
        deactivated_count = deactivate_result.rowcount

        # Delete very old inactive tracks (2x retention period)
        delete_q = delete(PersonTrack).where(
            PersonTrack.is_active.is_(False),
            PersonTrack.last_seen < delete_cutoff,
        )
        delete_result = await session.execute(delete_q)
        deleted_count = delete_result.rowcount

        logger.info(
            "Stale tracks processed",
            deactivated=deactivated_count,
            deleted=deleted_count,
            cutoff=cutoff.isoformat(),
            delete_cutoff=delete_cutoff.isoformat(),
        )

        return {
            "deactivated": deactivated_count,
            "deleted": deleted_count,
            "retention_hours": hours,
            "cutoff": cutoff.isoformat(),
            "delete_cutoff": delete_cutoff.isoformat(),
            "status": "completed",
        }
