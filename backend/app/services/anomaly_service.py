"""Anomaly detection service layer.

Provides the business logic for querying, managing, and triggering
anomaly detection operations. Acts as the bridge between the API
layer and the CV anomaly detector engine / database models.
"""

from __future__ import annotations

import json
import uuid
from datetime import datetime, timedelta, timezone
from typing import Any, Optional

import structlog
from sqlalchemy import and_, delete, func, select, update
from sqlalchemy.ext.asyncio import AsyncSession

from app.cv.analyzers.anomaly_detector import AnomalyDetector, BaselineProfile
from app.models.alert import Alert
from app.models.analytics import FootfallRecord
from app.models.anomaly import (
    AnomalyBaseline,
    AnomalyEvent,
    AnomalySeverity,
    AnomalyType,
)
from app.models.camera import Camera
from app.models.zone import Zone
from app.schemas.anomaly import SENSITIVITY_PRESETS

logger = structlog.stdlib.get_logger(__name__)


# -- Query operations --------------------------------------------------------


async def get_anomalies(
    db: AsyncSession,
    org_id: uuid.UUID,
    camera_id: Optional[uuid.UUID] = None,
    zone_id: Optional[uuid.UUID] = None,
    anomaly_type: Optional[str] = None,
    severity: Optional[str] = None,
    is_acknowledged: Optional[bool] = None,
    start_date: Optional[datetime] = None,
    end_date: Optional[datetime] = None,
    page: int = 1,
    page_size: int = 20,
) -> tuple[list[AnomalyEvent], int]:
    """Query anomaly events with filtering and pagination.

    Args:
        db: Async database session.
        org_id: Organization scope.
        camera_id: Optional camera filter.
        zone_id: Optional zone filter.
        anomaly_type: Optional type filter (count, temporal, etc.).
        severity: Optional severity filter (info, warning, critical).
        is_acknowledged: Optional acknowledged status filter.
        start_date: Optional start of date range.
        end_date: Optional end of date range.
        page: Page number (1-indexed).
        page_size: Items per page.

    Returns:
        Tuple of (anomaly_events, total_count).
    """
    query = select(AnomalyEvent).where(AnomalyEvent.org_id == org_id)

    if camera_id:
        query = query.where(AnomalyEvent.camera_id == camera_id)
    if zone_id:
        query = query.where(AnomalyEvent.zone_id == zone_id)
    if anomaly_type:
        try:
            at = AnomalyType(anomaly_type)
            query = query.where(AnomalyEvent.anomaly_type == at)
        except ValueError:
            pass
    if severity:
        try:
            sev = AnomalySeverity(severity)
            query = query.where(AnomalyEvent.severity == sev)
        except ValueError:
            pass
    if is_acknowledged is not None:
        query = query.where(AnomalyEvent.is_acknowledged == is_acknowledged)
    if start_date:
        query = query.where(AnomalyEvent.created_at >= start_date)
    if end_date:
        query = query.where(AnomalyEvent.created_at <= end_date)

    # Count total
    count_q = select(func.count()).select_from(query.subquery())
    total_result = await db.execute(count_q)
    total = total_result.scalar() or 0

    # Paginate
    offset = (max(1, page) - 1) * page_size
    query = query.order_by(AnomalyEvent.created_at.desc()).offset(offset).limit(page_size)
    result = await db.execute(query)
    records = list(result.scalars().all())

    return records, total


async def get_anomaly_by_id(
    db: AsyncSession,
    org_id: uuid.UUID,
    anomaly_id: uuid.UUID,
) -> Optional[AnomalyEvent]:
    """Fetch a single anomaly event by ID, scoped to the organization.

    Args:
        db: Async database session.
        org_id: Organization scope.
        anomaly_id: Anomaly event ID.

    Returns:
        AnomalyEvent if found, else None.
    """
    result = await db.execute(
        select(AnomalyEvent).where(
            AnomalyEvent.id == anomaly_id,
            AnomalyEvent.org_id == org_id,
        )
    )
    return result.scalar_one_or_none()


async def acknowledge_anomaly(
    db: AsyncSession,
    org_id: uuid.UUID,
    anomaly_id: uuid.UUID,
    user_id: uuid.UUID,
    notes: Optional[str] = None,
) -> Optional[AnomalyEvent]:
    """Mark an anomaly event as acknowledged.

    Args:
        db: Async database session.
        org_id: Organization scope.
        anomaly_id: Anomaly event ID.
        user_id: ID of the user acknowledging.
        notes: Optional acknowledgement notes.

    Returns:
        Updated AnomalyEvent if found, else None.
    """
    event = await get_anomaly_by_id(db, org_id, anomaly_id)
    if event is None:
        return None

    event.is_acknowledged = True
    event.acknowledged_by = user_id
    event.acknowledged_at = datetime.now(timezone.utc)

    if notes and event.metadata_json:
        event.metadata_json["acknowledgement_notes"] = notes
    elif notes:
        event.metadata_json = {"acknowledgement_notes": notes}

    db.add(event)
    await db.flush()

    logger.info(
        "anomaly.acknowledged",
        anomaly_id=str(anomaly_id),
        user_id=str(user_id),
    )

    return event


# -- Statistics operations ---------------------------------------------------


async def get_anomaly_stats(
    db: AsyncSession,
    org_id: uuid.UUID,
    start_date: Optional[datetime] = None,
    end_date: Optional[datetime] = None,
    camera_id: Optional[uuid.UUID] = None,
) -> dict[str, Any]:
    """Compute anomaly statistics for the organization.

    Args:
        db: Async database session.
        org_id: Organization scope.
        start_date: Optional start of date range.
        end_date: Optional end of date range.
        camera_id: Optional camera filter.

    Returns:
        Dict with total, by_type, by_severity, by_camera, etc.
    """
    base_filter = [AnomalyEvent.org_id == org_id]
    if start_date:
        base_filter.append(AnomalyEvent.created_at >= start_date)
    if end_date:
        base_filter.append(AnomalyEvent.created_at <= end_date)
    if camera_id:
        base_filter.append(AnomalyEvent.camera_id == camera_id)

    # Total count
    total_q = select(func.count()).where(and_(*base_filter))
    total = (await db.execute(total_q)).scalar() or 0

    # By type
    by_type_q = (
        select(AnomalyEvent.anomaly_type, func.count().label("cnt"))
        .where(and_(*base_filter))
        .group_by(AnomalyEvent.anomaly_type)
    )
    by_type_result = await db.execute(by_type_q)
    by_type = [
        {"label": row[0].value if hasattr(row[0], "value") else str(row[0]), "count": row[1]}
        for row in by_type_result.all()
    ]

    # By severity
    by_severity_q = (
        select(AnomalyEvent.severity, func.count().label("cnt"))
        .where(and_(*base_filter))
        .group_by(AnomalyEvent.severity)
    )
    by_severity_result = await db.execute(by_severity_q)
    by_severity = [
        {"label": row[0].value if hasattr(row[0], "value") else str(row[0]), "count": row[1]}
        for row in by_severity_result.all()
    ]

    # By camera
    by_camera_q = (
        select(Camera.name, func.count().label("cnt"))
        .join(AnomalyEvent, AnomalyEvent.camera_id == Camera.id)
        .where(and_(*base_filter))
        .group_by(Camera.name)
        .order_by(func.count().desc())
        .limit(10)
    )
    by_camera_result = await db.execute(by_camera_q)
    by_camera = [
        {"label": row[0], "count": row[1]}
        for row in by_camera_result.all()
    ]

    # Acknowledged vs unacknowledged
    ack_q = select(func.count()).where(
        and_(*base_filter, AnomalyEvent.is_acknowledged.is_(True))
    )
    acknowledged_count = (await db.execute(ack_q)).scalar() or 0

    # Average confidence
    avg_conf_q = select(func.avg(AnomalyEvent.confidence)).where(and_(*base_filter))
    avg_confidence = float((await db.execute(avg_conf_q)).scalar() or 0)

    # Timeline: group by hour for last 24h or by day for longer ranges
    timeline_q = (
        select(
            func.date_trunc("hour", AnomalyEvent.created_at).label("bucket"),
            func.count().label("total_count"),
            func.count().filter(AnomalyEvent.severity == AnomalySeverity.CRITICAL).label("critical_count"),
            func.count().filter(AnomalyEvent.severity == AnomalySeverity.WARNING).label("warning_count"),
            func.count().filter(AnomalyEvent.severity == AnomalySeverity.INFO).label("info_count"),
        )
        .where(and_(*base_filter))
        .group_by("bucket")
        .order_by("bucket")
        .limit(168)  # Max 7 days of hourly data
    )
    timeline_result = await db.execute(timeline_q)
    timeline = [
        {
            "timestamp": row[0].isoformat() if row[0] else "",
            "count": row[1],
            "critical": row[2],
            "warning": row[3],
            "info": row[4],
        }
        for row in timeline_result.all()
    ]

    return {
        "total": total,
        "by_type": by_type,
        "by_severity": by_severity,
        "by_camera": by_camera,
        "acknowledged_count": acknowledged_count,
        "unacknowledged_count": total - acknowledged_count,
        "avg_confidence": round(avg_confidence, 3),
        "timeline": timeline,
    }


# -- Detection execution ----------------------------------------------------


async def run_anomaly_detection(
    db: AsyncSession,
    camera_id: uuid.UUID,
    org_id: uuid.UUID,
    detections: Optional[list[dict[str, Any]]] = None,
    tracks: Optional[list[dict[str, Any]]] = None,
    event_counts: Optional[list[dict[str, Any]]] = None,
    redis_client: Any = None,
) -> list[AnomalyEvent]:
    """Execute anomaly detection for a camera and persist results.

    Loads the baseline from the database (or Redis), runs the detector,
    and stores any detected anomalies.

    Args:
        db: Async database session.
        camera_id: Camera to check.
        org_id: Organization scope.
        detections: Optional zone detection data.
        tracks: Optional track data.
        event_counts: Optional frequency data.
        redis_client: Optional async Redis client.

    Returns:
        List of persisted AnomalyEvent model instances.
    """
    detector = AnomalyDetector(redis_client=redis_client)

    # Load sensitivity config from Redis if available
    if redis_client:
        try:
            config_key = f"visionai:anomaly:sensitivity:{org_id}:{camera_id}"
            config_data = await redis_client.get(config_key)
            if config_data:
                config = json.loads(config_data)
                detector.set_thresholds(
                    warning_sigma=config.get("warning_sigma"),
                    critical_sigma=config.get("critical_sigma"),
                    min_confidence=config.get("min_confidence"),
                )
        except Exception as exc:
            logger.debug("Failed to load sensitivity config from Redis", error=str(exc))

    # Load baseline
    baseline = None
    if redis_client:
        baseline = await detector.load_baseline_from_redis(str(camera_id))

    if baseline is None:
        # Fall back to DB
        baseline_record = await db.execute(
            select(AnomalyBaseline).where(
                AnomalyBaseline.camera_id == camera_id,
                AnomalyBaseline.org_id == org_id,
                AnomalyBaseline.is_stale.is_(False),
            ).order_by(AnomalyBaseline.updated_at.desc()).limit(1)
        )
        bl = baseline_record.scalar_one_or_none()
        if bl and bl.hourly_profile:
            baseline = BaselineProfile.from_dict({
                "hourly_counts": bl.hourly_profile.get("hourly_counts", [0.0] * 24),
                "hourly_std": bl.hourly_profile.get("hourly_std", [1.0] * 24),
                "day_of_week_factors": (
                    bl.day_of_week_profile.get("factors", [1.0] * 7)
                    if bl.day_of_week_profile else [1.0] * 7
                ),
                "zone_baselines": bl.hourly_profile.get("zone_baselines", {}),
                "sample_count": bl.sample_count,
                "built_at": bl.updated_at.isoformat() if bl.updated_at else "",
            })

    if baseline is None:
        logger.debug("No baseline available, skipping detection", camera_id=str(camera_id))
        return []

    # Build observations dict
    now = datetime.now(timezone.utc)
    observations: dict[str, Any] = {
        "hour": now.hour,
        "day_of_week": now.weekday(),
    }

    # Get current count from the most recent footfall record
    recent_footfall_q = (
        select(FootfallRecord)
        .where(FootfallRecord.camera_id == camera_id)
        .order_by(FootfallRecord.timestamp.desc())
        .limit(1)
    )
    recent_footfall = (await db.execute(recent_footfall_q)).scalar_one_or_none()
    if recent_footfall:
        observations["current_count"] = float(recent_footfall.entries_count)

    if detections:
        observations["detections"] = detections
    if tracks:
        observations["tracks"] = tracks
    if event_counts:
        observations["event_counts"] = event_counts

    # Run composite detection
    score, events = detector.get_anomaly_score(
        observations=observations,
        baseline=baseline,
        camera_id=str(camera_id),
    )

    if not events:
        return []

    # Persist detected anomalies
    persisted: list[AnomalyEvent] = []
    for event in events:
        try:
            anomaly_type = AnomalyType(event.anomaly_type)
        except ValueError:
            anomaly_type = AnomalyType.COUNT

        try:
            severity = AnomalySeverity(event.severity)
        except ValueError:
            severity = AnomalySeverity.INFO

        db_event = AnomalyEvent(
            org_id=org_id,
            camera_id=camera_id,
            zone_id=uuid.UUID(event.zone_id) if event.zone_id else None,
            anomaly_type=anomaly_type,
            severity=severity,
            confidence=event.confidence,
            description=event.description,
            baseline_value=event.baseline_value,
            observed_value=event.observed_value,
            deviation_sigma=event.deviation_sigma,
            metadata_json={
                "composite_score": score,
                "detection_timestamp": event.timestamp,
            },
        )
        db.add(db_event)
        persisted.append(db_event)

    await db.flush()

    logger.info(
        "anomaly_detection.completed",
        camera_id=str(camera_id),
        anomalies_detected=len(persisted),
        composite_score=round(score, 3),
    )

    return persisted


# -- Baseline management ----------------------------------------------------


async def get_baseline_status(
    db: AsyncSession,
    org_id: uuid.UUID,
) -> list[dict[str, Any]]:
    """Return baseline freshness status per camera.

    Args:
        db: Async database session.
        org_id: Organization scope.

    Returns:
        List of dicts with camera baseline status.
    """
    query = (
        select(
            AnomalyBaseline.camera_id,
            AnomalyBaseline.zone_id,
            AnomalyBaseline.baseline_type,
            AnomalyBaseline.sample_count,
            AnomalyBaseline.is_stale,
            AnomalyBaseline.updated_at,
            Camera.name.label("camera_name"),
        )
        .join(Camera, AnomalyBaseline.camera_id == Camera.id)
        .where(AnomalyBaseline.org_id == org_id)
        .order_by(Camera.name, AnomalyBaseline.updated_at.desc())
    )

    result = await db.execute(query)
    rows = result.all()

    statuses = []
    for row in rows:
        zone_name = None
        if row.zone_id:
            zone_result = await db.execute(select(Zone.name).where(Zone.id == row.zone_id))
            zone_name = zone_result.scalar_one_or_none()

        statuses.append({
            "camera_id": str(row.camera_id),
            "camera_name": row.camera_name,
            "zone_id": str(row.zone_id) if row.zone_id else None,
            "zone_name": zone_name,
            "baseline_type": row.baseline_type,
            "sample_count": row.sample_count,
            "is_stale": row.is_stale,
            "updated_at": row.updated_at.isoformat() if row.updated_at else None,
        })

    # Add cameras with no baseline
    cameras_with_baseline = {s["camera_id"] for s in statuses}
    all_cameras_q = select(Camera.id, Camera.name).where(Camera.org_id == org_id, Camera.is_active.is_(True))
    all_cameras = await db.execute(all_cameras_q)
    for cam in all_cameras.all():
        if str(cam.id) not in cameras_with_baseline:
            statuses.append({
                "camera_id": str(cam.id),
                "camera_name": cam.name,
                "zone_id": None,
                "zone_name": None,
                "baseline_type": "footfall",
                "sample_count": 0,
                "is_stale": True,
                "updated_at": None,
            })

    return statuses


async def rebuild_baselines(
    db: AsyncSession,
    org_id: uuid.UUID,
    camera_ids: Optional[list[uuid.UUID]] = None,
    days: int = 30,
    redis_client: Any = None,
) -> dict[str, Any]:
    """Rebuild statistical baselines from historical data.

    Queries footfall records from the specified time window, computes
    new baselines, and stores them both in the database and Redis.

    Args:
        db: Async database session.
        org_id: Organization scope.
        camera_ids: Optional list of specific cameras. If None, rebuilds all.
        days: Number of historical days to use.
        redis_client: Optional async Redis client for caching.

    Returns:
        Dict summarizing the rebuild results.
    """
    now = datetime.now(timezone.utc)
    start_date = now - timedelta(days=days)

    # Determine cameras to process
    if camera_ids:
        cameras_q = select(Camera).where(
            Camera.org_id == org_id,
            Camera.id.in_(camera_ids),
            Camera.is_active.is_(True),
        )
    else:
        cameras_q = select(Camera).where(
            Camera.org_id == org_id,
            Camera.is_active.is_(True),
        )

    cameras_result = await db.execute(cameras_q)
    cameras = list(cameras_result.scalars().all())

    detector = AnomalyDetector(redis_client=redis_client)
    results = {"cameras_processed": 0, "baselines_created": 0, "errors": []}

    for camera in cameras:
        try:
            # Query historical footfall data
            footfall_q = (
                select(
                    FootfallRecord.timestamp,
                    FootfallRecord.entries_count,
                    FootfallRecord.zone_id,
                )
                .where(
                    FootfallRecord.camera_id == camera.id,
                    FootfallRecord.timestamp >= start_date,
                )
                .order_by(FootfallRecord.timestamp)
            )
            footfall_result = await db.execute(footfall_q)
            records = footfall_result.all()

            historical_data = [
                {
                    "timestamp": row.timestamp,
                    "count": row.entries_count,
                    "zone_id": str(row.zone_id) if row.zone_id else None,
                }
                for row in records
            ]

            # Build baseline
            baseline = detector.build_baseline(historical_data, days=days)

            # Upsert into database
            existing_q = select(AnomalyBaseline).where(
                AnomalyBaseline.org_id == org_id,
                AnomalyBaseline.camera_id == camera.id,
                AnomalyBaseline.zone_id.is_(None),
            )
            existing = (await db.execute(existing_q)).scalar_one_or_none()

            hourly_profile = {
                "hourly_counts": baseline.hourly_counts.tolist(),
                "hourly_std": baseline.hourly_std.tolist(),
                "zone_baselines": baseline.zone_baselines,
            }
            dow_profile = {
                "factors": baseline.day_of_week_factors.tolist(),
            }

            if existing:
                existing.hourly_profile = hourly_profile
                existing.day_of_week_profile = dow_profile
                existing.sample_count = baseline.sample_count
                existing.is_stale = False
                db.add(existing)
            else:
                new_baseline = AnomalyBaseline(
                    org_id=org_id,
                    camera_id=camera.id,
                    zone_id=None,
                    baseline_type="footfall",
                    hourly_profile=hourly_profile,
                    day_of_week_profile=dow_profile,
                    sample_count=baseline.sample_count,
                    is_stale=False,
                )
                db.add(new_baseline)

            # Save to Redis
            if redis_client:
                await detector.save_baseline_to_redis(str(camera.id), None, baseline)

            results["baselines_created"] += 1
            results["cameras_processed"] += 1

            logger.info(
                "anomaly_service.baseline_rebuilt",
                camera_id=str(camera.id),
                camera_name=camera.name,
                sample_count=baseline.sample_count,
            )

        except Exception as exc:
            logger.error(
                "anomaly_service.baseline_rebuild_error",
                camera_id=str(camera.id),
                error=str(exc),
            )
            results["errors"].append({
                "camera_id": str(camera.id),
                "error": str(exc),
            })

    await db.flush()

    return results


async def configure_sensitivity(
    org_id: uuid.UUID,
    camera_id: Optional[uuid.UUID],
    sensitivity: str,
    custom_overrides: Optional[dict[str, float]] = None,
    redis_client: Any = None,
) -> dict[str, Any]:
    """Adjust detection sensitivity thresholds for an org or camera.

    Stores the resolved thresholds in Redis for the anomaly detector
    to pick up during the next detection cycle.

    Args:
        org_id: Organization scope.
        camera_id: Optional specific camera. If None, applies org-wide.
        sensitivity: Preset name (low, medium, high).
        custom_overrides: Optional custom threshold overrides.
        redis_client: Async Redis client.

    Returns:
        Dict with the resolved configuration.
    """
    preset = SENSITIVITY_PRESETS.get(sensitivity, SENSITIVITY_PRESETS["medium"]).copy()

    if custom_overrides:
        if "warning_sigma" in custom_overrides:
            preset["warning_sigma"] = custom_overrides["warning_sigma"]
        if "critical_sigma" in custom_overrides:
            preset["critical_sigma"] = custom_overrides["critical_sigma"]
        if "min_confidence" in custom_overrides:
            preset["min_confidence"] = custom_overrides["min_confidence"]

    config = {
        "sensitivity": sensitivity,
        "warning_sigma": preset["warning_sigma"],
        "critical_sigma": preset["critical_sigma"],
        "min_confidence": preset["min_confidence"],
        "updated_at": datetime.now(timezone.utc).isoformat(),
    }

    if redis_client:
        if camera_id:
            key = f"visionai:anomaly:sensitivity:{org_id}:{camera_id}"
        else:
            key = f"visionai:anomaly:sensitivity:{org_id}:default"

        await redis_client.set(key, json.dumps(config), ex=86400 * 30)

        logger.info(
            "anomaly_service.sensitivity_configured",
            org_id=str(org_id),
            camera_id=str(camera_id) if camera_id else "org-wide",
            sensitivity=sensitivity,
        )

    return config
