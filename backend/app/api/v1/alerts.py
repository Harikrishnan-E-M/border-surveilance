"""
Alert management API endpoints.

Provides alert listing with advanced filtering, statistics, lifecycle
management (acknowledge, resolve, false-positive, escalate), and
a recent-alerts feed for real-time dashboards.
"""

from __future__ import annotations

import math
import uuid
from datetime import datetime, date, timedelta, timezone
from typing import Optional

import structlog
from fastapi import APIRouter, Depends, HTTPException, Query, status
from pydantic import BaseModel, Field
from sqlalchemy import and_, func, or_, select, desc
from sqlalchemy.ext.asyncio import AsyncSession

from app.database import get_db_session
from app.exceptions import (
    AuthorizationError,
    NotFoundError,
    ValidationError,
)
from app.middleware.auth import JWTBearer, TokenPayload
from app.models.alert import Alert, AlertEscalation, AlertStatus
from app.models.camera import Camera
from app.models.rule import RuleSeverity, RuleType
from app.models.user import User, UserRole
from app.schemas.common import ErrorResponse, SuccessResponse

logger = structlog.stdlib.get_logger(__name__)

router = APIRouter()


# ---------------------------------------------------------------------------
# Pydantic schemas
# ---------------------------------------------------------------------------


class AlertResponse(BaseModel):
    id: uuid.UUID
    org_id: uuid.UUID
    camera_id: uuid.UUID
    zone_id: uuid.UUID | None = None
    rule_id: uuid.UUID
    alert_type: str
    severity: str
    title: str
    description: str | None = None
    snapshot_path: str | None = None
    video_clip_path: str | None = None
    metadata_json: dict | None = None
    status: str
    acknowledged_by: uuid.UUID | None = None
    acknowledged_at: datetime | None = None
    resolved_by: uuid.UUID | None = None
    resolved_at: datetime | None = None
    created_at: datetime
    updated_at: datetime

    class Config:
        from_attributes = True


class ResolveRequest(BaseModel):
    notes: str | None = Field(default=None, max_length=2000, description="Resolution notes")


class EscalateRequest(BaseModel):
    escalated_to: uuid.UUID = Field(..., description="User ID to escalate to")
    reason: str | None = Field(default=None, max_length=2000, description="Reason for escalation")


# ---------------------------------------------------------------------------
# Helpers
# ---------------------------------------------------------------------------


async def _get_current_user(
    token: TokenPayload = Depends(JWTBearer()),
    db: AsyncSession = Depends(get_db_session),
) -> User:
    result = await db.execute(
        select(User).where(User.id == uuid.UUID(token.sub), User.is_active.is_(True))
    )
    user = result.scalars().first()
    if not user:
        raise HTTPException(status_code=401, detail="User not found or deactivated.")
    return user


def _require_operator(user: User) -> None:
    """Raise 403 if the user is below operator role."""
    allowed = {UserRole.SUPER_ADMIN, UserRole.ORG_ADMIN, UserRole.MANAGER, UserRole.OPERATOR}
    if user.role not in allowed:
        raise AuthorizationError(message="Operator role or higher is required.")


def _alert_to_response(alert: Alert) -> dict:
    return AlertResponse(
        id=alert.id,
        org_id=alert.org_id,
        camera_id=alert.camera_id,
        zone_id=alert.zone_id,
        rule_id=alert.rule_id,
        alert_type=alert.alert_type.value if isinstance(alert.alert_type, RuleType) else alert.alert_type,
        severity=alert.severity.value if isinstance(alert.severity, RuleSeverity) else alert.severity,
        title=alert.title,
        description=alert.description,
        snapshot_path=alert.snapshot_path,
        video_clip_path=alert.video_clip_path,
        metadata_json=alert.metadata_json,
        status=alert.status.value if isinstance(alert.status, AlertStatus) else alert.status,
        acknowledged_by=alert.acknowledged_by,
        acknowledged_at=alert.acknowledged_at,
        resolved_by=alert.resolved_by,
        resolved_at=alert.resolved_at,
        created_at=alert.created_at,
        updated_at=alert.updated_at,
    ).model_dump(mode="json")


# ---------------------------------------------------------------------------
# GET / - List alerts (paginated, filterable)
# ---------------------------------------------------------------------------


@router.get(
    "/",
    response_model=SuccessResponse,
    summary="List alerts (paginated, filterable)",
)
async def list_alerts(
    severity: str | None = Query(None, description="Filter by severity"),
    alert_type: str | None = Query(None, description="Filter by alert type"),
    camera_id: uuid.UUID | None = Query(None),
    alert_status: str | None = Query(None, alias="status", description="Filter by status"),
    start_date: datetime | None = Query(None, description="Start date (inclusive)"),
    end_date: datetime | None = Query(None, description="End date (inclusive)"),
    page: int = Query(1, ge=1),
    page_size: int = Query(20, ge=1, le=100),
    user: User = Depends(_get_current_user),
    db: AsyncSession = Depends(get_db_session),
) -> dict:
    """Return a paginated list of alerts for the user's organization."""
    query = select(Alert).where(Alert.org_id == user.org_id)

    if severity:
        try:
            query = query.where(Alert.severity == RuleSeverity(severity))
        except ValueError:
            raise ValidationError(message=f"Invalid severity: {severity}")
    if alert_type:
        try:
            query = query.where(Alert.alert_type == RuleType(alert_type))
        except ValueError:
            raise ValidationError(message=f"Invalid alert_type: {alert_type}")
    if camera_id:
        query = query.where(Alert.camera_id == camera_id)
    if alert_status:
        try:
            query = query.where(Alert.status == AlertStatus(alert_status))
        except ValueError:
            raise ValidationError(message=f"Invalid status: {alert_status}")
    if start_date:
        query = query.where(Alert.created_at >= start_date)
    if end_date:
        query = query.where(Alert.created_at <= end_date)

    count_q = select(func.count()).select_from(query.subquery())
    total = (await db.execute(count_q)).scalar() or 0

    query = query.order_by(Alert.created_at.desc()).offset((page - 1) * page_size).limit(page_size)
    result = await db.execute(query)
    alerts = result.scalars().all()

    return {
        "status": "success",
        "data": [_alert_to_response(a) for a in alerts],
        "meta": {
            "page": page,
            "page_size": page_size,
            "total": total,
            "total_pages": math.ceil(total / page_size) if page_size else 0,
        },
    }


# ---------------------------------------------------------------------------
# GET /stats - Alert statistics
# ---------------------------------------------------------------------------


@router.get(
    "/stats",
    response_model=SuccessResponse,
    summary="Get alert statistics",
)
async def alert_stats(
    start_date: datetime | None = Query(None),
    end_date: datetime | None = Query(None),
    user: User = Depends(_get_current_user),
    db: AsyncSession = Depends(get_db_session),
) -> dict:
    """Return alert counts grouped by severity, type, and status."""
    base_filter = [Alert.org_id == user.org_id]
    if start_date:
        base_filter.append(Alert.created_at >= start_date)
    if end_date:
        base_filter.append(Alert.created_at <= end_date)

    # Counts by severity
    severity_q = (
        select(Alert.severity, func.count().label("count"))
        .where(and_(*base_filter))
        .group_by(Alert.severity)
    )
    severity_result = await db.execute(severity_q)
    by_severity = {
        (row.severity.value if isinstance(row.severity, RuleSeverity) else row.severity): row.count
        for row in severity_result.all()
    }

    # Counts by type
    type_q = (
        select(Alert.alert_type, func.count().label("count"))
        .where(and_(*base_filter))
        .group_by(Alert.alert_type)
    )
    type_result = await db.execute(type_q)
    by_type = {
        (row.alert_type.value if isinstance(row.alert_type, RuleType) else row.alert_type): row.count
        for row in type_result.all()
    }

    # Counts by status
    status_q = (
        select(Alert.status, func.count().label("count"))
        .where(and_(*base_filter))
        .group_by(Alert.status)
    )
    status_result = await db.execute(status_q)
    by_status = {
        (row.status.value if isinstance(row.status, AlertStatus) else row.status): row.count
        for row in status_result.all()
    }

    # Total count
    total_q = select(func.count()).where(and_(*base_filter)).select_from(Alert)
    total = (await db.execute(total_q)).scalar() or 0

    return {
        "status": "success",
        "data": {
            "total": total,
            "by_severity": by_severity,
            "by_type": by_type,
            "by_status": by_status,
        },
    }


# ---------------------------------------------------------------------------
# GET /recent - Last 20 alerts for real-time feed
# ---------------------------------------------------------------------------


@router.get(
    "/recent",
    response_model=SuccessResponse,
    summary="Get most recent alerts",
)
async def recent_alerts(
    limit: int = Query(20, ge=1, le=50),
    user: User = Depends(_get_current_user),
    db: AsyncSession = Depends(get_db_session),
) -> dict:
    """Return the most recent alerts for real-time dashboard feed."""
    query = (
        select(Alert)
        .where(Alert.org_id == user.org_id)
        .order_by(Alert.created_at.desc())
        .limit(limit)
    )
    result = await db.execute(query)
    alerts = result.scalars().all()

    return {
        "status": "success",
        "data": [_alert_to_response(a) for a in alerts],
    }


# ---------------------------------------------------------------------------
# GET /{alert_id} - Get alert details
# ---------------------------------------------------------------------------


@router.get(
    "/{alert_id}",
    response_model=SuccessResponse,
    summary="Get alert details with related info",
    responses={404: {"model": ErrorResponse}},
)
async def get_alert(
    alert_id: uuid.UUID,
    user: User = Depends(_get_current_user),
    db: AsyncSession = Depends(get_db_session),
) -> dict:
    """Retrieve a single alert with its escalation history."""
    result = await db.execute(
        select(Alert).where(Alert.id == alert_id, Alert.org_id == user.org_id)
    )
    alert = result.scalars().first()
    if not alert:
        raise NotFoundError(resource="Alert", identifier=str(alert_id))

    # Fetch escalations
    esc_result = await db.execute(
        select(AlertEscalation).where(AlertEscalation.alert_id == alert_id).order_by(AlertEscalation.created_at.desc())
    )
    escalations = esc_result.scalars().all()
    escalation_data = [
        {
            "id": str(e.id),
            "escalated_to": str(e.escalated_to),
            "escalated_by": str(e.escalated_by),
            "reason": e.reason,
            "created_at": e.created_at.isoformat() if e.created_at else None,
        }
        for e in escalations
    ]

    alert_data = _alert_to_response(alert)
    alert_data["escalations"] = escalation_data

    return {
        "status": "success",
        "data": alert_data,
    }


# ---------------------------------------------------------------------------
# POST /{alert_id}/acknowledge - Acknowledge alert
# ---------------------------------------------------------------------------


@router.post(
    "/{alert_id}/acknowledge",
    response_model=SuccessResponse,
    summary="Acknowledge an alert",
    responses={404: {"model": ErrorResponse}},
)
async def acknowledge_alert(
    alert_id: uuid.UUID,
    user: User = Depends(_get_current_user),
    db: AsyncSession = Depends(get_db_session),
) -> dict:
    """Mark an alert as acknowledged. Requires operator role or above."""
    _require_operator(user)

    result = await db.execute(
        select(Alert).where(Alert.id == alert_id, Alert.org_id == user.org_id)
    )
    alert = result.scalars().first()
    if not alert:
        raise NotFoundError(resource="Alert", identifier=str(alert_id))

    if alert.status != AlertStatus.NEW:
        raise ValidationError(message=f"Cannot acknowledge alert with status '{alert.status.value}'. Only 'new' alerts can be acknowledged.")

    alert.status = AlertStatus.ACKNOWLEDGED
    alert.acknowledged_by = user.id
    alert.acknowledged_at = datetime.now(timezone.utc)
    db.add(alert)
    await db.flush()

    logger.info("Alert acknowledged", alert_id=str(alert.id), user_id=str(user.id))

    return {
        "status": "success",
        "data": _alert_to_response(alert),
        "message": "Alert acknowledged successfully.",
    }


# ---------------------------------------------------------------------------
# POST /{alert_id}/resolve - Resolve alert
# ---------------------------------------------------------------------------


@router.post(
    "/{alert_id}/resolve",
    response_model=SuccessResponse,
    summary="Resolve an alert with notes",
    responses={404: {"model": ErrorResponse}},
)
async def resolve_alert(
    alert_id: uuid.UUID,
    body: ResolveRequest | None = None,
    user: User = Depends(_get_current_user),
    db: AsyncSession = Depends(get_db_session),
) -> dict:
    """Mark an alert as resolved with optional notes. Requires operator role or above."""
    _require_operator(user)

    result = await db.execute(
        select(Alert).where(Alert.id == alert_id, Alert.org_id == user.org_id)
    )
    alert = result.scalars().first()
    if not alert:
        raise NotFoundError(resource="Alert", identifier=str(alert_id))

    if alert.status in (AlertStatus.RESOLVED, AlertStatus.FALSE_POSITIVE):
        raise ValidationError(message=f"Alert is already '{alert.status.value}'.")

    alert.status = AlertStatus.RESOLVED
    alert.resolved_by = user.id
    alert.resolved_at = datetime.now(timezone.utc)

    # Store resolution notes in metadata
    if body and body.notes:
        existing_meta = alert.metadata_json or {}
        existing_meta["resolution_notes"] = body.notes
        alert.metadata_json = existing_meta

    db.add(alert)
    await db.flush()

    logger.info("Alert resolved", alert_id=str(alert.id), user_id=str(user.id))

    return {
        "status": "success",
        "data": _alert_to_response(alert),
        "message": "Alert resolved successfully.",
    }


# ---------------------------------------------------------------------------
# POST /{alert_id}/false-positive - Mark as false positive
# ---------------------------------------------------------------------------


@router.post(
    "/{alert_id}/false-positive",
    response_model=SuccessResponse,
    summary="Mark alert as false positive",
    responses={404: {"model": ErrorResponse}},
)
async def mark_false_positive(
    alert_id: uuid.UUID,
    user: User = Depends(_get_current_user),
    db: AsyncSession = Depends(get_db_session),
) -> dict:
    """Mark an alert as a false positive. Any authenticated user can mark."""
    result = await db.execute(
        select(Alert).where(Alert.id == alert_id, Alert.org_id == user.org_id)
    )
    alert = result.scalars().first()
    if not alert:
        raise NotFoundError(resource="Alert", identifier=str(alert_id))

    alert.status = AlertStatus.FALSE_POSITIVE
    alert.resolved_by = user.id
    alert.resolved_at = datetime.now(timezone.utc)

    existing_meta = alert.metadata_json or {}
    existing_meta["false_positive_marked_by"] = str(user.id)
    alert.metadata_json = existing_meta

    db.add(alert)
    await db.flush()

    logger.info("Alert marked as false positive", alert_id=str(alert.id), user_id=str(user.id))

    return {
        "status": "success",
        "data": _alert_to_response(alert),
        "message": "Alert marked as false positive.",
    }


# ---------------------------------------------------------------------------
# POST /{alert_id}/escalate - Escalate alert
# ---------------------------------------------------------------------------


@router.post(
    "/{alert_id}/escalate",
    response_model=SuccessResponse,
    summary="Escalate alert to another user",
    responses={404: {"model": ErrorResponse}},
)
async def escalate_alert(
    alert_id: uuid.UUID,
    body: EscalateRequest,
    user: User = Depends(_get_current_user),
    db: AsyncSession = Depends(get_db_session),
) -> dict:
    """Escalate an alert to another user within the organization."""
    result = await db.execute(
        select(Alert).where(Alert.id == alert_id, Alert.org_id == user.org_id)
    )
    alert = result.scalars().first()
    if not alert:
        raise NotFoundError(resource="Alert", identifier=str(alert_id))

    # Verify the target user exists and belongs to the same org
    target_result = await db.execute(
        select(User).where(
            User.id == body.escalated_to,
            User.org_id == user.org_id,
            User.is_active.is_(True),
        )
    )
    target_user = target_result.scalars().first()
    if not target_user:
        raise NotFoundError(resource="User", identifier=str(body.escalated_to))

    # Cannot escalate to yourself
    if body.escalated_to == user.id:
        raise ValidationError(message="Cannot escalate an alert to yourself.")

    escalation = AlertEscalation(
        alert_id=alert.id,
        escalated_to=body.escalated_to,
        escalated_by=user.id,
        reason=body.reason,
    )
    db.add(escalation)

    alert.status = AlertStatus.ESCALATED
    db.add(alert)
    await db.flush()

    logger.info(
        "Alert escalated",
        alert_id=str(alert.id),
        escalated_to=str(body.escalated_to),
        escalated_by=str(user.id),
    )

    return {
        "status": "success",
        "data": _alert_to_response(alert),
        "message": f"Alert escalated to user {target_user.full_name}.",
    }


# ---------------------------------------------------------------------------
# POST /batch-acknowledge - Batch acknowledge alerts
# ---------------------------------------------------------------------------


class BatchAlertRequest(BaseModel):
    alert_ids: list[uuid.UUID] = Field(..., min_length=1, max_length=500)


@router.post(
    "/batch-acknowledge",
    response_model=SuccessResponse,
    summary="Batch acknowledge multiple alerts",
)
async def batch_acknowledge(
    body: BatchAlertRequest,
    user: User = Depends(_get_current_user),
    db: AsyncSession = Depends(get_db_session),
) -> dict:
    """Acknowledge multiple alerts at once. Requires operator role or above."""
    _require_operator(user)

    result = await db.execute(
        select(Alert).where(
            Alert.id.in_(body.alert_ids),
            Alert.org_id == user.org_id,
            Alert.status == AlertStatus.NEW,
        )
    )
    alerts = result.scalars().all()

    now = datetime.now(timezone.utc)
    count = 0
    for alert in alerts:
        alert.status = AlertStatus.ACKNOWLEDGED
        alert.acknowledged_by = user.id
        alert.acknowledged_at = now
        db.add(alert)
        count += 1

    await db.flush()
    logger.info("Batch acknowledge", count=count, user_id=str(user.id))

    return {
        "status": "success",
        "data": {"acknowledged_count": count},
        "message": f"{count} alert(s) acknowledged successfully.",
    }


# ---------------------------------------------------------------------------
# POST /batch-resolve - Batch resolve alerts
# ---------------------------------------------------------------------------


@router.post(
    "/batch-resolve",
    response_model=SuccessResponse,
    summary="Batch resolve multiple alerts",
)
async def batch_resolve(
    body: BatchAlertRequest,
    user: User = Depends(_get_current_user),
    db: AsyncSession = Depends(get_db_session),
) -> dict:
    """Resolve multiple alerts at once. Requires operator role or above."""
    _require_operator(user)

    result = await db.execute(
        select(Alert).where(
            Alert.id.in_(body.alert_ids),
            Alert.org_id == user.org_id,
            Alert.status.notin_([AlertStatus.RESOLVED, AlertStatus.FALSE_POSITIVE]),
        )
    )
    alerts = result.scalars().all()

    now = datetime.now(timezone.utc)
    count = 0
    for alert in alerts:
        alert.status = AlertStatus.RESOLVED
        alert.resolved_by = user.id
        alert.resolved_at = now
        db.add(alert)
        count += 1

    await db.flush()
    logger.info("Batch resolve", count=count, user_id=str(user.id))

    return {
        "status": "success",
        "data": {"resolved_count": count},
        "message": f"{count} alert(s) resolved successfully.",
    }
