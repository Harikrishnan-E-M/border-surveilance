"""
Camera management API endpoints.

Provides CRUD for cameras and camera groups, stream testing, snapshots,
health metrics, PTZ control, ONVIF discovery, bulk import, and
stream start/stop operations.
"""

from __future__ import annotations

import csv
import io
import math
import uuid
from datetime import datetime, timezone
from typing import Optional

import structlog
from fastapi import APIRouter, Depends, File, HTTPException, Query, Request, UploadFile, status
from fastapi.responses import StreamingResponse
from pydantic import BaseModel, Field
from sqlalchemy import String, func, select
from sqlalchemy.ext.asyncio import AsyncSession

from app.config import get_settings
from app.database import get_db_session
from app.exceptions import (
    AuthorizationError,
    DuplicateError,
    NotFoundError,
    StreamError,
    ValidationError,
)
from app.middleware.auth import JWTBearer, TokenPayload
from app.models.camera import (
    Camera,
    CameraGroup,
    CameraHealth,
    CameraHealthStatus,
    RecordingMode,
    StreamProtocol,
    camera_group_members,
)
from app.models.user import User, UserRole
from app.schemas.common import (
    ErrorResponse,
    PaginatedResponse,
    PaginationMeta,
    SuccessResponse,
)

logger = structlog.stdlib.get_logger(__name__)

router = APIRouter()


# ---------------------------------------------------------------------------
# Pydantic schemas (endpoint-local)
# ---------------------------------------------------------------------------

class CameraCreate(BaseModel):
    name: str = Field(..., min_length=1, max_length=255)
    stream_url: str = Field(..., min_length=1, max_length=1024)
    protocol: str = Field(default="rtsp")
    location_description: str | None = None
    username: str | None = None
    password: str | None = None
    resolution: str | None = None
    fps: int | None = Field(default=None, ge=1, le=120)
    codec: str | None = None
    latitude: float | None = None
    longitude: float | None = None
    floor_plan_x: float | None = None
    floor_plan_y: float | None = None
    recording_mode: str = Field(default="event")


class CameraUpdate(BaseModel):
    name: str | None = Field(default=None, max_length=255)
    stream_url: str | None = Field(default=None, max_length=1024)
    protocol: str | None = None
    location_description: str | None = None
    username: str | None = None
    password: str | None = None
    resolution: str | None = None
    fps: int | None = Field(default=None, ge=1, le=120)
    codec: str | None = None
    latitude: float | None = None
    longitude: float | None = None
    floor_plan_x: float | None = None
    floor_plan_y: float | None = None
    recording_mode: str | None = None
    is_active: bool | None = None


class CameraResponse(BaseModel):
    id: uuid.UUID
    org_id: uuid.UUID
    name: str
    stream_url: str
    protocol: str
    location_description: str | None = None
    resolution: str | None = None
    fps: int | None = None
    codec: str | None = None
    is_active: bool
    is_online: bool
    last_seen_at: datetime | None = None
    latitude: float | None = None
    longitude: float | None = None
    floor_plan_x: float | None = None
    floor_plan_y: float | None = None
    recording_mode: str
    created_at: datetime
    updated_at: datetime

    class Config:
        from_attributes = True


class PTZCommand(BaseModel):
    action: str = Field(..., description="PTZ action: pan_left, pan_right, tilt_up, tilt_down, zoom_in, zoom_out, preset, home")
    speed: float = Field(default=0.5, ge=0.0, le=1.0)
    preset_id: int | None = None


class CameraGroupCreate(BaseModel):
    name: str = Field(..., min_length=1, max_length=255)
    description: str | None = None
    camera_ids: list[uuid.UUID] = Field(default_factory=list)


class CameraGroupUpdate(BaseModel):
    name: str | None = Field(default=None, max_length=255)
    description: str | None = None
    camera_ids: list[uuid.UUID] | None = None


class CameraGroupResponse(BaseModel):
    id: uuid.UUID
    org_id: uuid.UUID
    name: str
    description: str | None = None
    camera_ids: list[uuid.UUID] = Field(default_factory=list)
    created_at: datetime
    updated_at: datetime


class CameraHealthResponse(BaseModel):
    id: uuid.UUID
    camera_id: uuid.UUID
    status: str
    fps_actual: float | None = None
    latency_ms: float | None = None
    packet_loss_pct: float | None = None
    cpu_usage: float | None = None
    memory_usage: float | None = None
    timestamp: datetime

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


def _camera_to_response(camera: Camera) -> dict:
    return CameraResponse(
        id=camera.id,
        org_id=camera.org_id,
        name=camera.name,
        stream_url=camera.stream_url,
        protocol=camera.protocol.value if isinstance(camera.protocol, StreamProtocol) else camera.protocol,
        location_description=camera.location_description,
        resolution=camera.resolution,
        fps=camera.fps,
        codec=camera.codec,
        is_active=camera.is_active,
        is_online=camera.is_online,
        last_seen_at=camera.last_seen_at,
        latitude=camera.latitude,
        longitude=camera.longitude,
        floor_plan_x=camera.floor_plan_x,
        floor_plan_y=camera.floor_plan_y,
        recording_mode=camera.recording_mode.value if isinstance(camera.recording_mode, RecordingMode) else camera.recording_mode,
        created_at=camera.created_at,
        updated_at=camera.updated_at,
    ).model_dump(mode="json")


# ---------------------------------------------------------------------------
# GET / - List cameras
# ---------------------------------------------------------------------------

@router.get(
    "/",
    response_model=SuccessResponse,
    summary="List cameras (paginated)",
)
async def list_cameras(
    page: int = Query(1, ge=1),
    page_size: int = Query(20, ge=1, le=500),
    search: str | None = Query(None, description="Search by camera name"),
    is_active: bool | None = Query(None),
    is_online: bool | None = Query(None),
    protocol: str | None = Query(None),
    user: User = Depends(_get_current_user),
    db: AsyncSession = Depends(get_db_session),
) -> dict:
    """Return a paginated list of cameras belonging to the user's organization."""
    query = select(Camera).where(Camera.org_id == user.org_id)

    if search:
        query = query.where(Camera.name.ilike(f"%{search}%"))
    if is_active is not None:
        query = query.where(Camera.is_active == is_active)
    if is_online is not None:
        query = query.where(Camera.is_online == is_online)
    if protocol:
        query = query.where(Camera.protocol == protocol)

    # Total count
    count_q = select(func.count()).select_from(query.subquery())
    total = (await db.execute(count_q)).scalar() or 0

    # Paginated results
    query = query.order_by(Camera.created_at.desc()).offset((page - 1) * page_size).limit(page_size)
    result = await db.execute(query)
    cameras = result.scalars().all()

    return {
        "status": "success",
        "data": [_camera_to_response(c) for c in cameras],
        "meta": {
            "page": page,
            "page_size": page_size,
            "total": total,
            "total_pages": math.ceil(total / page_size) if page_size else 0,
        },
    }


# ---------------------------------------------------------------------------
# POST / - Create camera
# ---------------------------------------------------------------------------

@router.post(
    "/",
    response_model=SuccessResponse,
    status_code=status.HTTP_201_CREATED,
    summary="Create a new camera",
    responses={403: {"model": ErrorResponse}},
)
async def create_camera(
    body: CameraCreate,
    user: User = Depends(_get_current_user),
    db: AsyncSession = Depends(get_db_session),
) -> dict:
    """Add a new camera to the organization. Requires manager role or above."""
    _require_manager(user)

    # Check camera limit for org
    count_result = await db.execute(
        select(func.count()).select_from(
            select(Camera).where(Camera.org_id == user.org_id).subquery()
        )
    )
    current_count = count_result.scalar() or 0

    # Load org to check max_cameras
    from app.models.organization import Organization
    org_result = await db.execute(
        select(Organization).where(Organization.id == user.org_id)
    )
    org = org_result.scalars().first()
    if org and current_count >= org.max_cameras:
        raise ValidationError(
            message=f"Camera limit reached ({org.max_cameras}). Upgrade your plan to add more cameras."
        )

    # Encrypt credentials if provided
    username_encrypted = None
    password_encrypted = None
    if body.username or body.password:
        try:
            from app.services.encryption_service import encrypt_value
            if body.username:
                username_encrypted = encrypt_value(body.username)
            if body.password:
                password_encrypted = encrypt_value(body.password)
        except ImportError:
            logger.warning("Encryption service not available; storing credentials as-is")
            username_encrypted = body.username
            password_encrypted = body.password

    camera = Camera(
        org_id=user.org_id,
        name=body.name,
        stream_url=body.stream_url,
        protocol=StreamProtocol(body.protocol),
        location_description=body.location_description,
        username_encrypted=username_encrypted,
        password_encrypted=password_encrypted,
        resolution=body.resolution,
        fps=body.fps,
        codec=body.codec,
        latitude=body.latitude,
        longitude=body.longitude,
        floor_plan_x=body.floor_plan_x,
        floor_plan_y=body.floor_plan_y,
        recording_mode=RecordingMode(body.recording_mode),
    )
    db.add(camera)
    await db.flush()

    logger.info("Camera created", camera_id=str(camera.id), name=camera.name)

    return {
        "status": "success",
        "data": _camera_to_response(camera),
        "message": "Camera created successfully.",
    }


# ---------------------------------------------------------------------------
# GET /health - Get health summary for all cameras (dashboard)
# ---------------------------------------------------------------------------

@router.get(
    "/health",
    summary="Get all cameras health summary",
)
async def get_all_cameras_health(
    user: User = Depends(_get_current_user),
    db: AsyncSession = Depends(get_db_session),
) -> list[dict]:
    """Return a simple health summary of all active cameras for the dashboard."""
    result = await db.execute(
        select(Camera).where(
            Camera.org_id == user.org_id,
            Camera.is_active.is_(True),
        ).order_by(Camera.name)
    )
    cameras = result.scalars().all()

    return [
        {
            "id": str(cam.id),
            "name": cam.name,
            "status": "online" if cam.is_online else "offline",
        }
        for cam in cameras
    ]


# ---------------------------------------------------------------------------
# POST /start-all - Start streams for all active cameras
# ---------------------------------------------------------------------------

@router.post(
    "/start-all",
    response_model=SuccessResponse,
    summary="Start streams for all active cameras",
)
async def start_all_streams(
    user: User = Depends(_get_current_user),
    db: AsyncSession = Depends(get_db_session),
) -> dict:
    """Start video streaming for all active cameras in the organization."""
    _require_manager(user)

    result = await db.execute(
        select(Camera).where(
            Camera.org_id == user.org_id,
            Camera.is_active.is_(True),
        )
    )
    cameras = result.scalars().all()

    started = []
    errors = []

    for cam in cameras:
        try:
            from app.services.stream_service import start_stream as svc_start_stream
            await svc_start_stream(
                camera_id=str(cam.id),
                stream_url=cam.stream_url,
                camera_name=cam.name,
            )
            cam.is_online = True
            cam.last_seen_at = datetime.now(timezone.utc)
            db.add(cam)
            started.append(str(cam.id))
        except Exception as exc:
            logger.warning("Failed to start stream", camera_id=str(cam.id), error=str(exc))
            errors.append({"camera_id": str(cam.id), "name": cam.name, "error": str(exc)})

    await db.flush()

    return {
        "status": "success",
        "data": {
            "started": started,
            "started_count": len(started),
            "errors": errors,
            "error_count": len(errors),
        },
        "message": f"Started {len(started)} stream(s), {len(errors)} failed.",
    }


# ---------------------------------------------------------------------------
# GET /{camera_id}/zones - Get zones for a camera
# ---------------------------------------------------------------------------

@router.get(
    "/{camera_id}/zones",
    response_model=SuccessResponse,
    summary="Get zones for a camera",
)
async def get_camera_zones(
    camera_id: uuid.UUID,
    is_active: bool | None = Query(None),
    user: User = Depends(_get_current_user),
    db: AsyncSession = Depends(get_db_session),
) -> dict:
    """Return all zones defined for the given camera."""
    from app.models.zone import Zone

    # Verify camera belongs to user's org
    cam_check = await db.execute(
        select(Camera.id).where(Camera.id == camera_id, Camera.org_id == user.org_id)
    )
    if not cam_check.scalars().first():
        raise NotFoundError(message="Camera not found.")

    query = select(Zone).where(
        Zone.camera_id == camera_id,
    )
    if is_active is not None:
        query = query.where(Zone.is_active.is_(is_active))
    query = query.order_by(Zone.name)

    result = await db.execute(query)
    zones = result.scalars().all()

    return {
        "status": "success",
        "data": [
            {
                "id": str(z.id),
                "camera_id": str(z.camera_id),
                "name": z.name,
                "zone_type": z.zone_type.value if hasattr(z.zone_type, "value") else z.zone_type,
                "polygon_points": z.polygon_points,
                "color_hex": z.color_hex,
                "is_active": z.is_active,
                "created_at": z.created_at.isoformat() if z.created_at else None,
                "updated_at": z.updated_at.isoformat() if z.updated_at else None,
            }
            for z in zones
        ],
    }


# ---------------------------------------------------------------------------
# DELETE /{camera_id}/zones/{zone_id} - Delete a zone from a camera
# ---------------------------------------------------------------------------

@router.delete(
    "/{camera_id}/zones/{zone_id}",
    response_model=SuccessResponse,
    summary="Delete a zone from a camera",
)
async def delete_camera_zone(
    camera_id: uuid.UUID,
    zone_id: uuid.UUID,
    user: User = Depends(_get_current_user),
    db: AsyncSession = Depends(get_db_session),
) -> dict:
    """Delete a zone belonging to a camera in the user's organization."""
    from app.models.zone import Zone

    _require_manager(user)

    # Verify camera belongs to user's org
    cam_check = await db.execute(
        select(Camera.id).where(Camera.id == camera_id, Camera.org_id == user.org_id)
    )
    if not cam_check.scalars().first():
        raise NotFoundError(message="Camera not found.")

    result = await db.execute(
        select(Zone).where(
            Zone.id == zone_id,
            Zone.camera_id == camera_id,
        )
    )
    zone = result.scalars().first()
    if not zone:
        raise NotFoundError(message="Zone not found.")

    await db.delete(zone)
    logger.info("Zone deleted", zone_id=str(zone_id), camera_id=str(camera_id))

    return {"status": "success", "data": None, "message": "Zone deleted."}


# ---------------------------------------------------------------------------
# GET /{camera_id} - Get camera details
# ---------------------------------------------------------------------------

@router.get(
    "/{camera_id}",
    response_model=SuccessResponse,
    summary="Get camera details",
    responses={404: {"model": ErrorResponse}},
)
async def get_camera(
    camera_id: uuid.UUID,
    user: User = Depends(_get_current_user),
    db: AsyncSession = Depends(get_db_session),
) -> dict:
    """Retrieve a single camera by ID."""
    result = await db.execute(
        select(Camera).where(Camera.id == camera_id, Camera.org_id == user.org_id)
    )
    camera = result.scalars().first()
    if not camera:
        raise NotFoundError(resource="Camera", identifier=str(camera_id))

    return {
        "status": "success",
        "data": _camera_to_response(camera),
    }


# ---------------------------------------------------------------------------
# PUT /{camera_id} - Update camera
# ---------------------------------------------------------------------------

@router.put(
    "/{camera_id}",
    response_model=SuccessResponse,
    summary="Update camera",
    responses={403: {"model": ErrorResponse}, 404: {"model": ErrorResponse}},
)
async def update_camera(
    camera_id: uuid.UUID,
    body: CameraUpdate,
    user: User = Depends(_get_current_user),
    db: AsyncSession = Depends(get_db_session),
) -> dict:
    """Update camera fields. Requires manager role or above."""
    _require_manager(user)

    result = await db.execute(
        select(Camera).where(Camera.id == camera_id, Camera.org_id == user.org_id)
    )
    camera = result.scalars().first()
    if not camera:
        raise NotFoundError(resource="Camera", identifier=str(camera_id))

    update_data = body.model_dump(exclude_unset=True)

    # Handle credential encryption
    if "username" in update_data:
        val = update_data.pop("username")
        try:
            from app.services.encryption_service import encrypt_value
            camera.username_encrypted = encrypt_value(val) if val else None
        except ImportError:
            camera.username_encrypted = val

    if "password" in update_data:
        val = update_data.pop("password")
        try:
            from app.services.encryption_service import encrypt_value
            camera.password_encrypted = encrypt_value(val) if val else None
        except ImportError:
            camera.password_encrypted = val

    # Handle enum fields
    if "protocol" in update_data:
        update_data["protocol"] = StreamProtocol(update_data["protocol"])
    if "recording_mode" in update_data:
        update_data["recording_mode"] = RecordingMode(update_data["recording_mode"])

    for field, value in update_data.items():
        setattr(camera, field, value)

    db.add(camera)
    await db.flush()

    logger.info("Camera updated", camera_id=str(camera.id))

    return {
        "status": "success",
        "data": _camera_to_response(camera),
        "message": "Camera updated successfully.",
    }


# ---------------------------------------------------------------------------
# DELETE /{camera_id} - Soft-delete camera
# ---------------------------------------------------------------------------

@router.delete(
    "/{camera_id}",
    response_model=SuccessResponse,
    summary="Delete camera (soft delete)",
    responses={403: {"model": ErrorResponse}, 404: {"model": ErrorResponse}},
)
async def delete_camera(
    camera_id: uuid.UUID,
    user: User = Depends(_get_current_user),
    db: AsyncSession = Depends(get_db_session),
) -> dict:
    """Soft-delete a camera by deactivating it. Requires manager role or above."""
    _require_manager(user)

    result = await db.execute(
        select(Camera).where(Camera.id == camera_id, Camera.org_id == user.org_id)
    )
    camera = result.scalars().first()
    if not camera:
        raise NotFoundError(resource="Camera", identifier=str(camera_id))

    camera.is_active = False
    camera.is_online = False
    db.add(camera)
    await db.flush()

    # Stop stream processing if running
    try:
        from app.services.stream_service import stop_stream
        await stop_stream(camera_id=str(camera.id))
    except (ImportError, Exception) as exc:
        logger.warning("Failed to stop stream on camera delete", error=str(exc))

    logger.info("Camera soft-deleted", camera_id=str(camera.id))

    return {
        "status": "success",
        "data": None,
        "message": "Camera deleted successfully.",
    }


# ---------------------------------------------------------------------------
# POST /{camera_id}/test-stream - Test camera stream connection
# ---------------------------------------------------------------------------

@router.post(
    "/{camera_id}/test-stream",
    response_model=SuccessResponse,
    summary="Test camera stream connection",
    responses={404: {"model": ErrorResponse}, 502: {"model": ErrorResponse}},
)
async def test_stream(
    camera_id: uuid.UUID,
    user: User = Depends(_get_current_user),
    db: AsyncSession = Depends(get_db_session),
) -> dict:
    """Attempt to connect to the camera stream and return a test snapshot."""
    _require_manager(user)

    result = await db.execute(
        select(Camera).where(Camera.id == camera_id, Camera.org_id == user.org_id)
    )
    camera = result.scalars().first()
    if not camera:
        raise NotFoundError(resource="Camera", identifier=str(camera_id))

    try:
        from app.services.stream_service import test_camera_stream
        test_result = await test_camera_stream(
            stream_url=camera.stream_url,
            username_encrypted=camera.username_encrypted,
            password_encrypted=camera.password_encrypted,
            protocol=camera.protocol.value,
        )
    except ImportError:
        raise StreamError(message="Stream service is not available.")
    except Exception as exc:
        logger.error("Stream test failed", camera_id=str(camera_id), error=str(exc))
        raise StreamError(message=f"Failed to connect to camera stream: {str(exc)}")

    return {
        "status": "success",
        "data": {
            "connected": test_result.get("connected", False),
            "resolution": test_result.get("resolution"),
            "fps": test_result.get("fps"),
            "codec": test_result.get("codec"),
            "snapshot_url": test_result.get("snapshot_url"),
            "latency_ms": test_result.get("latency_ms"),
        },
        "message": "Stream test completed.",
    }


# ---------------------------------------------------------------------------
# GET /{camera_id}/snapshot - Get current frame
# ---------------------------------------------------------------------------

@router.get(
    "/{camera_id}/snapshot",
    response_model=SuccessResponse,
    summary="Get current camera snapshot",
    responses={404: {"model": ErrorResponse}},
)
async def get_snapshot(
    camera_id: uuid.UUID,
    user: User = Depends(_get_current_user),
    db: AsyncSession = Depends(get_db_session),
) -> dict:
    """Grab the current frame from the camera and return a snapshot URL."""
    result = await db.execute(
        select(Camera).where(Camera.id == camera_id, Camera.org_id == user.org_id)
    )
    camera = result.scalars().first()
    if not camera:
        raise NotFoundError(resource="Camera", identifier=str(camera_id))

    try:
        from app.services.stream_service import capture_snapshot
        snapshot = await capture_snapshot(camera_id=str(camera.id))
    except ImportError:
        raise StreamError(message="Stream service is not available.")
    except Exception as exc:
        logger.error("Snapshot capture failed", camera_id=str(camera_id), error=str(exc))
        raise StreamError(message=f"Failed to capture snapshot: {str(exc)}")

    return {
        "status": "success",
        "data": {
            "camera_id": str(camera.id),
            "snapshot_url": snapshot.get("url"),
            "timestamp": snapshot.get("timestamp", datetime.now(timezone.utc).isoformat()),
        },
    }


# ---------------------------------------------------------------------------
# GET /{camera_id}/health - Get health metrics
# ---------------------------------------------------------------------------

@router.get(
    "/{camera_id}/health",
    response_model=SuccessResponse,
    summary="Get camera health metrics",
    responses={404: {"model": ErrorResponse}},
)
async def get_camera_health(
    camera_id: uuid.UUID,
    limit: int = Query(10, ge=1, le=100, description="Number of recent health records"),
    user: User = Depends(_get_current_user),
    db: AsyncSession = Depends(get_db_session),
) -> dict:
    """Retrieve recent health telemetry records for a camera."""
    # Verify camera belongs to org
    cam_result = await db.execute(
        select(Camera).where(Camera.id == camera_id, Camera.org_id == user.org_id)
    )
    camera = cam_result.scalars().first()
    if not camera:
        raise NotFoundError(resource="Camera", identifier=str(camera_id))

    result = await db.execute(
        select(CameraHealth)
        .where(CameraHealth.camera_id == camera_id)
        .order_by(CameraHealth.timestamp.desc())
        .limit(limit)
    )
    records = result.scalars().all()

    health_data = [
        CameraHealthResponse(
            id=r.id,
            camera_id=r.camera_id,
            status=r.status.value if isinstance(r.status, CameraHealthStatus) else r.status,
            fps_actual=r.fps_actual,
            latency_ms=r.latency_ms,
            packet_loss_pct=r.packet_loss_pct,
            cpu_usage=r.cpu_usage,
            memory_usage=r.memory_usage,
            timestamp=r.timestamp,
        ).model_dump(mode="json")
        for r in records
    ]

    return {
        "status": "success",
        "data": {
            "camera_id": str(camera.id),
            "is_online": camera.is_online,
            "last_seen_at": camera.last_seen_at.isoformat() if camera.last_seen_at else None,
            "records": health_data,
        },
    }


# ---------------------------------------------------------------------------
# POST /{camera_id}/ptz - PTZ control
# ---------------------------------------------------------------------------

@router.post(
    "/{camera_id}/ptz",
    response_model=SuccessResponse,
    summary="Send PTZ command to camera",
    responses={404: {"model": ErrorResponse}},
)
async def ptz_control(
    camera_id: uuid.UUID,
    body: PTZCommand,
    user: User = Depends(_get_current_user),
    db: AsyncSession = Depends(get_db_session),
) -> dict:
    """Send a pan/tilt/zoom command to a camera. Requires ONVIF protocol support."""
    _require_manager(user)

    result = await db.execute(
        select(Camera).where(Camera.id == camera_id, Camera.org_id == user.org_id)
    )
    camera = result.scalars().first()
    if not camera:
        raise NotFoundError(resource="Camera", identifier=str(camera_id))

    if camera.protocol != StreamProtocol.ONVIF:
        raise ValidationError(message="PTZ control is only available for ONVIF cameras.")

    try:
        from app.services.onvif_service import send_ptz_command
        ptz_result = await send_ptz_command(
            camera_id=str(camera.id),
            stream_url=camera.stream_url,
            username_encrypted=camera.username_encrypted,
            password_encrypted=camera.password_encrypted,
            action=body.action,
            speed=body.speed,
            preset_id=body.preset_id,
        )
    except ImportError:
        raise ValidationError(message="ONVIF service is not available.")
    except Exception as exc:
        logger.error("PTZ command failed", camera_id=str(camera_id), error=str(exc))
        raise StreamError(message=f"PTZ command failed: {str(exc)}")

    return {
        "status": "success",
        "data": {"action": body.action, "result": ptz_result},
        "message": "PTZ command sent successfully.",
    }


# ---------------------------------------------------------------------------
# POST /discover-onvif - ONVIF device discovery
# ---------------------------------------------------------------------------

@router.post(
    "/discover-onvif",
    response_model=SuccessResponse,
    summary="Discover ONVIF devices on the network",
)
async def discover_onvif(
    timeout: int = Query(5, ge=1, le=30, description="Discovery timeout in seconds"),
    user: User = Depends(_get_current_user),
) -> dict:
    """Perform WS-Discovery to find ONVIF-compatible cameras on the local network."""
    _require_manager(user)

    try:
        from app.services.onvif_service import discover_devices
        devices = await discover_devices(timeout=timeout)
    except ImportError:
        raise ValidationError(message="ONVIF service is not available.")
    except Exception as exc:
        logger.error("ONVIF discovery failed", error=str(exc))
        raise StreamError(message=f"ONVIF discovery failed: {str(exc)}")

    return {
        "status": "success",
        "data": {
            "devices": devices,
            "count": len(devices),
        },
        "message": f"Discovered {len(devices)} ONVIF device(s).",
    }


# ---------------------------------------------------------------------------
# POST /discover-usb - Discover USB cameras
# ---------------------------------------------------------------------------

@router.post(
    "/discover-usb",
    response_model=SuccessResponse,
    summary="Discover USB cameras attached to the server",
)
async def discover_usb(
    user: User = Depends(_get_current_user),
) -> dict:
    """Probe /dev/video* devices using OpenCV and return working USB cameras."""
    import cv2

    devices: list[dict] = []
    for idx in range(0, 16, 2):  # Even-numbered devices are capture devices
        dev_path = f"/dev/video{idx}"
        try:
            cap = cv2.VideoCapture(idx)
            if not cap.isOpened():
                continue
            ret, frame = cap.read()
            if ret:
                h, w = frame.shape[:2]
                fps = cap.get(cv2.CAP_PROP_FPS) or 30
                devices.append({
                    "device_index": idx,
                    "device_path": dev_path,
                    "name": f"USB Camera {idx // 2 + 1}",
                    "resolution": f"{w}x{h}",
                    "fps": int(fps),
                    "stream_url": dev_path,
                })
            cap.release()
        except Exception:
            continue

    return {
        "status": "success",
        "data": {
            "devices": devices,
            "count": len(devices),
        },
        "message": f"Discovered {len(devices)} USB camera(s).",
    }


# ---------------------------------------------------------------------------
# POST /discover-usb/register - Discover and auto-register USB cameras
# ---------------------------------------------------------------------------

@router.post(
    "/discover-usb/register",
    response_model=SuccessResponse,
    status_code=status.HTTP_201_CREATED,
    summary="Discover and register USB cameras",
)
async def discover_and_register_usb(
    user: User = Depends(_get_current_user),
    db: AsyncSession = Depends(get_db_session),
) -> dict:
    """Discover USB cameras, skip already-registered ones, and create new camera records."""
    import cv2

    _require_manager(user)

    # Find already-registered USB cameras
    existing_result = await db.execute(
        select(Camera.stream_url).where(
            Camera.org_id == user.org_id,
            Camera.protocol == StreamProtocol.USB,
        )
    )
    existing_urls = set(existing_result.scalars().all())

    registered: list[dict] = []
    skipped: list[dict] = []

    for idx in range(0, 16, 2):
        dev_path = f"/dev/video{idx}"
        try:
            cap = cv2.VideoCapture(idx)
            if not cap.isOpened():
                continue
            ret, frame = cap.read()
            if not ret:
                cap.release()
                continue

            h, w = frame.shape[:2]
            fps_detected = int(cap.get(cv2.CAP_PROP_FPS) or 30)
            cap.release()

            if dev_path in existing_urls:
                skipped.append({"device_path": dev_path, "reason": "already registered"})
                continue

            camera = Camera(
                org_id=user.org_id,
                name=f"USB Camera {idx // 2 + 1}",
                stream_url=dev_path,
                protocol=StreamProtocol.USB,
                location_description="Local USB webcam",
                resolution=f"{w}x{h}",
                fps=fps_detected if fps_detected > 0 else 30,
                recording_mode=RecordingMode.EVENT,
                is_active=True,
                is_online=True,
            )
            db.add(camera)
            await db.flush()

            registered.append(_camera_to_response(camera))
            logger.info("USB camera registered", camera_id=str(camera.id), device=dev_path)

        except Exception as exc:
            logger.warning("Failed to probe USB device", device=dev_path, error=str(exc))
            continue

    return {
        "status": "success",
        "data": {
            "registered": registered,
            "skipped": skipped,
            "registered_count": len(registered),
            "skipped_count": len(skipped),
        },
        "message": f"Registered {len(registered)} new USB camera(s), skipped {len(skipped)}.",
    }


# ---------------------------------------------------------------------------
# POST /bulk-import - CSV bulk import
# ---------------------------------------------------------------------------

@router.post(
    "/bulk-import",
    response_model=SuccessResponse,
    status_code=status.HTTP_201_CREATED,
    summary="Bulk import cameras from CSV",
)
async def bulk_import(
    file: UploadFile = File(..., description="CSV file with camera data"),
    user: User = Depends(_get_current_user),
    db: AsyncSession = Depends(get_db_session),
) -> dict:
    """Import cameras from a CSV file.

    Expected CSV columns: name, stream_url, protocol, location_description, fps, recording_mode
    """
    _require_manager(user)

    if not file.filename or not file.filename.endswith(".csv"):
        raise ValidationError(message="File must be a CSV (.csv) file.")

    content = await file.read()
    try:
        text = content.decode("utf-8")
    except UnicodeDecodeError:
        raise ValidationError(message="File must be UTF-8 encoded.")

    reader = csv.DictReader(io.StringIO(text))
    required_fields = {"name", "stream_url"}
    if not required_fields.issubset(set(reader.fieldnames or [])):
        raise ValidationError(
            message=f"CSV must contain at least these columns: {', '.join(required_fields)}"
        )

    created = []
    errors = []

    for row_num, row in enumerate(reader, start=2):
        name = (row.get("name") or "").strip()
        stream_url = (row.get("stream_url") or "").strip()

        if not name or not stream_url:
            errors.append({"row": row_num, "error": "name and stream_url are required"})
            continue

        protocol_val = (row.get("protocol") or "rtsp").strip().lower()
        try:
            protocol = StreamProtocol(protocol_val)
        except ValueError:
            protocol = StreamProtocol.RTSP

        recording_mode_val = (row.get("recording_mode") or "event").strip().lower()
        try:
            rec_mode = RecordingMode(recording_mode_val)
        except ValueError:
            rec_mode = RecordingMode.EVENT

        fps = None
        fps_str = (row.get("fps") or "").strip()
        if fps_str:
            try:
                fps = int(fps_str)
            except ValueError:
                pass

        camera = Camera(
            org_id=user.org_id,
            name=name,
            stream_url=stream_url,
            protocol=protocol,
            location_description=(row.get("location_description") or "").strip() or None,
            fps=fps,
            recording_mode=rec_mode,
        )
        db.add(camera)
        created.append(name)

    if created:
        await db.flush()

    logger.info(
        "Bulk import completed",
        created_count=len(created),
        error_count=len(errors),
    )

    return {
        "status": "success",
        "data": {
            "imported": len(created),
            "failed": len(errors),
            "errors": errors[:50],  # Cap error details
        },
        "message": f"Imported {len(created)} camera(s), {len(errors)} failed.",
    }


# ---------------------------------------------------------------------------
# POST /{camera_id}/start - Start stream processing
# ---------------------------------------------------------------------------

@router.post(
    "/{camera_id}/start",
    response_model=SuccessResponse,
    summary="Start stream processing for camera",
    responses={404: {"model": ErrorResponse}},
)
async def start_stream(
    camera_id: uuid.UUID,
    user: User = Depends(_get_current_user),
    db: AsyncSession = Depends(get_db_session),
) -> dict:
    """Start the video analytics pipeline for the specified camera."""
    _require_manager(user)

    result = await db.execute(
        select(Camera).where(Camera.id == camera_id, Camera.org_id == user.org_id)
    )
    camera = result.scalars().first()
    if not camera:
        raise NotFoundError(resource="Camera", identifier=str(camera_id))

    if not camera.is_active:
        raise ValidationError(message="Cannot start stream for a deactivated camera.")

    try:
        from app.services.stream_service import start_stream as svc_start_stream
        await svc_start_stream(
            camera_id=str(camera.id),
            stream_url=camera.stream_url,
            camera_name=camera.name,
        )
    except ImportError:
        raise StreamError(message="Stream service is not available.")
    except Exception as exc:
        logger.error("Failed to start stream", camera_id=str(camera_id), error=str(exc))
        raise StreamError(message=f"Failed to start stream: {str(exc)}")

    camera.is_online = True
    camera.last_seen_at = datetime.now(timezone.utc)
    db.add(camera)
    await db.flush()

    logger.info("Stream started", camera_id=str(camera.id))

    return {
        "status": "success",
        "data": {"camera_id": str(camera.id), "is_online": True},
        "message": "Stream processing started.",
    }


# ---------------------------------------------------------------------------
# POST /{camera_id}/stop - Stop stream processing
# ---------------------------------------------------------------------------

@router.post(
    "/{camera_id}/stop",
    response_model=SuccessResponse,
    summary="Stop stream processing for camera",
    responses={404: {"model": ErrorResponse}},
)
async def stop_stream_endpoint(
    camera_id: uuid.UUID,
    user: User = Depends(_get_current_user),
    db: AsyncSession = Depends(get_db_session),
) -> dict:
    """Stop the video analytics pipeline for the specified camera."""
    _require_manager(user)

    result = await db.execute(
        select(Camera).where(Camera.id == camera_id, Camera.org_id == user.org_id)
    )
    camera = result.scalars().first()
    if not camera:
        raise NotFoundError(resource="Camera", identifier=str(camera_id))

    try:
        from app.services.stream_service import stop_stream
        await stop_stream(camera_id=str(camera.id))
    except ImportError:
        raise StreamError(message="Stream service is not available.")
    except Exception as exc:
        logger.error("Failed to stop stream", camera_id=str(camera_id), error=str(exc))
        raise StreamError(message=f"Failed to stop stream: {str(exc)}")

    camera.is_online = False
    db.add(camera)
    await db.flush()

    logger.info("Stream stopped", camera_id=str(camera.id))

    return {
        "status": "success",
        "data": {"camera_id": str(camera.id), "is_online": False},
        "message": "Stream processing stopped.",
    }


# ---------------------------------------------------------------------------
# GET /{camera_id}/stream/mjpeg - Live MJPEG video stream
# ---------------------------------------------------------------------------

@router.get(
    "/{camera_id}/stream/mjpeg",
    summary="Live MJPEG video stream",
    responses={404: {"model": ErrorResponse}},
)
async def mjpeg_stream(
    request: Request,
    camera_id: uuid.UUID,
    fps: int = Query(15, ge=1, le=30, description="Target frames per second"),
    quality: int = Query(70, ge=10, le=100, description="JPEG quality (10-100)"),
    token: str | None = Query(None, description="JWT token (for img tag auth)"),
    db: AsyncSession = Depends(get_db_session),
) -> StreamingResponse:
    """Stream live video from a camera as MJPEG over HTTP.

    The stream is served as ``multipart/x-mixed-replace`` which is
    natively supported by ``<img>`` tags in all modern browsers.
    The camera stream must be started first via ``POST /{camera_id}/start``.

    Accepts authentication via either:
    - ``Authorization: Bearer <token>`` header
    - ``?token=<jwt>`` query parameter (for ``<img>`` tags)
    """
    from app.middleware.auth import decode_access_token

    # Try Authorization header first, then token query parameter
    token_payload = None
    auth_header = request.headers.get("authorization", "")
    try:
        if auth_header.lower().startswith("bearer "):
            jwt_token = auth_header[7:]
            token_payload = decode_access_token(jwt_token)
        elif token:
            token_payload = decode_access_token(token)
    except HTTPException:
        token_payload = None

    if token_payload is None:
        raise HTTPException(status_code=401, detail="Authentication required.")

    # Look up the real user for org scoping
    user_result = await db.execute(
        select(User).where(User.id == uuid.UUID(token_payload.sub), User.is_active.is_(True))
    )
    real_user = user_result.scalars().first()
    if not real_user:
        raise HTTPException(status_code=401, detail="User not found.")

    result = await db.execute(
        select(Camera).where(Camera.id == camera_id, Camera.org_id == real_user.org_id)
    )
    camera = result.scalars().first()
    if not camera:
        raise NotFoundError(resource="Camera", identifier=str(camera_id))

    from app.services.stream_service import mjpeg_frame_generator

    return StreamingResponse(
        mjpeg_frame_generator(
            camera_id=str(camera.id),
            fps_target=fps,
            quality=quality,
        ),
        media_type="multipart/x-mixed-replace; boundary=frame",
        headers={
            "Cache-Control": "no-cache, no-store, must-revalidate",
            "Pragma": "no-cache",
            "Expires": "0",
            "X-Accel-Buffering": "no",
        },
    )



# ---------------------------------------------------------------------------
# GET /{camera_id}/stream/status - Stream status
# ---------------------------------------------------------------------------

@router.get(
    "/{camera_id}/stream/status",
    response_model=SuccessResponse,
    summary="Get stream status for a camera",
)
async def stream_status(
    camera_id: uuid.UUID,
    user: User = Depends(_get_current_user),
    db: AsyncSession = Depends(get_db_session),
) -> dict:
    """Return stream health metrics for a camera."""
    result = await db.execute(
        select(Camera).where(Camera.id == camera_id, Camera.org_id == user.org_id)
    )
    camera = result.scalars().first()
    if not camera:
        raise NotFoundError(resource="Camera", identifier=str(camera_id))

    from app.services.stream_service import get_stream_status
    status_data = await get_stream_status(str(camera.id))

    return {
        "status": "success",
        "data": {
            "camera_id": str(camera.id),
            "camera_name": camera.name,
            **status_data,
        },
    }


# ---------------------------------------------------------------------------
# GET /groups - List camera groups
# ---------------------------------------------------------------------------

@router.get(
    "/groups",
    response_model=SuccessResponse,
    summary="List camera groups",
)
async def list_groups(
    user: User = Depends(_get_current_user),
    db: AsyncSession = Depends(get_db_session),
) -> dict:
    """Return all camera groups for the user's organization."""
    result = await db.execute(
        select(CameraGroup).where(CameraGroup.org_id == user.org_id).order_by(CameraGroup.name)
    )
    groups = result.scalars().all()

    groups_data = []
    for g in groups:
        camera_ids = [c.id for c in g.cameras] if g.cameras else []
        groups_data.append(
            CameraGroupResponse(
                id=g.id,
                org_id=g.org_id,
                name=g.name,
                description=g.description,
                camera_ids=camera_ids,
                created_at=g.created_at,
                updated_at=g.updated_at,
            ).model_dump(mode="json")
        )

    return {
        "status": "success",
        "data": groups_data,
    }


# ---------------------------------------------------------------------------
# POST /groups - Create camera group
# ---------------------------------------------------------------------------

@router.post(
    "/groups",
    response_model=SuccessResponse,
    status_code=status.HTTP_201_CREATED,
    summary="Create camera group",
)
async def create_group(
    body: CameraGroupCreate,
    user: User = Depends(_get_current_user),
    db: AsyncSession = Depends(get_db_session),
) -> dict:
    """Create a new camera group within the organization."""
    _require_manager(user)

    group = CameraGroup(
        org_id=user.org_id,
        name=body.name,
        description=body.description,
    )
    db.add(group)
    await db.flush()

    # Associate cameras with the group
    if body.camera_ids:
        cam_result = await db.execute(
            select(Camera).where(
                Camera.id.in_(body.camera_ids),
                Camera.org_id == user.org_id,
            )
        )
        cameras = cam_result.scalars().all()
        group.cameras = list(cameras)
        await db.flush()

    logger.info("Camera group created", group_id=str(group.id), name=group.name)

    camera_ids = [c.id for c in group.cameras] if group.cameras else []

    return {
        "status": "success",
        "data": CameraGroupResponse(
            id=group.id,
            org_id=group.org_id,
            name=group.name,
            description=group.description,
            camera_ids=camera_ids,
            created_at=group.created_at,
            updated_at=group.updated_at,
        ).model_dump(mode="json"),
        "message": "Camera group created successfully.",
    }


# ---------------------------------------------------------------------------
# PUT /groups/{group_id} - Update camera group
# ---------------------------------------------------------------------------

@router.put(
    "/groups/{group_id}",
    response_model=SuccessResponse,
    summary="Update camera group",
    responses={404: {"model": ErrorResponse}},
)
async def update_group(
    group_id: uuid.UUID,
    body: CameraGroupUpdate,
    user: User = Depends(_get_current_user),
    db: AsyncSession = Depends(get_db_session),
) -> dict:
    """Update a camera group's name, description, or member cameras."""
    _require_manager(user)

    result = await db.execute(
        select(CameraGroup).where(CameraGroup.id == group_id, CameraGroup.org_id == user.org_id)
    )
    group = result.scalars().first()
    if not group:
        raise NotFoundError(resource="CameraGroup", identifier=str(group_id))

    if body.name is not None:
        group.name = body.name
    if body.description is not None:
        group.description = body.description

    if body.camera_ids is not None:
        cam_result = await db.execute(
            select(Camera).where(
                Camera.id.in_(body.camera_ids),
                Camera.org_id == user.org_id,
            )
        )
        cameras = cam_result.scalars().all()
        group.cameras = list(cameras)

    db.add(group)
    await db.flush()

    camera_ids = [c.id for c in group.cameras] if group.cameras else []

    logger.info("Camera group updated", group_id=str(group.id))

    return {
        "status": "success",
        "data": CameraGroupResponse(
            id=group.id,
            org_id=group.org_id,
            name=group.name,
            description=group.description,
            camera_ids=camera_ids,
            created_at=group.created_at,
            updated_at=group.updated_at,
        ).model_dump(mode="json"),
        "message": "Camera group updated successfully.",
    }


# ---------------------------------------------------------------------------
# DELETE /groups/{group_id} - Delete camera group
# ---------------------------------------------------------------------------

@router.delete(
    "/groups/{group_id}",
    response_model=SuccessResponse,
    summary="Delete camera group",
    responses={404: {"model": ErrorResponse}},
)
async def delete_group(
    group_id: uuid.UUID,
    user: User = Depends(_get_current_user),
    db: AsyncSession = Depends(get_db_session),
) -> dict:
    """Delete a camera group. Does not delete the cameras themselves."""
    _require_manager(user)

    result = await db.execute(
        select(CameraGroup).where(CameraGroup.id == group_id, CameraGroup.org_id == user.org_id)
    )
    group = result.scalars().first()
    if not group:
        raise NotFoundError(resource="CameraGroup", identifier=str(group_id))

    await db.delete(group)
    await db.flush()

    logger.info("Camera group deleted", group_id=str(group_id))

    return {
        "status": "success",
        "data": None,
        "message": "Camera group deleted successfully.",
    }


# ---------------------------------------------------------------------------
# POST /{camera_id}/zones - Create zone for camera
# ---------------------------------------------------------------------------


class CameraZoneCreateRequest(BaseModel):
    name: str = Field(..., min_length=1, max_length=255)
    zone_type: str = Field(default="roi", description="Zone type")
    coordinates: list[dict] = Field(..., min_length=3, description="Polygon points [{x, y}]")
    color: str | None = Field(default="#3b82f6")
    description: str | None = None


@router.post(
    "/{camera_id}/zones",
    response_model=SuccessResponse,
    status_code=status.HTTP_201_CREATED,
    summary="Create a zone for a camera",
    responses={403: {"model": ErrorResponse}, 404: {"model": ErrorResponse}},
)
async def create_camera_zone(
    camera_id: uuid.UUID,
    body: CameraZoneCreateRequest,
    user: User = Depends(_get_current_user),
    db: AsyncSession = Depends(get_db_session),
) -> dict:
    """Create a new detection zone polygon for a camera."""
    _require_manager(user)

    # Verify camera
    cam_result = await db.execute(
        select(Camera).where(Camera.id == camera_id, Camera.org_id == user.org_id)
    )
    camera = cam_result.scalars().first()
    if not camera:
        raise NotFoundError(resource="Camera", identifier=str(camera_id))

    from app.models.zone import Zone, ZoneType

    # Validate zone type
    try:
        zt = ZoneType(body.zone_type)
    except ValueError:
        zt = ZoneType.ROI

    # Validate coordinates (normalize to float 0-1 range)
    coords = []
    for pt in body.coordinates:
        x = float(pt.get("x", 0))
        y = float(pt.get("y", 0))
        coords.append({"x": x, "y": y})

    zone = Zone(
        id=uuid.uuid4(),
        org_id=user.org_id,
        camera_id=camera_id,
        name=body.name,
        zone_type=zt,
        coordinates=coords,
        color=body.color or "#3b82f6",
        description=body.description,
        is_active=True,
    )
    db.add(zone)
    await db.flush()

    logger.info("Zone created", zone_id=str(zone.id), camera_id=str(camera_id))

    return {
        "status": "success",
        "data": {
            "id": str(zone.id),
            "camera_id": str(zone.camera_id),
            "name": zone.name,
            "zone_type": zone.zone_type.value if hasattr(zone.zone_type, "value") else str(zone.zone_type),
            "coordinates": zone.coordinates,
            "color": zone.color,
            "description": zone.description,
            "is_active": zone.is_active,
            "created_at": zone.created_at.isoformat() if zone.created_at else None,
        },
        "message": "Zone created successfully.",
    }
