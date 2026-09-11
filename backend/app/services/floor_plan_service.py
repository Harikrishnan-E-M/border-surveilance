"""Floor Plan service layer.

Business logic for floor plan management, camera/zone placement,
live status aggregation, heatmap overlay generation, and person
track mapping onto the spatial layout.
"""

from __future__ import annotations

import io
import math
import uuid
from datetime import datetime, timedelta, timezone
from typing import Any, Optional

import structlog
from sqlalchemy import func, select
from sqlalchemy.ext.asyncio import AsyncSession

from app.config import get_settings
from app.exceptions import NotFoundError, StorageError, ValidationError
from app.models.alert import Alert, AlertStatus
from app.models.analytics import FootfallRecord
from app.models.camera import Camera
from app.models.floor_plan import (
    CameraPlacement,
    FloorPlan,
    FloorPlanOverlay,
    OverlayType,
    ZonePlacement,
)
from app.models.zone import Zone

logger = structlog.stdlib.get_logger(__name__)


# ---------------------------------------------------------------------------
# Helpers
# ---------------------------------------------------------------------------


def _parse_time_range(time_range: str) -> tuple[datetime, datetime]:
    """Convert a human-friendly time range string into a (start, end) tuple.

    Supported values: 1h, 6h, 12h, 24h, 7d, 30d.
    """
    now = datetime.now(timezone.utc)
    mapping: dict[str, timedelta] = {
        "1h": timedelta(hours=1),
        "6h": timedelta(hours=6),
        "12h": timedelta(hours=12),
        "24h": timedelta(hours=24),
        "7d": timedelta(days=7),
        "30d": timedelta(days=30),
    }
    delta = mapping.get(time_range)
    if delta is None:
        delta = timedelta(hours=24)
    return now - delta, now


def _get_minio_client():
    """Return a MinIO client using the application settings."""
    try:
        from minio import Minio

        settings = get_settings()
        return Minio(
            settings.MINIO_ENDPOINT,
            access_key=settings.MINIO_ACCESS_KEY,
            secret_key=settings.MINIO_SECRET_KEY,
            secure=settings.MINIO_SECURE,
        )
    except ImportError:
        logger.warning("MinIO client library not installed")
        return None


FLOOR_PLAN_BUCKET = "floor-plans"


async def _ensure_bucket(client) -> None:
    """Create the floor-plans bucket if it does not exist."""
    if client is None:
        return
    try:
        if not client.bucket_exists(FLOOR_PLAN_BUCKET):
            client.make_bucket(FLOOR_PLAN_BUCKET)
            logger.info("Created MinIO bucket", bucket=FLOOR_PLAN_BUCKET)
    except Exception as exc:
        logger.error("Failed to ensure MinIO bucket", error=str(exc))


async def _upload_to_minio(
    file_data: bytes,
    object_name: str,
    content_type: str = "image/png",
) -> str:
    """Upload a file to MinIO and return the object path.

    Returns:
        The object path string (bucket/object_name).

    Raises:
        StorageError: If the upload fails.
    """
    client = _get_minio_client()
    if client is None:
        # Fallback: store just the path for development without MinIO
        logger.warning("MinIO unavailable, storing path only", object_name=object_name)
        return f"{FLOOR_PLAN_BUCKET}/{object_name}"

    await _ensure_bucket(client)

    try:
        data_stream = io.BytesIO(file_data)
        client.put_object(
            FLOOR_PLAN_BUCKET,
            object_name,
            data_stream,
            length=len(file_data),
            content_type=content_type,
        )
        logger.info("Uploaded to MinIO", bucket=FLOOR_PLAN_BUCKET, object_name=object_name)
        return f"{FLOOR_PLAN_BUCKET}/{object_name}"
    except Exception as exc:
        logger.error("MinIO upload failed", error=str(exc))
        raise StorageError(message=f"Failed to upload floor plan image: {exc}")


async def _delete_from_minio(object_path: str) -> None:
    """Delete an object from MinIO given its full path (bucket/name)."""
    client = _get_minio_client()
    if client is None:
        return
    try:
        parts = object_path.split("/", 1)
        if len(parts) == 2:
            client.remove_object(parts[0], parts[1])
            logger.info("Deleted from MinIO", path=object_path)
    except Exception as exc:
        logger.warning("MinIO delete failed", path=object_path, error=str(exc))


def _get_image_dimensions(image_data: bytes) -> tuple[int, int]:
    """Extract width and height from image bytes using Pillow or fallback."""
    try:
        from PIL import Image

        img = Image.open(io.BytesIO(image_data))
        return img.size  # (width, height)
    except ImportError:
        logger.warning("Pillow not installed, using default dimensions")
        return 1920, 1080
    except Exception:
        return 1920, 1080


# ---------------------------------------------------------------------------
# Floor Plan CRUD
# ---------------------------------------------------------------------------


async def create_floor_plan(
    db: AsyncSession,
    org_id: uuid.UUID,
    data: dict[str, Any],
    image_data: bytes,
    image_filename: str,
    content_type: str = "image/png",
) -> FloorPlan:
    """Upload a floor plan image to MinIO and create the database record.

    Args:
        db: Async database session.
        org_id: Organisation owning the floor plan.
        data: Floor plan metadata (name, building_name, floor_number, etc.).
        image_data: Raw bytes of the uploaded image file.
        image_filename: Original filename for the image.
        content_type: MIME type of the image.

    Returns:
        The created FloorPlan instance.
    """
    logger.info("Creating floor plan", name=data.get("name"), org_id=str(org_id))

    # Determine image dimensions
    width, height = _get_image_dimensions(image_data)

    # Upload image to MinIO
    ext = image_filename.rsplit(".", 1)[-1] if "." in image_filename else "png"
    object_name = f"{org_id}/{uuid.uuid4()}.{ext}"
    image_path = await _upload_to_minio(image_data, object_name, content_type)

    floor_plan = FloorPlan(
        id=uuid.uuid4(),
        org_id=org_id,
        name=data.get("name", "Untitled Floor Plan"),
        building_name=data.get("building_name"),
        floor_number=data.get("floor_number", 0),
        image_path=image_path,
        width_px=width,
        height_px=height,
        scale_meters_per_pixel=data.get("scale_meters_per_pixel"),
        metadata_json=data.get("metadata_json"),
        is_active=True,
    )
    db.add(floor_plan)
    await db.flush()

    logger.info("Floor plan created", floor_plan_id=str(floor_plan.id), name=floor_plan.name)
    return floor_plan


async def update_floor_plan(
    db: AsyncSession,
    floor_plan_id: uuid.UUID,
    data: dict[str, Any],
    org_id: Optional[uuid.UUID] = None,
) -> FloorPlan:
    """Update floor plan metadata fields.

    Args:
        db: Async database session.
        floor_plan_id: ID of the floor plan to update.
        data: Dict of fields to update (only non-None values applied).
        org_id: Optional org filter for multi-tenant access control.

    Returns:
        The updated FloorPlan instance.

    Raises:
        NotFoundError: If the floor plan does not exist.
    """
    floor_plan = await _get_floor_plan(db, floor_plan_id, org_id)

    updatable_fields = {
        "name", "building_name", "floor_number",
        "scale_meters_per_pixel", "metadata_json", "is_active",
    }
    for field, value in data.items():
        if field in updatable_fields and value is not None:
            setattr(floor_plan, field, value)

    await db.flush()
    logger.info("Floor plan updated", floor_plan_id=str(floor_plan_id))
    return floor_plan


async def delete_floor_plan(
    db: AsyncSession,
    floor_plan_id: uuid.UUID,
    org_id: Optional[uuid.UUID] = None,
) -> bool:
    """Delete a floor plan and all associated placements/overlays.

    Also removes the image and any overlay images from MinIO.

    Args:
        db: Async database session.
        floor_plan_id: ID of the floor plan to delete.
        org_id: Optional org filter.

    Returns:
        True if deletion was successful.

    Raises:
        NotFoundError: If the floor plan does not exist.
    """
    floor_plan = await _get_floor_plan(db, floor_plan_id, org_id)

    # Clean up MinIO objects
    await _delete_from_minio(floor_plan.image_path)
    for overlay in floor_plan.overlays or []:
        await _delete_from_minio(overlay.data_url)

    await db.delete(floor_plan)
    await db.flush()

    logger.info("Floor plan deleted", floor_plan_id=str(floor_plan_id))
    return True


async def get_floor_plan(
    db: AsyncSession,
    floor_plan_id: uuid.UUID,
    org_id: Optional[uuid.UUID] = None,
) -> FloorPlan:
    """Retrieve a single floor plan with all placements and overlays.

    Args:
        db: Async database session.
        floor_plan_id: Floor plan ID.
        org_id: Optional org filter.

    Returns:
        The FloorPlan instance with eager-loaded relationships.

    Raises:
        NotFoundError: If the floor plan does not exist.
    """
    return await _get_floor_plan(db, floor_plan_id, org_id)


async def get_floor_plans(
    db: AsyncSession,
    org_id: uuid.UUID,
) -> list[FloorPlan]:
    """List all floor plans for an organisation.

    Args:
        db: Async database session.
        org_id: Organisation filter.

    Returns:
        List of FloorPlan instances ordered by building name then floor number.
    """
    query = (
        select(FloorPlan)
        .where(FloorPlan.org_id == org_id, FloorPlan.is_active.is_(True))
        .order_by(FloorPlan.building_name.asc().nullslast(), FloorPlan.floor_number.asc())
    )
    result = await db.execute(query)
    return list(result.scalars().all())


async def _get_floor_plan(
    db: AsyncSession,
    floor_plan_id: uuid.UUID,
    org_id: Optional[uuid.UUID] = None,
) -> FloorPlan:
    """Internal helper to fetch a floor plan by ID with optional org filter."""
    query = select(FloorPlan).where(FloorPlan.id == floor_plan_id)
    if org_id is not None:
        query = query.where(FloorPlan.org_id == org_id)

    result = await db.execute(query)
    floor_plan = result.scalar_one_or_none()

    if floor_plan is None:
        raise NotFoundError(resource="FloorPlan", identifier=floor_plan_id)
    return floor_plan


# ---------------------------------------------------------------------------
# Camera Placement
# ---------------------------------------------------------------------------


async def place_camera(
    db: AsyncSession,
    floor_plan_id: uuid.UUID,
    camera_id: uuid.UUID,
    position: dict[str, Any],
    org_id: Optional[uuid.UUID] = None,
) -> CameraPlacement:
    """Place a camera on a floor plan.

    Args:
        db: Async database session.
        floor_plan_id: Target floor plan.
        camera_id: Camera to place.
        position: Dict with x_position, y_position, rotation_degrees, etc.
        org_id: Optional org filter.

    Returns:
        The created CameraPlacement.

    Raises:
        NotFoundError: If the floor plan or camera does not exist.
        ValidationError: If the camera is already placed on this floor plan.
    """
    floor_plan = await _get_floor_plan(db, floor_plan_id, org_id)

    # Verify camera exists
    cam_result = await db.execute(select(Camera).where(Camera.id == camera_id))
    camera = cam_result.scalar_one_or_none()
    if camera is None:
        raise NotFoundError(resource="Camera", identifier=camera_id)

    # Check for duplicate placement
    existing = await db.execute(
        select(CameraPlacement).where(
            CameraPlacement.floor_plan_id == floor_plan_id,
            CameraPlacement.camera_id == camera_id,
        )
    )
    if existing.scalar_one_or_none() is not None:
        raise ValidationError(
            message=f"Camera '{camera.name}' is already placed on this floor plan."
        )

    placement = CameraPlacement(
        id=uuid.uuid4(),
        floor_plan_id=floor_plan_id,
        camera_id=camera_id,
        x_position=position.get("x_position", 0.5),
        y_position=position.get("y_position", 0.5),
        rotation_degrees=position.get("rotation_degrees", 0.0),
        fov_angle=position.get("fov_angle", 90.0),
        fov_range=position.get("fov_range", 100.0),
        label=position.get("label"),
    )
    db.add(placement)
    await db.flush()

    logger.info(
        "Camera placed on floor plan",
        floor_plan_id=str(floor_plan_id),
        camera_id=str(camera_id),
        placement_id=str(placement.id),
    )
    return placement


async def update_camera_placement(
    db: AsyncSession,
    placement_id: uuid.UUID,
    position: dict[str, Any],
    org_id: Optional[uuid.UUID] = None,
) -> CameraPlacement:
    """Update the position/orientation of a camera placement.

    Args:
        db: Async database session.
        placement_id: Camera placement to update.
        position: Dict of fields to update.
        org_id: Optional org filter (checked via floor plan).

    Returns:
        The updated CameraPlacement.

    Raises:
        NotFoundError: If the placement does not exist.
    """
    placement = await _get_camera_placement(db, placement_id, org_id)

    updatable = {"x_position", "y_position", "rotation_degrees", "fov_angle", "fov_range", "label"}
    for field, value in position.items():
        if field in updatable and value is not None:
            setattr(placement, field, value)

    await db.flush()
    logger.info("Camera placement updated", placement_id=str(placement_id))
    return placement


async def remove_camera_placement(
    db: AsyncSession,
    placement_id: uuid.UUID,
    org_id: Optional[uuid.UUID] = None,
) -> bool:
    """Remove a camera from a floor plan.

    Args:
        db: Async database session.
        placement_id: Camera placement to remove.
        org_id: Optional org filter.

    Returns:
        True if removal was successful.

    Raises:
        NotFoundError: If the placement does not exist.
    """
    placement = await _get_camera_placement(db, placement_id, org_id)
    await db.delete(placement)
    await db.flush()
    logger.info("Camera placement removed", placement_id=str(placement_id))
    return True


async def _get_camera_placement(
    db: AsyncSession,
    placement_id: uuid.UUID,
    org_id: Optional[uuid.UUID] = None,
) -> CameraPlacement:
    """Fetch a camera placement by ID with optional org filter."""
    query = select(CameraPlacement).where(CameraPlacement.id == placement_id)
    result = await db.execute(query)
    placement = result.scalar_one_or_none()

    if placement is None:
        raise NotFoundError(resource="CameraPlacement", identifier=placement_id)

    # Org check via floor plan
    if org_id is not None:
        fp_result = await db.execute(
            select(FloorPlan).where(
                FloorPlan.id == placement.floor_plan_id,
                FloorPlan.org_id == org_id,
            )
        )
        if fp_result.scalar_one_or_none() is None:
            raise NotFoundError(resource="CameraPlacement", identifier=placement_id)

    return placement


# ---------------------------------------------------------------------------
# Zone Placement
# ---------------------------------------------------------------------------


async def place_zone(
    db: AsyncSession,
    floor_plan_id: uuid.UUID,
    zone_id: uuid.UUID,
    polygon: dict[str, Any],
    org_id: Optional[uuid.UUID] = None,
) -> ZonePlacement:
    """Place a zone polygon on a floor plan.

    Args:
        db: Async database session.
        floor_plan_id: Target floor plan.
        zone_id: Zone to place.
        polygon: Dict with polygon_points, color, opacity.
        org_id: Optional org filter.

    Returns:
        The created ZonePlacement.

    Raises:
        NotFoundError: If floor plan or zone does not exist.
        ValidationError: If the zone is already placed on this floor plan.
    """
    floor_plan = await _get_floor_plan(db, floor_plan_id, org_id)

    # Verify zone exists
    zone_result = await db.execute(select(Zone).where(Zone.id == zone_id))
    zone = zone_result.scalar_one_or_none()
    if zone is None:
        raise NotFoundError(resource="Zone", identifier=zone_id)

    # Check duplicate
    existing = await db.execute(
        select(ZonePlacement).where(
            ZonePlacement.floor_plan_id == floor_plan_id,
            ZonePlacement.zone_id == zone_id,
        )
    )
    if existing.scalar_one_or_none() is not None:
        raise ValidationError(
            message=f"Zone '{zone.name}' is already placed on this floor plan."
        )

    # Convert polygon points to list of dicts
    points = polygon.get("polygon_points", [])
    if isinstance(points, list) and len(points) > 0:
        if isinstance(points[0], dict):
            polygon_data = points
        else:
            # Handle list of FloorPlanPoint-like objects
            polygon_data = [{"x": p.x, "y": p.y} if hasattr(p, "x") else p for p in points]
    else:
        raise ValidationError(message="polygon_points must contain at least 3 coordinate pairs.")

    placement = ZonePlacement(
        id=uuid.uuid4(),
        floor_plan_id=floor_plan_id,
        zone_id=zone_id,
        polygon_points=polygon_data,
        color=polygon.get("color", "#3B82F6"),
        opacity=polygon.get("opacity", 0.3),
    )
    db.add(placement)
    await db.flush()

    logger.info(
        "Zone placed on floor plan",
        floor_plan_id=str(floor_plan_id),
        zone_id=str(zone_id),
        placement_id=str(placement.id),
    )
    return placement


async def update_zone_placement(
    db: AsyncSession,
    placement_id: uuid.UUID,
    polygon: dict[str, Any],
    org_id: Optional[uuid.UUID] = None,
) -> ZonePlacement:
    """Update a zone placement's polygon, color, or opacity.

    Args:
        db: Async database session.
        placement_id: Zone placement to update.
        polygon: Dict of fields to update.
        org_id: Optional org filter.

    Returns:
        The updated ZonePlacement.

    Raises:
        NotFoundError: If the placement does not exist.
    """
    placement = await _get_zone_placement(db, placement_id, org_id)

    if "polygon_points" in polygon and polygon["polygon_points"] is not None:
        points = polygon["polygon_points"]
        if isinstance(points, list) and len(points) >= 3:
            if isinstance(points[0], dict):
                placement.polygon_points = points
            else:
                placement.polygon_points = [
                    {"x": p.x, "y": p.y} if hasattr(p, "x") else p for p in points
                ]
    if "color" in polygon and polygon["color"] is not None:
        placement.color = polygon["color"]
    if "opacity" in polygon and polygon["opacity"] is not None:
        placement.opacity = polygon["opacity"]

    await db.flush()
    logger.info("Zone placement updated", placement_id=str(placement_id))
    return placement


async def remove_zone_placement(
    db: AsyncSession,
    placement_id: uuid.UUID,
    org_id: Optional[uuid.UUID] = None,
) -> bool:
    """Remove a zone from a floor plan.

    Args:
        db: Async database session.
        placement_id: Zone placement to remove.
        org_id: Optional org filter.

    Returns:
        True if removal was successful.

    Raises:
        NotFoundError: If the placement does not exist.
    """
    placement = await _get_zone_placement(db, placement_id, org_id)
    await db.delete(placement)
    await db.flush()
    logger.info("Zone placement removed", placement_id=str(placement_id))
    return True


async def _get_zone_placement(
    db: AsyncSession,
    placement_id: uuid.UUID,
    org_id: Optional[uuid.UUID] = None,
) -> ZonePlacement:
    """Fetch a zone placement by ID with optional org filter."""
    query = select(ZonePlacement).where(ZonePlacement.id == placement_id)
    result = await db.execute(query)
    placement = result.scalar_one_or_none()

    if placement is None:
        raise NotFoundError(resource="ZonePlacement", identifier=placement_id)

    if org_id is not None:
        fp_result = await db.execute(
            select(FloorPlan).where(
                FloorPlan.id == placement.floor_plan_id,
                FloorPlan.org_id == org_id,
            )
        )
        if fp_result.scalar_one_or_none() is None:
            raise NotFoundError(resource="ZonePlacement", identifier=placement_id)

    return placement


# ---------------------------------------------------------------------------
# Live Status
# ---------------------------------------------------------------------------


async def get_live_status(
    db: AsyncSession,
    floor_plan_id: uuid.UUID,
    org_id: Optional[uuid.UUID] = None,
) -> dict[str, Any]:
    """Get live status data for a floor plan including camera statuses,
    active alerts, and current zone occupancy.

    Args:
        db: Async database session.
        floor_plan_id: Floor plan to query.
        org_id: Optional org filter.

    Returns:
        Dict with camera_statuses, zone_occupancies, active_alerts, person_counts.
    """
    floor_plan = await _get_floor_plan(db, floor_plan_id, org_id)
    now = datetime.now(timezone.utc)

    # -- Camera statuses with alert counts ------------------------------------
    camera_statuses = []
    camera_ids: list[uuid.UUID] = []

    for cp in floor_plan.camera_placements or []:
        camera = cp.camera
        cam_id = cp.camera_id
        camera_ids.append(cam_id)

        # Count active alerts for this camera
        alert_count_result = await db.execute(
            select(func.count()).select_from(
                select(Alert).where(
                    Alert.camera_id == cam_id,
                    Alert.status.in_([AlertStatus.NEW.value, AlertStatus.ESCALATED.value]),
                ).subquery()
            )
        )
        active_alert_count = alert_count_result.scalar() or 0

        camera_statuses.append({
            "camera_id": str(cam_id),
            "placement_id": str(cp.id),
            "camera_name": camera.name if camera else (cp.label or "Unknown"),
            "x_position": cp.x_position,
            "y_position": cp.y_position,
            "rotation_degrees": cp.rotation_degrees,
            "fov_angle": cp.fov_angle,
            "fov_range": cp.fov_range,
            "is_online": camera.is_online if camera else False,
            "has_active_alerts": active_alert_count > 0,
            "active_alert_count": active_alert_count,
            "label": cp.label,
        })

    # -- Active alerts for all cameras on this floor plan ---------------------
    active_alerts = []
    if camera_ids:
        alerts_result = await db.execute(
            select(Alert)
            .where(
                Alert.camera_id.in_(camera_ids),
                Alert.status.in_([AlertStatus.NEW.value, AlertStatus.ESCALATED.value]),
            )
            .order_by(Alert.created_at.desc())
            .limit(50)
        )
        for alert in alerts_result.scalars().all():
            active_alerts.append({
                "alert_id": str(alert.id),
                "camera_id": str(alert.camera_id),
                "title": alert.title,
                "severity": alert.severity.value if hasattr(alert.severity, "value") else str(alert.severity),
                "alert_type": alert.alert_type.value if hasattr(alert.alert_type, "value") else str(alert.alert_type),
                "created_at": alert.created_at.isoformat(),
            })

    # -- Zone occupancies from latest footfall records ------------------------
    zone_occupancies = []
    person_counts: dict[str, int] = {}

    for zp in floor_plan.zone_placements or []:
        zone = zp.zone
        zone_id = zp.zone_id

        # Get latest occupancy estimate for this zone
        occupancy = 0
        ff_result = await db.execute(
            select(FootfallRecord)
            .where(FootfallRecord.zone_id == zone_id)
            .order_by(FootfallRecord.timestamp.desc())
            .limit(1)
        )
        latest_ff = ff_result.scalar_one_or_none()
        if latest_ff and latest_ff.occupancy_estimate is not None:
            occupancy = latest_ff.occupancy_estimate

        person_counts[str(zone_id)] = occupancy

        zone_occupancies.append({
            "zone_id": str(zone_id),
            "placement_id": str(zp.id),
            "zone_name": zone.name if zone else "Unknown",
            "zone_type": zone.zone_type.value if zone and hasattr(zone.zone_type, "value") else None,
            "polygon_points": zp.polygon_points,
            "color": zp.color,
            "opacity": zp.opacity,
            "current_occupancy": occupancy,
            "capacity": None,
        })

    return {
        "floor_plan_id": str(floor_plan_id),
        "camera_statuses": camera_statuses,
        "zone_occupancies": zone_occupancies,
        "active_alerts": active_alerts,
        "person_counts": person_counts,
        "timestamp": now.isoformat(),
    }


# ---------------------------------------------------------------------------
# Heatmap Overlay Generation
# ---------------------------------------------------------------------------


async def generate_heatmap_overlay(
    db: AsyncSession,
    floor_plan_id: uuid.UUID,
    time_range: str = "24h",
    color_scheme: str = "jet",
    org_id: Optional[uuid.UUID] = None,
) -> dict[str, Any]:
    """Generate a heatmap overlay image from analytics data projected onto
    the floor plan and store it in MinIO.

    The heatmap is built by aggregating footfall / detection data from all
    cameras placed on the floor plan, mapping their positions onto the
    floor plan canvas, and rendering a gaussian-blurred heatmap image.

    Args:
        db: Async database session.
        floor_plan_id: Floor plan to generate the heatmap for.
        time_range: Time window (1h, 6h, 12h, 24h, 7d, 30d).
        color_scheme: Matplotlib colour map name.
        org_id: Optional org filter.

    Returns:
        Dict with overlay metadata (id, image_url, generated_at).
    """
    floor_plan = await _get_floor_plan(db, floor_plan_id, org_id)
    start_time, end_time = _parse_time_range(time_range)
    now = datetime.now(timezone.utc)

    # Collect footfall data per camera placement position
    heatmap_points: list[tuple[float, float, int]] = []

    for cp in floor_plan.camera_placements or []:
        # Sum entries for this camera in the time range
        ff_result = await db.execute(
            select(func.coalesce(func.sum(FootfallRecord.entries_count), 0)).where(
                FootfallRecord.camera_id == cp.camera_id,
                FootfallRecord.timestamp >= start_time,
                FootfallRecord.timestamp <= end_time,
            )
        )
        total_entries = ff_result.scalar() or 0
        if total_entries > 0:
            heatmap_points.append((cp.x_position, cp.y_position, int(total_entries)))

    # Generate heatmap image
    heatmap_bytes = _render_heatmap_image(
        width=floor_plan.width_px,
        height=floor_plan.height_px,
        points=heatmap_points,
        color_scheme=color_scheme,
    )

    # Upload to MinIO
    object_name = f"{floor_plan.org_id}/overlays/{floor_plan_id}/heatmap_{uuid.uuid4()}.png"
    image_path = await _upload_to_minio(heatmap_bytes, object_name, "image/png")

    # Store overlay record
    overlay = FloorPlanOverlay(
        id=uuid.uuid4(),
        floor_plan_id=floor_plan_id,
        overlay_type=OverlayType.HEATMAP,
        data_url=image_path,
        generated_at=now,
        valid_until=now + timedelta(hours=1),
    )
    db.add(overlay)
    await db.flush()

    logger.info(
        "Heatmap overlay generated",
        floor_plan_id=str(floor_plan_id),
        overlay_id=str(overlay.id),
        point_count=len(heatmap_points),
    )

    return {
        "id": str(overlay.id),
        "floor_plan_id": str(floor_plan_id),
        "overlay_type": "heatmap",
        "image_url": image_path,
        "generated_at": now.isoformat(),
        "valid_until": overlay.valid_until.isoformat() if overlay.valid_until else None,
    }


def _render_heatmap_image(
    width: int,
    height: int,
    points: list[tuple[float, float, int]],
    color_scheme: str = "jet",
) -> bytes:
    """Render a heatmap image from weighted points.

    Uses numpy and PIL for rendering. Falls back to a transparent PNG
    if the required libraries are not available.

    Args:
        width: Output image width in pixels.
        height: Output image height in pixels.
        points: List of (rel_x, rel_y, weight) tuples.
        color_scheme: Colour map name.

    Returns:
        PNG image bytes.
    """
    try:
        import numpy as np
        from PIL import Image

        # Create a blank heatmap array
        heatmap = np.zeros((height, width), dtype=np.float64)

        # Gaussian radius proportional to image size
        radius = max(width, height) // 10

        for rel_x, rel_y, weight in points:
            cx = int(rel_x * width)
            cy = int(rel_y * height)

            # Apply gaussian blob
            y_coords, x_coords = np.ogrid[
                max(0, cy - radius): min(height, cy + radius),
                max(0, cx - radius): min(width, cx + radius),
            ]
            dist_sq = (x_coords - cx) ** 2 + (y_coords - cy) ** 2
            sigma = radius / 3.0
            gaussian = np.exp(-dist_sq / (2 * sigma * sigma))
            gaussian *= weight

            y_start = max(0, cy - radius)
            y_end = min(height, cy + radius)
            x_start = max(0, cx - radius)
            x_end = min(width, cx + radius)

            heatmap[y_start:y_end, x_start:x_end] += gaussian

        # Normalise to 0-255
        if heatmap.max() > 0:
            heatmap = (heatmap / heatmap.max() * 255).astype(np.uint8)
        else:
            heatmap = heatmap.astype(np.uint8)

        # Apply colour map
        colour_maps = {
            "jet": _colormap_jet,
            "hot": _colormap_hot,
            "inferno": _colormap_inferno,
            "viridis": _colormap_viridis,
            "plasma": _colormap_plasma,
        }
        apply_cmap = colour_maps.get(color_scheme, _colormap_jet)

        # Create RGBA image
        rgba = np.zeros((height, width, 4), dtype=np.uint8)
        for y in range(height):
            for x in range(width):
                val = heatmap[y, x]
                if val > 5:  # threshold to avoid coloring near-zero areas
                    r, g, b = apply_cmap(val / 255.0)
                    rgba[y, x] = [r, g, b, min(val, 200)]  # semi-transparent

        img = Image.fromarray(rgba, "RGBA")
        buffer = io.BytesIO()
        img.save(buffer, format="PNG")
        return buffer.getvalue()

    except ImportError:
        logger.warning("numpy/Pillow not installed, generating empty overlay")
        return _generate_empty_png(width, height)


def _colormap_jet(v: float) -> tuple[int, int, int]:
    """Simple jet colormap approximation."""
    r = int(max(0, min(255, 255 * min(4 * v - 1.5, -4 * v + 4.5))))
    g = int(max(0, min(255, 255 * min(4 * v - 0.5, -4 * v + 3.5))))
    b = int(max(0, min(255, 255 * min(4 * v + 0.5, -4 * v + 2.5))))
    return r, g, b


def _colormap_hot(v: float) -> tuple[int, int, int]:
    """Simple hot colormap approximation."""
    r = int(max(0, min(255, 255 * min(3 * v, 1.0))))
    g = int(max(0, min(255, 255 * max(0, 3 * v - 1.0))))
    b = int(max(0, min(255, 255 * max(0, 3 * v - 2.0))))
    return r, g, b


def _colormap_inferno(v: float) -> tuple[int, int, int]:
    """Simplified inferno colormap."""
    r = int(max(0, min(255, 255 * (1.4 * v ** 3 - 0.6 * v ** 2 + 0.8 * v + 0.0))))
    g = int(max(0, min(255, 255 * max(0, 3 * v ** 2 - 2 * v ** 3))))
    b = int(max(0, min(255, 255 * max(0, 1 - 3 * v ** 2 + 2 * v ** 3))))
    return r, g, b


def _colormap_viridis(v: float) -> tuple[int, int, int]:
    """Simplified viridis colormap."""
    r = int(max(0, min(255, 255 * (0.27 + 0.73 * v ** 2))))
    g = int(max(0, min(255, 255 * (0.0 + 0.99 * v))))
    b = int(max(0, min(255, 255 * (0.33 + 0.67 * (1 - v)))))
    return r, g, b


def _colormap_plasma(v: float) -> tuple[int, int, int]:
    """Simplified plasma colormap."""
    r = int(max(0, min(255, 255 * min(1.0, 0.05 + 2.4 * v))))
    g = int(max(0, min(255, 255 * max(0, -0.7 + 2.7 * v - 1.5 * v ** 2))))
    b = int(max(0, min(255, 255 * max(0, 0.53 - 1.6 * v + 2.1 * v ** 2))))
    return r, g, b


def _generate_empty_png(width: int, height: int) -> bytes:
    """Generate a 1x1 transparent PNG as a fallback."""
    try:
        from PIL import Image

        img = Image.new("RGBA", (width, height), (0, 0, 0, 0))
        buffer = io.BytesIO()
        img.save(buffer, format="PNG")
        return buffer.getvalue()
    except ImportError:
        # Minimal 1x1 transparent PNG
        return (
            b"\x89PNG\r\n\x1a\n\x00\x00\x00\rIHDR\x00\x00\x00\x01"
            b"\x00\x00\x00\x01\x08\x06\x00\x00\x00\x1f\x15\xc4\x89"
            b"\x00\x00\x00\nIDATx\x9cc\x00\x01\x00\x00\x05\x00\x01"
            b"\r\n\xb4\x00\x00\x00\x00IEND\xaeB`\x82"
        )


# ---------------------------------------------------------------------------
# Person Tracks on Map
# ---------------------------------------------------------------------------


async def get_person_tracks_on_map(
    db: AsyncSession,
    floor_plan_id: uuid.UUID,
    global_person_id: uuid.UUID,
    org_id: Optional[uuid.UUID] = None,
) -> dict[str, Any]:
    """Map a person's movement across cameras onto the floor plan.

    Looks up all person tracks for the given global_person_id, then maps
    each camera appearance to its position on the floor plan.

    Args:
        db: Async database session.
        floor_plan_id: Floor plan to project tracks onto.
        global_person_id: Cross-camera person identity.
        org_id: Optional org filter.

    Returns:
        Dict with track_points, total_cameras_visited, first/last seen, duration.
    """
    floor_plan = await _get_floor_plan(db, floor_plan_id, org_id)

    # Build a camera_id -> placement map for quick lookup
    camera_position_map: dict[uuid.UUID, dict[str, Any]] = {}
    for cp in floor_plan.camera_placements or []:
        camera_position_map[cp.camera_id] = {
            "x_position": cp.x_position,
            "y_position": cp.y_position,
            "camera_name": cp.camera.name if cp.camera else (cp.label or "Unknown"),
        }

    # Fetch person tracks from the ReID system
    track_points: list[dict[str, Any]] = []
    try:
        from app.models.reid import PersonTrack

        tracks_result = await db.execute(
            select(PersonTrack)
            .where(PersonTrack.global_person_id == global_person_id)
            .order_by(PersonTrack.first_seen.asc())
        )
        tracks = tracks_result.scalars().all()

        cameras_visited: set[uuid.UUID] = set()
        first_seen: Optional[datetime] = None
        last_seen: Optional[datetime] = None

        for track in tracks:
            if track.camera_id in camera_position_map:
                pos = camera_position_map[track.camera_id]
                cameras_visited.add(track.camera_id)

                if first_seen is None or track.first_seen < first_seen:
                    first_seen = track.first_seen
                if last_seen is None or track.last_seen > last_seen:
                    last_seen = track.last_seen

                track_points.append({
                    "camera_id": str(track.camera_id),
                    "camera_name": pos["camera_name"],
                    "x_position": pos["x_position"],
                    "y_position": pos["y_position"],
                    "timestamp": track.first_seen.isoformat(),
                    "thumbnail_url": track.thumbnail_path,
                })

        total_duration = None
        if first_seen and last_seen:
            total_duration = (last_seen - first_seen).total_seconds()

        return {
            "floor_plan_id": str(floor_plan_id),
            "global_person_id": str(global_person_id),
            "track_points": track_points,
            "total_cameras_visited": len(cameras_visited),
            "first_seen": first_seen.isoformat() if first_seen else None,
            "last_seen": last_seen.isoformat() if last_seen else None,
            "total_duration_seconds": total_duration,
        }

    except ImportError:
        logger.warning("ReID models not available for person track mapping")
        return {
            "floor_plan_id": str(floor_plan_id),
            "global_person_id": str(global_person_id),
            "track_points": [],
            "total_cameras_visited": 0,
            "first_seen": None,
            "last_seen": None,
            "total_duration_seconds": None,
        }
