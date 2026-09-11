"""Zone CRUD and polygon validation service.

Zones are polygonal regions of interest drawn on camera views. They
scope detection rules to specific areas and are stored as JSON arrays
of normalised [x, y] coordinate pairs (0.0 to 1.0).
"""

from __future__ import annotations

import uuid
from typing import Optional

import structlog
from sqlalchemy import func, select
from sqlalchemy.ext.asyncio import AsyncSession

from app.exceptions import NotFoundError, ValidationError
from app.models.camera import Camera
from app.models.zone import Zone, ZoneType
from app.schemas.zone import ZoneCreate, ZoneUpdate

logger = structlog.stdlib.get_logger(__name__)

MINIMUM_POLYGON_POINTS = 3


# ── Polygon Validation ──────────────────────────────────────────────────


def validate_polygon(points: list[dict[str, float]]) -> list[dict[str, float]]:
    """Validate a polygon definition for zone creation.

    Checks that:
    1. The polygon has at least 3 points.
    2. All coordinates are normalised to [0.0, 1.0].
    3. No duplicate consecutive vertices.

    Args:
        points: List of point dicts with 'x' and 'y' keys.

    Returns:
        The validated list of points.

    Raises:
        ValidationError: If any validation check fails.
    """
    if len(points) < MINIMUM_POLYGON_POINTS:
        raise ValidationError(
            message=f"Polygon must have at least {MINIMUM_POLYGON_POINTS} points, got {len(points)}",
            code="POLYGON_TOO_FEW_POINTS",
        )

    for idx, point in enumerate(points):
        x = point.get("x")
        y = point.get("y")

        if x is None or y is None:
            raise ValidationError(
                message=f"Point at index {idx} is missing 'x' or 'y' coordinate",
                code="POLYGON_INVALID_POINT",
            )

        if not (0.0 <= x <= 1.0):
            raise ValidationError(
                message=f"Point at index {idx} has x={x} outside [0.0, 1.0] range",
                code="POLYGON_COORD_OUT_OF_RANGE",
            )

        if not (0.0 <= y <= 1.0):
            raise ValidationError(
                message=f"Point at index {idx} has y={y} outside [0.0, 1.0] range",
                code="POLYGON_COORD_OUT_OF_RANGE",
            )

    # Check for duplicate consecutive vertices
    for i in range(len(points)):
        next_idx = (i + 1) % len(points)
        if (
            abs(points[i]["x"] - points[next_idx]["x"]) < 1e-9
            and abs(points[i]["y"] - points[next_idx]["y"]) < 1e-9
        ):
            raise ValidationError(
                message=f"Points at index {i} and {next_idx} are duplicates",
                code="POLYGON_DUPLICATE_POINTS",
            )

    return points


# ── Zone CRUD ────────────────────────────────────────────────────────────


async def create_zone(
    db: AsyncSession,
    org_id: uuid.UUID,
    data: ZoneCreate,
) -> Zone:
    """Create a new zone on a camera.

    Validates that the camera exists and belongs to the organization,
    and that the polygon definition is valid.

    Args:
        db: Async database session.
        org_id: Organization owning the camera.
        data: Zone creation payload.

    Returns:
        The newly created Zone instance.

    Raises:
        NotFoundError: If the camera does not exist.
        ValidationError: If the polygon is invalid.
    """
    logger.info("Creating zone", name=data.name, camera_id=str(data.camera_id))

    # Verify camera exists and belongs to org
    result = await db.execute(
        select(Camera).where(Camera.id == data.camera_id, Camera.org_id == org_id)
    )
    camera = result.scalar_one_or_none()
    if camera is None:
        raise NotFoundError(resource="Camera", identifier=data.camera_id)

    # Validate polygon
    points_dicts = [{"x": p.x, "y": p.y} for p in data.polygon_points]
    validate_polygon(points_dicts)

    # Map schema zone type to model zone type
    try:
        zone_type = ZoneType(data.zone_type.value)
    except ValueError:
        zone_type = ZoneType.MONITORING

    zone = Zone(
        id=uuid.uuid4(),
        camera_id=data.camera_id,
        name=data.name,
        zone_type=zone_type,
        polygon_points=points_dicts,
        color_hex=data.color_hex or "#FF0000",
        is_active=True,
    )
    db.add(zone)
    await db.flush()

    logger.info("Zone created", zone_id=str(zone.id), name=zone.name)
    return zone


async def get_zones(
    db: AsyncSession,
    camera_id: Optional[uuid.UUID] = None,
    org_id: Optional[uuid.UUID] = None,
    is_active: Optional[bool] = None,
    page: int = 1,
    page_size: int = 50,
) -> tuple[list[Zone], int]:
    """Retrieve zones with optional filtering and pagination.

    Args:
        db: Async database session.
        camera_id: Optional filter by camera.
        org_id: Optional organization filter (via camera relationship).
        is_active: Optional active status filter.
        page: Page number (1-indexed).
        page_size: Items per page.

    Returns:
        A tuple of (zones, total_count).
    """
    query = select(Zone)

    if camera_id is not None:
        query = query.where(Zone.camera_id == camera_id)

    if org_id is not None:
        query = query.join(Camera).where(Camera.org_id == org_id)

    if is_active is not None:
        query = query.where(Zone.is_active == is_active)

    count_query = select(func.count()).select_from(query.subquery())
    total_result = await db.execute(count_query)
    total = total_result.scalar() or 0

    offset = (max(1, page) - 1) * page_size
    query = query.order_by(Zone.created_at.desc()).offset(offset).limit(page_size)
    result = await db.execute(query)
    zones = list(result.scalars().all())

    return zones, total


async def get_zone(
    db: AsyncSession,
    zone_id: uuid.UUID,
    org_id: Optional[uuid.UUID] = None,
) -> Zone:
    """Retrieve a single zone by ID.

    Args:
        db: Async database session.
        zone_id: Zone unique identifier.
        org_id: Optional organization filter.

    Returns:
        The Zone instance.

    Raises:
        NotFoundError: If the zone does not exist.
    """
    query = select(Zone).where(Zone.id == zone_id)

    if org_id is not None:
        query = query.join(Camera).where(Camera.org_id == org_id)

    result = await db.execute(query)
    zone = result.scalar_one_or_none()

    if zone is None:
        raise NotFoundError(resource="Zone", identifier=zone_id)

    return zone


async def update_zone(
    db: AsyncSession,
    zone_id: uuid.UUID,
    data: ZoneUpdate,
    org_id: Optional[uuid.UUID] = None,
) -> Zone:
    """Update an existing zone's fields.

    Only non-None fields in the update payload are changed. If polygon
    points are provided, they are validated before update.

    Args:
        db: Async database session.
        zone_id: Zone to update.
        data: Partial update payload.
        org_id: Optional organization filter.

    Returns:
        The updated Zone instance.

    Raises:
        NotFoundError: If the zone does not exist.
        ValidationError: If the updated polygon is invalid.
    """
    zone = await get_zone(db, zone_id, org_id)

    if data.name is not None:
        zone.name = data.name

    if data.zone_type is not None:
        try:
            zone.zone_type = ZoneType(data.zone_type.value)
        except ValueError:
            zone.zone_type = ZoneType.MONITORING

    if data.polygon_points is not None:
        points_dicts = [{"x": p.x, "y": p.y} for p in data.polygon_points]
        validate_polygon(points_dicts)
        zone.polygon_points = points_dicts

    if data.color_hex is not None:
        zone.color_hex = data.color_hex

    if data.is_active is not None:
        zone.is_active = data.is_active

    await db.flush()
    logger.info("Zone updated", zone_id=str(zone_id))
    return zone


async def delete_zone(
    db: AsyncSession,
    zone_id: uuid.UUID,
    org_id: Optional[uuid.UUID] = None,
) -> bool:
    """Delete a zone and cascade to associated rules.

    Args:
        db: Async database session.
        zone_id: Zone to delete.
        org_id: Optional organization filter.

    Returns:
        True if the zone was deleted.

    Raises:
        NotFoundError: If the zone does not exist.
    """
    zone = await get_zone(db, zone_id, org_id)
    await db.delete(zone)
    await db.flush()
    logger.info("Zone deleted", zone_id=str(zone_id))
    return True
