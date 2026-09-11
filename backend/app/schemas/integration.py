"""Pydantic schemas for the Webhook & Integration Platform.

Covers webhook CRUD, delivery logs, integration configuration,
event type listings, and test request/response schemas.
"""

from __future__ import annotations


from datetime import datetime
from typing import Any, Optional
from uuid import UUID

from pydantic import BaseModel, ConfigDict, Field, field_validator, HttpUrl


# ── Webhook Schemas ──────────────────────────────────────────────────────


class RetryPolicy(BaseModel):
    """Webhook retry policy configuration."""

    max_retries: int = Field(
        default=5,
        ge=0,
        le=20,
        description="Maximum number of retry attempts.",
        examples=[5],
    )
    backoff_seconds: int = Field(
        default=30,
        ge=5,
        le=3600,
        description="Initial backoff interval in seconds (doubles each retry).",
        examples=[30],
    )


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
        description="HTTPS URL to receive webhook POSTs.",
        examples=["https://hooks.example.com/visionai"],
    )
    events: list[str] = Field(
        min_length=1,
        description="List of event type strings to subscribe to.",
        examples=[["alert.created", "alert.resolved", "camera.offline"]],
    )
    headers: dict[str, str] | None = Field(
        default=None,
        description="Optional custom HTTP headers included in deliveries.",
        examples=[{"X-Custom-Key": "my-value"}],
    )
    retry_policy: RetryPolicy = Field(
        default_factory=RetryPolicy,
        description="Retry policy for failed deliveries.",
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
        from app.models.integration import EventType

        valid = {e.value for e in EventType}
        invalid = [e for e in v if e not in valid]
        if invalid:
            raise ValueError(f"Unknown event types: {', '.join(invalid)}")
        return v


class WebhookUpdate(BaseModel):
    """Request body for updating an existing webhook endpoint."""

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
    events: list[str] | None = Field(
        default=None,
        min_length=1,
        description="Updated event subscriptions.",
    )
    headers: dict[str, str] | None = Field(
        default=None,
        description="Updated custom headers.",
    )
    is_active: bool | None = Field(
        default=None,
        description="Enable or disable the webhook.",
    )
    retry_policy: RetryPolicy | None = Field(
        default=None,
        description="Updated retry policy.",
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
            from app.models.integration import EventType

            valid = {e.value for e in EventType}
            invalid = [e for e in v if e not in valid]
            if invalid:
                raise ValueError(f"Unknown event types: {', '.join(invalid)}")
        return v


class WebhookResponse(BaseModel):
    """Webhook endpoint response representation."""

    model_config = ConfigDict(from_attributes=True)

    id: UUID = Field(description="Webhook endpoint ID.")
    org_id: UUID = Field(description="Owning organization ID.")
    name: str = Field(description="Webhook name.")
    url: str = Field(description="Webhook URL.")
    secret: str = Field(description="HMAC-SHA256 signing secret.")
    events: list[str] = Field(description="Subscribed event types.")
    headers: dict[str, str] | None = Field(default=None, description="Custom HTTP headers.")
    is_active: bool = Field(description="Whether the webhook is active.")
    retry_policy: dict[str, Any] | None = Field(default=None, description="Retry policy.")
    created_by: UUID | None = Field(default=None, description="Creator user ID.")
    created_at: datetime = Field(description="Creation timestamp.")
    updated_at: datetime = Field(description="Last update timestamp.")


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
    payload: dict[str, Any] = Field(description="Delivered JSON payload.")
    status: str = Field(description="Delivery status: pending, success, failed, retrying.")
    status_code: int | None = Field(default=None, description="HTTP response status code.")
    response_body: str | None = Field(default=None, description="Truncated response body.")
    attempt_number: int = Field(description="Current attempt number.")
    next_retry_at: datetime | None = Field(default=None, description="Next retry timestamp.")
    error_message: str | None = Field(default=None, description="Error details.")
    created_at: datetime = Field(description="Delivery creation timestamp.")
    completed_at: datetime | None = Field(default=None, description="Completion timestamp.")


class DeliveryListResponse(BaseModel):
    """Paginated delivery log response."""

    status: str = Field(default="success")
    data: list[WebhookDeliveryResponse] = Field(description="Delivery records.")
    meta: dict[str, Any] = Field(description="Pagination metadata.")


class DeliveryFilters(BaseModel):
    """Query filters for delivery log listing."""

    status: str | None = Field(
        default=None,
        description="Filter by delivery status.",
    )
    event_type: str | None = Field(
        default=None,
        description="Filter by event type.",
    )
    start_date: datetime | None = Field(
        default=None,
        description="Start date for time range filter.",
    )
    end_date: datetime | None = Field(
        default=None,
        description="End date for time range filter.",
    )


# ── Integration Config Schemas ───────────────────────────────────────────


class IntegrationConfigCreate(BaseModel):
    """Request body for creating a third-party integration."""

    integration_type: str = Field(
        description="Integration type: slack, teams, pagerduty, jira, custom.",
        examples=["slack"],
    )
    name: str = Field(
        min_length=1,
        max_length=255,
        description="Human-readable integration name.",
        examples=["Production Slack Channel"],
    )
    config: dict[str, Any] = Field(
        description="Integration-specific configuration.",
        examples=[
            {
                "webhook_url": "https://hooks.slack.com/services/T00/B00/xxxx",
                "channel": "#alerts",
                "username": "VisionAI Bot",
            }
        ],
    )

    @field_validator("integration_type")
    @classmethod
    def validate_integration_type(cls, v: str) -> str:
        from app.models.integration import IntegrationType

        valid = {t.value for t in IntegrationType}
        if v not in valid:
            raise ValueError(f"Invalid integration type '{v}'. Must be one of: {', '.join(sorted(valid))}")
        return v


class IntegrationConfigUpdate(BaseModel):
    """Request body for updating a third-party integration."""

    name: str | None = Field(
        default=None,
        min_length=1,
        max_length=255,
        description="Updated integration name.",
    )
    config: dict[str, Any] | None = Field(
        default=None,
        description="Updated configuration.",
    )
    is_active: bool | None = Field(
        default=None,
        description="Enable or disable the integration.",
    )


class IntegrationConfigResponse(BaseModel):
    """Integration configuration response representation."""

    model_config = ConfigDict(from_attributes=True)

    id: UUID = Field(description="Integration config ID.")
    org_id: UUID = Field(description="Owning organization ID.")
    integration_type: str = Field(description="Integration type.")
    name: str = Field(description="Integration name.")
    config: dict[str, Any] = Field(description="Integration configuration (redacted secrets).")
    is_active: bool = Field(description="Whether the integration is active.")
    created_by: UUID | None = Field(default=None, description="Creator user ID.")
    created_at: datetime = Field(description="Creation timestamp.")
    updated_at: datetime = Field(description="Last update timestamp.")


class IntegrationConfigListResponse(BaseModel):
    """Paginated integration config list response."""

    status: str = Field(default="success")
    data: list[IntegrationConfigResponse] = Field(description="Integration configs.")
    meta: dict[str, Any] = Field(description="Pagination metadata.")


# ── Event Type Schema ────────────────────────────────────────────────────


class EventTypeInfo(BaseModel):
    """Single event type with description and example payload."""

    event_type: str = Field(description="Event type identifier.")
    description: str = Field(description="Human-readable description.")
    example_payload: dict[str, Any] = Field(description="Example webhook payload for this event.")
    subscriber_count: int = Field(
        default=0,
        description="Number of active webhooks subscribed to this event.",
    )


class EventTypeList(BaseModel):
    """List of all available event types."""

    status: str = Field(default="success")
    data: list[EventTypeInfo] = Field(description="Available event types.")


# ── Test Schemas ─────────────────────────────────────────────────────────


class WebhookTestRequest(BaseModel):
    """Request body for testing a webhook delivery."""

    event_type: str = Field(
        default="alert.created",
        description="Event type to simulate in the test delivery.",
        examples=["alert.created"],
    )

    @field_validator("event_type")
    @classmethod
    def validate_event_type(cls, v: str) -> str:
        from app.models.integration import EventType

        valid = {e.value for e in EventType}
        if v not in valid:
            raise ValueError(f"Unknown event type: {v}")
        return v


class WebhookTestResponse(BaseModel):
    """Response from a webhook test delivery."""

    status: str = Field(default="success")
    data: dict[str, Any] = Field(
        description="Test delivery result including status code and response body.",
    )


class IntegrationTestResponse(BaseModel):
    """Response from an integration connection test."""

    status: str = Field(default="success")
    data: dict[str, Any] = Field(
        description="Test result including success flag and optional error.",
    )
