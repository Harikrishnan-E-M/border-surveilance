"""
VisionAI FastAPI Dependencies.

Reusable dependency functions injected via ``Depends()`` across all
API endpoints.  Includes authentication, authorisation, database
session management, Redis access, and rate limiting.
"""

from __future__ import annotations

import time
from collections.abc import AsyncGenerator, Callable
from typing import Any
from uuid import UUID

import redis.asyncio as aioredis
import structlog
from fastapi import Depends, Request
from fastapi.security import HTTPAuthorizationCredentials, HTTPBearer
from jose import JWTError, jwt
from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession

from app.config import Settings, get_settings
from app.database import AsyncSessionLocal, get_db_session
from app.exceptions import (
    AuthenticationError,
    AuthorizationError,
    RateLimitError,
)

logger = structlog.stdlib.get_logger(__name__)

# ── Security Scheme ───────────────────────────────────────────────────────

bearer_scheme = HTTPBearer(auto_error=False)


# ── Database Dependency ───────────────────────────────────────────────────


async def get_db(
    session: AsyncSession = Depends(get_db_session),
) -> AsyncGenerator[AsyncSession, None]:
    """Provide an async database session.

    This is a thin wrapper around ``get_db_session`` that can be used as
    a FastAPI dependency directly::

        @router.get("/items")
        async def list_items(db: AsyncSession = Depends(get_db)):
            ...

    Yields:
        AsyncSession: An active async database session.
    """
    yield session


# ── Redis Dependency ──────────────────────────────────────────────────────

_redis_pool: aioredis.Redis | None = None


async def init_redis(settings: Settings | None = None) -> aioredis.Redis:
    """Initialise the global Redis connection pool.

    Should be called once during application startup.

    Args:
        settings: Application settings. Uses ``get_settings()`` if None.

    Returns:
        aioredis.Redis: The connected Redis client.
    """
    global _redis_pool
    if settings is None:
        settings = get_settings()
    _redis_pool = aioredis.from_url(
        settings.REDIS_URL,
        max_connections=settings.REDIS_MAX_CONNECTIONS,
        decode_responses=True,
        socket_timeout=5,
        socket_connect_timeout=5,
        retry_on_timeout=True,
    )
    # Verify connectivity
    await _redis_pool.ping()
    logger.info("Redis connection pool initialised", url=settings.REDIS_URL)
    return _redis_pool


async def close_redis() -> None:
    """Close the global Redis connection pool.

    Should be called during application shutdown.
    """
    global _redis_pool
    if _redis_pool is not None:
        await _redis_pool.close()
        _redis_pool = None
        logger.info("Redis connection pool closed")


async def get_redis() -> aioredis.Redis:
    """FastAPI dependency that returns the Redis connection pool.

    Raises:
        RuntimeError: If Redis has not been initialised yet.

    Returns:
        aioredis.Redis: The active Redis client.
    """
    if _redis_pool is None:
        raise RuntimeError(
            "Redis connection pool is not initialised. "
            "Ensure init_redis() is called during application startup."
        )
    return _redis_pool


# ── JWT Token Helpers ─────────────────────────────────────────────────────


def _decode_token(token: str, settings: Settings) -> dict[str, Any]:
    """Decode and validate a JWT token.

    Args:
        token: The raw JWT string.
        settings: Application settings containing JWT configuration.

    Returns:
        dict: The decoded token payload.

    Raises:
        AuthenticationError: If the token is expired, malformed, or invalid.
    """
    try:
        payload = jwt.decode(
            token,
            settings.JWT_SECRET_KEY,
            algorithms=[settings.JWT_ALGORITHM],
        )
    except JWTError as exc:
        raise AuthenticationError(
            message=f"Invalid authentication token: {exc}",
            code="INVALID_TOKEN",
        ) from exc

    if "sub" not in payload:
        raise AuthenticationError(
            message="Token payload missing 'sub' claim",
            code="INVALID_TOKEN",
        )

    token_type = payload.get("type", "access")
    if token_type != "access":
        raise AuthenticationError(
            message="Expected an access token",
            code="INVALID_TOKEN_TYPE",
        )

    return payload


# ── Authentication Dependencies ───────────────────────────────────────────


async def get_current_user(
    credentials: HTTPAuthorizationCredentials | None = Depends(bearer_scheme),
    db: AsyncSession = Depends(get_db_session),
    settings: Settings = Depends(get_settings),
) -> Any:
    """Extract and validate the JWT bearer token, then load the user.

    This dependency:
    1. Extracts the ``Authorization: Bearer <token>`` header.
    2. Decodes and validates the JWT.
    3. Loads the corresponding user from the database.
    4. Returns the user ORM object.

    Raises:
        AuthenticationError: If the token is missing, invalid, or the
            user no longer exists.

    Returns:
        User: The authenticated user model instance.
    """
    if credentials is None:
        raise AuthenticationError(
            message="Authentication credentials were not provided",
            code="MISSING_TOKEN",
        )

    payload = _decode_token(credentials.credentials, settings)
    user_id = payload["sub"]

    # Import here to avoid circular dependency (models -> database -> dependencies)
    from app.models.user import User

    try:
        user_uuid = UUID(user_id)
    except (ValueError, AttributeError) as exc:
        raise AuthenticationError(
            message="Invalid user identifier in token",
            code="INVALID_TOKEN",
        ) from exc

    result = await db.execute(select(User).where(User.id == user_uuid))
    user = result.scalar_one_or_none()

    if user is None:
        raise AuthenticationError(
            message="User associated with this token no longer exists",
            code="USER_NOT_FOUND",
        )

    return user


async def get_current_active_user(
    current_user: Any = Depends(get_current_user),
) -> Any:
    """Ensure the authenticated user's account is active.

    Args:
        current_user: The user loaded by ``get_current_user``.

    Raises:
        AuthorizationError: If the user account is deactivated.

    Returns:
        User: The active user model instance.
    """
    if not getattr(current_user, "is_active", True):
        raise AuthorizationError(
            message="Your account has been deactivated. Please contact an administrator.",
            code="ACCOUNT_DISABLED",
        )
    return current_user


# ── Role-Based Access Control ─────────────────────────────────────────────


def require_role(allowed_roles: list[str]) -> Callable:
    """Return a FastAPI dependency that enforces role-based access.

    Usage::

        @router.delete("/users/{user_id}", dependencies=[Depends(require_role(["admin"]))])
        async def delete_user(...):
            ...

    Args:
        allowed_roles: List of role names that are permitted (e.g.
            ``["admin", "operator"]``).

    Returns:
        Callable: A FastAPI-compatible dependency function.
    """

    async def _role_checker(
        current_user: Any = Depends(get_current_active_user),
    ) -> Any:
        """Check if the current user has one of the required roles.

        Args:
            current_user: The active authenticated user.

        Raises:
            AuthorizationError: If the user's role is not in the
                allowed list.

        Returns:
            User: The authorised user.
        """
        user_role = getattr(current_user, "role", None)
        if user_role is None or user_role not in allowed_roles:
            logger.warning(
                "Access denied: insufficient role",
                user_id=str(current_user.id),
                user_role=user_role,
                required_roles=allowed_roles,
            )
            raise AuthorizationError(
                message=(
                    f"This action requires one of the following roles: "
                    f"{', '.join(allowed_roles)}. Your role: {user_role}."
                ),
                code="INSUFFICIENT_ROLE",
            )
        return current_user

    return _role_checker


# ── Rate Limiter ──────────────────────────────────────────────────────────


class RateLimiter:
    """Sliding-window rate limiter backed by Redis.

    Instantiate as a FastAPI dependency::

        limiter = RateLimiter(max_requests=30, window_seconds=60)

        @router.get("/search", dependencies=[Depends(limiter)])
        async def search(...):
            ...

    The limiter keys requests by the client's IP address.  When the limit
    is exceeded a ``RateLimitError`` (HTTP 429) is raised.

    Args:
        max_requests: Maximum number of requests allowed within the
            sliding window.
        window_seconds: Window duration in seconds.
    """

    def __init__(self, max_requests: int = 60, window_seconds: int = 60) -> None:
        self.max_requests = max_requests
        self.window_seconds = window_seconds

    async def __call__(
        self,
        request: Request,
        redis_client: aioredis.Redis = Depends(get_redis),
    ) -> None:
        """Execute the rate-limit check.

        Args:
            request: The incoming HTTP request.
            redis_client: The Redis connection (injected).

        Raises:
            RateLimitError: If the client has exceeded the allowed request rate.
        """
        # Identify client by IP (supports X-Forwarded-For behind reverse proxy)
        forwarded_for = request.headers.get("x-forwarded-for")
        client_ip = forwarded_for.split(",")[0].strip() if forwarded_for else (
            request.client.host if request.client else "unknown"
        )

        key = f"ratelimit:{client_ip}:{request.url.path}"
        now = time.time()
        window_start = now - self.window_seconds

        pipe = redis_client.pipeline()
        # Remove entries outside the sliding window
        pipe.zremrangebyscore(key, 0, window_start)
        # Count remaining entries
        pipe.zcard(key)
        # Add the current request
        pipe.zadd(key, {str(now): now})
        # Set expiry on the key
        pipe.expire(key, self.window_seconds + 1)
        results = await pipe.execute()

        request_count = results[1]

        if request_count >= self.max_requests:
            # Calculate time until the oldest request in the window expires
            oldest = await redis_client.zrange(key, 0, 0, withscores=True)
            retry_after = self.window_seconds
            if oldest:
                oldest_score = oldest[0][1]
                retry_after = max(1, int(self.window_seconds - (now - oldest_score)))
            logger.warning(
                "Rate limit exceeded",
                client_ip=client_ip,
                path=request.url.path,
                count=request_count,
                limit=self.max_requests,
            )
            raise RateLimitError(retry_after=retry_after)


# ── Pagination Dependency ─────────────────────────────────────────────────


class PaginationParams:
    """Common pagination parameters extracted from query strings.

    Usage::

        @router.get("/items")
        async def list_items(pagination: PaginationParams = Depends()):
            offset = pagination.offset
            limit = pagination.limit
    """

    def __init__(
        self,
        page: int = 1,
        page_size: int = 20,
    ) -> None:
        """Initialise pagination parameters.

        Args:
            page: Page number (1-indexed). Clamped to a minimum of 1.
            page_size: Items per page. Clamped between 1 and 100.
        """
        self.page = max(1, page)
        self.page_size = max(1, min(100, page_size))

    @property
    def offset(self) -> int:
        """Calculate the SQL OFFSET value."""
        return (self.page - 1) * self.page_size

    @property
    def limit(self) -> int:
        """Return the SQL LIMIT value (alias for page_size)."""
        return self.page_size
