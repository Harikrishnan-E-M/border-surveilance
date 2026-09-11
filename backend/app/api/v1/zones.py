"""
Zone management API endpoints.

Provides CRUD operations for camera zones (regions of interest) including
polygon-based zone definitions, filtering by camera, and zone lifecycle.
"""

from __future__ import annotations

import math
import uuid
from datetime import datetime, timezone
from typing import Optional

import structlog
from fastapi import APIRouter, Depends, HTTPException, Query, status
from pydantic import BaseModel, Field
from sqlalchemy import func, select
from sqlalchemy.ext.asyncio import AsyncSession

from app.database import get_db_session
from app.exceptions import (
    AuthorizationError,
    NotFoundError,
    ValidationError,
)
from app.middleware.auth import JWTBearer, TokenPayload
from app.models.camera import Camera
from app.models.user import User, UserRole
from app.models.zone import Zone, ZoneType
from app.schemas.common import ErrorResponse, SuccessResponse

logger = structlog.stdlib.get_logger(__name__)

router = APIRouter()


# ---------------------------------------------------------------------------
# Pydantic schemas (endpoint-local)
# ---------------------------------------------------------------------------


class ZoneCreate(BaseModel):
    camera_id: uuid.UUID
    name: str = Field(..., min_length=1, max_length=255)
    zone_type: str = Field(..., description="Zone type: restricted, monitoring, entry_exit, parking, ppe_required, safe")
    polygon_points: list[list[float]] = Field(
        ..., min_length=3, description="List of [x, y] coordinate pairs defining the polygon"
    )
    color_hex: str = Field(default="#FF0000", max_length=9)
    is_active: bool = Field(default=True)


class ZoneUpdate(BaseModel):
    name: str | None = Field(default=None, max_length=255)
    zone_type: str | None = None
    polygon_points: list[list[float]] | None = Field(default=None, min_length=3)
    color_hex: str | None = Field(default=None, max_length=9)
    is_active: bool | None = None


class ZoneResponse(BaseModel):
    id: uuid.UUID
    camera_id: uuid.UUID
    name: str
    zone_type: str
    polygon_points: list
    color_hex: str
    is_active: bool
    created_at: datetime
    updated_at: datetime

    class Config:
        from_attributes = True


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
    """Raise 403 if the user is below manager role."""
    allowed = {UserRole.SUPER_ADMIN, UserRole.ORG_ADMIN, UserRole.MANAGER}
    if user.role not in allowed:
        raise AuthorizationError(message="Manager role or higher is required.")


def _zone_to_response(zone: Zone) -> dict:
    return ZoneResponse(
        id=zone.id,
        camera_id=zone.camera_id,
        name=zone.name,
        zone_type=zone.zone_type.value if isinstance(zone.zone_type, ZoneType) else zone.zone_type,
        polygon_points=zone.polygon_points,
        color_hex=zone.color_hex,
        is_active=zone.is_active,
        created_at=zone.created_at,
        updated_at=zone.updated_at,
    ).model_dump(mode="json")


# ---------------------------------------------------------------------------
# GET / - List zones
# ---------------------------------------------------------------------------


@router.get(
    "/",
    response_model=SuccessResponse,
    summary="List zones (optionally filter by camera)",
)
async def list_zones(
    camera_id: uuid.UUID | None = Query(None, description="Filter by camera ID"),
    is_active: bool | None = Query(None, description="Filter by active status"),
    page: int = Query(1, ge=1),
    page_size: int = Query(20, ge=1, le=100),
    user: User = Depends(_get_current_user),
    db: AsyncSession = Depends(get_db_session),
) -> dict:
    """Return a list of zones belonging to the user's organization."""
    # Build base query: zones whose camera belongs to the user's org
    query = (
        select(Zone)
        .join(Camera, Zone.camera_id == Camera.id)
        .where(Camera.org_id == user.org_id)
    )

    if camera_id is not None:
        query = query.where(Zone.camera_id == camera_id)
    if is_active is not None:
        query = query.where(Zone.is_active == is_active)

    count_q = select(func.count()).select_from(query.subquery())
    total = (await db.execute(count_q)).scalar() or 0

    query = query.order_by(Zone.created_at.desc()).offset((page - 1) * page_size).limit(page_size)
    result = await db.execute(query)
    zones = result.scalars().all()

    return {
        "status": "success",
        "data": [_zone_to_response(z) for z in zones],
        "meta": {
            "page": page,
            "page_size": page_size,
            "total": total,
            "total_pages": math.ceil(total / page_size) if page_size else 0,
        },
    }


# ---------------------------------------------------------------------------
# POST / - Create zone
# ---------------------------------------------------------------------------


@router.post(
    "/",
    response_model=SuccessResponse,
    status_code=status.HTTP_201_CREATED,
    summary="Create a new zone",
    responses={403: {"model": ErrorResponse}},
)
async def create_zone(
    body: ZoneCreate,
    user: User = Depends(_get_current_user),
    db: AsyncSession = Depends(get_db_session),
) -> dict:
    """Create a new zone on a camera. Requires manager role or above."""
    _require_manager(user)

    # Verify camera belongs to org
    cam_result = await db.execute(
        select(Camera).where(Camera.id == body.camera_id, Camera.org_id == user.org_id)
    )
    camera = cam_result.scalars().first()
    if not camera:
        raise NotFoundError(resource="Camera", identifier=str(body.camera_id))

    # Validate zone type
    try:
        zone_type = ZoneType(body.zone_type)
    except ValueError:
        valid_types = [t.value for t in ZoneType]
        raise ValidationError(
            message=f"Invalid zone_type '{body.zone_type}'. Must be one of: {', '.join(valid_types)}"
        )

    zone = Zone(
        camera_id=body.camera_id,
        name=body.name,
        zone_type=zone_type,
        polygon_points=body.polygon_points,
        color_hex=body.color_hex,
        is_active=body.is_active,
    )
    db.add(zone)
    await db.flush()

    logger.info("Zone created", zone_id=str(zone.id), name=zone.name, camera_id=str(body.camera_id))

    return {
        "status": "success",
        "data": _zone_to_response(zone),
        "message": "Zone created successfully.",
    }


# ---------------------------------------------------------------------------
# GET /{zone_id} - Get zone details
# ---------------------------------------------------------------------------


@router.get(
    "/{zone_id}",
    response_model=SuccessResponse,
    summary="Get zone details",
    responses={404: {"model": ErrorResponse}},
)
async def get_zone(
    zone_id: uuid.UUID,
    user: User = Depends(_get_current_user),
    db: AsyncSession = Depends(get_db_session),
) -> dict:
    """Retrieve a single zone by ID."""
    result = await db.execute(
        select(Zone)
        .join(Camera, Zone.camera_id == Camera.id)
        .where(Zone.id == zone_id, Camera.org_id == user.org_id)
    )
    zone = result.scalars().first()
    if not zone:
        raise NotFoundError(resource="Zone", identifier=str(zone_id))

    return {
        "status": "success",
        "data": _zone_to_response(zone),
    }


# ---------------------------------------------------------------------------
# PUT /{zone_id} - Update zone
# ---------------------------------------------------------------------------


@router.put(
    "/{zone_id}",
    response_model=SuccessResponse,
    summary="Update zone",
    responses={403: {"model": ErrorResponse}, 404: {"model": ErrorResponse}},
)
async def update_zone(
    zone_id: uuid.UUID,
    body: ZoneUpdate,
    user: User = Depends(_get_current_user),
    db: AsyncSession = Depends(get_db_session),
) -> dict:
    """Update zone fields. Requires manager role or above."""
    _require_manager(user)

    result = await db.execute(
        select(Zone)
        .join(Camera, Zone.camera_id == Camera.id)
        .where(Zone.id == zone_id, Camera.org_id == user.org_id)
    )
    zone = result.scalars().first()
    if not zone:
        raise NotFoundError(resource="Zone", identifier=str(zone_id))

    update_data = body.model_dump(exclude_unset=True)

    if "zone_type" in update_data:
        try:
            update_data["zone_type"] = ZoneType(update_data["zone_type"])
        except ValueError:
            valid_types = [t.value for t in ZoneType]
            raise ValidationError(
                message=f"Invalid zone_type. Must be one of: {', '.join(valid_types)}"
            )

    for field, value in update_data.items():
        setattr(zone, field, value)

    db.add(zone)
    await db.flush()

    logger.info("Zone updated", zone_id=str(zone.id))

    return {
        "status": "success",
        "data": _zone_to_response(zone),
        "message": "Zone updated successfully.",
    }


# ---------------------------------------------------------------------------
# DELETE /{zone_id} - Delete zone
# ---------------------------------------------------------------------------


@router.delete(
    "/{zone_id}",
    response_model=SuccessResponse,
    summary="Delete zone",
    responses={403: {"model": ErrorResponse}, 404: {"model": ErrorResponse}},
)
async def delete_zone(
    zone_id: uuid.UUID,
    user: User = Depends(_get_current_user),
    db: AsyncSession = Depends(get_db_session),
) -> dict:
    """Delete a zone. Requires manager role or above."""
    _require_manager(user)

    result = await db.execute(
        select(Zone)
        .join(Camera, Zone.camera_id == Camera.id)
        .where(Zone.id == zone_id, Camera.org_id == user.org_id)
    )
    zone = result.scalars().first()
    if not zone:
        raise NotFoundError(resource="Zone", identifier=str(zone_id))

    await db.delete(zone)
    await db.flush()

    logger.info("Zone deleted", zone_id=str(zone_id))

    return {
        "status": "success",
        "data": None,
        "message": "Zone deleted successfully.",
    }


# ---------------------------------------------------------------------------
# GET /camera/{camera_id} - Get all zones for a camera
# ---------------------------------------------------------------------------


@router.get(
    "/camera/{camera_id}",
    response_model=SuccessResponse,
    summary="Get all zones for a camera",
    responses={404: {"model": ErrorResponse}},
)
async def get_zones_for_camera(
    camera_id: uuid.UUID,
    is_active: bool | None = Query(None),
    user: User = Depends(_get_current_user),
    db: AsyncSession = Depends(get_db_session),
) -> dict:
    """Retrieve all zones for a specific camera."""
    # Verify camera belongs to org
    cam_result = await db.execute(
        select(Camera).where(Camera.id == camera_id, Camera.org_id == user.org_id)
    )
    camera = cam_result.scalars().first()
    if not camera:
        raise NotFoundError(resource="Camera", identifier=str(camera_id))

    query = select(Zone).where(Zone.camera_id == camera_id)
    if is_active is not None:
        query = query.where(Zone.is_active == is_active)
    query = query.order_by(Zone.created_at.desc())

    result = await db.execute(query)
    zones = result.scalars().all()

    return {
        "status": "success",
        "data": [_zone_to_response(z) for z in zones],
    }
