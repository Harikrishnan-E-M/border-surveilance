"""Alert lifecycle management and dispatch service.

Handles alert creation, acknowledgement, resolution, escalation,
statistics computation, Redis pub/sub broadcasting, and WebSocket
notification queueing.
"""

from __future__ import annotations

import json
import uuid
from datetime import datetime, timezone
from typing import Any, Optional

import structlog
from sqlalchemy import case, distinct, func, select
from sqlalchemy.ext.asyncio import AsyncSession

from app.exceptions import NotFoundError, ValidationError
from app.models.alert import Alert, AlertEscalation, AlertStatus
from app.models.camera import Camera
from app.models.rule import Rule, RuleSeverity, RuleType

logger = structlog.stdlib.get_logger(__name__)


# ── Alert CRUD ───────────────────────────────────────────────────────────


async def create_alert(
    db: AsyncSession,
    org_id: uuid.UUID,
    camera_id: uuid.UUID,
    rule_id: uuid.UUID,
    alert_type: RuleType,
    severity: RuleSeverity,
    title: str,
    description: Optional[str] = None,
    zone_id: Optional[uuid.UUID] = None,
    snapshot_path: Optional[str] = None,
    video_clip_path: Optional[str] = None,
    metadata_json: Optional[dict[str, Any]] = None,
) -> Alert:
    """Create a new alert and dispatch it via Redis pub/sub.

    Args:
        db: Async database session.
        org_id: Organization that owns the alert.
        camera_id: Camera that generated the event.
        rule_id: Rule that triggered.
        alert_type: Type of the triggering rule.
        severity: Alert severity level.
        title: Short human-readable title.
        description: Optional detailed description.
        zone_id: Optional zone where the event occurred.
        snapshot_path: Optional path to event snapshot.
        video_clip_path: Optional path to event video clip.
        metadata_json: Optional detection metadata.

    Returns:
        The created Alert instance.
    """
    alert = Alert(
        id=uuid.uuid4(),
        org_id=org_id,
        camera_id=camera_id,
        zone_id=zone_id,
        rule_id=rule_id,
        alert_type=alert_type,
        severity=severity,
        title=title,
        description=description,
        snapshot_path=snapshot_path,
        video_clip_path=video_clip_path,
        metadata_json=metadata_json or {},
        status=AlertStatus.NEW,
    )
    db.add(alert)
    await db.flush()

    logger.info(
        "Alert created",
        alert_id=str(alert.id),
        alert_type=alert_type.value,
        severity=severity.value,
    )

    await dispatch(alert)

    return alert


async def dispatch(alert: Alert) -> None:
    """Dispatch an alert via Redis pub/sub and queue notification tasks.

    Publishes the alert to a Redis channel for real-time WebSocket
    distribution and enqueues background notification tasks for
    configured channels (email, SMS, Telegram, etc.).

    Args:
        alert: The alert to dispatch.
    """
    alert_payload = {
        "id": str(alert.id),
        "org_id": str(alert.org_id),
        "camera_id": str(alert.camera_id),
        "zone_id": str(alert.zone_id) if alert.zone_id else None,
        "rule_id": str(alert.rule_id),
        "alert_type": alert.alert_type.value,
        "severity": alert.severity.value,
        "title": alert.title,
        "description": alert.description,
        "status": alert.status.value,
        "snapshot_path": alert.snapshot_path,
        "created_at": alert.created_at.isoformat() if alert.created_at else None,
    }

    try:
        from app.dependencies import get_redis

        redis = await get_redis()
        channel = f"alerts:{alert.org_id}"
        await redis.publish(channel, json.dumps(alert_payload))

        await redis.lpush(
            f"alert_notifications:{alert.org_id}",
            json.dumps(alert_payload),
        )
        await redis.ltrim(f"alert_notifications:{alert.org_id}", 0, 999)

        logger.debug("Alert dispatched to Redis", alert_id=str(alert.id), channel=channel)

    except Exception as exc:
        logger.warning("Redis dispatch failed, alert still persisted", error=str(exc))

    try:
        from app.workers.alert_tasks import dispatch_alert

        dispatch_alert.delay(str(alert.id))
    except Exception as exc:
        logger.debug("Celery task queueing failed", error=str(exc))


async def broadcast_to_websocket(alert: Alert) -> None:
    """Broadcast an alert event to connected WebSocket clients.

    Uses Redis pub/sub for distribution to all API server instances.

    Args:
        alert: The alert to broadcast.
    """
    payload = {
        "event": "new_alert",
        "data": {
            "id": str(alert.id),
            "alert_type": alert.alert_type.value,
            "severity": alert.severity.value,
            "title": alert.title,
            "camera_id": str(alert.camera_id),
            "status": alert.status.value,
            "created_at": alert.created_at.isoformat() if alert.created_at else None,
        },
    }

    try:
        from app.dependencies import get_redis

        redis = await get_redis()
        await redis.publish(f"ws:alerts:{alert.org_id}", json.dumps(payload))
    except Exception as exc:
        logger.debug("WebSocket broadcast failed", error=str(exc))


# ── Alert Actions ────────────────────────────────────────────────────────


async def acknowledge_alert(
    db: AsyncSession,
    alert_id: uuid.UUID,
    user_id: uuid.UUID,
    note: Optional[str] = None,
    org_id: Optional[uuid.UUID] = None,
) -> Alert:
    """Acknowledge an alert (mark as being reviewed).

    Args:
        db: Async database session.
        alert_id: Alert to acknowledge.
        user_id: User performing the action.
        note: Optional acknowledgement note.
        org_id: Optional organization filter.

    Returns:
        The updated Alert instance.

    Raises:
        NotFoundError: If the alert does not exist.
        ValidationError: If the alert is already resolved.
    """
    alert = await _get_alert(db, alert_id, org_id)

    if alert.status == AlertStatus.RESOLVED:
        raise ValidationError(
            message="Cannot acknowledge a resolved alert",
            code="ALERT_ALREADY_RESOLVED",
        )

    alert.status = AlertStatus.ACKNOWLEDGED
    alert.acknowledged_by = user_id
    alert.acknowledged_at = datetime.now(timezone.utc)

    if note and alert.metadata_json is not None:
        alert.metadata_json = {**alert.metadata_json, "acknowledge_note": note}

    await db.flush()
    logger.info("Alert acknowledged", alert_id=str(alert_id), user_id=str(user_id))

    return alert


async def resolve_alert(
    db: AsyncSession,
    alert_id: uuid.UUID,
    user_id: uuid.UUID,
    resolution_note: str,
    is_false_positive: bool = False,
    org_id: Optional[uuid.UUID] = None,
) -> Alert:
    """Resolve an alert (close it).

    Args:
        db: Async database session.
        alert_id: Alert to resolve.
        user_id: User performing the action.
        resolution_note: Explanation of the resolution.
        is_false_positive: Whether this was a false positive.
        org_id: Optional organization filter.

    Returns:
        The updated Alert instance.

    Raises:
        NotFoundError: If the alert does not exist.
        ValidationError: If the alert is already resolved.
    """
    alert = await _get_alert(db, alert_id, org_id)

    if alert.status == AlertStatus.RESOLVED:
        raise ValidationError(
            message="Alert is already resolved",
            code="ALERT_ALREADY_RESOLVED",
        )

    alert.status = AlertStatus.FALSE_POSITIVE if is_false_positive else AlertStatus.RESOLVED
    alert.resolved_by = user_id
    alert.resolved_at = datetime.now(timezone.utc)

    if alert.metadata_json is not None:
        alert.metadata_json = {
            **alert.metadata_json,
            "resolution_note": resolution_note,
            "is_false_positive": is_false_positive,
        }

    await db.flush()
    logger.info(
        "Alert resolved",
        alert_id=str(alert_id),
        user_id=str(user_id),
        false_positive=is_false_positive,
    )

    return alert


async def escalate_alert(
    db: AsyncSession,
    alert_id: uuid.UUID,
    escalated_by: uuid.UUID,
    escalated_to: uuid.UUID,
    reason: str,
    org_id: Optional[uuid.UUID] = None,
) -> AlertEscalation:
    """Escalate an alert to another user.

    Args:
        db: Async database session.
        alert_id: Alert to escalate.
        escalated_by: User performing the escalation.
        escalated_to: User receiving the escalation.
        reason: Reason for escalation.
        org_id: Optional organization filter.

    Returns:
        The created AlertEscalation instance.

    Raises:
        NotFoundError: If the alert does not exist.
    """
    alert = await _get_alert(db, alert_id, org_id)

    alert.status = AlertStatus.ESCALATED
    await db.flush()

    escalation = AlertEscalation(
        id=uuid.uuid4(),
        alert_id=alert_id,
        escalated_by=escalated_by,
        escalated_to=escalated_to,
        reason=reason,
    )
    db.add(escalation)
    await db.flush()

    logger.info(
        "Alert escalated",
        alert_id=str(alert_id),
        escalated_by=str(escalated_by),
        escalated_to=str(escalated_to),
    )

    try:
        from app.dependencies import get_redis

        redis = await get_redis()
        payload = {
            "event": "alert_escalated",
            "alert_id": str(alert_id),
            "escalated_to": str(escalated_to),
            "reason": reason,
        }
        await redis.publish(f"ws:alerts:{alert.org_id}", json.dumps(payload))
    except Exception as exc:
        logger.debug("Escalation notification failed", error=str(exc))

    return escalation


# ── Alert Statistics ─────────────────────────────────────────────────────


async def get_alert_stats(
    db: AsyncSession,
    org_id: uuid.UUID,
    start_date: Optional[datetime] = None,
    end_date: Optional[datetime] = None,
) -> dict[str, Any]:
    """Compute aggregate alert statistics for an organization.

    Args:
        db: Async database session.
        org_id: Organization to compute stats for.
        start_date: Optional start of the time window.
        end_date: Optional end of the time window.

    Returns:
        Dict with total, by_severity, by_type, by_camera, by_status,
        and false_positive_rate.
    """
    base = select(Alert).where(Alert.org_id == org_id)

    if start_date:
        base = base.where(Alert.created_at >= start_date)
    if end_date:
        base = base.where(Alert.created_at <= end_date)

    # Total count
    total_q = select(func.count()).select_from(base.subquery())
    total_result = await db.execute(total_q)
    total = total_result.scalar() or 0

    # By severity
    severity_q = (
        select(Alert.severity, func.count())
        .where(Alert.org_id == org_id)
    )
    if start_date:
        severity_q = severity_q.where(Alert.created_at >= start_date)
    if end_date:
        severity_q = severity_q.where(Alert.created_at <= end_date)
    severity_q = severity_q.group_by(Alert.severity)
    sev_result = await db.execute(severity_q)
    by_severity = {row[0].value: row[1] for row in sev_result.all()}

    # By type
    type_q = (
        select(Alert.alert_type, func.count())
        .where(Alert.org_id == org_id)
    )
    if start_date:
        type_q = type_q.where(Alert.created_at >= start_date)
    if end_date:
        type_q = type_q.where(Alert.created_at <= end_date)
    type_q = type_q.group_by(Alert.alert_type)
    type_result = await db.execute(type_q)
    by_type = {row[0].value: row[1] for row in type_result.all()}

    # By camera
    camera_q = (
        select(Camera.name, func.count(Alert.id))
        .join(Camera, Alert.camera_id == Camera.id)
        .where(Alert.org_id == org_id)
    )
    if start_date:
        camera_q = camera_q.where(Alert.created_at >= start_date)
    if end_date:
        camera_q = camera_q.where(Alert.created_at <= end_date)
    camera_q = camera_q.group_by(Camera.name)
    cam_result = await db.execute(camera_q)
    by_camera = {row[0]: row[1] for row in cam_result.all()}

    # By status
    status_q = (
        select(Alert.status, func.count())
        .where(Alert.org_id == org_id)
    )
    if start_date:
        status_q = status_q.where(Alert.created_at >= start_date)
    if end_date:
        status_q = status_q.where(Alert.created_at <= end_date)
    status_q = status_q.group_by(Alert.status)
    status_result = await db.execute(status_q)
    by_status = {row[0].value: row[1] for row in status_result.all()}

    # False positive rate
    resolved_count = by_status.get("resolved", 0) + by_status.get("false_positive", 0)
    fp_count = by_status.get("false_positive", 0)
    false_positive_rate = fp_count / resolved_count if resolved_count > 0 else 0.0

    return {
        "total": total,
        "by_severity": by_severity,
        "by_type": by_type,
        "by_camera": by_camera,
        "by_status": by_status,
        "false_positive_rate": round(false_positive_rate, 4),
    }


# ── Helpers ──────────────────────────────────────────────────────────────


async def _get_alert(
    db: AsyncSession,
    alert_id: uuid.UUID,
    org_id: Optional[uuid.UUID] = None,
) -> Alert:
    """Retrieve an alert by ID with optional org filter.

    Raises:
        NotFoundError: If the alert does not exist.
    """
    query = select(Alert).where(Alert.id == alert_id)
    if org_id:
        query = query.where(Alert.org_id == org_id)

    result = await db.execute(query)
    alert = result.scalar_one_or_none()

    if alert is None:
        raise NotFoundError(resource="Alert", identifier=alert_id)

    return alert
