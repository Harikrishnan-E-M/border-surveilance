"""Multi-Site Federation Celery tasks.

Provides background tasks for periodic heartbeat checks, scheduled
cross-site synchronisation, and on-demand sync operations. All
database access uses the async context manager from the database
module, bridged into Celery's synchronous task execution via
``asyncio.run()``.
"""

from __future__ import annotations

import asyncio
import uuid
from datetime import datetime, timedelta, timezone
from typing import Any

import structlog
from celery import shared_task
from sqlalchemy import and_, select

from app.database import get_db_context
from app.models.federation import Site, SyncType
from app.services.federation_service import FederationService

logger = structlog.stdlib.get_logger(__name__)

federation_service = FederationService()


# ── Helpers ─────────────────────────────────────────────────────────────────


def _run_async(coro):
    """Run an async coroutine in a new event loop.

    Celery workers run synchronous tasks, so we bridge to the async
    database session and service layer with a fresh event loop.
    """
    loop = asyncio.new_event_loop()
    try:
        return loop.run_until_complete(coro)
    finally:
        loop.close()


# ── Sync Site Task ──────────────────────────────────────────────────────────


@shared_task(
    name="app.workers.federation_tasks.sync_site_task",
    bind=True,
    max_retries=3,
    default_retry_delay=60,
    acks_late=True,
)
def sync_site_task(self, site_id: str, sync_type: str = "alerts") -> dict[str, Any]:
    """Background sync for a specific site and data type.

    Args:
        site_id: UUID of the site to sync (as string).
        sync_type: Type of sync: alerts, cameras, faces, vehicles.

    Returns:
        Dict with sync result status and record count.
    """
    logger.info(
        "Starting site sync task",
        site_id=site_id,
        sync_type=sync_type,
    )

    async def _execute():
        async with get_db_context() as db:
            sid = uuid.UUID(site_id)

            if sync_type == "alerts":
                # Sync alerts from the last 24 hours
                since = datetime.now(timezone.utc) - timedelta(hours=24)
                record = await federation_service.sync_alerts(db, sid, since=since)
            elif sync_type == "cameras":
                record = await federation_service.sync_cameras(db, sid)
            elif sync_type == "faces":
                record = await federation_service.sync_faces(db, sid)
            elif sync_type == "vehicles":
                record = await federation_service.sync_vehicles(db, sid)
            else:
                since = datetime.now(timezone.utc) - timedelta(hours=24)
                record = await federation_service.sync_alerts(db, sid, since=since)

            return {
                "sync_id": str(record.id),
                "status": record.status.value if hasattr(record.status, "value") else str(record.status),
                "records_synced": record.records_synced,
                "error_message": record.error_message,
            }

    try:
        result = _run_async(_execute())
        logger.info(
            "Site sync task completed",
            site_id=site_id,
            sync_type=sync_type,
            result=result,
        )
        return result
    except Exception as exc:
        logger.error(
            "Site sync task failed",
            site_id=site_id,
            sync_type=sync_type,
            error=str(exc),
        )
        raise self.retry(exc=exc)


# ── Heartbeat All Sites ────────────────────────────────────────────────────


@shared_task(
    name="app.workers.federation_tasks.heartbeat_all_sites",
    bind=True,
    max_retries=1,
    default_retry_delay=30,
    acks_late=True,
)
def heartbeat_all_sites(self) -> dict[str, Any]:
    """Periodic heartbeat check for all registered sites.

    Pings every site's health endpoint and updates online status.
    Scheduled to run every 60 seconds via Celery Beat.

    Returns:
        Dict with site count and per-site heartbeat results.
    """
    logger.info("Starting heartbeat check for all federation sites")

    async def _execute():
        results: dict[str, Any] = {}
        async with get_db_context() as db:
            site_query = select(Site)
            site_result = await db.execute(site_query)
            sites = list(site_result.scalars().all())

            for site in sites:
                try:
                    hb_result = await federation_service.heartbeat(db, site.id)
                    results[site.code] = {
                        "reachable": hb_result.get("reachable", False),
                        "latency_ms": hb_result.get("latency_ms"),
                    }
                except Exception as exc:
                    results[site.code] = {
                        "reachable": False,
                        "error": str(exc),
                    }

        return {
            "total_sites": len(sites),
            "results": results,
        }

    try:
        result = _run_async(_execute())
        online = sum(1 for r in result["results"].values() if r.get("reachable"))
        logger.info(
            "Heartbeat check completed",
            total=result["total_sites"],
            online=online,
            offline=result["total_sites"] - online,
        )
        return result
    except Exception as exc:
        logger.error("Heartbeat check failed", error=str(exc))
        raise self.retry(exc=exc)


# ── Sync All Sites ─────────────────────────────────────────────────────────


@shared_task(
    name="app.workers.federation_tasks.sync_all_sites",
    bind=True,
    max_retries=1,
    default_retry_delay=120,
    acks_late=True,
)
def sync_all_sites(self) -> dict[str, Any]:
    """Scheduled sync for all online sites.

    Runs every 5 minutes via Celery Beat. Syncs alerts and camera
    status from all online sites.

    Returns:
        Dict with sync results per site.
    """
    logger.info("Starting scheduled sync for all federation sites")

    async def _execute():
        results: dict[str, Any] = {}
        since = datetime.now(timezone.utc) - timedelta(minutes=10)

        async with get_db_context() as db:
            site_query = select(Site).where(Site.is_online.is_(True))
            site_result = await db.execute(site_query)
            sites = list(site_result.scalars().all())

            for site in sites:
                site_results: dict[str, Any] = {}
                try:
                    # Sync alerts
                    alert_record = await federation_service.sync_alerts(db, site.id, since=since)
                    site_results["alerts"] = {
                        "status": alert_record.status.value if hasattr(alert_record.status, "value") else str(alert_record.status),
                        "records_synced": alert_record.records_synced,
                    }
                except Exception as exc:
                    site_results["alerts"] = {"status": "failed", "error": str(exc)}

                try:
                    # Sync camera status
                    camera_record = await federation_service.sync_cameras(db, site.id)
                    site_results["cameras"] = {
                        "status": camera_record.status.value if hasattr(camera_record.status, "value") else str(camera_record.status),
                        "records_synced": camera_record.records_synced,
                    }
                except Exception as exc:
                    site_results["cameras"] = {"status": "failed", "error": str(exc)}

                results[site.code] = site_results

        return {
            "total_sites_synced": len(results),
            "results": results,
        }

    try:
        result = _run_async(_execute())
        logger.info(
            "Scheduled sync completed",
            total_sites_synced=result["total_sites_synced"],
        )
        return result
    except Exception as exc:
        logger.error("Scheduled sync failed", error=str(exc))
        raise self.retry(exc=exc)


# ── Sync Face Database ─────────────────────────────────────────────────────


@shared_task(
    name="app.workers.federation_tasks.sync_face_database",
    bind=True,
    max_retries=3,
    default_retry_delay=120,
    acks_late=True,
)
def sync_face_database(self, site_id: str) -> dict[str, Any]:
    """Full face database sync for a specific site.

    Pushes all locally enrolled faces to the remote site to
    ensure consistent face recognition across the federation.

    Args:
        site_id: UUID of the site to sync faces with (as string).

    Returns:
        Dict with sync result.
    """
    logger.info("Starting face database sync", site_id=site_id)

    async def _execute():
        async with get_db_context() as db:
            sid = uuid.UUID(site_id)
            record = await federation_service.sync_faces(db, sid)
            return {
                "sync_id": str(record.id),
                "status": record.status.value if hasattr(record.status, "value") else str(record.status),
                "records_synced": record.records_synced,
                "error_message": record.error_message,
            }

    try:
        result = _run_async(_execute())
        logger.info(
            "Face database sync completed",
            site_id=site_id,
            result=result,
        )
        return result
    except Exception as exc:
        logger.error(
            "Face database sync failed",
            site_id=site_id,
            error=str(exc),
        )
        raise self.retry(exc=exc)


# ── Mark Stale Sites Offline ───────────────────────────────────────────────


@shared_task(
    name="app.workers.federation_tasks.mark_stale_sites_offline",
    bind=True,
    max_retries=1,
    acks_late=True,
)
def mark_stale_sites_offline(self, stale_minutes: int = 5) -> dict[str, Any]:
    """Mark sites as offline if they haven't sent a heartbeat recently.

    Args:
        stale_minutes: Number of minutes without a heartbeat before
            a site is considered offline.

    Returns:
        Dict with the number of sites marked offline.
    """
    logger.info("Checking for stale federation sites", stale_minutes=stale_minutes)

    async def _execute():
        from sqlalchemy import update as sa_update

        threshold = datetime.now(timezone.utc) - timedelta(minutes=stale_minutes)

        async with get_db_context() as db:
            # Find sites that are marked online but haven't heartbeated
            stale_query = select(Site).where(
                and_(
                    Site.is_online.is_(True),
                    (Site.last_heartbeat < threshold) | (Site.last_heartbeat.is_(None)),
                )
            )
            result = await db.execute(stale_query)
            stale_sites = list(result.scalars().all())

            for site in stale_sites:
                site.is_online = False

            await db.flush()

            return {
                "stale_sites_marked_offline": len(stale_sites),
                "site_codes": [s.code for s in stale_sites],
            }

    try:
        result = _run_async(_execute())
        if result["stale_sites_marked_offline"] > 0:
            logger.warning(
                "Stale sites marked offline",
                count=result["stale_sites_marked_offline"],
                codes=result["site_codes"],
            )
        return result
    except Exception as exc:
        logger.error("Stale site check failed", error=str(exc))
        raise self.retry(exc=exc)
