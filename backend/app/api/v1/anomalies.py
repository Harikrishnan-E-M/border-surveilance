"""
Anomaly Detection API endpoints.

Provides anomaly event listing, statistics, individual event details,
acknowledgement, baseline management, and sensitivity configuration.
"""

from __future__ import annotations

import math
import uuid
from datetime import datetime, timezone
from typing import Optional

import structlog
from fastapi import APIRouter, Depends, HTTPException, Query, status
from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession

from app.database import get_db_session
from app.exceptions import NotFoundError, ValidationError
from app.middleware.auth import JWTBearer, TokenPayload
from app.models.user import User
from app.schemas.anomaly import (
    AcknowledgeRequest,
    AnomalyEventList,
    AnomalyEventResponse,
    AnomalySeverityEnum,
    AnomalyStatsResponse,
    AnomalyTypeEnum,
    BaselineRebuildRequest,
    BaselineStatusResponse,
    SensitivityConfig,
    SensitivityConfigResponse,
)
from app.schemas.common import SuccessResponse
from app.services import anomaly_service

logger = structlog.stdlib.get_logger(__name__)

router = APIRouter()


# ---------------------------------------------------------------------------
# Helpers
# ---------------------------------------------------------------------------


async def _get_current_user(
    token: TokenPayload = Depends(JWTBearer()),
    db: AsyncSession = Depends(get_db_session),
) -> User:
    """Validate the JWT token and load the active user."""
    result = await db.execute(
        select(User).where(User.id == uuid.UUID(token.sub), User.is_active.is_(True))
    )
    user = result.scalars().first()
    if not user:
        raise HTTPException(status_code=401, detail="User not found or deactivated.")
    return user


async def _get_redis():
    """Get the async Redis client. Returns None if unavailable."""
    try:
        from app.dependencies import get_redis
        return await get_redis()
    except Exception:
        return None


def _serialize_anomaly(event) -> dict:
    """Convert an AnomalyEvent ORM instance to a response dict."""
    camera_name = ""
    zone_name = None

    if hasattr(event, "camera") and event.camera:
        camera_name = event.camera.name
    if hasattr(event, "zone") and event.zone:
        zone_name = event.zone.name

    return {
        "id": str(event.id),
        "org_id": str(event.org_id),
        "camera_id": str(event.camera_id),
        "camera_name": camera_name,
        "zone_id": str(event.zone_id) if event.zone_id else None,
        "zone_name": zone_name,
        "anomaly_type": event.anomaly_type.value if hasattr(event.anomaly_type, "value") else str(event.anomaly_type),
        "severity": event.severity.value if hasattr(event.severity, "value") else str(event.severity),
        "confidence": event.confidence,
        "description": event.description,
        "baseline_value": event.baseline_value,
        "observed_value": event.observed_value,
        "deviation_sigma": event.deviation_sigma,
        "metadata_json": event.metadata_json,
        "thumbnail_path": event.thumbnail_path,
        "is_acknowledged": event.is_acknowledged,
        "acknowledged_by": str(event.acknowledged_by) if event.acknowledged_by else None,
        "acknowledged_at": event.acknowledged_at.isoformat() if event.acknowledged_at else None,
        "created_at": event.created_at.isoformat() if event.created_at else None,
    }


# ---------------------------------------------------------------------------
# GET /anomalies - List anomaly events
# ---------------------------------------------------------------------------


@router.get(
    "",
    response_model=SuccessResponse,
    summary="List anomaly events with filters",
)
async def list_anomalies(
    camera_id: Optional[uuid.UUID] = Query(None, description="Filter by camera"),
    zone_id: Optional[uuid.UUID] = Query(None, description="Filter by zone"),
    anomaly_type: Optional[str] = Query(None, description="Filter by type: count, temporal, spatial, behavioral, frequency"),
    severity: Optional[str] = Query(None, description="Filter by severity: info, warning, critical"),
    is_acknowledged: Optional[bool] = Query(None, description="Filter by acknowledgement status"),
    start_date: Optional[datetime] = Query(None, description="Start of date range (ISO-8601)"),
    end_date: Optional[datetime] = Query(None, description="End of date range (ISO-8601)"),
    page: int = Query(1, ge=1, description="Page number"),
    page_size: int = Query(20, ge=1, le=100, description="Items per page"),
    user: User = Depends(_get_current_user),
    db: AsyncSession = Depends(get_db_session),
) -> dict:
    """Return paginated anomaly events for the user's organization."""
    events, total = await anomaly_service.get_anomalies(
        db=db,
        org_id=user.org_id,
        camera_id=camera_id,
        zone_id=zone_id,
        anomaly_type=anomaly_type,
        severity=severity,
        is_acknowledged=is_acknowledged,
        start_date=start_date,
        end_date=end_date,
        page=page,
        page_size=page_size,
    )

    data = [_serialize_anomaly(e) for e in events]

    return {
        "status": "success",
        "data": data,
        "meta": {
            "page": page,
            "page_size": page_size,
            "total": total,
            "total_pages": math.ceil(total / page_size) if page_size else 0,
        },
    }


# ---------------------------------------------------------------------------
# GET /anomalies/stats - Anomaly statistics
# ---------------------------------------------------------------------------


@router.get(
    "/stats",
    response_model=SuccessResponse,
    summary="Get anomaly statistics",
)
async def anomaly_stats(
    start_date: Optional[datetime] = Query(None),
    end_date: Optional[datetime] = Query(None),
    camera_id: Optional[uuid.UUID] = Query(None),
    user: User = Depends(_get_current_user),
    db: AsyncSession = Depends(get_db_session),
) -> dict:
    """Return aggregated anomaly statistics for the organization."""
    stats = await anomaly_service.get_anomaly_stats(
        db=db,
        org_id=user.org_id,
        start_date=start_date,
        end_date=end_date,
        camera_id=camera_id,
    )

    return {
        "status": "success",
        "data": stats,
    }


# ---------------------------------------------------------------------------
# GET /anomalies/baselines - Baseline status
# ---------------------------------------------------------------------------


@router.get(
    "/baselines",
    response_model=SuccessResponse,
    summary="Get baseline status per camera",
)
async def baseline_status(
    user: User = Depends(_get_current_user),
    db: AsyncSession = Depends(get_db_session),
) -> dict:
    """Return baseline freshness status for each camera in the org."""
    statuses = await anomaly_service.get_baseline_status(
        db=db,
        org_id=user.org_id,
    )

    return {
        "status": "success",
        "data": statuses,
    }


# ---------------------------------------------------------------------------
# POST /anomalies/baselines/rebuild - Trigger baseline rebuild
# ---------------------------------------------------------------------------


@router.post(
    "/baselines/rebuild",
    response_model=SuccessResponse,
    summary="Trigger baseline rebuild",
)
async def rebuild_baselines(
    body: BaselineRebuildRequest,
    user: User = Depends(_get_current_user),
    db: AsyncSession = Depends(get_db_session),
) -> dict:
    """Trigger a baseline rebuild from historical data.

    For large datasets this is dispatched to a Celery worker. For
    immediate (smaller) rebuilds it executes inline.
    """
    redis = await _get_redis()

    # Try to dispatch as a background task
    try:
        from app.workers.anomaly_tasks import rebuild_baselines_task

        camera_id_strs = [str(cid) for cid in body.camera_ids] if body.camera_ids else None
        task = rebuild_baselines_task.delay(
            org_id=str(user.org_id),
            camera_ids=camera_id_strs,
            days=body.days,
        )

        logger.info(
            "anomaly.baseline_rebuild_dispatched",
            task_id=task.id,
            cameras=len(body.camera_ids) if body.camera_ids else "all",
        )

        return {
            "status": "success",
            "data": {
                "task_id": task.id,
                "status": "dispatched",
            },
            "message": "Baseline rebuild task has been dispatched.",
        }
    except Exception as exc:
        # Fallback: run inline
        logger.warning("Celery dispatch failed, running inline", error=str(exc))
        results = await anomaly_service.rebuild_baselines(
            db=db,
            org_id=user.org_id,
            camera_ids=body.camera_ids,
            days=body.days,
            redis_client=redis,
        )

        return {
            "status": "success",
            "data": results,
            "message": "Baseline rebuild completed inline.",
        }


# ---------------------------------------------------------------------------
# PUT /anomalies/sensitivity - Configure detection sensitivity
# ---------------------------------------------------------------------------


@router.put(
    "/sensitivity",
    response_model=SuccessResponse,
    summary="Configure detection sensitivity",
)
async def update_sensitivity(
    body: SensitivityConfig,
    user: User = Depends(_get_current_user),
    db: AsyncSession = Depends(get_db_session),
) -> dict:
    """Update anomaly detection sensitivity for the organization or a specific camera."""
    redis = await _get_redis()

    resolved = body.resolve()

    config = await anomaly_service.configure_sensitivity(
        org_id=user.org_id,
        camera_id=body.camera_id,
        sensitivity=body.sensitivity.value,
        custom_overrides=resolved,
        redis_client=redis,
    )

    return {
        "status": "success",
        "data": config,
        "message": f"Sensitivity set to '{body.sensitivity.value}' successfully.",
    }


# ---------------------------------------------------------------------------
# GET /anomalies/{id} - Single anomaly detail
# ---------------------------------------------------------------------------


@router.get(
    "/{anomaly_id}",
    response_model=SuccessResponse,
    summary="Get single anomaly event detail",
)
async def get_anomaly_detail(
    anomaly_id: uuid.UUID,
    user: User = Depends(_get_current_user),
    db: AsyncSession = Depends(get_db_session),
) -> dict:
    """Return full details of a single anomaly event."""
    event = await anomaly_service.get_anomaly_by_id(
        db=db,
        org_id=user.org_id,
        anomaly_id=anomaly_id,
    )

    if event is None:
        raise NotFoundError(resource="AnomalyEvent", identifier=str(anomaly_id))

    return {
        "status": "success",
        "data": _serialize_anomaly(event),
    }


# ---------------------------------------------------------------------------
# POST /anomalies/{id}/acknowledge - Acknowledge an anomaly
# ---------------------------------------------------------------------------


@router.post(
    "/{anomaly_id}/acknowledge",
    response_model=SuccessResponse,
    summary="Acknowledge an anomaly event",
)
async def acknowledge_anomaly(
    anomaly_id: uuid.UUID,
    body: Optional[AcknowledgeRequest] = None,
    user: User = Depends(_get_current_user),
    db: AsyncSession = Depends(get_db_session),
) -> dict:
    """Mark an anomaly event as acknowledged by the current user."""
    notes = body.notes if body else None

    event = await anomaly_service.acknowledge_anomaly(
        db=db,
        org_id=user.org_id,
        anomaly_id=anomaly_id,
        user_id=user.id,
        notes=notes,
    )

    if event is None:
        raise NotFoundError(resource="AnomalyEvent", identifier=str(anomaly_id))

    return {
        "status": "success",
        "data": _serialize_anomaly(event),
        "message": "Anomaly acknowledged successfully.",
    }
