"""
VisionAI Analytics Aggregation Celery Tasks.

Handles hourly footfall aggregation, heatmap generation, dwell time
computation, daily attendance aggregation, and statistical pattern analysis.
All tasks interact with the database for reading raw events and storing
aggregated results.
"""

from __future__ import annotations

import asyncio
import io
import json
import uuid
from collections import defaultdict
from datetime import datetime, timedelta, timezone
from typing import Any

import numpy as np
import redis
import structlog
from sqlalchemy import delete, func, select, text

from app.config import get_settings
from app.workers.celery_app import celery_app

logger = structlog.stdlib.get_logger(__name__)
settings = get_settings()

_redis = redis.Redis.from_url(settings.REDIS_URL, decode_responses=True)


def _run_async(coro):
    """Run an async coroutine from a synchronous Celery task context."""
    loop = asyncio.new_event_loop()
    try:
        return loop.run_until_complete(coro)
    finally:
        loop.close()


@celery_app.task(
    name="app.workers.analytics_tasks.aggregate_footfall_hourly",
    bind=True,
    max_retries=3,
    default_retry_delay=120,
    queue="analytics",
)
def aggregate_footfall_hourly(self) -> dict[str, Any]:
    """Aggregate raw detection events into hourly footfall records.

    Queries all detection events from the previous hour, groups them by
    camera and zone, computes entry/exit counts, and upserts the results
    into the footfall_records table.

    Returns:
        dict summarizing the aggregation results.
    """
    log = logger.bind(task_id=self.request.id)
    log.info("Starting hourly footfall aggregation")

    try:
        now = datetime.now(timezone.utc)
        hour_start = now.replace(minute=0, second=0, microsecond=0) - timedelta(hours=1)
        hour_end = hour_start + timedelta(hours=1)

        async def _aggregate():
            from app.database import get_db_context

            async with get_db_context() as session:
                # Query raw detection events for the last hour
                # Uses a direct SQL query for aggregation efficiency
                query = text("""
                    WITH detection_counts AS (
                        SELECT
                            camera_id,
                            zone_id,
                            COUNT(*) FILTER (WHERE metadata_json->>'direction' = 'entry') AS entry_count,
                            COUNT(*) FILTER (WHERE metadata_json->>'direction' = 'exit') AS exit_count,
                            COUNT(*) AS total_count,
                            COUNT(DISTINCT metadata_json->>'track_id') AS unique_objects
                        FROM alerts
                        WHERE created_at >= :hour_start
                          AND created_at < :hour_end
                          AND alert_type IN ('line_crossing', 'intrusion_detection')
                        GROUP BY camera_id, zone_id
                    )
                    SELECT * FROM detection_counts
                """)

                result = await session.execute(
                    query,
                    {"hour_start": hour_start, "hour_end": hour_end},
                )
                rows = result.fetchall()

                records_created = 0
                for row in rows:
                    # Upsert footfall record
                    upsert_query = text("""
                        INSERT INTO footfall_records (
                            id, camera_id, zone_id, hour_start,
                            entry_count, exit_count, total_count,
                            unique_objects, created_at, updated_at
                        ) VALUES (
                            :id, :camera_id, :zone_id, :hour_start,
                            :entry_count, :exit_count, :total_count,
                            :unique_objects, NOW(), NOW()
                        )
                        ON CONFLICT (camera_id, zone_id, hour_start)
                        DO UPDATE SET
                            entry_count = EXCLUDED.entry_count,
                            exit_count = EXCLUDED.exit_count,
                            total_count = EXCLUDED.total_count,
                            unique_objects = EXCLUDED.unique_objects,
                            updated_at = NOW()
                    """)

                    await session.execute(
                        upsert_query,
                        {
                            "id": str(uuid.uuid4()),
                            "camera_id": str(row.camera_id),
                            "zone_id": str(row.zone_id) if row.zone_id else None,
                            "hour_start": hour_start,
                            "entry_count": row.entry_count or 0,
                            "exit_count": row.exit_count or 0,
                            "total_count": row.total_count or 0,
                            "unique_objects": row.unique_objects or 0,
                        },
                    )
                    records_created += 1

                return records_created

        records_created = _run_async(_aggregate())

        # Cache the latest hourly totals in Redis for dashboard
        _redis.set(
            "visionai:analytics:footfall:last_aggregation",
            json.dumps({
                "hour_start": hour_start.isoformat(),
                "hour_end": hour_end.isoformat(),
                "records_created": records_created,
                "aggregated_at": now.isoformat(),
            }),
            ex=7200,
        )

        log.info(
            "Footfall aggregation completed",
            hour_start=hour_start.isoformat(),
            records_created=records_created,
        )

        return {
            "status": "completed",
            "hour_start": hour_start.isoformat(),
            "hour_end": hour_end.isoformat(),
            "records_created": records_created,
        }

    except Exception as exc:
        log.error("Footfall aggregation failed", error=str(exc))
        raise self.retry(exc=exc)


@celery_app.task(
    name="app.workers.analytics_tasks.generate_heatmap",
    bind=True,
    max_retries=2,
    default_retry_delay=60,
    soft_time_limit=300,
    time_limit=600,
    queue="analytics",
)
def generate_heatmap(
    self, camera_id: str, start_time: str, end_time: str
) -> dict[str, Any]:
    """Generate and store a heatmap image for a camera over a time range.

    Collects all detection bounding box center points within the time range,
    bins them into a spatial grid, applies Gaussian smoothing, and renders
    the heatmap as a PNG image stored in MinIO.

    Args:
        camera_id: UUID of the camera.
        start_time: ISO-8601 start of the time range.
        end_time: ISO-8601 end of the time range.

    Returns:
        dict with the heatmap image URL and metadata.
    """
    log = logger.bind(camera_id=camera_id, task_id=self.request.id)
    log.info("Generating heatmap", start_time=start_time, end_time=end_time)

    try:
        start_dt = datetime.fromisoformat(start_time)
        end_dt = datetime.fromisoformat(end_time)

        async def _collect_points():
            from app.database import get_db_context

            async with get_db_context() as session:
                query = text("""
                    SELECT
                        metadata_json->'bbox'->>'cx' AS cx,
                        metadata_json->'bbox'->>'cy' AS cy
                    FROM alerts
                    WHERE camera_id = :camera_id
                      AND created_at >= :start_time
                      AND created_at <= :end_time
                      AND metadata_json->'bbox' IS NOT NULL
                """)
                result = await session.execute(
                    query,
                    {
                        "camera_id": camera_id,
                        "start_time": start_dt,
                        "end_time": end_dt,
                    },
                )
                points = []
                for row in result.fetchall():
                    try:
                        cx = float(row.cx)
                        cy = float(row.cy)
                        points.append((cx, cy))
                    except (TypeError, ValueError):
                        continue
                return points

        points = _run_async(_collect_points())

        if not points:
            log.warning("No detection points found for heatmap")
            return {
                "camera_id": camera_id,
                "status": "empty",
                "message": "No detection points in time range",
                "point_count": 0,
            }

        # Generate heatmap array
        width, height = 1920, 1080
        grid_w, grid_h = 192, 108  # 10x downscaled grid
        heatmap = np.zeros((grid_h, grid_w), dtype=np.float64)

        for cx, cy in points:
            gx = int(cx * grid_w / width)
            gy = int(cy * grid_h / height)
            gx = max(0, min(gx, grid_w - 1))
            gy = max(0, min(gy, grid_h - 1))
            heatmap[gy, gx] += 1.0

        # Apply Gaussian blur for smooth heatmap
        from scipy.ndimage import gaussian_filter
        heatmap = gaussian_filter(heatmap, sigma=3.0)

        # Normalize to 0-255
        if heatmap.max() > 0:
            heatmap = (heatmap / heatmap.max() * 255).astype(np.uint8)
        else:
            heatmap = heatmap.astype(np.uint8)

        # Create colored heatmap image
        from PIL import Image

        # Apply colormap: blue -> green -> yellow -> red
        colored = np.zeros((grid_h, grid_w, 4), dtype=np.uint8)
        for y in range(grid_h):
            for x in range(grid_w):
                val = heatmap[y, x]
                if val == 0:
                    colored[y, x] = [0, 0, 0, 0]  # Transparent
                elif val < 64:
                    colored[y, x] = [0, 0, int(val * 4), int(val * 2)]
                elif val < 128:
                    colored[y, x] = [0, int((val - 64) * 4), 255, 180]
                elif val < 192:
                    colored[y, x] = [int((val - 128) * 4), 255, int(255 - (val - 128) * 4), 200]
                else:
                    colored[y, x] = [255, int(255 - (val - 192) * 4), 0, 220]

        # Upscale to full resolution
        img = Image.fromarray(colored, mode="RGBA")
        img = img.resize((width, height), Image.BILINEAR)

        # Save to bytes
        buffer = io.BytesIO()
        img.save(buffer, format="PNG", optimize=True)
        buffer.seek(0)
        image_bytes = buffer.getvalue()

        # Upload to MinIO
        from minio import Minio

        minio_client = Minio(
            settings.MINIO_ENDPOINT,
            access_key=settings.MINIO_ACCESS_KEY,
            secret_key=settings.MINIO_SECRET_KEY,
            secure=settings.MINIO_SECURE,
        )

        bucket = settings.MINIO_BUCKET_SNAPSHOTS
        if not minio_client.bucket_exists(bucket):
            minio_client.make_bucket(bucket)

        object_name = (
            f"heatmaps/{camera_id}/"
            f"{start_dt.strftime('%Y%m%d_%H%M')}-{end_dt.strftime('%Y%m%d_%H%M')}.png"
        )

        minio_client.put_object(
            bucket,
            object_name,
            io.BytesIO(image_bytes),
            length=len(image_bytes),
            content_type="image/png",
        )

        # Store heatmap record in database
        async def _store_record():
            from app.database import get_db_context

            async with get_db_context() as session:
                await session.execute(
                    text("""
                        INSERT INTO heatmap_records (
                            id, camera_id, start_time, end_time,
                            image_path, point_count, created_at, updated_at
                        ) VALUES (
                            :id, :camera_id, :start_time, :end_time,
                            :image_path, :point_count, NOW(), NOW()
                        )
                    """),
                    {
                        "id": str(uuid.uuid4()),
                        "camera_id": camera_id,
                        "start_time": start_dt,
                        "end_time": end_dt,
                        "image_path": f"{bucket}/{object_name}",
                        "point_count": len(points),
                    },
                )

        _run_async(_store_record())

        image_url = f"{bucket}/{object_name}"
        log.info("Heatmap generated", point_count=len(points), image_url=image_url)

        return {
            "camera_id": camera_id,
            "status": "completed",
            "image_url": image_url,
            "point_count": len(points),
            "start_time": start_time,
            "end_time": end_time,
        }

    except Exception as exc:
        log.error("Heatmap generation failed", error=str(exc))
        raise self.retry(exc=exc)


@celery_app.task(
    name="app.workers.analytics_tasks.compute_dwell_times",
    bind=True,
    max_retries=2,
    default_retry_delay=60,
    queue="analytics",
)
def compute_dwell_times(self, camera_id: str, zone_id: str) -> dict[str, Any]:
    """Calculate dwell times from tracking data for a specific camera/zone.

    Analyzes object tracking records to compute how long each tracked
    entity stayed within the specified zone. Results are stored in the
    dwell_records table.

    Args:
        camera_id: UUID of the camera.
        zone_id: UUID of the zone.

    Returns:
        dict with computed dwell time statistics.
    """
    log = logger.bind(camera_id=camera_id, zone_id=zone_id, task_id=self.request.id)
    log.info("Computing dwell times")

    try:
        now = datetime.now(timezone.utc)
        lookback = now - timedelta(hours=1)

        async def _compute():
            from app.database import get_db_context

            async with get_db_context() as session:
                # Get tracking data for the zone in the last hour
                query = text("""
                    SELECT
                        metadata_json->>'track_id' AS track_id,
                        MIN(created_at) AS first_seen,
                        MAX(created_at) AS last_seen,
                        COUNT(*) AS detection_count
                    FROM alerts
                    WHERE camera_id = :camera_id
                      AND zone_id = :zone_id
                      AND created_at >= :lookback
                      AND metadata_json->>'track_id' IS NOT NULL
                    GROUP BY metadata_json->>'track_id'
                    HAVING COUNT(*) > 1
                """)

                result = await session.execute(
                    query,
                    {
                        "camera_id": camera_id,
                        "zone_id": zone_id,
                        "lookback": lookback,
                    },
                )
                tracks = result.fetchall()

                dwell_times = []
                for track in tracks:
                    dwell_seconds = (track.last_seen - track.first_seen).total_seconds()
                    dwell_times.append(dwell_seconds)

                    # Store individual dwell record
                    await session.execute(
                        text("""
                            INSERT INTO dwell_records (
                                id, camera_id, zone_id, track_id,
                                enter_time, exit_time, dwell_seconds,
                                created_at, updated_at
                            ) VALUES (
                                :id, :camera_id, :zone_id, :track_id,
                                :enter_time, :exit_time, :dwell_seconds,
                                NOW(), NOW()
                            )
                            ON CONFLICT (camera_id, zone_id, track_id, enter_time)
                            DO UPDATE SET
                                exit_time = EXCLUDED.exit_time,
                                dwell_seconds = EXCLUDED.dwell_seconds,
                                updated_at = NOW()
                        """),
                        {
                            "id": str(uuid.uuid4()),
                            "camera_id": camera_id,
                            "zone_id": zone_id,
                            "track_id": track.track_id,
                            "enter_time": track.first_seen,
                            "exit_time": track.last_seen,
                            "dwell_seconds": dwell_seconds,
                        },
                    )

                return dwell_times

        dwell_times = _run_async(_compute())

        if dwell_times:
            stats = {
                "count": len(dwell_times),
                "min_seconds": round(min(dwell_times), 2),
                "max_seconds": round(max(dwell_times), 2),
                "avg_seconds": round(sum(dwell_times) / len(dwell_times), 2),
                "median_seconds": round(float(np.median(dwell_times)), 2),
                "p95_seconds": round(float(np.percentile(dwell_times, 95)), 2),
            }
        else:
            stats = {
                "count": 0,
                "min_seconds": 0,
                "max_seconds": 0,
                "avg_seconds": 0,
                "median_seconds": 0,
                "p95_seconds": 0,
            }

        # Cache stats in Redis
        cache_key = f"visionai:analytics:dwell:{camera_id}:{zone_id}"
        _redis.set(cache_key, json.dumps(stats), ex=3600)

        log.info("Dwell times computed", **stats)

        return {
            "camera_id": camera_id,
            "zone_id": zone_id,
            "status": "completed",
            "statistics": stats,
        }

    except Exception as exc:
        log.error("Dwell time computation failed", error=str(exc))
        raise self.retry(exc=exc)


@celery_app.task(
    name="app.workers.analytics_tasks.aggregate_attendance_daily",
    bind=True,
    max_retries=3,
    default_retry_delay=120,
    queue="analytics",
)
def aggregate_attendance_daily(self) -> dict[str, Any]:
    """Compute daily attendance records from face recognition events.

    Processes face recognition events from the previous day, determines
    first-seen and last-seen times for each recognized person per camera,
    and creates attendance log records.

    Returns:
        dict summarizing attendance records created.
    """
    log = logger.bind(task_id=self.request.id)
    log.info("Starting daily attendance aggregation")

    try:
        now = datetime.now(timezone.utc)
        yesterday_start = (now - timedelta(days=1)).replace(
            hour=0, minute=0, second=0, microsecond=0
        )
        yesterday_end = yesterday_start + timedelta(days=1)

        async def _aggregate():
            from app.database import get_db_context

            async with get_db_context() as session:
                query = text("""
                    SELECT
                        a.camera_id,
                        a.org_id,
                        a.metadata_json->>'person_id' AS person_id,
                        a.metadata_json->>'person_name' AS person_name,
                        MIN(a.created_at) AS first_seen,
                        MAX(a.created_at) AS last_seen,
                        COUNT(*) AS event_count
                    FROM alerts a
                    WHERE a.alert_type = 'face_recognized'
                      AND a.created_at >= :start_time
                      AND a.created_at < :end_time
                      AND a.metadata_json->>'person_id' IS NOT NULL
                    GROUP BY a.camera_id, a.org_id,
                             a.metadata_json->>'person_id',
                             a.metadata_json->>'person_name'
                """)

                result = await session.execute(
                    query,
                    {"start_time": yesterday_start, "end_time": yesterday_end},
                )
                rows = result.fetchall()
                records_created = 0

                for row in rows:
                    first_seen = row.first_seen
                    last_seen = row.last_seen
                    duration_seconds = (last_seen - first_seen).total_seconds()

                    await session.execute(
                        text("""
                            INSERT INTO attendance_logs (
                                id, org_id, camera_id, person_id,
                                person_name, date, first_seen, last_seen,
                                duration_seconds, event_count,
                                created_at, updated_at
                            ) VALUES (
                                :id, :org_id, :camera_id, :person_id,
                                :person_name, :date, :first_seen, :last_seen,
                                :duration_seconds, :event_count,
                                NOW(), NOW()
                            )
                            ON CONFLICT (org_id, person_id, date)
                            DO UPDATE SET
                                first_seen = LEAST(attendance_logs.first_seen, EXCLUDED.first_seen),
                                last_seen = GREATEST(attendance_logs.last_seen, EXCLUDED.last_seen),
                                duration_seconds = EXCLUDED.duration_seconds,
                                event_count = EXCLUDED.event_count,
                                updated_at = NOW()
                        """),
                        {
                            "id": str(uuid.uuid4()),
                            "org_id": str(row.org_id),
                            "camera_id": str(row.camera_id),
                            "person_id": row.person_id,
                            "person_name": row.person_name or "Unknown",
                            "date": yesterday_start.date(),
                            "first_seen": first_seen,
                            "last_seen": last_seen,
                            "duration_seconds": duration_seconds,
                            "event_count": row.event_count,
                        },
                    )
                    records_created += 1

                return records_created

        records_created = _run_async(_aggregate())

        log.info(
            "Attendance aggregation completed",
            date=yesterday_start.date().isoformat(),
            records_created=records_created,
        )

        return {
            "status": "completed",
            "date": yesterday_start.date().isoformat(),
            "records_created": records_created,
        }

    except Exception as exc:
        log.error("Attendance aggregation failed", error=str(exc))
        raise self.retry(exc=exc)


@celery_app.task(
    name="app.workers.analytics_tasks.compute_patterns",
    bind=True,
    max_retries=2,
    default_retry_delay=60,
    soft_time_limit=300,
    time_limit=600,
    queue="analytics",
)
def compute_patterns(
    self, org_id: str, metric: str, period: str
) -> dict[str, Any]:
    """Perform statistical pattern analysis on aggregated analytics data.

    Computes trends, anomalies, and patterns for the specified metric
    (footfall, dwell, attendance, alerts) over the given period
    (daily, weekly, monthly).

    Args:
        org_id: UUID of the organization.
        metric: The metric to analyze (footfall, dwell, attendance, alerts).
        period: Analysis period (daily, weekly, monthly).

    Returns:
        dict containing pattern analysis results.
    """
    log = logger.bind(org_id=org_id, metric=metric, period=period, task_id=self.request.id)
    log.info("Computing pattern analysis")

    try:
        now = datetime.now(timezone.utc)

        period_days = {"daily": 30, "weekly": 90, "monthly": 365}.get(period, 30)
        start_date = now - timedelta(days=period_days)

        async def _query_data():
            from app.database import get_db_context

            async with get_db_context() as session:
                if metric == "footfall":
                    query = text("""
                        SELECT
                            DATE(hour_start) AS date,
                            SUM(total_count) AS value
                        FROM footfall_records fr
                        JOIN cameras c ON fr.camera_id = c.id
                        WHERE c.org_id = :org_id
                          AND fr.hour_start >= :start_date
                        GROUP BY DATE(hour_start)
                        ORDER BY date
                    """)
                elif metric == "dwell":
                    query = text("""
                        SELECT
                            DATE(enter_time) AS date,
                            AVG(dwell_seconds) AS value
                        FROM dwell_records dr
                        JOIN cameras c ON dr.camera_id = c.id
                        WHERE c.org_id = :org_id
                          AND dr.enter_time >= :start_date
                        GROUP BY DATE(enter_time)
                        ORDER BY date
                    """)
                elif metric == "attendance":
                    query = text("""
                        SELECT
                            date,
                            COUNT(DISTINCT person_id) AS value
                        FROM attendance_logs
                        WHERE org_id = :org_id
                          AND date >= :start_date
                        GROUP BY date
                        ORDER BY date
                    """)
                elif metric == "alerts":
                    query = text("""
                        SELECT
                            DATE(created_at) AS date,
                            COUNT(*) AS value
                        FROM alerts
                        WHERE org_id = :org_id
                          AND created_at >= :start_date
                        GROUP BY DATE(created_at)
                        ORDER BY date
                    """)
                else:
                    return []

                result = await session.execute(
                    query,
                    {"org_id": org_id, "start_date": start_date},
                )
                return [
                    {"date": row.date.isoformat(), "value": float(row.value)}
                    for row in result.fetchall()
                ]

        data_points = _run_async(_query_data())

        if len(data_points) < 2:
            return {
                "org_id": org_id,
                "metric": metric,
                "period": period,
                "status": "insufficient_data",
                "data_points": len(data_points),
            }

        values = np.array([dp["value"] for dp in data_points])

        # Basic statistical analysis
        mean_val = float(np.mean(values))
        std_val = float(np.std(values))
        median_val = float(np.median(values))

        # Trend detection (simple linear regression)
        x = np.arange(len(values), dtype=np.float64)
        if len(x) > 1:
            coeffs = np.polyfit(x, values, 1)
            slope = float(coeffs[0])
            trend = "increasing" if slope > 0.1 else "decreasing" if slope < -0.1 else "stable"
            trend_strength = abs(slope) / (mean_val + 1e-10) * 100
        else:
            slope = 0.0
            trend = "stable"
            trend_strength = 0.0

        # Anomaly detection (values beyond 2 standard deviations)
        anomalies = []
        if std_val > 0:
            z_scores = (values - mean_val) / std_val
            for i, z in enumerate(z_scores):
                if abs(z) > 2.0:
                    anomalies.append({
                        "date": data_points[i]["date"],
                        "value": data_points[i]["value"],
                        "z_score": round(float(z), 2),
                        "type": "spike" if z > 0 else "dip",
                    })

        # Day-of-week pattern (for daily granularity)
        dow_pattern = defaultdict(list)
        for dp in data_points:
            dow = datetime.fromisoformat(dp["date"]).strftime("%A")
            dow_pattern[dow].append(dp["value"])

        dow_averages = {
            day: round(float(np.mean(vals)), 2)
            for day, vals in dow_pattern.items()
        }

        # Peak detection
        peak_day = max(data_points, key=lambda dp: dp["value"])
        low_day = min(data_points, key=lambda dp: dp["value"])

        result = {
            "org_id": org_id,
            "metric": metric,
            "period": period,
            "status": "completed",
            "data_points": len(data_points),
            "statistics": {
                "mean": round(mean_val, 2),
                "median": round(median_val, 2),
                "std_dev": round(std_val, 2),
                "min": round(float(np.min(values)), 2),
                "max": round(float(np.max(values)), 2),
            },
            "trend": {
                "direction": trend,
                "slope": round(slope, 4),
                "strength_pct": round(trend_strength, 2),
            },
            "anomalies": anomalies,
            "day_of_week_averages": dow_averages,
            "peak": {"date": peak_day["date"], "value": peak_day["value"]},
            "low": {"date": low_day["date"], "value": low_day["value"]},
            "time_series": data_points,
        }

        # Cache the result
        cache_key = f"visionai:analytics:patterns:{org_id}:{metric}:{period}"
        _redis.set(cache_key, json.dumps(result), ex=3600)

        log.info(
            "Pattern analysis completed",
            data_points=len(data_points),
            trend=trend,
            anomalies=len(anomalies),
        )

        return result

    except Exception as exc:
        log.error("Pattern analysis failed", error=str(exc))
        raise self.retry(exc=exc)
