"""
Administration API endpoints.

Provides user management, organization settings, audit logging,
system health monitoring, and API key management.
"""

from __future__ import annotations

import csv
import hashlib
import io
import math
import secrets
import uuid
from datetime import datetime, timezone
from typing import Optional

import structlog
from fastapi import APIRouter, Depends, HTTPException, Query, status
from fastapi.responses import StreamingResponse
from pydantic import BaseModel, Field
from sqlalchemy import func, select, desc
from sqlalchemy.ext.asyncio import AsyncSession

from app.database import get_db_session
from app.exceptions import (
    AuthorizationError,
    DuplicateError,
    NotFoundError,
    ValidationError,
)
from app.middleware.auth import JWTBearer, TokenPayload
from app.models.organization import Organization
from app.models.user import APIKey, User, UserRole
from app.schemas.common import ErrorResponse, SuccessResponse

logger = structlog.stdlib.get_logger(__name__)

router = APIRouter()


# ---------------------------------------------------------------------------
# Pydantic schemas
# ---------------------------------------------------------------------------


class CreateUserRequest(BaseModel):
    email: str = Field(..., min_length=5, max_length=320)
    full_name: str = Field(..., min_length=1, max_length=255)
    password: str = Field(..., min_length=8, max_length=128)
    role: str = Field(default="viewer", description="User role: super_admin, org_admin, manager, operator, viewer")
    phone: str | None = Field(default=None, max_length=20)


class UpdateUserRequest(BaseModel):
    full_name: str | None = Field(default=None, max_length=255)
    role: str | None = None
    phone: str | None = Field(default=None, max_length=20)
    is_active: bool | None = None


class UserResponse(BaseModel):
    id: uuid.UUID
    org_id: uuid.UUID
    email: str
    full_name: str
    phone: str | None = None
    role: str
    avatar_url: str | None = None
    is_active: bool
    last_login: datetime | None = None
    created_at: datetime
    updated_at: datetime

    class Config:
        from_attributes = True


class OrgSettingsUpdate(BaseModel):
    name: str | None = Field(default=None, max_length=255)
    logo_url: str | None = Field(default=None, max_length=1024)
    timezone: str | None = Field(default=None, max_length=64)


class CreateAPIKeyRequest(BaseModel):
    name: str = Field(..., min_length=1, max_length=255, description="Descriptive name for the API key")
    permissions: dict | None = Field(default=None, description="Optional permission scopes")
    expires_at: datetime | None = Field(default=None, description="Optional expiration datetime")


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


def _require_org_admin(user: User) -> None:
    """Raise 403 if the user is not an org admin or super admin."""
    allowed = {UserRole.SUPER_ADMIN, UserRole.ORG_ADMIN}
    if user.role not in allowed:
        raise AuthorizationError(message="Organization admin role or higher is required.")


def _user_to_response(user: User) -> dict:
    return UserResponse(
        id=user.id,
        org_id=user.org_id,
        email=user.email,
        full_name=user.full_name,
        phone=user.phone,
        role=user.role.value if isinstance(user.role, UserRole) else user.role,
        avatar_url=user.avatar_url,
        is_active=user.is_active,
        last_login=user.last_login,
        created_at=user.created_at,
        updated_at=user.updated_at,
    ).model_dump(mode="json")


def _hash_password(plain: str) -> str:
    """Hash a plaintext password with bcrypt."""
    import bcrypt
    return bcrypt.hashpw(plain.encode("utf-8"), bcrypt.gensalt()).decode("utf-8")


# ---------------------------------------------------------------------------
# GET /users - List users in organization
# ---------------------------------------------------------------------------


@router.get(
    "/users",
    response_model=SuccessResponse,
    summary="List users in organization",
)
async def list_users(
    search: str | None = Query(None, description="Search by name or email"),
    role: str | None = Query(None),
    is_active: bool | None = Query(None),
    page: int = Query(1, ge=1),
    page_size: int = Query(20, ge=1, le=100),
    user: User = Depends(_get_current_user),
    db: AsyncSession = Depends(get_db_session),
) -> dict:
    """Return a paginated list of users in the organization. Requires org_admin+."""
    _require_org_admin(user)

    query = select(User).where(User.org_id == user.org_id)

    if search:
        query = query.where(
            User.full_name.ilike(f"%{search}%") | User.email.ilike(f"%{search}%")
        )
    if role:
        try:
            query = query.where(User.role == UserRole(role))
        except ValueError:
            raise ValidationError(message=f"Invalid role: {role}")
    if is_active is not None:
        query = query.where(User.is_active == is_active)

    count_q = select(func.count()).select_from(query.subquery())
    total = (await db.execute(count_q)).scalar() or 0

    query = query.order_by(User.created_at.desc()).offset((page - 1) * page_size).limit(page_size)
    result = await db.execute(query)
    users = result.scalars().all()

    return {
        "status": "success",
        "data": [_user_to_response(u) for u in users],
        "meta": {
            "page": page,
            "page_size": page_size,
            "total": total,
            "total_pages": math.ceil(total / page_size) if page_size else 0,
        },
    }


# ---------------------------------------------------------------------------
# POST /users - Create/invite user
# ---------------------------------------------------------------------------


@router.post(
    "/users",
    response_model=SuccessResponse,
    status_code=status.HTTP_201_CREATED,
    summary="Create or invite a new user",
    responses={403: {"model": ErrorResponse}, 409: {"model": ErrorResponse}},
)
async def create_user(
    body: CreateUserRequest,
    user: User = Depends(_get_current_user),
    db: AsyncSession = Depends(get_db_session),
) -> dict:
    """Create a new user in the organization. Requires org_admin+."""
    _require_org_admin(user)

    # Check user limit
    from app.models.organization import Organization
    org_result = await db.execute(
        select(Organization).where(Organization.id == user.org_id)
    )
    org = org_result.scalars().first()

    current_count_result = await db.execute(
        select(func.count()).where(User.org_id == user.org_id)
    )
    current_count = current_count_result.scalar() or 0

    if org and current_count >= org.max_users:
        raise ValidationError(
            message=f"User limit reached ({org.max_users}). Upgrade your plan to add more users."
        )

    # Check duplicate email
    existing = await db.execute(
        select(User).where(User.email == body.email)
    )
    if existing.scalars().first():
        raise DuplicateError(message="An account with this email already exists.")

    # Validate role
    try:
        new_role = UserRole(body.role)
    except ValueError:
        valid = [r.value for r in UserRole]
        raise ValidationError(message=f"Invalid role '{body.role}'. Must be one of: {', '.join(valid)}")

    # Non-super-admins cannot create super_admin users
    if new_role == UserRole.SUPER_ADMIN and user.role != UserRole.SUPER_ADMIN:
        raise AuthorizationError(message="Only super admins can create super admin users.")

    new_user = User(
        org_id=user.org_id,
        email=body.email,
        hashed_password=_hash_password(body.password),
        full_name=body.full_name,
        role=new_role,
        phone=body.phone,
    )
    db.add(new_user)
    await db.flush()

    logger.info("User created", new_user_id=str(new_user.id), role=new_role.value, created_by=str(user.id))

    return {
        "status": "success",
        "data": _user_to_response(new_user),
        "message": "User created successfully.",
    }


# ---------------------------------------------------------------------------
# PUT /users/{user_id} - Update user role/status
# ---------------------------------------------------------------------------


@router.put(
    "/users/{user_id}",
    response_model=SuccessResponse,
    summary="Update user role or status",
    responses={403: {"model": ErrorResponse}, 404: {"model": ErrorResponse}},
)
async def update_user(
    user_id: uuid.UUID,
    body: UpdateUserRequest,
    user: User = Depends(_get_current_user),
    db: AsyncSession = Depends(get_db_session),
) -> dict:
    """Update a user's role, name, or active status. Requires org_admin+."""
    _require_org_admin(user)

    result = await db.execute(
        select(User).where(User.id == user_id, User.org_id == user.org_id)
    )
    target_user = result.scalars().first()
    if not target_user:
        raise NotFoundError(resource="User", identifier=str(user_id))

    # Prevent self-deactivation
    if user_id == user.id and body.is_active is False:
        raise ValidationError(message="You cannot deactivate your own account.")

    # Prevent self-demotion
    if user_id == user.id and body.role and body.role != user.role.value:
        raise ValidationError(message="You cannot change your own role.")

    update_data = body.model_dump(exclude_unset=True)

    if "role" in update_data:
        try:
            new_role = UserRole(update_data["role"])
            if new_role == UserRole.SUPER_ADMIN and user.role != UserRole.SUPER_ADMIN:
                raise AuthorizationError(message="Only super admins can assign super admin role.")
            update_data["role"] = new_role
        except ValueError:
            raise ValidationError(message=f"Invalid role: {update_data['role']}")

    for field, value in update_data.items():
        setattr(target_user, field, value)

    db.add(target_user)
    await db.flush()

    logger.info("User updated", target_user_id=str(user_id), updated_by=str(user.id))

    return {
        "status": "success",
        "data": _user_to_response(target_user),
        "message": "User updated successfully.",
    }


# ---------------------------------------------------------------------------
# DELETE /users/{user_id} - Deactivate user
# ---------------------------------------------------------------------------


@router.delete(
    "/users/{user_id}",
    response_model=SuccessResponse,
    summary="Deactivate a user",
    responses={403: {"model": ErrorResponse}, 404: {"model": ErrorResponse}},
)
async def deactivate_user(
    user_id: uuid.UUID,
    user: User = Depends(_get_current_user),
    db: AsyncSession = Depends(get_db_session),
) -> dict:
    """Deactivate a user account. Requires org_admin+."""
    _require_org_admin(user)

    if user_id == user.id:
        raise ValidationError(message="You cannot deactivate your own account.")

    result = await db.execute(
        select(User).where(User.id == user_id, User.org_id == user.org_id)
    )
    target_user = result.scalars().first()
    if not target_user:
        raise NotFoundError(resource="User", identifier=str(user_id))

    target_user.is_active = False
    db.add(target_user)
    await db.flush()

    logger.info("User deactivated", target_user_id=str(user_id), deactivated_by=str(user.id))

    return {
        "status": "success",
        "data": None,
        "message": "User deactivated successfully.",
    }


# ---------------------------------------------------------------------------
# GET /organization - Get organization settings
# ---------------------------------------------------------------------------


@router.get(
    "/organization",
    response_model=SuccessResponse,
    summary="Get organization settings",
)
async def get_organization(
    user: User = Depends(_get_current_user),
    db: AsyncSession = Depends(get_db_session),
) -> dict:
    """Return the organization's settings and plan details."""
    result = await db.execute(
        select(Organization).where(Organization.id == user.org_id)
    )
    org = result.scalars().first()
    if not org:
        raise NotFoundError(resource="Organization", identifier=str(user.org_id))

    # Get current usage counts
    user_count = (await db.execute(
        select(func.count()).where(User.org_id == user.org_id)
    )).scalar() or 0

    from app.models.camera import Camera
    camera_count = (await db.execute(
        select(func.count()).where(Camera.org_id == user.org_id, Camera.is_active.is_(True))
    )).scalar() or 0

    return {
        "status": "success",
        "data": {
            "id": str(org.id),
            "name": org.name,
            "slug": org.slug,
            "subscription_tier": org.subscription_tier.value if hasattr(org.subscription_tier, 'value') else org.subscription_tier,
            "max_cameras": org.max_cameras,
            "max_users": org.max_users,
            "logo_url": org.logo_url,
            "timezone": org.timezone,
            "is_active": org.is_active,
            "current_cameras": camera_count,
            "current_users": user_count,
            "created_at": org.created_at.isoformat() if org.created_at else None,
            "updated_at": org.updated_at.isoformat() if org.updated_at else None,
        },
    }


# ---------------------------------------------------------------------------
# PUT /organization - Update organization settings
# ---------------------------------------------------------------------------


@router.put(
    "/organization",
    response_model=SuccessResponse,
    summary="Update organization settings",
    responses={403: {"model": ErrorResponse}},
)
async def update_organization(
    body: OrgSettingsUpdate,
    user: User = Depends(_get_current_user),
    db: AsyncSession = Depends(get_db_session),
) -> dict:
    """Update organization settings. Requires org_admin+."""
    _require_org_admin(user)

    result = await db.execute(
        select(Organization).where(Organization.id == user.org_id)
    )
    org = result.scalars().first()
    if not org:
        raise NotFoundError(resource="Organization", identifier=str(user.org_id))

    update_data = body.model_dump(exclude_unset=True)
    for field, value in update_data.items():
        setattr(org, field, value)

    db.add(org)
    await db.flush()

    logger.info("Organization updated", org_id=str(org.id), updated_by=str(user.id))

    return {
        "status": "success",
        "data": {
            "id": str(org.id),
            "name": org.name,
            "slug": org.slug,
            "logo_url": org.logo_url,
            "timezone": org.timezone,
        },
        "message": "Organization settings updated successfully.",
    }


# ---------------------------------------------------------------------------
# GET /audit-log - Audit trail
# ---------------------------------------------------------------------------


@router.get(
    "/audit-log",
    response_model=SuccessResponse,
    summary="Get audit trail (paginated, filterable)",
)
async def get_audit_log(
    action: str | None = Query(None, description="Filter by action type"),
    user_id: uuid.UUID | None = Query(None, description="Filter by user ID"),
    start_date: datetime | None = Query(None),
    end_date: datetime | None = Query(None),
    page: int = Query(1, ge=1),
    page_size: int = Query(20, ge=1, le=100),
    user: User = Depends(_get_current_user),
) -> dict:
    """Return the organization's audit trail. Requires org_admin+."""
    _require_org_admin(user)

    try:
        from app.services.audit_service import get_audit_logs
        result = await get_audit_logs(
            org_id=str(user.org_id),
            action=action,
            user_id=str(user_id) if user_id else None,
            start_date=start_date,
            end_date=end_date,
            page=page,
            page_size=page_size,
        )
        return {
            "status": "success",
            "data": result.get("logs", []),
            "meta": result.get("meta", {"page": page, "page_size": page_size, "total": 0, "total_pages": 0}),
        }
    except ImportError:
        # Fallback to Redis-based audit log
        try:
            import json
            from app.dependencies import get_redis
            redis = await get_redis()
            key = f"audit_log:{user.org_id}"
            logs_raw = await redis.lrange(key, (page - 1) * page_size, page * page_size - 1)
            total = await redis.llen(key)
            logs = [json.loads(l) for l in logs_raw]

            return {
                "status": "success",
                "data": logs,
                "meta": {
                    "page": page,
                    "page_size": page_size,
                    "total": total,
                    "total_pages": math.ceil(total / page_size) if page_size else 0,
                },
            }
        except Exception:
            return {
                "status": "success",
                "data": [],
                "meta": {"page": page, "page_size": page_size, "total": 0, "total_pages": 0},
                "message": "Audit service is not configured.",
            }


# ---------------------------------------------------------------------------
# GET /system-health - System metrics
# ---------------------------------------------------------------------------


@router.get(
    "/system-health",
    response_model=SuccessResponse,
    summary="Get system health metrics",
)
async def system_health(
    user: User = Depends(_get_current_user),
) -> dict:
    """Return system health metrics including CPU, GPU, memory, and queue status."""
    health_data: dict = {
        "cpu": None,
        "memory": None,
        "gpu": None,
        "disk": None,
        "redis": None,
        "database": None,
        "queues": None,
    }

    # System metrics
    try:
        import psutil
        health_data["cpu"] = {
            "usage_percent": psutil.cpu_percent(interval=0.1),
            "count": psutil.cpu_count(),
            "load_avg": list(psutil.getloadavg()) if hasattr(psutil, "getloadavg") else None,
        }
        mem = psutil.virtual_memory()
        health_data["memory"] = {
            "total_gb": round(mem.total / (1024**3), 2),
            "available_gb": round(mem.available / (1024**3), 2),
            "used_percent": mem.percent,
        }
        disk = psutil.disk_usage("/")
        health_data["disk"] = {
            "total_gb": round(disk.total / (1024**3), 2),
            "free_gb": round(disk.free / (1024**3), 2),
            "used_percent": round(disk.percent, 1),
        }
    except ImportError:
        logger.debug("psutil not available for system metrics")

    # GPU metrics
    try:
        import subprocess
        result = subprocess.run(
            ["nvidia-smi", "--query-gpu=utilization.gpu,memory.used,memory.total,temperature.gpu",
             "--format=csv,noheader,nounits"],
            capture_output=True, text=True, timeout=5,
        )
        if result.returncode == 0:
            gpu_lines = result.stdout.strip().split("\n")
            gpus = []
            for line in gpu_lines:
                parts = [p.strip() for p in line.split(",")]
                if len(parts) == 4:
                    gpus.append({
                        "utilization_percent": float(parts[0]),
                        "memory_used_mb": float(parts[1]),
                        "memory_total_mb": float(parts[2]),
                        "temperature_c": float(parts[3]),
                    })
            health_data["gpu"] = gpus
    except (ImportError, FileNotFoundError, subprocess.TimeoutExpired, Exception):
        pass

    # Redis health
    try:
        from app.dependencies import get_redis
        redis = await get_redis()
        info = await redis.info("memory")
        health_data["redis"] = {
            "status": "healthy",
            "used_memory_mb": round(info.get("used_memory", 0) / (1024**2), 2),
            "connected_clients": (await redis.info("clients")).get("connected_clients"),
        }
    except Exception as exc:
        health_data["redis"] = {"status": "unhealthy", "error": str(exc)}

    # Database health
    try:
        from app.database import check_db_health
        health_data["database"] = await check_db_health()
    except Exception as exc:
        health_data["database"] = {"status": "unhealthy", "error": str(exc)}

    # Queue metrics (Celery)
    try:
        from app.dependencies import get_redis
        redis = await get_redis()
        queue_lengths = {}
        for queue_name in ["default", "alerts", "analytics", "recordings"]:
            length = await redis.llen(f"celery:{queue_name}")
            queue_lengths[queue_name] = length
        health_data["queues"] = queue_lengths
    except Exception:
        health_data["queues"] = {}

    return {
        "status": "success",
        "data": health_data,
    }


# ---------------------------------------------------------------------------
# POST /api-keys - Create API key
# ---------------------------------------------------------------------------


@router.post(
    "/api-keys",
    response_model=SuccessResponse,
    status_code=status.HTTP_201_CREATED,
    summary="Create a new API key",
    responses={403: {"model": ErrorResponse}},
)
async def create_api_key(
    body: CreateAPIKeyRequest,
    user: User = Depends(_get_current_user),
    db: AsyncSession = Depends(get_db_session),
) -> dict:
    """Create a new API key for programmatic access. Requires org_admin+."""
    _require_org_admin(user)

    # Generate a secure random key
    raw_key = f"vai_{secrets.token_urlsafe(48)}"
    key_hash = hashlib.sha256(raw_key.encode()).hexdigest()

    api_key = APIKey(
        org_id=user.org_id,
        user_id=user.id,
        key_hash=key_hash,
        name=body.name,
        permissions=body.permissions,
        expires_at=body.expires_at,
    )
    db.add(api_key)
    await db.flush()

    logger.info("API key created", key_id=str(api_key.id), name=body.name, user_id=str(user.id))

    return {
        "status": "success",
        "data": {
            "id": str(api_key.id),
            "name": api_key.name,
            "key": raw_key,  # Only shown once
            "permissions": api_key.permissions,
            "expires_at": api_key.expires_at.isoformat() if api_key.expires_at else None,
            "created_at": api_key.created_at.isoformat() if api_key.created_at else None,
        },
        "message": "API key created. Save the key now -- it will not be shown again.",
    }


# ---------------------------------------------------------------------------
# GET /api-keys - List API keys
# ---------------------------------------------------------------------------


@router.get(
    "/api-keys",
    response_model=SuccessResponse,
    summary="List API keys for organization",
)
async def list_api_keys(
    user: User = Depends(_get_current_user),
    db: AsyncSession = Depends(get_db_session),
) -> dict:
    """Return all API keys for the organization (hashed, not the raw key)."""
    result = await db.execute(
        select(APIKey)
        .where(APIKey.org_id == user.org_id)
        .order_by(APIKey.created_at.desc())
    )
    keys = result.scalars().all()

    data = [
        {
            "id": str(k.id),
            "name": k.name,
            "key_prefix": k.key_hash[:8] + "...",
            "permissions": k.permissions,
            "is_active": k.is_active,
            "expires_at": k.expires_at.isoformat() if k.expires_at else None,
            "created_at": k.created_at.isoformat() if k.created_at else None,
            "user_id": str(k.user_id),
        }
        for k in keys
    ]

    return {
        "status": "success",
        "data": data,
    }


# ---------------------------------------------------------------------------
# DELETE /api-keys/{key_id} - Revoke API key
# ---------------------------------------------------------------------------


@router.delete(
    "/api-keys/{key_id}",
    response_model=SuccessResponse,
    summary="Revoke an API key",
    responses={403: {"model": ErrorResponse}, 404: {"model": ErrorResponse}},
)
async def revoke_api_key(
    key_id: uuid.UUID,
    user: User = Depends(_get_current_user),
    db: AsyncSession = Depends(get_db_session),
) -> dict:
    """Revoke (deactivate) an API key. Requires org_admin+."""
    _require_org_admin(user)

    result = await db.execute(
        select(APIKey).where(APIKey.id == key_id, APIKey.org_id == user.org_id)
    )
    api_key = result.scalars().first()
    if not api_key:
        raise NotFoundError(resource="APIKey", identifier=str(key_id))

    api_key.is_active = False
    db.add(api_key)
    await db.flush()

    logger.info("API key revoked", key_id=str(key_id), revoked_by=str(user.id))

    return {
        "status": "success",
        "data": None,
        "message": "API key revoked successfully.",
    }


# ---------------------------------------------------------------------------
# POST /users/bulk-deactivate - Bulk deactivate users
# ---------------------------------------------------------------------------


class BulkDeactivateRequest(BaseModel):
    user_ids: list[uuid.UUID] = Field(..., min_length=1, description="User IDs to deactivate")


@router.post(
    "/users/bulk-deactivate",
    response_model=SuccessResponse,
    summary="Bulk deactivate users",
    responses={403: {"model": ErrorResponse}},
)
async def bulk_deactivate_users(
    body: BulkDeactivateRequest,
    user: User = Depends(_get_current_user),
    db: AsyncSession = Depends(get_db_session),
) -> dict:
    """Deactivate multiple users at once. Requires org_admin+."""
    _require_org_admin(user)

    # Prevent self-deactivation
    if user.id in body.user_ids:
        raise ValidationError(message="You cannot deactivate your own account.")

    result = await db.execute(
        select(User).where(
            User.id.in_(body.user_ids),
            User.org_id == user.org_id,
            User.is_active.is_(True),
        )
    )
    targets = result.scalars().all()

    deactivated_ids = []
    for target in targets:
        target.is_active = False
        db.add(target)
        deactivated_ids.append(str(target.id))

    await db.flush()

    logger.info(
        "Users bulk-deactivated",
        count=len(deactivated_ids),
        deactivated_by=str(user.id),
    )

    return {
        "status": "success",
        "data": {
            "deactivated_ids": deactivated_ids,
            "count": len(deactivated_ids),
        },
        "message": f"{len(deactivated_ids)} user(s) deactivated successfully.",
    }


# ---------------------------------------------------------------------------
# PATCH /users/{user_id}/status - Update user active status
# ---------------------------------------------------------------------------


class UserStatusUpdate(BaseModel):
    status: str = Field(..., description="New status: 'active', 'inactive', or 'suspended'")


@router.patch(
    "/users/{user_id}/status",
    response_model=SuccessResponse,
    summary="Update user active status",
    responses={403: {"model": ErrorResponse}, 404: {"model": ErrorResponse}},
)
async def update_user_status(
    user_id: uuid.UUID,
    body: UserStatusUpdate,
    user: User = Depends(_get_current_user),
    db: AsyncSession = Depends(get_db_session),
) -> dict:
    """Toggle a user's active/inactive/suspended status. Requires org_admin+."""
    _require_org_admin(user)

    if user_id == user.id:
        raise ValidationError(message="You cannot change your own status.")

    result = await db.execute(
        select(User).where(User.id == user_id, User.org_id == user.org_id)
    )
    target_user = result.scalars().first()
    if not target_user:
        raise NotFoundError(resource="User", identifier=str(user_id))

    status_map = {
        "active": True,
        "inactive": False,
        "suspended": False,
    }
    if body.status not in status_map:
        raise ValidationError(
            message=f"Invalid status '{body.status}'. Must be one of: active, inactive, suspended"
        )

    target_user.is_active = status_map[body.status]
    db.add(target_user)
    await db.flush()

    logger.info(
        "User status updated",
        target_user_id=str(user_id),
        new_status=body.status,
        updated_by=str(user.id),
    )

    return {
        "status": "success",
        "data": _user_to_response(target_user),
        "message": f"User status updated to '{body.status}'.",
    }


# ---------------------------------------------------------------------------
# GET /audit-logs/export - Export audit logs as CSV
# ---------------------------------------------------------------------------


@router.get(
    "/audit-logs/export",
    summary="Export audit logs as CSV",
    responses={403: {"model": ErrorResponse}},
)
async def export_audit_logs(
    format: str = Query("csv", description="Export format: csv"),
    search: str | None = Query(None),
    action: str | None = Query(None),
    resource_type: str | None = Query(None),
    user_id: uuid.UUID | None = Query(None, alias="user_id"),
    date_from: datetime | None = Query(None),
    date_to: datetime | None = Query(None),
    sort_by: str = Query("timestamp"),
    sort_dir: str = Query("desc"),
    page_size: int = Query(10000, ge=1, le=50000),
    user: User = Depends(_get_current_user),
) -> StreamingResponse:
    """Export audit logs as a downloadable CSV file. Requires org_admin+."""
    _require_org_admin(user)

    try:
        from app.services.audit_service import get_audit_logs
        result = await get_audit_logs(
            org_id=str(user.org_id),
            action=action,
            user_id=str(user_id) if user_id else None,
            start_date=date_from,
            end_date=date_to,
            page=1,
            page_size=page_size,
        )
        logs = result.get("logs", [])
    except Exception:
        logs = []

    # Filter by search / resource_type in memory
    if search:
        search_lower = search.lower()
        logs = [
            l for l in logs
            if search_lower in str(l.get("action", "")).lower()
            or search_lower in str(l.get("resource_type", "")).lower()
            or search_lower in str(l.get("details", "")).lower()
        ]
    if resource_type:
        logs = [l for l in logs if l.get("resource_type") == resource_type]

    # Sort
    reverse = sort_dir == "desc"
    logs.sort(key=lambda l: l.get(sort_by, ""), reverse=reverse)

    # Build CSV
    output = io.StringIO()
    writer = csv.writer(output)
    writer.writerow(["Timestamp", "Action", "User ID", "Resource Type", "Resource ID", "IP Address", "Details"])
    for log in logs:
        writer.writerow([
            log.get("timestamp", ""),
            log.get("action", ""),
            log.get("user_id", ""),
            log.get("resource_type", ""),
            log.get("resource_id", ""),
            log.get("ip_address", ""),
            str(log.get("details", "")),
        ])

    output.seek(0)
    return StreamingResponse(
        iter([output.getvalue()]),
        media_type="text/csv",
        headers={
            "Content-Disposition": f"attachment; filename=audit_logs_{datetime.now(timezone.utc).strftime('%Y%m%d_%H%M%S')}.csv"
        },
    )
