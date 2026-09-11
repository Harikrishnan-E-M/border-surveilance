"""Camera, PTZ, ONVIF discovery, and camera-group Pydantic schemas.

Covers camera CRUD, health monitoring, stream testing, bulk CSV import,
PTZ control commands, ONVIF auto-discovery, and camera grouping.
"""

from __future__ import annotations


import enum
from datetime import datetime
from typing import Literal
from uuid import UUID

from pydantic import BaseModel, ConfigDict, Field

from app.schemas.common import PaginatedResponse


# ── Enums ─────────────────────────────────────────────────────────────────────


class StreamProtocol(str, enum.Enum):
    """Supported camera streaming protocols."""

    RTSP = "rtsp"
    RTMP = "rtmp"
    HTTP = "http"
    HLS = "hls"
    WEBRTC = "webrtc"
    ONVIF = "onvif"


class RecordingMode(str, enum.Enum):
    """Recording strategies for a camera feed."""

    NONE = "none"
    CONTINUOUS = "continuous"
    EVENT = "event"
    SCHEDULE = "schedule"


class HealthStatus(str, enum.Enum):
    """Camera connectivity health status."""

    ONLINE = "online"
    OFFLINE = "offline"
    DEGRADED = "degraded"


class PTZAction(str, enum.Enum):
    """Available PTZ (Pan-Tilt-Zoom) control actions."""

    PAN_LEFT = "pan_left"
    PAN_RIGHT = "pan_right"
    TILT_UP = "tilt_up"
    TILT_DOWN = "tilt_down"
    ZOOM_IN = "zoom_in"
    ZOOM_OUT = "zoom_out"
    PRESET_GOTO = "preset_goto"


# ── Camera CRUD ───────────────────────────────────────────────────────────────


class CameraCreate(BaseModel):
    """Request body for registering a new camera."""

    name: str = Field(
        min_length=1,
        max_length=255,
        description="Human-readable camera name.",
        examples=["Main Entrance"],
    )
    stream_url: str = Field(
        min_length=1,
        max_length=2048,
        description="Full stream URL (e.g. rtsp://host:554/stream).",
        examples=["rtsp://192.168.1.100:554/cam/realmonitor?channel=1&subtype=0"],
    )
    protocol: StreamProtocol = Field(
        description="Streaming protocol used by the camera.",
        examples=["rtsp"],
    )
    location_description: str | None = Field(
        default=None,
        max_length=512,
        description="Free-text description of the camera's physical location.",
        examples=["Building A, Ground Floor, Main Lobby"],
    )
    username: str | None = Field(
        default=None,
        max_length=255,
        description="Camera authentication username.",
    )
    password: str | None = Field(
        default=None,
        max_length=255,
        description="Camera authentication password.",
    )
    resolution: str | None = Field(
        default=None,
        max_length=20,
        description="Stream resolution (WxH).",
        examples=["1920x1080"],
    )
    fps: int | None = Field(
        default=None,
        ge=1,
        le=120,
        description="Frames per second.",
        examples=[25],
    )
    latitude: float | None = Field(
        default=None,
        ge=-90.0,
        le=90.0,
        description="GPS latitude of the camera.",
        examples=[28.6139],
    )
    longitude: float | None = Field(
        default=None,
        ge=-180.0,
        le=180.0,
        description="GPS longitude of the camera.",
        examples=[77.2090],
    )
    recording_mode: RecordingMode = Field(
        default=RecordingMode.NONE,
        description="Recording strategy for the camera feed.",
        examples=["none"],
    )


class CameraUpdate(BaseModel):
    """Partial update for an existing camera. Only supplied fields are changed."""

    name: str | None = Field(
        default=None,
        min_length=1,
        max_length=255,
        description="Updated camera name.",
    )
    stream_url: str | None = Field(
        default=None,
        min_length=1,
        max_length=2048,
        description="Updated stream URL.",
    )
    location_description: str | None = Field(
        default=None,
        max_length=512,
        description="Updated location description.",
    )
    is_active: bool | None = Field(
        default=None,
        description="Enable or disable the camera.",
    )
    recording_mode: RecordingMode | None = Field(
        default=None,
        description="Updated recording strategy.",
    )


class CameraResponse(BaseModel):
    """Full camera representation returned by read endpoints."""

    model_config = ConfigDict(from_attributes=True)

    id: UUID = Field(description="Camera unique identifier.")
    org_id: UUID = Field(description="Owning organization ID.")
    name: str = Field(description="Camera name.", examples=["Main Entrance"])
    stream_url: str = Field(description="Camera stream URL.")
    protocol: str = Field(description="Streaming protocol.", examples=["rtsp"])
    location_description: str | None = Field(
        default=None,
        description="Physical location description.",
    )
    resolution: str | None = Field(default=None, description="Stream resolution.")
    fps: int | None = Field(default=None, description="Frames per second.")
    latitude: float | None = Field(default=None, description="GPS latitude.")
    longitude: float | None = Field(default=None, description="GPS longitude.")
    recording_mode: str = Field(
        description="Recording strategy.",
        examples=["continuous"],
    )
    health_status: HealthStatus = Field(
        description="Current connectivity status: online, offline, or degraded.",
        examples=["online"],
    )
    is_active: bool = Field(description="Whether the camera is enabled.")
    last_seen: datetime | None = Field(
        default=None,
        description="Last timestamp the camera reported frames.",
    )
    created_at: datetime = Field(description="Camera creation timestamp.")
    updated_at: datetime | None = Field(
        default=None,
        description="Last update timestamp.",
    )


# Typed alias for paginated camera lists
CameraListResponse = PaginatedResponse[CameraResponse]


# ── Bulk Import ───────────────────────────────────────────────────────────────


class CameraBulkImport(BaseModel):
    """Metadata schema describing the expected CSV columns for bulk import.

    The actual file is uploaded as ``multipart/form-data``; this schema
    documents the expected column structure and is not used as a JSON body.
    """

    file_description: str = Field(
        default="CSV file with columns: name, stream_url, protocol, location_description, username, password, resolution, fps, latitude, longitude, recording_mode",
        description="Description of the expected CSV file format.",
    )


# ── Stream Testing ────────────────────────────────────────────────────────────


class StreamTestResponse(BaseModel):
    """Result of a stream connectivity / capability test."""

    success: bool = Field(
        description="Whether the stream could be connected to.",
    )
    snapshot_url: str | None = Field(
        default=None,
        description="URL of a captured test snapshot, if available.",
    )
    resolution: str | None = Field(
        default=None,
        description="Detected stream resolution.",
        examples=["1920x1080"],
    )
    fps: int | None = Field(
        default=None,
        description="Detected frames per second.",
        examples=[25],
    )
    error: str | None = Field(
        default=None,
        description="Error message if the test failed.",
        examples=["Connection timed out after 10 seconds."],
    )


# ── Camera Health ─────────────────────────────────────────────────────────────


class CameraHealthRecord(BaseModel):
    """Single health check record for a camera."""

    status: HealthStatus = Field(description="Health status at the recorded time.")
    fps: float | None = Field(default=None, description="Measured FPS at check time.")
    latency_ms: float | None = Field(
        default=None,
        description="Round-trip latency in milliseconds.",
    )
    error: str | None = Field(
        default=None,
        description="Error message if the check failed.",
    )
    checked_at: datetime = Field(description="Timestamp of the health check.")


class CameraHealthResponse(BaseModel):
    """Health history for a single camera."""

    camera_id: UUID = Field(description="Camera being reported on.")
    camera_name: str = Field(description="Camera display name.")
    current_status: HealthStatus = Field(description="Current health status.")
    history: list[CameraHealthRecord] = Field(
        default_factory=list,
        description="Chronological list of recent health check records.",
    )


# ── PTZ Control ───────────────────────────────────────────────────────────────


class PTZControlRequest(BaseModel):
    """Command to move a PTZ camera or jump to a preset."""

    action: PTZAction = Field(
        description="PTZ action to perform.",
        examples=["pan_left"],
    )
    value: float = Field(
        default=0.5,
        ge=0.0,
        le=1.0,
        description="Magnitude of the movement (0.0 to 1.0). For preset_goto, this is the preset index.",
        examples=[0.5],
    )


# ── ONVIF Discovery ──────────────────────────────────────────────────────────


class ONVIFDevice(BaseModel):
    """Single device discovered via ONVIF WS-Discovery."""

    ip: str = Field(description="Device IP address.", examples=["192.168.1.100"])
    port: int = Field(description="ONVIF service port.", examples=[80])
    name: str | None = Field(
        default=None,
        description="Device name reported by ONVIF.",
        examples=["HikVision DS-2CD2185"],
    )
    manufacturer: str | None = Field(
        default=None,
        description="Device manufacturer.",
        examples=["Hikvision"],
    )
    model: str | None = Field(
        default=None,
        description="Device model identifier.",
        examples=["DS-2CD2185FWD-I"],
    )
    stream_urls: list[str] = Field(
        default_factory=list,
        description="Discovered RTSP stream URLs for the device.",
        examples=[["rtsp://192.168.1.100:554/Streaming/Channels/101"]],
    )


class ONVIFDiscoveryResponse(BaseModel):
    """Results from an ONVIF network discovery scan."""

    devices: list[ONVIFDevice] = Field(
        default_factory=list,
        description="List of ONVIF-compliant devices found on the network.",
    )
    scan_duration_seconds: float = Field(
        description="How long the discovery scan took.",
        examples=[5.2],
    )


# ── Camera Groups ─────────────────────────────────────────────────────────────


class CameraGroupCreate(BaseModel):
    """Request body for creating a new camera group."""

    name: str = Field(
        min_length=1,
        max_length=255,
        description="Group name.",
        examples=["Parking Lot Cameras"],
    )
    description: str | None = Field(
        default=None,
        max_length=1024,
        description="Optional description of the group.",
        examples=["All cameras covering the north parking lot."],
    )
    camera_ids: list[UUID] = Field(
        min_length=1,
        description="IDs of cameras to include in the group.",
    )


class CameraGroupResponse(BaseModel):
    """Full camera group representation."""

    model_config = ConfigDict(from_attributes=True)

    id: UUID = Field(description="Group unique identifier.")
    name: str = Field(description="Group name.")
    description: str | None = Field(
        default=None,
        description="Group description.",
    )
    cameras: list[CameraResponse] = Field(
        default_factory=list,
        description="Cameras belonging to this group.",
    )
    created_at: datetime = Field(description="Group creation timestamp.")
    updated_at: datetime | None = Field(
        default=None,
        description="Last update timestamp.",
    )
