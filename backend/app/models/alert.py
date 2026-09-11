"""Alert and alert escalation models."""

import enum
import uuid
from datetime import datetime
from typing import TYPE_CHECKING, List, Optional

from sqlalchemy import DateTime, ForeignKey, String, Text, func
from sqlalchemy import Enum as SAEnum
from sqlalchemy.dialects.postgresql import JSON, UUID
from sqlalchemy.orm import Mapped, mapped_column, relationship

from app.database import Base, TimestampMixin
from app.models.rule import RuleSeverity, RuleType

if TYPE_CHECKING:
    from app.models.camera import Camera
    from app.models.organization import Organization
    from app.models.rule import Rule
    from app.models.user import User
    from app.models.zone import Zone


class AlertStatus(str, enum.Enum):
    """Lifecycle status of an alert."""

    NEW = "new"
    ACKNOWLEDGED = "acknowledged"
    RESOLVED = "resolved"
    FALSE_POSITIVE = "false_positive"
    ESCALATED = "escalated"


class Alert(Base):
    """An alert generated when a detection rule fires.

    Alerts capture the full context of the event: snapshot, video clip,
    detection metadata, and lifecycle status (new -> acknowledged ->
    resolved or false_positive). Alerts may be escalated to other users.
    """

    __tablename__ = "alerts"

    org_id: Mapped[uuid.UUID] = mapped_column(
        UUID(as_uuid=True), ForeignKey("organizations.id", ondelete="CASCADE"), nullable=False, index=True
    )
    camera_id: Mapped[uuid.UUID] = mapped_column(
        UUID(as_uuid=True), ForeignKey("cameras.id", ondelete="CASCADE"), nullable=False, index=True
    )
    zone_id: Mapped[Optional[uuid.UUID]] = mapped_column(
        UUID(as_uuid=True), ForeignKey("zones.id", ondelete="SET NULL"), nullable=True, index=True
    )
    rule_id: Mapped[uuid.UUID] = mapped_column(
        UUID(as_uuid=True), ForeignKey("rules.id", ondelete="CASCADE"), nullable=False, index=True
    )
    alert_type: Mapped[RuleType] = mapped_column(
        String(32),
        nullable=False,
    )
    severity: Mapped[RuleSeverity] = mapped_column(
        String(32),
        nullable=False,
    )
    title: Mapped[str] = mapped_column(String(512), nullable=False)
    description: Mapped[Optional[str]] = mapped_column(Text, nullable=True)
    snapshot_path: Mapped[Optional[str]] = mapped_column(String(1024), nullable=True)
    video_clip_path: Mapped[Optional[str]] = mapped_column(String(1024), nullable=True)
    metadata_json: Mapped[Optional[dict]] = mapped_column(JSON, nullable=True)
    status: Mapped[AlertStatus] = mapped_column(
        String(32),
        nullable=False,
        default=AlertStatus.NEW,
        server_default=AlertStatus.NEW.value,
    )
    acknowledged_by: Mapped[Optional[uuid.UUID]] = mapped_column(
        UUID(as_uuid=True), ForeignKey("users.id", ondelete="SET NULL"), nullable=True
    )
    acknowledged_at: Mapped[Optional[datetime]] = mapped_column(
        DateTime(timezone=True), nullable=True
    )
    resolved_by: Mapped[Optional[uuid.UUID]] = mapped_column(
        UUID(as_uuid=True), ForeignKey("users.id", ondelete="SET NULL"), nullable=True
    )
    resolved_at: Mapped[Optional[datetime]] = mapped_column(
        DateTime(timezone=True), nullable=True
    )

    # ── Relationships ──────────────────────────────────────────────────
    organization: Mapped["Organization"] = relationship(
        "Organization", back_populates="alerts", lazy="selectin"
    )
    camera: Mapped["Camera"] = relationship(
        "Camera", back_populates="alerts", lazy="selectin"
    )
    zone: Mapped[Optional["Zone"]] = relationship(
        "Zone", back_populates="alerts", lazy="selectin"
    )
    rule: Mapped["Rule"] = relationship(
        "Rule", back_populates="alerts", lazy="selectin"
    )
    acknowledged_by_user: Mapped[Optional["User"]] = relationship(
        "User",
        back_populates="acknowledged_alerts",
        foreign_keys=[acknowledged_by],
        lazy="selectin",
    )
    resolved_by_user: Mapped[Optional["User"]] = relationship(
        "User",
        back_populates="resolved_alerts",
        foreign_keys=[resolved_by],
        lazy="selectin",
    )
    escalations: Mapped[List["AlertEscalation"]] = relationship(
        "AlertEscalation", back_populates="alert", cascade="all, delete-orphan", lazy="selectin"
    )

    def __repr__(self) -> str:
        return f"<Alert(id={self.id}, type={self.alert_type.value}, status={self.status.value})>"


class AlertEscalation(Base):
    """Record of an alert being escalated to another user.

    Tracks who escalated, who received it, and the reason.
    """

    __tablename__ = "alert_escalations"

    alert_id: Mapped[uuid.UUID] = mapped_column(
        UUID(as_uuid=True), ForeignKey("alerts.id", ondelete="CASCADE"), nullable=False, index=True
    )
    escalated_to: Mapped[uuid.UUID] = mapped_column(
        UUID(as_uuid=True), ForeignKey("users.id", ondelete="CASCADE"), nullable=False, index=True
    )
    escalated_by: Mapped[uuid.UUID] = mapped_column(
        UUID(as_uuid=True), ForeignKey("users.id", ondelete="CASCADE"), nullable=False, index=True
    )
    reason: Mapped[Optional[str]] = mapped_column(Text, nullable=True)

    # ── Relationships ──────────────────────────────────────────────────
    alert: Mapped["Alert"] = relationship(
        "Alert", back_populates="escalations", lazy="selectin"
    )
    escalated_to_user: Mapped["User"] = relationship(
        "User",
        back_populates="escalations_received",
        foreign_keys=[escalated_to],
        lazy="selectin",
    )
    escalated_by_user: Mapped["User"] = relationship(
        "User",
        back_populates="escalations_sent",
        foreign_keys=[escalated_by],
        lazy="selectin",
    )

    def __repr__(self) -> str:
        return f"<AlertEscalation(id={self.id}, alert_id={self.alert_id})>"
