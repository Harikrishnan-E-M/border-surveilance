"""Camera, camera group, and camera health models."""

import enum
import uuid
from datetime import datetime
from typing import TYPE_CHECKING, List, Optional

from sqlalchemy import (
    Boolean,
    Column,
    DateTime,
    Float,
    ForeignKey,
    Integer,
    String,
    Table,
    Text,
    func,
)
from sqlalchemy import Enum as SAEnum
from sqlalchemy import Uuid as UUID
from sqlalchemy.orm import Mapped, mapped_column, relationship

from app.database import Base, TimestampMixin

if TYPE_CHECKING:
    from app.models.alert import Alert
    from app.models.analytics import DwellRecord, FootfallRecord, HeatmapRecord
    from app.models.attendance import AttendanceLog
    from app.models.face import FaceEvent
    from app.models.organization import Organization
    from app.models.recording import Recording
    from app.models.rule import Rule
    from app.models.vehicle import VehicleEvent, VehicleLog
    from app.models.zone import Zone


class StreamProtocol(str, enum.Enum):
    """Supported camera stream protocols."""

    RTSP = "rtsp"
    ONVIF = "onvif"
    USB = "usb"
    FILE = "file"
    HTTP = "http"


class RecordingMode(str, enum.Enum):
    """Camera recording mode configuration."""

    CONTINUOUS = "continuous"
    EVENT = "event"
    NONE = "none"


class CameraHealthStatus(str, enum.Enum):
    """Real-time camera health status."""

    ONLINE = "online"
    OFFLINE = "offline"
    DEGRADED = "degraded"


# ── Association table for Camera <-> CameraGroup M2M ──────────────────
camera_group_members = Table(
    "camera_group_members",
    Base.metadata,
    Column(
        "camera_id",
        UUID(as_uuid=True),
        ForeignKey("cameras.id", ondelete="CASCADE"),
        primary_key=True,
    ),
    Column(
        "group_id",
        UUID(as_uuid=True),
        ForeignKey("camera_groups.id", ondelete="CASCADE"),
        primary_key=True,
    ),
)


class Camera(Base):
    """Represents a surveillance camera connected to the platform.

    Stream credentials are stored encrypted at rest. The camera model
    tracks connectivity status and geolocation for floor-plan mapping.
    """

    __tablename__ = "cameras"

    org_id: Mapped[uuid.UUID] = mapped_column(
        UUID(as_uuid=True), ForeignKey("organizations.id", ondelete="CASCADE"), nullable=False, index=True
    )
    name: Mapped[str] = mapped_column(String(255), nullable=False)
    location_description: Mapped[Optional[str]] = mapped_column(Text, nullable=True)
    stream_url: Mapped[str] = mapped_column(String(1024), nullable=False)
    protocol: Mapped[StreamProtocol] = mapped_column(
        String(32),
        nullable=False,
        default=StreamProtocol.RTSP,
    )
    username_encrypted: Mapped[Optional[str]] = mapped_column(String(512), nullable=True)
    password_encrypted: Mapped[Optional[str]] = mapped_column(String(512), nullable=True)
    resolution: Mapped[Optional[str]] = mapped_column(String(20), nullable=True)
    fps: Mapped[Optional[int]] = mapped_column(Integer, nullable=True)
    codec: Mapped[Optional[str]] = mapped_column(String(20), nullable=True)
    is_active: Mapped[bool] = mapped_column(
        Boolean, nullable=False, default=True, server_default="true"
    )
    is_online: Mapped[bool] = mapped_column(
        Boolean, nullable=False, default=False, server_default="false"
    )
    last_seen_at: Mapped[Optional[datetime]] = mapped_column(
        DateTime(timezone=True), nullable=True
    )
    latitude: Mapped[Optional[float]] = mapped_column(Float, nullable=True)
    longitude: Mapped[Optional[float]] = mapped_column(Float, nullable=True)
    floor_plan_x: Mapped[Optional[float]] = mapped_column(Float, nullable=True)
    floor_plan_y: Mapped[Optional[float]] = mapped_column(Float, nullable=True)
    recording_mode: Mapped[RecordingMode] = mapped_column(
        String(32),
        nullable=False,
        default=RecordingMode.EVENT,
        server_default=RecordingMode.EVENT.value,
    )

    # ── Relationships ──────────────────────────────────────────────────
    organization: Mapped["Organization"] = relationship(
        "Organization", back_populates="cameras", lazy="selectin"
    )
    zones: Mapped[List["Zone"]] = relationship(
        "Zone", back_populates="camera", cascade="all, delete-orphan", lazy="selectin"
    )
    rules: Mapped[List["Rule"]] = relationship(
        "Rule", back_populates="camera", cascade="all, delete-orphan", lazy="selectin"
    )
    alerts: Mapped[List["Alert"]] = relationship(
        "Alert", back_populates="camera", cascade="all, delete-orphan", lazy="selectin"
    )
    health_records: Mapped[List["CameraHealth"]] = relationship(
        "CameraHealth", back_populates="camera", cascade="all, delete-orphan", lazy="selectin"
    )
    groups: Mapped[List["CameraGroup"]] = relationship(
        "CameraGroup",
        secondary=camera_group_members,
        back_populates="cameras",
        lazy="selectin",
    )
    face_events: Mapped[List["FaceEvent"]] = relationship(
        "FaceEvent", back_populates="camera", cascade="all, delete-orphan", lazy="selectin"
    )
    vehicle_events: Mapped[List["VehicleEvent"]] = relationship(
        "VehicleEvent", back_populates="camera", cascade="all, delete-orphan", lazy="selectin"
    )
    attendance_logs: Mapped[List["AttendanceLog"]] = relationship(
        "AttendanceLog", back_populates="camera", cascade="all, delete-orphan", lazy="selectin"
    )
    footfall_records: Mapped[List["FootfallRecord"]] = relationship(
        "FootfallRecord", back_populates="camera", cascade="all, delete-orphan", lazy="selectin"
    )
    dwell_records: Mapped[List["DwellRecord"]] = relationship(
        "DwellRecord", back_populates="camera", cascade="all, delete-orphan", lazy="selectin"
    )
    heatmap_records: Mapped[List["HeatmapRecord"]] = relationship(
        "HeatmapRecord", back_populates="camera", cascade="all, delete-orphan", lazy="selectin"
    )
    recordings: Mapped[List["Recording"]] = relationship(
        "Recording", back_populates="camera", cascade="all, delete-orphan", lazy="selectin"
    )
    entry_vehicle_logs: Mapped[List["VehicleLog"]] = relationship(
        "VehicleLog",
        back_populates="entry_camera",
        foreign_keys="[VehicleLog.entry_camera_id]",
        lazy="selectin",
    )
    exit_vehicle_logs: Mapped[List["VehicleLog"]] = relationship(
        "VehicleLog",
        back_populates="exit_camera",
        foreign_keys="[VehicleLog.exit_camera_id]",
        lazy="selectin",
    )

    def __repr__(self) -> str:
        return f"<Camera(id={self.id}, name='{self.name}', protocol={self.protocol.value})>"


class CameraGroup(Base):
    """Logical grouping of cameras for bulk operations and viewing.

    Groups allow operators to monitor sets of cameras together
    (e.g. all cameras on Floor 2, all parking cameras).
    """

    __tablename__ = "camera_groups"

    org_id: Mapped[uuid.UUID] = mapped_column(
        UUID(as_uuid=True), ForeignKey("organizations.id", ondelete="CASCADE"), nullable=False, index=True
    )
    name: Mapped[str] = mapped_column(String(255), nullable=False)
    description: Mapped[Optional[str]] = mapped_column(Text, nullable=True)

    # ── Relationships ──────────────────────────────────────────────────
    cameras: Mapped[List["Camera"]] = relationship(
        "Camera",
        secondary=camera_group_members,
        back_populates="groups",
        lazy="selectin",
    )

    def __repr__(self) -> str:
        return f"<CameraGroup(id={self.id}, name='{self.name}')>"


class CameraHealth(Base):
    """Point-in-time health telemetry record for a camera.

    Collected periodically to track camera reliability, latency,
    and resource consumption on the edge/server side.
    """

    __tablename__ = "camera_health"

    camera_id: Mapped[uuid.UUID] = mapped_column(
        UUID(as_uuid=True), ForeignKey("cameras.id", ondelete="CASCADE"), nullable=False, index=True
    )
    timestamp: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), nullable=False, server_default=func.now()
    )
    status: Mapped[CameraHealthStatus] = mapped_column(
        String(32),
        nullable=False,
    )
    fps_actual: Mapped[Optional[float]] = mapped_column(Float, nullable=True)
    latency_ms: Mapped[Optional[float]] = mapped_column(Float, nullable=True)
    packet_loss_pct: Mapped[Optional[float]] = mapped_column(Float, nullable=True)
    cpu_usage: Mapped[Optional[float]] = mapped_column(Float, nullable=True)
    memory_usage: Mapped[Optional[float]] = mapped_column(Float, nullable=True)

    # ── Relationships ──────────────────────────────────────────────────
    camera: Mapped["Camera"] = relationship(
        "Camera", back_populates="health_records", lazy="selectin"
    )

    def __repr__(self) -> str:
        return f"<CameraHealth(id={self.id}, camera_id={self.camera_id}, status={self.status.value})>"
