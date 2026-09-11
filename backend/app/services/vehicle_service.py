"""Vehicle CRUD, plate search, event logging, and entry/exit pairing.

Provides the business logic layer for the ANPR (Automatic Number Plate
Recognition) subsystem including vehicle registration, plate-based
search, detection event logging, entry/exit log pairing, and
vehicle access log queries.
"""

from __future__ import annotations

import uuid
from datetime import datetime, timezone
from typing import Any, Optional

import structlog
from sqlalchemy import func, or_, select
from sqlalchemy.exc import IntegrityError
from sqlalchemy.ext.asyncio import AsyncSession

from app.exceptions import DuplicateError, NotFoundError
from app.models.vehicle import (
    Vehicle,
    VehicleCategory,
    VehicleDirection,
    VehicleEvent,
    VehicleLog,
)
from app.schemas.vehicle import VehicleCreate

logger = structlog.stdlib.get_logger(__name__)


# ── Vehicle CRUD ─────────────────────────────────────────────────────────


async def create_vehicle(
    db: AsyncSession,
    org_id: uuid.UUID,
    data: VehicleCreate,
) -> Vehicle:
    """Register a new vehicle in the ANPR system.

    Args:
        db: Async database session.
        org_id: Organization the vehicle belongs to.
        data: Vehicle creation payload.

    Returns:
        The newly created Vehicle instance.

    Raises:
        DuplicateError: If a vehicle with the same plate already exists.
    """
    logger.info("Creating vehicle", plate=data.plate_number, org_id=str(org_id))

    plate_normalized = data.plate_number.upper().replace(" ", "").replace("-", "")

    try:
        category = VehicleCategory(data.category.value)
    except ValueError:
        category = VehicleCategory.VISITOR

    vehicle = Vehicle(
        id=uuid.uuid4(),
        org_id=org_id,
        plate_number=plate_normalized,
        owner_name=data.owner_name,
        vehicle_type=data.vehicle_type.value if data.vehicle_type else None,
        color=data.color,
        make=data.make,
        model_name=data.model_name,
        category=category,
        department=data.department,
        phone=data.phone,
        notes=data.notes,
        is_active=True,
    )
    db.add(vehicle)

    try:
        await db.flush()
    except IntegrityError as exc:
        await db.rollback()
        logger.warning("Duplicate vehicle plate", plate=plate_normalized, error=str(exc))
        raise DuplicateError(
            message=f"Vehicle with plate '{plate_normalized}' already exists",
            code="VEHICLE_DUPLICATE_PLATE",
        )

    logger.info("Vehicle created", vehicle_id=str(vehicle.id), plate=plate_normalized)
    return vehicle


async def get_vehicles(
    db: AsyncSession,
    org_id: uuid.UUID,
    page: int = 1,
    page_size: int = 20,
    search: Optional[str] = None,
    category: Optional[str] = None,
    is_active: Optional[bool] = None,
) -> tuple[list[Vehicle], int]:
    """Retrieve a paginated list of vehicles for an organization.

    Args:
        db: Async database session.
        org_id: Organization filter.
        page: Page number (1-indexed).
        page_size: Items per page.
        search: Optional search by plate number or owner name.
        category: Optional category filter.
        is_active: Optional active status filter.

    Returns:
        A tuple of (vehicles, total_count).
    """
    query = select(Vehicle).where(Vehicle.org_id == org_id)

    if search:
        search_filter = f"%{search}%"
        query = query.where(
            or_(
                Vehicle.plate_number.ilike(search_filter),
                Vehicle.owner_name.ilike(search_filter),
            )
        )

    if category:
        try:
            cat = VehicleCategory(category)
            query = query.where(Vehicle.category == cat)
        except ValueError:
            pass

    if is_active is not None:
        query = query.where(Vehicle.is_active == is_active)

    count_q = select(func.count()).select_from(query.subquery())
    total_result = await db.execute(count_q)
    total = total_result.scalar() or 0

    offset = (max(1, page) - 1) * page_size
    query = query.order_by(Vehicle.created_at.desc()).offset(offset).limit(page_size)
    result = await db.execute(query)
    vehicles = list(result.scalars().all())

    return vehicles, total


# ── Plate Search ─────────────────────────────────────────────────────────


async def search_plate(
    db: AsyncSession,
    org_id: uuid.UUID,
    plate_query: str,
    start_date: Optional[datetime] = None,
    end_date: Optional[datetime] = None,
    camera_ids: Optional[list[uuid.UUID]] = None,
    page: int = 1,
    page_size: int = 50,
) -> tuple[list[VehicleEvent], int]:
    """Search vehicle events by partial plate number match.

    Args:
        db: Async database session.
        org_id: Organization scope (applied via camera relationship).
        plate_query: Full or partial plate to search for.
        start_date: Optional start date filter.
        end_date: Optional end date filter.
        camera_ids: Optional camera filter.
        page: Page number.
        page_size: Items per page.

    Returns:
        Tuple of (matching vehicle events, total count).
    """
    from app.models.camera import Camera

    plate_normalized = plate_query.upper().replace(" ", "").replace("-", "")
    plate_pattern = f"%{plate_normalized}%"

    query = (
        select(VehicleEvent)
        .join(Camera, VehicleEvent.camera_id == Camera.id)
        .where(
            Camera.org_id == org_id,
            VehicleEvent.plate_number.ilike(plate_pattern),
        )
    )

    if start_date:
        query = query.where(VehicleEvent.timestamp >= start_date)
    if end_date:
        query = query.where(VehicleEvent.timestamp <= end_date)
    if camera_ids:
        query = query.where(VehicleEvent.camera_id.in_(camera_ids))

    count_q = select(func.count()).select_from(query.subquery())
    total_result = await db.execute(count_q)
    total = total_result.scalar() or 0

    offset = (max(1, page) - 1) * page_size
    query = query.order_by(VehicleEvent.timestamp.desc()).offset(offset).limit(page_size)
    result = await db.execute(query)
    events = list(result.scalars().all())

    return events, total


# ── Event Logging ────────────────────────────────────────────────────────


async def log_vehicle_event(
    db: AsyncSession,
    camera_id: uuid.UUID,
    plate_number: str,
    timestamp: datetime,
    plate_confidence: Optional[float] = None,
    direction: Optional[VehicleDirection] = None,
    zone_id: Optional[uuid.UUID] = None,
    plate_snapshot_path: Optional[str] = None,
    vehicle_snapshot_path: Optional[str] = None,
) -> VehicleEvent:
    """Log an ANPR detection event and attempt to match a registered vehicle.

    Args:
        db: Async database session.
        camera_id: Camera that captured the plate.
        plate_number: Detected plate text.
        timestamp: Detection timestamp.
        plate_confidence: OCR confidence score.
        direction: Direction of travel.
        zone_id: Detection zone.
        plate_snapshot_path: Path to cropped plate image.
        vehicle_snapshot_path: Path to full vehicle image.

    Returns:
        The created VehicleEvent instance.
    """
    plate_normalized = plate_number.upper().replace(" ", "").replace("-", "")

    matched_vehicle = await match_vehicle(db, camera_id, plate_normalized)

    event = VehicleEvent(
        id=uuid.uuid4(),
        camera_id=camera_id,
        zone_id=zone_id,
        plate_number=plate_normalized,
        plate_confidence=plate_confidence,
        vehicle_id=matched_vehicle.id if matched_vehicle else None,
        direction=direction,
        plate_snapshot_path=plate_snapshot_path,
        vehicle_snapshot_path=vehicle_snapshot_path,
        timestamp=timestamp,
    )
    db.add(event)
    await db.flush()

    logger.info(
        "Vehicle event logged",
        event_id=str(event.id),
        plate=plate_normalized,
        matched=matched_vehicle is not None,
        direction=direction.value if direction else None,
    )

    return event


async def match_vehicle(
    db: AsyncSession,
    camera_id: uuid.UUID,
    plate_number: str,
) -> Optional[Vehicle]:
    """Attempt to match a detected plate to a registered vehicle.

    Looks up the vehicle by plate number within the same organization
    as the camera.

    Args:
        db: Async database session.
        camera_id: Camera for org scoping.
        plate_number: Normalized plate number.

    Returns:
        The matched Vehicle, or None.
    """
    from app.models.camera import Camera

    cam_result = await db.execute(select(Camera).where(Camera.id == camera_id))
    camera = cam_result.scalar_one_or_none()
    if camera is None:
        return None

    result = await db.execute(
        select(Vehicle).where(
            Vehicle.org_id == camera.org_id,
            Vehicle.plate_number == plate_number,
            Vehicle.is_active.is_(True),
        )
    )
    return result.scalar_one_or_none()


# ── Entry/Exit Pairing ──────────────────────────────────────────────────


async def pair_entry_exit(
    db: AsyncSession,
    vehicle_id: uuid.UUID,
    camera_id: uuid.UUID,
    direction: VehicleDirection,
    timestamp: datetime,
) -> Optional[VehicleLog]:
    """Pair entry and exit events for a vehicle.

    On entry: creates a new VehicleLog with entry fields populated.
    On exit: finds the most recent unpaired entry log for this vehicle
    and populates the exit fields plus computes duration.

    Args:
        db: Async database session.
        vehicle_id: Registered vehicle ID.
        camera_id: Camera that captured the event.
        direction: Entry or exit.
        timestamp: Event timestamp.

    Returns:
        The created or updated VehicleLog, or None if direction is PASSING.
    """
    if direction == VehicleDirection.PASSING:
        return None

    if direction == VehicleDirection.ENTRY:
        log = VehicleLog(
            id=uuid.uuid4(),
            vehicle_id=vehicle_id,
            entry_camera_id=camera_id,
            entry_time=timestamp,
        )
        db.add(log)
        await db.flush()
        logger.info("Vehicle entry logged", vehicle_id=str(vehicle_id))
        return log

    if direction == VehicleDirection.EXIT:
        result = await db.execute(
            select(VehicleLog)
            .where(
                VehicleLog.vehicle_id == vehicle_id,
                VehicleLog.exit_time.is_(None),
            )
            .order_by(VehicleLog.entry_time.desc())
            .limit(1)
        )
        log = result.scalar_one_or_none()

        if log is None:
            log = VehicleLog(
                id=uuid.uuid4(),
                vehicle_id=vehicle_id,
                entry_camera_id=camera_id,
                entry_time=timestamp,
                exit_camera_id=camera_id,
                exit_time=timestamp,
                duration_seconds=0,
            )
            db.add(log)
            await db.flush()
            logger.info("Vehicle exit without prior entry", vehicle_id=str(vehicle_id))
            return log

        log.exit_camera_id = camera_id
        log.exit_time = timestamp
        duration = (timestamp - log.entry_time).total_seconds()
        log.duration_seconds = int(duration)
        await db.flush()

        logger.info(
            "Vehicle exit paired",
            vehicle_id=str(vehicle_id),
            duration_seconds=log.duration_seconds,
        )
        return log

    return None


# ── Vehicle Logs ─────────────────────────────────────────────────────────


async def get_vehicle_logs(
    db: AsyncSession,
    org_id: uuid.UUID,
    vehicle_id: Optional[uuid.UUID] = None,
    start_date: Optional[datetime] = None,
    end_date: Optional[datetime] = None,
    page: int = 1,
    page_size: int = 50,
) -> tuple[list[VehicleLog], int]:
    """Retrieve vehicle entry/exit logs with optional filtering.

    Args:
        db: Async database session.
        org_id: Organization scope.
        vehicle_id: Optional vehicle filter.
        start_date: Optional start date filter.
        end_date: Optional end date filter.
        page: Page number.
        page_size: Items per page.

    Returns:
        Tuple of (vehicle_logs, total_count).
    """
    query = (
        select(VehicleLog)
        .join(Vehicle, VehicleLog.vehicle_id == Vehicle.id)
        .where(Vehicle.org_id == org_id)
    )

    if vehicle_id:
        query = query.where(VehicleLog.vehicle_id == vehicle_id)
    if start_date:
        query = query.where(VehicleLog.entry_time >= start_date)
    if end_date:
        query = query.where(VehicleLog.entry_time <= end_date)

    count_q = select(func.count()).select_from(query.subquery())
    total_result = await db.execute(count_q)
    total = total_result.scalar() or 0

    offset = (max(1, page) - 1) * page_size
    query = query.order_by(VehicleLog.entry_time.desc()).offset(offset).limit(page_size)
    result = await db.execute(query)
    logs = list(result.scalars().all())

    return logs, total
