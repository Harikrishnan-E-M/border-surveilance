"""Pydantic schemas for edge device management, deployment, metrics, and analytics.

Covers device CRUD, model deployment, health monitoring, pipeline
configuration, result synchronization, and aggregate analytics.
"""

from __future__ import annotations


import enum
from datetime import datetime
from typing import Any
from uuid import UUID

from pydantic import BaseModel, ConfigDict, Field


# ── Enums ─────────────────────────────────────────────────────────────────────


class EdgeDeviceTypeEnum(str, enum.Enum):
    """Supported edge hardware platforms."""

    JETSON_ORIN_NANO = "jetson_orin_nano"
    JETSON_ORIN_NX = "jetson_orin_nx"
    JETSON_AGX_ORIN = "jetson_agx_orin"
    GENERIC_GPU = "generic_gpu"
    CPU_ONLY = "cpu_only"


class ModelFormatEnum(str, enum.Enum):
    """Model file formats supported for edge deployment."""

    ONNX = "onnx"
    TENSORRT = "tensorrt"
    OPENVINO = "openvino"


class DeploymentStatusEnum(str, enum.Enum):
    """Lifecycle states for a model deployment."""

    DEPLOYING = "deploying"
    DEPLOYED = "deployed"
    FAILED = "failed"
    OUTDATED = "outdated"


# ── Edge Device CRUD ──────────────────────────────────────────────────────────


class EdgeDeviceCreate(BaseModel):
    """Request body for registering a new edge device."""

    name: str = Field(
        min_length=1,
        max_length=255,
        description="Human-readable device name.",
        examples=["Warehouse Jetson 01"],
    )
    device_type: EdgeDeviceTypeEnum = Field(
        description="Hardware platform type.",
        examples=["jetson_orin_nano"],
    )
    ip_address: str = Field(
        min_length=1,
        max_length=45,
        description="Device IP address (IPv4 or IPv6).",
        examples=["192.168.1.50"],
    )
    api_url: str = Field(
        min_length=1,
        max_length=1024,
        description="Full URL of the edge device API server.",
        examples=["http://192.168.1.50:8080"],
    )
    api_key: str | None = Field(
        default=None,
        max_length=512,
        description="API key for authenticating with the edge device.",
    )
    hardware_info: dict[str, Any] | None = Field(
        default=None,
        description="Hardware specifications (gpu_model, cpu_cores, ram_gb, storage_gb).",
        examples=[{
            "gpu_model": "NVIDIA Orin Nano 8GB",
            "cpu_cores": 6,
            "ram_gb": 8,
            "storage_gb": 256,
        }],
    )
    assigned_cameras: list[UUID] | None = Field(
        default=None,
        description="List of camera IDs assigned to this edge device.",
    )
    firmware_version: str | None = Field(
        default=None,
        max_length=100,
        description="Device firmware or JetPack version.",
        examples=["JetPack 6.0"],
    )
    location: str | None = Field(
        default=None,
        max_length=512,
        description="Physical location of the device.",
        examples=["Building A, Server Room 2"],
    )


class EdgeDeviceUpdate(BaseModel):
    """Partial update for an existing edge device."""

    name: str | None = Field(
        default=None,
        min_length=1,
        max_length=255,
        description="Updated device name.",
    )
    device_type: EdgeDeviceTypeEnum | None = Field(
        default=None,
        description="Updated hardware type.",
    )
    ip_address: str | None = Field(
        default=None,
        min_length=1,
        max_length=45,
        description="Updated IP address.",
    )
    api_url: str | None = Field(
        default=None,
        min_length=1,
        max_length=1024,
        description="Updated API URL.",
    )
    api_key: str | None = Field(
        default=None,
        max_length=512,
        description="Updated API key.",
    )
    hardware_info: dict[str, Any] | None = Field(
        default=None,
        description="Updated hardware specs.",
    )
    assigned_cameras: list[UUID] | None = Field(
        default=None,
        description="Updated camera assignment list.",
    )
    firmware_version: str | None = Field(
        default=None,
        max_length=100,
        description="Updated firmware version.",
    )
    location: str | None = Field(
        default=None,
        max_length=512,
        description="Updated physical location.",
    )


class EdgeDeviceResponse(BaseModel):
    """Full edge device representation returned by read endpoints."""

    model_config = ConfigDict(from_attributes=True)

    id: UUID = Field(description="Device unique identifier.")
    org_id: UUID = Field(description="Owning organization ID.")
    name: str = Field(description="Device name.")
    device_type: str = Field(description="Hardware platform type.")
    ip_address: str = Field(description="Device IP address.")
    api_url: str = Field(description="Edge device API URL.")
    hardware_info: dict[str, Any] | None = Field(
        default=None, description="Hardware specifications."
    )
    assigned_cameras: list[UUID] | None = Field(
        default=None, description="Assigned camera IDs."
    )
    is_online: bool = Field(description="Whether the device is currently online.")
    last_heartbeat: datetime | None = Field(
        default=None, description="Last heartbeat timestamp."
    )
    firmware_version: str | None = Field(
        default=None, description="Firmware version."
    )
    location: str | None = Field(default=None, description="Physical location.")
    created_at: datetime = Field(description="Device registration timestamp.")
    updated_at: datetime | None = Field(
        default=None, description="Last update timestamp."
    )


class EdgeDeviceListResponse(BaseModel):
    """Paginated list of edge devices."""

    status: str = Field(default="success")
    data: list[EdgeDeviceResponse] = Field(
        default_factory=list, description="List of edge devices."
    )
    total: int = Field(description="Total number of devices.")


# ── Deployment Schemas ────────────────────────────────────────────────────────


class DeployModelRequest(BaseModel):
    """Request body for deploying a model to an edge device."""

    model_name: str = Field(
        min_length=1,
        max_length=255,
        description="Name of the model to deploy.",
        examples=["yolov8n-coco"],
    )
    model_version: str = Field(
        min_length=1,
        max_length=100,
        description="Model version tag.",
        examples=["v1.2.0"],
    )
    model_format: ModelFormatEnum = Field(
        default=ModelFormatEnum.ONNX,
        description="Model file format.",
        examples=["onnx"],
    )


class EdgeDeploymentResponse(BaseModel):
    """Representation of a model deployment on an edge device."""

    model_config = ConfigDict(from_attributes=True)

    id: UUID = Field(description="Deployment unique identifier.")
    device_id: UUID = Field(description="Target edge device ID.")
    model_name: str = Field(description="Model name.")
    model_version: str = Field(description="Model version.")
    model_format: str = Field(description="Model file format.")
    status: str = Field(description="Deployment status.")
    file_size: int | None = Field(default=None, description="Model file size in bytes.")
    deploy_started_at: datetime | None = Field(
        default=None, description="Deployment start time."
    )
    deploy_completed_at: datetime | None = Field(
        default=None, description="Deployment completion time."
    )
    error_message: str | None = Field(
        default=None, description="Error message if deployment failed."
    )
    created_at: datetime = Field(description="Record creation timestamp.")
    updated_at: datetime | None = Field(default=None, description="Last update.")


# ── Metrics Schemas ───────────────────────────────────────────────────────────


class EdgeMetricsResponse(BaseModel):
    """Single metrics data point from an edge device."""

    model_config = ConfigDict(from_attributes=True)

    id: UUID = Field(description="Metrics record identifier.")
    device_id: UUID = Field(description="Edge device ID.")
    timestamp: datetime = Field(description="Timestamp of the measurement.")
    cpu_usage_pct: float | None = Field(default=None, description="CPU usage %.")
    gpu_usage_pct: float | None = Field(default=None, description="GPU usage %.")
    memory_usage_pct: float | None = Field(
        default=None, description="Memory usage %."
    )
    temperature_celsius: float | None = Field(
        default=None, description="GPU/SoC temperature in Celsius."
    )
    disk_usage_pct: float | None = Field(default=None, description="Disk usage %.")
    fps_processing: float | None = Field(
        default=None, description="Current processing FPS."
    )
    inference_latency_ms: float | None = Field(
        default=None, description="Inference latency in milliseconds."
    )
    detections_count: int | None = Field(
        default=None, description="Number of detections in this period."
    )
    uptime_seconds: float | None = Field(
        default=None, description="Device uptime in seconds."
    )


class MetricsHistory(BaseModel):
    """Historical metrics for an edge device over a time range."""

    device_id: UUID = Field(description="Edge device ID.")
    device_name: str = Field(description="Edge device name.")
    time_range: str = Field(description="Requested time range.", examples=["1h"])
    data_points: list[EdgeMetricsResponse] = Field(
        default_factory=list, description="Chronological list of metrics."
    )


# ── Health Schemas ────────────────────────────────────────────────────────────


class EdgeHealthResponse(BaseModel):
    """Real-time health snapshot for an edge device."""

    device_id: UUID = Field(description="Edge device ID.")
    device_name: str = Field(description="Device display name.")
    is_online: bool = Field(description="Whether the device is reachable.")
    cpu_usage_pct: float | None = Field(default=None, description="Current CPU %.")
    gpu_usage_pct: float | None = Field(default=None, description="Current GPU %.")
    memory_usage_pct: float | None = Field(
        default=None, description="Current memory %."
    )
    temperature_celsius: float | None = Field(
        default=None, description="Current temperature."
    )
    disk_usage_pct: float | None = Field(default=None, description="Current disk %.")
    uptime_seconds: float | None = Field(
        default=None, description="Device uptime in seconds."
    )
    fps_processing: float | None = Field(
        default=None, description="Current processing FPS."
    )
    inference_latency_ms: float | None = Field(
        default=None, description="Current inference latency."
    )
    last_heartbeat: datetime | None = Field(
        default=None, description="Last heartbeat timestamp."
    )
    cpu_status: str = Field(
        default="unknown",
        description="CPU health indicator: normal, warning, critical.",
    )
    gpu_status: str = Field(
        default="unknown",
        description="GPU health indicator: normal, warning, critical.",
    )
    memory_status: str = Field(
        default="unknown",
        description="Memory health indicator: normal, warning, critical.",
    )
    temperature_status: str = Field(
        default="unknown",
        description="Temperature health indicator: normal, warning, critical.",
    )
    disk_status: str = Field(
        default="unknown",
        description="Disk health indicator: normal, warning, critical.",
    )


# ── Pipeline Config Schemas ───────────────────────────────────────────────────


class PipelineConfigUpdate(BaseModel):
    """Request body for updating the CV pipeline configuration on an edge device."""

    detection_model: str | None = Field(
        default=None,
        description="Detection model name to use.",
        examples=["yolov8n"],
    )
    detection_confidence: float | None = Field(
        default=None,
        ge=0.0,
        le=1.0,
        description="Minimum detection confidence threshold.",
        examples=[0.5],
    )
    tracking_enabled: bool | None = Field(
        default=None,
        description="Enable or disable object tracking.",
    )
    tracking_algorithm: str | None = Field(
        default=None,
        description="Tracking algorithm to use.",
        examples=["bytetrack"],
    )
    max_fps: int | None = Field(
        default=None,
        ge=1,
        le=120,
        description="Maximum processing FPS.",
        examples=[30],
    )
    resize_width: int | None = Field(
        default=None,
        ge=160,
        le=3840,
        description="Input resize width for inference.",
        examples=[640],
    )
    resize_height: int | None = Field(
        default=None,
        ge=120,
        le=2160,
        description="Input resize height for inference.",
        examples=[480],
    )
    classes_of_interest: list[str] | None = Field(
        default=None,
        description="Object classes to detect.",
        examples=[["person", "vehicle", "bag"]],
    )
    enable_tensorrt: bool | None = Field(
        default=None,
        description="Use TensorRT optimized engine.",
    )
    batch_size: int | None = Field(
        default=None,
        ge=1,
        le=32,
        description="Inference batch size.",
        examples=[1],
    )
    extra_config: dict[str, Any] | None = Field(
        default=None,
        description="Additional pipeline-specific configuration.",
    )


# ── Sync Results Schemas ──────────────────────────────────────────────────────


class DetectionResult(BaseModel):
    """Single detection result from the edge CV pipeline."""

    camera_id: UUID = Field(description="Camera that produced the detection.")
    timestamp: datetime = Field(description="Detection timestamp.")
    class_name: str = Field(description="Detected object class.", examples=["person"])
    confidence: float = Field(
        ge=0.0, le=1.0, description="Detection confidence.", examples=[0.92]
    )
    bbox: list[float] = Field(
        description="Bounding box [x1, y1, x2, y2] in normalized coords.",
        examples=[[0.1, 0.2, 0.5, 0.8]],
    )
    track_id: int | None = Field(
        default=None, description="Object tracker ID.", examples=[42]
    )
    metadata: dict[str, Any] | None = Field(
        default=None,
        description="Additional detection metadata (e.g., license plate, face embedding).",
    )


class SyncResultsRequest(BaseModel):
    """Batch of detection results synced from an edge device to the cloud."""

    results: list[DetectionResult] = Field(
        min_length=1,
        description="List of detection results to sync.",
    )
    batch_id: str | None = Field(
        default=None,
        description="Optional batch identifier for deduplication.",
    )
    device_timestamp: datetime | None = Field(
        default=None,
        description="Edge device local timestamp when the batch was created.",
    )


class SyncResultsResponse(BaseModel):
    """Response after processing synced detection results."""

    status: str = Field(default="success")
    accepted_count: int = Field(description="Number of results accepted.")
    rejected_count: int = Field(
        default=0, description="Number of results rejected."
    )
    message: str | None = Field(
        default=None, description="Optional status message."
    )


# ── Analytics Schemas ─────────────────────────────────────────────────────────


class EdgeAnalyticsResponse(BaseModel):
    """Aggregate edge deployment statistics for an organization."""

    total_devices: int = Field(
        default=0, description="Total number of registered edge devices."
    )
    online_devices: int = Field(
        default=0, description="Number of currently online devices."
    )
    offline_devices: int = Field(
        default=0, description="Number of currently offline devices."
    )
    total_fps: float = Field(
        default=0.0,
        description="Sum of processing FPS across all online devices.",
    )
    avg_inference_latency_ms: float = Field(
        default=0.0,
        description="Average inference latency across all online devices.",
    )
    total_detections: int = Field(
        default=0,
        description="Total detections processed across all devices (last hour).",
    )
    total_deployed_models: int = Field(
        default=0, description="Total number of active model deployments."
    )
    avg_gpu_usage_pct: float = Field(
        default=0.0,
        description="Average GPU utilization across all online devices.",
    )
    avg_cpu_usage_pct: float = Field(
        default=0.0,
        description="Average CPU utilization across all online devices.",
    )
    avg_memory_usage_pct: float = Field(
        default=0.0,
        description="Average memory utilization across all online devices.",
    )
    avg_temperature_celsius: float = Field(
        default=0.0,
        description="Average temperature across all online devices.",
    )
    devices_by_type: dict[str, int] = Field(
        default_factory=dict,
        description="Count of devices by hardware type.",
    )


# ── Heartbeat Schema ─────────────────────────────────────────────────────────


class HeartbeatRequest(BaseModel):
    """Heartbeat payload sent by an edge device to report its status."""

    device_id: UUID = Field(description="Edge device unique identifier.")
    cpu_usage_pct: float | None = Field(default=None, description="Current CPU %.")
    gpu_usage_pct: float | None = Field(default=None, description="Current GPU %.")
    memory_usage_pct: float | None = Field(
        default=None, description="Current memory %."
    )
    temperature_celsius: float | None = Field(
        default=None, description="Current temperature."
    )
    disk_usage_pct: float | None = Field(default=None, description="Current disk %.")
    fps_processing: float | None = Field(
        default=None, description="Current processing FPS."
    )
    inference_latency_ms: float | None = Field(
        default=None, description="Current inference latency."
    )
    detections_count: int | None = Field(
        default=None, description="Detections since last heartbeat."
    )
    uptime_seconds: float | None = Field(
        default=None, description="Device uptime in seconds."
    )
    firmware_version: str | None = Field(
        default=None, description="Current firmware version."
    )
    deployed_models: list[dict[str, str]] | None = Field(
        default=None,
        description="List of currently loaded models [{name, version, format}].",
    )


class HeartbeatResponse(BaseModel):
    """Response to a heartbeat from an edge device."""

    status: str = Field(default="ok")
    server_time: datetime = Field(description="Server UTC timestamp.")
    pending_commands: list[dict[str, Any]] = Field(
        default_factory=list,
        description="Commands queued for the device (deploy, restart, config update).",
    )
