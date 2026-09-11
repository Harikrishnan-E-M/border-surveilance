"""
VisionAI Anomaly Detection Celery Tasks.

Provides background tasks for baseline computation, periodic anomaly
checking, and old anomaly event cleanup.
"""

from __future__ import annotations

import asyncio
import json
import uuid
from datetime import datetime, timedelta, timezone
from typing import Any, Optional

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
    name="app.workers.anomaly_tasks.rebuild_baselines_task",
    bind=True,
    max_retries=3,
    default_retry_delay=120,
    soft_time_limit=600,
    time_limit=900,
    queue="analytics",
)
def rebuild_baselines_task(
    self,
    org_id: str,
    camera_ids: Optional[list[str]] = None,
    days: int = 30,
) -> dict[str, Any]:
    """Long-running baseline computation task.

    Rebuilds statistical baselines from historical footfall and
    detection data for the specified organization and cameras.

    Args:
        org_id: Organization UUID string.
        camera_ids: Optional list of camera UUID strings.
            If None, rebuilds all cameras in the org.
        days: Number of historical days to use for baseline computation.

    Returns:
        dict summarizing the rebuild results.
    """
    log = logger.bind(task_id=self.request.id, org_id=org_id)
    log.info("Starting baseline rebuild", cameras=camera_ids or "all", days=days)

    try:
        org_uuid = uuid.UUID(org_id)
        camera_uuids = [uuid.UUID(cid) for cid in camera_ids] if camera_ids else None

        async def _rebuild():
            import redis.asyncio as aioredis
            from app.database import get_db_context
            from app.services.anomaly_service import rebuild_baselines

            # Create async Redis client for the task
            async_redis = aioredis.from_url(
                settings.REDIS_URL,
                decode_responses=True,
                socket_timeout=5,
            )

            try:
                async with get_db_context() as session:
                    results = await rebuild_baselines(
                        db=session,
                        org_id=org_uuid,
                        camera_ids=camera_uuids,
                        days=days,
                        redis_client=async_redis,
                    )
                    return results
            finally:
                await async_redis.close()

        results = _run_async(_rebuild())

        # Cache the last rebuild timestamp
        _redis.set(
            f"visionai:anomaly:last_baseline_rebuild:{org_id}",
            json.dumps({
                "completed_at": datetime.now(timezone.utc).isoformat(),
                "cameras_processed": results.get("cameras_processed", 0),
                "baselines_created": results.get("baselines_created", 0),
            }),
            ex=86400 * 7,
        )

        log.info(
            "Baseline rebuild completed",
            cameras_processed=results.get("cameras_processed", 0),
            baselines_created=results.get("baselines_created", 0),
            errors=len(results.get("errors", [])),
        )

        return {
            "status": "completed",
            "org_id": org_id,
            **results,
        }

    except Exception as exc:
        log.error("Baseline rebuild failed", error=str(exc))
        raise self.retry(exc=exc)


@celery_app.task(
    name="app.workers.anomaly_tasks.run_periodic_anomaly_check",
    bind=True,
    max_retries=2,
    default_retry_delay=60,
    soft_time_limit=240,
    time_limit=300,
    queue="analytics",
)
def run_periodic_anomaly_check(self) -> dict[str, Any]:
    """Scheduled task to check recent data against baselines.

    Runs every 5 minutes via Celery Beat. Iterates over all active
    cameras with non-stale baselines and runs anomaly detection on
    the most recent analytics data.

    Returns:
        dict summarizing detection results across all cameras.
    """
    log = logger.bind(task_id=self.request.id)
    log.info("Starting periodic anomaly check")

    try:
        now = datetime.now(timezone.utc)
        lookback = now - timedelta(minutes=15)

        async def _check_all():
            import redis.asyncio as aioredis
            from app.database import get_db_context
            from app.models.anomaly import AnomalyBaseline
            from app.models.analytics import FootfallRecord
            from app.models.camera import Camera
            from app.services.anomaly_service import run_anomaly_detection

            async_redis = aioredis.from_url(
                settings.REDIS_URL,
                decode_responses=True,
                socket_timeout=5,
            )

            total_anomalies = 0
            cameras_checked = 0
            errors = []

            try:
                async with get_db_context() as session:
                    # Get all cameras with active baselines
                    cameras_q = (
                        select(Camera.id, Camera.org_id, Camera.name)
                        .join(
                            AnomalyBaseline,
                            AnomalyBaseline.camera_id == Camera.id,
                        )
                        .where(
                            Camera.is_active.is_(True),
                            Camera.is_online.is_(True),
                            AnomalyBaseline.is_stale.is_(False),
                        )
                        .distinct()
                    )
                    cameras_result = await session.execute(cameras_q)
                    cameras = cameras_result.all()

                    for cam in cameras:
                        try:
                            # Get recent footfall data for frequency analysis
                            recent_q = (
                                select(
                                    FootfallRecord.timestamp,
                                    FootfallRecord.entries_count,
                                )
                                .where(
                                    FootfallRecord.camera_id == cam.id,
                                    FootfallRecord.timestamp >= lookback,
                                )
                                .order_by(FootfallRecord.timestamp)
                            )
                            recent_result = await session.execute(recent_q)
                            recent_data = recent_result.all()

                            event_counts = [
                                {
                                    "timestamp": row.timestamp.isoformat(),
                                    "count": row.entries_count,
                                }
                                for row in recent_data
                            ]

                            anomalies = await run_anomaly_detection(
                                db=session,
                                camera_id=cam.id,
                                org_id=cam.org_id,
                                event_counts=event_counts if len(event_counts) >= 4 else None,
                                redis_client=async_redis,
                            )

                            total_anomalies += len(anomalies)
                            cameras_checked += 1

                        except Exception as exc:
                            errors.append({
                                "camera_id": str(cam.id),
                                "error": str(exc),
                            })
                            log.warning(
                                "Anomaly check failed for camera",
                                camera_id=str(cam.id),
                                error=str(exc),
                            )

                    return {
                        "cameras_checked": cameras_checked,
                        "total_anomalies": total_anomalies,
                        "errors": errors,
                    }
            finally:
                await async_redis.close()

        results = _run_async(_check_all())

        # Cache the check results in Redis
        _redis.set(
            "visionai:anomaly:last_periodic_check",
            json.dumps({
                "completed_at": now.isoformat(),
                "cameras_checked": results["cameras_checked"],
                "anomalies_found": results["total_anomalies"],
            }),
            ex=600,
        )

        log.info(
            "Periodic anomaly check completed",
            cameras_checked=results["cameras_checked"],
            anomalies_found=results["total_anomalies"],
            errors=len(results["errors"]),
        )

        return {
            "status": "completed",
            **results,
        }

    except Exception as exc:
        log.error("Periodic anomaly check failed", error=str(exc))
        raise self.retry(exc=exc)


@celery_app.task(
    name="app.workers.anomaly_tasks.cleanup_old_anomalies",
    bind=True,
    max_retries=2,
    default_retry_delay=120,
    queue="maintenance",
)
def cleanup_old_anomalies(self, days: int = 90) -> dict[str, Any]:
    """Delete anomaly events older than the specified retention period.

    Args:
        days: Number of days to retain. Events older than this are deleted.

    Returns:
        dict with the count of deleted records.
    """
    log = logger.bind(task_id=self.request.id, retention_days=days)
    log.info("Starting anomaly cleanup")

    try:
        cutoff = datetime.now(timezone.utc) - timedelta(days=days)

        async def _cleanup():
            from app.database import get_db_context
            from app.models.anomaly import AnomalyEvent

            async with get_db_context() as session:
                # Count before deletion for reporting
                count_q = select(func.count()).where(
                    AnomalyEvent.created_at < cutoff
                )
                count = (await session.execute(count_q)).scalar() or 0

                if count > 0:
                    # Delete in batches to avoid long-running transactions
                    batch_size = 5000
                    deleted_total = 0

                    while deleted_total < count:
                        # Get IDs of records to delete
                        ids_q = (
                            select(AnomalyEvent.id)
                            .where(AnomalyEvent.created_at < cutoff)
                            .limit(batch_size)
                        )
                        ids_result = await session.execute(ids_q)
                        ids_to_delete = [row[0] for row in ids_result.all()]

                        if not ids_to_delete:
                            break

                        await session.execute(
                            delete(AnomalyEvent).where(
                                AnomalyEvent.id.in_(ids_to_delete)
                            )
                        )
                        await session.flush()
                        deleted_total += len(ids_to_delete)

                    return deleted_total

                return 0

        deleted = _run_async(_cleanup())

        log.info("Anomaly cleanup completed", deleted_count=deleted, cutoff=cutoff.isoformat())

        return {
            "status": "completed",
            "deleted_count": deleted,
            "cutoff_date": cutoff.isoformat(),
            "retention_days": days,
        }

    except Exception as exc:
        log.error("Anomaly cleanup failed", error=str(exc))
        raise self.retry(exc=exc)
