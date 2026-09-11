"""Shared Pydantic schemas used across the VisionAI platform.

Provides generic pagination, response wrappers, date-range filters,
sorting parameters, bulk operations, and health-check responses.
"""

from __future__ import annotations

import math
from datetime import datetime
from typing import Any, Generic, Literal, TypeVar
from uuid import UUID

from pydantic import BaseModel, ConfigDict, Field

T = TypeVar("T")


# ── Pagination ────────────────────────────────────────────────────────────────


class PaginationParams(BaseModel):
    """Query parameters for paginated list endpoints."""

    page: int = Field(
        default=1,
        ge=1,
        description="Page number (1-indexed).",
        examples=[1],
    )
    page_size: int = Field(
        default=20,
        ge=1,
        le=100,
        description="Number of items per page. Maximum 100.",
        examples=[20],
    )

    @property
    def offset(self) -> int:
        """Calculate the SQL OFFSET from page and page_size."""
        return (self.page - 1) * self.page_size


class PaginationMeta(BaseModel):
    """Metadata block returned inside every paginated response."""

    page: int = Field(description="Current page number.")
    page_size: int = Field(description="Items per page.")
    total: int = Field(description="Total number of items matching the query.")
    total_pages: int = Field(description="Total number of pages.")


class PaginatedResponse(BaseModel, Generic[T]):
    """Generic paginated response wrapper.

    Usage::

        PaginatedResponse[CameraResponse](
            status="success",
            data=[...],
            meta=PaginationMeta(page=1, page_size=20, total=42, total_pages=3),
        )
    """

    status: str = Field(
        default="success",
        description="Response status indicator.",
        examples=["success"],
    )
    data: list[T] = Field(description="List of items for the current page.")
    meta: PaginationMeta = Field(description="Pagination metadata.")

    @classmethod
    def create(
        cls,
        items: list[T],
        total: int,
        page: int,
        page_size: int,
    ) -> PaginatedResponse[T]:
        """Convenience factory that auto-computes *total_pages*."""
        return cls(
            data=items,
            meta=PaginationMeta(
                page=page,
                page_size=page_size,
                total=total,
                total_pages=math.ceil(total / page_size) if page_size else 0,
            ),
        )


# ── Standard Response Wrappers ────────────────────────────────────────────────


class SuccessResponse(BaseModel):
    """Uniform envelope for successful non-paginated responses."""

    status: str = Field(
        default="success",
        description="Always 'success' for this response type.",
        examples=["success"],
    )
    data: Any = Field(
        default=None,
        description="Response payload. Type varies by endpoint.",
    )
    message: str | None = Field(
        default=None,
        description="Optional human-readable message.",
        examples=["Camera created successfully."],
    )


class ErrorResponse(BaseModel):
    """Uniform envelope for error responses (4xx / 5xx).

    The ``code`` field carries a machine-readable error identifier that
    front-end clients can use for i18n or programmatic handling.
    """

    status: str = Field(
        default="error",
        description="Always 'error' for this response type.",
        examples=["error"],
    )
    detail: str = Field(
        description="Human-readable error description.",
        examples=["Camera with the given ID was not found."],
    )
    code: str = Field(
        description="Machine-readable error code.",
        examples=["CAMERA_NOT_FOUND"],
    )


# ── Filters & Sorting ────────────────────────────────────────────────────────


class DateRangeFilter(BaseModel):
    """Reusable date-range filter for analytics and list endpoints."""

    start_date: datetime | None = Field(
        default=None,
        description="Inclusive lower bound (ISO-8601 with timezone).",
        examples=["2025-01-01T00:00:00Z"],
    )
    end_date: datetime | None = Field(
        default=None,
        description="Inclusive upper bound (ISO-8601 with timezone).",
        examples=["2025-01-31T23:59:59Z"],
    )


class SortParams(BaseModel):
    """Reusable sorting parameters for list endpoints."""

    sort_by: str = Field(
        default="created_at",
        description="Column name to sort by.",
        examples=["created_at", "name"],
    )
    sort_order: Literal["asc", "desc"] = Field(
        default="desc",
        description="Sort direction.",
        examples=["desc"],
    )


# ── Bulk Operations ──────────────────────────────────────────────────────────


class BulkDeleteRequest(BaseModel):
    """Request body for bulk-delete endpoints."""

    ids: list[UUID] = Field(
        min_length=1,
        description="List of resource UUIDs to delete.",
        examples=[["550e8400-e29b-41d4-a716-446655440000"]],
    )


# ── Health Check ──────────────────────────────────────────────────────────────


class HealthResponse(BaseModel):
    """Response from the ``/health`` endpoint."""

    status: str = Field(
        description="Overall platform health: healthy, degraded, or unhealthy.",
        examples=["healthy"],
    )
    version: str = Field(
        description="Running application version.",
        examples=["1.0.0"],
    )
    uptime: float = Field(
        description="Process uptime in seconds.",
        examples=[86400.5],
    )
    services: dict[str, str] = Field(
        description="Per-service health map (service name -> status).",
        examples=[
            {
                "database": "healthy",
                "redis": "healthy",
                "minio": "healthy",
                "mediamtx": "healthy",
            }
        ],
    )
