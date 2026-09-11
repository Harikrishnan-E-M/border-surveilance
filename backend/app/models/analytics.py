"""Analytics models: footfall counting, dwell time, and heatmaps."""

import enum
import uuid
from datetime import datetime
from typing import TYPE_CHECKING, Optional

from sqlalchemy import DateTime, Float, ForeignKey, Integer, String, func
from sqlalchemy import Enum as SAEnum
from sqlalchemy.dialects.postgresql import UUID
from sqlalchemy.orm import Mapped, mapped_column, relationship

from app.database import Base, TimestampMixin

if TYPE_CHECKING:
    from app.models.camera import Camera
    from app.models.zone import Zone


class AggregationPeriod(str, enum.Enum):
    """Time period over which analytics are aggregated."""

    HOURLY = "hourly"
    DAILY = "daily"
    WEEKLY = "weekly"


class FootfallRecord(Base):
    """Aggregated footfall (people counting) data for a camera/zone.

    Records entry/exit counts and estimated occupancy over a defined
    time period. Used for occupancy dashboards and trend analysis.
    """

    __tablename__ = "footfall_records"

    camera_id: Mapped[uuid.UUID] = mapped_column(
        UUID(as_uuid=True), ForeignKey("cameras.id", ondelete="CASCADE"), nullable=False, index=True
    )
    zone_id: Mapped[uuid.UUID] = mapped_column(
        UUID(as_uuid=True), ForeignKey("zones.id", ondelete="CASCADE"), nullable=False, index=True
    )
    timestamp: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), nullable=False, index=True
    )
    period: Mapped[AggregationPeriod] = mapped_column(
        String(32),
        nullable=False,
    )
    entries_count: Mapped[int] = mapped_column(Integer, nullable=False, default=0, server_default="0")
    exits_count: Mapped[int] = mapped_column(Integer, nullable=False, default=0, server_default="0")
    occupancy_estimate: Mapped[Optional[int]] = mapped_column(Integer, nullable=True)

    # ── Relationships ──────────────────────────────────────────────────
    camera: Mapped["Camera"] = relationship(
        "Camera", back_populates="footfall_records", lazy="selectin"
    )
    zone: Mapped["Zone"] = relationship(
        "Zone", back_populates="footfall_records", lazy="selectin"
    )

    def __repr__(self) -> str:
        return (
            f"<FootfallRecord(id={self.id}, camera_id={self.camera_id}, "
            f"period={self.period.value}, entries={self.entries_count})>"
        )


class DwellRecord(Base):
    """Individual dwell time record for a tracked object in a zone.

    Captures how long a specific tracked entity (identified by track_id)
    remained within a zone boundary. Used for dwell-time analytics.
    """

    __tablename__ = "dwell_records"

    camera_id: Mapped[uuid.UUID] = mapped_column(
        UUID(as_uuid=True), ForeignKey("cameras.id", ondelete="CASCADE"), nullable=False, index=True
    )
    zone_id: Mapped[uuid.UUID] = mapped_column(
        UUID(as_uuid=True), ForeignKey("zones.id", ondelete="CASCADE"), nullable=False, index=True
    )
    track_id: Mapped[str] = mapped_column(String(128), nullable=False, index=True)
    enter_time: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), nullable=False
    )
    exit_time: Mapped[Optional[datetime]] = mapped_column(
        DateTime(timezone=True), nullable=True
    )
    dwell_seconds: Mapped[Optional[float]] = mapped_column(Float, nullable=True)

    # ── Relationships ──────────────────────────────────────────────────
    camera: Mapped["Camera"] = relationship(
        "Camera", back_populates="dwell_records", lazy="selectin"
    )
    zone: Mapped["Zone"] = relationship(
        "Zone", back_populates="dwell_records", lazy="selectin"
    )

    def __repr__(self) -> str:
        return f"<DwellRecord(id={self.id}, track_id='{self.track_id}', dwell={self.dwell_seconds}s)>"


class HeatmapRecord(Base):
    """Generated heatmap image for a camera over a time window.

    Stores a rendered heatmap image showing movement density
    aggregated over the given time range.
    """

    __tablename__ = "heatmap_records"

    camera_id: Mapped[uuid.UUID] = mapped_column(
        UUID(as_uuid=True), ForeignKey("cameras.id", ondelete="CASCADE"), nullable=False, index=True
    )
    start_time: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), nullable=False
    )
    end_time: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), nullable=False
    )
    image_path: Mapped[str] = mapped_column(String(1024), nullable=False)
    resolution: Mapped[Optional[str]] = mapped_column(String(20), nullable=True)

    # ── Relationships ──────────────────────────────────────────────────
    camera: Mapped["Camera"] = relationship(
        "Camera", back_populates="heatmap_records", lazy="selectin"
    )

    def __repr__(self) -> str:
        return f"<HeatmapRecord(id={self.id}, camera_id={self.camera_id}, start={self.start_time})>"
