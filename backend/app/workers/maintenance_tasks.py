"""Celery tasks for system maintenance.

Handles periodic health checks, data cleanup, cache updates, and database maintenance.
"""

import os
import time
from datetime import datetime, timedelta, timezone
from typing import Any

import structlog

from app.workers.celery_app import celery_app

logger = structlog.get_logger(__name__)


@celery_app.task(
    name="maintenance.check_camera_health",
    queue="default",
)
def check_camera_health() -> dict:
    """Check health of all active cameras.

    Pings each camera's stream URL to verify connectivity.
    Updates camera health records and marks cameras as online/offline.

    Returns:
        Dict with counts of online, offline, and degraded cameras.
    """
    log = logger.bind(task="check_camera_health")
    log.info("Starting camera health check")

    import cv2

    results = {"online": 0, "offline": 0, "degraded": 0, "checked": 0}

    try:
        # Get all active cameras from database
        from sqlalchemy import create_engine, select
        from sqlalchemy.orm import Session

        from app.config import get_settings

        settings = get_settings()
        sync_url = settings.DATABASE_URL.replace("+asyncpg", "")
        engine = create_engine(sync_url)

        from app.models.camera import Camera, CameraHealth, CameraHealthStatus
        from app.utils.encryption import decrypt_string

        with Session(engine) as session:
            cameras = session.execute(
                select(Camera).where(Camera.is_active == True)
            ).scalars().all()

            for camera in cameras:
                results["checked"] += 1
                start_time = time.time()

                try:
                    stream_url = decrypt_string(camera.stream_url) if camera.stream_url else ""
                    if not stream_url:
                        status = CameraHealthStatus.offline
                        results["offline"] += 1
                        continue

                    # Try to open stream and grab a frame
                    cap = cv2.VideoCapture(stream_url)
                    cap.set(cv2.CAP_PROP_OPEN_TIMEOUT_MSEC, 5000)
                    cap.set(cv2.CAP_PROP_READ_TIMEOUT_MSEC, 5000)

                    if cap.isOpened():
                        ret, frame = cap.read()
                        latency_ms = (time.time() - start_time) * 1000
                        fps_actual = cap.get(cv2.CAP_PROP_FPS)

                        if ret and frame is not None:
                            if latency_ms > 3000:
                                status = CameraHealthStatus.degraded
                                results["degraded"] += 1
                            else:
                                status = CameraHealthStatus.online
                                results["online"] += 1
                        else:
                            status = CameraHealthStatus.degraded
                            results["degraded"] += 1
                    else:
                        status = CameraHealthStatus.offline
                        latency_ms = (time.time() - start_time) * 1000
                        fps_actual = 0
                        results["offline"] += 1

                    cap.release()

                    # Update camera status
                    camera.is_online = status != CameraHealthStatus.offline
                    if status == CameraHealthStatus.online:
                        camera.last_seen_at = datetime.now(timezone.utc)

                    # Log health record
                    health = CameraHealth(
                        camera_id=camera.id,
                        timestamp=datetime.now(timezone.utc),
                        status=status,
                        fps_actual=fps_actual if fps_actual > 0 else None,
                        latency_ms=latency_ms,
                    )
                    session.add(health)

                except Exception as cam_err:
                    log.warning(
                        "Camera health check failed",
                        camera_id=str(camera.id),
                        error=str(cam_err),
                    )
                    camera.is_online = False
                    results["offline"] += 1

            session.commit()

        engine.dispose()

    except Exception as exc:
        log.error("Camera health check failed", error=str(exc))

    log.info("Camera health check complete", **results)
    return results


@celery_app.task(
    name="maintenance.cleanup_old_events",
    queue="default",
)
def cleanup_old_events(days: int = 90) -> dict:
    """Archive or delete events older than the specified retention period.

    Args:
        days: Number of days to retain events.

    Returns:
        Dict with count of deleted records per table.
    """
    log = logger.bind(task="cleanup_old_events", retention_days=days)
    log.info("Starting old event cleanup")

    cutoff = datetime.now(timezone.utc) - timedelta(days=days)
    deleted = {}

    try:
        from sqlalchemy import create_engine, delete
        from sqlalchemy.orm import Session

        from app.config import get_settings
        from app.models.face import FaceEvent
        from app.models.vehicle import VehicleEvent
        from app.models.analytics import DwellRecord, FootfallRecord

        settings = get_settings()
        sync_url = settings.DATABASE_URL.replace("+asyncpg", "")
        engine = create_engine(sync_url)

        with Session(engine) as session:
            # Delete old face events (keep those linked to persons)
            result = session.execute(
                delete(FaceEvent).where(
                    FaceEvent.created_at < cutoff,
                    FaceEvent.person_id == None,
                )
            )
            deleted["face_events_anonymous"] = result.rowcount

            # Delete old vehicle events
            result = session.execute(
                delete(VehicleEvent).where(VehicleEvent.created_at < cutoff)
            )
            deleted["vehicle_events"] = result.rowcount

            # Delete old dwell records
            result = session.execute(
                delete(DwellRecord).where(DwellRecord.created_at < cutoff)
            )
            deleted["dwell_records"] = result.rowcount

            # Delete old footfall records (keep daily/weekly aggregates longer)
            result = session.execute(
                delete(FootfallRecord).where(
                    FootfallRecord.created_at < cutoff,
                    FootfallRecord.period == "hourly",
                )
            )
            deleted["footfall_hourly"] = result.rowcount

            # Delete old camera health records
            from app.models.camera import CameraHealth

            health_cutoff = datetime.now(timezone.utc) - timedelta(days=7)
            result = session.execute(
                delete(CameraHealth).where(CameraHealth.created_at < health_cutoff)
            )
            deleted["camera_health"] = result.rowcount

            session.commit()

        engine.dispose()

    except Exception as exc:
        log.error("Event cleanup failed", error=str(exc))

    log.info("Event cleanup complete", deleted=deleted)
    return deleted


@celery_app.task(
    name="maintenance.cleanup_temp_files",
    queue="default",
)
def cleanup_temp_files() -> dict:
    """Remove temporary processing files from disk.

    Cleans up /tmp/visionai directory for files older than 24 hours.

    Returns:
        Dict with count and total size of deleted files.
    """
    log = logger.bind(task="cleanup_temp_files")
    temp_dir = "/tmp/visionai"
    deleted_count = 0
    deleted_bytes = 0
    cutoff = time.time() - 86400  # 24 hours ago

    if not os.path.exists(temp_dir):
        return {"deleted_files": 0, "deleted_bytes": 0}

    try:
        for root, dirs, files in os.walk(temp_dir, topdown=False):
            for fname in files:
                fpath = os.path.join(root, fname)
                try:
                    if os.path.getmtime(fpath) < cutoff:
                        fsize = os.path.getsize(fpath)
                        os.remove(fpath)
                        deleted_count += 1
                        deleted_bytes += fsize
                except OSError:
                    pass

            # Remove empty directories
            for dname in dirs:
                dpath = os.path.join(root, dname)
                try:
                    if not os.listdir(dpath):
                        os.rmdir(dpath)
                except OSError:
                    pass

    except Exception as exc:
        log.error("Temp file cleanup failed", error=str(exc))

    log.info(
        "Temp file cleanup complete",
        deleted_files=deleted_count,
        deleted_mb=round(deleted_bytes / (1024 * 1024), 2),
    )
    return {"deleted_files": deleted_count, "deleted_bytes": deleted_bytes}


@celery_app.task(
    name="maintenance.update_analytics_cache",
    queue="default",
)
def update_analytics_cache() -> dict:
    """Pre-compute dashboard statistics and store in Redis cache.

    Caches: camera counts, alert summaries, footfall totals,
    compliance percentages for fast dashboard loading.

    Returns:
        Dict with cached key count.
    """
    log = logger.bind(task="update_analytics_cache")
    log.info("Updating analytics cache")

    cached_keys = 0

    try:
        import json

        import redis

        from app.config import get_settings

        settings = get_settings()
        r = redis.from_url(settings.REDIS_URL, decode_responses=True)

        from sqlalchemy import create_engine, func, select
        from sqlalchemy.orm import Session

        from app.models.alert import Alert, AlertStatus
        from app.models.camera import Camera

        sync_url = settings.DATABASE_URL.replace("+asyncpg", "")
        engine = create_engine(sync_url)

        today = datetime.now(timezone.utc).date()

        with Session(engine) as session:
            # Camera counts
            total_cameras = session.execute(
                select(func.count(Camera.id)).where(Camera.is_active == True)
            ).scalar() or 0
            online_cameras = session.execute(
                select(func.count(Camera.id)).where(
                    Camera.is_active == True, Camera.is_online == True
                )
            ).scalar() or 0

            cache_data = {
                "total_cameras": total_cameras,
                "online_cameras": online_cameras,
                "offline_cameras": total_cameras - online_cameras,
            }

            # Today's alerts
            today_start = datetime.combine(today, datetime.min.time()).replace(
                tzinfo=timezone.utc
            )
            total_alerts = session.execute(
                select(func.count(Alert.id)).where(Alert.created_at >= today_start)
            ).scalar() or 0
            cache_data["alerts_today"] = total_alerts

            new_alerts = session.execute(
                select(func.count(Alert.id)).where(
                    Alert.created_at >= today_start,
                    Alert.status == AlertStatus.new,
                )
            ).scalar() or 0
            cache_data["unacknowledged_alerts"] = new_alerts

            # Store in Redis with 5 minute TTL
            r.setex(
                "visionai:dashboard:stats",
                300,
                json.dumps(cache_data),
            )
            cached_keys += 1

        engine.dispose()

    except Exception as exc:
        log.error("Analytics cache update failed", error=str(exc))

    log.info("Analytics cache updated", cached_keys=cached_keys)
    return {"cached_keys": cached_keys}


@celery_app.task(
    name="maintenance.database_vacuum",
    queue="default",
)
def database_vacuum() -> dict:
    """Run VACUUM ANALYZE on large tables for query performance.

    Returns:
        Dict with list of vacuumed tables.
    """
    log = logger.bind(task="database_vacuum")
    log.info("Starting database vacuum")

    tables_to_vacuum = [
        "face_events",
        "vehicle_events",
        "alerts",
        "camera_health",
        "footfall_records",
        "dwell_records",
        "attendance_logs",
    ]
    vacuumed = []

    try:
        from sqlalchemy import create_engine, text

        from app.config import get_settings

        settings = get_settings()
        sync_url = settings.DATABASE_URL.replace("+asyncpg", "")
        engine = create_engine(sync_url, isolation_level="AUTOCOMMIT")

        with engine.connect() as conn:
            for table in tables_to_vacuum:
                try:
                    conn.execute(text(f"VACUUM ANALYZE {table}"))
                    vacuumed.append(table)
                    log.debug("Vacuumed table", table=table)
                except Exception as e:
                    log.warning("Vacuum failed for table", table=table, error=str(e))

        engine.dispose()

    except Exception as exc:
        log.error("Database vacuum failed", error=str(exc))

    log.info("Database vacuum complete", tables=vacuumed)
    return {"vacuumed_tables": vacuumed}
