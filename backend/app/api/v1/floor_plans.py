"""
Floor Plan API endpoints for the Digital Twin feature.

Provides CRUD for floor plans with image upload, camera and zone
placement management, live status overlay data, heatmap generation,
and person track mapping onto the spatial layout.
"""

from __future__ import annotations

import json
import math
import uuid
from datetime import datetime, timezone

import structlog
from fastapi import (
    APIRouter,
    Depends,
    File,
    Form,
    HTTPException,
    Query,
    UploadFile,
    status,
)
from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession

from app.database import get_db_session
from app.exceptions import (
    AuthorizationError,
    NotFoundError,
    StorageError,
    ValidationError,
)
from app.middleware.auth import JWTBearer, TokenPayload
from app.models.floor_plan import (
    CameraPlacement,
    FloorPlan,
    FloorPlanOverlay,
    OverlayType,
    ZonePlacement,
)
from app.models.user import User, UserRole
from app.schemas.common import ErrorResponse, SuccessResponse
from app.schemas.floor_plan import (
    CameraPlacementCreate,
    CameraPlacementUpdate,
    FloorPlanUpdate,
    HeatmapGenerateRequest,
    ZonePlacementCreate,
    ZonePlacementUpdate,
)
from app.services import floor_plan_service as svc

logger = structlog.stdlib.get_logger(__name__)

router = APIRouter()


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


def _floor_plan_to_list_item(fp: FloorPlan) -> dict:
    """Convert a FloorPlan model to a lightweight list dict."""
    return {
        "id": str(fp.id),
        "org_id": str(fp.org_id),
        "name": fp.name,
        "building_name": fp.building_name,
        "floor_number": fp.floor_number,
        "image_url": fp.image_path,
        "width_px": fp.width_px,
        "height_px": fp.height_px,
        "is_active": fp.is_active,
        "camera_count": len(fp.camera_placements or []),
        "zone_count": len(fp.zone_placements or []),
        "created_at": fp.created_at.isoformat() if fp.created_at else None,
        "updated_at": fp.updated_at.isoformat() if fp.updated_at else None,
    }


def _floor_plan_to_response(fp: FloorPlan) -> dict:
    """Convert a FloorPlan model to a full response dict with placements."""
    camera_placements = []
    for cp in fp.camera_placements or []:
        camera_placements.append({
            "id": str(cp.id),
            "floor_plan_id": str(cp.floor_plan_id),
            "camera_id": str(cp.camera_id),
            "x_position": cp.x_position,
            "y_position": cp.y_position,
            "rotation_degrees": cp.rotation_degrees,
            "fov_angle": cp.fov_angle,
            "fov_range": cp.fov_range,
            "label": cp.label,
            "camera_name": cp.camera.name if cp.camera else None,
            "camera_is_online": cp.camera.is_online if cp.camera else None,
            "created_at": cp.created_at.isoformat() if cp.created_at else None,
            "updated_at": cp.updated_at.isoformat() if cp.updated_at else None,
        })

    zone_placements = []
    for zp in fp.zone_placements or []:
        zone_placements.append({
            "id": str(zp.id),
            "floor_plan_id": str(zp.floor_plan_id),
            "zone_id": str(zp.zone_id),
            "polygon_points": zp.polygon_points,
            "color": zp.color,
            "opacity": zp.opacity,
            "zone_name": zp.zone.name if zp.zone else None,
            "zone_type": zp.zone.zone_type.value if zp.zone and hasattr(zp.zone.zone_type, "value") else None,
            "created_at": zp.created_at.isoformat() if zp.created_at else None,
            "updated_at": zp.updated_at.isoformat() if zp.updated_at else None,
        })

    overlays = []
    for ov in fp.overlays or []:
        overlays.append({
            "id": str(ov.id),
            "floor_plan_id": str(ov.floor_plan_id),
            "overlay_type": ov.overlay_type.value if hasattr(ov.overlay_type, "value") else str(ov.overlay_type),
            "image_url": ov.data_url,
            "generated_at": ov.generated_at.isoformat() if ov.generated_at else None,
            "valid_until": ov.valid_until.isoformat() if ov.valid_until else None,
        })

    return {
        "id": str(fp.id),
        "org_id": str(fp.org_id),
        "name": fp.name,
        "building_name": fp.building_name,
        "floor_number": fp.floor_number,
        "image_url": fp.image_path,
        "width_px": fp.width_px,
        "height_px": fp.height_px,
        "scale_meters_per_pixel": fp.scale_meters_per_pixel,
        "metadata_json": fp.metadata_json,
        "is_active": fp.is_active,
        "camera_placements": camera_placements,
        "zone_placements": zone_placements,
        "overlays": overlays,
        "created_at": fp.created_at.isoformat() if fp.created_at else None,
        "updated_at": fp.updated_at.isoformat() if fp.updated_at else None,
    }


def _camera_placement_to_response(cp: CameraPlacement) -> dict:
    """Convert a CameraPlacement model to a response dict."""
    return {
        "id": str(cp.id),
        "floor_plan_id": str(cp.floor_plan_id),
        "camera_id": str(cp.camera_id),
        "x_position": cp.x_position,
        "y_position": cp.y_position,
        "rotation_degrees": cp.rotation_degrees,
        "fov_angle": cp.fov_angle,
        "fov_range": cp.fov_range,
        "label": cp.label,
        "camera_name": cp.camera.name if cp.camera else None,
        "camera_is_online": cp.camera.is_online if cp.camera else None,
        "created_at": cp.created_at.isoformat() if cp.created_at else None,
        "updated_at": cp.updated_at.isoformat() if cp.updated_at else None,
    }


def _zone_placement_to_response(zp: ZonePlacement) -> dict:
    """Convert a ZonePlacement model to a response dict."""
    return {
        "id": str(zp.id),
        "floor_plan_id": str(zp.floor_plan_id),
        "zone_id": str(zp.zone_id),
        "polygon_points": zp.polygon_points,
        "color": zp.color,
        "opacity": zp.opacity,
        "zone_name": zp.zone.name if zp.zone else None,
        "zone_type": zp.zone.zone_type.value if zp.zone and hasattr(zp.zone.zone_type, "value") else None,
        "created_at": zp.created_at.isoformat() if zp.created_at else None,
        "updated_at": zp.updated_at.isoformat() if zp.updated_at else None,
    }


# ---------------------------------------------------------------------------
# POST / - Upload floor plan (multipart with image)
# ---------------------------------------------------------------------------


@router.post(
    "/",
    response_model=SuccessResponse,
    status_code=status.HTTP_201_CREATED,
    summary="Upload a new floor plan",
    responses={403: {"model": ErrorResponse}},
)
async def create_floor_plan(
    image: UploadFile = File(..., description="Floor plan image file (PNG, JPG, SVG)"),
    name: str = Form(..., min_length=1, max_length=255, description="Floor plan display name"),
    building_name: str | None = Form(default=None, max_length=255, description="Building name"),
    floor_number: int = Form(default=0, description="Floor number"),
    scale_meters_per_pixel: float | None = Form(default=None, description="Scale factor"),
    metadata_json: str | None = Form(default=None, description="JSON metadata string"),
    user: User = Depends(_get_current_user),
    db: AsyncSession = Depends(get_db_session),
) -> dict:
    """Upload a floor plan image and create the floor plan record.

    The image is stored in MinIO. Floor plan metadata is provided as
    form fields alongside the image file.
    """
    _require_manager(user)

    # Validate image
    if not image.filename:
        raise ValidationError(message="Image file is required.")

    allowed_types = {"image/png", "image/jpeg", "image/jpg", "image/svg+xml", "image/webp"}
    if image.content_type and image.content_type not in allowed_types:
        raise ValidationError(
            message=f"Unsupported image type '{image.content_type}'. Allowed: PNG, JPG, SVG, WebP."
        )

    image_data = await image.read()
    if len(image_data) > 50 * 1024 * 1024:  # 50 MB limit
        raise ValidationError(message="Image file too large. Maximum size is 50 MB.")

    # Parse optional JSON metadata
    parsed_metadata = None
    if metadata_json:
        try:
            parsed_metadata = json.loads(metadata_json)
        except json.JSONDecodeError:
            raise ValidationError(message="metadata_json must be valid JSON.")

    data = {
        "name": name,
        "building_name": building_name,
        "floor_number": floor_number,
        "scale_meters_per_pixel": scale_meters_per_pixel,
        "metadata_json": parsed_metadata,
    }

    floor_plan = await svc.create_floor_plan(
        db=db,
        org_id=user.org_id,
        data=data,
        image_data=image_data,
        image_filename=image.filename or "floor_plan.png",
        content_type=image.content_type or "image/png",
    )

    logger.info("Floor plan created via API", floor_plan_id=str(floor_plan.id))

    return {
        "status": "success",
        "data": _floor_plan_to_response(floor_plan),
        "message": "Floor plan created successfully.",
    }


# ---------------------------------------------------------------------------
# GET / - List floor plans
# ---------------------------------------------------------------------------


@router.get(
    "/",
    response_model=SuccessResponse,
    summary="List floor plans",
)
async def list_floor_plans(
    user: User = Depends(_get_current_user),
    db: AsyncSession = Depends(get_db_session),
) -> dict:
    """Return all floor plans for the user's organisation."""
    floor_plans = await svc.get_floor_plans(db, user.org_id)

    return {
        "status": "success",
        "data": [_floor_plan_to_list_item(fp) for fp in floor_plans],
    }


# ---------------------------------------------------------------------------
# GET /{id} - Get floor plan with all placements
# ---------------------------------------------------------------------------


@router.get(
    "/{floor_plan_id}",
    response_model=SuccessResponse,
    summary="Get floor plan details",
    responses={404: {"model": ErrorResponse}},
)
async def get_floor_plan(
    floor_plan_id: uuid.UUID,
    user: User = Depends(_get_current_user),
    db: AsyncSession = Depends(get_db_session),
) -> dict:
    """Retrieve a floor plan with all camera/zone placements and overlays."""
    floor_plan = await svc.get_floor_plan(db, floor_plan_id, user.org_id)

    return {
        "status": "success",
        "data": _floor_plan_to_response(floor_plan),
    }


# ---------------------------------------------------------------------------
# PUT /{id} - Update floor plan
# ---------------------------------------------------------------------------


@router.put(
    "/{floor_plan_id}",
    response_model=SuccessResponse,
    summary="Update floor plan metadata",
    responses={403: {"model": ErrorResponse}, 404: {"model": ErrorResponse}},
)
async def update_floor_plan(
    floor_plan_id: uuid.UUID,
    body: FloorPlanUpdate,
    user: User = Depends(_get_current_user),
    db: AsyncSession = Depends(get_db_session),
) -> dict:
    """Update floor plan metadata. Requires manager role or above."""
    _require_manager(user)

    update_data = body.model_dump(exclude_unset=True)
    floor_plan = await svc.update_floor_plan(db, floor_plan_id, update_data, user.org_id)

    return {
        "status": "success",
        "data": _floor_plan_to_response(floor_plan),
        "message": "Floor plan updated successfully.",
    }


# ---------------------------------------------------------------------------
# DELETE /{id} - Delete floor plan
# ---------------------------------------------------------------------------


@router.delete(
    "/{floor_plan_id}",
    response_model=SuccessResponse,
    summary="Delete floor plan",
    responses={403: {"model": ErrorResponse}, 404: {"model": ErrorResponse}},
)
async def delete_floor_plan(
    floor_plan_id: uuid.UUID,
    user: User = Depends(_get_current_user),
    db: AsyncSession = Depends(get_db_session),
) -> dict:
    """Delete a floor plan and all associated placements and overlays."""
    _require_manager(user)

    await svc.delete_floor_plan(db, floor_plan_id, user.org_id)

    return {
        "status": "success",
        "data": None,
        "message": "Floor plan deleted successfully.",
    }


# ---------------------------------------------------------------------------
# POST /{id}/cameras - Place camera
# ---------------------------------------------------------------------------


@router.post(
    "/{floor_plan_id}/cameras",
    response_model=SuccessResponse,
    status_code=status.HTTP_201_CREATED,
    summary="Place a camera on the floor plan",
    responses={403: {"model": ErrorResponse}, 404: {"model": ErrorResponse}},
)
async def place_camera(
    floor_plan_id: uuid.UUID,
    body: CameraPlacementCreate,
    user: User = Depends(_get_current_user),
    db: AsyncSession = Depends(get_db_session),
) -> dict:
    """Place a camera at a specific position on the floor plan."""
    _require_manager(user)

    position = body.model_dump()
    camera_id = position.pop("camera_id")

    placement = await svc.place_camera(
        db=db,
        floor_plan_id=floor_plan_id,
        camera_id=camera_id,
        position=position,
        org_id=user.org_id,
    )

    return {
        "status": "success",
        "data": _camera_placement_to_response(placement),
        "message": "Camera placed on floor plan successfully.",
    }


# ---------------------------------------------------------------------------
# PUT /{id}/cameras/{placement_id} - Update camera placement
# ---------------------------------------------------------------------------


@router.put(
    "/{floor_plan_id}/cameras/{placement_id}",
    response_model=SuccessResponse,
    summary="Update camera placement",
    responses={403: {"model": ErrorResponse}, 404: {"model": ErrorResponse}},
)
async def update_camera_placement(
    floor_plan_id: uuid.UUID,
    placement_id: uuid.UUID,
    body: CameraPlacementUpdate,
    user: User = Depends(_get_current_user),
    db: AsyncSession = Depends(get_db_session),
) -> dict:
    """Move or re-orient a camera on the floor plan."""
    _require_manager(user)

    position = body.model_dump(exclude_unset=True)
    placement = await svc.update_camera_placement(db, placement_id, position, user.org_id)

    return {
        "status": "success",
        "data": _camera_placement_to_response(placement),
        "message": "Camera placement updated successfully.",
    }


# ---------------------------------------------------------------------------
# DELETE /{id}/cameras/{placement_id} - Remove camera
# ---------------------------------------------------------------------------


@router.delete(
    "/{floor_plan_id}/cameras/{placement_id}",
    response_model=SuccessResponse,
    summary="Remove camera from floor plan",
    responses={403: {"model": ErrorResponse}, 404: {"model": ErrorResponse}},
)
async def remove_camera_placement(
    floor_plan_id: uuid.UUID,
    placement_id: uuid.UUID,
    user: User = Depends(_get_current_user),
    db: AsyncSession = Depends(get_db_session),
) -> dict:
    """Remove a camera from the floor plan."""
    _require_manager(user)

    await svc.remove_camera_placement(db, placement_id, user.org_id)

    return {
        "status": "success",
        "data": None,
        "message": "Camera removed from floor plan.",
    }


# ---------------------------------------------------------------------------
# POST /{id}/zones - Place zone
# ---------------------------------------------------------------------------


@router.post(
    "/{floor_plan_id}/zones",
    response_model=SuccessResponse,
    status_code=status.HTTP_201_CREATED,
    summary="Place a zone on the floor plan",
    responses={403: {"model": ErrorResponse}, 404: {"model": ErrorResponse}},
)
async def place_zone(
    floor_plan_id: uuid.UUID,
    body: ZonePlacementCreate,
    user: User = Depends(_get_current_user),
    db: AsyncSession = Depends(get_db_session),
) -> dict:
    """Draw a zone polygon on the floor plan."""
    _require_manager(user)

    polygon = body.model_dump()
    zone_id = polygon.pop("zone_id")
    # Convert polygon_points from list of dicts to list of dicts with x, y
    if "polygon_points" in polygon:
        polygon["polygon_points"] = [
            {"x": p["x"], "y": p["y"]} if isinstance(p, dict) else {"x": p.x, "y": p.y}
            for p in polygon["polygon_points"]
        ]

    placement = await svc.place_zone(
        db=db,
        floor_plan_id=floor_plan_id,
        zone_id=zone_id,
        polygon=polygon,
        org_id=user.org_id,
    )

    return {
        "status": "success",
        "data": _zone_placement_to_response(placement),
        "message": "Zone placed on floor plan successfully.",
    }


# ---------------------------------------------------------------------------
# PUT /{id}/zones/{placement_id} - Update zone placement
# ---------------------------------------------------------------------------


@router.put(
    "/{floor_plan_id}/zones/{placement_id}",
    response_model=SuccessResponse,
    summary="Update zone placement",
    responses={403: {"model": ErrorResponse}, 404: {"model": ErrorResponse}},
)
async def update_zone_placement(
    floor_plan_id: uuid.UUID,
    placement_id: uuid.UUID,
    body: ZonePlacementUpdate,
    user: User = Depends(_get_current_user),
    db: AsyncSession = Depends(get_db_session),
) -> dict:
    """Update a zone's polygon shape, colour, or opacity on the floor plan."""
    _require_manager(user)

    polygon = body.model_dump(exclude_unset=True)
    if "polygon_points" in polygon and polygon["polygon_points"] is not None:
        polygon["polygon_points"] = [
            {"x": p["x"], "y": p["y"]} if isinstance(p, dict) else {"x": p.x, "y": p.y}
            for p in polygon["polygon_points"]
        ]

    placement = await svc.update_zone_placement(db, placement_id, polygon, user.org_id)

    return {
        "status": "success",
        "data": _zone_placement_to_response(placement),
        "message": "Zone placement updated successfully.",
    }


# ---------------------------------------------------------------------------
# DELETE /{id}/zones/{placement_id} - Remove zone
# ---------------------------------------------------------------------------


@router.delete(
    "/{floor_plan_id}/zones/{placement_id}",
    response_model=SuccessResponse,
    summary="Remove zone from floor plan",
    responses={403: {"model": ErrorResponse}, 404: {"model": ErrorResponse}},
)
async def remove_zone_placement(
    floor_plan_id: uuid.UUID,
    placement_id: uuid.UUID,
    user: User = Depends(_get_current_user),
    db: AsyncSession = Depends(get_db_session),
) -> dict:
    """Remove a zone from the floor plan."""
    _require_manager(user)

    await svc.remove_zone_placement(db, placement_id, user.org_id)

    return {
        "status": "success",
        "data": None,
        "message": "Zone removed from floor plan.",
    }


# ---------------------------------------------------------------------------
# GET /{id}/live - Live status overlay data
# ---------------------------------------------------------------------------


@router.get(
    "/{floor_plan_id}/live",
    response_model=SuccessResponse,
    summary="Get live floor plan status",
    responses={404: {"model": ErrorResponse}},
)
async def get_live_status(
    floor_plan_id: uuid.UUID,
    user: User = Depends(_get_current_user),
    db: AsyncSession = Depends(get_db_session),
) -> dict:
    """Get real-time status data for the floor plan including camera
    statuses, zone occupancies, active alerts, and person counts."""
    live_data = await svc.get_live_status(db, floor_plan_id, user.org_id)

    return {
        "status": "success",
        "data": live_data,
    }


# ---------------------------------------------------------------------------
# POST /{id}/heatmap - Generate heatmap overlay
# ---------------------------------------------------------------------------


@router.post(
    "/{floor_plan_id}/heatmap",
    response_model=SuccessResponse,
    summary="Generate heatmap overlay",
    responses={404: {"model": ErrorResponse}},
)
async def generate_heatmap(
    floor_plan_id: uuid.UUID,
    body: HeatmapGenerateRequest,
    user: User = Depends(_get_current_user),
    db: AsyncSession = Depends(get_db_session),
) -> dict:
    """Generate a heatmap overlay from analytics data projected onto
    the floor plan and stored in MinIO."""
    overlay_data = await svc.generate_heatmap_overlay(
        db=db,
        floor_plan_id=floor_plan_id,
        time_range=body.time_range,
        color_scheme=body.color_scheme,
        org_id=user.org_id,
    )

    return {
        "status": "success",
        "data": overlay_data,
        "message": "Heatmap overlay generated successfully.",
    }


# ---------------------------------------------------------------------------
# GET /{id}/tracks/{person_id} - Person track on map
# ---------------------------------------------------------------------------


@router.get(
    "/{floor_plan_id}/tracks/{person_id}",
    response_model=SuccessResponse,
    summary="Get person track on floor plan",
    responses={404: {"model": ErrorResponse}},
)
async def get_person_tracks(
    floor_plan_id: uuid.UUID,
    person_id: uuid.UUID,
    user: User = Depends(_get_current_user),
    db: AsyncSession = Depends(get_db_session),
) -> dict:
    """Map a person's movement across cameras onto the floor plan,
    showing their trajectory through the building."""
    track_data = await svc.get_person_tracks_on_map(
        db=db,
        floor_plan_id=floor_plan_id,
        global_person_id=person_id,
        org_id=user.org_id,
    )

    return {
        "status": "success",
        "data": track_data,
    }
