"""Standalone webhook endpoint and delivery models.

Provides a self-contained webhook subsystem with encrypted secrets,
per-event subscriptions, delivery tracking with retry support,
and a catalogue of well-known event types used throughout VisionAI.

This module complements the broader integration platform in
``app.models.integration`` by offering a dedicated, finer-grained
webhook model with per-endpoint retry/timeout knobs and Fernet-
encrypted signing secrets.
"""

from __future__ import annotations

import enum
import uuid
from datetime import datetime
from typing import TYPE_CHECKING, List, Optional

from sqlalchemy import (
    Boolean,
    DateTime,
    Float,
    ForeignKey,
    Integer,
    String,
    Text,
    func,
)
from sqlalchemy import Enum as SAEnum
from sqlalchemy import JSON as JSONB, JSON as ARRAY, Uuid as UUID
from sqlalchemy.orm import Mapped, mapped_column, relationship

from app.database import Base, TimestampMixin

if TYPE_CHECKING:
    from app.models.organization import Organization
    from app.models.user import User


# ── Webhook Event Type Constants ──────────────────────────────────────────


class WebhookEventType(str, enum.Enum):
    """All event types that can trigger a webhook delivery.

    These constants are referenced when creating or filtering webhook
    subscriptions and when dispatching events from the pipeline.
    """

    ALERT_CREATED = "alert.created"
    ALERT_RESOLVED = "alert.resolved"
    CAMERA_ONLINE = "camera.online"
    CAMERA_OFFLINE = "camera.offline"
    FACE_RECOGNIZED = "face.recognized"
    FACE_UNKNOWN = "face.unknown"
    VEHICLE_DETECTED = "vehicle.detected"
    ANOMALY_DETECTED = "anomaly.detected"
    PERSON_ENTERED = "person.entered"
    PERSON_EXITED = "person.exited"


WEBHOOK_EVENT_DESCRIPTIONS: dict[str, str] = {
    WebhookEventType.ALERT_CREATED.value: (
        "Fired when a new alert is created by a detection rule."
    ),
    WebhookEventType.ALERT_RESOLVED.value: (
        "Fired when an existing alert is resolved or marked as false positive."
    ),
    WebhookEventType.CAMERA_ONLINE.value: (
        "Fired when a camera comes back online after being unreachable."
    ),
    WebhookEventType.CAMERA_OFFLINE.value: (
        "Fired when a camera becomes unreachable or goes offline."
    ),
    WebhookEventType.FACE_RECOGNIZED.value: (
        "Fired when a known enrolled face is recognized by the system."
    ),
    WebhookEventType.FACE_UNKNOWN.value: (
        "Fired when an unknown face is detected in a restricted area."
    ),
    WebhookEventType.VEHICLE_DETECTED.value: (
        "Fired when a vehicle is detected entering or exiting a monitored zone."
    ),
    WebhookEventType.ANOMALY_DETECTED.value: (
        "Fired when an anomalous behaviour pattern is detected by the anomaly engine."
    ),
    WebhookEventType.PERSON_ENTERED.value: (
        "Fired when a person enters a monitored zone or area of interest."
    ),
    WebhookEventType.PERSON_EXITED.value: (
        "Fired when a person exits a monitored zone or area of interest."
    ),
}

WEBHOOK_EVENT_EXAMPLE_PAYLOADS: dict[str, dict] = {
    WebhookEventType.ALERT_CREATED.value: {
        "event": "alert.created",
        "timestamp": "2026-02-24T10:30:00Z",
        "data": {
            "alert_id": "550e8400-e29b-41d4-a716-446655440000",
            "title": "Intrusion detected in Entry Gate Zone",
            "severity": "high",
            "camera_id": "660e8400-e29b-41d4-a716-446655440001",
            "camera_name": "Main Entrance",
            "zone_name": "Entry Gate Zone",
        },
    },
    WebhookEventType.ALERT_RESOLVED.value: {
        "event": "alert.resolved",
        "timestamp": "2026-02-24T10:45:00Z",
        "data": {
            "alert_id": "550e8400-e29b-41d4-a716-446655440000",
            "resolved_by": "John Smith",
            "resolution_note": "Verified as delivery person.",
        },
    },
    WebhookEventType.CAMERA_ONLINE.value: {
        "event": "camera.online",
        "timestamp": "2026-02-24T10:00:00Z",
        "data": {
            "camera_id": "660e8400-e29b-41d4-a716-446655440001",
            "camera_name": "Main Entrance",
            "previous_status": "offline",
        },
    },
    WebhookEventType.CAMERA_OFFLINE.value: {
        "event": "camera.offline",
        "timestamp": "2026-02-24T09:55:00Z",
        "data": {
            "camera_id": "660e8400-e29b-41d4-a716-446655440001",
            "camera_name": "Main Entrance",
            "last_seen_at": "2026-02-24T09:50:00Z",
        },
    },
    WebhookEventType.FACE_RECOGNIZED.value: {
        "event": "face.recognized",
        "timestamp": "2026-02-24T11:00:00Z",
        "data": {
            "person_id": "770e8400-e29b-41d4-a716-446655440002",
            "person_name": "Alice Johnson",
            "camera_name": "Lobby Camera",
            "confidence": 0.97,
        },
    },
    WebhookEventType.FACE_UNKNOWN.value: {
        "event": "face.unknown",
        "timestamp": "2026-02-24T11:05:00Z",
        "data": {
            "camera_name": "Server Room Entrance",
            "snapshot_url": "/snapshots/unknown_face_20260224_110500.jpg",
        },
    },
    WebhookEventType.VEHICLE_DETECTED.value: {
        "event": "vehicle.detected",
        "timestamp": "2026-02-24T08:30:00Z",
        "data": {
            "plate_number": "ABC-1234",
            "camera_name": "Parking Gate A",
            "vehicle_category": "sedan",
            "direction": "entry",
        },
    },
    WebhookEventType.ANOMALY_DETECTED.value: {
        "event": "anomaly.detected",
        "timestamp": "2026-02-24T14:00:00Z",
        "data": {
            "anomaly_type": "unusual_crowd_density",
            "camera_name": "Food Court",
            "confidence": 0.85,
            "description": "Crowd density 3x above average for this time.",
        },
    },
    WebhookEventType.PERSON_ENTERED.value: {
        "event": "person.entered",
        "timestamp": "2026-02-24T12:15:00Z",
        "data": {
            "zone_id": "880e8400-e29b-41d4-a716-446655440003",
            "zone_name": "Restricted Area B",
            "camera_name": "Hallway Camera 3",
            "person_count": 1,
        },
    },
    WebhookEventType.PERSON_EXITED.value: {
        "event": "person.exited",
        "timestamp": "2026-02-24T12:25:00Z",
        "data": {
            "zone_id": "880e8400-e29b-41d4-a716-446655440003",
            "zone_name": "Restricted Area B",
            "camera_name": "Hallway Camera 3",
            "dwell_time_seconds": 600,
        },
    },
}


# ── Webhook Delivery Status ──────────────────────────────────────────────


class WebhookDeliveryStatus(str, enum.Enum):
    """Status of a single webhook delivery attempt."""

    PENDING = "pending"
    DELIVERED = "delivered"
    FAILED = "failed"


# ── Webhook Endpoint Model ───────────────────────────────────────────────


class WebhookEndpointV2(Base):
    """A registered webhook endpoint that receives event notifications.

    Each endpoint stores a Fernet-encrypted HMAC signing secret, a list
    of subscribed event types, optional custom headers (JSONB), and
    per-endpoint retry/timeout configuration.

    The ``secret`` column is encrypted at the application layer using
    :func:`app.utils.encryption.encrypt_string` before persistence and
    decrypted on read via :func:`app.utils.encryption.decrypt_string`.
    """

    __tablename__ = "webhook_endpoints_v2"

    org_id: Mapped[uuid.UUID] = mapped_column(
        UUID(as_uuid=True),
        ForeignKey("organizations.id", ondelete="CASCADE"),
        nullable=False,
        index=True,
    )
    name: Mapped[str] = mapped_column(
        String(255),
        nullable=False,
    )
    url: Mapped[str] = mapped_column(
        String(2048),
        nullable=False,
    )
    secret: Mapped[str] = mapped_column(
        String(1024),
        nullable=False,
        doc="Fernet-encrypted HMAC-SHA256 signing secret.",
    )
    is_active: Mapped[bool] = mapped_column(
        Boolean,
        nullable=False,
        default=True,
        server_default="true",
    )
    events: Mapped[list[str]] = mapped_column(
        ARRAY(String),
        nullable=False,
        default=list,
        doc="List of subscribed event type strings.",
    )
    headers_json: Mapped[Optional[dict]] = mapped_column(
        JSONB,
        nullable=True,
        default=None,
        doc="Optional custom HTTP headers sent with each delivery.",
    )
    retry_count: Mapped[int] = mapped_column(
        Integer,
        nullable=False,
        default=3,
        server_default="3",
        doc="Maximum number of retry attempts for failed deliveries.",
    )
    timeout_seconds: Mapped[int] = mapped_column(
        Integer,
        nullable=False,
        default=30,
        server_default="30",
        doc="HTTP request timeout in seconds.",
    )
    created_by: Mapped[Optional[uuid.UUID]] = mapped_column(
        UUID(as_uuid=True),
        ForeignKey("users.id", ondelete="SET NULL"),
        nullable=True,
    )

    # ── Relationships ──────────────────────────────────────────────────
    organization: Mapped["Organization"] = relationship(
        "Organization",
        lazy="selectin",
    )
    creator: Mapped[Optional["User"]] = relationship(
        "User",
        lazy="selectin",
    )
    deliveries: Mapped[List["WebhookDeliveryV2"]] = relationship(
        "WebhookDeliveryV2",
        back_populates="webhook",
        cascade="all, delete-orphan",
        lazy="noload",
    )

    def __repr__(self) -> str:
        return (
            f"<WebhookEndpointV2(id={self.id}, name='{self.name}', "
            f"url='{self.url[:60]}...', active={self.is_active})>"
        )


# ── Webhook Delivery Model ───────────────────────────────────────────────


class WebhookDeliveryV2(Base):
    """Record of a single webhook delivery attempt.

    Tracks the full lifecycle of a delivery: the outbound payload,
    HTTP response status/body, retry scheduling, latency measurement,
    and terminal status.
    """

    __tablename__ = "webhook_delivery_v2s"

    webhook_id: Mapped[uuid.UUID] = mapped_column(
        UUID(as_uuid=True),
        ForeignKey("webhook_endpoints_v2.id", ondelete="CASCADE"),
        nullable=False,
        index=True,
    )
    event_type: Mapped[str] = mapped_column(
        String(100),
        nullable=False,
        index=True,
    )
    payload_json: Mapped[dict] = mapped_column(
        JSONB,
        nullable=False,
    )
    status: Mapped[WebhookDeliveryStatus] = mapped_column(
        String(32),
        nullable=False,
        default=WebhookDeliveryStatus.PENDING,
        server_default=WebhookDeliveryStatus.PENDING.value,
    )
    status_code: Mapped[Optional[int]] = mapped_column(
        Integer,
        nullable=True,
        doc="HTTP response status code.",
    )
    response_body: Mapped[Optional[str]] = mapped_column(
        Text,
        nullable=True,
        doc="Truncated response body (max 4096 chars).",
    )
    attempts: Mapped[int] = mapped_column(
        Integer,
        nullable=False,
        default=0,
        server_default="0",
    )
    next_retry_at: Mapped[Optional[datetime]] = mapped_column(
        DateTime(timezone=True),
        nullable=True,
        doc="Scheduled UTC time for the next retry attempt.",
    )
    error_message: Mapped[Optional[str]] = mapped_column(
        Text,
        nullable=True,
        doc="Error description if delivery failed.",
    )
    duration_ms: Mapped[Optional[float]] = mapped_column(
        Float,
        nullable=True,
        doc="Round-trip latency of the delivery request in milliseconds.",
    )
    delivered_at: Mapped[Optional[datetime]] = mapped_column(
        DateTime(timezone=True),
        nullable=True,
        doc="Timestamp when delivery succeeded.",
    )

    # ── Relationships ──────────────────────────────────────────────────
    webhook: Mapped["WebhookEndpointV2"] = relationship(
        "WebhookEndpointV2",
        back_populates="deliveries",
        lazy="selectin",
    )

    def __repr__(self) -> str:
        return (
            f"<WebhookDeliveryV2(id={self.id}, webhook_id={self.webhook_id}, "
            f"event={self.event_type}, status={self.status.value}, "
            f"attempts={self.attempts})>"
        )
