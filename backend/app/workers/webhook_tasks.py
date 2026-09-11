"""
VisionAI Webhook & Integration Celery Tasks.

Handles async webhook delivery with retries, periodic retry of failed
deliveries, cleanup of old delivery logs, and dispatch to third-party
integrations (Slack, Teams, PagerDuty, Jira).
"""

from __future__ import annotations

import asyncio
import uuid
from datetime import datetime, timedelta, timezone
from typing import Any

import structlog
from sqlalchemy import and_, delete, select

from app.config import get_settings
from app.workers.celery_app import celery_app

logger = structlog.stdlib.get_logger(__name__)
settings = get_settings()


def _run_async(coro):
    """Run an async coroutine from a synchronous Celery task context."""
    loop = asyncio.new_event_loop()
    try:
        return loop.run_until_complete(coro)
    finally:
        loop.close()


# ── Webhook Delivery Task ────────────────────────────────────────────────


@celery_app.task(
    name="app.workers.webhook_tasks.deliver_webhook_task",
    bind=True,
    max_retries=5,
    default_retry_delay=15,
    queue="alerts",
)
def deliver_webhook_task(
    self,
    webhook_id: str,
    event_type: str,
    payload: dict[str, Any],
) -> dict[str, Any]:
    """Async webhook delivery with retry support.

    Delivers a webhook event payload to the target endpoint. On failure,
    the delivery is recorded in the database and scheduled for retry
    according to the endpoint's retry policy.

    Args:
        webhook_id: UUID of the target webhook endpoint.
        event_type: The event type string.
        payload: JSON-serializable event payload.

    Returns:
        Dict with delivery status and details.
    """
    log = logger.bind(
        webhook_id=webhook_id,
        event_type=event_type,
        task_id=self.request.id,
    )
    log.info("Delivering webhook event")

    try:
        result = _run_async(_deliver_webhook_async(webhook_id, event_type, payload))
        log.info(
            "Webhook delivery completed",
            delivery_id=result.get("delivery_id"),
            status=result.get("status"),
        )
        return result

    except Exception as exc:
        log.error("Webhook delivery task failed", error=str(exc))
        raise self.retry(exc=exc)


async def _deliver_webhook_async(
    webhook_id: str,
    event_type: str,
    payload: dict[str, Any],
) -> dict[str, Any]:
    """Perform the async webhook delivery within a database session."""
    from app.database import get_db_context
    from app.services import webhook_service

    async with get_db_context() as session:
        delivery = await webhook_service.deliver_webhook(
            db=session,
            webhook_id=uuid.UUID(webhook_id),
            event_type=event_type,
            payload=payload,
        )
        return {
            "delivery_id": str(delivery.id),
            "webhook_id": webhook_id,
            "event_type": event_type,
            "status": delivery.status.value,
            "status_code": delivery.status_code,
            "attempt_number": delivery.attempt_number,
            "error_message": delivery.error_message,
        }


# ── Retry Failed Webhooks (Periodic) ────────────────────────────────────


@celery_app.task(
    name="app.workers.webhook_tasks.retry_failed_webhooks",
    bind=True,
    max_retries=2,
    default_retry_delay=60,
    queue="alerts",
)
def retry_failed_webhooks(self) -> dict[str, Any]:
    """Periodic task to retry failed webhook deliveries.

    Finds all deliveries in 'retrying' status whose next_retry_at has
    passed and attempts redelivery with exponential backoff.

    Returns:
        Dict with the number of retried deliveries.
    """
    log = logger.bind(task_id=self.request.id)
    log.info("Starting failed webhook retry batch")

    try:
        retried_count = _run_async(_retry_failed_async())
        log.info("Failed webhook retry batch completed", retried=retried_count)
        return {
            "status": "completed",
            "retried_count": retried_count,
        }

    except Exception as exc:
        log.error("Failed webhook retry batch failed", error=str(exc))
        raise self.retry(exc=exc)


async def _retry_failed_async() -> int:
    """Perform the async retry of failed deliveries."""
    from app.database import get_db_context
    from app.services import webhook_service

    async with get_db_context() as session:
        return await webhook_service.retry_failed_deliveries(session)


# ── Cleanup Old Deliveries (Periodic) ────────────────────────────────────


@celery_app.task(
    name="app.workers.webhook_tasks.cleanup_old_deliveries",
    bind=True,
    max_retries=2,
    default_retry_delay=120,
    queue="maintenance",
)
def cleanup_old_deliveries(self, days: int = 30) -> dict[str, Any]:
    """Cleanup old delivery log records beyond the retention period.

    Removes webhook delivery records older than the specified number
    of days to prevent unbounded table growth.

    Args:
        days: Retention period in days (default: 30).

    Returns:
        Dict with the number of deleted records.
    """
    log = logger.bind(task_id=self.request.id, retention_days=days)
    log.info("Starting delivery log cleanup")

    try:
        deleted_count = _run_async(_cleanup_deliveries_async(days))
        log.info("Delivery log cleanup completed", deleted=deleted_count)
        return {
            "status": "completed",
            "deleted_count": deleted_count,
            "retention_days": days,
        }

    except Exception as exc:
        log.error("Delivery log cleanup failed", error=str(exc))
        raise self.retry(exc=exc)


async def _cleanup_deliveries_async(days: int) -> int:
    """Delete delivery records older than the retention period."""
    from app.database import get_db_context
    from app.models.integration import WebhookDelivery

    cutoff = datetime.now(timezone.utc) - timedelta(days=days)

    async with get_db_context() as session:
        # Count first for logging
        count_q = (
            select(WebhookDelivery.id)
            .where(WebhookDelivery.created_at < cutoff)
        )
        count_result = await session.execute(count_q)
        record_ids = [row[0] for row in count_result.all()]

        if not record_ids:
            return 0

        # Delete in batches of 1000 to avoid lock contention
        total_deleted = 0
        batch_size = 1000

        for i in range(0, len(record_ids), batch_size):
            batch = record_ids[i : i + batch_size]
            del_q = delete(WebhookDelivery).where(
                WebhookDelivery.id.in_(batch)
            )
            result = await session.execute(del_q)
            total_deleted += result.rowcount
            await session.flush()

        logger.info(
            "Old delivery records cleaned up",
            deleted=total_deleted,
            cutoff=cutoff.isoformat(),
        )
        return total_deleted


# ── Dispatch Integration Event ───────────────────────────────────────────


@celery_app.task(
    name="app.workers.webhook_tasks.dispatch_integration_event",
    bind=True,
    max_retries=3,
    default_retry_delay=15,
    queue="alerts",
)
def dispatch_integration_event(
    self,
    org_id: str,
    event_type: str,
    payload: dict[str, Any],
) -> dict[str, Any]:
    """Dispatch an event to all active third-party integrations.

    Fans out the event to Slack, Teams, PagerDuty, Jira, and any
    other configured integrations for the organization.

    Args:
        org_id: UUID of the organization.
        event_type: The event type string.
        payload: The event payload.

    Returns:
        Dict with dispatch results per integration.
    """
    log = logger.bind(
        org_id=org_id,
        event_type=event_type,
        task_id=self.request.id,
    )
    log.info("Dispatching event to integrations")

    try:
        result = _run_async(
            _dispatch_integration_event_async(org_id, event_type, payload)
        )
        log.info(
            "Integration event dispatch completed",
            integrations_notified=result.get("integrations_notified", 0),
            successful=result.get("successful", 0),
        )
        return result

    except Exception as exc:
        log.error("Integration event dispatch failed", error=str(exc))
        raise self.retry(exc=exc)


async def _dispatch_integration_event_async(
    org_id: str,
    event_type: str,
    payload: dict[str, Any],
) -> dict[str, Any]:
    """Perform the async integration event dispatch."""
    from app.database import get_db_context
    from app.services.integration_service import IntegrationManager

    async with get_db_context() as session:
        manager = IntegrationManager(session)
        return await manager.route_event(
            org_id=uuid.UUID(org_id),
            event_type=event_type,
            payload=payload,
        )


# ── Combined Event Dispatch (Webhooks + Integrations) ────────────────────


@celery_app.task(
    name="app.workers.webhook_tasks.dispatch_event_to_all",
    bind=True,
    max_retries=3,
    default_retry_delay=10,
    queue="alerts",
)
def dispatch_event_to_all(
    self,
    org_id: str,
    event_type: str,
    payload: dict[str, Any],
) -> dict[str, Any]:
    """Dispatch an event to both webhooks and integrations.

    This is the main entry point for event dispatch. It fans out
    the event to all matching webhooks and all active third-party
    integrations for the organization.

    Args:
        org_id: UUID of the organization.
        event_type: The event type string.
        payload: The event payload.

    Returns:
        Dict with combined dispatch results.
    """
    log = logger.bind(
        org_id=org_id,
        event_type=event_type,
        task_id=self.request.id,
    )
    log.info("Dispatching event to webhooks and integrations")

    try:
        result = _run_async(
            _dispatch_event_to_all_async(org_id, event_type, payload)
        )
        log.info(
            "Full event dispatch completed",
            webhook_deliveries=result.get("webhook_deliveries", 0),
            integration_results=result.get("integration_results", {}),
        )
        return result

    except Exception as exc:
        log.error("Full event dispatch failed", error=str(exc))
        raise self.retry(exc=exc)


async def _dispatch_event_to_all_async(
    org_id: str,
    event_type: str,
    payload: dict[str, Any],
) -> dict[str, Any]:
    """Perform the async dispatch to both webhooks and integrations."""
    from app.database import get_db_context
    from app.services import webhook_service
    from app.services.integration_service import IntegrationManager

    org_uuid = uuid.UUID(org_id)

    async with get_db_context() as session:
        # Dispatch to webhooks
        webhook_deliveries = await webhook_service.dispatch_event(
            db=session,
            org_id=org_uuid,
            event_type=event_type,
            payload=payload,
        )

        # Dispatch to integrations
        manager = IntegrationManager(session)
        integration_results = await manager.route_event(
            org_id=org_uuid,
            event_type=event_type,
            payload=payload,
        )

        return {
            "webhook_deliveries": len(webhook_deliveries),
            "webhook_statuses": [
                {
                    "delivery_id": str(d.id),
                    "webhook_id": str(d.webhook_id),
                    "status": d.status.value,
                }
                for d in webhook_deliveries
            ],
            "integration_results": integration_results,
        }
