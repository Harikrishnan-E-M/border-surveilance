"""
VisionAI Custom Exceptions and FastAPI Exception Handlers.

Provides a hierarchy of domain-specific exceptions and the corresponding
FastAPI exception handlers that serialize them into a consistent JSON
response format::

    {
        "status": "error",
        "detail": "Human-readable message",
        "code": "MACHINE_READABLE_CODE"
    }
"""

from __future__ import annotations

from typing import Any

import structlog
from fastapi import FastAPI, Request, status
from fastapi.exceptions import RequestValidationError
from fastapi.responses import JSONResponse
from starlette.exceptions import HTTPException as StarletteHTTPException

logger = structlog.stdlib.get_logger(__name__)


# ── Exception Hierarchy ───────────────────────────────────────────────────


class VisionAIException(Exception):
    """Base exception for all VisionAI domain errors.

    Attributes:
        message: Human-readable error description.
        code: Machine-readable error code (UPPER_SNAKE_CASE).
        status_code: HTTP status code to return to the client.
        headers: Optional extra HTTP headers.
    """

    message: str = "An unexpected error occurred"
    code: str = "INTERNAL_ERROR"
    status_code: int = status.HTTP_500_INTERNAL_SERVER_ERROR
    headers: dict[str, str] | None = None

    def __init__(
        self,
        message: str | None = None,
        code: str | None = None,
        status_code: int | None = None,
        headers: dict[str, str] | None = None,
    ) -> None:
        if message is not None:
            self.message = message
        if code is not None:
            self.code = code
        if status_code is not None:
            self.status_code = status_code
        if headers is not None:
            self.headers = headers
        super().__init__(self.message)

    def to_dict(self) -> dict[str, Any]:
        """Serialize to the standard error response body."""
        return {
            "status": "error",
            "detail": self.message,
            "code": self.code,
        }


class NotFoundError(VisionAIException):
    """Raised when a requested resource does not exist."""

    message = "The requested resource was not found"
    code = "NOT_FOUND"
    status_code = status.HTTP_404_NOT_FOUND

    def __init__(self, resource: str = "Resource", identifier: Any = None, **kwargs: Any) -> None:
        msg = f"{resource} not found"
        if identifier is not None:
            msg = f"{resource} with id '{identifier}' not found"
        super().__init__(message=msg, **kwargs)


class AuthenticationError(VisionAIException):
    """Raised when authentication fails (invalid or missing credentials)."""

    message = "Authentication failed"
    code = "AUTHENTICATION_ERROR"
    status_code = status.HTTP_401_UNAUTHORIZED
    headers = {"WWW-Authenticate": "Bearer"}


class AuthorizationError(VisionAIException):
    """Raised when an authenticated user lacks required permissions."""

    message = "You do not have permission to perform this action"
    code = "AUTHORIZATION_ERROR"
    status_code = status.HTTP_403_FORBIDDEN


class ValidationError(VisionAIException):
    """Raised when input data fails business-logic validation.

    This is distinct from Pydantic's ``RequestValidationError`` which
    handles structural/type validation.  Use this for domain-level
    checks (e.g. "camera already exists with this IP").
    """

    message = "Validation error"
    code = "VALIDATION_ERROR"
    status_code = status.HTTP_422_UNPROCESSABLE_ENTITY

    def __init__(self, message: str = "Validation error", errors: list[dict[str, Any]] | None = None, **kwargs: Any) -> None:
        self.errors = errors or []
        super().__init__(message=message, **kwargs)

    def to_dict(self) -> dict[str, Any]:
        payload = super().to_dict()
        if self.errors:
            payload["errors"] = self.errors
        return payload


class StreamError(VisionAIException):
    """Raised when a camera stream operation fails (connect, read, decode)."""

    message = "Stream error"
    code = "STREAM_ERROR"
    status_code = status.HTTP_502_BAD_GATEWAY


class InferenceError(VisionAIException):
    """Raised when an ML model inference operation fails."""

    message = "Inference error"
    code = "INFERENCE_ERROR"
    status_code = status.HTTP_500_INTERNAL_SERVER_ERROR


class StorageError(VisionAIException):
    """Raised when a storage operation fails (MinIO, filesystem)."""

    message = "Storage error"
    code = "STORAGE_ERROR"
    status_code = status.HTTP_502_BAD_GATEWAY


class RateLimitError(VisionAIException):
    """Raised when the client exceeds the rate limit."""

    message = "Rate limit exceeded. Please try again later."
    code = "RATE_LIMIT_EXCEEDED"
    status_code = status.HTTP_429_TOO_MANY_REQUESTS
    headers = {"Retry-After": "60"}

    def __init__(self, retry_after: int = 60, **kwargs: Any) -> None:
        self.headers = {"Retry-After": str(retry_after)}
        super().__init__(**kwargs)


class DuplicateError(VisionAIException):
    """Raised when a create operation violates a uniqueness constraint."""

    message = "Resource already exists"
    code = "DUPLICATE_ERROR"
    status_code = status.HTTP_409_CONFLICT


class ConfigurationError(VisionAIException):
    """Raised when a required service is misconfigured."""

    message = "Service configuration error"
    code = "CONFIGURATION_ERROR"
    status_code = status.HTTP_500_INTERNAL_SERVER_ERROR


# ── FastAPI Exception Handlers ────────────────────────────────────────────


def _error_response(
    status_code: int,
    detail: str,
    code: str,
    headers: dict[str, str] | None = None,
    extra: dict[str, Any] | None = None,
) -> JSONResponse:
    """Build a standardised JSON error response."""
    body: dict[str, Any] = {
        "status": "error",
        "detail": detail,
        "code": code,
    }
    if extra:
        body.update(extra)
    return JSONResponse(status_code=status_code, content=body, headers=headers)


async def visionai_exception_handler(request: Request, exc: VisionAIException) -> JSONResponse:
    """Handle all VisionAI domain exceptions."""
    logger.warning(
        "Domain exception",
        code=exc.code,
        detail=exc.message,
        path=str(request.url),
        method=request.method,
    )
    payload = exc.to_dict()
    return JSONResponse(
        status_code=exc.status_code,
        content=payload,
        headers=exc.headers,
    )


async def http_exception_handler(request: Request, exc: StarletteHTTPException) -> JSONResponse:
    """Handle Starlette/FastAPI HTTP exceptions (404, 405, etc.)."""
    code_map = {
        400: "BAD_REQUEST",
        401: "UNAUTHORIZED",
        403: "FORBIDDEN",
        404: "NOT_FOUND",
        405: "METHOD_NOT_ALLOWED",
        408: "REQUEST_TIMEOUT",
        409: "CONFLICT",
        413: "PAYLOAD_TOO_LARGE",
        422: "UNPROCESSABLE_ENTITY",
        429: "TOO_MANY_REQUESTS",
        500: "INTERNAL_SERVER_ERROR",
        502: "BAD_GATEWAY",
        503: "SERVICE_UNAVAILABLE",
        504: "GATEWAY_TIMEOUT",
    }
    return _error_response(
        status_code=exc.status_code,
        detail=str(exc.detail),
        code=code_map.get(exc.status_code, "HTTP_ERROR"),
        headers=getattr(exc, "headers", None),
    )


async def validation_exception_handler(request: Request, exc: RequestValidationError) -> JSONResponse:
    """Handle Pydantic request validation errors.

    Reformats the raw Pydantic errors into the standard VisionAI
    response shape with an additional ``errors`` list.
    """
    errors = []
    for error in exc.errors():
        field = " -> ".join(str(loc) for loc in error.get("loc", []))
        errors.append(
            {
                "field": field,
                "message": error.get("msg", "Invalid value"),
                "type": error.get("type", "value_error"),
            }
        )
    logger.info(
        "Request validation failed",
        path=str(request.url),
        error_count=len(errors),
    )
    return _error_response(
        status_code=status.HTTP_422_UNPROCESSABLE_ENTITY,
        detail="Request validation failed",
        code="VALIDATION_ERROR",
        extra={"errors": errors},
    )


async def unhandled_exception_handler(request: Request, exc: Exception) -> JSONResponse:
    """Last-resort handler for unexpected exceptions.

    Logs the full traceback and returns a generic 500 to the client
    without leaking internal details.
    """
    logger.exception(
        "Unhandled exception",
        path=str(request.url),
        method=request.method,
        error_type=type(exc).__name__,
    )
    return _error_response(
        status_code=status.HTTP_500_INTERNAL_SERVER_ERROR,
        detail="An unexpected internal error occurred",
        code="INTERNAL_ERROR",
    )


def register_exception_handlers(app: FastAPI) -> None:
    """Register all exception handlers on the FastAPI application.

    Call this during app initialisation (e.g. inside ``create_app()``).

    Args:
        app: The FastAPI application instance.
    """
    app.add_exception_handler(VisionAIException, visionai_exception_handler)  # type: ignore[arg-type]
    app.add_exception_handler(StarletteHTTPException, http_exception_handler)  # type: ignore[arg-type]
    app.add_exception_handler(RequestValidationError, validation_exception_handler)  # type: ignore[arg-type]
    app.add_exception_handler(Exception, unhandled_exception_handler)  # type: ignore[arg-type]
