"""
Notification configuration API endpoints.

Provides management of notification channels (email, WhatsApp, Telegram,
SMS, webhook), test notifications, and user notification preferences.
"""

from __future__ import annotations

import uuid
from datetime import datetime, timezone
from typing import Optional

import structlog
from fastapi import APIRouter, Depends, HTTPException, Query, status
from pydantic import BaseModel, Field
from sqlalchemy import func, select
from sqlalchemy.ext.asyncio import AsyncSession

from app.database import get_db_session
from app.exceptions import (
    AuthorizationError,
    NotFoundError,
    ValidationError,
)
from app.middleware.auth import JWTBearer, TokenPayload
from app.models.user import User, UserRole
from app.schemas.common import ErrorResponse, SuccessResponse

logger = structlog.stdlib.get_logger(__name__)

router = APIRouter()


# ---------------------------------------------------------------------------
# Pydantic schemas
# ---------------------------------------------------------------------------


class NotificationChannelCreate(BaseModel):
    name: str = Field(..., min_length=1, max_length=255, description="Channel display name")
    channel_type: str = Field(
        ..., description="Channel type: email, whatsapp, telegram, sms, webhook"
    )
    config: dict = Field(
        ...,
        description="Channel-specific configuration. "
        "Email: {smtp_host, smtp_port, username, password, from_email}. "
        "WhatsApp: {api_url, api_key, phone_number}. "
        "Telegram: {bot_token, chat_id}. "
        "SMS: {provider, api_key, from_number}. "
        "Webhook: {url, method, headers, secret}.",
    )
    is_active: bool = Field(default=True)
    alert_severities: list[str] = Field(
        default_factory=lambda: ["critical", "high"],
        description="Alert severities that trigger this channel",
    )


class NotificationChannelUpdate(BaseModel):
    name: str | None = Field(default=None, max_length=255)
    config: dict | None = None
    is_active: bool | None = None
    alert_severities: list[str] | None = None


class NotificationPreferencesUpdate(BaseModel):
    email_enabled: bool = Field(default=True, description="Receive email notifications")
    push_enabled: bool = Field(default=True, description="Receive push notifications")
    sms_enabled: bool = Field(default=False, description="Receive SMS notifications")
    alert_severities: list[str] = Field(
        default_factory=lambda: ["critical", "high", "medium"],
        description="Minimum severity levels to receive",
    )
    quiet_hours_start: str | None = Field(
        default=None, description="Quiet hours start (HH:MM format)"
    )
    quiet_hours_end: str | None = Field(
        default=None, description="Quiet hours end (HH:MM format)"
    )
    alert_types: list[str] | None = Field(
        default=None, description="Specific alert types to subscribe to (null = all)"
    )


# ---------------------------------------------------------------------------
# Valid channel types
# ---------------------------------------------------------------------------

VALID_CHANNEL_TYPES = {"email", "whatsapp", "telegram", "sms", "webhook"}


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


def _require_manager(user: User) -> None:
    allowed = {UserRole.SUPER_ADMIN, UserRole.ORG_ADMIN, UserRole.MANAGER}
    if user.role not in allowed:
        raise AuthorizationError(message="Manager role or higher is required.")


# ---------------------------------------------------------------------------
# GET /channels - List notification channels
# ---------------------------------------------------------------------------


@router.get(
    "/channels",
    response_model=SuccessResponse,
    summary="List notification channels for organization",
)
async def list_channels(
    user: User = Depends(_get_current_user),
) -> dict:
    """Return all notification channels configured for the organization."""
    try:
        from app.services.notification_service import list_channels as svc_list
        channels = await svc_list(org_id=str(user.org_id))
        return {
            "status": "success",
            "data": channels,
        }
    except ImportError:
        logger.debug("Notification service not available")
        # Fallback: try Redis-based storage
        try:
            from app.dependencies import get_redis
            redis = await get_redis()
            key = f"notification_channels:{user.org_id}"
            import json
            channels_raw = await redis.get(key)
            channels = json.loads(channels_raw) if channels_raw else []
            return {
                "status": "success",
                "data": channels,
            }
        except Exception:
            return {
                "status": "success",
                "data": [],
                "message": "Notification service is not configured.",
            }


# ---------------------------------------------------------------------------
# POST /channels - Create notification channel
# ---------------------------------------------------------------------------


@router.post(
    "/channels",
    response_model=SuccessResponse,
    status_code=status.HTTP_201_CREATED,
    summary="Create a notification channel",
    responses={403: {"model": ErrorResponse}},
)
async def create_channel(
    body: NotificationChannelCreate,
    user: User = Depends(_get_current_user),
) -> dict:
    """Create a new notification channel. Requires manager role or above."""
    _require_manager(user)

    if body.channel_type not in VALID_CHANNEL_TYPES:
        raise ValidationError(
            message=f"Invalid channel_type '{body.channel_type}'. Must be one of: {', '.join(sorted(VALID_CHANNEL_TYPES))}"
        )

    # Validate channel-specific config
    _validate_channel_config(body.channel_type, body.config)

    channel_id = str(uuid.uuid4())
    channel_data = {
        "id": channel_id,
        "org_id": str(user.org_id),
        "name": body.name,
        "channel_type": body.channel_type,
        "config": body.config,
        "is_active": body.is_active,
        "alert_severities": body.alert_severities,
        "created_at": datetime.now(timezone.utc).isoformat(),
        "created_by": str(user.id),
    }

    try:
        from app.services.notification_service import create_channel as svc_create
        result = await svc_create(org_id=str(user.org_id), channel_data=channel_data)
        return {
            "status": "success",
            "data": result,
            "message": "Notification channel created successfully.",
        }
    except ImportError:
        # Fallback to Redis storage
        try:
            import json
            from app.dependencies import get_redis
            redis = await get_redis()
            key = f"notification_channels:{user.org_id}"
            channels_raw = await redis.get(key)
            channels = json.loads(channels_raw) if channels_raw else []
            channels.append(channel_data)
            await redis.set(key, json.dumps(channels))
        except Exception as exc:
            logger.error("Failed to store notification channel", error=str(exc))

        logger.info("Notification channel created", channel_id=channel_id, type=body.channel_type)

        return {
            "status": "success",
            "data": channel_data,
            "message": "Notification channel created successfully.",
        }


def _validate_channel_config(channel_type: str, config: dict) -> None:
    """Validate that required config fields are present for the channel type."""
    required_fields: dict[str, list[str]] = {
        "email": ["from_email"],
        "whatsapp": ["api_url", "api_key", "phone_number"],
        "telegram": ["bot_token", "chat_id"],
        "sms": ["provider", "api_key", "from_number"],
        "webhook": ["url"],
    }

    fields = required_fields.get(channel_type, [])
    missing = [f for f in fields if f not in config or not config[f]]
    if missing:
        raise ValidationError(
            message=f"Missing required config fields for {channel_type}: {', '.join(missing)}"
        )


# ---------------------------------------------------------------------------
# PUT /channels/{channel_id} - Update channel
# ---------------------------------------------------------------------------


@router.put(
    "/channels/{channel_id}",
    response_model=SuccessResponse,
    summary="Update notification channel",
    responses={404: {"model": ErrorResponse}},
)
async def update_channel(
    channel_id: uuid.UUID,
    body: NotificationChannelUpdate,
    user: User = Depends(_get_current_user),
) -> dict:
    """Update a notification channel configuration."""
    _require_manager(user)

    try:
        from app.services.notification_service import update_channel as svc_update
        result = await svc_update(
            org_id=str(user.org_id),
            channel_id=str(channel_id),
            updates=body.model_dump(exclude_unset=True),
        )
        if not result:
            raise NotFoundError(resource="NotificationChannel", identifier=str(channel_id))
        return {
            "status": "success",
            "data": result,
            "message": "Notification channel updated successfully.",
        }
    except ImportError:
        # Fallback to Redis
        try:
            import json
            from app.dependencies import get_redis
            redis = await get_redis()
            key = f"notification_channels:{user.org_id}"
            channels_raw = await redis.get(key)
            channels = json.loads(channels_raw) if channels_raw else []

            updated = False
            for ch in channels:
                if ch.get("id") == str(channel_id):
                    update_data = body.model_dump(exclude_unset=True)
                    ch.update(update_data)
                    ch["updated_at"] = datetime.now(timezone.utc).isoformat()
                    updated = True
                    break

            if not updated:
                raise NotFoundError(resource="NotificationChannel", identifier=str(channel_id))

            await redis.set(key, json.dumps(channels))
            return {
                "status": "success",
                "data": next(ch for ch in channels if ch.get("id") == str(channel_id)),
                "message": "Notification channel updated successfully.",
            }
        except NotFoundError:
            raise
        except Exception as exc:
            logger.error("Failed to update notification channel", error=str(exc))
            raise NotFoundError(resource="NotificationChannel", identifier=str(channel_id))


# ---------------------------------------------------------------------------
# DELETE /channels/{channel_id} - Delete channel
# ---------------------------------------------------------------------------


@router.delete(
    "/channels/{channel_id}",
    response_model=SuccessResponse,
    summary="Delete notification channel",
    responses={404: {"model": ErrorResponse}},
)
async def delete_channel(
    channel_id: uuid.UUID,
    user: User = Depends(_get_current_user),
) -> dict:
    """Delete a notification channel."""
    _require_manager(user)

    try:
        from app.services.notification_service import delete_channel as svc_delete
        deleted = await svc_delete(org_id=str(user.org_id), channel_id=str(channel_id))
        if not deleted:
            raise NotFoundError(resource="NotificationChannel", identifier=str(channel_id))
    except ImportError:
        try:
            import json
            from app.dependencies import get_redis
            redis = await get_redis()
            key = f"notification_channels:{user.org_id}"
            channels_raw = await redis.get(key)
            channels = json.loads(channels_raw) if channels_raw else []
            original_len = len(channels)
            channels = [ch for ch in channels if ch.get("id") != str(channel_id)]
            if len(channels) == original_len:
                raise NotFoundError(resource="NotificationChannel", identifier=str(channel_id))
            await redis.set(key, json.dumps(channels))
        except NotFoundError:
            raise
        except Exception as exc:
            logger.error("Failed to delete notification channel", error=str(exc))
            raise NotFoundError(resource="NotificationChannel", identifier=str(channel_id))

    logger.info("Notification channel deleted", channel_id=str(channel_id))

    return {
        "status": "success",
        "data": None,
        "message": "Notification channel deleted successfully.",
    }


# ---------------------------------------------------------------------------
# POST /channels/{channel_id}/test - Send test notification
# ---------------------------------------------------------------------------


@router.post(
    "/channels/{channel_id}/test",
    response_model=SuccessResponse,
    summary="Send a test notification via channel",
    responses={404: {"model": ErrorResponse}},
)
async def test_channel(
    channel_id: uuid.UUID,
    user: User = Depends(_get_current_user),
) -> dict:
    """Send a test notification through the specified channel."""
    _require_manager(user)

    try:
        from app.services.notification_service import send_test_notification
        result = await send_test_notification(
            org_id=str(user.org_id),
            channel_id=str(channel_id),
            user_name=user.full_name,
        )
        return {
            "status": "success",
            "data": result,
            "message": "Test notification sent successfully.",
        }
    except ImportError:
        logger.warning("Notification service not available for test")
        return {
            "status": "success",
            "data": {"sent": False, "reason": "Notification service is not configured."},
            "message": "Notification service is not configured. Test not sent.",
        }
    except Exception as exc:
        logger.error("Test notification failed", error=str(exc))
        raise ValidationError(message=f"Test notification failed: {str(exc)}")


# ---------------------------------------------------------------------------
# GET /preferences - Get user notification preferences
# ---------------------------------------------------------------------------


@router.get(
    "/preferences",
    response_model=SuccessResponse,
    summary="Get current user's notification preferences",
)
async def get_preferences(
    user: User = Depends(_get_current_user),
) -> dict:
    """Return the authenticated user's notification preferences."""
    try:
        from app.services.notification_service import get_user_preferences
        prefs = await get_user_preferences(user_id=str(user.id))
        return {
            "status": "success",
            "data": prefs,
        }
    except ImportError:
        # Fallback to Redis
        try:
            import json
            from app.dependencies import get_redis
            redis = await get_redis()
            key = f"notification_prefs:{user.id}"
            prefs_raw = await redis.get(key)
            prefs = json.loads(prefs_raw) if prefs_raw else {
                "email_enabled": True,
                "push_enabled": True,
                "sms_enabled": False,
                "alert_severities": ["critical", "high", "medium"],
                "quiet_hours_start": None,
                "quiet_hours_end": None,
                "alert_types": None,
            }
            return {
                "status": "success",
                "data": prefs,
            }
        except Exception:
            return {
                "status": "success",
                "data": {
                    "email_enabled": True,
                    "push_enabled": True,
                    "sms_enabled": False,
                    "alert_severities": ["critical", "high", "medium"],
                    "quiet_hours_start": None,
                    "quiet_hours_end": None,
                    "alert_types": None,
                },
            }


# ---------------------------------------------------------------------------
# PUT /preferences - Update user notification preferences
# ---------------------------------------------------------------------------


@router.put(
    "/preferences",
    response_model=SuccessResponse,
    summary="Update current user's notification preferences",
)
async def update_preferences(
    body: NotificationPreferencesUpdate,
    user: User = Depends(_get_current_user),
) -> dict:
    """Update the authenticated user's notification preferences."""
    prefs_data = body.model_dump()

    try:
        from app.services.notification_service import update_user_preferences
        result = await update_user_preferences(
            user_id=str(user.id),
            preferences=prefs_data,
        )
        return {
            "status": "success",
            "data": result,
            "message": "Notification preferences updated successfully.",
        }
    except ImportError:
        # Fallback to Redis
        try:
            import json
            from app.dependencies import get_redis
            redis = await get_redis()
            key = f"notification_prefs:{user.id}"
            await redis.set(key, json.dumps(prefs_data))
        except Exception as exc:
            logger.error("Failed to save notification preferences", error=str(exc))

        logger.info("Notification preferences updated", user_id=str(user.id))

        return {
            "status": "success",
            "data": prefs_data,
            "message": "Notification preferences updated successfully.",
        }
