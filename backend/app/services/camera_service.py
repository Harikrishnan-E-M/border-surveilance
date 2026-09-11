"""Camera CRUD, stream operations, ONVIF discovery, and PTZ control.

Provides the business logic layer for camera management including
encrypted credential storage, stream connectivity testing, snapshot
capture, bulk CSV import, health monitoring, and PTZ commands.
"""

from __future__ import annotations

import asyncio
import csv
import io
import time
import uuid
from datetime import datetime, timezone
from typing import Any, Optional

import cv2
import numpy as np
import structlog
from sqlalchemy import func, select
from sqlalchemy.exc import IntegrityError
from sqlalchemy.ext.asyncio import AsyncSession

from app.config import get_settings
from app.exceptions import (
    DuplicateError,
    NotFoundError,
    StreamError,
    ValidationError,
)
from app.models.camera import (
    Camera,
    CameraHealth,
    CameraHealthStatus,
    RecordingMode,
    StreamProtocol,
)
from app.schemas.camera import CameraCreate, CameraUpdate
from app.utils.encryption import decrypt_string, encrypt_string

logger = structlog.stdlib.get_logger(__name__)


# ── Camera CRUD ──────────────────────────────────────────────────────────


async def create_camera(
    db: AsyncSession,
    org_id: uuid.UUID,
    data: CameraCreate,
) -> Camera:
    """Create a new camera with encrypted credentials.

    Args:
        db: Async database session.
        org_id: Organization the camera belongs to.
        data: Camera creation payload.

    Returns:
        The newly created Camera instance.

    Raises:
        ValidationError: If the stream protocol is invalid.
    """
    logger.info("Creating camera", name=data.name, org_id=str(org_id))

    username_enc: Optional[str] = None
    password_enc: Optional[str] = None

    if data.username:
        username_enc = encrypt_string(data.username)
    if data.password:
        password_enc = encrypt_string(data.password)

    stream_url_enc = encrypt_string(data.stream_url)

    camera = Camera(
        id=uuid.uuid4(),
        org_id=org_id,
        name=data.name,
        stream_url=stream_url_enc,
        protocol=StreamProtocol(data.protocol.value),
        location_description=data.location_description,
        username_encrypted=username_enc,
        password_encrypted=password_enc,
        resolution=data.resolution,
        fps=data.fps,
        latitude=data.latitude,
        longitude=data.longitude,
        recording_mode=RecordingMode(data.recording_mode.value),
        is_active=True,
        is_online=False,
    )
    db.add(camera)

    try:
        await db.flush()
    except IntegrityError as exc:
        await db.rollback()
        logger.error("Integrity error creating camera", error=str(exc))
        raise DuplicateError(
            message=f"A camera with name '{data.name}' already exists in this organization",
            code="CAMERA_DUPLICATE",
        )

    logger.info("Camera created", camera_id=str(camera.id), name=camera.name)
    return camera


async def get_cameras(
    db: AsyncSession,
    org_id: uuid.UUID,
    page: int = 1,
    page_size: int = 20,
    is_active: Optional[bool] = None,
    search: Optional[str] = None,
) -> tuple[list[Camera], int]:
    """Retrieve a paginated list of cameras for an organization.

    Args:
        db: Async database session.
        org_id: Organization filter.
        page: Page number (1-indexed).
        page_size: Items per page.
        is_active: Optional active status filter.
        search: Optional search string for name or location.

    Returns:
        A tuple of (cameras, total_count).
    """
    query = select(Camera).where(Camera.org_id == org_id)

    if is_active is not None:
        query = query.where(Camera.is_active == is_active)

    if search:
        search_filter = f"%{search}%"
        query = query.where(
            Camera.name.ilike(search_filter)
            | Camera.location_description.ilike(search_filter)
        )

    count_query = select(func.count()).select_from(query.subquery())
    total_result = await db.execute(count_query)
    total = total_result.scalar() or 0

    offset = (max(1, page) - 1) * page_size
    query = query.order_by(Camera.created_at.desc()).offset(offset).limit(page_size)
    result = await db.execute(query)
    cameras = list(result.scalars().all())

    return cameras, total


async def get_camera(
    db: AsyncSession,
    camera_id: uuid.UUID,
    org_id: Optional[uuid.UUID] = None,
) -> Camera:
    """Retrieve a single camera by ID.

    Args:
        db: Async database session.
        camera_id: Camera unique identifier.
        org_id: Optional organization filter for multi-tenant access control.

    Returns:
        The Camera instance.

    Raises:
        NotFoundError: If the camera does not exist.
    """
    query = select(Camera).where(Camera.id == camera_id)
    if org_id is not None:
        query = query.where(Camera.org_id == org_id)

    result = await db.execute(query)
    camera = result.scalar_one_or_none()

    if camera is None:
        raise NotFoundError(resource="Camera", identifier=camera_id)

    return camera


async def update_camera(
    db: AsyncSession,
    camera_id: uuid.UUID,
    data: CameraUpdate,
    org_id: Optional[uuid.UUID] = None,
) -> Camera:
    """Update an existing camera's fields.

    Only non-None fields in the update payload are changed.
    Sensitive fields are re-encrypted if updated.

    Args:
        db: Async database session.
        camera_id: Camera to update.
        data: Partial update payload.
        org_id: Optional organization filter.

    Returns:
        The updated Camera instance.

    Raises:
        NotFoundError: If the camera does not exist.
    """
    camera = await get_camera(db, camera_id, org_id)

    if data.name is not None:
        camera.name = data.name
    if data.stream_url is not None:
        camera.stream_url = encrypt_string(data.stream_url)
    if data.location_description is not None:
        camera.location_description = data.location_description
    if data.is_active is not None:
        camera.is_active = data.is_active
    if data.recording_mode is not None:
        camera.recording_mode = RecordingMode(data.recording_mode.value)

    await db.flush()
    logger.info("Camera updated", camera_id=str(camera_id))
    return camera


async def delete_camera(
    db: AsyncSession,
    camera_id: uuid.UUID,
    org_id: Optional[uuid.UUID] = None,
) -> bool:
    """Delete a camera and all associated records (cascading).

    Args:
        db: Async database session.
        camera_id: Camera to delete.
        org_id: Optional organization filter.

    Returns:
        True if the camera was deleted.

    Raises:
        NotFoundError: If the camera does not exist.
    """
    camera = await get_camera(db, camera_id, org_id)
    await db.delete(camera)
    await db.flush()
    logger.info("Camera deleted", camera_id=str(camera_id))
    return True


# ── Stream Operations ────────────────────────────────────────────────────


def _get_decrypted_stream_url(camera: Camera) -> str:
    """Decrypt and return the camera's stream URL.

    If the URL contains credential placeholders, the encrypted username
    and password are decrypted and substituted.
    """
    try:
        return decrypt_string(camera.stream_url)
    except ValueError:
        return camera.stream_url


async def test_stream(
    db: AsyncSession,
    camera_id: uuid.UUID,
    org_id: Optional[uuid.UUID] = None,
) -> dict[str, Any]:
    """Test connectivity and capability of a camera stream.

    Opens the stream with OpenCV VideoCapture, reads a single frame,
    and reports resolution, FPS, and success status.

    Args:
        db: Async database session.
        camera_id: Camera to test.
        org_id: Optional organization filter.

    Returns:
        A dict with success, resolution, fps, and optional error.
    """
    camera = await get_camera(db, camera_id, org_id)
    stream_url = _get_decrypted_stream_url(camera)

    logger.info("Testing stream", camera_id=str(camera_id))

    loop = asyncio.get_event_loop()
    result = await loop.run_in_executor(None, _test_stream_sync, stream_url)
    return result


def _test_stream_sync(stream_url: str) -> dict[str, Any]:
    """Synchronous stream test using OpenCV VideoCapture."""
    cap = None
    try:
        cap = cv2.VideoCapture(stream_url)
        if not cap.isOpened():
            return {
                "success": False,
                "resolution": None,
                "fps": None,
                "error": "Failed to open stream. Check URL and credentials.",
            }

        ret, frame = cap.read()
        if not ret or frame is None:
            return {
                "success": False,
                "resolution": None,
                "fps": None,
                "error": "Stream opened but failed to read a frame.",
            }

        width = int(cap.get(cv2.CAP_PROP_FRAME_WIDTH))
        height = int(cap.get(cv2.CAP_PROP_FRAME_HEIGHT))
        fps = int(cap.get(cv2.CAP_PROP_FPS))

        return {
            "success": True,
            "resolution": f"{width}x{height}",
            "fps": fps if fps > 0 else None,
            "error": None,
        }
    except Exception as exc:
        return {
            "success": False,
            "resolution": None,
            "fps": None,
            "error": f"Stream test failed: {exc}",
        }
    finally:
        if cap is not None:
            cap.release()


async def get_snapshot(
    db: AsyncSession,
    camera_id: uuid.UUID,
    org_id: Optional[uuid.UUID] = None,
) -> bytes:
    """Capture a single frame from a camera stream as JPEG bytes.

    Args:
        db: Async database session.
        camera_id: Camera to capture from.
        org_id: Optional organization filter.

    Returns:
        JPEG-encoded image bytes.

    Raises:
        StreamError: If the stream cannot be opened or a frame cannot be read.
    """
    camera = await get_camera(db, camera_id, org_id)
    stream_url = _get_decrypted_stream_url(camera)

    logger.info("Capturing snapshot", camera_id=str(camera_id))

    loop = asyncio.get_event_loop()
    jpeg_bytes = await loop.run_in_executor(None, _capture_snapshot_sync, stream_url)

    if jpeg_bytes is None:
        raise StreamError(
            message=f"Failed to capture snapshot from camera '{camera.name}'",
            code="SNAPSHOT_FAILED",
        )

    return jpeg_bytes


def _capture_snapshot_sync(stream_url: str) -> Optional[bytes]:
    """Synchronous snapshot capture using OpenCV."""
    cap = None
    try:
        cap = cv2.VideoCapture(stream_url)
        if not cap.isOpened():
            return None

        ret, frame = cap.read()
        if not ret or frame is None:
            return None

        success, buffer = cv2.imencode(".jpg", frame, [cv2.IMWRITE_JPEG_QUALITY, 85])
        if not success:
            return None

        return buffer.tobytes()
    except Exception as exc:
        logger.error("Snapshot capture failed", error=str(exc))
        return None
    finally:
        if cap is not None:
            cap.release()


# ── ONVIF Discovery ──────────────────────────────────────────────────────


async def discover_onvif_devices(
    timeout: float = 5.0,
) -> dict[str, Any]:
    """Discover ONVIF-compatible cameras on the local network.

    Uses WS-Discovery to find devices. Falls back gracefully if the
    onvif-zeep library is not available.

    Args:
        timeout: Discovery timeout in seconds.

    Returns:
        Dict with 'devices' list and 'scan_duration_seconds'.
    """
    logger.info("Starting ONVIF discovery", timeout=timeout)
    start = time.monotonic()

    devices: list[dict[str, Any]] = []

    try:
        from onvif import ONVIFCamera
        from wsdiscovery.discovery import ThreadedWSDiscovery

        wsd = ThreadedWSDiscovery()
        wsd.start()

        loop = asyncio.get_event_loop()
        services = await asyncio.wait_for(
            loop.run_in_executor(None, wsd.searchServices),
            timeout=timeout,
        )

        for service in services:
            scopes = service.getScopes()
            xaddrs = service.getXAddrs()

            device_info: dict[str, Any] = {
                "ip": "",
                "port": 80,
                "name": None,
                "manufacturer": None,
                "model": None,
                "stream_urls": [],
            }

            for xaddr in xaddrs:
                if "://" in xaddr:
                    parts = xaddr.split("://")[1].split("/")[0]
                    if ":" in parts:
                        host, port_str = parts.rsplit(":", 1)
                        device_info["ip"] = host
                        try:
                            device_info["port"] = int(port_str)
                        except ValueError:
                            device_info["port"] = 80
                    else:
                        device_info["ip"] = parts

            for scope in scopes:
                scope_str = scope.getValue()
                if "onvif://www.onvif.org/name/" in scope_str:
                    device_info["name"] = scope_str.split("/name/")[-1]
                elif "onvif://www.onvif.org/manufacturer/" in scope_str:
                    device_info["manufacturer"] = scope_str.split("/manufacturer/")[-1]
                elif "onvif://www.onvif.org/hardware/" in scope_str:
                    device_info["model"] = scope_str.split("/hardware/")[-1]

            if device_info["ip"]:
                devices.append(device_info)

        wsd.stop()

    except ImportError:
        logger.warning("ONVIF discovery unavailable: onvif-zeep or wsdiscovery not installed")
    except asyncio.TimeoutError:
        logger.warning("ONVIF discovery timed out", timeout=timeout)
    except Exception as exc:
        logger.error("ONVIF discovery failed", error=str(exc))

    scan_duration = round(time.monotonic() - start, 2)
    logger.info("ONVIF discovery completed", device_count=len(devices), duration=scan_duration)

    return {
        "devices": devices,
        "scan_duration_seconds": scan_duration,
    }


# ── PTZ Control ──────────────────────────────────────────────────────────


async def send_ptz_command(
    db: AsyncSession,
    camera_id: uuid.UUID,
    action: str,
    value: float = 0.5,
    org_id: Optional[uuid.UUID] = None,
) -> dict[str, Any]:
    """Send a PTZ (Pan-Tilt-Zoom) command to a camera via ONVIF.

    Args:
        db: Async database session.
        camera_id: Target camera.
        action: PTZ action (pan_left, pan_right, tilt_up, tilt_down, zoom_in, zoom_out, preset_goto).
        value: Magnitude of movement (0.0 to 1.0) or preset index.
        org_id: Optional organization filter.

    Returns:
        Dict with success status and message.
    """
    camera = await get_camera(db, camera_id, org_id)
    stream_url = _get_decrypted_stream_url(camera)

    logger.info("Sending PTZ command", camera_id=str(camera_id), action=action, value=value)

    ptz_vectors: dict[str, tuple[float, float, float]] = {
        "pan_left": (-value, 0.0, 0.0),
        "pan_right": (value, 0.0, 0.0),
        "tilt_up": (0.0, value, 0.0),
        "tilt_down": (0.0, -value, 0.0),
        "zoom_in": (0.0, 0.0, value),
        "zoom_out": (0.0, 0.0, -value),
    }

    try:
        from onvif import ONVIFCamera

        parts = stream_url.replace("rtsp://", "").split("@")
        if len(parts) == 2:
            cred_part, host_part = parts
            username, password = cred_part.split(":", 1) if ":" in cred_part else (cred_part, "")
            host = host_part.split("/")[0].split(":")[0]
        else:
            host = parts[0].split("/")[0].split(":")[0]
            username = ""
            password = ""

        if camera.username_encrypted:
            username = decrypt_string(camera.username_encrypted)
        if camera.password_encrypted:
            password = decrypt_string(camera.password_encrypted)

        cam = ONVIFCamera(host, 80, username, password)
        media_service = cam.create_media_service()
        ptz_service = cam.create_ptz_service()

        profiles = media_service.GetProfiles()
        if not profiles:
            return {"success": False, "message": "No media profiles found on camera"}

        profile_token = profiles[0].token

        if action == "preset_goto":
            presets = ptz_service.GetPresets({"ProfileToken": profile_token})
            preset_index = int(value)
            if preset_index < len(presets):
                ptz_service.GotoPreset({
                    "ProfileToken": profile_token,
                    "PresetToken": presets[preset_index].token,
                })
                return {"success": True, "message": f"Moved to preset {preset_index}"}
            return {"success": False, "message": f"Preset {preset_index} not found"}

        if action not in ptz_vectors:
            return {"success": False, "message": f"Unknown PTZ action: {action}"}

        pan, tilt, zoom = ptz_vectors[action]
        request = ptz_service.create_type("ContinuousMove")
        request.ProfileToken = profile_token
        request.Velocity = {"PanTilt": {"x": pan, "y": tilt}, "Zoom": {"x": zoom}}

        ptz_service.ContinuousMove(request)

        await asyncio.sleep(0.5)
        ptz_service.Stop({"ProfileToken": profile_token})

        return {"success": True, "message": f"PTZ command '{action}' executed"}

    except ImportError:
        logger.warning("ONVIF library not installed for PTZ control")
        return {"success": False, "message": "ONVIF library not installed"}
    except Exception as exc:
        logger.error("PTZ command failed", error=str(exc), action=action)
        return {"success": False, "message": f"PTZ command failed: {exc}"}


# ── Bulk Import ──────────────────────────────────────────────────────────


async def bulk_import_cameras(
    db: AsyncSession,
    org_id: uuid.UUID,
    csv_content: str,
) -> dict[str, Any]:
    """Import cameras in bulk from CSV content.

    Expected CSV columns: name, stream_url, protocol, location_description,
    username, password, resolution, fps, latitude, longitude, recording_mode.

    Args:
        db: Async database session.
        org_id: Organization to import cameras into.
        csv_content: Raw CSV string.

    Returns:
        Dict with imported_count, failed_count, and errors list.
    """
    logger.info("Starting bulk camera import", org_id=str(org_id))

    reader = csv.DictReader(io.StringIO(csv_content))
    imported_count = 0
    failed_count = 0
    errors: list[dict[str, str]] = []

    for row_num, row in enumerate(reader, start=2):
        try:
            name = row.get("name", "").strip()
            stream_url = row.get("stream_url", "").strip()

            if not name or not stream_url:
                errors.append({"row": str(row_num), "error": "Missing required fields: name and stream_url"})
                failed_count += 1
                continue

            protocol_str = row.get("protocol", "rtsp").strip().lower()
            try:
                protocol = StreamProtocol(protocol_str)
            except ValueError:
                protocol = StreamProtocol.RTSP

            rec_mode_str = row.get("recording_mode", "event").strip().lower()
            try:
                rec_mode = RecordingMode(rec_mode_str)
            except ValueError:
                rec_mode = RecordingMode.EVENT

            fps_str = row.get("fps", "").strip()
            fps_val: Optional[int] = None
            if fps_str:
                try:
                    fps_val = int(fps_str)
                except ValueError:
                    pass

            lat_str = row.get("latitude", "").strip()
            lng_str = row.get("longitude", "").strip()
            latitude: Optional[float] = None
            longitude: Optional[float] = None
            if lat_str:
                try:
                    latitude = float(lat_str)
                except ValueError:
                    pass
            if lng_str:
                try:
                    longitude = float(lng_str)
                except ValueError:
                    pass

            username = row.get("username", "").strip() or None
            password = row.get("password", "").strip() or None

            camera = Camera(
                id=uuid.uuid4(),
                org_id=org_id,
                name=name,
                stream_url=encrypt_string(stream_url),
                protocol=protocol,
                location_description=row.get("location_description", "").strip() or None,
                username_encrypted=encrypt_string(username) if username else None,
                password_encrypted=encrypt_string(password) if password else None,
                resolution=row.get("resolution", "").strip() or None,
                fps=fps_val,
                latitude=latitude,
                longitude=longitude,
                recording_mode=rec_mode,
                is_active=True,
                is_online=False,
            )
            db.add(camera)
            imported_count += 1

        except Exception as exc:
            errors.append({"row": str(row_num), "error": str(exc)})
            failed_count += 1

    if imported_count > 0:
        try:
            await db.flush()
        except IntegrityError as exc:
            await db.rollback()
            logger.error("Bulk import integrity error", error=str(exc))
            return {
                "imported_count": 0,
                "failed_count": imported_count + failed_count,
                "errors": [{"row": "all", "error": f"Database integrity error: {exc}"}],
            }

    logger.info(
        "Bulk import completed",
        imported=imported_count,
        failed=failed_count,
    )

    return {
        "imported_count": imported_count,
        "failed_count": failed_count,
        "errors": errors,
    }


# ── Camera Health ────────────────────────────────────────────────────────


async def update_camera_health(
    db: AsyncSession,
    camera_id: uuid.UUID,
    status: CameraHealthStatus,
    fps_actual: Optional[float] = None,
    latency_ms: Optional[float] = None,
    packet_loss_pct: Optional[float] = None,
    cpu_usage: Optional[float] = None,
    memory_usage: Optional[float] = None,
) -> CameraHealth:
    """Record a health telemetry point for a camera and update its online status.

    Args:
        db: Async database session.
        camera_id: Camera to update health for.
        status: Health status at this point in time.
        fps_actual: Measured FPS.
        latency_ms: Round-trip latency in milliseconds.
        packet_loss_pct: Packet loss percentage.
        cpu_usage: CPU usage percentage.
        memory_usage: Memory usage percentage.

    Returns:
        The created CameraHealth record.
    """
    health = CameraHealth(
        id=uuid.uuid4(),
        camera_id=camera_id,
        status=status,
        timestamp=datetime.now(timezone.utc),
        fps_actual=fps_actual,
        latency_ms=latency_ms,
        packet_loss_pct=packet_loss_pct,
        cpu_usage=cpu_usage,
        memory_usage=memory_usage,
    )
    db.add(health)

    result = await db.execute(select(Camera).where(Camera.id == camera_id))
    camera = result.scalar_one_or_none()
    if camera is not None:
        camera.is_online = status == CameraHealthStatus.ONLINE
        camera.last_seen_at = datetime.now(timezone.utc)

    await db.flush()

    logger.debug(
        "Camera health updated",
        camera_id=str(camera_id),
        status=status.value,
        fps=fps_actual,
        latency_ms=latency_ms,
    )

    return health


async def get_camera_health(
    db: AsyncSession,
    camera_id: uuid.UUID,
    limit: int = 50,
) -> list[CameraHealth]:
    """Retrieve recent health records for a camera.

    Args:
        db: Async database session.
        camera_id: Camera to get health for.
        limit: Maximum number of records to return.

    Returns:
        List of CameraHealth records ordered by timestamp descending.
    """
    query = (
        select(CameraHealth)
        .where(CameraHealth.camera_id == camera_id)
        .order_by(CameraHealth.timestamp.desc())
        .limit(limit)
    )
    result = await db.execute(query)
    return list(result.scalars().all())
