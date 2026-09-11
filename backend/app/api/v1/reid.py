"""
Cross-camera Person Re-Identification API endpoints.

Provides person tracking across cameras, journey timelines, image-based
search, cross-camera match review (confirm/reject), identity merge/split,
and aggregate statistics.
"""

from __future__ import annotations

import math
import uuid
from datetime import datetime
from typing import Optional

import structlog
from fastapi import APIRouter, Depends, File, HTTPException, Query, UploadFile, status
from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession

from app.database import get_db_session
from app.exceptions import NotFoundError, ValidationError
from app.middleware.auth import JWTBearer, TokenPayload
from app.models.reid import PersonJourney, PersonTrack, ReIDMatch
from app.models.user import User, UserRole
from app.schemas.common import ErrorResponse, SuccessResponse
from app.schemas.reid import (
    MergeRequest,
    SplitRequest,
)
from app.services import reid_service

logger = structlog.stdlib.get_logger(__name__)

router = APIRouter()


# ---------------------------------------------------------------------------
# Auth helpers (same pattern as faces.py)
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
        raise HTTPException(
            status_code=403, detail="Manager role or higher is required."
        )


# ---------------------------------------------------------------------------
# GET /persons - List tracked persons with pagination
# ---------------------------------------------------------------------------


@router.get(
    "/persons",
    response_model=SuccessResponse,
    summary="List tracked persons (paginated)",
)
async def list_tracked_persons(
    camera_id: uuid.UUID | None = Query(None, description="Filter by camera"),
    is_active: bool | None = Query(None, description="Filter active/inactive tracks"),
    page: int = Query(1, ge=1),
    page_size: int = Query(20, ge=1, le=100),
    user: User = Depends(_get_current_user),
    db: AsyncSession = Depends(get_db_session),
) -> dict:
    """Return a paginated list of person tracks for the organization."""
    from sqlalchemy import func, distinct

    base = select(PersonTrack).where(PersonTrack.org_id == user.org_id)

    if camera_id:
        base = base.where(PersonTrack.camera_id == camera_id)
    if is_active is not None:
        base = base.where(PersonTrack.is_active == is_active)

    count_q = select(func.count()).select_from(base.subquery())
    total = (await db.execute(count_q)).scalar() or 0

    query = (
        base
        .order_by(PersonTrack.last_seen.desc())
        .offset((page - 1) * page_size)
        .limit(page_size)
    )
    result = await db.execute(query)
    tracks = result.scalars().all()

    data = []
    for t in tracks:
        camera_name = t.camera.name if t.camera else None
        data.append({
            "id": str(t.id),
            "org_id": str(t.org_id),
            "global_person_id": str(t.global_person_id),
            "camera_id": str(t.camera_id),
            "camera_name": camera_name,
            "track_id": t.track_id,
            "first_seen": t.first_seen.isoformat() if t.first_seen else None,
            "last_seen": t.last_seen.isoformat() if t.last_seen else None,
            "thumbnail_path": t.thumbnail_path,
            "metadata_json": t.metadata_json,
            "is_active": t.is_active,
            "created_at": t.created_at.isoformat() if t.created_at else None,
            "updated_at": t.updated_at.isoformat() if t.updated_at else None,
        })

    return {
        "status": "success",
        "data": data,
        "meta": {
            "page": page,
            "page_size": page_size,
            "total": total,
            "total_pages": math.ceil(total / page_size) if page_size else 0,
        },
    }


# ---------------------------------------------------------------------------
# GET /persons/active - Currently visible persons
# ---------------------------------------------------------------------------


@router.get(
    "/persons/active",
    response_model=SuccessResponse,
    summary="Get currently visible persons across all cameras",
)
async def get_active_persons(
    user: User = Depends(_get_current_user),
    db: AsyncSession = Depends(get_db_session),
) -> dict:
    """Return persons with at least one active track across any camera."""
    persons = await reid_service.get_active_persons(db, user.org_id)
    return {
        "status": "success",
        "data": persons,
    }


# ---------------------------------------------------------------------------
# GET /persons/{global_person_id}/journey - Person journey timeline
# ---------------------------------------------------------------------------


@router.get(
    "/persons/{global_person_id}/journey",
    response_model=SuccessResponse,
    summary="Get cross-camera journey for a person",
    responses={404: {"model": ErrorResponse}},
)
async def get_person_journey(
    global_person_id: uuid.UUID,
    user: User = Depends(_get_current_user),
    db: AsyncSession = Depends(get_db_session),
) -> dict:
    """Retrieve the full cross-camera journey timeline for a person."""
    journey = await reid_service.get_person_journey(
        db, user.org_id, global_person_id
    )
    return {
        "status": "success",
        "data": journey,
    }


# ---------------------------------------------------------------------------
# GET /persons/{global_person_id}/heatmap - Camera visit heatmap
# ---------------------------------------------------------------------------


@router.get(
    "/persons/{global_person_id}/heatmap",
    response_model=SuccessResponse,
    summary="Get camera visit frequency for a person",
)
async def get_person_heatmap(
    global_person_id: uuid.UUID,
    user: User = Depends(_get_current_user),
    db: AsyncSession = Depends(get_db_session),
) -> dict:
    """Return camera visit frequency data for heatmap visualization."""
    heatmap = await reid_service.get_person_heatmap(
        db, user.org_id, global_person_id
    )
    return {
        "status": "success",
        "data": heatmap,
    }


# ---------------------------------------------------------------------------
# POST /search - Search person by image upload
# ---------------------------------------------------------------------------


@router.post(
    "/search",
    response_model=SuccessResponse,
    summary="Search for a person across cameras by image upload",
)
async def search_person(
    image: UploadFile = File(..., description="Person image to search for"),
    threshold: float = Query(0.4, ge=0.0, le=1.0, description="Minimum similarity"),
    top_k: int = Query(20, ge=1, le=100, description="Max results"),
    user: User = Depends(_get_current_user),
    db: AsyncSession = Depends(get_db_session),
) -> dict:
    """Upload a person image and find matching identities across all cameras."""
    if not image.content_type or not image.content_type.startswith("image/"):
        raise ValidationError(message="Uploaded file is not a valid image.")

    image_bytes = await image.read()

    try:
        import cv2
        import numpy as np

        nparr = np.frombuffer(image_bytes, np.uint8)
        img = cv2.imdecode(nparr, cv2.IMREAD_COLOR)
        if img is None:
            raise ValidationError(message="Failed to decode the uploaded image.")

        results = await reid_service.search_person(
            db, user.org_id, img, top_k=top_k, threshold=threshold
        )
    except ValidationError:
        raise
    except Exception as exc:
        logger.error("reid.search_failed", error=str(exc))
        raise ValidationError(message=f"Person search failed: {str(exc)}")

    return {
        "status": "success",
        "data": {
            "matches": results,
            "total": len(results),
        },
    }


# ---------------------------------------------------------------------------
# GET /matches - Cross-camera match history
# ---------------------------------------------------------------------------


@router.get(
    "/matches",
    response_model=SuccessResponse,
    summary="List cross-camera identity matches",
)
async def list_matches(
    start_date: datetime | None = Query(None),
    end_date: datetime | None = Query(None),
    min_similarity: float = Query(0.0, ge=0.0, le=1.0),
    is_confirmed: bool | None = Query(None, description="null=all, true=confirmed, false=rejected"),
    page: int = Query(1, ge=1),
    page_size: int = Query(20, ge=1, le=100),
    user: User = Depends(_get_current_user),
    db: AsyncSession = Depends(get_db_session),
) -> dict:
    """Return paginated cross-camera identity matches with optional filters."""
    items, total = await reid_service.get_cross_camera_matches(
        db,
        org_id=user.org_id,
        start_date=start_date,
        end_date=end_date,
        min_similarity=min_similarity,
        is_confirmed=is_confirmed,
        page=page,
        page_size=page_size,
    )

    return {
        "status": "success",
        "data": items,
        "meta": {
            "page": page,
            "page_size": page_size,
            "total": total,
            "total_pages": math.ceil(total / page_size) if page_size else 0,
        },
    }


# ---------------------------------------------------------------------------
# POST /matches/{id}/confirm - Confirm a match
# ---------------------------------------------------------------------------


@router.post(
    "/matches/{match_id}/confirm",
    response_model=SuccessResponse,
    summary="Confirm a cross-camera match",
    responses={404: {"model": ErrorResponse}},
)
async def confirm_match(
    match_id: uuid.UUID,
    user: User = Depends(_get_current_user),
    db: AsyncSession = Depends(get_db_session),
) -> dict:
    """Mark a cross-camera match as confirmed (same person)."""
    result = await reid_service.confirm_match(
        db, match_id, user.id, confirmed=True
    )
    return {
        "status": "success",
        "data": result,
        "message": "Match confirmed successfully.",
    }


# ---------------------------------------------------------------------------
# POST /matches/{id}/reject - Reject a match
# ---------------------------------------------------------------------------


@router.post(
    "/matches/{match_id}/reject",
    response_model=SuccessResponse,
    summary="Reject a cross-camera match",
    responses={404: {"model": ErrorResponse}},
)
async def reject_match(
    match_id: uuid.UUID,
    user: User = Depends(_get_current_user),
    db: AsyncSession = Depends(get_db_session),
) -> dict:
    """Reject a cross-camera match (different persons) and optionally split."""
    result = await reid_service.confirm_match(
        db, match_id, user.id, confirmed=False
    )
    return {
        "status": "success",
        "data": result,
        "message": "Match rejected successfully.",
    }


# ---------------------------------------------------------------------------
# POST /merge - Merge two person identities
# ---------------------------------------------------------------------------


@router.post(
    "/merge",
    response_model=SuccessResponse,
    summary="Merge two person identities",
    responses={403: {"model": ErrorResponse}},
)
async def merge_identities(
    body: MergeRequest,
    user: User = Depends(_get_current_user),
    db: AsyncSession = Depends(get_db_session),
) -> dict:
    """Merge two separate person identities into one.

    All tracks from ``source_global_person_id`` are reassigned to
    ``target_global_person_id``. Requires manager role or above.
    """
    _require_manager(user)

    result = await reid_service.merge_identities(
        db,
        org_id=user.org_id,
        target_global_person_id=body.target_global_person_id,
        source_global_person_id=body.source_global_person_id,
    )
    return {
        "status": "success",
        "data": result,
        "message": "Identities merged successfully.",
    }


# ---------------------------------------------------------------------------
# POST /split - Split incorrectly merged tracks
# ---------------------------------------------------------------------------


@router.post(
    "/split",
    response_model=SuccessResponse,
    summary="Split tracks from a person identity",
    responses={403: {"model": ErrorResponse}},
)
async def split_identity(
    body: SplitRequest,
    user: User = Depends(_get_current_user),
    db: AsyncSession = Depends(get_db_session),
) -> dict:
    """Split selected tracks from a person identity into a new identity.

    Requires manager role or above.
    """
    _require_manager(user)

    result = await reid_service.split_identity(
        db,
        org_id=user.org_id,
        global_person_id=body.global_person_id,
        track_ids=body.track_ids,
    )
    return {
        "status": "success",
        "data": result,
        "message": "Identity split successfully.",
    }


# ---------------------------------------------------------------------------
# GET /stats - ReID statistics
# ---------------------------------------------------------------------------


@router.get(
    "/stats",
    response_model=SuccessResponse,
    summary="Get ReID statistics",
)
async def get_reid_stats(
    user: User = Depends(_get_current_user),
    db: AsyncSession = Depends(get_db_session),
) -> dict:
    """Return aggregate re-identification statistics for the organization."""
    stats = await reid_service.get_reid_stats(db, user.org_id)
    return {
        "status": "success",
        "data": stats,
    }
