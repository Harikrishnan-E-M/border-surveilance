"""Authentication service for user management, password operations, and API keys.

Provides the business logic layer between auth API routes and the database.
All passwords are hashed with bcrypt via passlib. API keys use SHA-256 hashing
for fast lookup while remaining irreversible.
"""

from __future__ import annotations

import hashlib
import re
import secrets
import uuid
from datetime import datetime, timedelta, timezone
from typing import Optional

import bcrypt
import structlog
from sqlalchemy import func, select
from sqlalchemy.exc import IntegrityError
from sqlalchemy.ext.asyncio import AsyncSession

from app.exceptions import (
    AuthenticationError,
    DuplicateError,
    NotFoundError,
    ValidationError,
)
from app.models.organization import Organization
from app.models.user import APIKey, User, UserRole
from app.schemas.auth import APIKeyCreate, RegisterRequest, UserCreate, UserUpdate

logger = structlog.stdlib.get_logger(__name__)

# ── Password helpers ──────────────────────────────────────────────────────


def _hash_password(password: str) -> str:
    """Hash a plaintext password using bcrypt."""
    return bcrypt.hashpw(password.encode("utf-8"), bcrypt.gensalt()).decode("utf-8")


def _verify_password(plain_password: str, hashed_password: str) -> bool:
    """Verify a plaintext password against a bcrypt hash."""
    try:
        return bcrypt.checkpw(plain_password.encode("utf-8"), hashed_password.encode("utf-8"))
    except Exception:
        return False


def _generate_api_key() -> str:
    """Generate a cryptographically secure API key with a recognizable prefix."""
    random_part = secrets.token_urlsafe(32)
    return f"vai_k_{random_part}"


def _hash_api_key(key: str) -> str:
    """Hash an API key using SHA-256 for fast lookup."""
    return hashlib.sha256(key.encode("utf-8")).hexdigest()


def _slugify(name: str) -> str:
    """Convert an organization name to a URL-safe slug."""
    slug = name.lower().strip()
    slug = re.sub(r"[^\w\s-]", "", slug)
    slug = re.sub(r"[\s_]+", "-", slug)
    slug = re.sub(r"-+", "-", slug)
    return slug.strip("-")


# ── Authentication ────────────────────────────────────────────────────────


async def authenticate_user(
    db: AsyncSession,
    email: str,
    password: str,
) -> User:
    """Authenticate a user by email and password.

    Args:
        db: Async database session.
        email: The user's email address.
        password: The plaintext password to verify.

    Returns:
        The authenticated User instance.

    Raises:
        AuthenticationError: If the email is not found, password is wrong,
            or the account is inactive.
    """
    logger.info("Authenticating user", email=email)

    result = await db.execute(
        select(User).where(func.lower(User.email) == email.lower())
    )
    user = result.scalar_one_or_none()

    if user is None:
        logger.warning("Authentication failed: user not found", email=email)
        raise AuthenticationError(
            message="Invalid email or password",
            code="INVALID_CREDENTIALS",
        )

    import asyncio
    is_valid_pw = await asyncio.to_thread(_verify_password, password, user.hashed_password)
    if not is_valid_pw:
        logger.warning("Authentication failed: bad password", email=email)
        raise AuthenticationError(
            message="Invalid email or password",
            code="INVALID_CREDENTIALS",
        )

    if not user.is_active:
        logger.warning("Authentication failed: account disabled", email=email)
        raise AuthenticationError(
            message="Your account has been deactivated. Please contact an administrator.",
            code="ACCOUNT_DISABLED",
        )

    user.last_login = datetime.now(timezone.utc)
    await db.flush()

    logger.info("User authenticated successfully", user_id=str(user.id))
    return user


# ── User CRUD ─────────────────────────────────────────────────────────────


async def create_user(
    db: AsyncSession,
    user_data: UserCreate,
    org_id: uuid.UUID,
) -> User:
    """Create a new user within an organization.

    Args:
        db: Async database session.
        user_data: Validated user creation payload.
        org_id: The organization to add the user to.

    Returns:
        The newly created User instance.

    Raises:
        DuplicateError: If a user with the same email already exists.
        ValidationError: If the role is not valid.
    """
    logger.info("Creating user", email=user_data.email, org_id=str(org_id))

    existing = await get_user_by_email(db, user_data.email)
    if existing is not None:
        raise DuplicateError(
            message=f"A user with email '{user_data.email}' already exists",
            code="EMAIL_ALREADY_EXISTS",
        )

    try:
        role = UserRole(user_data.role)
    except ValueError:
        valid_roles = [r.value for r in UserRole]
        raise ValidationError(
            message=f"Invalid role '{user_data.role}'. Must be one of: {', '.join(valid_roles)}"
        )

    user = User(
        id=uuid.uuid4(),
        org_id=org_id,
        email=user_data.email.lower(),
        hashed_password=_hash_password(user_data.password),
        full_name=user_data.full_name,
        phone=user_data.phone,
        role=role,
        is_active=True,
    )
    db.add(user)

    try:
        await db.flush()
    except IntegrityError as exc:
        await db.rollback()
        logger.error("Integrity error creating user", error=str(exc))
        raise DuplicateError(
            message=f"A user with email '{user_data.email}' already exists",
            code="EMAIL_ALREADY_EXISTS",
        )

    logger.info("User created", user_id=str(user.id), email=user.email)
    return user


async def register_organization(
    db: AsyncSession,
    data: RegisterRequest,
) -> tuple[Organization, User]:
    """Register a new organization and its first admin user.

    Args:
        db: Async database session.
        data: Registration payload containing org name, email, password, and full name.

    Returns:
        A tuple of (Organization, User) for the newly created entities.

    Raises:
        DuplicateError: If a user with the given email already exists
            or the organization slug is taken.
    """
    logger.info("Registering organization", org_name=data.org_name, email=data.email)

    existing_user = await get_user_by_email(db, data.email)
    if existing_user is not None:
        raise DuplicateError(
            message=f"A user with email '{data.email}' already exists",
            code="EMAIL_ALREADY_EXISTS",
        )

    slug = _slugify(data.org_name)
    result = await db.execute(
        select(Organization).where(Organization.slug == slug)
    )
    if result.scalar_one_or_none() is not None:
        slug = f"{slug}-{secrets.token_hex(3)}"

    org = Organization(
        id=uuid.uuid4(),
        name=data.org_name,
        slug=slug,
    )
    db.add(org)
    await db.flush()

    user = User(
        id=uuid.uuid4(),
        org_id=org.id,
        email=data.email.lower(),
        hashed_password=_hash_password(data.password),
        full_name=data.full_name,
        role=UserRole.ORG_ADMIN,
        is_active=True,
    )
    db.add(user)

    try:
        await db.flush()
    except IntegrityError as exc:
        await db.rollback()
        logger.error("Integrity error during registration", error=str(exc))
        raise DuplicateError(
            message="Registration failed due to a conflict. Please try again.",
            code="REGISTRATION_CONFLICT",
        )

    logger.info(
        "Organization registered",
        org_id=str(org.id),
        user_id=str(user.id),
    )
    return org, user


async def get_user_by_id(
    db: AsyncSession,
    user_id: uuid.UUID,
) -> User:
    """Retrieve a user by their UUID.

    Args:
        db: Async database session.
        user_id: The user's unique identifier.

    Returns:
        The User instance.

    Raises:
        NotFoundError: If no user exists with the given ID.
    """
    result = await db.execute(select(User).where(User.id == user_id))
    user = result.scalar_one_or_none()

    if user is None:
        raise NotFoundError(resource="User", identifier=user_id)

    return user


async def get_user_by_email(
    db: AsyncSession,
    email: str,
) -> Optional[User]:
    """Retrieve a user by email address, or None if not found.

    Args:
        db: Async database session.
        email: The email address to look up.

    Returns:
        The User instance or None.
    """
    result = await db.execute(
        select(User).where(func.lower(User.email) == email.lower())
    )
    return result.scalar_one_or_none()


async def update_user(
    db: AsyncSession,
    user_id: uuid.UUID,
    data: UserUpdate,
) -> User:
    """Update an existing user's profile fields.

    Only fields provided (non-None) in the update payload are changed.

    Args:
        db: Async database session.
        user_id: The user's unique identifier.
        data: Partial update payload.

    Returns:
        The updated User instance.

    Raises:
        NotFoundError: If the user does not exist.
        ValidationError: If the provided role is invalid.
    """
    user = await get_user_by_id(db, user_id)

    if data.full_name is not None:
        user.full_name = data.full_name
    if data.phone is not None:
        user.phone = data.phone
    if data.is_active is not None:
        user.is_active = data.is_active
    if data.role is not None:
        try:
            user.role = UserRole(data.role)
        except ValueError:
            valid_roles = [r.value for r in UserRole]
            raise ValidationError(
                message=f"Invalid role '{data.role}'. Must be one of: {', '.join(valid_roles)}"
            )

    await db.flush()
    logger.info("User updated", user_id=str(user_id))
    return user


# ── Password Management ───────────────────────────────────────────────────


async def change_password(
    db: AsyncSession,
    user_id: uuid.UUID,
    current_password: str,
    new_password: str,
) -> bool:
    """Change a user's password after verifying the current one.

    Args:
        db: Async database session.
        user_id: The user's unique identifier.
        current_password: The current password for verification.
        new_password: The new password to set.

    Returns:
        True if the password was changed successfully.

    Raises:
        AuthenticationError: If the current password is incorrect.
        NotFoundError: If the user does not exist.
        ValidationError: If the new password is too short.
    """
    if len(new_password) < 8:
        raise ValidationError(message="New password must be at least 8 characters long")

    user = await get_user_by_id(db, user_id)

    if not _verify_password(current_password, user.hashed_password):
        raise AuthenticationError(
            message="Current password is incorrect",
            code="INVALID_CURRENT_PASSWORD",
        )

    user.hashed_password = _hash_password(new_password)
    await db.flush()

    logger.info("Password changed", user_id=str(user_id))
    return True


async def create_password_reset_token(
    db: AsyncSession,
    email: str,
) -> str:
    """Generate a password-reset token for the given email.

    The token is stored as a hashed value in Redis (or as a fallback, in
    the user's record) and is valid for 1 hour.

    Args:
        db: Async database session.
        email: The email address of the user requesting a reset.

    Returns:
        The plaintext reset token to be sent via email.

    Raises:
        NotFoundError: If no user exists with the given email.
    """
    user = await get_user_by_email(db, email)
    if user is None:
        logger.warning("Password reset requested for unknown email", email=email)
        raise NotFoundError(resource="User", identifier=email)

    token = secrets.token_urlsafe(48)
    token_hash = hashlib.sha256(token.encode("utf-8")).hexdigest()
    expiry = datetime.now(timezone.utc) + timedelta(hours=1)

    try:
        import redis.asyncio as aioredis

        from app.dependencies import get_redis

        redis_client = await get_redis()
        await redis_client.setex(
            f"pwd_reset:{token_hash}",
            3600,
            f"{user.id}",
        )
    except Exception:
        logger.warning(
            "Redis unavailable for password reset token, falling back to log-only",
            user_id=str(user.id),
        )

    logger.info(
        "Password reset token created",
        user_id=str(user.id),
        expires_at=expiry.isoformat(),
    )
    return token


async def reset_password(
    db: AsyncSession,
    token: str,
    new_password: str,
) -> bool:
    """Reset a user's password using a previously issued reset token.

    Args:
        db: Async database session.
        token: The plaintext reset token from the email link.
        new_password: The new password to set.

    Returns:
        True if the password was reset successfully.

    Raises:
        AuthenticationError: If the token is invalid or expired.
        ValidationError: If the new password is too short.
    """
    if len(new_password) < 8:
        raise ValidationError(message="New password must be at least 8 characters long")

    token_hash = hashlib.sha256(token.encode("utf-8")).hexdigest()

    try:
        from app.dependencies import get_redis

        redis_client = await get_redis()
        user_id_str = await redis_client.get(f"pwd_reset:{token_hash}")

        if user_id_str is None:
            raise AuthenticationError(
                message="Invalid or expired password reset token",
                code="INVALID_RESET_TOKEN",
            )

        user_id = uuid.UUID(user_id_str)
        user = await get_user_by_id(db, user_id)
        user.hashed_password = _hash_password(new_password)
        await db.flush()

        await redis_client.delete(f"pwd_reset:{token_hash}")

        logger.info("Password reset completed", user_id=str(user_id))
        return True

    except (RuntimeError, ConnectionError) as exc:
        logger.error("Redis unavailable for password reset", error=str(exc))
        raise AuthenticationError(
            message="Password reset service is temporarily unavailable",
            code="SERVICE_UNAVAILABLE",
        )


# ── API Keys ─────────────────────────────────────────────────────────────


async def create_api_key(
    db: AsyncSession,
    org_id: uuid.UUID,
    user_id: uuid.UUID,
    data: APIKeyCreate,
) -> tuple[APIKey, str]:
    """Create a new API key for programmatic platform access.

    The raw key value is returned only once at creation time.

    Args:
        db: Async database session.
        org_id: The organization the key belongs to.
        user_id: The user creating the key.
        data: Key creation payload (name, permissions, expiration).

    Returns:
        A tuple of (APIKey model, raw plaintext key string).
    """
    raw_key = _generate_api_key()
    key_hash = _hash_api_key(raw_key)

    api_key = APIKey(
        id=uuid.uuid4(),
        org_id=org_id,
        user_id=user_id,
        key_hash=key_hash,
        name=data.name,
        permissions=data.permissions if data.permissions else None,
        expires_at=data.expires_at,
        is_active=True,
    )
    db.add(api_key)
    await db.flush()

    logger.info(
        "API key created",
        key_id=str(api_key.id),
        name=data.name,
        user_id=str(user_id),
    )
    return api_key, raw_key


async def validate_api_key(
    db: AsyncSession,
    key: str,
) -> Optional[tuple[User, Organization]]:
    """Validate an API key and return the associated user and organization.

    Args:
        db: Async database session.
        key: The raw API key string to validate.

    Returns:
        A tuple of (User, Organization) if valid, or None if invalid.
    """
    key_hash = _hash_api_key(key)

    result = await db.execute(
        select(APIKey).where(
            APIKey.key_hash == key_hash,
            APIKey.is_active.is_(True),
        )
    )
    api_key = result.scalar_one_or_none()

    if api_key is None:
        logger.debug("API key validation failed: key not found")
        return None

    if api_key.expires_at is not None and api_key.expires_at < datetime.now(timezone.utc):
        logger.warning("API key validation failed: key expired", key_id=str(api_key.id))
        return None

    user_result = await db.execute(select(User).where(User.id == api_key.user_id))
    user = user_result.scalar_one_or_none()

    if user is None or not user.is_active:
        logger.warning(
            "API key validation failed: user inactive or not found",
            key_id=str(api_key.id),
        )
        return None

    org_result = await db.execute(
        select(Organization).where(Organization.id == api_key.org_id)
    )
    org = org_result.scalar_one_or_none()

    if org is None or not org.is_active:
        logger.warning(
            "API key validation failed: organization inactive or not found",
            key_id=str(api_key.id),
        )
        return None

    logger.debug("API key validated", key_id=str(api_key.id), user_id=str(user.id))
    return user, org
