"""
Dashboard API endpoints.

Provides aggregated stats, alert trends, and alert distribution
data for the frontend dashboard overview page.
"""

from __future__ import annotations

import uuid
from datetime import datetime, timedelta, timezone

import structlog
from fastapi import APIRouter, Depends, HTTPException
from sqlalchemy import String, case, cast, func, select
from sqlalchemy.ext.asyncio import AsyncSession

from app.database import get_db_session
from app.middleware.auth import JWTBearer, TokenPayload
from app.models.alert import Alert
from app.models.camera import Camera
from app.models.person import Person
from app.models.rule import Rule
from app.models.user import User

logger = structlog.stdlib.get_logger(__name__)

router = APIRouter()


# ── Helper ────────────────────────────────────────────────────────────────────

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


# ── GET /stats ────────────────────────────────────────────────────────────────

@router.get("/stats", summary="Dashboard statistics")
async def get_dashboard_stats(
    user: User = Depends(_get_current_user),
    db: AsyncSession = Depends(get_db_session),
) -> dict:
    """Aggregate counts for cameras, alerts, persons, and rules."""
    org_id = user.org_id

    # Camera counts
    cam_result = await db.execute(
        select(
            func.count(Camera.id).label("total"),
            func.count(Camera.id).filter(Camera.is_online.is_(True)).label("online"),
            func.count(Camera.id).filter(Camera.is_online.is_(False)).label("offline"),
        ).where(Camera.org_id == org_id, Camera.is_active.is_(True))
    )
    cam_row = cam_result.one()

    # Alerts today
    today_start = datetime.now(timezone.utc).replace(
        hour=0, minute=0, second=0, microsecond=0
    ).replace(tzinfo=None)

    alert_result = await db.execute(
        select(
            func.count(Alert.id).label("today"),
            func.count(Alert.id).filter(
                cast(Alert.severity, String) == "critical"
            ).label("critical"),
            func.count(Alert.id).filter(
                cast(Alert.severity, String) == "warning"
            ).label("warning_count"),
            func.count(Alert.id).filter(
                cast(Alert.severity, String) == "info"
            ).label("info_count"),
        ).where(Alert.org_id == org_id, Alert.created_at >= today_start)
    )
    alert_row = alert_result.one()

    # Persons enrolled
    person_result = await db.execute(
        select(func.count(Person.id)).where(
            Person.org_id == org_id, Person.is_active.is_(True)
        )
    )
    person_count = person_result.scalar() or 0

    # Active rules
    rule_result = await db.execute(
        select(func.count(Rule.id)).where(
            Rule.org_id == org_id, Rule.is_active.is_(True)
        )
    )
    rule_count = rule_result.scalar() or 0

    return {
        "cameras": {
            "total": cam_row.total or 0,
            "online": cam_row.online or 0,
            "offline": cam_row.offline or 0,
            "degraded": 0,
        },
        "alerts": {
            "today": alert_row.today or 0,
            "critical": alert_row.critical or 0,
            "warning": alert_row.warning_count or 0,
            "info": alert_row.info_count or 0,
        },
        "persons": {
            "enrolled": person_count,
        },
        "rules": {
            "active": rule_count,
        },
    }


# ── GET /alert-trends ────────────────────────────────────────────────────────

@router.get("/alert-trends", summary="Alert trends over the last 7 days")
async def get_alert_trends(
    user: User = Depends(_get_current_user),
    db: AsyncSession = Depends(get_db_session),
) -> list[dict]:
    """Return daily alert counts by severity for the last 7 days."""
    org_id = user.org_id
    now = datetime.now(timezone.utc).replace(tzinfo=None)
    seven_days_ago = (now - timedelta(days=7)).replace(
        hour=0, minute=0, second=0, microsecond=0
    )

    result = await db.execute(
        select(
            func.date(Alert.created_at).label("day"),
            func.count(Alert.id).filter(
                cast(Alert.severity, String) == "critical"
            ).label("critical"),
            func.count(Alert.id).filter(
                cast(Alert.severity, String).in_(["high", "medium", "warning"])
            ).label("warning"),
            func.count(Alert.id).filter(
                cast(Alert.severity, String).in_(["low", "info"])
            ).label("info"),
        )
        .where(Alert.org_id == org_id, Alert.created_at >= seven_days_ago)
        .group_by(func.date(Alert.created_at))
        .order_by(func.date(Alert.created_at))
    )
    rows = result.all()

    # Build a full 7-day series filling gaps with zeros
    day_map = {}
    for row in rows:
        day_str = str(row.day)
        day_map[day_str] = {
            "date": day_str,
            "critical": row.critical or 0,
            "warning": row.warning or 0,
            "info": row.info or 0,
        }

    trends = []
    for i in range(7):
        day = (seven_days_ago + timedelta(days=i)).strftime("%Y-%m-%d")
        trends.append(day_map.get(day, {"date": day, "critical": 0, "warning": 0, "info": 0}))

    return trends


# ── GET /alert-distribution ──────────────────────────────────────────────────

@router.get("/alert-distribution", summary="Alert distribution by type")
async def get_alert_distribution(
    user: User = Depends(_get_current_user),
    db: AsyncSession = Depends(get_db_session),
) -> list[dict]:
    """Return alert counts grouped by alert_type."""
    org_id = user.org_id

    result = await db.execute(
        select(
            cast(Alert.alert_type, String).label("alert_type"),
            func.count(Alert.id).label("count"),
        )
        .where(Alert.org_id == org_id)
        .group_by(cast(Alert.alert_type, String))
        .order_by(func.count(Alert.id).desc())
        .limit(10)
    )
    rows = result.all()

    return [
        {
            "type": row.alert_type.replace("_", " ").title(),
            "count": row.count,
        }
        for row in rows
    ]
