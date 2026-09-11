"""
Request Logging Middleware for VisionAI.

Assigns a unique request ID to every incoming request, logs request
and response metadata using structlog, and configures structured
logging output format based on the application environment.

Usage::

    from app.middleware.logging import RequestLoggingMiddleware, setup_logging

    # Initialise logging first
    setup_logging()

    # Then add the middleware
    app.add_middleware(RequestLoggingMiddleware)
"""

from __future__ import annotations

import logging
import sys
import time
import uuid
from typing import Optional

import structlog
from fastapi import Request, Response
from starlette.middleware.base import BaseHTTPMiddleware, RequestResponseEndpoint
from starlette.types import ASGIApp

from app.config import get_settings


# ── Structlog Configuration ───────────────────────────────────────────────────


def setup_logging() -> None:
    """Initialise structlog configuration for the application.

    In production, output is JSON-formatted for log aggregation tools
    (e.g. Elasticsearch, Loki, CloudWatch).  In development, output uses
    coloured console rendering for readability.

    This function should be called once at application startup, before
    any logging is performed.  It configures both structlog and the
    standard library ``logging`` module to work together.
    """
    settings = get_settings()

    # Shared processors applied to every log entry
    shared_processors: list[structlog.types.Processor] = [
        structlog.contextvars.merge_contextvars,
        structlog.stdlib.add_logger_name,
        structlog.stdlib.add_log_level,
        structlog.stdlib.ExtraAdder(),
        structlog.processors.TimeStamper(fmt="iso"),
        structlog.processors.StackInfoRenderer(),
        structlog.processors.UnicodeDecoder(),
    ]

    if settings.is_production or settings.APP_ENV == "staging":
        # Production: JSON output for structured log aggregation
        renderer: structlog.types.Processor = structlog.processors.JSONRenderer()
        log_format = "%(message)s"
    else:
        # Development: coloured, human-readable console output
        renderer = structlog.dev.ConsoleRenderer(colors=True)
        log_format = "%(message)s"

    structlog.configure(
        processors=[
            *shared_processors,
            structlog.stdlib.ProcessorFormatter.wrap_for_formatter,
        ],
        logger_factory=structlog.stdlib.LoggerFactory(),
        wrapper_class=structlog.stdlib.BoundLogger,
        cache_logger_on_first_use=True,
    )

    # Configure the standard library root logger
    formatter = structlog.stdlib.ProcessorFormatter(
        processor=renderer,
        foreign_pre_chain=shared_processors,
    )

    handler = logging.StreamHandler(sys.stdout)
    handler.setFormatter(formatter)

    root_logger = logging.getLogger()
    root_logger.handlers.clear()
    root_logger.addHandler(handler)
    root_logger.setLevel(getattr(logging, settings.LOG_LEVEL.upper(), logging.INFO))

    # Reduce noise from third-party libraries
    for noisy_logger in (
        "uvicorn",
        "uvicorn.access",
        "uvicorn.error",
        "sqlalchemy.engine",
        "httpcore",
        "httpx",
        "watchfiles",
    ):
        logging.getLogger(noisy_logger).setLevel(logging.WARNING)

    structlog.stdlib.get_logger(__name__).info(
        "Logging initialised",
        environment=settings.APP_ENV,
        log_level=settings.LOG_LEVEL,
        format="json" if settings.is_production else "console",
    )


# ── Request Logging Middleware ────────────────────────────────────────────────


class RequestLoggingMiddleware(BaseHTTPMiddleware):
    """Middleware that logs details of every HTTP request and response.

    For each request, the middleware:

    1. Generates a unique ``request_id`` (UUID4) and stores it in
       structlog's context variables and ``request.state``.
    2. Measures the wall-clock duration of the request.
    3. Logs a structured entry with: method, path, status_code,
       duration_ms, client_ip, and user_id (if an authenticated
       user is available on ``request.state``).
    4. Sets the ``X-Request-ID`` response header for traceability.

    Attributes:
        excluded_paths: Paths that should not generate log entries
            (e.g. health checks, metrics).
    """

    def __init__(
        self,
        app: ASGIApp,
        excluded_paths: Optional[list[str]] = None,
    ) -> None:
        """Initialise the request logging middleware.

        Args:
            app: The ASGI application.
            excluded_paths: List of path prefixes to exclude from logging.
                Defaults to health and metrics endpoints.
        """
        super().__init__(app)
        self.excluded_paths = excluded_paths or [
            "/health",
            "/metrics",
        ]
        self._logger = structlog.stdlib.get_logger("visionai.access")

    def _get_client_ip(self, request: Request) -> str:
        """Extract the real client IP address from the request.

        Checks proxy headers before falling back to the direct connection
        address.

        Args:
            request: The incoming HTTP request.

        Returns:
            str: The client IP address.
        """
        forwarded_for = request.headers.get("X-Forwarded-For")
        if forwarded_for:
            return forwarded_for.split(",")[0].strip()

        real_ip = request.headers.get("X-Real-IP")
        if real_ip:
            return real_ip.strip()

        if request.client:
            return request.client.host

        return "unknown"

    def _is_excluded(self, path: str) -> bool:
        """Check whether the given path should be excluded from logging.

        Args:
            path: The request URL path.

        Returns:
            bool: True if the path is excluded.
        """
        return any(path.startswith(excluded) for excluded in self.excluded_paths)

    async def dispatch(
        self,
        request: Request,
        call_next: RequestResponseEndpoint,
    ) -> Response:
        """Process the request with logging instrumentation.

        Args:
            request: The incoming HTTP request.
            call_next: The next middleware or route handler.

        Returns:
            Response: The HTTP response from downstream.
        """
        path = request.url.path

        # Generate and bind request ID
        request_id = str(uuid.uuid4())
        request.state.request_id = request_id

        # Bind request context for all log entries within this request
        structlog.contextvars.clear_contextvars()
        structlog.contextvars.bind_contextvars(request_id=request_id)

        # Skip detailed logging for excluded paths
        if self._is_excluded(path):
            response = await call_next(request)
            response.headers["X-Request-ID"] = request_id
            return response

        client_ip = self._get_client_ip(request)
        method = request.method
        start_time = time.monotonic()

        # Log the incoming request
        self._logger.info(
            "Request started",
            method=method,
            path=path,
            client_ip=client_ip,
            query_params=str(request.query_params) if request.query_params else None,
        )

        # Process the request
        try:
            response = await call_next(request)
        except Exception as exc:
            duration_ms = round((time.monotonic() - start_time) * 1000, 2)
            self._logger.error(
                "Request failed with unhandled exception",
                method=method,
                path=path,
                client_ip=client_ip,
                duration_ms=duration_ms,
                error=str(exc),
                error_type=type(exc).__name__,
            )
            raise

        duration_ms = round((time.monotonic() - start_time) * 1000, 2)

        # Extract user_id from request state if authentication middleware ran
        user_id: Optional[str] = None
        if hasattr(request.state, "user") and request.state.user is not None:
            user_id = getattr(request.state.user, "sub", None)

        # Choose log level based on status code
        status_code = response.status_code
        log_kwargs = {
            "method": method,
            "path": path,
            "status_code": status_code,
            "duration_ms": duration_ms,
            "client_ip": client_ip,
        }

        if user_id:
            log_kwargs["user_id"] = user_id

        if status_code >= 500:
            self._logger.error("Request completed", **log_kwargs)
        elif status_code >= 400:
            self._logger.warning("Request completed", **log_kwargs)
        else:
            self._logger.info("Request completed", **log_kwargs)

        # Attach request ID to the response
        response.headers["X-Request-ID"] = request_id

        return response
