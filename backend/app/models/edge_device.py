"""Edge device, deployment, and metrics models for edge/Jetson management.

Supports registration, monitoring, model deployment tracking, and
performance telemetry for NVIDIA Jetson and other edge compute devices
running VisionAI CV pipelines.
"""

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
from sqlalchemy.dialects.postgresql import ARRAY, JSONB, UUID
from sqlalchemy.orm import Mapped, mapped_column, relationship

from app.database import Base, TimestampMixin

if TYPE_CHECKING:
    pass


# ── Enums ──────────────────────────────────────────────────────────────


class EdgeDeviceType(str, enum.Enum):
    """Supported edge hardware platforms."""

    JETSON_ORIN_NANO = "jetson_orin_nano"
    JETSON_ORIN_NX = "jetson_orin_nx"
    JETSON_AGX_ORIN = "jetson_agx_orin"
    GENERIC_GPU = "generic_gpu"
    CPU_ONLY = "cpu_only"


class ModelFormat(str, enum.Enum):
    """Model file formats supported for edge deployment."""

    ONNX = "onnx"
    TENSORRT = "tensorrt"
    OPENVINO = "openvino"


class DeploymentStatus(str, enum.Enum):
    """Lifecycle states for a model deployment on an edge device."""

    DEPLOYING = "deploying"
    DEPLOYED = "deployed"
    FAILED = "failed"
    OUTDATED = "outdated"


# ── Edge Device Model ──────────────────────────────────────────────────


class EdgeDevice(Base):
    """Represents a physical edge compute device (Jetson, GPU server, etc.).

    Stores connection details, hardware specifications, assigned cameras,
    and real-time availability status.  The ``api_key`` field is stored
    encrypted at rest using Fernet symmetric encryption.
    """

    __tablename__ = "edge_devices"

    org_id: Mapped[uuid.UUID] = mapped_column(
        UUID(as_uuid=True),
        ForeignKey("organizations.id", ondelete="CASCADE"),
        nullable=False,
        index=True,
    )
    name: Mapped[str] = mapped_column(String(255), nullable=False)
    device_type: Mapped[EdgeDeviceType] = mapped_column(
        String(32),
        nullable=False,
        default=EdgeDeviceType.JETSON_ORIN_NANO,
    )
    ip_address: Mapped[str] = mapped_column(String(45), nullable=False)
    api_url: Mapped[str] = mapped_column(String(1024), nullable=False)
    api_key_encrypted: Mapped[Optional[str]] = mapped_column(
        String(1024), nullable=True
    )
    hardware_info: Mapped[Optional[dict]] = mapped_column(
        JSONB, nullable=True, default=dict
    )
    assigned_cameras: Mapped[Optional[list]] = mapped_column(
        ARRAY(UUID(as_uuid=True)), nullable=True, default=list
    )
    is_online: Mapped[bool] = mapped_column(
        Boolean, nullable=False, default=False, server_default="false"
    )
    last_heartbeat: Mapped[Optional[datetime]] = mapped_column(
        DateTime(timezone=True), nullable=True
    )
    firmware_version: Mapped[Optional[str]] = mapped_column(
        String(100), nullable=True
    )
    location: Mapped[Optional[str]] = mapped_column(Text, nullable=True)

    # ── Relationships ──────────────────────────────────────────────────
    deployments: Mapped[List["EdgeDeployment"]] = relationship(
        "EdgeDeployment",
        back_populates="device",
        cascade="all, delete-orphan",
        lazy="selectin",
    )
    metrics: Mapped[List["EdgeMetrics"]] = relationship(
        "EdgeMetrics",
        back_populates="device",
        cascade="all, delete-orphan",
        lazy="selectin",
        order_by="EdgeMetrics.timestamp.desc()",
    )

    def __repr__(self) -> str:
        return (
            f"<EdgeDevice(id={self.id}, name='{self.name}', "
            f"type={self.device_type.value}, online={self.is_online})>"
        )


# ── Edge Deployment Model ──────────────────────────────────────────────


class EdgeDeployment(Base):
    """Tracks a model deployment to an edge device.

    Records the model name, version, format, deployment status, timing,
    file size, and any error messages encountered during deployment.
    """

    __tablename__ = "edge_deployments"

    device_id: Mapped[uuid.UUID] = mapped_column(
        UUID(as_uuid=True),
        ForeignKey("edge_devices.id", ondelete="CASCADE"),
        nullable=False,
        index=True,
    )
    model_name: Mapped[str] = mapped_column(String(255), nullable=False)
    model_version: Mapped[str] = mapped_column(String(100), nullable=False)
    model_format: Mapped[ModelFormat] = mapped_column(
        String(32),
        nullable=False,
        default=ModelFormat.ONNX,
    )
    status: Mapped[DeploymentStatus] = mapped_column(
        String(32),
        nullable=False,
        default=DeploymentStatus.DEPLOYING,
    )
    file_size: Mapped[Optional[int]] = mapped_column(Integer, nullable=True)
    deploy_started_at: Mapped[Optional[datetime]] = mapped_column(
        DateTime(timezone=True), nullable=True
    )
    deploy_completed_at: Mapped[Optional[datetime]] = mapped_column(
        DateTime(timezone=True), nullable=True
    )
    error_message: Mapped[Optional[str]] = mapped_column(Text, nullable=True)

    # ── Relationships ──────────────────────────────────────────────────
    device: Mapped["EdgeDevice"] = relationship(
        "EdgeDevice", back_populates="deployments", lazy="selectin"
    )

    def __repr__(self) -> str:
        return (
            f"<EdgeDeployment(id={self.id}, device_id={self.device_id}, "
            f"model='{self.model_name}:{self.model_version}', "
            f"status={self.status.value})>"
        )


# ── Edge Metrics Model ─────────────────────────────────────────────────


class EdgeMetrics(Base):
    """Point-in-time performance telemetry for an edge device.

    Collected periodically (via heartbeat or polling) to track resource
    utilization, inference performance, and detection throughput.
    """

    __tablename__ = "edge_metricss"

    device_id: Mapped[uuid.UUID] = mapped_column(
        UUID(as_uuid=True),
        ForeignKey("edge_devices.id", ondelete="CASCADE"),
        nullable=False,
        index=True,
    )
    timestamp: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), nullable=False, server_default=func.now()
    )
    cpu_usage_pct: Mapped[Optional[float]] = mapped_column(Float, nullable=True)
    gpu_usage_pct: Mapped[Optional[float]] = mapped_column(Float, nullable=True)
    memory_usage_pct: Mapped[Optional[float]] = mapped_column(Float, nullable=True)
    temperature_celsius: Mapped[Optional[float]] = mapped_column(
        Float, nullable=True
    )
    disk_usage_pct: Mapped[Optional[float]] = mapped_column(Float, nullable=True)
    fps_processing: Mapped[Optional[float]] = mapped_column(Float, nullable=True)
    inference_latency_ms: Mapped[Optional[float]] = mapped_column(
        Float, nullable=True
    )
    detections_count: Mapped[Optional[int]] = mapped_column(Integer, nullable=True)
    uptime_seconds: Mapped[Optional[float]] = mapped_column(Float, nullable=True)

    # ── Relationships ──────────────────────────────────────────────────
    device: Mapped["EdgeDevice"] = relationship(
        "EdgeDevice", back_populates="metrics", lazy="selectin"
    )

    def __repr__(self) -> str:
        return (
            f"<EdgeMetrics(id={self.id}, device_id={self.device_id}, "
            f"timestamp={self.timestamp}, cpu={self.cpu_usage_pct}%, "
            f"gpu={self.gpu_usage_pct}%)>"
        )
