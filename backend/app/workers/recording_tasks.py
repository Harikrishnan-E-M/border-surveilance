"""
VisionAI Recording Management Celery Tasks.

Manages continuous HLS recording via FFmpeg, event clip extraction,
and cleanup of expired recordings. Uses subprocess for FFmpeg process
management with proper signal handling and resource cleanup.
"""

from __future__ import annotations

import asyncio
import os
import shutil
import signal
import subprocess
import uuid
from datetime import datetime, timedelta, timezone
from pathlib import Path
from typing import Any

import redis
import structlog
from sqlalchemy import delete, select, text, update

from app.config import get_settings
from app.workers.celery_app import celery_app

logger = structlog.stdlib.get_logger(__name__)
settings = get_settings()

_redis = redis.Redis.from_url(settings.REDIS_URL, decode_responses=True)

# Redis key prefixes for recording management
RECORDING_PID_PREFIX = "visionai:recording:pid:"
RECORDING_STATUS_PREFIX = "visionai:recording:status:"
RECORDING_BASE_DIR = "/opt/visionai/recordings"


def _run_async(coro):
    """Run an async coroutine from a synchronous Celery task context."""
    loop = asyncio.new_event_loop()
    try:
        return loop.run_until_complete(coro)
    finally:
        loop.close()


def _ensure_recording_dir(camera_id: str, date_str: str | None = None) -> Path:
    """Create and return the recording directory for a camera."""
    if date_str is None:
        date_str = datetime.now(timezone.utc).strftime("%Y-%m-%d")
    rec_dir = Path(RECORDING_BASE_DIR) / camera_id / date_str
    rec_dir.mkdir(parents=True, exist_ok=True)
    return rec_dir


@celery_app.task(
    name="app.workers.recording_tasks.start_continuous_recording",
    bind=True,
    max_retries=3,
    default_retry_delay=15,
    soft_time_limit=None,
    time_limit=None,
    queue="video",
)
def start_continuous_recording(self, camera_id: str) -> dict[str, Any]:
    """Start continuous HLS recording for a camera using FFmpeg.

    Launches an FFmpeg subprocess that reads from the camera's RTSP stream
    and writes HLS segments to local storage. The process PID is stored
    in Redis for lifecycle management.

    Args:
        camera_id: UUID of the camera to record.

    Returns:
        dict with recording status and process information.
    """
    log = logger.bind(camera_id=camera_id, task_id=self.request.id)
    log.info("Starting continuous recording")

    pid_key = f"{RECORDING_PID_PREFIX}{camera_id}"
    status_key = f"{RECORDING_STATUS_PREFIX}{camera_id}"

    try:
        # Check if recording is already running
        existing_pid = _redis.get(pid_key)
        if existing_pid:
            try:
                os.kill(int(existing_pid), 0)
                log.warning("Recording already active", pid=existing_pid)
                return {
                    "camera_id": camera_id,
                    "status": "already_running",
                    "pid": int(existing_pid),
                }
            except (ProcessLookupError, ValueError):
                # Process no longer exists, clean up stale PID
                _redis.delete(pid_key)

        # Load camera stream URL from database
        async def _get_stream_url():
            from app.database import get_db_context
            from app.models.camera import Camera

            async with get_db_context() as session:
                result = await session.execute(
                    select(Camera.stream_url, Camera.name, Camera.org_id).where(
                        Camera.id == uuid.UUID(camera_id)
                    )
                )
                row = result.one_or_none()
                if row is None:
                    return None, None, None
                return row.stream_url, row.name, str(row.org_id)

        stream_url, camera_name, org_id = _run_async(_get_stream_url())

        if stream_url is None:
            log.error("Camera not found")
            return {"camera_id": camera_id, "status": "error", "message": "Camera not found"}

        # Prepare output directory
        date_str = datetime.now(timezone.utc).strftime("%Y-%m-%d")
        output_dir = _ensure_recording_dir(camera_id, date_str)
        playlist_path = output_dir / "stream.m3u8"
        segment_pattern = str(output_dir / "segment_%05d.ts")

        # Build FFmpeg command for HLS recording
        ffmpeg_cmd = [
            "ffmpeg",
            "-hide_banner",
            "-loglevel", "warning",
            # Input options
            "-rtsp_transport", "tcp",
            "-stimeout", "5000000",
            "-i", stream_url,
            # Encoding options (copy codec for efficiency)
            "-c:v", "copy",
            "-c:a", "copy",
            # HLS output options
            "-f", "hls",
            "-hls_time", "10",
            "-hls_list_size", "360",  # Keep 1 hour of segments in playlist
            "-hls_flags", "delete_segments+append_list",
            "-hls_segment_filename", segment_pattern,
            "-hls_allow_cache", "1",
            str(playlist_path),
        ]

        # Launch FFmpeg process
        process = subprocess.Popen(
            ffmpeg_cmd,
            stdout=subprocess.DEVNULL,
            stderr=subprocess.PIPE,
            preexec_fn=os.setsid,
        )

        # Store PID in Redis
        _redis.set(pid_key, str(process.pid))
        _redis.set(status_key, "recording")

        # Store recording metadata
        _redis.hset(
            f"visionai:recording:meta:{camera_id}",
            mapping={
                "pid": str(process.pid),
                "started_at": datetime.now(timezone.utc).isoformat(),
                "output_dir": str(output_dir),
                "playlist_path": str(playlist_path),
                "camera_name": camera_name or "",
                "org_id": org_id or "",
                "task_id": self.request.id or "",
            },
        )

        # Create recording record in database
        async def _create_recording():
            from app.database import get_db_context

            async with get_db_context() as session:
                await session.execute(
                    text("""
                        INSERT INTO recordings (
                            id, org_id, camera_id, recording_type,
                            start_time, file_path, status,
                            created_at, updated_at
                        ) VALUES (
                            :id, :org_id, :camera_id, 'continuous',
                            :start_time, :file_path, 'recording',
                            NOW(), NOW()
                        )
                    """),
                    {
                        "id": str(uuid.uuid4()),
                        "org_id": org_id,
                        "camera_id": camera_id,
                        "start_time": datetime.now(timezone.utc),
                        "file_path": str(playlist_path),
                    },
                )

        _run_async(_create_recording())

        log.info(
            "Continuous recording started",
            pid=process.pid,
            output_dir=str(output_dir),
        )

        return {
            "camera_id": camera_id,
            "status": "recording",
            "pid": process.pid,
            "output_dir": str(output_dir),
            "playlist_path": str(playlist_path),
        }

    except Exception as exc:
        log.error("Failed to start recording", error=str(exc))
        _redis.set(status_key, "failed")
        raise self.retry(exc=exc)


@celery_app.task(
    name="app.workers.recording_tasks.stop_recording",
    bind=True,
    max_retries=2,
    default_retry_delay=5,
    queue="video",
)
def stop_recording(self, camera_id: str) -> dict[str, Any]:
    """Stop the FFmpeg recording process for a camera.

    Sends SIGTERM to the FFmpeg process, waits for graceful shutdown,
    and falls back to SIGKILL if necessary. Updates Redis and database.

    Args:
        camera_id: UUID of the camera to stop recording.

    Returns:
        dict with status information.
    """
    log = logger.bind(camera_id=camera_id, task_id=self.request.id)
    log.info("Stopping recording")

    pid_key = f"{RECORDING_PID_PREFIX}{camera_id}"
    status_key = f"{RECORDING_STATUS_PREFIX}{camera_id}"

    try:
        pid_str = _redis.get(pid_key)
        if not pid_str:
            log.warning("No recording PID found in Redis")
            _redis.set(status_key, "stopped")
            return {"camera_id": camera_id, "status": "not_running"}

        pid = int(pid_str)

        try:
            # Send SIGTERM for graceful shutdown
            os.killpg(os.getpgid(pid), signal.SIGTERM)
            log.info("Sent SIGTERM to FFmpeg process group", pid=pid)

            # Wait up to 10 seconds for graceful shutdown
            import time
            for _ in range(20):
                try:
                    os.kill(pid, 0)
                    time.sleep(0.5)
                except ProcessLookupError:
                    break
            else:
                # Force kill if still running
                try:
                    os.killpg(os.getpgid(pid), signal.SIGKILL)
                    log.warning("Sent SIGKILL to FFmpeg process group", pid=pid)
                except ProcessLookupError:
                    pass

        except ProcessLookupError:
            log.info("FFmpeg process already terminated", pid=pid)

        # Clean up Redis
        _redis.delete(pid_key)
        _redis.set(status_key, "stopped")
        _redis.delete(f"visionai:recording:meta:{camera_id}")

        # Update recording record in database
        async def _finalize_recording():
            from app.database import get_db_context

            async with get_db_context() as session:
                await session.execute(
                    text("""
                        UPDATE recordings
                        SET status = 'completed',
                            end_time = NOW(),
                            updated_at = NOW()
                        WHERE camera_id = :camera_id
                          AND status = 'recording'
                    """),
                    {"camera_id": camera_id},
                )

        _run_async(_finalize_recording())

        log.info("Recording stopped successfully", pid=pid)

        return {
            "camera_id": camera_id,
            "status": "stopped",
            "pid": pid,
        }

    except Exception as exc:
        log.error("Failed to stop recording", error=str(exc))
        raise self.retry(exc=exc)


@celery_app.task(
    name="app.workers.recording_tasks.extract_event_clip",
    bind=True,
    max_retries=3,
    default_retry_delay=10,
    soft_time_limit=120,
    time_limit=180,
    queue="video",
)
def extract_event_clip(
    self,
    camera_id: str,
    event_time: str,
    pre_seconds: int = 10,
    post_seconds: int = 10,
) -> dict[str, Any]:
    """Extract a video clip around an event timestamp.

    Uses FFmpeg to cut a segment from the continuous recording centered
    around the event time, re-encodes to MP4, and uploads to MinIO.

    Args:
        camera_id: UUID of the camera.
        event_time: ISO-8601 timestamp of the event.
        pre_seconds: Seconds to include before the event (default 10).
        post_seconds: Seconds to include after the event (default 10).

    Returns:
        dict with the clip URL and metadata.
    """
    log = logger.bind(camera_id=camera_id, event_time=event_time, task_id=self.request.id)
    log.info("Extracting event clip", pre=pre_seconds, post=post_seconds)

    try:
        event_dt = datetime.fromisoformat(event_time)
        clip_start = event_dt - timedelta(seconds=pre_seconds)
        clip_end = event_dt + timedelta(seconds=post_seconds)
        duration = pre_seconds + post_seconds

        # Determine the recording directory for the event date
        date_str = event_dt.strftime("%Y-%m-%d")
        recording_dir = Path(RECORDING_BASE_DIR) / camera_id / date_str
        playlist_path = recording_dir / "stream.m3u8"

        if not playlist_path.exists():
            log.error("No recording found for date", date=date_str)
            return {
                "camera_id": camera_id,
                "status": "error",
                "message": f"No recording found for {date_str}",
            }

        # Create output clip
        clip_id = str(uuid.uuid4())[:8]
        clip_filename = f"clip_{camera_id[:8]}_{event_dt.strftime('%Y%m%d_%H%M%S')}_{clip_id}.mp4"
        clips_dir = Path(RECORDING_BASE_DIR) / "clips"
        clips_dir.mkdir(parents=True, exist_ok=True)
        clip_path = clips_dir / clip_filename

        # Calculate start offset within the HLS stream
        # FFmpeg can seek within HLS by time
        start_offset = clip_start.strftime("%H:%M:%S")

        ffmpeg_cmd = [
            "ffmpeg",
            "-hide_banner",
            "-loglevel", "warning",
            "-y",  # Overwrite output
            # Input
            "-i", str(playlist_path),
            # Seek and duration
            "-ss", start_offset,
            "-t", str(duration),
            # Output encoding
            "-c:v", "libx264",
            "-preset", "fast",
            "-crf", "23",
            "-c:a", "aac",
            "-b:a", "128k",
            "-movflags", "+faststart",
            str(clip_path),
        ]

        result = subprocess.run(
            ffmpeg_cmd,
            capture_output=True,
            text=True,
            timeout=120,
        )

        if result.returncode != 0:
            log.error("FFmpeg clip extraction failed", stderr=result.stderr[:500])
            return {
                "camera_id": camera_id,
                "status": "error",
                "message": f"FFmpeg failed: {result.stderr[:200]}",
            }

        if not clip_path.exists():
            log.error("Clip file was not created")
            return {"camera_id": camera_id, "status": "error", "message": "Clip file not created"}

        clip_size = clip_path.stat().st_size

        # Upload clip to MinIO
        from minio import Minio

        minio_client = Minio(
            settings.MINIO_ENDPOINT,
            access_key=settings.MINIO_ACCESS_KEY,
            secret_key=settings.MINIO_SECRET_KEY,
            secure=settings.MINIO_SECURE,
        )

        bucket = settings.MINIO_BUCKET_RECORDINGS
        if not minio_client.bucket_exists(bucket):
            minio_client.make_bucket(bucket)

        object_name = f"clips/{camera_id}/{clip_filename}"

        minio_client.fput_object(
            bucket,
            object_name,
            str(clip_path),
            content_type="video/mp4",
        )

        clip_url = f"{bucket}/{object_name}"

        # Clean up local clip file
        clip_path.unlink(missing_ok=True)

        log.info(
            "Event clip extracted",
            clip_url=clip_url,
            duration=duration,
            size_bytes=clip_size,
        )

        return {
            "camera_id": camera_id,
            "status": "completed",
            "clip_url": clip_url,
            "event_time": event_time,
            "clip_start": clip_start.isoformat(),
            "clip_end": clip_end.isoformat(),
            "duration_seconds": duration,
            "size_bytes": clip_size,
        }

    except subprocess.TimeoutExpired:
        log.error("FFmpeg clip extraction timed out")
        return {"camera_id": camera_id, "status": "error", "message": "FFmpeg timed out"}
    except Exception as exc:
        log.error("Event clip extraction failed", error=str(exc))
        raise self.retry(exc=exc)


@celery_app.task(
    name="app.workers.recording_tasks.cleanup_expired_recordings",
    bind=True,
    max_retries=1,
    soft_time_limit=600,
    time_limit=900,
    queue="maintenance",
)
def cleanup_expired_recordings(self) -> dict[str, Any]:
    """Delete recordings that have passed their retention period.

    Checks each organization's retention policy, identifies expired
    recordings, deletes files from disk and MinIO, and removes
    database records.

    Returns:
        dict summarizing cleanup results.
    """
    log = logger.bind(task_id=self.request.id)
    log.info("Starting recording cleanup")

    try:
        default_retention_days = 30

        async def _find_expired():
            from app.database import get_db_context

            async with get_db_context() as session:
                cutoff = datetime.now(timezone.utc) - timedelta(days=default_retention_days)

                result = await session.execute(
                    text("""
                        SELECT id, camera_id, file_path, recording_type
                        FROM recordings
                        WHERE start_time < :cutoff
                          AND status IN ('completed', 'failed')
                    """),
                    {"cutoff": cutoff},
                )
                return [
                    {
                        "id": str(row.id),
                        "camera_id": str(row.camera_id),
                        "file_path": row.file_path,
                        "recording_type": row.recording_type,
                    }
                    for row in result.fetchall()
                ]

        expired_recordings = _run_async(_find_expired())

        deleted_count = 0
        errors = []

        for recording in expired_recordings:
            try:
                file_path = recording["file_path"]

                # Delete local files
                if file_path and os.path.exists(file_path):
                    parent_dir = os.path.dirname(file_path)
                    if os.path.isdir(parent_dir):
                        shutil.rmtree(parent_dir, ignore_errors=True)
                    elif os.path.isfile(file_path):
                        os.unlink(file_path)

                # Delete from MinIO if path looks like a bucket path
                if file_path and "/" in file_path and not file_path.startswith("/"):
                    try:
                        from minio import Minio

                        minio_client = Minio(
                            settings.MINIO_ENDPOINT,
                            access_key=settings.MINIO_ACCESS_KEY,
                            secret_key=settings.MINIO_SECRET_KEY,
                            secure=settings.MINIO_SECURE,
                        )
                        parts = file_path.split("/", 1)
                        if len(parts) == 2:
                            minio_client.remove_object(parts[0], parts[1])
                    except Exception as minio_exc:
                        log.warning(
                            "Failed to delete from MinIO",
                            path=file_path,
                            error=str(minio_exc),
                        )

                deleted_count += 1

            except Exception as exc:
                errors.append({"recording_id": recording["id"], "error": str(exc)})
                log.warning("Failed to delete recording files", recording_id=recording["id"], error=str(exc))

        # Delete database records
        if expired_recordings:
            async def _delete_records():
                from app.database import get_db_context

                async with get_db_context() as session:
                    ids = [r["id"] for r in expired_recordings]
                    await session.execute(
                        text("DELETE FROM recordings WHERE id = ANY(:ids)"),
                        {"ids": ids},
                    )

            _run_async(_delete_records())

        log.info(
            "Recording cleanup completed",
            total_expired=len(expired_recordings),
            deleted=deleted_count,
            errors=len(errors),
        )

        return {
            "status": "completed",
            "total_expired": len(expired_recordings),
            "deleted": deleted_count,
            "errors": len(errors),
            "error_details": errors[:10],
        }

    except Exception as exc:
        log.error("Recording cleanup failed", error=str(exc))
        raise self.retry(exc=exc)
