"""
CLIP-powered Natural Language Video Search API endpoints.

Provides text and image search across indexed video frames, search
history, per-camera indexing status, and indexing trigger endpoints.
"""

from __future__ import annotations

import uuid
from datetime import datetime
from typing import Optional

import cv2
import numpy as np
import structlog
from fastapi import APIRouter, Depends, File, Form, HTTPException, Query, UploadFile, status
from sqlalchemy.ext.asyncio import AsyncSession

from app.database import get_db_session
from app.exceptions import NotFoundError, ValidationError
from app.middleware.auth import JWTBearer, TokenPayload
from app.models.camera import Camera
from app.models.recording import Recording
from app.models.user import User, UserRole
from app.schemas.clip_search import (
    ImageSearchRequest,
    IndexCameraRequest,
    IndexingStatusResponse,
    IndexingTaskResponse,
    IndexRecordingRequest,
    SearchHistoryResponse,
    SearchResponse,
    TextSearchRequest,
)
from app.schemas.common import ErrorResponse, SuccessResponse
from sqlalchemy import select

logger = structlog.stdlib.get_logger(__name__)

router = APIRouter()


# ---------------------------------------------------------------------------
# Helpers
# ---------------------------------------------------------------------------


async def _get_current_user(
    token: TokenPayload = Depends(JWTBearer()),
    db: AsyncSession = Depends(get_db_session),
) -> User:
    """Load the authenticated user from the JWT token."""
    result = await db.execute(
        select(User).where(User.id == uuid.UUID(token.sub), User.is_active.is_(True))
    )
    user = result.scalars().first()
    if not user:
        raise HTTPException(status_code=401, detail="User not found or deactivated.")
    return user


def _require_manager(user: User) -> None:
    """Raise AuthorizationError if the user is not a manager or above."""
    from app.exceptions import AuthorizationError

    allowed = {UserRole.SUPER_ADMIN, UserRole.ORG_ADMIN, UserRole.MANAGER}
    if user.role not in allowed:
        raise AuthorizationError(message="Manager role or higher is required.")


# ---------------------------------------------------------------------------
# POST /search/text - Natural language text search
# ---------------------------------------------------------------------------


@router.post(
    "/text",
    response_model=SuccessResponse,
    summary="Search video frames using natural language",
    responses={422: {"model": ErrorResponse}},
)
async def search_text(
    body: TextSearchRequest,
    user: User = Depends(_get_current_user),
    db: AsyncSession = Depends(get_db_session),
) -> dict:
    """Search indexed video frames using a natural language text query.

    Encodes the query text with CLIP and finds the most semantically
    similar frames using pgvector cosine similarity search.

    Returns ranked results with thumbnails, similarity scores,
    camera info, and timestamps.
    """
    from app.services.clip_search_service import search_by_text

    try:
        result = await search_by_text(
            db=db,
            org_id=user.org_id,
            user_id=user.id,
            query_text=body.query,
            cameras=body.cameras,
            date_from=body.date_from,
            date_to=body.date_to,
            time_of_day_start=body.time_of_day_start,
            time_of_day_end=body.time_of_day_end,
            top_k=body.top_k,
            min_similarity=body.min_similarity,
        )
    except RuntimeError as exc:
        logger.error("CLIP search failed", error=str(exc))
        raise ValidationError(
            message="Search service is not available. CLIP models may not be loaded."
        )
    except Exception as exc:
        logger.error("Search failed", error=str(exc))
        raise ValidationError(message=f"Search failed: {str(exc)}")

    return {
        "status": "success",
        "data": result,
    }


# ---------------------------------------------------------------------------
# POST /search/image - Image similarity search
# ---------------------------------------------------------------------------


@router.post(
    "/image",
    response_model=SuccessResponse,
    summary="Search video frames using an image query",
    responses={422: {"model": ErrorResponse}},
)
async def search_image(
    file: UploadFile = File(..., description="Query image file (JPEG, PNG)"),
    cameras: Optional[str] = Form(
        default=None,
        description="Comma-separated camera UUIDs to filter",
    ),
    date_from: Optional[str] = Form(default=None, description="Start date ISO-8601"),
    date_to: Optional[str] = Form(default=None, description="End date ISO-8601"),
    top_k: int = Form(default=50, ge=1, le=500, description="Max results"),
    min_similarity: float = Form(default=0.15, ge=0.0, le=1.0),
    user: User = Depends(_get_current_user),
    db: AsyncSession = Depends(get_db_session),
) -> dict:
    """Search indexed video frames using an uploaded image.

    Upload a reference image and find the most visually similar frames
    across all indexed recordings.
    """
    # Validate file type
    allowed_types = {"image/jpeg", "image/png", "image/webp", "image/bmp"}
    if file.content_type and file.content_type not in allowed_types:
        raise ValidationError(
            message=f"Unsupported image format '{file.content_type}'. Allowed: JPEG, PNG, WebP, BMP."
        )

    # Read and decode image
    file_bytes = await file.read()
    if len(file_bytes) == 0:
        raise ValidationError(message="Uploaded file is empty.")

    if len(file_bytes) > 10 * 1024 * 1024:  # 10MB limit
        raise ValidationError(message="Image file too large. Maximum size is 10MB.")

    nparr = np.frombuffer(file_bytes, np.uint8)
    image = cv2.imdecode(nparr, cv2.IMREAD_COLOR)
    if image is None:
        raise ValidationError(message="Could not decode the uploaded image.")

    # Parse camera filter
    camera_ids = None
    if cameras:
        try:
            camera_ids = [uuid.UUID(c.strip()) for c in cameras.split(",") if c.strip()]
        except ValueError:
            raise ValidationError(message="Invalid camera UUID in filter.")

    # Parse date filters
    parsed_date_from = None
    parsed_date_to = None
    if date_from:
        try:
            parsed_date_from = datetime.fromisoformat(date_from)
        except ValueError:
            raise ValidationError(message="Invalid date_from format. Use ISO-8601.")
    if date_to:
        try:
            parsed_date_to = datetime.fromisoformat(date_to)
        except ValueError:
            raise ValidationError(message="Invalid date_to format. Use ISO-8601.")

    from app.services.clip_search_service import search_by_image

    try:
        result = await search_by_image(
            db=db,
            org_id=user.org_id,
            user_id=user.id,
            image=image,
            cameras=camera_ids,
            date_from=parsed_date_from,
            date_to=parsed_date_to,
            top_k=top_k,
            min_similarity=min_similarity,
        )
    except RuntimeError as exc:
        logger.error("CLIP image search failed", error=str(exc))
        raise ValidationError(
            message="Search service is not available. CLIP models may not be loaded."
        )
    except Exception as exc:
        logger.error("Image search failed", error=str(exc))
        raise ValidationError(message=f"Image search failed: {str(exc)}")

    return {
        "status": "success",
        "data": result,
    }


# ---------------------------------------------------------------------------
# GET /search/history - User's search history
# ---------------------------------------------------------------------------


@router.get(
    "/history",
    response_model=SuccessResponse,
    summary="Get user's search history",
)
async def get_search_history(
    limit: int = Query(default=50, ge=1, le=200, description="Maximum history items"),
    user: User = Depends(_get_current_user),
    db: AsyncSession = Depends(get_db_session),
) -> dict:
    """Retrieve the authenticated user's recent search queries.

    Returns the most recent searches with query text, result counts,
    and execution times.
    """
    from app.services.clip_search_service import get_search_history as _get_history

    result = await _get_history(
        db=db,
        user_id=user.id,
        limit=limit,
    )

    return {
        "status": "success",
        "data": result,
    }


# ---------------------------------------------------------------------------
# GET /search/indexing-status - Per-camera indexing status
# ---------------------------------------------------------------------------


@router.get(
    "/indexing-status",
    response_model=SuccessResponse,
    summary="Get per-camera CLIP indexing status",
)
async def get_indexing_status(
    user: User = Depends(_get_current_user),
    db: AsyncSession = Depends(get_db_session),
) -> dict:
    """Return indexing coverage for each camera in the organization.

    Shows the number of indexed frames, last indexed time, and
    whether indexing is currently in progress for each camera.
    """
    from app.services.clip_search_service import get_indexing_status as _get_status

    result = await _get_status(
        db=db,
        org_id=user.org_id,
    )

    return {
        "status": "success",
        "data": result,
    }


# ---------------------------------------------------------------------------
# POST /search/index/{recording_id} - Trigger recording indexing
# ---------------------------------------------------------------------------


@router.post(
    "/index/{recording_id}",
    response_model=SuccessResponse,
    status_code=status.HTTP_202_ACCEPTED,
    summary="Trigger CLIP indexing for a recording",
    responses={404: {"model": ErrorResponse}},
)
async def index_recording(
    recording_id: uuid.UUID,
    body: IndexRecordingRequest = IndexRecordingRequest(),
    user: User = Depends(_get_current_user),
    db: AsyncSession = Depends(get_db_session),
) -> dict:
    """Queue a background task to index a recording's frames with CLIP.

    Extracts frames at the specified FPS, encodes them with the CLIP
    visual encoder, and stores embeddings in pgvector for search.
    """
    _require_manager(user)

    # Verify recording exists and belongs to org
    result = await db.execute(
        select(Recording).where(
            Recording.id == recording_id,
            Recording.org_id == user.org_id,
        )
    )
    recording = result.scalars().first()
    if not recording:
        raise NotFoundError(resource="Recording", identifier=str(recording_id))

    # Dispatch Celery task
    try:
        from app.workers.clip_tasks import index_recording_task

        task = index_recording_task.delay(
            recording_id=str(recording_id),
            fps=body.fps,
        )
        task_id = task.id
    except Exception as exc:
        logger.error("Failed to dispatch indexing task", error=str(exc))
        raise ValidationError(
            message=f"Failed to queue indexing task: {str(exc)}"
        )

    logger.info(
        "Recording indexing queued",
        recording_id=str(recording_id),
        task_id=task_id,
        fps=body.fps,
    )

    return {
        "status": "success",
        "data": {
            "task_id": task_id,
            "status": "queued",
            "message": f"Indexing queued for recording {recording_id} at {body.fps} FPS.",
        },
        "message": "Recording indexing has been queued.",
    }


# ---------------------------------------------------------------------------
# POST /search/index/camera/{camera_id} - Start continuous frame indexing
# ---------------------------------------------------------------------------


@router.post(
    "/index/camera/{camera_id}",
    response_model=SuccessResponse,
    status_code=status.HTTP_202_ACCEPTED,
    summary="Start continuous CLIP indexing for a camera",
    responses={404: {"model": ErrorResponse}},
)
async def start_camera_indexing(
    camera_id: uuid.UUID,
    body: IndexCameraRequest = IndexCameraRequest(),
    user: User = Depends(_get_current_user),
    db: AsyncSession = Depends(get_db_session),
) -> dict:
    """Start a background task that continuously indexes frames from a
    camera's live stream at the specified FPS.
    """
    _require_manager(user)

    # Verify camera exists and belongs to org
    result = await db.execute(
        select(Camera).where(
            Camera.id == camera_id,
            Camera.org_id == user.org_id,
        )
    )
    camera = result.scalars().first()
    if not camera:
        raise NotFoundError(resource="Camera", identifier=str(camera_id))

    # Dispatch Celery task
    try:
        from app.workers.clip_tasks import index_live_frames_task

        task = index_live_frames_task.delay(
            camera_id=str(camera_id),
            fps=body.fps,
        )
        task_id = task.id
    except Exception as exc:
        logger.error("Failed to dispatch live indexing task", error=str(exc))
        raise ValidationError(
            message=f"Failed to queue live indexing task: {str(exc)}"
        )

    logger.info(
        "Camera live indexing started",
        camera_id=str(camera_id),
        task_id=task_id,
        fps=body.fps,
    )

    return {
        "status": "success",
        "data": {
            "task_id": task_id,
            "status": "queued",
            "message": f"Live indexing started for camera {camera.name} at {body.fps} FPS.",
        },
        "message": "Camera live indexing has been started.",
    }


# ---------------------------------------------------------------------------
# DELETE /search/index/camera/{camera_id} - Delete camera's index
# ---------------------------------------------------------------------------


@router.delete(
    "/index/camera/{camera_id}",
    response_model=SuccessResponse,
    summary="Delete all CLIP embeddings for a camera",
    responses={
        403: {"model": ErrorResponse},
        404: {"model": ErrorResponse},
    },
)
async def delete_camera_index(
    camera_id: uuid.UUID,
    user: User = Depends(_get_current_user),
    db: AsyncSession = Depends(get_db_session),
) -> dict:
    """Delete all indexed frame embeddings for a camera.

    This removes all CLIP embeddings for the specified camera from
    pgvector. Search results for this camera will no longer appear
    until the camera is re-indexed.
    """
    _require_manager(user)

    # Verify camera exists
    result = await db.execute(
        select(Camera).where(
            Camera.id == camera_id,
            Camera.org_id == user.org_id,
        )
    )
    camera = result.scalars().first()
    if not camera:
        raise NotFoundError(resource="Camera", identifier=str(camera_id))

    from app.services.clip_search_service import delete_index

    deletion_result = await delete_index(
        db=db,
        camera_id=camera_id,
        org_id=user.org_id,
    )

    return {
        "status": "success",
        "data": deletion_result,
        "message": f"Deleted {deletion_result['frames_deleted']} frame embeddings for camera '{camera.name}'.",
    }
