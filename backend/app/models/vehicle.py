"""Vehicle, vehicle event, and vehicle log models for ANPR."""

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
    UniqueConstraint,
    func,
)
from sqlalchemy import Enum as SAEnum
from sqlalchemy.dialects.postgresql import UUID
from sqlalchemy.orm import Mapped, mapped_column, relationship

from app.database import Base, TimestampMixin

if TYPE_CHECKING:
    from app.models.camera import Camera
    from app.models.organization import Organization
    from app.models.zone import Zone


class VehicleCategory(str, enum.Enum):
    """Access category for a registered vehicle."""

    WHITELIST = "whitelist"
    BLACKLIST = "blacklist"
    VISITOR = "visitor"
    EMPLOYEE = "employee"
    VIP = "vip"


class VehicleDirection(str, enum.Enum):
    """Direction of travel for a vehicle event."""

    ENTRY = "entry"
    EXIT = "exit"
    PASSING = "passing"


class Vehicle(Base):
    """A registered vehicle in the ANPR system.

    Vehicles are registered per organization with a plate number.
    The combination of org_id and plate_number must be unique.
    """

    __tablename__ = "vehicles"
    __table_args__ = (
        UniqueConstraint("org_id", "plate_number", name="uq_vehicle_org_plate"),
    )

    org_id: Mapped[uuid.UUID] = mapped_column(
        UUID(as_uuid=True), ForeignKey("organizations.id", ondelete="CASCADE"), nullable=False, index=True
    )
    plate_number: Mapped[str] = mapped_column(String(20), nullable=False, index=True)
    owner_name: Mapped[Optional[str]] = mapped_column(String(255), nullable=True)
    vehicle_type: Mapped[Optional[str]] = mapped_column(String(50), nullable=True)
    color: Mapped[Optional[str]] = mapped_column(String(50), nullable=True)
    make: Mapped[Optional[str]] = mapped_column(String(100), nullable=True)
    model_name: Mapped[Optional[str]] = mapped_column(String(100), nullable=True)
    category: Mapped[VehicleCategory] = mapped_column(
        String(32),
        nullable=False,
        default=VehicleCategory.VISITOR,
    )
    department: Mapped[Optional[str]] = mapped_column(String(255), nullable=True)
    phone: Mapped[Optional[str]] = mapped_column(String(20), nullable=True)
    notes: Mapped[Optional[str]] = mapped_column(Text, nullable=True)
    is_active: Mapped[bool] = mapped_column(
        Boolean, nullable=False, default=True, server_default="true"
    )

    # ── Relationships ──────────────────────────────────────────────────
    organization: Mapped["Organization"] = relationship(
        "Organization", back_populates="vehicles", lazy="selectin"
    )
    vehicle_events: Mapped[List["VehicleEvent"]] = relationship(
        "VehicleEvent", back_populates="vehicle", lazy="selectin"
    )
    vehicle_logs: Mapped[List["VehicleLog"]] = relationship(
        "VehicleLog", back_populates="vehicle", cascade="all, delete-orphan", lazy="selectin"
    )

    def __repr__(self) -> str:
        return f"<Vehicle(id={self.id}, plate='{self.plate_number}', category={self.category.value})>"


class VehicleEvent(Base):
    """A single ANPR detection event from a camera feed.

    Records the plate number detected, confidence, matched vehicle
    (if registered), direction of travel, and snapshot images.
    """

    __tablename__ = "vehicle_events"

    camera_id: Mapped[uuid.UUID] = mapped_column(
        UUID(as_uuid=True), ForeignKey("cameras.id", ondelete="CASCADE"), nullable=False, index=True
    )
    zone_id: Mapped[Optional[uuid.UUID]] = mapped_column(
        UUID(as_uuid=True), ForeignKey("zones.id", ondelete="SET NULL"), nullable=True, index=True
    )
    plate_number: Mapped[str] = mapped_column(String(20), nullable=False, index=True)
    plate_confidence: Mapped[Optional[float]] = mapped_column(Float, nullable=True)
    vehicle_id: Mapped[Optional[uuid.UUID]] = mapped_column(
        UUID(as_uuid=True), ForeignKey("vehicles.id", ondelete="SET NULL"), nullable=True, index=True
    )
    direction: Mapped[Optional[VehicleDirection]] = mapped_column(
        String(32),
        nullable=True,
    )
    plate_snapshot_path: Mapped[Optional[str]] = mapped_column(String(1024), nullable=True)
    vehicle_snapshot_path: Mapped[Optional[str]] = mapped_column(String(1024), nullable=True)
    timestamp: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), nullable=False, index=True
    )

    # ── Relationships ──────────────────────────────────────────────────
    camera: Mapped["Camera"] = relationship(
        "Camera", back_populates="vehicle_events", lazy="selectin"
    )
    zone: Mapped[Optional["Zone"]] = relationship(
        "Zone", back_populates="vehicle_events", lazy="selectin"
    )
    vehicle: Mapped[Optional["Vehicle"]] = relationship(
        "Vehicle", back_populates="vehicle_events", lazy="selectin"
    )

    def __repr__(self) -> str:
        return f"<VehicleEvent(id={self.id}, plate='{self.plate_number}', direction={self.direction})>"


class VehicleLog(Base):
    """Entry/exit log pairing for a registered vehicle.

    Correlates entry and exit events to compute parking duration.
    The exit fields remain NULL until the vehicle exits.
    """

    __tablename__ = "vehicle_logs"

    vehicle_id: Mapped[uuid.UUID] = mapped_column(
        UUID(as_uuid=True), ForeignKey("vehicles.id", ondelete="CASCADE"), nullable=False, index=True
    )
    entry_camera_id: Mapped[uuid.UUID] = mapped_column(
        UUID(as_uuid=True), ForeignKey("cameras.id", ondelete="CASCADE"), nullable=False, index=True
    )
    entry_time: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), nullable=False
    )
    exit_camera_id: Mapped[Optional[uuid.UUID]] = mapped_column(
        UUID(as_uuid=True), ForeignKey("cameras.id", ondelete="SET NULL"), nullable=True
    )
    exit_time: Mapped[Optional[datetime]] = mapped_column(
        DateTime(timezone=True), nullable=True
    )
    duration_seconds: Mapped[Optional[int]] = mapped_column(Integer, nullable=True)

    # ── Relationships ──────────────────────────────────────────────────
    vehicle: Mapped["Vehicle"] = relationship(
        "Vehicle", back_populates="vehicle_logs", lazy="selectin"
    )
    entry_camera: Mapped["Camera"] = relationship(
        "Camera",
        back_populates="entry_vehicle_logs",
        foreign_keys=[entry_camera_id],
        lazy="selectin",
    )
    exit_camera: Mapped[Optional["Camera"]] = relationship(
        "Camera",
        back_populates="exit_vehicle_logs",
        foreign_keys=[exit_camera_id],
        lazy="selectin",
    )

    def __repr__(self) -> str:
        return f"<VehicleLog(id={self.id}, vehicle_id={self.vehicle_id}, entry={self.entry_time})>"
