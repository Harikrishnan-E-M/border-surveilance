"""
Authentication API endpoints.

Handles login, registration, token refresh, logout, password management,
and current-user profile retrieval/update.
"""

from __future__ import annotations

import uuid
from datetime import datetime, timezone

import structlog
from fastapi import APIRouter, Depends, HTTPException, status
from sqlalchemy import select, update
from sqlalchemy.ext.asyncio import AsyncSession

from app.config import get_settings
from app.database import get_db_session
from app.exceptions import (
    AuthenticationError,
    DuplicateError,
    NotFoundError,
    ValidationError,
)
from app.middleware.auth import (
    JWTBearer,
    TokenPayload,
    create_access_token,
    create_refresh_token,
    decode_refresh_token,
)
from app.models.organization import Organization
from app.models.user import User, UserRole
from app.schemas.auth import (
    ChangePasswordRequest,
    LoginRequest,
    LoginResponse,
    PasswordResetConfirm,
    PasswordResetRequest,
    RefreshTokenRequest,
    RegisterRequest,
    UserResponse,
    UserUpdate,
)
from app.schemas.common import ErrorResponse, SuccessResponse

logger = structlog.stdlib.get_logger(__name__)

router = APIRouter()

# ---------------------------------------------------------------------------
# Helpers
# ---------------------------------------------------------------------------

async def _get_current_user(
    token: TokenPayload = Depends(JWTBearer()),
    db: AsyncSession = Depends(get_db_session),
) -> User:
    """Resolve the authenticated user from the JWT token."""
    result = await db.execute(
        select(User).where(User.id == uuid.UUID(token.sub), User.is_active.is_(True))
    )
    user = result.scalars().first()
    if not user:
        raise AuthenticationError(message="User account not found or deactivated.")
    return user


def _hash_password(plain: str) -> str:
    """Hash a plaintext password with bcrypt."""
    import bcrypt
    return bcrypt.hashpw(plain.encode("utf-8"), bcrypt.gensalt()).decode("utf-8")


def _verify_password(plain: str, hashed: str) -> bool:
    """Verify a plaintext password against a bcrypt hash."""
    import bcrypt
    try:
        return bcrypt.checkpw(plain.encode("utf-8"), hashed.encode("utf-8"))
    except Exception:
        return False


def _slugify(name: str) -> str:
    """Convert an organization name to a URL-safe slug."""
    import re
    slug = name.lower().strip()
    slug = re.sub(r"[^\w\s-]", "", slug)
    slug = re.sub(r"[\s_]+", "-", slug)
    slug = re.sub(r"-+", "-", slug).strip("-")
    return slug


# ---------------------------------------------------------------------------
# POST /login
# ---------------------------------------------------------------------------

@router.post(
    "/login",
    response_model=SuccessResponse,
    status_code=status.HTTP_200_OK,
    summary="Login with email and password",
    responses={401: {"model": ErrorResponse}},
)
async def login(
    body: LoginRequest,
    db: AsyncSession = Depends(get_db_session),
) -> dict:
    """Authenticate a user with email/password and return JWT token pair."""
    logger.info("Login attempt", email=body.email)

    result = await db.execute(
        select(User).where(User.email == body.email)
    )
    user = result.scalars().first()

    import asyncio
    is_valid_pw = await asyncio.to_thread(_verify_password, body.password, user.hashed_password) if user else False
    if not user or not is_valid_pw:
        logger.warning("Login failed: invalid credentials", email=body.email)
        raise AuthenticationError(message="Invalid email or password.")

    if not user.is_active:
        logger.warning("Login failed: account deactivated", email=body.email)
        raise AuthenticationError(message="Account is deactivated. Contact your administrator.")

    # Generate token pair
    role_str = user.role.value if hasattr(user.role, "value") else str(user.role)
    access_token = create_access_token(
        user_id=str(user.id),
        org_id=str(user.org_id),
        role=role_str,
    )
    refresh_token = create_refresh_token(
        user_id=str(user.id),
        org_id=str(user.org_id),
        role=role_str,
    )

    # Update last login timestamp
    user.last_login = datetime.now(timezone.utc).replace(tzinfo=None)
    db.add(user)
    await db.flush()

    logger.info("Login successful", user_id=str(user.id), role=role_str)

    return {
        "status": "success",
        "data": {
            "access_token": access_token,
            "refresh_token": refresh_token,
            "token_type": "bearer",
            "user": UserResponse.model_validate(user).model_dump(mode="json"),
        },
    }


# ---------------------------------------------------------------------------
# POST /register
# ---------------------------------------------------------------------------

@router.post(
    "/register",
    response_model=SuccessResponse,
    status_code=status.HTTP_201_CREATED,
    summary="Register a new organization and admin user",
    responses={409: {"model": ErrorResponse}},
)
async def register(
    body: RegisterRequest,
    db: AsyncSession = Depends(get_db_session),
) -> dict:
    """Self-service registration: creates a new organization and its first admin user."""
    logger.info("Registration attempt", email=body.email, org_name=body.org_name)

    # Check for duplicate email
    existing_user = await db.execute(
        select(User).where(User.email == body.email)
    )
    if existing_user.scalars().first():
        raise DuplicateError(message="An account with this email already exists.")

    # Check for duplicate org slug
    slug = _slugify(body.org_name)
    existing_org = await db.execute(
        select(Organization).where(Organization.slug == slug)
    )
    if existing_org.scalars().first():
        raise DuplicateError(message="An organization with this name already exists.")

    # Create organization
    org = Organization(
        name=body.org_name,
        slug=slug,
    )
    db.add(org)
    await db.flush()

    # Create admin user
    user = User(
        org_id=org.id,
        email=body.email,
        hashed_password=_hash_password(body.password),
        full_name=body.full_name,
        role=UserRole.ORG_ADMIN,
        last_login=datetime.now(timezone.utc),
    )
    db.add(user)
    await db.flush()

    # Generate token pair
    access_token = create_access_token(
        user_id=str(user.id),
        org_id=str(org.id),
        role=user.role.value,
    )
    refresh_token = create_refresh_token(
        user_id=str(user.id),
        org_id=str(org.id),
        role=user.role.value,
    )

    logger.info("Registration successful", user_id=str(user.id), org_id=str(org.id))

    return {
        "status": "success",
        "data": {
            "access_token": access_token,
            "refresh_token": refresh_token,
            "token_type": "bearer",
            "user": UserResponse.model_validate(user).model_dump(mode="json"),
        },
    }


# ---------------------------------------------------------------------------
# POST /refresh
# ---------------------------------------------------------------------------

@router.post(
    "/refresh",
    response_model=SuccessResponse,
    status_code=status.HTTP_200_OK,
    summary="Refresh access token",
    responses={401: {"model": ErrorResponse}},
)
async def refresh_token(
    body: RefreshTokenRequest,
    db: AsyncSession = Depends(get_db_session),
) -> dict:
    """Exchange a valid refresh token for a new access/refresh token pair."""
    payload = decode_refresh_token(body.refresh_token)

    # Verify user still exists and is active
    result = await db.execute(
        select(User).where(User.id == uuid.UUID(payload.sub), User.is_active.is_(True))
    )
    user = result.scalars().first()
    if not user:
        raise AuthenticationError(message="User account not found or deactivated.")

    access_token = create_access_token(
        user_id=str(user.id),
        org_id=str(user.org_id),
        role=user.role.value,
    )
    new_refresh_token = create_refresh_token(
        user_id=str(user.id),
        org_id=str(user.org_id),
        role=user.role.value,
    )

    logger.info("Token refreshed", user_id=str(user.id))

    return {
        "status": "success",
        "data": {
            "access_token": access_token,
            "refresh_token": new_refresh_token,
            "token_type": "bearer",
        },
    }


# ---------------------------------------------------------------------------
# POST /logout
# ---------------------------------------------------------------------------

@router.post(
    "/logout",
    response_model=SuccessResponse,
    status_code=status.HTTP_200_OK,
    summary="Invalidate refresh token",
)
async def logout(
    body: RefreshTokenRequest,
    user: User = Depends(_get_current_user),
    db: AsyncSession = Depends(get_db_session),
) -> dict:
    """Invalidate the supplied refresh token.

    The token JTI is added to a server-side deny-list stored in Redis
    so it cannot be reused.
    """
    import redis.asyncio as aioredis
    settings = get_settings()

    payload = decode_refresh_token(body.refresh_token)

    # Verify the refresh token belongs to the authenticated user
    if payload.sub != str(user.id):
        raise AuthenticationError(message="Refresh token does not belong to the authenticated user.")

    # Add the JTI to Redis deny-list with TTL matching token expiry
    try:
        redis_client = aioredis.from_url(settings.REDIS_URL, decode_responses=True)
        ttl = max(payload.exp - int(datetime.now(timezone.utc).timestamp()), 0)
        await redis_client.setex(f"token:blacklist:{payload.jti}", ttl, "1")
        await redis_client.aclose()
    except Exception as exc:
        logger.error("Failed to blacklist token in Redis", error=str(exc))
        # Proceed anyway -- token will expire naturally

    logger.info("User logged out", user_id=str(user.id))

    return {
        "status": "success",
        "data": None,
        "message": "Successfully logged out.",
    }


# ---------------------------------------------------------------------------
# POST /forgot-password
# ---------------------------------------------------------------------------

@router.post(
    "/forgot-password",
    response_model=SuccessResponse,
    status_code=status.HTTP_200_OK,
    summary="Request password reset email",
)
async def forgot_password(
    body: PasswordResetRequest,
    db: AsyncSession = Depends(get_db_session),
) -> dict:
    """Send a password-reset email if the account exists.

    Always returns 200 to avoid leaking user existence information.
    """
    logger.info("Password reset requested", email=body.email)

    result = await db.execute(
        select(User).where(User.email == body.email, User.is_active.is_(True))
    )
    user = result.scalars().first()

    if user:
        # Generate a time-limited reset token and store in Redis
        import redis.asyncio as aioredis
        settings = get_settings()

        reset_token = str(uuid.uuid4())
        try:
            redis_client = aioredis.from_url(settings.REDIS_URL, decode_responses=True)
            await redis_client.setex(
                f"password_reset:{reset_token}",
                3600,  # 1 hour TTL
                str(user.id),
            )
            await redis_client.aclose()
        except Exception as exc:
            logger.error("Failed to store reset token in Redis", error=str(exc))
            raise HTTPException(
                status_code=status.HTTP_500_INTERNAL_SERVER_ERROR,
                detail="Failed to process password reset request.",
            )

        # Dispatch email asynchronously via service layer
        try:
            from app.services.email_service import send_password_reset_email
            await send_password_reset_email(
                to_email=user.email,
                user_name=user.full_name,
                reset_token=reset_token,
            )
        except ImportError:
            logger.warning("Email service not available; reset token generated but email not sent")
        except Exception as exc:
            logger.error("Failed to send password reset email", error=str(exc))

    # Always return success to prevent email enumeration
    return {
        "status": "success",
        "data": None,
        "message": "If the email is registered, a password reset link has been sent.",
    }


# ---------------------------------------------------------------------------
# POST /reset-password
# ---------------------------------------------------------------------------

@router.post(
    "/reset-password",
    response_model=SuccessResponse,
    status_code=status.HTTP_200_OK,
    summary="Reset password with token",
    responses={400: {"model": ErrorResponse}},
)
async def reset_password(
    body: PasswordResetConfirm,
    db: AsyncSession = Depends(get_db_session),
) -> dict:
    """Reset a user's password using the token received via email."""
    import redis.asyncio as aioredis
    settings = get_settings()

    # Look up the reset token in Redis
    try:
        redis_client = aioredis.from_url(settings.REDIS_URL, decode_responses=True)
        user_id_str = await redis_client.get(f"password_reset:{body.token}")
    except Exception as exc:
        logger.error("Redis connection failed during password reset", error=str(exc))
        raise HTTPException(
            status_code=status.HTTP_500_INTERNAL_SERVER_ERROR,
            detail="Failed to validate reset token.",
        )

    if not user_id_str:
        raise ValidationError(message="Invalid or expired password reset token.")

    # Fetch the user
    result = await db.execute(
        select(User).where(User.id == uuid.UUID(user_id_str))
    )
    user = result.scalars().first()
    if not user:
        raise NotFoundError(resource="User")

    # Update password
    user.hashed_password = _hash_password(body.new_password)
    db.add(user)
    await db.flush()

    # Delete the used reset token
    try:
        await redis_client.delete(f"password_reset:{body.token}")
        await redis_client.aclose()
    except Exception:
        pass  # Non-critical cleanup

    logger.info("Password reset successful", user_id=str(user.id))

    return {
        "status": "success",
        "data": None,
        "message": "Password has been reset successfully.",
    }


# ---------------------------------------------------------------------------
# POST /change-password
# ---------------------------------------------------------------------------

@router.post(
    "/change-password",
    response_model=SuccessResponse,
    status_code=status.HTTP_200_OK,
    summary="Change password (authenticated)",
    responses={400: {"model": ErrorResponse}},
)
async def change_password(
    body: ChangePasswordRequest,
    user: User = Depends(_get_current_user),
    db: AsyncSession = Depends(get_db_session),
) -> dict:
    """Change the current user's password. Requires the current password for verification."""
    if not _verify_password(body.current_password, user.hashed_password):
        raise ValidationError(message="Current password is incorrect.")

    if body.current_password == body.new_password:
        raise ValidationError(message="New password must differ from the current password.")

    user.hashed_password = _hash_password(body.new_password)
    db.add(user)
    await db.flush()

    logger.info("Password changed", user_id=str(user.id))

    return {
        "status": "success",
        "data": None,
        "message": "Password changed successfully.",
    }


# ---------------------------------------------------------------------------
# GET /me
# ---------------------------------------------------------------------------

@router.get(
    "/me",
    response_model=SuccessResponse,
    status_code=status.HTTP_200_OK,
    summary="Get current user profile",
)
async def get_me(
    user: User = Depends(_get_current_user),
) -> dict:
    """Return the authenticated user's profile."""
    return {
        "status": "success",
        "data": UserResponse.model_validate(user).model_dump(mode="json"),
    }


# ---------------------------------------------------------------------------
# PUT /me
# ---------------------------------------------------------------------------

@router.put(
    "/me",
    response_model=SuccessResponse,
    status_code=status.HTTP_200_OK,
    summary="Update current user profile",
)
async def update_me(
    body: UserUpdate,
    user: User = Depends(_get_current_user),
    db: AsyncSession = Depends(get_db_session),
) -> dict:
    """Update the authenticated user's profile fields.

    Users cannot change their own role or active status via this endpoint.
    """
    update_data = body.model_dump(exclude_unset=True)

    # Users cannot change their own role or is_active via the profile endpoint
    update_data.pop("role", None)
    update_data.pop("is_active", None)

    if not update_data:
        raise ValidationError(message="No valid fields provided for update.")

    for field, value in update_data.items():
        setattr(user, field, value)

    db.add(user)
    await db.flush()

    logger.info("User profile updated", user_id=str(user.id), fields=list(update_data.keys()))

    return {
        "status": "success",
        "data": UserResponse.model_validate(user).model_dump(mode="json"),
    }
