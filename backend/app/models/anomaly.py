"""Anomaly detection models: events and baselines for unsupervised anomaly detection."""

import enum
import uuid
from datetime import datetime
from typing import TYPE_CHECKING, Optional

from sqlalchemy import Boolean, DateTime, Float, ForeignKey, Integer, String, Text, func
from sqlalchemy import Enum as SAEnum
from sqlalchemy.dialects.postgresql import JSON, UUID
from sqlalchemy.orm import Mapped, mapped_column, relationship

from app.database import Base, TimestampMixin

if TYPE_CHECKING:
    from app.models.camera import Camera
    from app.models.organization import Organization
    from app.models.user import User
    from app.models.zone import Zone


class AnomalyType(str, enum.Enum):
    """Classification of detected anomaly types."""

    COUNT = "count"
    TEMPORAL = "temporal"
    SPATIAL = "spatial"
    BEHAVIORAL = "behavioral"
    FREQUENCY = "frequency"


class AnomalySeverity(str, enum.Enum):
    """Severity level of an anomaly event."""

    INFO = "info"
    WARNING = "warning"
    CRITICAL = "critical"


class AnomalyEvent(Base):
    """A detected anomaly event from the unsupervised detection engine.

    Captures the full context of an anomaly: the type of deviation,
    severity, confidence, statistical details (baseline vs. observed),
    and lifecycle status (unacknowledged -> acknowledged).
    """

    __tablename__ = "anomaly_events"

    org_id: Mapped[uuid.UUID] = mapped_column(
        UUID(as_uuid=True), ForeignKey("organizations.id", ondelete="CASCADE"), nullable=False, index=True
    )
    camera_id: Mapped[uuid.UUID] = mapped_column(
        UUID(as_uuid=True), ForeignKey("cameras.id", ondelete="CASCADE"), nullable=False, index=True
    )
    zone_id: Mapped[Optional[uuid.UUID]] = mapped_column(
        UUID(as_uuid=True), ForeignKey("zones.id", ondelete="SET NULL"), nullable=True, index=True
    )
    anomaly_type: Mapped[AnomalyType] = mapped_column(
        String(32),
        nullable=False,
        index=True,
    )
    severity: Mapped[AnomalySeverity] = mapped_column(
        String(32),
        nullable=False,
        index=True,
    )
    confidence: Mapped[float] = mapped_column(Float, nullable=False, default=0.0)
    description: Mapped[str] = mapped_column(Text, nullable=False)
    baseline_value: Mapped[Optional[float]] = mapped_column(Float, nullable=True)
    observed_value: Mapped[Optional[float]] = mapped_column(Float, nullable=True)
    deviation_sigma: Mapped[Optional[float]] = mapped_column(Float, nullable=True)
    metadata_json: Mapped[Optional[dict]] = mapped_column(JSON, nullable=True)
    thumbnail_path: Mapped[Optional[str]] = mapped_column(String(1024), nullable=True)
    is_acknowledged: Mapped[bool] = mapped_column(
        Boolean, nullable=False, default=False, server_default="false", index=True
    )
    acknowledged_by: Mapped[Optional[uuid.UUID]] = mapped_column(
        UUID(as_uuid=True), ForeignKey("users.id", ondelete="SET NULL"), nullable=True
    )
    acknowledged_at: Mapped[Optional[datetime]] = mapped_column(
        DateTime(timezone=True), nullable=True
    )

    # -- Relationships -------------------------------------------------------
    organization: Mapped["Organization"] = relationship(
        "Organization", lazy="selectin"
    )
    camera: Mapped["Camera"] = relationship(
        "Camera", lazy="selectin"
    )
    zone: Mapped[Optional["Zone"]] = relationship(
        "Zone", lazy="selectin"
    )
    acknowledged_by_user: Mapped[Optional["User"]] = relationship(
        "User",
        foreign_keys=[acknowledged_by],
        lazy="selectin",
    )

    def __repr__(self) -> str:
        return (
            f"<AnomalyEvent(id={self.id}, type={self.anomaly_type.value}, "
            f"severity={self.severity.value}, confidence={self.confidence:.2f})>"
        )


class AnomalyBaseline(Base):
    """Stored statistical baseline for a camera/zone combination.

    Contains hourly and day-of-week profiles computed from historical
    analytics data. Used by the anomaly detection engine to determine
    whether current observations deviate significantly from normal.
    """

    __tablename__ = "anomaly_baselines"

    org_id: Mapped[uuid.UUID] = mapped_column(
        UUID(as_uuid=True), ForeignKey("organizations.id", ondelete="CASCADE"), nullable=False, index=True
    )
    camera_id: Mapped[uuid.UUID] = mapped_column(
        UUID(as_uuid=True), ForeignKey("cameras.id", ondelete="CASCADE"), nullable=False, index=True
    )
    zone_id: Mapped[Optional[uuid.UUID]] = mapped_column(
        UUID(as_uuid=True), ForeignKey("zones.id", ondelete="SET NULL"), nullable=True, index=True
    )
    baseline_type: Mapped[str] = mapped_column(
        String(64), nullable=False, default="footfall", server_default="footfall"
    )
    hourly_profile: Mapped[Optional[dict]] = mapped_column(
        JSON, nullable=True, comment="24-element array of hourly means and std devs"
    )
    day_of_week_profile: Mapped[Optional[dict]] = mapped_column(
        JSON, nullable=True, comment="7-element array of day-of-week scaling factors"
    )
    sample_count: Mapped[int] = mapped_column(
        Integer, nullable=False, default=0, server_default="0"
    )
    is_stale: Mapped[bool] = mapped_column(
        Boolean, nullable=False, default=True, server_default="true"
    )

    # -- Relationships -------------------------------------------------------
    organization: Mapped["Organization"] = relationship(
        "Organization", lazy="selectin"
    )
    camera: Mapped["Camera"] = relationship(
        "Camera", lazy="selectin"
    )
    zone: Mapped[Optional["Zone"]] = relationship(
        "Zone", lazy="selectin"
    )

    def __repr__(self) -> str:
        return (
            f"<AnomalyBaseline(id={self.id}, camera_id={self.camera_id}, "
            f"type={self.baseline_type}, samples={self.sample_count})>"
        )
