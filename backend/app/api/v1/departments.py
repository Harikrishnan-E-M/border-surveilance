"""
Department API endpoints.

Provides a list of departments derived from Person records.
"""

from __future__ import annotations

import uuid

import structlog
from fastapi import APIRouter, Depends, HTTPException, status
from sqlalchemy import func, select
from sqlalchemy.ext.asyncio import AsyncSession

from app.database import get_db_session
from app.middleware.auth import JWTBearer, TokenPayload
from app.models.person import Person
from app.models.user import User
from app.schemas.common import SuccessResponse

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
        raise HTTPException(
            status_code=status.HTTP_401_UNAUTHORIZED,
            detail="User not found or deactivated.",
        )
    return user


# ── GET / - List departments ──────────────────────────────────────────────────


@router.get(
    "/",
    response_model=SuccessResponse,
    summary="List departments",
)
async def list_departments(
    user: User = Depends(_get_current_user),
    db: AsyncSession = Depends(get_db_session),
) -> dict:
    """Return distinct department names from person records.

    Departments are derived from the Person model rather than being
    a separate entity. This provides a flat list for filter dropdowns.
    """
    result = await db.execute(
        select(func.distinct(Person.department))
        .where(
            Person.org_id == user.org_id,
            Person.department.isnot(None),
            Person.department != "",
        )
        .order_by(Person.department)
    )
    departments = [
        {"id": dept, "name": dept}
        for dept in result.scalars().all()
        if dept
    ]

    return {
        "status": "success",
        "data": departments,
    }
