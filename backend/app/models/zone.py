"""Zone model for defining regions of interest within camera views."""

import enum
import uuid
from datetime import datetime
from typing import TYPE_CHECKING, List, Optional

from sqlalchemy import Boolean, DateTime, ForeignKey, String, func
from sqlalchemy import Enum as SAEnum
from sqlalchemy.dialects.postgresql import JSON, UUID
from sqlalchemy.orm import Mapped, mapped_column, relationship

from app.database import Base, TimestampMixin

if TYPE_CHECKING:
    from app.models.alert import Alert
    from app.models.analytics import DwellRecord, FootfallRecord
    from app.models.camera import Camera
    from app.models.face import FaceEvent
    from app.models.rule import Rule
    from app.models.vehicle import VehicleEvent


class ZoneType(str, enum.Enum):
    """Classification of zone purpose and behaviour."""

    RESTRICTED = "restricted"
    MONITORING = "monitoring"
    ENTRY_EXIT = "entry_exit"
    PARKING = "parking"
    PPE_REQUIRED = "ppe_required"
    SAFE = "safe"


class Zone(Base):
    """A polygonal region of interest drawn on a camera view.

    Zones define areas where specific detection rules apply, such as
    restricted areas, PPE-required zones, entry/exit boundaries, etc.
    The polygon is stored as a JSON array of [x, y] coordinate pairs.
    """

    __tablename__ = "zones"

    camera_id: Mapped[uuid.UUID] = mapped_column(
        UUID(as_uuid=True), ForeignKey("cameras.id", ondelete="CASCADE"), nullable=False, index=True
    )
    name: Mapped[str] = mapped_column(String(255), nullable=False)
    zone_type: Mapped[ZoneType] = mapped_column(
        String(32),
        nullable=False,
    )
    polygon_points: Mapped[list] = mapped_column(JSON, nullable=False)
    color_hex: Mapped[str] = mapped_column(
        String(9), nullable=False, default="#FF0000", server_default="#FF0000"
    )
    is_active: Mapped[bool] = mapped_column(
        Boolean, nullable=False, default=True, server_default="true"
    )

    # ── Relationships ──────────────────────────────────────────────────
    camera: Mapped["Camera"] = relationship(
        "Camera", back_populates="zones", lazy="selectin"
    )
    rules: Mapped[List["Rule"]] = relationship(
        "Rule", back_populates="zone", cascade="all, delete-orphan", lazy="selectin"
    )
    alerts: Mapped[List["Alert"]] = relationship(
        "Alert", back_populates="zone", lazy="selectin"
    )
    face_events: Mapped[List["FaceEvent"]] = relationship(
        "FaceEvent", back_populates="zone", lazy="selectin"
    )
    vehicle_events: Mapped[List["VehicleEvent"]] = relationship(
        "VehicleEvent", back_populates="zone", lazy="selectin"
    )
    footfall_records: Mapped[List["FootfallRecord"]] = relationship(
        "FootfallRecord", back_populates="zone", lazy="selectin"
    )
    dwell_records: Mapped[List["DwellRecord"]] = relationship(
        "DwellRecord", back_populates="zone", lazy="selectin"
    )

    def __repr__(self) -> str:
        return f"<Zone(id={self.id}, name='{self.name}', type={self.zone_type.value})>"
