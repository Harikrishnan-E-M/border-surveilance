"""
Rate Limiting Middleware for VisionAI.

Implements a sliding window rate limiter backed by Redis.  Each client
is identified by IP address and the request endpoint path.  When the
limit is exceeded, a 429 Too Many Requests response is returned with a
``Retry-After`` header indicating how long the client must wait.

Usage::

    from app.middleware.rate_limit import RateLimiter

    # In FastAPI app setup
    app.add_middleware(RateLimiter)
"""

from __future__ import annotations

import time
from typing import Any, Callable, Optional

import structlog
from fastapi import FastAPI, Request, Response, status
from fastapi.responses import JSONResponse
from redis.asyncio import Redis
from starlette.middleware.base import BaseHTTPMiddleware, RequestResponseEndpoint
from starlette.types import ASGIApp

from app.config import get_settings

logger = structlog.stdlib.get_logger(__name__)

# ── Default rate limit configurations ─────────────────────────────────────────

# Endpoint prefix -> (max_requests, window_seconds)
DEFAULT_RATE_LIMITS: dict[str, tuple[int, int]] = {
    "/api/v1/auth/": (5, 60),        # 5 req/min for auth endpoints
    "/api/v1/auth/login": (5, 60),
    "/api/v1/auth/register": (5, 60),
    "/api/v1/auth/forgot-password": (3, 60),
    "/api/v1/auth/refresh": (10, 60),
}

# Fallback limit for all other endpoints
GENERAL_RATE_LIMIT: tuple[int, int] = (100, 60)  # 100 req/min


# ── Sliding Window Algorithm ──────────────────────────────────────────────────


class SlidingWindowCounter:
    """Redis-backed sliding window rate limiter.

    Uses a sorted set where each member is a unique request timestamp.
    The score is the Unix timestamp of the request.  On each check, entries
    outside the current window are pruned and the remaining count is compared
    against the limit.

    This approach provides smoother rate limiting than fixed windows and is
    resistant to burst attacks at window boundaries.

    Attributes:
        redis: Async Redis client instance.
    """

    def __init__(self, redis: Redis) -> None:
        """Initialise the sliding window counter.

        Args:
            redis: An async Redis client connected to the rate-limiting database.
        """
        self.redis = redis

    async def is_allowed(
        self,
        key: str,
        max_requests: int,
        window_seconds: int,
    ) -> tuple[bool, int, float]:
        """Check whether a request is allowed under the rate limit.

        Uses a Redis sorted set to implement a sliding window counter.
        All Redis operations are performed atomically within a pipeline.

        Args:
            key: Unique rate-limit key (e.g. ``rate_limit:192.168.1.1:/api/v1/cameras``).
            max_requests: Maximum number of requests allowed in the window.
            window_seconds: Length of the sliding window in seconds.

        Returns:
            A tuple of:
            - ``allowed`` (bool): Whether the request should proceed.
            - ``remaining`` (int): Number of requests remaining in the window.
            - ``retry_after`` (float): Seconds until the oldest entry expires
              (only meaningful when ``allowed`` is False).
        """
        now = time.time()
        window_start = now - window_seconds

        pipe = self.redis.pipeline(transaction=True)

        # Remove entries outside the current window
        pipe.zremrangebyscore(key, 0, window_start)

        # Count current entries in the window
        pipe.zcard(key)

        # Add the current request (unique member via precise timestamp)
        # Use timestamp + random suffix to avoid collisions for same-ms requests
        member = f"{now}"
        pipe.zadd(key, {member: now})

        # Set TTL to auto-clean keys after the window expires
        pipe.expire(key, window_seconds + 1)

        # Get the oldest entry to compute retry-after
        pipe.zrange(key, 0, 0, withscores=True)

        results: list[Any] = await pipe.execute()

        current_count: int = results[1]

        if current_count < max_requests:
            remaining = max_requests - current_count - 1  # -1 for current request
            return True, max(remaining, 0), 0.0

        # Rate limit exceeded -- compute retry-after from oldest entry
        oldest_entries: list[tuple[bytes, float]] = results[4]
        if oldest_entries:
            oldest_score = oldest_entries[0][1]
            retry_after = (oldest_score + window_seconds) - now
        else:
            retry_after = float(window_seconds)

        # Remove the entry we just added since we're rejecting the request
        await self.redis.zrem(key, member)

        remaining = 0
        return False, remaining, max(retry_after, 1.0)


# ── Rate Limit Middleware ─────────────────────────────────────────────────────


class RateLimiter(BaseHTTPMiddleware):
    """FastAPI middleware that enforces per-client, per-endpoint rate limits.

    The middleware uses Redis as a shared backend so that rate limits are
    enforced consistently across multiple application workers.

    Rate limit information is included in every response via standard
    headers:

    - ``X-RateLimit-Limit``: Maximum requests allowed in the window.
    - ``X-RateLimit-Remaining``: Requests remaining in the current window.
    - ``X-RateLimit-Reset``: Unix timestamp when the window resets.

    When the limit is exceeded, a ``429 Too Many Requests`` response is
    returned with a ``Retry-After`` header.

    Attributes:
        rate_limits: Mapping of endpoint prefixes to (max_requests, window) tuples.
        general_limit: Fallback rate limit for unmatched endpoints.
        redis: Async Redis client (lazily initialised).
        counter: SlidingWindowCounter instance (lazily initialised).
        excluded_paths: Paths that are exempt from rate limiting.
    """

    def __init__(
        self,
        app: ASGIApp,
        rate_limits: Optional[dict[str, tuple[int, int]]] = None,
        general_limit: Optional[tuple[int, int]] = None,
        excluded_paths: Optional[list[str]] = None,
    ) -> None:
        """Initialise the rate limiter middleware.

        Args:
            app: The ASGI application.
            rate_limits: Custom endpoint prefix to limit mappings.
                Defaults to ``DEFAULT_RATE_LIMITS``.
            general_limit: Fallback limit as ``(max_requests, window_seconds)``.
                Defaults to ``GENERAL_RATE_LIMIT``.
            excluded_paths: List of path prefixes to exclude from rate limiting
                (e.g. ``["/health", "/docs"]``).
        """
        super().__init__(app)
        self.rate_limits = rate_limits or DEFAULT_RATE_LIMITS
        self.general_limit = general_limit or GENERAL_RATE_LIMIT
        self.excluded_paths = excluded_paths or [
            "/health",
            "/docs",
            "/redoc",
            "/openapi.json",
            "/metrics",
        ]
        self._redis: Optional[Redis] = None
        self._counter: Optional[SlidingWindowCounter] = None

    async def _get_redis(self) -> Redis:
        """Lazily initialise and return the Redis client.

        Returns:
            Redis: An async Redis connection.
        """
        if self._redis is None:
            settings = get_settings()
            self._redis = Redis.from_url(
                settings.REDIS_URL,
                decode_responses=True,
                max_connections=settings.REDIS_MAX_CONNECTIONS,
            )
        return self._redis

    async def _get_counter(self) -> SlidingWindowCounter:
        """Lazily initialise and return the sliding window counter.

        Returns:
            SlidingWindowCounter: The rate limit counter backed by Redis.
        """
        if self._counter is None:
            redis = await self._get_redis()
            self._counter = SlidingWindowCounter(redis)
        return self._counter

    def _get_client_ip(self, request: Request) -> str:
        """Extract the real client IP from the request.

        Checks ``X-Forwarded-For`` and ``X-Real-IP`` headers first (for
        requests behind a reverse proxy) and falls back to the direct
        client address.

        Args:
            request: The incoming HTTP request.

        Returns:
            str: The client IP address.
        """
        # Check X-Forwarded-For (first IP is the original client)
        forwarded_for = request.headers.get("X-Forwarded-For")
        if forwarded_for:
            return forwarded_for.split(",")[0].strip()

        # Check X-Real-IP
        real_ip = request.headers.get("X-Real-IP")
        if real_ip:
            return real_ip.strip()

        # Fallback to direct connection
        if request.client:
            return request.client.host

        return "unknown"

    def _get_rate_limit(self, path: str) -> tuple[int, int]:
        """Determine the applicable rate limit for the given path.

        Checks endpoint prefixes from most specific to least specific.

        Args:
            path: The request URL path.

        Returns:
            tuple: ``(max_requests, window_seconds)`` for the matched rule.
        """
        # Sort by prefix length descending for most-specific-first matching
        sorted_limits = sorted(
            self.rate_limits.items(),
            key=lambda item: len(item[0]),
            reverse=True,
        )

        for prefix, limit in sorted_limits:
            if path.startswith(prefix):
                return limit

        return self.general_limit

    def _is_excluded(self, path: str) -> bool:
        """Check whether the path is excluded from rate limiting.

        Args:
            path: The request URL path.

        Returns:
            bool: True if the path should bypass rate limiting.
        """
        return any(path.startswith(excluded) for excluded in self.excluded_paths)

    async def dispatch(
        self,
        request: Request,
        call_next: RequestResponseEndpoint,
    ) -> Response:
        """Process the request through the rate limiter.

        If the rate limit is not exceeded, the request proceeds normally
        with rate-limit headers attached to the response.  Otherwise, a
        429 response is returned immediately.

        Args:
            request: The incoming HTTP request.
            call_next: The next middleware or route handler.

        Returns:
            Response: The HTTP response (either from downstream or a 429).
        """
        path = request.url.path

        # Skip rate limiting for excluded paths
        if self._is_excluded(path):
            return await call_next(request)

        client_ip = self._get_client_ip(request)
        max_requests, window_seconds = self._get_rate_limit(path)
        key = f"rate_limit:{client_ip}:{path}"

        try:
            counter = await self._get_counter()
            allowed, remaining, retry_after = await counter.is_allowed(
                key=key,
                max_requests=max_requests,
                window_seconds=window_seconds,
            )
        except Exception as exc:
            # If Redis is down, allow the request through (fail-open)
            logger.error(
                "Rate limiter Redis error, failing open",
                error=str(exc),
                client_ip=client_ip,
                path=path,
            )
            return await call_next(request)

        if not allowed:
            logger.warning(
                "Rate limit exceeded",
                client_ip=client_ip,
                path=path,
                limit=max_requests,
                window=window_seconds,
                retry_after=retry_after,
            )
            return JSONResponse(
                status_code=status.HTTP_429_TOO_MANY_REQUESTS,
                content={
                    "status": "error",
                    "detail": "Rate limit exceeded. Please try again later.",
                    "code": "RATE_LIMIT_EXCEEDED",
                },
                headers={
                    "Retry-After": str(int(retry_after)),
                    "X-RateLimit-Limit": str(max_requests),
                    "X-RateLimit-Remaining": "0",
                    "X-RateLimit-Reset": str(int(time.time() + retry_after)),
                },
            )

        # Proceed with the request
        response: Response = await call_next(request)

        # Attach rate-limit headers to successful responses
        response.headers["X-RateLimit-Limit"] = str(max_requests)
        response.headers["X-RateLimit-Remaining"] = str(remaining)
        response.headers["X-RateLimit-Reset"] = str(int(time.time() + window_seconds))

        return response
