"""Analytics service for footfall, heatmaps, dwell times, and dashboards.

Provides query interfaces for aggregated analytics data including
footfall counting, occupancy estimation, heatmap generation, dwell
time analysis, emotion analytics, pattern detection, and dashboard
summary statistics.
"""

from __future__ import annotations

import uuid
from datetime import datetime, timedelta, timezone
from typing import Any, Optional

import structlog
from sqlalchemy import func, select
from sqlalchemy.ext.asyncio import AsyncSession

from app.models.alert import Alert, AlertStatus
from app.models.analytics import (
    AggregationPeriod,
    DwellRecord,
    FootfallRecord,
    HeatmapRecord,
)
from app.models.camera import Camera
from app.models.face import FaceEvent
from app.models.person import Person
from app.models.recording import Recording

logger = structlog.stdlib.get_logger(__name__)


# ── Footfall Analytics ───────────────────────────────────────────────────


async def get_footfall(
    db: AsyncSession,
    org_id: uuid.UUID,
    camera_id: Optional[uuid.UUID] = None,
    zone_id: Optional[uuid.UUID] = None,
    period: str = "hourly",
    start_date: Optional[datetime] = None,
    end_date: Optional[datetime] = None,
    page: int = 1,
    page_size: int = 100,
) -> tuple[list[FootfallRecord], int]:
    """Retrieve footfall records with filtering and pagination.

    Args:
        db: Async database session.
        org_id: Organization scope.
        camera_id: Optional camera filter.
        zone_id: Optional zone filter.
        period: Aggregation period ('hourly', 'daily', 'weekly').
        start_date: Optional start of time window.
        end_date: Optional end of time window.
        page: Page number.
        page_size: Items per page.

    Returns:
        Tuple of (footfall_records, total_count).
    """
    query = (
        select(FootfallRecord)
        .join(Camera, FootfallRecord.camera_id == Camera.id)
        .where(Camera.org_id == org_id)
    )

    if camera_id:
        query = query.where(FootfallRecord.camera_id == camera_id)
    if zone_id:
        query = query.where(FootfallRecord.zone_id == zone_id)

    try:
        agg_period = AggregationPeriod(period)
        query = query.where(FootfallRecord.period == agg_period)
    except ValueError:
        pass

    if start_date:
        query = query.where(FootfallRecord.timestamp >= start_date)
    if end_date:
        query = query.where(FootfallRecord.timestamp <= end_date)

    count_q = select(func.count()).select_from(query.subquery())
    total_result = await db.execute(count_q)
    total = total_result.scalar() or 0

    offset = (max(1, page) - 1) * page_size
    query = query.order_by(FootfallRecord.timestamp.desc()).offset(offset).limit(page_size)
    result = await db.execute(query)
    records = list(result.scalars().all())

    return records, total


async def get_footfall_summary(
    db: AsyncSession,
    org_id: uuid.UUID,
    camera_id: Optional[uuid.UUID] = None,
    start_date: Optional[datetime] = None,
    end_date: Optional[datetime] = None,
) -> dict[str, Any]:
    """Compute a summary of footfall data for a time range.

    Args:
        db: Async database session.
        org_id: Organization scope.
        camera_id: Optional camera filter.
        start_date: Optional start filter.
        end_date: Optional end filter.

    Returns:
        Dict with total_entries, total_exits, avg_occupancy, peak_hour,
        and daily_breakdown.
    """
    base = (
        select(FootfallRecord)
        .join(Camera, FootfallRecord.camera_id == Camera.id)
        .where(Camera.org_id == org_id)
    )

    if camera_id:
        base = base.where(FootfallRecord.camera_id == camera_id)
    if start_date:
        base = base.where(FootfallRecord.timestamp >= start_date)
    if end_date:
        base = base.where(FootfallRecord.timestamp <= end_date)

    totals_q = select(
        func.coalesce(func.sum(FootfallRecord.entries_count), 0),
        func.coalesce(func.sum(FootfallRecord.exits_count), 0),
        func.coalesce(func.avg(FootfallRecord.occupancy_estimate), 0),
    ).select_from(base.subquery())

    # Build the query for totals using the base filters directly
    total_entries_q = (
        select(func.coalesce(func.sum(FootfallRecord.entries_count), 0))
        .join(Camera, FootfallRecord.camera_id == Camera.id)
        .where(Camera.org_id == org_id)
    )
    total_exits_q = (
        select(func.coalesce(func.sum(FootfallRecord.exits_count), 0))
        .join(Camera, FootfallRecord.camera_id == Camera.id)
        .where(Camera.org_id == org_id)
    )
    avg_occ_q = (
        select(func.coalesce(func.avg(FootfallRecord.occupancy_estimate), 0))
        .join(Camera, FootfallRecord.camera_id == Camera.id)
        .where(Camera.org_id == org_id)
    )

    if camera_id:
        total_entries_q = total_entries_q.where(FootfallRecord.camera_id == camera_id)
        total_exits_q = total_exits_q.where(FootfallRecord.camera_id == camera_id)
        avg_occ_q = avg_occ_q.where(FootfallRecord.camera_id == camera_id)
    if start_date:
        total_entries_q = total_entries_q.where(FootfallRecord.timestamp >= start_date)
        total_exits_q = total_exits_q.where(FootfallRecord.timestamp >= start_date)
        avg_occ_q = avg_occ_q.where(FootfallRecord.timestamp >= start_date)
    if end_date:
        total_entries_q = total_entries_q.where(FootfallRecord.timestamp <= end_date)
        total_exits_q = total_exits_q.where(FootfallRecord.timestamp <= end_date)
        avg_occ_q = avg_occ_q.where(FootfallRecord.timestamp <= end_date)

    entries_result = await db.execute(total_entries_q)
    total_entries = entries_result.scalar() or 0

    exits_result = await db.execute(total_exits_q)
    total_exits = exits_result.scalar() or 0

    occ_result = await db.execute(avg_occ_q)
    avg_occupancy = float(occ_result.scalar() or 0)

    # Find peak hour
    peak_q = (
        select(
            func.extract("hour", FootfallRecord.timestamp).label("hour"),
            func.sum(FootfallRecord.entries_count).label("total"),
        )
        .join(Camera, FootfallRecord.camera_id == Camera.id)
        .where(Camera.org_id == org_id)
    )
    if camera_id:
        peak_q = peak_q.where(FootfallRecord.camera_id == camera_id)
    if start_date:
        peak_q = peak_q.where(FootfallRecord.timestamp >= start_date)
    if end_date:
        peak_q = peak_q.where(FootfallRecord.timestamp <= end_date)

    peak_q = peak_q.group_by("hour").order_by(func.sum(FootfallRecord.entries_count).desc()).limit(1)
    peak_result = await db.execute(peak_q)
    peak_row = peak_result.first()
    peak_hour = int(peak_row[0]) if peak_row else None

    return {
        "total_entries": int(total_entries),
        "total_exits": int(total_exits),
        "avg_occupancy": round(avg_occupancy, 1),
        "peak_hour": peak_hour,
    }


# ── Heatmap ──────────────────────────────────────────────────────────────


async def get_heatmap_url(
    db: AsyncSession,
    org_id: uuid.UUID,
    camera_id: uuid.UUID,
    start_time: Optional[datetime] = None,
    end_time: Optional[datetime] = None,
) -> Optional[str]:
    """Get the URL of the most recent heatmap image for a camera.

    Args:
        db: Async database session.
        org_id: Organization scope.
        camera_id: Camera to get heatmap for.
        start_time: Optional start time filter.
        end_time: Optional end time filter.

    Returns:
        Path/URL of the heatmap image, or None if not found.
    """
    query = (
        select(HeatmapRecord)
        .join(Camera, HeatmapRecord.camera_id == Camera.id)
        .where(
            Camera.org_id == org_id,
            HeatmapRecord.camera_id == camera_id,
        )
    )

    if start_time:
        query = query.where(HeatmapRecord.start_time >= start_time)
    if end_time:
        query = query.where(HeatmapRecord.end_time <= end_time)

    query = query.order_by(HeatmapRecord.created_at.desc()).limit(1)
    result = await db.execute(query)
    record = result.scalar_one_or_none()

    return record.image_path if record else None


# ── Dwell Time ───────────────────────────────────────────────────────────


async def get_dwell_times(
    db: AsyncSession,
    org_id: uuid.UUID,
    camera_id: Optional[uuid.UUID] = None,
    zone_id: Optional[uuid.UUID] = None,
    start_date: Optional[datetime] = None,
    end_date: Optional[datetime] = None,
    page: int = 1,
    page_size: int = 50,
) -> tuple[list[DwellRecord], int]:
    """Retrieve dwell time records with optional filtering.

    Args:
        db: Async database session.
        org_id: Organization scope.
        camera_id: Optional camera filter.
        zone_id: Optional zone filter.
        start_date: Optional start filter.
        end_date: Optional end filter.
        page: Page number.
        page_size: Items per page.

    Returns:
        Tuple of (dwell_records, total_count).
    """
    query = (
        select(DwellRecord)
        .join(Camera, DwellRecord.camera_id == Camera.id)
        .where(Camera.org_id == org_id)
    )

    if camera_id:
        query = query.where(DwellRecord.camera_id == camera_id)
    if zone_id:
        query = query.where(DwellRecord.zone_id == zone_id)
    if start_date:
        query = query.where(DwellRecord.enter_time >= start_date)
    if end_date:
        query = query.where(DwellRecord.enter_time <= end_date)

    count_q = select(func.count()).select_from(query.subquery())
    total_result = await db.execute(count_q)
    total = total_result.scalar() or 0

    offset = (max(1, page) - 1) * page_size
    query = query.order_by(DwellRecord.enter_time.desc()).offset(offset).limit(page_size)
    result = await db.execute(query)
    records = list(result.scalars().all())

    return records, total


# ── Emotion Analytics ────────────────────────────────────────────────────


async def get_emotion_analytics(
    db: AsyncSession,
    org_id: uuid.UUID,
    camera_id: Optional[uuid.UUID] = None,
    start_date: Optional[datetime] = None,
    end_date: Optional[datetime] = None,
) -> dict[str, Any]:
    """Get emotion distribution from face events.

    Args:
        db: Async database session.
        org_id: Organization scope.
        camera_id: Optional camera filter.
        start_date: Optional start filter.
        end_date: Optional end filter.

    Returns:
        Dict with emotion distribution, total faces, and avg confidence.
    """
    query = (
        select(FaceEvent.emotion, func.count().label("count"))
        .join(Camera, FaceEvent.camera_id == Camera.id)
        .where(
            Camera.org_id == org_id,
            FaceEvent.emotion.isnot(None),
        )
    )

    if camera_id:
        query = query.where(FaceEvent.camera_id == camera_id)
    if start_date:
        query = query.where(FaceEvent.timestamp >= start_date)
    if end_date:
        query = query.where(FaceEvent.timestamp <= end_date)

    query = query.group_by(FaceEvent.emotion)
    result = await db.execute(query)
    rows = result.all()

    distribution = {row[0]: row[1] for row in rows}
    total = sum(distribution.values())

    return {
        "distribution": distribution,
        "total_faces": total,
        "dominant_emotion": max(distribution, key=distribution.get) if distribution else None,
    }


# ── Occupancy ────────────────────────────────────────────────────────────


async def get_occupancy(
    camera_id: uuid.UUID,
) -> dict[str, Any]:
    """Get real-time occupancy estimate from Redis.

    The occupancy count is maintained by the detection pipeline and
    stored in Redis for low-latency access.

    Args:
        camera_id: Camera to get occupancy for.

    Returns:
        Dict with camera_id, current_count, and timestamp.
    """
    try:
        from app.dependencies import get_redis

        redis = await get_redis()
        key = f"occupancy:{camera_id}"
        data = await redis.hgetall(key)

        if data:
            return {
                "camera_id": str(camera_id),
                "current_count": int(data.get("count", 0)),
                "timestamp": data.get("timestamp"),
            }
    except Exception as exc:
        logger.debug("Redis occupancy fetch failed", error=str(exc))

    return {
        "camera_id": str(camera_id),
        "current_count": 0,
        "timestamp": None,
    }


# ── Dashboard Stats ──────────────────────────────────────────────────────


async def get_dashboard_stats(
    db: AsyncSession,
    org_id: uuid.UUID,
) -> dict[str, Any]:
    """Compute top-level dashboard statistics for an organization.

    Returns:
        Dict with camera counts, alert counts, person count,
        recording stats, and recent activity.
    """
    # Camera counts
    camera_total_q = select(func.count()).where(Camera.org_id == org_id)
    camera_online_q = select(func.count()).where(
        Camera.org_id == org_id, Camera.is_online.is_(True)
    )

    camera_total = (await db.execute(camera_total_q)).scalar() or 0
    camera_online = (await db.execute(camera_online_q)).scalar() or 0

    # Alert counts (last 24h)
    now = datetime.now(timezone.utc)
    day_ago = now - timedelta(hours=24)

    alerts_today_q = select(func.count()).where(
        Alert.org_id == org_id,
        Alert.created_at >= day_ago,
    )
    alerts_new_q = select(func.count()).where(
        Alert.org_id == org_id,
        Alert.status == AlertStatus.NEW,
    )

    alerts_today = (await db.execute(alerts_today_q)).scalar() or 0
    alerts_unresolved = (await db.execute(alerts_new_q)).scalar() or 0

    # Person count
    person_count_q = select(func.count()).where(
        Person.org_id == org_id, Person.is_active.is_(True)
    )
    person_count = (await db.execute(person_count_q)).scalar() or 0

    # Recording count
    recording_count_q = select(func.count()).where(Recording.org_id == org_id)
    recording_count = (await db.execute(recording_count_q)).scalar() or 0

    # Total footfall today
    footfall_today_q = (
        select(func.coalesce(func.sum(FootfallRecord.entries_count), 0))
        .join(Camera, FootfallRecord.camera_id == Camera.id)
        .where(
            Camera.org_id == org_id,
            FootfallRecord.timestamp >= day_ago,
        )
    )
    footfall_today = (await db.execute(footfall_today_q)).scalar() or 0

    return {
        "cameras": {
            "total": camera_total,
            "online": camera_online,
            "offline": camera_total - camera_online,
        },
        "alerts": {
            "today": alerts_today,
            "unresolved": alerts_unresolved,
        },
        "persons": {
            "enrolled": person_count,
        },
        "recordings": {
            "total": recording_count,
        },
        "footfall_today": int(footfall_today),
    }


# ── Pattern Analytics ────────────────────────────────────────────────────


async def get_patterns(
    db: AsyncSession,
    org_id: uuid.UUID,
    camera_id: Optional[uuid.UUID] = None,
    days: int = 7,
) -> dict[str, Any]:
    """Analyze footfall patterns over recent days.

    Computes hourly averages, day-of-week patterns, and trend
    direction (increasing/decreasing/stable).

    Args:
        db: Async database session.
        org_id: Organization scope.
        camera_id: Optional camera filter.
        days: Number of days to analyze.

    Returns:
        Dict with hourly_averages, day_of_week_pattern, trend.
    """
    now = datetime.now(timezone.utc)
    start = now - timedelta(days=days)

    base_filter = [
        Camera.org_id == org_id,
        FootfallRecord.timestamp >= start,
    ]
    if camera_id:
        base_filter.append(FootfallRecord.camera_id == camera_id)

    # Hourly averages
    hourly_q = (
        select(
            func.extract("hour", FootfallRecord.timestamp).label("hour"),
            func.avg(FootfallRecord.entries_count).label("avg_entries"),
        )
        .join(Camera, FootfallRecord.camera_id == Camera.id)
        .where(*base_filter)
        .group_by("hour")
        .order_by("hour")
    )
    hourly_result = await db.execute(hourly_q)
    hourly_averages = {int(row[0]): round(float(row[1]), 1) for row in hourly_result.all()}

    # Day of week pattern (0=Sunday in extract)
    dow_q = (
        select(
            func.extract("dow", FootfallRecord.timestamp).label("dow"),
            func.avg(FootfallRecord.entries_count).label("avg_entries"),
        )
        .join(Camera, FootfallRecord.camera_id == Camera.id)
        .where(*base_filter)
        .group_by("dow")
        .order_by("dow")
    )
    dow_result = await db.execute(dow_q)
    day_names = ["Sunday", "Monday", "Tuesday", "Wednesday", "Thursday", "Friday", "Saturday"]
    day_of_week = {}
    for row in dow_result.all():
        idx = int(row[0])
        if 0 <= idx < 7:
            day_of_week[day_names[idx]] = round(float(row[1]), 1)

    # Trend: compare first half vs second half
    midpoint = start + timedelta(days=days / 2)
    first_half_q = (
        select(func.coalesce(func.avg(FootfallRecord.entries_count), 0))
        .join(Camera, FootfallRecord.camera_id == Camera.id)
        .where(
            *base_filter,
            FootfallRecord.timestamp < midpoint,
        )
    )
    second_half_q = (
        select(func.coalesce(func.avg(FootfallRecord.entries_count), 0))
        .join(Camera, FootfallRecord.camera_id == Camera.id)
        .where(
            *base_filter,
            FootfallRecord.timestamp >= midpoint,
        )
    )

    first_avg = float((await db.execute(first_half_q)).scalar() or 0)
    second_avg = float((await db.execute(second_half_q)).scalar() or 0)

    if first_avg == 0:
        trend = "stable"
    else:
        change = (second_avg - first_avg) / first_avg
        if change > 0.1:
            trend = "increasing"
        elif change < -0.1:
            trend = "decreasing"
        else:
            trend = "stable"

    return {
        "hourly_averages": hourly_averages,
        "day_of_week_pattern": day_of_week,
        "trend": trend,
        "period_days": days,
        "first_half_avg": round(first_avg, 1),
        "second_half_avg": round(second_avg, 1),
    }
