"""
VisionAI CLIP Indexing Celery Tasks.

Background tasks for indexing video recordings and live camera streams
with CLIP embeddings for natural language search. Includes recording
indexing, live frame indexing, full reindex, and orphan cleanup.
"""

from __future__ import annotations

import asyncio
import os
import time
import uuid
from datetime import datetime, timedelta, timezone
from pathlib import Path
from typing import Any

import redis
import structlog
from sqlalchemy import delete, func, select, text

from app.config import get_settings
from app.workers.celery_app import celery_app

logger = structlog.stdlib.get_logger(__name__)
settings = get_settings()

_redis = redis.Redis.from_url(settings.REDIS_URL, decode_responses=True)

# Redis key prefixes for indexing state
INDEXING_PREFIX = "visionai:clip:indexing:"
INDEXING_PROGRESS_PREFIX = "visionai:clip:progress:"


def _run_async(coro):
    """Run an async coroutine from a synchronous Celery task context."""
    loop = asyncio.new_event_loop()
    try:
        return loop.run_until_complete(coro)
    finally:
        loop.close()


# ---------------------------------------------------------------------------
# index_recording_task - Index a recording's frames with CLIP
# ---------------------------------------------------------------------------


@celery_app.task(
    name="app.workers.clip_tasks.index_recording_task",
    bind=True,
    max_retries=3,
    default_retry_delay=30,
    soft_time_limit=3600,
    time_limit=7200,
    queue="video",
)
def index_recording_task(
    self,
    recording_id: str,
    fps: float = 1.0,
) -> dict[str, Any]:
    """Index a recording's frames with CLIP embeddings.

    Extracts frames from the recording video at the specified FPS,
    encodes each frame with the CLIP visual encoder, and stores the
    512-dimensional embeddings in pgvector for cosine similarity search.

    Args:
        recording_id: UUID of the recording to index.
        fps: Frames per second to extract (default 1.0).

    Returns:
        Dict with indexing statistics (frames indexed, duration, etc.).
    """
    log = logger.bind(recording_id=recording_id, task_id=self.request.id)
    log.info("Starting recording indexing", fps=fps)

    progress_key = f"{INDEXING_PROGRESS_PREFIX}{recording_id}"

    try:
        _redis.hset(
            progress_key,
            mapping={
                "status": "running",
                "started_at": datetime.now(timezone.utc).isoformat(),
                "recording_id": recording_id,
                "task_id": self.request.id or "",
                "fps": str(fps),
                "frames_indexed": "0",
            },
        )
        _redis.expire(progress_key, 86400)  # Expire after 24 hours

        async def _do_index():
            from app.database import get_db_context
            from app.services.clip_search_service import index_recording

            async with get_db_context() as session:
                result = await index_recording(
                    db=session,
                    recording_id=uuid.UUID(recording_id),
                    fps=fps,
                )
                return result

        result = _run_async(_do_index())

        _redis.hset(
            progress_key,
            mapping={
                "status": "completed",
                "completed_at": datetime.now(timezone.utc).isoformat(),
                "frames_indexed": str(result.get("frames_indexed", 0)),
            },
        )

        log.info(
            "Recording indexing completed",
            frames_indexed=result.get("frames_indexed", 0),
        )

        return result

    except Exception as exc:
        log.error("Recording indexing failed", error=str(exc))

        _redis.hset(
            progress_key,
            mapping={
                "status": "failed",
                "error": str(exc)[:500],
            },
        )

        raise self.retry(exc=exc)


# ---------------------------------------------------------------------------
# index_live_frames_task - Continuous indexing from live stream
# ---------------------------------------------------------------------------


@celery_app.task(
    name="app.workers.clip_tasks.index_live_frames_task",
    bind=True,
    max_retries=5,
    default_retry_delay=15,
    soft_time_limit=None,
    time_limit=None,
    queue="video",
)
def index_live_frames_task(
    self,
    camera_id: str,
    fps: float = 1.0,
    duration_seconds: int = 3600,
) -> dict[str, Any]:
    """Continuously index frames from a camera's live stream.

    Opens the camera's RTSP stream, captures frames at the target FPS,
    encodes them with CLIP, and stores embeddings in pgvector. Runs for
    up to ``duration_seconds`` (default 1 hour) before exiting.

    Args:
        camera_id: UUID of the camera to index.
        fps: Frames per second to capture and index.
        duration_seconds: Maximum runtime in seconds (default 3600).

    Returns:
        Dict with indexing statistics.
    """
    import cv2

    log = logger.bind(camera_id=camera_id, task_id=self.request.id)
    log.info("Starting live frame indexing", fps=fps, duration=duration_seconds)

    indexing_key = f"{INDEXING_PREFIX}{camera_id}"
    progress_key = f"{INDEXING_PROGRESS_PREFIX}live:{camera_id}"

    try:
        # Check if already indexing
        if _redis.exists(indexing_key):
            log.warning("Live indexing already active for camera")
            return {
                "camera_id": camera_id,
                "status": "already_running",
            }

        # Mark as indexing
        _redis.set(indexing_key, self.request.id or "active", ex=duration_seconds + 60)
        _redis.hset(
            progress_key,
            mapping={
                "status": "running",
                "started_at": datetime.now(timezone.utc).isoformat(),
                "camera_id": camera_id,
                "fps": str(fps),
                "frames_indexed": "0",
            },
        )
        _redis.expire(progress_key, duration_seconds + 3600)

        # Load camera info
        async def _get_camera_info():
            from app.database import get_db_context
            from app.models.camera import Camera

            async with get_db_context() as session:
                result = await session.execute(
                    select(
                        Camera.stream_url,
                        Camera.org_id,
                        Camera.name,
                    ).where(Camera.id == uuid.UUID(camera_id))
                )
                row = result.one_or_none()
                if row is None:
                    return None, None, None
                return row.stream_url, str(row.org_id), row.name

        stream_url, org_id, camera_name = _run_async(_get_camera_info())

        if stream_url is None:
            log.error("Camera not found")
            _redis.delete(indexing_key)
            return {"camera_id": camera_id, "status": "error", "message": "Camera not found"}

        # Decrypt stream URL if needed
        try:
            from app.utils.encryption import decrypt_string
            stream_url = decrypt_string(stream_url)
        except (ImportError, ValueError):
            pass

        # Open video stream
        cap = cv2.VideoCapture(stream_url)
        if not cap.isOpened():
            log.error("Failed to open camera stream", stream_url=stream_url[:50])
            _redis.delete(indexing_key)
            return {
                "camera_id": camera_id,
                "status": "error",
                "message": "Failed to open camera stream",
            }

        frame_interval = 1.0 / fps
        frames_indexed = 0
        start_time = time.monotonic()
        last_capture = 0.0

        # Create a virtual recording for live frames
        async def _create_virtual_recording():
            from app.database import get_db_context
            from app.models.recording import Recording, RecordingType

            rec_id = uuid.uuid4()
            async with get_db_context() as session:
                await session.execute(
                    text("""
                        INSERT INTO recordings (
                            id, org_id, camera_id, recording_type,
                            start_time, file_path,
                            created_at, updated_at
                        ) VALUES (
                            :id, :org_id, :camera_id, 'continuous',
                            :start_time, :file_path,
                            NOW(), NOW()
                        )
                    """),
                    {
                        "id": str(rec_id),
                        "org_id": org_id,
                        "camera_id": camera_id,
                        "start_time": datetime.now(timezone.utc),
                        "file_path": f"live-index/{camera_id}",
                    },
                )
            return rec_id

        virtual_recording_id = _run_async(_create_virtual_recording())

        try:
            while (time.monotonic() - start_time) < duration_seconds:
                # Check if task was revoked
                if not _redis.exists(indexing_key):
                    log.info("Indexing stopped via Redis key removal")
                    break

                current_time = time.monotonic()
                if (current_time - last_capture) < frame_interval:
                    import time as time_module
                    time_module.sleep(0.01)
                    continue

                ret, frame = cap.read()
                if not ret:
                    log.warning("Frame capture failed, retrying...")
                    cap.release()
                    import time as time_module
                    time_module.sleep(2)
                    cap = cv2.VideoCapture(stream_url)
                    if not cap.isOpened():
                        log.error("Stream reconnection failed")
                        break
                    continue

                last_capture = current_time
                now = datetime.now(timezone.utc)

                # Index this frame
                async def _index_single_frame(
                    _frame=frame, _ts=now, _frame_num=frames_indexed
                ):
                    from app.database import get_db_context
                    from app.services.clip_search_service import index_frame

                    async with get_db_context() as session:
                        await index_frame(
                            db=session,
                            org_id=uuid.UUID(org_id),
                            camera_id=uuid.UUID(camera_id),
                            frame=_frame,
                            timestamp=_ts,
                            recording_id=virtual_recording_id,
                            frame_number=_frame_num,
                        )

                try:
                    _run_async(_index_single_frame())
                    frames_indexed += 1

                    # Update progress every 10 frames
                    if frames_indexed % 10 == 0:
                        _redis.hset(
                            progress_key,
                            "frames_indexed",
                            str(frames_indexed),
                        )
                        log.debug(
                            "Live indexing progress",
                            frames_indexed=frames_indexed,
                        )

                except Exception as frame_exc:
                    log.warning(
                        "Failed to index frame",
                        error=str(frame_exc),
                        frame_num=frames_indexed,
                    )

        finally:
            cap.release()
            _redis.delete(indexing_key)

        # Update progress
        _redis.hset(
            progress_key,
            mapping={
                "status": "completed",
                "completed_at": datetime.now(timezone.utc).isoformat(),
                "frames_indexed": str(frames_indexed),
            },
        )

        elapsed = round(time.monotonic() - start_time, 1)

        log.info(
            "Live frame indexing completed",
            frames_indexed=frames_indexed,
            elapsed_seconds=elapsed,
        )

        return {
            "camera_id": camera_id,
            "status": "completed",
            "frames_indexed": frames_indexed,
            "elapsed_seconds": elapsed,
        }

    except Exception as exc:
        log.error("Live indexing failed", error=str(exc))
        _redis.delete(indexing_key)
        _redis.hset(progress_key, "status", "failed")
        raise self.retry(exc=exc)


# ---------------------------------------------------------------------------
# reindex_all_task - Full reindex for an organization
# ---------------------------------------------------------------------------


@celery_app.task(
    name="app.workers.clip_tasks.reindex_all_task",
    bind=True,
    max_retries=1,
    soft_time_limit=43200,   # 12 hours soft limit
    time_limit=86400,        # 24 hours hard limit
    queue="video",
)
def reindex_all_task(
    self,
    org_id: str,
    fps: float = 1.0,
) -> dict[str, Any]:
    """Reindex all recordings for an organization.

    Iterates through all recordings belonging to the organization,
    clears existing embeddings, and re-indexes each recording with
    fresh CLIP embeddings.

    Args:
        org_id: UUID of the organization to reindex.
        fps: Frames per second to extract.

    Returns:
        Dict with overall reindexing statistics.
    """
    log = logger.bind(org_id=org_id, task_id=self.request.id)
    log.info("Starting full reindex", fps=fps)

    try:
        async def _do_reindex():
            from app.database import get_db_context
            from app.models.clip_search import FrameEmbedding
            from app.models.recording import Recording
            from app.services.clip_search_service import index_recording

            async with get_db_context() as session:
                # Delete all existing embeddings for the org
                await session.execute(
                    delete(FrameEmbedding).where(
                        FrameEmbedding.org_id == uuid.UUID(org_id)
                    )
                )
                await session.flush()

                log.info("Cleared existing embeddings for reindex")

                # Get all recordings
                result = await session.execute(
                    select(Recording.id).where(
                        Recording.org_id == uuid.UUID(org_id)
                    ).order_by(Recording.start_time.desc())
                )
                recording_ids = [row[0] for row in result.all()]

            total_recordings = len(recording_ids)
            total_frames = 0
            errors = []

            for i, rec_id in enumerate(recording_ids):
                log.info(
                    "Indexing recording",
                    progress=f"{i + 1}/{total_recordings}",
                    recording_id=str(rec_id),
                )

                try:
                    async with get_db_context() as session:
                        result = await index_recording(
                            db=session,
                            recording_id=rec_id,
                            fps=fps,
                        )
                        total_frames += result.get("frames_indexed", 0)
                except Exception as exc:
                    errors.append({
                        "recording_id": str(rec_id),
                        "error": str(exc)[:200],
                    })
                    log.warning(
                        "Failed to index recording",
                        recording_id=str(rec_id),
                        error=str(exc),
                    )

            return {
                "org_id": org_id,
                "total_recordings": total_recordings,
                "recordings_indexed": total_recordings - len(errors),
                "total_frames_indexed": total_frames,
                "errors": len(errors),
                "error_details": errors[:20],
            }

        result = _run_async(_do_reindex())

        log.info(
            "Full reindex completed",
            total_frames=result.get("total_frames_indexed", 0),
            errors=result.get("errors", 0),
        )

        return result

    except Exception as exc:
        log.error("Full reindex failed", error=str(exc))
        raise self.retry(exc=exc)


# ---------------------------------------------------------------------------
# cleanup_orphaned_embeddings - Remove embeddings for deleted recordings
# ---------------------------------------------------------------------------


@celery_app.task(
    name="app.workers.clip_tasks.cleanup_orphaned_embeddings",
    bind=True,
    max_retries=1,
    soft_time_limit=600,
    time_limit=900,
    queue="maintenance",
)
def cleanup_orphaned_embeddings(self) -> dict[str, Any]:
    """Remove frame embeddings that reference deleted recordings.

    Identifies FrameEmbedding rows whose recording_id no longer exists
    in the recordings table and deletes them. This handles the case
    where recordings are deleted but their embeddings were not cleaned
    up via CASCADE (e.g., due to race conditions or manual deletions).

    Returns:
        Dict with cleanup statistics.
    """
    log = logger.bind(task_id=self.request.id)
    log.info("Starting orphaned embedding cleanup")

    try:
        async def _do_cleanup():
            from app.database import get_db_context
            from app.models.clip_search import FrameEmbedding
            from app.models.recording import Recording

            async with get_db_context() as session:
                # Find orphaned embeddings using a LEFT JOIN
                orphaned_result = await session.execute(
                    text("""
                        SELECT COUNT(*)
                        FROM frame_embeddings fe
                        LEFT JOIN recordings r ON fe.recording_id = r.id
                        WHERE r.id IS NULL
                    """)
                )
                orphaned_count = orphaned_result.scalar() or 0

                if orphaned_count == 0:
                    return {
                        "status": "completed",
                        "orphaned_found": 0,
                        "deleted": 0,
                    }

                # Delete orphaned embeddings
                delete_result = await session.execute(
                    text("""
                        DELETE FROM frame_embeddings
                        WHERE recording_id NOT IN (
                            SELECT id FROM recordings
                        )
                    """)
                )

                deleted = delete_result.rowcount or 0

                return {
                    "status": "completed",
                    "orphaned_found": orphaned_count,
                    "deleted": deleted,
                }

        result = _run_async(_do_cleanup())

        log.info(
            "Orphaned embedding cleanup completed",
            orphaned=result.get("orphaned_found", 0),
            deleted=result.get("deleted", 0),
        )

        return result

    except Exception as exc:
        log.error("Orphaned embedding cleanup failed", error=str(exc))
        raise self.retry(exc=exc)
