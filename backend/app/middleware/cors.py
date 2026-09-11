"""
CORS Configuration for VisionAI.

Reads allowed origins from the application configuration and applies
Cross-Origin Resource Sharing middleware to the FastAPI application.

Usage::

    from app.middleware.cors import setup_cors
    setup_cors(app)
"""

from __future__ import annotations

import structlog
from fastapi import FastAPI
from fastapi.middleware.cors import CORSMiddleware

from app.config import get_settings

logger = structlog.stdlib.get_logger(__name__)


def setup_cors(app: FastAPI) -> None:
    """Configure CORS middleware on the FastAPI application.

    Reads the list of allowed origins from ``Settings.cors_origins_list``
    and registers the ``CORSMiddleware`` with appropriate defaults.

    In development mode, all origins are allowed to simplify local
    testing.  In production, only the explicitly configured origins are
    permitted.

    The middleware allows:
    - Credentials (cookies, authorization headers)
    - Standard HTTP methods (GET, POST, PUT, PATCH, DELETE, OPTIONS)
    - Common request headers including Authorization and Content-Type

    Args:
        app: The FastAPI application instance to attach CORS middleware to.
    """
    settings = get_settings()
    origins: list[str] = settings.cors_origins_list

    # In development, allow all origins for convenience
    if settings.is_development:
        origins = ["*"]
        logger.info("CORS configured for development: allowing all origins")
    else:
        logger.info(
            "CORS configured",
            allowed_origins=origins,
            environment=settings.APP_ENV,
        )

    app.add_middleware(
        CORSMiddleware,
        allow_origins=origins,
        allow_credentials=True,
        allow_methods=["GET", "POST", "PUT", "PATCH", "DELETE", "OPTIONS"],
        allow_headers=[
            "Accept",
            "Accept-Language",
            "Authorization",
            "Cache-Control",
            "Content-Type",
            "DNT",
            "If-Modified-Since",
            "Keep-Alive",
            "Origin",
            "Referer",
            "User-Agent",
            "X-Requested-With",
            "X-Request-ID",
        ],
        expose_headers=[
            "Content-Disposition",
            "X-Request-ID",
            "X-RateLimit-Limit",
            "X-RateLimit-Remaining",
            "X-RateLimit-Reset",
        ],
        max_age=600,  # Cache preflight responses for 10 minutes
    )
