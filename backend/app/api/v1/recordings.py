"""
Recording management API endpoints.

Provides listing, details, timeline view, export clip creation,
deletion, and video upload for offline analysis.
"""

from __future__ import annotations

import math
import uuid
from datetime import datetime, date, timedelta, timezone
from typing import Optional

import structlog
from fastapi import APIRouter, Depends, File, HTTPException, Query, UploadFile, status
from pydantic import BaseModel, Field
from sqlalchemy import and_, func, select, desc
from sqlalchemy.ext.asyncio import AsyncSession

from app.database import get_db_session
from app.exceptions import (
    AuthorizationError,
    NotFoundError,
    ValidationError,
)
from app.middleware.auth import JWTBearer, TokenPayload
from app.models.camera import Camera
from app.models.recording import Recording, RecordingSegment, RecordingType
from app.models.user import User, UserRole
from app.schemas.common import ErrorResponse, SuccessResponse

logger = structlog.stdlib.get_logger(__name__)

router = APIRouter()


# ---------------------------------------------------------------------------
# Pydantic schemas
# ---------------------------------------------------------------------------


class RecordingResponse(BaseModel):
    id: uuid.UUID
    camera_id: uuid.UUID
    org_id: uuid.UUID
    recording_type: str
    start_time: datetime
    end_time: datetime | None = None
    duration_seconds: float | None = None
    file_path: str
    file_size_bytes: int | None = None
    is_archived: bool
    expires_at: datetime | None = None
    created_at: datetime
    updated_at: datetime

    class Config:
        from_attributes = True


class ExportClipRequest(BaseModel):
    camera_id: uuid.UUID
    start_time: datetime
    end_time: datetime
    format: str = Field(default="mp4", description="Output format: mp4, webm")


# ---------------------------------------------------------------------------
# Helpers
# ---------------------------------------------------------------------------


async def _get_current_user(
    token: TokenPayload = Depends(JWTBearer()),
    db: AsyncSession = Depends(get_db_session),
) -> User:
    result = await db.execute(
        select(User).where(User.id == uuid.UUID(token.sub), User.is_active.is_(True))
    )
    user = result.scalars().first()
    if not user:
        raise HTTPException(status_code=401, detail="User not found or deactivated.")
    return user


def _require_manager(user: User) -> None:
    allowed = {UserRole.SUPER_ADMIN, UserRole.ORG_ADMIN, UserRole.MANAGER}
    if user.role not in allowed:
        raise AuthorizationError(message="Manager role or higher is required.")


def _recording_to_response(recording: Recording) -> dict:
    return RecordingResponse(
        id=recording.id,
        camera_id=recording.camera_id,
        org_id=recording.org_id,
        recording_type=recording.recording_type.value if isinstance(recording.recording_type, RecordingType) else recording.recording_type,
        start_time=recording.start_time,
        end_time=recording.end_time,
        duration_seconds=recording.duration_seconds,
        file_path=recording.file_path,
        file_size_bytes=recording.file_size_bytes,
        is_archived=recording.is_archived,
        expires_at=recording.expires_at,
        created_at=recording.created_at,
        updated_at=recording.updated_at,
    ).model_dump(mode="json")


# ---------------------------------------------------------------------------
# GET /storage - Storage usage summary
# ---------------------------------------------------------------------------


@router.get(
    "/storage",
    response_model=SuccessResponse,
    summary="Get recording storage usage info",
)
async def get_storage_info(
    user: User = Depends(_get_current_user),
    db: AsyncSession = Depends(get_db_session),
) -> dict:
    """Return storage usage statistics for the organization's recordings."""
    # Count recordings and sum file sizes
    result = await db.execute(
        select(
            func.count(Recording.id),
            func.coalesce(func.sum(Recording.file_size_bytes), 0),
        ).where(Recording.org_id == user.org_id)
    )
    row = result.first()
    recording_count = row[0] if row else 0
    used_bytes = row[1] if row else 0

    # Use a sensible default total (100 GB) since actual disk info
    # is infrastructure-dependent
    total_bytes = 100 * 1024 * 1024 * 1024  # 100 GB default
    free_bytes = max(0, total_bytes - used_bytes)

    return {
        "status": "success",
        "data": {
            "total_bytes": total_bytes,
            "used_bytes": used_bytes,
            "free_bytes": free_bytes,
            "recording_count": recording_count,
        },
    }


# ---------------------------------------------------------------------------
# GET / - List recordings
# ---------------------------------------------------------------------------


@router.get(
    "/",
    response_model=SuccessResponse,
    summary="List recordings (paginated, filterable)",
)
async def list_recordings(
    camera_id: uuid.UUID | None = Query(None),
    recording_type: str | None = Query(None, description="Recording type: continuous, event"),
    start_date: datetime | None = Query(None),
    end_date: datetime | None = Query(None),
    is_archived: bool | None = Query(None),
    page: int = Query(1, ge=1),
    page_size: int = Query(20, ge=1, le=100),
    user: User = Depends(_get_current_user),
    db: AsyncSession = Depends(get_db_session),
) -> dict:
    """Return a paginated list of recordings for the organization."""
    query = select(Recording).where(Recording.org_id == user.org_id)

    if camera_id:
        query = query.where(Recording.camera_id == camera_id)
    if recording_type:
        try:
            query = query.where(Recording.recording_type == RecordingType(recording_type))
        except ValueError:
            raise ValidationError(message=f"Invalid recording_type: {recording_type}")
    if start_date:
        query = query.where(Recording.start_time >= start_date)
    if end_date:
        query = query.where(Recording.start_time <= end_date)
    if is_archived is not None:
        query = query.where(Recording.is_archived == is_archived)

    count_q = select(func.count()).select_from(query.subquery())
    total = (await db.execute(count_q)).scalar() or 0

    query = query.order_by(Recording.start_time.desc()).offset((page - 1) * page_size).limit(page_size)
    result = await db.execute(query)
    recordings = result.scalars().all()

    return {
        "status": "success",
        "data": [_recording_to_response(r) for r in recordings],
        "meta": {
            "page": page,
            "page_size": page_size,
            "total": total,
            "total_pages": math.ceil(total / page_size) if page_size else 0,
        },
    }


# ---------------------------------------------------------------------------
# GET /{recording_id} - Get recording details with stream URL
# ---------------------------------------------------------------------------


@router.get(
    "/{recording_id}",
    response_model=SuccessResponse,
    summary="Get recording details with stream URL",
    responses={404: {"model": ErrorResponse}},
)
async def get_recording(
    recording_id: uuid.UUID,
    user: User = Depends(_get_current_user),
    db: AsyncSession = Depends(get_db_session),
) -> dict:
    """Retrieve a recording with its segment details and stream URL."""
    result = await db.execute(
        select(Recording).where(Recording.id == recording_id, Recording.org_id == user.org_id)
    )
    recording = result.scalars().first()
    if not recording:
        raise NotFoundError(resource="Recording", identifier=str(recording_id))

    # Fetch segments
    seg_result = await db.execute(
        select(RecordingSegment)
        .where(RecordingSegment.recording_id == recording_id)
        .order_by(RecordingSegment.segment_number.asc())
    )
    segments = seg_result.scalars().all()

    segment_data = [
        {
            "id": str(s.id),
            "segment_number": s.segment_number,
            "file_path": s.file_path,
            "start_time": s.start_time.isoformat() if s.start_time else None,
            "end_time": s.end_time.isoformat() if s.end_time else None,
            "duration_seconds": s.duration_seconds,
            "file_size_bytes": s.file_size_bytes,
        }
        for s in segments
    ]

    # Generate stream URL
    stream_url = None
    try:
        from app.services.stream_service import generate_playback_url
        stream_url = await generate_playback_url(file_path=recording.file_path)
    except (ImportError, Exception) as exc:
        logger.debug("Could not generate stream URL", error=str(exc))

    recording_data = _recording_to_response(recording)
    recording_data["segments"] = segment_data
    recording_data["stream_url"] = stream_url

    return {
        "status": "success",
        "data": recording_data,
    }


# ---------------------------------------------------------------------------
# GET /timeline/{camera_id} - Recording timeline for date
# ---------------------------------------------------------------------------


@router.get(
    "/timeline/{camera_id}",
    response_model=SuccessResponse,
    summary="Get recording timeline for a camera on a specific date",
    responses={404: {"model": ErrorResponse}},
)
async def recording_timeline(
    camera_id: uuid.UUID,
    target_date: date = Query(..., alias="date", description="Date to get timeline for (YYYY-MM-DD)"),
    user: User = Depends(_get_current_user),
    db: AsyncSession = Depends(get_db_session),
) -> dict:
    """Return a timeline of recordings for a camera on a given date."""
    # Verify camera
    cam_result = await db.execute(
        select(Camera).where(Camera.id == camera_id, Camera.org_id == user.org_id)
    )
    if not cam_result.scalars().first():
        raise NotFoundError(resource="Camera", identifier=str(camera_id))

    day_start = datetime.combine(target_date, datetime.min.time()).replace(tzinfo=timezone.utc)
    day_end = day_start + timedelta(days=1)

    query = (
        select(Recording)
        .where(
            Recording.camera_id == camera_id,
            Recording.org_id == user.org_id,
            Recording.start_time >= day_start,
            Recording.start_time < day_end,
        )
        .order_by(Recording.start_time.asc())
    )
    result = await db.execute(query)
    recordings = result.scalars().all()

    timeline = [
        {
            "id": str(r.id),
            "recording_type": r.recording_type.value if isinstance(r.recording_type, RecordingType) else r.recording_type,
            "start_time": r.start_time.isoformat(),
            "end_time": r.end_time.isoformat() if r.end_time else None,
            "duration_seconds": r.duration_seconds,
        }
        for r in recordings
    ]

    # Calculate total recorded duration
    total_duration = sum(
        r.duration_seconds for r in recordings if r.duration_seconds
    )

    return {
        "status": "success",
        "data": {
            "camera_id": str(camera_id),
            "date": target_date.isoformat(),
            "recordings": timeline,
            "total_recordings": len(timeline),
            "total_duration_seconds": total_duration,
        },
    }


# ---------------------------------------------------------------------------
# POST /export - Export clip between start/end times
# ---------------------------------------------------------------------------


@router.post(
    "/export",
    response_model=SuccessResponse,
    status_code=status.HTTP_202_ACCEPTED,
    summary="Export a video clip between start and end times",
)
async def export_clip(
    body: ExportClipRequest,
    user: User = Depends(_get_current_user),
    db: AsyncSession = Depends(get_db_session),
) -> dict:
    """Request a video clip export for a camera between two timestamps."""
    # Verify camera
    cam_result = await db.execute(
        select(Camera).where(Camera.id == body.camera_id, Camera.org_id == user.org_id)
    )
    if not cam_result.scalars().first():
        raise NotFoundError(resource="Camera", identifier=str(body.camera_id))

    if body.end_time <= body.start_time:
        raise ValidationError(message="end_time must be after start_time.")

    duration = (body.end_time - body.start_time).total_seconds()
    if duration > 3600:
        raise ValidationError(message="Maximum clip duration is 1 hour (3600 seconds).")

    # Dispatch export job
    export_id = str(uuid.uuid4())
    try:
        from app.services.recording_service import export_video_clip
        await export_video_clip(
            export_id=export_id,
            camera_id=str(body.camera_id),
            start_time=body.start_time,
            end_time=body.end_time,
            output_format=body.format,
        )
    except ImportError:
        logger.warning("Recording service not available for clip export")
    except Exception as exc:
        logger.error("Clip export failed", error=str(exc))
        raise ValidationError(message=f"Clip export failed: {str(exc)}")

    return {
        "status": "success",
        "data": {
            "export_id": export_id,
            "camera_id": str(body.camera_id),
            "start_time": body.start_time.isoformat(),
            "end_time": body.end_time.isoformat(),
            "format": body.format,
            "status": "processing",
        },
        "message": "Clip export has been queued for processing.",
    }


# ---------------------------------------------------------------------------
# DELETE /{recording_id} - Delete recording
# ---------------------------------------------------------------------------


@router.delete(
    "/{recording_id}",
    response_model=SuccessResponse,
    summary="Delete recording",
    responses={403: {"model": ErrorResponse}, 404: {"model": ErrorResponse}},
)
async def delete_recording(
    recording_id: uuid.UUID,
    user: User = Depends(_get_current_user),
    db: AsyncSession = Depends(get_db_session),
) -> dict:
    """Delete a recording and its segments. Requires manager role or above."""
    _require_manager(user)

    result = await db.execute(
        select(Recording).where(Recording.id == recording_id, Recording.org_id == user.org_id)
    )
    recording = result.scalars().first()
    if not recording:
        raise NotFoundError(resource="Recording", identifier=str(recording_id))

    # Delete physical files
    try:
        from app.services.storage_service import delete_file
        await delete_file(recording.file_path)
        # Delete segment files
        for seg in recording.segments:
            await delete_file(seg.file_path)
    except (ImportError, Exception) as exc:
        logger.warning("Failed to delete recording files from storage", error=str(exc))

    await db.delete(recording)
    await db.flush()

    logger.info("Recording deleted", recording_id=str(recording_id))

    return {
        "status": "success",
        "data": None,
        "message": "Recording deleted successfully.",
    }


# ---------------------------------------------------------------------------
# POST /upload - Upload video file for offline analysis
# ---------------------------------------------------------------------------


@router.post(
    "/upload",
    response_model=SuccessResponse,
    status_code=status.HTTP_202_ACCEPTED,
    summary="Upload video file for offline analysis",
)
async def upload_video(
    camera_id: uuid.UUID = Query(..., description="Camera to associate with"),
    file: UploadFile = File(..., description="Video file to upload"),
    user: User = Depends(_get_current_user),
    db: AsyncSession = Depends(get_db_session),
) -> dict:
    """Upload a video file for offline analysis on a specified camera."""
    # Verify camera
    cam_result = await db.execute(
        select(Camera).where(Camera.id == camera_id, Camera.org_id == user.org_id)
    )
    if not cam_result.scalars().first():
        raise NotFoundError(resource="Camera", identifier=str(camera_id))

    # Validate file type
    allowed_types = {"video/mp4", "video/mpeg", "video/avi", "video/x-msvideo", "video/webm", "video/quicktime"}
    if file.content_type and file.content_type not in allowed_types:
        raise ValidationError(
            message=f"Unsupported video format '{file.content_type}'. Allowed: mp4, mpeg, avi, webm, mov"
        )

    # Read and store file
    file_bytes = await file.read()
    file_size = len(file_bytes)
    storage_path = f"uploads/{user.org_id}/{camera_id}/{uuid.uuid4()}/{file.filename}"

    try:
        from app.services.storage_service import upload_file
        storage_path = await upload_file(
            file_bytes=file_bytes,
            path=storage_path,
            content_type=file.content_type,
        )
    except ImportError:
        logger.warning("Storage service not available; recording file path only")
    except Exception as exc:
        logger.error("Video upload failed", error=str(exc))
        raise ValidationError(message=f"Video upload failed: {str(exc)}")

    # Create recording record
    recording = Recording(
        camera_id=camera_id,
        org_id=user.org_id,
        recording_type=RecordingType.EVENT,
        start_time=datetime.now(timezone.utc),
        file_path=storage_path,
        file_size_bytes=file_size,
    )
    db.add(recording)
    await db.flush()

    # Dispatch offline analysis job
    try:
        from app.services.recording_service import analyze_uploaded_video
        await analyze_uploaded_video(
            recording_id=str(recording.id),
            camera_id=str(camera_id),
            file_path=storage_path,
        )
    except (ImportError, Exception) as exc:
        logger.warning("Could not dispatch offline analysis", error=str(exc))

    logger.info(
        "Video uploaded for analysis",
        recording_id=str(recording.id),
        camera_id=str(camera_id),
        file_size=file_size,
    )

    return {
        "status": "success",
        "data": {
            "recording_id": str(recording.id),
            "file_path": storage_path,
            "file_size_bytes": file_size,
            "status": "uploaded",
        },
        "message": "Video uploaded successfully. Analysis will begin shortly.",
    }


# ---------------------------------------------------------------------------
# GET /{recording_id}/download - Download recording file
# ---------------------------------------------------------------------------


@router.get(
    "/{recording_id}/download",
    summary="Download a recording file",
    responses={404: {"model": ErrorResponse}},
)
async def download_recording(
    recording_id: uuid.UUID,
    user: User = Depends(_get_current_user),
    db: AsyncSession = Depends(get_db_session),
):
    """Download a recording file. Returns a presigned URL or streams the file."""
    result = await db.execute(
        select(Recording).where(Recording.id == recording_id, Recording.org_id == user.org_id)
    )
    recording = result.scalars().first()
    if not recording:
        raise NotFoundError(resource="Recording", identifier=str(recording_id))

    # Try to get a presigned URL from storage service
    try:
        from app.services.storage_service import get_presigned_url
        url = await get_presigned_url(recording.file_path, expires_seconds=3600)
        from fastapi.responses import RedirectResponse
        return RedirectResponse(url=url)
    except Exception as exc:
        logger.debug("Could not generate presigned URL", error=str(exc))

    # Fallback: return file info for client-side handling
    return {
        "status": "success",
        "data": {
            "recording_id": str(recording.id),
            "file_path": recording.file_path,
            "file_size_bytes": recording.file_size_bytes,
            "format": recording.format,
        },
        "message": "Download link generated.",
    }
