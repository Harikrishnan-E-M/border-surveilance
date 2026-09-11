"""Webhook lifecycle management and delivery service.

Handles webhook endpoint CRUD, HMAC-SHA256 payload signing, HTTP
delivery with timeout handling, exponential backoff retries, delivery
log querying, and fan-out event dispatching to all matching endpoints.
"""

from __future__ import annotations

import hashlib
import hmac
import json
import math
import secrets
import uuid
from datetime import datetime, timedelta, timezone
from typing import Any, Optional

import httpx
import structlog
from sqlalchemy import and_, func, select
from sqlalchemy.ext.asyncio import AsyncSession

from app.exceptions import NotFoundError, ValidationError
from app.models.integration import (
    DeliveryStatus,
    EventType,
    WebhookDelivery,
    WebhookEndpoint,
    EVENT_TYPE_EXAMPLE_PAYLOADS,
)

logger = structlog.stdlib.get_logger(__name__)


# ── Secret Generation ────────────────────────────────────────────────────


def generate_webhook_secret() -> str:
    """Generate a cryptographically secure webhook signing secret.

    Returns:
        A 64-character hex string suitable for HMAC-SHA256 signing.
    """
    return f"whsec_{secrets.token_hex(32)}"


# ── HMAC Signing ─────────────────────────────────────────────────────────


def sign_payload(payload: str, secret: str) -> str:
    """Compute an HMAC-SHA256 signature over a JSON payload string.

    Args:
        payload: The JSON-encoded payload body as a string.
        secret: The webhook endpoint's signing secret.

    Returns:
        Hex-encoded HMAC-SHA256 digest prefixed with ``sha256=``.
    """
    signature = hmac.new(
        secret.encode("utf-8"),
        payload.encode("utf-8"),
        hashlib.sha256,
    ).hexdigest()
    return f"sha256={signature}"


def verify_signature(payload: str, secret: str, signature: str) -> bool:
    """Verify an HMAC-SHA256 signature against a payload.

    Args:
        payload: The JSON-encoded payload body.
        secret: The webhook endpoint's signing secret.
        signature: The signature to verify (``sha256=...`` format).

    Returns:
        True if the signature is valid.
    """
    expected = sign_payload(payload, secret)
    return hmac.compare_digest(expected, signature)


# ── Webhook CRUD ─────────────────────────────────────────────────────────


async def create_webhook(
    db: AsyncSession,
    org_id: uuid.UUID,
    data: dict[str, Any],
    created_by: uuid.UUID | None = None,
) -> WebhookEndpoint:
    """Create a new webhook endpoint with an auto-generated HMAC secret.

    Args:
        db: Async database session.
        org_id: Organization that owns the webhook.
        data: Webhook configuration from the request body.
        created_by: User ID of the creator.

    Returns:
        The newly created WebhookEndpoint instance.
    """
    retry_policy = data.get("retry_policy", {})
    if isinstance(retry_policy, dict):
        retry_dict = {
            "max_retries": retry_policy.get("max_retries", 5),
            "backoff_seconds": retry_policy.get("backoff_seconds", 30),
        }
    else:
        retry_dict = {
            "max_retries": retry_policy.max_retries,
            "backoff_seconds": retry_policy.backoff_seconds,
        }

    webhook = WebhookEndpoint(
        id=uuid.uuid4(),
        org_id=org_id,
        name=data["name"],
        url=data["url"],
        secret=generate_webhook_secret(),
        events=data["events"],
        headers=data.get("headers"),
        is_active=True,
        retry_policy=retry_dict,
        created_by=created_by,
    )
    db.add(webhook)
    await db.flush()

    logger.info(
        "Webhook endpoint created",
        webhook_id=str(webhook.id),
        name=webhook.name,
        events=webhook.events,
        org_id=str(org_id),
    )
    return webhook


async def update_webhook(
    db: AsyncSession,
    webhook_id: uuid.UUID,
    data: dict[str, Any],
    org_id: uuid.UUID | None = None,
) -> WebhookEndpoint:
    """Update an existing webhook endpoint configuration.

    Args:
        db: Async database session.
        webhook_id: ID of the webhook to update.
        data: Fields to update.
        org_id: Optional organization filter for access control.

    Returns:
        The updated WebhookEndpoint instance.

    Raises:
        NotFoundError: If the webhook does not exist.
    """
    webhook = await _get_webhook(db, webhook_id, org_id)

    if "name" in data and data["name"] is not None:
        webhook.name = data["name"]
    if "url" in data and data["url"] is not None:
        webhook.url = data["url"]
    if "events" in data and data["events"] is not None:
        webhook.events = data["events"]
    if "headers" in data:
        webhook.headers = data["headers"]
    if "is_active" in data and data["is_active"] is not None:
        webhook.is_active = data["is_active"]
    if "retry_policy" in data and data["retry_policy"] is not None:
        rp = data["retry_policy"]
        if isinstance(rp, dict):
            webhook.retry_policy = rp
        else:
            webhook.retry_policy = {
                "max_retries": rp.max_retries,
                "backoff_seconds": rp.backoff_seconds,
            }

    await db.flush()

    logger.info("Webhook endpoint updated", webhook_id=str(webhook_id))
    return webhook


async def delete_webhook(
    db: AsyncSession,
    webhook_id: uuid.UUID,
    org_id: uuid.UUID | None = None,
) -> WebhookEndpoint:
    """Soft-delete a webhook endpoint.

    Args:
        db: Async database session.
        webhook_id: ID of the webhook to delete.
        org_id: Optional organization filter.

    Returns:
        The soft-deleted WebhookEndpoint instance.

    Raises:
        NotFoundError: If the webhook does not exist.
    """
    webhook = await _get_webhook(db, webhook_id, org_id)
    webhook.is_deleted = True
    webhook.is_active = False
    await db.flush()

    logger.info("Webhook endpoint soft-deleted", webhook_id=str(webhook_id))
    return webhook


async def list_webhooks(
    db: AsyncSession,
    org_id: uuid.UUID,
    page: int = 1,
    page_size: int = 20,
) -> tuple[list[WebhookEndpoint], int]:
    """List all active (non-deleted) webhooks for an organization.

    Args:
        db: Async database session.
        org_id: Organization to list webhooks for.
        page: Page number (1-indexed).
        page_size: Number of items per page.

    Returns:
        Tuple of (webhook list, total count).
    """
    base_filter = and_(
        WebhookEndpoint.org_id == org_id,
        WebhookEndpoint.is_deleted.is_(False),
    )

    count_q = select(func.count()).select_from(WebhookEndpoint).where(base_filter)
    total = (await db.execute(count_q)).scalar() or 0

    query = (
        select(WebhookEndpoint)
        .where(base_filter)
        .order_by(WebhookEndpoint.created_at.desc())
        .offset((page - 1) * page_size)
        .limit(page_size)
    )
    result = await db.execute(query)
    webhooks = list(result.scalars().all())

    return webhooks, total


async def get_webhook(
    db: AsyncSession,
    webhook_id: uuid.UUID,
    org_id: uuid.UUID | None = None,
) -> WebhookEndpoint:
    """Retrieve a single webhook endpoint by ID.

    Args:
        db: Async database session.
        webhook_id: Webhook ID to retrieve.
        org_id: Optional organization filter.

    Returns:
        The WebhookEndpoint instance.

    Raises:
        NotFoundError: If the webhook does not exist.
    """
    return await _get_webhook(db, webhook_id, org_id)


# ── Webhook Delivery ─────────────────────────────────────────────────────


async def deliver_webhook(
    db: AsyncSession,
    webhook_id: uuid.UUID,
    event_type: str,
    payload: dict[str, Any],
) -> WebhookDelivery:
    """Deliver a webhook event to its endpoint via HTTP POST.

    Sends the payload with HMAC-SHA256 signature, tracks the delivery
    attempt, and schedules a retry on failure.

    Args:
        db: Async database session.
        webhook_id: Target webhook endpoint ID.
        event_type: The event type triggering this delivery.
        payload: JSON-serializable event payload.

    Returns:
        The WebhookDelivery record.
    """
    webhook = await _get_webhook(db, webhook_id)

    delivery = WebhookDelivery(
        id=uuid.uuid4(),
        webhook_id=webhook.id,
        event_type=event_type,
        payload=payload,
        status=DeliveryStatus.PENDING,
        attempt_number=1,
    )
    db.add(delivery)
    await db.flush()

    log = logger.bind(
        delivery_id=str(delivery.id),
        webhook_id=str(webhook.id),
        event_type=event_type,
    )

    body = json.dumps(payload, separators=(",", ":"), sort_keys=True, default=str)
    signature = sign_payload(body, webhook.secret)

    request_headers = {
        "Content-Type": "application/json",
        "User-Agent": "VisionAI-Webhook/1.0",
        "X-VisionAI-Signature": signature,
        "X-VisionAI-Event": event_type,
        "X-VisionAI-Delivery": str(delivery.id),
        "X-VisionAI-Timestamp": datetime.now(timezone.utc).isoformat(),
    }

    if webhook.headers:
        request_headers.update(webhook.headers)

    try:
        async with httpx.AsyncClient(timeout=30.0) as client:
            response = await client.post(
                webhook.url,
                content=body,
                headers=request_headers,
            )

        delivery.status_code = response.status_code
        delivery.response_body = response.text[:4096] if response.text else None

        if 200 <= response.status_code < 300:
            delivery.status = DeliveryStatus.SUCCESS
            delivery.completed_at = datetime.now(timezone.utc)
            log.info("Webhook delivered successfully", status_code=response.status_code)
        else:
            delivery.status = DeliveryStatus.FAILED
            delivery.error_message = f"HTTP {response.status_code}: {response.text[:500]}"
            _schedule_retry(delivery, webhook)
            log.warning(
                "Webhook delivery failed with HTTP error",
                status_code=response.status_code,
            )

    except httpx.TimeoutException:
        delivery.status = DeliveryStatus.FAILED
        delivery.error_message = "Request timed out after 30 seconds"
        _schedule_retry(delivery, webhook)
        log.warning("Webhook delivery timed out")

    except httpx.ConnectError as exc:
        delivery.status = DeliveryStatus.FAILED
        delivery.error_message = f"Connection error: {str(exc)[:500]}"
        _schedule_retry(delivery, webhook)
        log.warning("Webhook delivery connection error", error=str(exc))

    except Exception as exc:
        delivery.status = DeliveryStatus.FAILED
        delivery.error_message = f"Unexpected error: {str(exc)[:500]}"
        _schedule_retry(delivery, webhook)
        log.error("Webhook delivery unexpected error", error=str(exc))

    await db.flush()
    return delivery


async def verify_delivery(
    db: AsyncSession,
    delivery_id: uuid.UUID,
) -> WebhookDelivery:
    """Check the status of a specific webhook delivery.

    Args:
        db: Async database session.
        delivery_id: Delivery record ID.

    Returns:
        The WebhookDelivery record.

    Raises:
        NotFoundError: If the delivery does not exist.
    """
    result = await db.execute(
        select(WebhookDelivery).where(WebhookDelivery.id == delivery_id)
    )
    delivery = result.scalar_one_or_none()
    if delivery is None:
        raise NotFoundError(resource="WebhookDelivery", identifier=delivery_id)
    return delivery


async def retry_delivery(
    db: AsyncSession,
    delivery_id: uuid.UUID,
    webhook_id: uuid.UUID | None = None,
) -> WebhookDelivery:
    """Manually retry a failed webhook delivery.

    Args:
        db: Async database session.
        delivery_id: The delivery to retry.
        webhook_id: Optional webhook ID for access control validation.

    Returns:
        The newly created retry delivery record.

    Raises:
        NotFoundError: If the delivery does not exist.
        ValidationError: If the delivery is not in a retryable state.
    """
    query = select(WebhookDelivery).where(WebhookDelivery.id == delivery_id)
    if webhook_id:
        query = query.where(WebhookDelivery.webhook_id == webhook_id)

    result = await db.execute(query)
    delivery = result.scalar_one_or_none()
    if delivery is None:
        raise NotFoundError(resource="WebhookDelivery", identifier=delivery_id)

    if delivery.status not in (DeliveryStatus.FAILED, DeliveryStatus.RETRYING):
        raise ValidationError(
            message=f"Cannot retry delivery with status '{delivery.status.value}'. "
            "Only failed or retrying deliveries can be retried.",
            code="DELIVERY_NOT_RETRYABLE",
        )

    return await deliver_webhook(
        db=db,
        webhook_id=delivery.webhook_id,
        event_type=delivery.event_type,
        payload=delivery.payload,
    )


async def retry_failed_deliveries(db: AsyncSession) -> int:
    """Retry all failed deliveries whose next_retry_at has passed.

    Uses exponential backoff: each retry doubles the wait interval.

    Args:
        db: Async database session.

    Returns:
        Number of deliveries retried.
    """
    now = datetime.now(timezone.utc)

    query = (
        select(WebhookDelivery)
        .where(
            and_(
                WebhookDelivery.status == DeliveryStatus.RETRYING,
                WebhookDelivery.next_retry_at <= now,
            )
        )
        .order_by(WebhookDelivery.next_retry_at.asc())
        .limit(100)
    )
    result = await db.execute(query)
    deliveries = list(result.scalars().all())

    retried = 0
    for delivery in deliveries:
        try:
            webhook = await _get_webhook(db, delivery.webhook_id)
            if not webhook.is_active or webhook.is_deleted:
                delivery.status = DeliveryStatus.FAILED
                delivery.error_message = "Webhook endpoint deactivated or deleted"
                delivery.completed_at = now
                await db.flush()
                continue

            body = json.dumps(
                delivery.payload, separators=(",", ":"), sort_keys=True, default=str
            )
            signature = sign_payload(body, webhook.secret)

            request_headers = {
                "Content-Type": "application/json",
                "User-Agent": "VisionAI-Webhook/1.0",
                "X-VisionAI-Signature": signature,
                "X-VisionAI-Event": delivery.event_type,
                "X-VisionAI-Delivery": str(delivery.id),
                "X-VisionAI-Timestamp": now.isoformat(),
                "X-VisionAI-Retry": str(delivery.attempt_number),
            }
            if webhook.headers:
                request_headers.update(webhook.headers)

            async with httpx.AsyncClient(timeout=30.0) as client:
                response = await client.post(
                    webhook.url,
                    content=body,
                    headers=request_headers,
                )

            delivery.attempt_number += 1
            delivery.status_code = response.status_code
            delivery.response_body = response.text[:4096] if response.text else None

            if 200 <= response.status_code < 300:
                delivery.status = DeliveryStatus.SUCCESS
                delivery.completed_at = now
                delivery.next_retry_at = None
                logger.info(
                    "Retry delivery succeeded",
                    delivery_id=str(delivery.id),
                    attempt=delivery.attempt_number,
                )
            else:
                delivery.error_message = f"HTTP {response.status_code}"
                _schedule_retry(delivery, webhook)

            retried += 1

        except Exception as exc:
            delivery.attempt_number += 1
            delivery.error_message = f"Retry failed: {str(exc)[:500]}"
            try:
                webhook = await _get_webhook(db, delivery.webhook_id)
                _schedule_retry(delivery, webhook)
            except NotFoundError:
                delivery.status = DeliveryStatus.FAILED
                delivery.completed_at = now

            logger.error(
                "Retry delivery failed",
                delivery_id=str(delivery.id),
                error=str(exc),
            )

        await db.flush()

    logger.info("Failed delivery retry batch completed", retried=retried)
    return retried


async def get_delivery_log(
    db: AsyncSession,
    webhook_id: uuid.UUID,
    page: int = 1,
    page_size: int = 20,
    status_filter: str | None = None,
    event_type_filter: str | None = None,
    start_date: datetime | None = None,
    end_date: datetime | None = None,
) -> tuple[list[WebhookDelivery], int]:
    """Retrieve paginated delivery history for a webhook endpoint.

    Args:
        db: Async database session.
        webhook_id: Webhook to list deliveries for.
        page: Page number.
        page_size: Items per page.
        status_filter: Optional delivery status filter.
        event_type_filter: Optional event type filter.
        start_date: Optional start date filter.
        end_date: Optional end date filter.

    Returns:
        Tuple of (delivery list, total count).
    """
    conditions = [WebhookDelivery.webhook_id == webhook_id]

    if status_filter:
        try:
            ds = DeliveryStatus(status_filter)
            conditions.append(WebhookDelivery.status == ds)
        except ValueError:
            pass

    if event_type_filter:
        conditions.append(WebhookDelivery.event_type == event_type_filter)

    if start_date:
        conditions.append(WebhookDelivery.created_at >= start_date)
    if end_date:
        conditions.append(WebhookDelivery.created_at <= end_date)

    base = and_(*conditions)

    count_q = select(func.count()).select_from(WebhookDelivery).where(base)
    total = (await db.execute(count_q)).scalar() or 0

    query = (
        select(WebhookDelivery)
        .where(base)
        .order_by(WebhookDelivery.created_at.desc())
        .offset((page - 1) * page_size)
        .limit(page_size)
    )
    result = await db.execute(query)
    deliveries = list(result.scalars().all())

    return deliveries, total


# ── Event Dispatch ───────────────────────────────────────────────────────


async def dispatch_event(
    db: AsyncSession,
    org_id: uuid.UUID,
    event_type: str,
    payload: dict[str, Any],
) -> list[WebhookDelivery]:
    """Fan out an event to all matching active webhooks for an organization.

    Finds all active, non-deleted webhooks subscribed to the given
    event type and creates a delivery for each.

    Args:
        db: Async database session.
        org_id: Organization to dispatch for.
        event_type: The event type string.
        payload: The event payload.

    Returns:
        List of created WebhookDelivery records.
    """
    query = select(WebhookEndpoint).where(
        and_(
            WebhookEndpoint.org_id == org_id,
            WebhookEndpoint.is_active.is_(True),
            WebhookEndpoint.is_deleted.is_(False),
        )
    )
    result = await db.execute(query)
    webhooks = list(result.scalars().all())

    matching = [w for w in webhooks if event_type in (w.events or [])]

    deliveries: list[WebhookDelivery] = []
    for webhook in matching:
        try:
            delivery = await deliver_webhook(
                db=db,
                webhook_id=webhook.id,
                event_type=event_type,
                payload=payload,
            )
            deliveries.append(delivery)
        except Exception as exc:
            logger.error(
                "Failed to dispatch event to webhook",
                webhook_id=str(webhook.id),
                event_type=event_type,
                error=str(exc),
            )

    logger.info(
        "Event dispatched to webhooks",
        org_id=str(org_id),
        event_type=event_type,
        total_webhooks=len(matching),
        deliveries_created=len(deliveries),
    )

    return deliveries


async def send_test_delivery(
    db: AsyncSession,
    webhook_id: uuid.UUID,
    event_type: str = "alert.created",
    org_id: uuid.UUID | None = None,
) -> dict[str, Any]:
    """Send a test delivery to a webhook endpoint.

    Uses the example payload for the specified event type.

    Args:
        db: Async database session.
        webhook_id: Webhook to test.
        event_type: Event type to simulate.
        org_id: Optional organization filter.

    Returns:
        Dict with delivery result details.
    """
    webhook = await _get_webhook(db, webhook_id, org_id)

    test_payload = {
        "event": event_type,
        "timestamp": datetime.now(timezone.utc).isoformat(),
        "test": True,
        "data": EVENT_TYPE_EXAMPLE_PAYLOADS.get(event_type, {}).get("data", {}),
    }

    delivery = await deliver_webhook(
        db=db,
        webhook_id=webhook.id,
        event_type=event_type,
        payload=test_payload,
    )

    return {
        "delivery_id": str(delivery.id),
        "status": delivery.status.value,
        "status_code": delivery.status_code,
        "response_body": delivery.response_body[:500] if delivery.response_body else None,
        "error_message": delivery.error_message,
    }


# ── Event Type Subscriber Counts ─────────────────────────────────────────


async def get_event_subscriber_counts(
    db: AsyncSession,
    org_id: uuid.UUID,
) -> dict[str, int]:
    """Count active webhook subscriptions per event type.

    Args:
        db: Async database session.
        org_id: Organization to count for.

    Returns:
        Dict mapping event type strings to subscriber counts.
    """
    query = select(WebhookEndpoint.events).where(
        and_(
            WebhookEndpoint.org_id == org_id,
            WebhookEndpoint.is_active.is_(True),
            WebhookEndpoint.is_deleted.is_(False),
        )
    )
    result = await db.execute(query)
    rows = result.scalars().all()

    counts: dict[str, int] = {}
    for events_list in rows:
        if events_list:
            for event in events_list:
                counts[event] = counts.get(event, 0) + 1

    return counts


# ── Helpers ──────────────────────────────────────────────────────────────


def _schedule_retry(delivery: WebhookDelivery, webhook: WebhookEndpoint) -> None:
    """Schedule a retry for a failed delivery with exponential backoff.

    If the maximum number of retries has been reached, the delivery is
    marked as permanently failed.

    Args:
        delivery: The failed delivery record.
        webhook: The parent webhook endpoint (for retry policy).
    """
    policy = webhook.retry_policy or {"max_retries": 5, "backoff_seconds": 30}
    max_retries = policy.get("max_retries", 5)
    backoff_base = policy.get("backoff_seconds", 30)

    if delivery.attempt_number >= max_retries:
        delivery.status = DeliveryStatus.FAILED
        delivery.completed_at = datetime.now(timezone.utc)
        delivery.next_retry_at = None
        logger.warning(
            "Webhook delivery exhausted retries",
            delivery_id=str(delivery.id),
            attempts=delivery.attempt_number,
        )
        return

    # Exponential backoff: backoff_base * 2^(attempt-1)
    delay_seconds = backoff_base * (2 ** (delivery.attempt_number - 1))
    # Cap at 1 hour
    delay_seconds = min(delay_seconds, 3600)

    delivery.status = DeliveryStatus.RETRYING
    delivery.next_retry_at = datetime.now(timezone.utc) + timedelta(seconds=delay_seconds)

    logger.info(
        "Webhook delivery scheduled for retry",
        delivery_id=str(delivery.id),
        attempt=delivery.attempt_number,
        next_retry_at=delivery.next_retry_at.isoformat(),
        delay_seconds=delay_seconds,
    )


async def _get_webhook(
    db: AsyncSession,
    webhook_id: uuid.UUID,
    org_id: uuid.UUID | None = None,
) -> WebhookEndpoint:
    """Retrieve a webhook by ID with optional org filter.

    Excludes soft-deleted webhooks.

    Raises:
        NotFoundError: If the webhook does not exist.
    """
    conditions = [
        WebhookEndpoint.id == webhook_id,
        WebhookEndpoint.is_deleted.is_(False),
    ]
    if org_id:
        conditions.append(WebhookEndpoint.org_id == org_id)

    result = await db.execute(
        select(WebhookEndpoint).where(and_(*conditions))
    )
    webhook = result.scalar_one_or_none()

    if webhook is None:
        raise NotFoundError(resource="WebhookEndpoint", identifier=webhook_id)

    return webhook
