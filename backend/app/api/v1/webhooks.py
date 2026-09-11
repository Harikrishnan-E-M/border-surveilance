"""
Standalone Webhook Management API endpoints.

Provides full CRUD for webhook endpoints (v2 model with Fernet-
encrypted secrets), delivery log access with filtering and retry
support, event type catalogue, per-endpoint statistics, and test
delivery dispatch.
"""

from __future__ import annotations

import hashlib
import hmac
import math
import secrets
import time
import uuid
from datetime import datetime, timedelta, timezone
from typing import Any

import httpx
import structlog
from fastapi import APIRouter, Depends, HTTPException, Query, status
from sqlalchemy import and_, delete, func, select
from sqlalchemy.ext.asyncio import AsyncSession

from app.database import get_db_session
from app.middleware.auth import JWTBearer, TokenPayload
from app.models.user import User, UserRole
from app.models.webhook import (
    WEBHOOK_EVENT_DESCRIPTIONS,
    WEBHOOK_EVENT_EXAMPLE_PAYLOADS,
    WebhookDeliveryStatus,
    WebhookDeliveryV2,
    WebhookEndpointV2,
    WebhookEventType,
)
from app.schemas.common import ErrorResponse, SuccessResponse
from app.schemas.webhook import (
    EventTypeStats,
    WebhookCreate,
    WebhookDeliveryResponse,
    WebhookEventInfo,
    WebhookResponse,
    WebhookStats,
    WebhookTestRequest,
    WebhookTestResponse,
    WebhookUpdate,
)
from app.utils.encryption import decrypt_string, encrypt_string

logger = structlog.stdlib.get_logger(__name__)

router = APIRouter()


# ── Helpers ──────────────────────────────────────────────────────────────


async def _get_current_user(
    token: TokenPayload = Depends(JWTBearer()),
    db: AsyncSession = Depends(get_db_session),
) -> User:
    """Resolve the current authenticated user."""
    result = await db.execute(
        select(User).where(
            User.id == uuid.UUID(token.sub),
            User.is_active.is_(True),
        )
    )
    user = result.scalars().first()
    if not user:
        raise HTTPException(status_code=401, detail="User not found or deactivated.")
    return user


def _require_admin(user: User) -> None:
    """Raise 403 if the user lacks admin/manager privileges."""
    allowed = {UserRole.SUPER_ADMIN, UserRole.ORG_ADMIN, UserRole.MANAGER}
    if user.role not in allowed:
        raise HTTPException(
            status_code=status.HTTP_403_FORBIDDEN,
            detail="Admin or manager role required for webhook management.",
        )


def _webhook_to_response(
    webhook: WebhookEndpointV2,
    *,
    include_secret: bool = False,
) -> dict[str, Any]:
    """Serialize a WebhookEndpointV2 model to a response dict."""
    resp = WebhookResponse(
        id=webhook.id,
        org_id=webhook.org_id,
        name=webhook.name,
        url=webhook.url,
        events=webhook.events or [],
        headers=webhook.headers_json,
        is_active=webhook.is_active,
        retry_count=webhook.retry_count,
        timeout=webhook.timeout_seconds,
        created_by=webhook.created_by,
        created_at=webhook.created_at,
        updated_at=webhook.updated_at,
    )
    data = resp.model_dump(mode="json")
    if include_secret:
        try:
            data["secret_plaintext"] = decrypt_string(webhook.secret)
        except Exception:
            data["secret_plaintext"] = None
    return data


def _delivery_to_response(delivery: WebhookDeliveryV2) -> dict[str, Any]:
    """Serialize a WebhookDeliveryV2 model to a response dict."""
    return WebhookDeliveryResponse(
        id=delivery.id,
        webhook_id=delivery.webhook_id,
        event_type=delivery.event_type,
        payload_json=delivery.payload_json,
        status=(
            delivery.status.value
            if hasattr(delivery.status, "value")
            else delivery.status
        ),
        status_code=delivery.status_code,
        response_body=delivery.response_body,
        attempts=delivery.attempts,
        next_retry_at=delivery.next_retry_at,
        error_message=delivery.error_message,
        duration_ms=delivery.duration_ms,
        created_at=delivery.created_at,
        delivered_at=delivery.delivered_at,
    ).model_dump(mode="json")


async def _get_webhook_or_404(
    db: AsyncSession,
    webhook_id: uuid.UUID,
    org_id: uuid.UUID,
) -> WebhookEndpointV2:
    """Load a webhook endpoint by ID, scoped to the organization."""
    result = await db.execute(
        select(WebhookEndpointV2).where(
            WebhookEndpointV2.id == webhook_id,
            WebhookEndpointV2.org_id == org_id,
        )
    )
    webhook = result.scalars().first()
    if not webhook:
        raise HTTPException(
            status_code=status.HTTP_404_NOT_FOUND,
            detail="Webhook endpoint not found.",
        )
    return webhook


# ── POST /webhooks ───────────────────────────────────────────────────────


@router.post(
    "/",
    response_model=SuccessResponse,
    status_code=status.HTTP_201_CREATED,
    summary="Create a new webhook endpoint",
    responses={403: {"model": ErrorResponse}, 422: {"model": ErrorResponse}},
)
async def create_webhook(
    body: WebhookCreate,
    user: User = Depends(_get_current_user),
    db: AsyncSession = Depends(get_db_session),
) -> dict:
    """Register a new webhook endpoint.

    If ``secret`` is not provided, a cryptographically random secret is
    generated.  The secret is encrypted with Fernet before storage and
    returned in plaintext exactly once in the response.
    """
    _require_admin(user)

    # Generate or use provided secret
    raw_secret = body.secret or secrets.token_urlsafe(32)
    encrypted_secret = encrypt_string(raw_secret)

    webhook = WebhookEndpointV2(
        org_id=user.org_id,
        name=body.name,
        url=body.url,
        secret=encrypted_secret,
        events=body.events,
        headers_json=body.headers,
        is_active=True,
        retry_count=body.retry_count,
        timeout_seconds=body.timeout,
        created_by=user.id,
    )
    db.add(webhook)
    await db.flush()
    await db.refresh(webhook)

    logger.info(
        "Webhook endpoint created",
        webhook_id=str(webhook.id),
        user_id=str(user.id),
    )

    data = _webhook_to_response(webhook)
    data["secret_plaintext"] = raw_secret

    return {
        "status": "success",
        "data": data,
        "message": "Webhook endpoint created successfully.",
    }


# ── GET /webhooks ────────────────────────────────────────────────────────


@router.get(
    "/",
    response_model=SuccessResponse,
    summary="List webhook endpoints",
)
async def list_webhooks(
    page: int = Query(1, ge=1),
    page_size: int = Query(20, ge=1, le=100),
    is_active: bool | None = Query(None, description="Filter by active status"),
    user: User = Depends(_get_current_user),
    db: AsyncSession = Depends(get_db_session),
) -> dict:
    """Return a paginated list of webhook endpoints for the organization."""
    _require_admin(user)

    query = select(WebhookEndpointV2).where(
        WebhookEndpointV2.org_id == user.org_id,
    )
    count_query = select(func.count(WebhookEndpointV2.id)).where(
        WebhookEndpointV2.org_id == user.org_id,
    )

    if is_active is not None:
        query = query.where(WebhookEndpointV2.is_active == is_active)
        count_query = count_query.where(WebhookEndpointV2.is_active == is_active)

    total_result = await db.execute(count_query)
    total = total_result.scalar() or 0

    offset = (page - 1) * page_size
    query = query.order_by(WebhookEndpointV2.created_at.desc())
    query = query.offset(offset).limit(page_size)

    result = await db.execute(query)
    webhooks = result.scalars().all()

    return {
        "status": "success",
        "data": [_webhook_to_response(w) for w in webhooks],
        "meta": {
            "page": page,
            "page_size": page_size,
            "total": total,
            "total_pages": math.ceil(total / page_size) if page_size else 0,
        },
    }


# ── GET /webhooks/events ─────────────────────────────────────────────────


@router.get(
    "/events",
    response_model=SuccessResponse,
    summary="List available webhook event types",
)
async def list_event_types(
    user: User = Depends(_get_current_user),
    db: AsyncSession = Depends(get_db_session),
) -> dict:
    """Return all available event types with descriptions and example payloads."""
    _require_admin(user)

    # Count subscribers per event type
    subscriber_counts: dict[str, int] = {}
    for et in WebhookEventType:
        count_q = select(func.count(WebhookEndpointV2.id)).where(
            WebhookEndpointV2.org_id == user.org_id,
            WebhookEndpointV2.is_active.is_(True),
            WebhookEndpointV2.events.any(et.value),
        )
        count_result = await db.execute(count_q)
        subscriber_counts[et.value] = count_result.scalar() or 0

    event_types = []
    for et in WebhookEventType:
        event_types.append(
            WebhookEventInfo(
                event_type=et.value,
                description=WEBHOOK_EVENT_DESCRIPTIONS.get(et.value, ""),
                example_payload=WEBHOOK_EVENT_EXAMPLE_PAYLOADS.get(et.value, {}),
                subscriber_count=subscriber_counts.get(et.value, 0),
            ).model_dump()
        )

    return {
        "status": "success",
        "data": event_types,
    }


# ── GET /webhooks/{id} ──────────────────────────────────────────────────


@router.get(
    "/{webhook_id}",
    response_model=SuccessResponse,
    summary="Get webhook endpoint details",
    responses={404: {"model": ErrorResponse}},
)
async def get_webhook(
    webhook_id: uuid.UUID,
    user: User = Depends(_get_current_user),
    db: AsyncSession = Depends(get_db_session),
) -> dict:
    """Retrieve a single webhook endpoint by ID."""
    _require_admin(user)

    webhook = await _get_webhook_or_404(db, webhook_id, user.org_id)

    return {
        "status": "success",
        "data": _webhook_to_response(webhook),
    }


# ── PUT /webhooks/{id} ──────────────────────────────────────────────────


@router.put(
    "/{webhook_id}",
    response_model=SuccessResponse,
    summary="Update a webhook endpoint",
    responses={404: {"model": ErrorResponse}},
)
async def update_webhook(
    webhook_id: uuid.UUID,
    body: WebhookUpdate,
    user: User = Depends(_get_current_user),
    db: AsyncSession = Depends(get_db_session),
) -> dict:
    """Update an existing webhook endpoint configuration."""
    _require_admin(user)

    webhook = await _get_webhook_or_404(db, webhook_id, user.org_id)

    update_data = body.model_dump(exclude_none=True)

    if "name" in update_data:
        webhook.name = update_data["name"]
    if "url" in update_data:
        webhook.url = update_data["url"]
    if "secret" in update_data:
        webhook.secret = encrypt_string(update_data["secret"])
    if "events" in update_data:
        webhook.events = update_data["events"]
    if "headers" in update_data:
        webhook.headers_json = update_data["headers"]
    if "is_active" in update_data:
        webhook.is_active = update_data["is_active"]
    if "retry_count" in update_data:
        webhook.retry_count = update_data["retry_count"]
    if "timeout" in update_data:
        webhook.timeout_seconds = update_data["timeout"]

    await db.flush()
    await db.refresh(webhook)

    logger.info(
        "Webhook endpoint updated",
        webhook_id=str(webhook.id),
        user_id=str(user.id),
        fields=list(update_data.keys()),
    )

    return {
        "status": "success",
        "data": _webhook_to_response(webhook),
        "message": "Webhook endpoint updated successfully.",
    }


# ── DELETE /webhooks/{id} ────────────────────────────────────────────────


@router.delete(
    "/{webhook_id}",
    response_model=SuccessResponse,
    summary="Delete a webhook endpoint",
    responses={404: {"model": ErrorResponse}},
)
async def delete_webhook(
    webhook_id: uuid.UUID,
    user: User = Depends(_get_current_user),
    db: AsyncSession = Depends(get_db_session),
) -> dict:
    """Delete a webhook endpoint and all its delivery records."""
    _require_admin(user)

    webhook = await _get_webhook_or_404(db, webhook_id, user.org_id)

    await db.delete(webhook)
    await db.flush()

    logger.info(
        "Webhook endpoint deleted",
        webhook_id=str(webhook_id),
        user_id=str(user.id),
    )

    return {
        "status": "success",
        "data": None,
        "message": "Webhook endpoint deleted successfully.",
    }


# ── POST /webhooks/{id}/test ────────────────────────────────────────────


@router.post(
    "/{webhook_id}/test",
    response_model=SuccessResponse,
    summary="Test a webhook endpoint with a sample event",
    responses={404: {"model": ErrorResponse}},
)
async def test_webhook(
    webhook_id: uuid.UUID,
    body: WebhookTestRequest | None = None,
    user: User = Depends(_get_current_user),
    db: AsyncSession = Depends(get_db_session),
) -> dict:
    """Send a test event delivery to verify webhook connectivity.

    Uses the standard example payload for the event type unless a
    custom ``sample_payload`` is provided in the request body.
    """
    _require_admin(user)

    webhook = await _get_webhook_or_404(db, webhook_id, user.org_id)

    event_type = body.event_type if body else "alert.created"
    payload = (
        body.sample_payload
        if body and body.sample_payload
        else WEBHOOK_EVENT_EXAMPLE_PAYLOADS.get(event_type, {"event": event_type})
    )

    # Decrypt secret for signing
    try:
        raw_secret = decrypt_string(webhook.secret)
    except Exception:
        raw_secret = ""

    # Build signature
    import json

    payload_bytes = json.dumps(payload, default=str).encode("utf-8")
    signature = hmac.new(
        raw_secret.encode("utf-8"),
        payload_bytes,
        hashlib.sha256,
    ).hexdigest()

    # Build headers
    headers = {
        "Content-Type": "application/json",
        "X-VisionAI-Event": event_type,
        "X-VisionAI-Signature": f"sha256={signature}",
        "X-VisionAI-Delivery": str(uuid.uuid4()),
        "User-Agent": "VisionAI-Webhook/1.0",
    }
    if webhook.headers_json:
        headers.update(webhook.headers_json)

    start_time = time.monotonic()
    test_result: dict[str, Any]

    try:
        async with httpx.AsyncClient(timeout=webhook.timeout_seconds) as client:
            response = await client.post(
                webhook.url,
                content=payload_bytes,
                headers=headers,
            )
        elapsed_ms = round((time.monotonic() - start_time) * 1000, 2)

        success = 200 <= response.status_code < 300
        test_result = WebhookTestResponse(
            success=success,
            status_code=response.status_code,
            response_time_ms=elapsed_ms,
            error=None if success else f"HTTP {response.status_code}",
        ).model_dump()

    except httpx.TimeoutException:
        elapsed_ms = round((time.monotonic() - start_time) * 1000, 2)
        test_result = WebhookTestResponse(
            success=False,
            status_code=None,
            response_time_ms=elapsed_ms,
            error="Connection timed out.",
        ).model_dump()

    except httpx.ConnectError as exc:
        elapsed_ms = round((time.monotonic() - start_time) * 1000, 2)
        test_result = WebhookTestResponse(
            success=False,
            status_code=None,
            response_time_ms=elapsed_ms,
            error=f"Connection failed: {exc}",
        ).model_dump()

    except Exception as exc:
        elapsed_ms = round((time.monotonic() - start_time) * 1000, 2)
        test_result = WebhookTestResponse(
            success=False,
            status_code=None,
            response_time_ms=elapsed_ms,
            error=str(exc),
        ).model_dump()

    logger.info(
        "Webhook test delivery completed",
        webhook_id=str(webhook_id),
        success=test_result.get("success"),
        status_code=test_result.get("status_code"),
        response_time_ms=test_result.get("response_time_ms"),
    )

    return {
        "status": "success",
        "data": test_result,
    }


# ── GET /webhooks/{id}/deliveries ────────────────────────────────────────


@router.get(
    "/{webhook_id}/deliveries",
    response_model=SuccessResponse,
    summary="Get webhook delivery history",
    responses={404: {"model": ErrorResponse}},
)
async def get_deliveries(
    webhook_id: uuid.UUID,
    page: int = Query(1, ge=1),
    page_size: int = Query(20, ge=1, le=100),
    delivery_status: str | None = Query(None, alias="status"),
    event_type: str | None = Query(None),
    start_date: datetime | None = Query(None),
    end_date: datetime | None = Query(None),
    user: User = Depends(_get_current_user),
    db: AsyncSession = Depends(get_db_session),
) -> dict:
    """Return paginated delivery history for a webhook endpoint.

    Supports filtering by status, event type, and date range.
    """
    _require_admin(user)

    # Verify access
    await _get_webhook_or_404(db, webhook_id, user.org_id)

    conditions = [WebhookDeliveryV2.webhook_id == webhook_id]

    if delivery_status:
        try:
            status_enum = WebhookDeliveryStatus(delivery_status)
            conditions.append(WebhookDeliveryV2.status == status_enum)
        except ValueError:
            raise HTTPException(
                status_code=status.HTTP_422_UNPROCESSABLE_ENTITY,
                detail=f"Invalid status '{delivery_status}'. "
                f"Must be one of: {', '.join(s.value for s in WebhookDeliveryStatus)}",
            )

    if event_type:
        conditions.append(WebhookDeliveryV2.event_type == event_type)

    if start_date:
        conditions.append(WebhookDeliveryV2.created_at >= start_date)

    if end_date:
        conditions.append(WebhookDeliveryV2.created_at <= end_date)

    # Count total
    count_q = select(func.count(WebhookDeliveryV2.id)).where(and_(*conditions))
    total_result = await db.execute(count_q)
    total = total_result.scalar() or 0

    # Fetch page
    offset = (page - 1) * page_size
    query = (
        select(WebhookDeliveryV2)
        .where(and_(*conditions))
        .order_by(WebhookDeliveryV2.created_at.desc())
        .offset(offset)
        .limit(page_size)
    )
    result = await db.execute(query)
    deliveries = result.scalars().all()

    return {
        "status": "success",
        "data": [_delivery_to_response(d) for d in deliveries],
        "meta": {
            "page": page,
            "page_size": page_size,
            "total": total,
            "total_pages": math.ceil(total / page_size) if page_size else 0,
        },
    }


# ── GET /webhooks/{id}/stats ─────────────────────────────────────────────


@router.get(
    "/{webhook_id}/stats",
    response_model=SuccessResponse,
    summary="Get webhook delivery statistics",
    responses={404: {"model": ErrorResponse}},
)
async def get_webhook_stats(
    webhook_id: uuid.UUID,
    user: User = Depends(_get_current_user),
    db: AsyncSession = Depends(get_db_session),
) -> dict:
    """Return aggregate delivery statistics for a webhook endpoint."""
    _require_admin(user)

    await _get_webhook_or_404(db, webhook_id, user.org_id)

    # Total deliveries
    total_q = select(func.count(WebhookDeliveryV2.id)).where(
        WebhookDeliveryV2.webhook_id == webhook_id,
    )
    total_result = await db.execute(total_q)
    total_deliveries = total_result.scalar() or 0

    # Delivered count
    delivered_q = select(func.count(WebhookDeliveryV2.id)).where(
        WebhookDeliveryV2.webhook_id == webhook_id,
        WebhookDeliveryV2.status == WebhookDeliveryStatus.DELIVERED,
    )
    delivered_result = await db.execute(delivered_q)
    delivered_count = delivered_result.scalar() or 0

    # Average latency
    avg_latency_q = select(func.avg(WebhookDeliveryV2.duration_ms)).where(
        WebhookDeliveryV2.webhook_id == webhook_id,
        WebhookDeliveryV2.duration_ms.is_not(None),
    )
    avg_result = await db.execute(avg_latency_q)
    avg_latency_ms = round(float(avg_result.scalar() or 0.0), 2)

    success_rate = (
        round((delivered_count / total_deliveries) * 100, 2)
        if total_deliveries > 0
        else 0.0
    )

    # Per-event-type breakdown
    by_event_type: list[dict] = []
    event_types_q = (
        select(WebhookDeliveryV2.event_type)
        .where(WebhookDeliveryV2.webhook_id == webhook_id)
        .distinct()
    )
    et_result = await db.execute(event_types_q)
    distinct_events = [row[0] for row in et_result.all()]

    for et in distinct_events:
        base_cond = and_(
            WebhookDeliveryV2.webhook_id == webhook_id,
            WebhookDeliveryV2.event_type == et,
        )

        et_total_q = select(func.count(WebhookDeliveryV2.id)).where(base_cond)
        et_total = (await db.execute(et_total_q)).scalar() or 0

        et_delivered_q = select(func.count(WebhookDeliveryV2.id)).where(
            base_cond, WebhookDeliveryV2.status == WebhookDeliveryStatus.DELIVERED
        )
        et_delivered = (await db.execute(et_delivered_q)).scalar() or 0

        et_failed_q = select(func.count(WebhookDeliveryV2.id)).where(
            base_cond, WebhookDeliveryV2.status == WebhookDeliveryStatus.FAILED
        )
        et_failed = (await db.execute(et_failed_q)).scalar() or 0

        et_pending_q = select(func.count(WebhookDeliveryV2.id)).where(
            base_cond, WebhookDeliveryV2.status == WebhookDeliveryStatus.PENDING
        )
        et_pending = (await db.execute(et_pending_q)).scalar() or 0

        by_event_type.append(
            EventTypeStats(
                event_type=et,
                total=et_total,
                delivered=et_delivered,
                failed=et_failed,
                pending=et_pending,
            ).model_dump()
        )

    stats = WebhookStats(
        total_deliveries=total_deliveries,
        success_rate=success_rate,
        avg_latency_ms=avg_latency_ms,
        by_event_type=by_event_type,
    )

    return {
        "status": "success",
        "data": stats.model_dump(),
    }


# ── POST /webhooks/{id}/retry/{delivery_id} ─────────────────────────────


@router.post(
    "/{webhook_id}/retry/{delivery_id}",
    response_model=SuccessResponse,
    summary="Retry a failed webhook delivery",
    responses={
        404: {"model": ErrorResponse},
        422: {"model": ErrorResponse},
    },
)
async def retry_delivery(
    webhook_id: uuid.UUID,
    delivery_id: uuid.UUID,
    user: User = Depends(_get_current_user),
    db: AsyncSession = Depends(get_db_session),
) -> dict:
    """Manually retry a failed delivery.

    Resets the delivery status to ``pending``, increments the attempt
    counter, and schedules an immediate retry.
    """
    _require_admin(user)

    webhook = await _get_webhook_or_404(db, webhook_id, user.org_id)

    # Load delivery
    delivery_result = await db.execute(
        select(WebhookDeliveryV2).where(
            WebhookDeliveryV2.id == delivery_id,
            WebhookDeliveryV2.webhook_id == webhook_id,
        )
    )
    delivery = delivery_result.scalars().first()
    if not delivery:
        raise HTTPException(
            status_code=status.HTTP_404_NOT_FOUND,
            detail="Delivery record not found.",
        )

    if delivery.status == WebhookDeliveryStatus.DELIVERED:
        raise HTTPException(
            status_code=status.HTTP_422_UNPROCESSABLE_ENTITY,
            detail="Cannot retry an already delivered webhook.",
        )

    # Reset for retry
    delivery.status = WebhookDeliveryStatus.PENDING
    delivery.next_retry_at = datetime.now(timezone.utc)
    delivery.error_message = None
    delivery.attempts += 1

    await db.flush()
    await db.refresh(delivery)

    logger.info(
        "Webhook delivery retry initiated",
        delivery_id=str(delivery_id),
        webhook_id=str(webhook_id),
        user_id=str(user.id),
        attempt=delivery.attempts,
    )

    return {
        "status": "success",
        "data": _delivery_to_response(delivery),
        "message": "Delivery retry initiated.",
    }
