"""Recording service for video capture, storage, and lifecycle management.

Provides FFmpeg-based recording start/stop, recording queries,
timeline views, clip export, and storage cleanup for expired recordings.
"""

from __future__ import annotations

import asyncio
import os
import subprocess
import uuid
from datetime import datetime, timedelta, timezone
from pathlib import Path
from typing import Any, Optional

import structlog
from sqlalchemy import func, select
from sqlalchemy.ext.asyncio import AsyncSession

from app.config import get_settings
from app.exceptions import NotFoundError, StreamError, ValidationError
from app.models.camera import Camera
from app.models.recording import Recording, RecordingSegment, RecordingType
from app.utils.encryption import decrypt_string
from app.utils.video_utils import extract_clip, get_video_info

logger = structlog.stdlib.get_logger(__name__)

_active_processes: dict[uuid.UUID, subprocess.Popen] = {}


# ── Recording Lifecycle ──────────────────────────────────────────────────


async def start_recording(
    db: AsyncSession,
    camera_id: uuid.UUID,
    org_id: uuid.UUID,
    recording_type: str = "continuous",
    duration_limit_seconds: Optional[int] = None,
) -> Recording:
    """Start recording from a camera stream using FFmpeg.

    Spawns an FFmpeg subprocess that writes video to the configured
    recordings directory. The recording metadata is persisted to the
    database immediately.

    Args:
        db: Async database session.
        camera_id: Camera to record from.
        org_id: Organization owning the recording.
        recording_type: 'continuous' or 'event'.
        duration_limit_seconds: Optional maximum recording duration.

    Returns:
        The created Recording instance.

    Raises:
        NotFoundError: If the camera does not exist.
        StreamError: If FFmpeg fails to start.
    """
    result = await db.execute(
        select(Camera).where(Camera.id == camera_id, Camera.org_id == org_id)
    )
    camera = result.scalar_one_or_none()
    if camera is None:
        raise NotFoundError(resource="Camera", identifier=camera_id)

    try:
        stream_url = decrypt_string(camera.stream_url)
    except ValueError:
        stream_url = camera.stream_url

    settings = get_settings()
    now = datetime.now(timezone.utc)
    date_str = now.strftime("%Y-%m-%d")
    time_str = now.strftime("%H%M%S")

    output_dir = os.path.join(
        settings.MINIO_BUCKET_RECORDINGS,
        str(org_id),
        str(camera_id),
        date_str,
    )
    os.makedirs(output_dir, exist_ok=True)

    filename = f"rec_{time_str}_{uuid.uuid4().hex[:8]}.mp4"
    file_path = os.path.join(output_dir, filename)

    try:
        rec_type = RecordingType(recording_type)
    except ValueError:
        rec_type = RecordingType.CONTINUOUS

    recording = Recording(
        id=uuid.uuid4(),
        camera_id=camera_id,
        org_id=org_id,
        recording_type=rec_type,
        start_time=now,
        file_path=file_path,
        is_archived=False,
    )

    if duration_limit_seconds:
        recording.expires_at = now + timedelta(seconds=duration_limit_seconds)

    db.add(recording)
    await db.flush()

    cmd = [
        "ffmpeg",
        "-y",
        "-rtsp_transport", "tcp",
        "-stimeout", "10000000",
        "-i", stream_url,
        "-c", "copy",
        "-movflags", "+faststart",
        "-loglevel", "warning",
    ]

    if duration_limit_seconds:
        cmd.extend(["-t", str(duration_limit_seconds)])

    cmd.append(file_path)

    try:
        process = subprocess.Popen(
            cmd,
            stdout=subprocess.PIPE,
            stderr=subprocess.PIPE,
            text=True,
        )
        _active_processes[recording.id] = process

        logger.info(
            "Recording started",
            recording_id=str(recording.id),
            camera_id=str(camera_id),
            pid=process.pid,
            file_path=file_path,
        )

    except FileNotFoundError:
        raise StreamError(
            message="FFmpeg not found. Ensure FFmpeg is installed.",
            code="FFMPEG_NOT_FOUND",
        )
    except Exception as exc:
        raise StreamError(
            message=f"Failed to start recording: {exc}",
            code="RECORDING_START_FAILED",
        )

    return recording


async def stop_recording(
    db: AsyncSession,
    recording_id: uuid.UUID,
    org_id: Optional[uuid.UUID] = None,
) -> Recording:
    """Stop an active recording and finalize its metadata.

    Terminates the FFmpeg process and updates duration and file size
    in the database.

    Args:
        db: Async database session.
        recording_id: Recording to stop.
        org_id: Optional organization filter.

    Returns:
        The updated Recording instance.

    Raises:
        NotFoundError: If the recording does not exist.
    """
    query = select(Recording).where(Recording.id == recording_id)
    if org_id:
        query = query.where(Recording.org_id == org_id)

    result = await db.execute(query)
    recording = result.scalar_one_or_none()
    if recording is None:
        raise NotFoundError(resource="Recording", identifier=recording_id)

    process = _active_processes.pop(recording_id, None)
    if process is not None and process.poll() is None:
        process.terminate()
        try:
            process.wait(timeout=10)
        except subprocess.TimeoutExpired:
            process.kill()
            process.wait(timeout=5)
        logger.info("Recording FFmpeg process terminated", recording_id=str(recording_id))

    now = datetime.now(timezone.utc)
    recording.end_time = now

    if recording.start_time:
        recording.duration_seconds = (now - recording.start_time).total_seconds()

    if os.path.isfile(recording.file_path):
        recording.file_size_bytes = os.path.getsize(recording.file_path)

    await db.flush()

    logger.info(
        "Recording stopped",
        recording_id=str(recording_id),
        duration=recording.duration_seconds,
        size=recording.file_size_bytes,
    )

    return recording


# ── Recording Queries ────────────────────────────────────────────────────


async def get_recordings(
    db: AsyncSession,
    org_id: uuid.UUID,
    camera_id: Optional[uuid.UUID] = None,
    recording_type: Optional[str] = None,
    start_date: Optional[datetime] = None,
    end_date: Optional[datetime] = None,
    page: int = 1,
    page_size: int = 20,
) -> tuple[list[Recording], int]:
    """Retrieve recordings with filtering and pagination.

    Args:
        db: Async database session.
        org_id: Organization filter.
        camera_id: Optional camera filter.
        recording_type: Optional type filter.
        start_date: Optional start time filter.
        end_date: Optional end time filter.
        page: Page number.
        page_size: Items per page.

    Returns:
        Tuple of (recordings, total_count).
    """
    query = select(Recording).where(Recording.org_id == org_id)

    if camera_id:
        query = query.where(Recording.camera_id == camera_id)
    if recording_type:
        try:
            rt = RecordingType(recording_type)
            query = query.where(Recording.recording_type == rt)
        except ValueError:
            pass
    if start_date:
        query = query.where(Recording.start_time >= start_date)
    if end_date:
        query = query.where(Recording.start_time <= end_date)

    count_q = select(func.count()).select_from(query.subquery())
    total_result = await db.execute(count_q)
    total = total_result.scalar() or 0

    offset = (max(1, page) - 1) * page_size
    query = query.order_by(Recording.start_time.desc()).offset(offset).limit(page_size)
    result = await db.execute(query)
    recordings = list(result.scalars().all())

    return recordings, total


async def get_recording_timeline(
    db: AsyncSession,
    org_id: uuid.UUID,
    camera_id: uuid.UUID,
    target_date: Optional[datetime] = None,
) -> list[dict[str, Any]]:
    """Get a timeline of recordings for a camera on a specific date.

    Returns time segments showing when recordings are available.

    Args:
        db: Async database session.
        org_id: Organization filter.
        camera_id: Camera to get timeline for.
        target_date: Date to view (defaults to today).

    Returns:
        List of timeline segment dicts.
    """
    if target_date is None:
        target_date = datetime.now(timezone.utc)

    day_start = target_date.replace(hour=0, minute=0, second=0, microsecond=0)
    day_end = day_start + timedelta(days=1)

    query = (
        select(Recording)
        .where(
            Recording.org_id == org_id,
            Recording.camera_id == camera_id,
            Recording.start_time >= day_start,
            Recording.start_time < day_end,
        )
        .order_by(Recording.start_time.asc())
    )

    result = await db.execute(query)
    recordings = list(result.scalars().all())

    timeline = []
    for rec in recordings:
        timeline.append({
            "recording_id": str(rec.id),
            "start_time": rec.start_time.isoformat(),
            "end_time": rec.end_time.isoformat() if rec.end_time else None,
            "duration_seconds": rec.duration_seconds,
            "recording_type": rec.recording_type.value,
            "file_path": rec.file_path,
            "file_size_bytes": rec.file_size_bytes,
            "is_active": rec.id in _active_processes,
        })

    return timeline


# ── Clip Export ──────────────────────────────────────────────────────────


async def export_clip(
    db: AsyncSession,
    recording_id: uuid.UUID,
    start_offset: float,
    duration: float,
    org_id: Optional[uuid.UUID] = None,
) -> str:
    """Export a clip from an existing recording.

    Uses FFmpeg to extract a segment from a recording file.

    Args:
        db: Async database session.
        recording_id: Source recording.
        start_offset: Start position in seconds from recording beginning.
        duration: Clip duration in seconds.
        org_id: Optional organization filter.

    Returns:
        Path to the exported clip file.

    Raises:
        NotFoundError: If the recording does not exist.
        ValidationError: If parameters are invalid.
        StreamError: If clip extraction fails.
    """
    query = select(Recording).where(Recording.id == recording_id)
    if org_id:
        query = query.where(Recording.org_id == org_id)

    result = await db.execute(query)
    recording = result.scalar_one_or_none()
    if recording is None:
        raise NotFoundError(resource="Recording", identifier=recording_id)

    if not os.path.isfile(recording.file_path):
        raise StreamError(
            message="Recording file not found on disk",
            code="RECORDING_FILE_MISSING",
        )

    if duration <= 0:
        raise ValidationError(
            message="Clip duration must be positive",
            code="INVALID_CLIP_DURATION",
        )

    clip_dir = os.path.join(os.path.dirname(recording.file_path), "clips")
    os.makedirs(clip_dir, exist_ok=True)

    clip_filename = f"clip_{uuid.uuid4().hex[:8]}.mp4"
    clip_path = os.path.join(clip_dir, clip_filename)

    loop = asyncio.get_event_loop()
    success = await loop.run_in_executor(
        None,
        extract_clip,
        recording.file_path,
        clip_path,
        start_offset,
        duration,
    )

    if not success:
        raise StreamError(
            message="Failed to export clip from recording",
            code="CLIP_EXPORT_FAILED",
        )

    logger.info(
        "Clip exported",
        recording_id=str(recording_id),
        clip_path=clip_path,
        start_offset=start_offset,
        duration=duration,
    )

    return clip_path


# ── Storage Cleanup ──────────────────────────────────────────────────────


async def cleanup_expired(
    db: AsyncSession,
    org_id: Optional[uuid.UUID] = None,
    dry_run: bool = False,
) -> dict[str, Any]:
    """Delete expired recordings and their files from disk.

    Recordings past their expires_at timestamp and not archived are
    eligible for cleanup.

    Args:
        db: Async database session.
        org_id: Optional organization filter.
        dry_run: If True, only report what would be deleted.

    Returns:
        Dict with deleted_count, freed_bytes, and errors.
    """
    now = datetime.now(timezone.utc)

    query = select(Recording).where(
        Recording.expires_at.isnot(None),
        Recording.expires_at < now,
        Recording.is_archived.is_(False),
    )
    if org_id:
        query = query.where(Recording.org_id == org_id)

    result = await db.execute(query)
    expired = list(result.scalars().all())

    deleted_count = 0
    freed_bytes = 0
    errors: list[str] = []

    for recording in expired:
        if dry_run:
            deleted_count += 1
            freed_bytes += recording.file_size_bytes or 0
            continue

        file_path = recording.file_path
        file_size = recording.file_size_bytes or 0

        if os.path.isfile(file_path):
            try:
                os.remove(file_path)
                freed_bytes += file_size
            except OSError as exc:
                errors.append(f"Failed to delete {file_path}: {exc}")
                continue

        for segment in recording.segments:
            if os.path.isfile(segment.file_path):
                try:
                    os.remove(segment.file_path)
                    freed_bytes += segment.file_size_bytes or 0
                except OSError as exc:
                    errors.append(f"Failed to delete segment {segment.file_path}: {exc}")

        await db.delete(recording)
        deleted_count += 1

    if not dry_run and deleted_count > 0:
        await db.flush()

    freed_mb = round(freed_bytes / (1024 * 1024), 2)
    logger.info(
        "Recording cleanup completed",
        deleted=deleted_count,
        freed_mb=freed_mb,
        errors_count=len(errors),
        dry_run=dry_run,
    )

    return {
        "deleted_count": deleted_count,
        "freed_bytes": freed_bytes,
        "freed_mb": freed_mb,
        "errors": errors,
        "dry_run": dry_run,
    }
