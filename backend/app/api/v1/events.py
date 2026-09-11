"""
Events API endpoints.

Provides recent events (alerts) for the dashboard activity feed.
"""

from __future__ import annotations

import uuid

import structlog
from fastapi import APIRouter, Depends, HTTPException, Query
from sqlalchemy import String, cast, select
from sqlalchemy.ext.asyncio import AsyncSession

from app.database import get_db_session
from app.middleware.auth import JWTBearer, TokenPayload
from app.models.alert import Alert
from app.models.camera import Camera
from app.models.user import User

logger = structlog.stdlib.get_logger(__name__)

router = APIRouter()


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


@router.get("/recent", summary="Get recent events")
async def get_recent_events(
    limit: int = Query(20, ge=1, le=100, description="Number of recent events"),
    user: User = Depends(_get_current_user),
    db: AsyncSession = Depends(get_db_session),
) -> list[dict]:
    """Return the most recent alerts as events for the dashboard feed."""
    org_id = user.org_id

    result = await db.execute(
        select(Alert)
        .where(Alert.org_id == org_id)
        .order_by(Alert.created_at.desc())
        .limit(limit)
    )
    alerts = result.scalars().all()

    events = []
    for a in alerts:
        # Get camera name
        camera_name = "Unknown"
        if a.camera:
            camera_name = a.camera.name
        else:
            cam_result = await db.execute(
                select(Camera.name).where(Camera.id == a.camera_id)
            )
            cam_name = cam_result.scalar()
            if cam_name:
                camera_name = cam_name

        # Map severity: critical stays critical, high/medium -> warning, low/info -> info
        raw_severity = a.severity.value if hasattr(a.severity, "value") else str(a.severity)
        if raw_severity == "critical":
            severity = "critical"
        elif raw_severity in ("high", "medium", "warning"):
            severity = "warning"
        else:
            severity = "info"

        raw_type = a.alert_type.value if hasattr(a.alert_type, "value") else str(a.alert_type)

        events.append({
            "id": str(a.id),
            "type": raw_type.replace("_", " ").title(),
            "camera": camera_name,
            "message": a.title or a.description or raw_type.replace("_", " ").title(),
            "severity": severity,
            "timestamp": a.created_at.isoformat() if a.created_at else None,
        })

    return events
