"""
Webhook & Integration Platform API endpoints.

Provides full CRUD for webhook endpoints, delivery log access with
retry support, third-party integration management (Slack, Teams,
PagerDuty, Jira), and event type listing.
"""

from __future__ import annotations

import math
import uuid
from datetime import datetime
from typing import Any

import structlog
from fastapi import APIRouter, Depends, HTTPException, Query, status
from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession

from app.database import get_db_session
from app.exceptions import NotFoundError, ValidationError
from app.middleware.auth import JWTBearer, TokenPayload
from app.models.integration import (
    EVENT_TYPE_DESCRIPTIONS,
    EVENT_TYPE_EXAMPLE_PAYLOADS,
    EventType,
    IntegrationType,
)
from app.models.user import User, UserRole
from app.schemas.common import ErrorResponse, SuccessResponse
from app.schemas.integration import (
    DeliveryFilters,
    DeliveryListResponse,
    EventTypeInfo,
    EventTypeList,
    IntegrationConfigCreate,
    IntegrationConfigResponse,
    IntegrationConfigUpdate,
    IntegrationTestResponse,
    WebhookCreate,
    WebhookDeliveryResponse,
    WebhookListResponse,
    WebhookResponse,
    WebhookTestRequest,
    WebhookTestResponse,
    WebhookUpdate,
)
from app.services import integration_service, webhook_service

logger = structlog.stdlib.get_logger(__name__)

router = APIRouter()


# ── Helpers ──────────────────────────────────────────────────────────────


async def _get_current_user(
    token: TokenPayload = Depends(JWTBearer()),
    db: AsyncSession = Depends(get_db_session),
) -> User:
    """Resolve the current authenticated user."""
    result = await db.execute(
        select(User).where(User.id == uuid.UUID(token.sub), User.is_active.is_(True))
    )
    user = result.scalars().first()
    if not user:
        raise HTTPException(status_code=401, detail="User not found or deactivated.")
    return user


def _require_admin(user: User) -> None:
    """Raise 403 if the user is below admin role."""
    allowed = {UserRole.SUPER_ADMIN, UserRole.ORG_ADMIN, UserRole.MANAGER}
    if user.role not in allowed:
        raise HTTPException(
            status_code=status.HTTP_403_FORBIDDEN,
            detail="Admin or manager role required for integration management.",
        )


def _webhook_to_response(webhook) -> dict[str, Any]:
    """Serialize a WebhookEndpoint model to a response dict."""
    return WebhookResponse(
        id=webhook.id,
        org_id=webhook.org_id,
        name=webhook.name,
        url=webhook.url,
        secret=webhook.secret,
        events=webhook.events or [],
        headers=webhook.headers,
        is_active=webhook.is_active,
        retry_policy=webhook.retry_policy,
        created_by=webhook.created_by,
        created_at=webhook.created_at,
        updated_at=webhook.updated_at,
    ).model_dump(mode="json")


def _delivery_to_response(delivery) -> dict[str, Any]:
    """Serialize a WebhookDelivery model to a response dict."""
    return WebhookDeliveryResponse(
        id=delivery.id,
        webhook_id=delivery.webhook_id,
        event_type=delivery.event_type,
        payload=delivery.payload,
        status=delivery.status.value if hasattr(delivery.status, "value") else delivery.status,
        status_code=delivery.status_code,
        response_body=delivery.response_body,
        attempt_number=delivery.attempt_number,
        next_retry_at=delivery.next_retry_at,
        error_message=delivery.error_message,
        created_at=delivery.created_at,
        completed_at=delivery.completed_at,
    ).model_dump(mode="json")


def _integration_to_response(integration) -> dict[str, Any]:
    """Serialize an IntegrationConfig model to a response dict.

    Redacts sensitive fields in the config (tokens, passwords, secrets).
    """
    config = dict(integration.config) if integration.config else {}
    sensitive_keys = {"api_token", "auth_token", "password", "secret", "routing_key", "access_token"}
    redacted_config = {}
    for k, v in config.items():
        if k in sensitive_keys and isinstance(v, str) and len(v) > 4:
            redacted_config[k] = v[:4] + "****"
        else:
            redacted_config[k] = v

    return IntegrationConfigResponse(
        id=integration.id,
        org_id=integration.org_id,
        integration_type=(
            integration.integration_type.value
            if hasattr(integration.integration_type, "value")
            else integration.integration_type
        ),
        name=integration.name,
        config=redacted_config,
        is_active=integration.is_active,
        created_by=integration.created_by,
        created_at=integration.created_at,
        updated_at=integration.updated_at,
    ).model_dump(mode="json")


# ── Webhook Endpoints ────────────────────────────────────────────────────


@router.post(
    "/webhooks",
    response_model=SuccessResponse,
    status_code=status.HTTP_201_CREATED,
    summary="Create a new webhook endpoint",
    responses={
        403: {"model": ErrorResponse},
        422: {"model": ErrorResponse},
    },
)
async def create_webhook(
    body: WebhookCreate,
    user: User = Depends(_get_current_user),
    db: AsyncSession = Depends(get_db_session),
) -> dict:
    """Register a new webhook endpoint with auto-generated HMAC secret."""
    _require_admin(user)

    webhook = await webhook_service.create_webhook(
        db=db,
        org_id=user.org_id,
        data=body.model_dump(),
        created_by=user.id,
    )

    return {
        "status": "success",
        "data": _webhook_to_response(webhook),
        "message": "Webhook endpoint created successfully.",
    }


@router.get(
    "/webhooks",
    response_model=SuccessResponse,
    summary="List webhook endpoints",
)
async def list_webhooks(
    page: int = Query(1, ge=1),
    page_size: int = Query(20, ge=1, le=100),
    user: User = Depends(_get_current_user),
    db: AsyncSession = Depends(get_db_session),
) -> dict:
    """Return a paginated list of webhook endpoints for the organization."""
    _require_admin(user)

    webhooks, total = await webhook_service.list_webhooks(
        db=db,
        org_id=user.org_id,
        page=page,
        page_size=page_size,
    )

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


@router.get(
    "/webhooks/{webhook_id}",
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

    webhook = await webhook_service.get_webhook(db, webhook_id, user.org_id)
    return {
        "status": "success",
        "data": _webhook_to_response(webhook),
    }


@router.put(
    "/webhooks/{webhook_id}",
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

    webhook = await webhook_service.update_webhook(
        db=db,
        webhook_id=webhook_id,
        data=body.model_dump(exclude_none=True),
        org_id=user.org_id,
    )

    return {
        "status": "success",
        "data": _webhook_to_response(webhook),
        "message": "Webhook endpoint updated successfully.",
    }


@router.delete(
    "/webhooks/{webhook_id}",
    response_model=SuccessResponse,
    summary="Delete a webhook endpoint",
    responses={404: {"model": ErrorResponse}},
)
async def delete_webhook(
    webhook_id: uuid.UUID,
    user: User = Depends(_get_current_user),
    db: AsyncSession = Depends(get_db_session),
) -> dict:
    """Soft-delete a webhook endpoint and deactivate it."""
    _require_admin(user)

    await webhook_service.delete_webhook(db, webhook_id, user.org_id)

    return {
        "status": "success",
        "data": None,
        "message": "Webhook endpoint deleted successfully.",
    }


@router.post(
    "/webhooks/{webhook_id}/test",
    response_model=SuccessResponse,
    summary="Send a test delivery to a webhook",
    responses={404: {"model": ErrorResponse}},
)
async def test_webhook(
    webhook_id: uuid.UUID,
    body: WebhookTestRequest | None = None,
    user: User = Depends(_get_current_user),
    db: AsyncSession = Depends(get_db_session),
) -> dict:
    """Send a test event delivery to verify webhook connectivity."""
    _require_admin(user)

    event_type = body.event_type if body else "alert.created"

    result = await webhook_service.send_test_delivery(
        db=db,
        webhook_id=webhook_id,
        event_type=event_type,
        org_id=user.org_id,
    )

    return {
        "status": "success",
        "data": result,
    }


# ── Delivery Log ─────────────────────────────────────────────────────────


@router.get(
    "/webhooks/{webhook_id}/deliveries",
    response_model=SuccessResponse,
    summary="Get webhook delivery log",
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
    """Return paginated delivery history for a webhook endpoint."""
    _require_admin(user)

    # Verify webhook access
    await webhook_service.get_webhook(db, webhook_id, user.org_id)

    deliveries, total = await webhook_service.get_delivery_log(
        db=db,
        webhook_id=webhook_id,
        page=page,
        page_size=page_size,
        status_filter=delivery_status,
        event_type_filter=event_type,
        start_date=start_date,
        end_date=end_date,
    )

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


@router.post(
    "/webhooks/{webhook_id}/deliveries/{delivery_id}/retry",
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
    """Manually retry a failed or retrying webhook delivery."""
    _require_admin(user)

    # Verify webhook access
    await webhook_service.get_webhook(db, webhook_id, user.org_id)

    delivery = await webhook_service.retry_delivery(
        db=db,
        delivery_id=delivery_id,
        webhook_id=webhook_id,
    )

    return {
        "status": "success",
        "data": _delivery_to_response(delivery),
        "message": "Delivery retry initiated.",
    }


# ── Event Types ──────────────────────────────────────────────────────────


@router.get(
    "/event-types",
    response_model=SuccessResponse,
    summary="List all available event types",
)
async def list_event_types(
    user: User = Depends(_get_current_user),
    db: AsyncSession = Depends(get_db_session),
) -> dict:
    """Return all available event types with descriptions, example payloads, and subscriber counts."""
    subscriber_counts = await webhook_service.get_event_subscriber_counts(db, user.org_id)

    event_types = []
    for et in EventType:
        event_types.append(
            EventTypeInfo(
                event_type=et.value,
                description=EVENT_TYPE_DESCRIPTIONS.get(et.value, ""),
                example_payload=EVENT_TYPE_EXAMPLE_PAYLOADS.get(et.value, {}),
                subscriber_count=subscriber_counts.get(et.value, 0),
            ).model_dump()
        )

    return {
        "status": "success",
        "data": event_types,
    }


# ── Integration Configs ──────────────────────────────────────────────────


@router.post(
    "/configs",
    response_model=SuccessResponse,
    status_code=status.HTTP_201_CREATED,
    summary="Create a third-party integration",
    responses={
        403: {"model": ErrorResponse},
        422: {"model": ErrorResponse},
    },
)
async def create_integration(
    body: IntegrationConfigCreate,
    user: User = Depends(_get_current_user),
    db: AsyncSession = Depends(get_db_session),
) -> dict:
    """Create a new third-party integration (Slack, Teams, PagerDuty, Jira)."""
    _require_admin(user)

    integration = await integration_service.create_integration(
        db=db,
        org_id=user.org_id,
        data=body.model_dump(),
        created_by=user.id,
    )

    return {
        "status": "success",
        "data": _integration_to_response(integration),
        "message": "Integration created successfully.",
    }


@router.get(
    "/configs",
    response_model=SuccessResponse,
    summary="List third-party integrations",
)
async def list_integrations(
    page: int = Query(1, ge=1),
    page_size: int = Query(20, ge=1, le=100),
    user: User = Depends(_get_current_user),
    db: AsyncSession = Depends(get_db_session),
) -> dict:
    """Return a paginated list of third-party integrations."""
    _require_admin(user)

    integrations, total = await integration_service.list_integrations(
        db=db,
        org_id=user.org_id,
        page=page,
        page_size=page_size,
    )

    return {
        "status": "success",
        "data": [_integration_to_response(i) for i in integrations],
        "meta": {
            "page": page,
            "page_size": page_size,
            "total": total,
            "total_pages": math.ceil(total / page_size) if page_size else 0,
        },
    }


@router.put(
    "/configs/{integration_id}",
    response_model=SuccessResponse,
    summary="Update a third-party integration",
    responses={404: {"model": ErrorResponse}},
)
async def update_integration(
    integration_id: uuid.UUID,
    body: IntegrationConfigUpdate,
    user: User = Depends(_get_current_user),
    db: AsyncSession = Depends(get_db_session),
) -> dict:
    """Update an existing third-party integration configuration."""
    _require_admin(user)

    integration = await integration_service.update_integration(
        db=db,
        integration_id=integration_id,
        data=body.model_dump(exclude_none=True),
        org_id=user.org_id,
    )

    return {
        "status": "success",
        "data": _integration_to_response(integration),
        "message": "Integration updated successfully.",
    }


@router.delete(
    "/configs/{integration_id}",
    response_model=SuccessResponse,
    summary="Delete a third-party integration",
    responses={404: {"model": ErrorResponse}},
)
async def delete_integration(
    integration_id: uuid.UUID,
    user: User = Depends(_get_current_user),
    db: AsyncSession = Depends(get_db_session),
) -> dict:
    """Soft-delete a third-party integration."""
    _require_admin(user)

    await integration_service.delete_integration(db, integration_id, user.org_id)

    return {
        "status": "success",
        "data": None,
        "message": "Integration deleted successfully.",
    }


@router.post(
    "/configs/{integration_id}/test",
    response_model=SuccessResponse,
    summary="Test a third-party integration",
    responses={404: {"model": ErrorResponse}},
)
async def test_integration(
    integration_id: uuid.UUID,
    user: User = Depends(_get_current_user),
    db: AsyncSession = Depends(get_db_session),
) -> dict:
    """Test a third-party integration by sending a test message or checking connectivity."""
    _require_admin(user)

    result = await integration_service.test_integration(
        db=db,
        integration_id=integration_id,
        org_id=user.org_id,
    )

    return {
        "status": "success",
        "data": result,
    }
