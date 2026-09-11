"""Pydantic schemas for the standalone Webhook subsystem.

Covers webhook endpoint CRUD, delivery log responses, test request/
response payloads, and aggregate delivery statistics.
"""

from __future__ import annotations


from datetime import datetime
from typing import Any, Optional
from uuid import UUID

from pydantic import BaseModel, ConfigDict, Field, field_validator


# ── Webhook Endpoint Schemas ─────────────────────────────────────────────


class WebhookCreate(BaseModel):
    """Request body for creating a new webhook endpoint."""

    name: str = Field(
        min_length=1,
        max_length=255,
        description="Human-readable webhook name.",
        examples=["Production Alert Hook"],
    )
    url: str = Field(
        min_length=1,
        max_length=2048,
        description="HTTPS URL that will receive webhook POST requests.",
        examples=["https://hooks.example.com/visionai"],
    )
    secret: str | None = Field(
        default=None,
        max_length=512,
        description=(
            "HMAC-SHA256 signing secret. If omitted the server will "
            "auto-generate a cryptographically random secret."
        ),
        examples=["whsec_abc123..."],
    )
    events: list[str] = Field(
        min_length=1,
        description="List of event type strings to subscribe to.",
        examples=[["alert.created", "alert.resolved", "camera.offline"]],
    )
    headers: dict[str, str] | None = Field(
        default=None,
        description="Optional custom HTTP headers sent with every delivery.",
        examples=[{"X-Custom-Key": "my-value"}],
    )
    retry_count: int = Field(
        default=3,
        ge=0,
        le=20,
        description="Maximum number of retry attempts.",
        examples=[3],
    )
    timeout: int = Field(
        default=30,
        ge=1,
        le=120,
        description="HTTP request timeout in seconds.",
        examples=[30],
    )

    @field_validator("url")
    @classmethod
    def validate_url(cls, v: str) -> str:
        """Ensure the webhook URL uses HTTPS or HTTP (for local dev)."""
        if not v.startswith(("https://", "http://")):
            raise ValueError("Webhook URL must start with https:// or http://")
        return v

    @field_validator("events")
    @classmethod
    def validate_events(cls, v: list[str]) -> list[str]:
        """Validate that all event types are recognized."""
        from app.models.webhook import WebhookEventType

        valid = {e.value for e in WebhookEventType}
        invalid = [e for e in v if e not in valid]
        if invalid:
            raise ValueError(f"Unknown event types: {', '.join(invalid)}")
        return v


class WebhookUpdate(BaseModel):
    """Request body for partially updating a webhook endpoint."""

    name: str | None = Field(
        default=None,
        min_length=1,
        max_length=255,
        description="Updated webhook name.",
    )
    url: str | None = Field(
        default=None,
        min_length=1,
        max_length=2048,
        description="Updated webhook URL.",
    )
    secret: str | None = Field(
        default=None,
        max_length=512,
        description="Updated HMAC signing secret.",
    )
    events: list[str] | None = Field(
        default=None,
        min_length=1,
        description="Updated event subscriptions.",
    )
    headers: dict[str, str] | None = Field(
        default=None,
        description="Updated custom HTTP headers.",
    )
    is_active: bool | None = Field(
        default=None,
        description="Enable or disable the webhook.",
    )
    retry_count: int | None = Field(
        default=None,
        ge=0,
        le=20,
        description="Updated max retry count.",
    )
    timeout: int | None = Field(
        default=None,
        ge=1,
        le=120,
        description="Updated timeout in seconds.",
    )

    @field_validator("url")
    @classmethod
    def validate_url(cls, v: str | None) -> str | None:
        if v is not None and not v.startswith(("https://", "http://")):
            raise ValueError("Webhook URL must start with https:// or http://")
        return v

    @field_validator("events")
    @classmethod
    def validate_events(cls, v: list[str] | None) -> list[str] | None:
        if v is not None:
            from app.models.webhook import WebhookEventType

            valid = {e.value for e in WebhookEventType}
            invalid = [e for e in v if e not in valid]
            if invalid:
                raise ValueError(f"Unknown event types: {', '.join(invalid)}")
        return v


class WebhookResponse(BaseModel):
    """Webhook endpoint response representation.

    The ``secret`` field is intentionally **omitted** from responses
    to prevent accidental leakage.  The secret is only visible once
    at creation time via the ``secret_plaintext`` field.
    """

    model_config = ConfigDict(from_attributes=True)

    id: UUID = Field(description="Webhook endpoint ID.")
    org_id: UUID = Field(description="Owning organization ID.")
    name: str = Field(description="Webhook name.")
    url: str = Field(description="Webhook URL.")
    events: list[str] = Field(description="Subscribed event types.")
    headers: dict[str, str] | None = Field(
        default=None,
        description="Custom HTTP headers.",
    )
    is_active: bool = Field(description="Whether the webhook is active.")
    retry_count: int = Field(description="Max retry attempts.")
    timeout: int = Field(description="Request timeout in seconds.")
    created_by: UUID | None = Field(
        default=None,
        description="Creator user ID.",
    )
    created_at: datetime = Field(description="Creation timestamp.")
    updated_at: datetime = Field(description="Last update timestamp.")
    secret_plaintext: str | None = Field(
        default=None,
        exclude=True,
        description="Plaintext secret -- only populated on creation.",
    )


class WebhookListResponse(BaseModel):
    """Paginated webhook list response."""

    status: str = Field(default="success")
    data: list[WebhookResponse] = Field(description="Webhook endpoints.")
    meta: dict[str, Any] = Field(description="Pagination metadata.")


# ── Webhook Delivery Schemas ─────────────────────────────────────────────


class WebhookDeliveryResponse(BaseModel):
    """Single webhook delivery record."""

    model_config = ConfigDict(from_attributes=True)

    id: UUID = Field(description="Delivery record ID.")
    webhook_id: UUID = Field(description="Parent webhook endpoint ID.")
    event_type: str = Field(description="Event type that triggered the delivery.")
    payload_json: dict[str, Any] = Field(description="Delivered JSON payload.")
    status: str = Field(
        description="Delivery status: pending, delivered, failed.",
    )
    status_code: int | None = Field(
        default=None,
        description="HTTP response status code.",
    )
    response_body: str | None = Field(
        default=None,
        description="Truncated response body.",
    )
    attempts: int = Field(description="Total delivery attempts so far.")
    next_retry_at: datetime | None = Field(
        default=None,
        description="Next retry timestamp.",
    )
    error_message: str | None = Field(
        default=None,
        description="Error details.",
    )
    duration_ms: float | None = Field(
        default=None,
        description="Delivery round-trip latency in milliseconds.",
    )
    created_at: datetime = Field(description="Delivery creation timestamp.")
    delivered_at: datetime | None = Field(
        default=None,
        description="Timestamp when delivery succeeded.",
    )


class WebhookDeliveryList(BaseModel):
    """Paginated delivery log response."""

    status: str = Field(default="success")
    data: list[WebhookDeliveryResponse] = Field(description="Delivery records.")
    meta: dict[str, Any] = Field(description="Pagination metadata.")


# ── Test Schemas ─────────────────────────────────────────────────────────


class WebhookTestRequest(BaseModel):
    """Request body for sending a test delivery to a webhook."""

    event_type: str = Field(
        default="alert.created",
        description="Event type to simulate.",
        examples=["alert.created"],
    )
    sample_payload: dict[str, Any] | None = Field(
        default=None,
        description=(
            "Custom payload to send. If omitted the server uses the "
            "standard example payload for the event type."
        ),
    )

    @field_validator("event_type")
    @classmethod
    def validate_event_type(cls, v: str) -> str:
        from app.models.webhook import WebhookEventType

        valid = {e.value for e in WebhookEventType}
        if v not in valid:
            raise ValueError(f"Unknown event type: {v}")
        return v


class WebhookTestResponse(BaseModel):
    """Response from a webhook test delivery."""

    success: bool = Field(description="Whether the test delivery succeeded.")
    status_code: int | None = Field(
        default=None,
        description="HTTP response status code.",
    )
    response_time_ms: float | None = Field(
        default=None,
        description="Round-trip time in milliseconds.",
    )
    error: str | None = Field(
        default=None,
        description="Error message if the test failed.",
    )


# ── Statistics Schemas ───────────────────────────────────────────────────


class EventTypeStats(BaseModel):
    """Delivery statistics for a single event type."""

    event_type: str = Field(description="Event type identifier.")
    total: int = Field(description="Total deliveries for this event type.")
    delivered: int = Field(description="Successful deliveries.")
    failed: int = Field(description="Failed deliveries.")
    pending: int = Field(description="Pending deliveries.")


class WebhookStats(BaseModel):
    """Aggregate delivery statistics for a webhook endpoint."""

    total_deliveries: int = Field(
        description="Total number of delivery attempts.",
    )
    success_rate: float = Field(
        description="Percentage of successful deliveries (0-100).",
    )
    avg_latency_ms: float = Field(
        description="Average delivery latency in milliseconds.",
    )
    by_event_type: list[EventTypeStats] = Field(
        description="Per-event-type delivery breakdown.",
    )


# ── Event Type Info ──────────────────────────────────────────────────────


class WebhookEventInfo(BaseModel):
    """Event type information for the event catalogue endpoint."""

    event_type: str = Field(description="Event type identifier.")
    description: str = Field(description="Human-readable description.")
    example_payload: dict[str, Any] = Field(
        description="Example webhook payload for this event type.",
    )
    subscriber_count: int = Field(
        default=0,
        description="Number of active webhook endpoints subscribed to this event.",
    )
