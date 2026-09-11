"""Rule model for defining detection and alerting rules."""

import enum
import uuid
from datetime import datetime
from typing import TYPE_CHECKING, List, Optional

from sqlalchemy import Boolean, DateTime, ForeignKey, Integer, String, Text, func
from sqlalchemy import Enum as SAEnum
from sqlalchemy.dialects.postgresql import JSON, UUID
from sqlalchemy.orm import Mapped, mapped_column, relationship

from app.database import Base, TimestampMixin

if TYPE_CHECKING:
    from app.models.alert import Alert
    from app.models.camera import Camera
    from app.models.organization import Organization
    from app.models.zone import Zone


class RuleType(str, enum.Enum):
    """All supported detection / analytics rule types.

    Each rule type maps to a specific CV pipeline configuration.
    """

    INTRUSION_DETECTION = "intrusion_detection"
    LOITERING = "loitering"
    CROWD_FORMATION = "crowd_formation"
    OBJECT_LEFT_BEHIND = "object_left_behind"
    OBJECT_REMOVED = "object_removed"
    WRONG_DIRECTION = "wrong_direction"
    LINE_CROSSING = "line_crossing"
    FACE_RECOGNIZED = "face_recognized"
    FACE_UNKNOWN = "face_unknown"
    FACE_BLACKLISTED = "face_blacklisted"
    ANPR_BLACKLISTED = "anpr_blacklisted"
    ANPR_UNKNOWN = "anpr_unknown"
    PPE_VIOLATION = "ppe_violation"
    FIRE_SMOKE = "fire_smoke"
    FALL_DETECTION = "fall_detection"
    VIOLENCE_DETECTION = "violence_detection"
    OCCUPANCY_THRESHOLD = "occupancy_threshold"
    CAMERA_TAMPER = "camera_tamper"
    NO_ENTRY_ZONE = "no_entry_zone"
    TAILGATING = "tailgating"
    SPEED_VIOLATION = "speed_violation"
    ILLEGAL_PARKING = "illegal_parking"


class RuleSeverity(str, enum.Enum):
    """Alert severity level assigned when a rule triggers."""

    CRITICAL = "critical"
    HIGH = "high"
    MEDIUM = "medium"
    LOW = "low"
    INFO = "info"


class Rule(Base):
    """Defines a detection rule bound to a camera and optionally a zone.

    Rules specify what type of event to detect, the parameters for
    the detection algorithm, severity level, and which channels to
    alert on. They can be scheduled via cron and have cooldown periods
    to prevent alert flooding.
    """

    __tablename__ = "rules"

    zone_id: Mapped[Optional[uuid.UUID]] = mapped_column(
        UUID(as_uuid=True), ForeignKey("zones.id", ondelete="SET NULL"), nullable=True, index=True
    )
    camera_id: Mapped[uuid.UUID] = mapped_column(
        UUID(as_uuid=True), ForeignKey("cameras.id", ondelete="CASCADE"), nullable=False, index=True
    )
    org_id: Mapped[uuid.UUID] = mapped_column(
        UUID(as_uuid=True), ForeignKey("organizations.id", ondelete="CASCADE"), nullable=False, index=True
    )
    rule_type: Mapped[RuleType] = mapped_column(
        String(32),
        nullable=False,
    )
    parameters: Mapped[Optional[dict]] = mapped_column(JSON, nullable=True)
    severity: Mapped[RuleSeverity] = mapped_column(
        String(32),
        nullable=False,
        default=RuleSeverity.MEDIUM,
    )
    alert_channels: Mapped[Optional[dict]] = mapped_column(JSON, nullable=True)
    is_active: Mapped[bool] = mapped_column(
        Boolean, nullable=False, default=True, server_default="true"
    )
    cooldown_seconds: Mapped[int] = mapped_column(
        Integer, nullable=False, default=300, server_default="300"
    )
    schedule_cron: Mapped[Optional[str]] = mapped_column(String(128), nullable=True)

    # ── Relationships ──────────────────────────────────────────────────
    zone: Mapped[Optional["Zone"]] = relationship(
        "Zone", back_populates="rules", lazy="selectin"
    )
    camera: Mapped["Camera"] = relationship(
        "Camera", back_populates="rules", lazy="selectin"
    )
    organization: Mapped["Organization"] = relationship(
        "Organization", back_populates="rules", lazy="selectin"
    )
    alerts: Mapped[List["Alert"]] = relationship(
        "Alert", back_populates="rule", cascade="all, delete-orphan", lazy="selectin"
    )

    def __repr__(self) -> str:
        return f"<Rule(id={self.id}, type={self.rule_type.value}, severity={self.severity.value})>"
