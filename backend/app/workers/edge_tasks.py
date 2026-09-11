"""Edge device management Celery tasks.

Provides background tasks for periodic health checks, model deployment,
result synchronization, metrics cleanup, and stale-device detection.
All database access uses the async context manager from the database
module, bridged into Celery's synchronous task execution via a fresh
event loop.
"""

from __future__ import annotations

import asyncio
import uuid
from datetime import datetime, timedelta, timezone
from typing import Any

import structlog
from celery import shared_task
from sqlalchemy import select

from app.database import get_db_context
from app.models.edge_device import EdgeDevice
from app.services.edge_service import EdgeService

logger = structlog.stdlib.get_logger(__name__)


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


# ── Check Edge Health Task ──────────────────────────────────────────────────


@shared_task(
    name="app.workers.edge_tasks.check_edge_health_task",
    bind=True,
    max_retries=1,
    default_retry_delay=15,
    acks_late=True,
)
def check_edge_health_task(self) -> dict[str, Any]:
    """Periodic health check for all registered edge devices.

    Polls each device's ``/health`` endpoint, updates online status,
    and stores metrics. Also marks stale devices as offline.
    Scheduled to run every 30 seconds via Celery Beat.

    Returns:
        Dict with total device count and per-device health results.
    """
    logger.info("Starting periodic edge device health check")

    async def _execute():
        results: dict[str, Any] = {}

        async with get_db_context() as db:
            # Fetch all edge devices
            device_query = select(EdgeDevice)
            device_result = await db.execute(device_query)
            devices = list(device_result.scalars().all())

            for device in devices:
                try:
                    health = await EdgeService.check_device_health(
                        db=db,
                        device_id=device.id,
                    )
                    results[str(device.id)] = {
                        "name": device.name,
                        "status": health.get("status", "unknown"),
                        "online": health.get("status") == "online",
                    }
                except Exception as exc:
                    results[str(device.id)] = {
                        "name": device.name,
                        "status": "error",
                        "online": False,
                        "error": str(exc)[:200],
                    }

            # Mark stale devices offline
            stale_count = await EdgeService.mark_stale_devices_offline(
                db=db, stale_minutes=2
            )

        return {
            "total_devices": len(devices),
            "results": results,
            "stale_devices_marked_offline": stale_count,
        }

    try:
        result = _run_async(_execute())
        online = sum(1 for r in result["results"].values() if r.get("online"))
        logger.info(
            "Edge health check completed",
            total=result["total_devices"],
            online=online,
            offline=result["total_devices"] - online,
            stale_marked=result["stale_devices_marked_offline"],
        )
        return result
    except Exception as exc:
        logger.error("Edge health check failed", error=str(exc))
        raise self.retry(exc=exc)


# ── Deploy Model Task ───────────────────────────────────────────────────────


@shared_task(
    name="app.workers.edge_tasks.deploy_model_task",
    bind=True,
    max_retries=3,
    default_retry_delay=60,
    acks_late=True,
)
def deploy_model_task(
    self,
    device_id: str,
    model_name: str,
    model_version: str,
    model_format: str = "onnx",
) -> dict[str, Any]:
    """Background model deployment to an edge device.

    Handles the potentially long-running model transfer and loading
    process. Retries up to 3 times on failure.

    Args:
        device_id: UUID of the target device (as string).
        model_name: Name of the model to deploy.
        model_version: Model version tag.
        model_format: Model format (onnx, tensorrt, openvino).

    Returns:
        Dict with deployment ID and status.
    """
    logger.info(
        "Starting background model deployment",
        device_id=device_id,
        model=f"{model_name}:{model_version}",
        format=model_format,
    )

    async def _execute():
        async with get_db_context() as db:
            did = uuid.UUID(device_id)
            deployment = await EdgeService.deploy_model(
                db=db,
                device_id=did,
                model_name=model_name,
                model_version=model_version,
                model_format=model_format,
            )
            return {
                "deployment_id": str(deployment.id),
                "status": deployment.status.value,
                "model": f"{model_name}:{model_version}",
                "error_message": deployment.error_message,
            }

    try:
        result = _run_async(_execute())
        logger.info(
            "Background model deployment completed",
            device_id=device_id,
            result=result,
        )
        return result
    except Exception as exc:
        logger.error(
            "Background model deployment failed",
            device_id=device_id,
            model=f"{model_name}:{model_version}",
            error=str(exc),
        )
        raise self.retry(exc=exc)


# ── Sync Edge Results Task ──────────────────────────────────────────────────


@shared_task(
    name="app.workers.edge_tasks.sync_edge_results_task",
    bind=True,
    max_retries=3,
    default_retry_delay=30,
    acks_late=True,
)
def sync_edge_results_task(self, device_id: str) -> dict[str, Any]:
    """Pull detection results from an edge device.

    Contacts the edge device's metrics endpoint to collect recent
    detection results and stores them in the cloud database.

    Args:
        device_id: UUID of the device to pull results from (as string).

    Returns:
        Dict with sync status and count of results received.
    """
    logger.info("Starting edge result sync", device_id=device_id)

    async def _execute():
        import httpx

        from app.utils.encryption import decrypt_string

        async with get_db_context() as db:
            did = uuid.UUID(device_id)
            device = await EdgeService.get_edge_device(db=db, device_id=did)

            if not device.is_online:
                return {
                    "status": "skipped",
                    "message": "Device is offline",
                    "results_count": 0,
                }

            api_key = None
            if device.api_key_encrypted:
                api_key = decrypt_string(device.api_key_encrypted)

            headers: dict[str, str] = {}
            if api_key:
                headers["X-API-Key"] = api_key

            try:
                async with httpx.AsyncClient(timeout=15.0) as client:
                    response = await client.get(
                        f"{device.api_url}/metrics",
                        headers=headers,
                    )

                    if response.status_code == 200:
                        data = response.json()
                        return {
                            "status": "success",
                            "device_id": str(did),
                            "device_name": device.name,
                            "metrics": data,
                        }
                    else:
                        return {
                            "status": "error",
                            "message": f"HTTP {response.status_code}",
                        }

            except httpx.RequestError as exc:
                return {
                    "status": "error",
                    "message": f"Connection error: {str(exc)[:200]}",
                }

    try:
        result = _run_async(_execute())
        logger.info(
            "Edge result sync completed",
            device_id=device_id,
            status=result.get("status"),
        )
        return result
    except Exception as exc:
        logger.error(
            "Edge result sync failed",
            device_id=device_id,
            error=str(exc),
        )
        raise self.retry(exc=exc)


# ── Cleanup Old Metrics Task ───────────────────────────────────────────────


@shared_task(
    name="app.workers.edge_tasks.cleanup_old_metrics",
    bind=True,
    max_retries=1,
    default_retry_delay=300,
    acks_late=True,
)
def cleanup_old_metrics(self, days: int = 7) -> dict[str, Any]:
    """Clean up old edge metrics data.

    Removes metrics records older than the specified retention period
    to prevent unbounded database growth.  Defaults to 7-day retention.

    Args:
        days: Number of days of metrics to retain.

    Returns:
        Dict with the number of deleted records.
    """
    logger.info("Starting edge metrics cleanup", retention_days=days)

    async def _execute():
        async with get_db_context() as db:
            deleted_count = await EdgeService.cleanup_old_metrics(
                db=db, days=days
            )
            return {
                "status": "success",
                "deleted_count": deleted_count,
                "retention_days": days,
            }

    try:
        result = _run_async(_execute())
        logger.info(
            "Edge metrics cleanup completed",
            deleted_count=result["deleted_count"],
            retention_days=days,
        )
        return result
    except Exception as exc:
        logger.error(
            "Edge metrics cleanup failed",
            error=str(exc),
            retention_days=days,
        )
        raise self.retry(exc=exc)


# ── Bulk Deploy Model Task ──────────────────────────────────────────────────


@shared_task(
    name="app.workers.edge_tasks.bulk_deploy_model_task",
    bind=True,
    max_retries=1,
    default_retry_delay=120,
    acks_late=True,
)
def bulk_deploy_model_task(
    self,
    org_id: str,
    model_name: str,
    model_version: str,
    model_format: str = "onnx",
) -> dict[str, Any]:
    """Deploy a model to all online edge devices in an organization.

    Iterates through all online devices and initiates model deployment
    on each one.  Individual device failures do not affect other deployments.

    Args:
        org_id: Organization UUID (as string).
        model_name: Name of the model to deploy.
        model_version: Model version tag.
        model_format: Model format (onnx, tensorrt, openvino).

    Returns:
        Dict with per-device deployment results.
    """
    logger.info(
        "Starting bulk model deployment",
        org_id=org_id,
        model=f"{model_name}:{model_version}",
    )

    async def _execute():
        results: dict[str, Any] = {}
        async with get_db_context() as db:
            oid = uuid.UUID(org_id)
            devices, _ = await EdgeService.get_edge_devices(db=db, org_id=oid)

            online_devices = [d for d in devices if d.is_online]

            for device in online_devices:
                try:
                    deployment = await EdgeService.deploy_model(
                        db=db,
                        device_id=device.id,
                        model_name=model_name,
                        model_version=model_version,
                        model_format=model_format,
                    )
                    results[str(device.id)] = {
                        "name": device.name,
                        "deployment_id": str(deployment.id),
                        "status": deployment.status.value,
                    }
                except Exception as exc:
                    results[str(device.id)] = {
                        "name": device.name,
                        "status": "failed",
                        "error": str(exc)[:200],
                    }

        return {
            "total_devices": len(online_devices),
            "results": results,
        }

    try:
        result = _run_async(_execute())
        successful = sum(
            1 for r in result["results"].values()
            if r.get("status") == "deployed"
        )
        logger.info(
            "Bulk model deployment completed",
            total=result["total_devices"],
            successful=successful,
            failed=result["total_devices"] - successful,
        )
        return result
    except Exception as exc:
        logger.error(
            "Bulk model deployment failed",
            org_id=org_id,
            error=str(exc),
        )
        raise self.retry(exc=exc)


# ── Bulk Restart Task ───────────────────────────────────────────────────────


@shared_task(
    name="app.workers.edge_tasks.bulk_restart_task",
    bind=True,
    max_retries=1,
    default_retry_delay=60,
    acks_late=True,
)
def bulk_restart_task(self, org_id: str) -> dict[str, Any]:
    """Restart all online edge devices in an organization.

    Args:
        org_id: Organization UUID (as string).

    Returns:
        Dict with per-device restart results.
    """
    logger.info("Starting bulk edge device restart", org_id=org_id)

    async def _execute():
        results: dict[str, Any] = {}
        async with get_db_context() as db:
            oid = uuid.UUID(org_id)
            devices, _ = await EdgeService.get_edge_devices(db=db, org_id=oid)

            online_devices = [d for d in devices if d.is_online]

            for device in online_devices:
                try:
                    restart_result = await EdgeService.restart_device(
                        db=db, device_id=device.id
                    )
                    results[str(device.id)] = {
                        "name": device.name,
                        "status": restart_result.get("status", "unknown"),
                        "message": restart_result.get("message", ""),
                    }
                except Exception as exc:
                    results[str(device.id)] = {
                        "name": device.name,
                        "status": "error",
                        "error": str(exc)[:200],
                    }

        return {
            "total_devices": len(online_devices),
            "results": results,
        }

    try:
        result = _run_async(_execute())
        successful = sum(
            1 for r in result["results"].values()
            if r.get("status") == "success"
        )
        logger.info(
            "Bulk restart completed",
            total=result["total_devices"],
            successful=successful,
        )
        return result
    except Exception as exc:
        logger.error(
            "Bulk restart failed",
            org_id=org_id,
            error=str(exc),
        )
        raise self.retry(exc=exc)
