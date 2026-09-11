"""Pydantic schemas for the Digital Twin / Floor Plan feature.

Covers floor plan CRUD, camera and zone placement on floor plans,
live status overlay data, heatmap generation, and person track
mapping onto the spatial layout.
"""

from __future__ import annotations


import enum
from datetime import datetime
from typing import Optional
from uuid import UUID

from pydantic import BaseModel, ConfigDict, Field


# -- Enums ---------------------------------------------------------------------


class OverlayType(str, enum.Enum):
    """Types of generated overlays for a floor plan."""

    HEATMAP = "heatmap"
    OCCUPANCY = "occupancy"
    ALERTS = "alerts"
    TRACKS = "tracks"


# -- Shared building blocks ----------------------------------------------------


class FloorPlanPoint(BaseModel):
    """A single point in relative coordinates on the floor plan."""

    x: float = Field(
        ge=0.0, le=1.0,
        description="Horizontal position as fraction of image width.",
        examples=[0.5],
    )
    y: float = Field(
        ge=0.0, le=1.0,
        description="Vertical position as fraction of image height.",
        examples=[0.5],
    )


# -- Floor Plan CRUD ----------------------------------------------------------


class FloorPlanCreate(BaseModel):
    """Request body for creating a new floor plan (JSON part of multipart)."""

    name: str = Field(
        min_length=1, max_length=255,
        description="Display name for the floor plan.",
        examples=["Main Building - Ground Floor"],
    )
    building_name: str | None = Field(
        default=None, max_length=255,
        description="Building name for grouping.",
        examples=["Main Building"],
    )
    floor_number: int = Field(
        default=0,
        description="Floor number (0 = ground).",
        examples=[0],
    )
    scale_meters_per_pixel: float | None = Field(
        default=None, gt=0,
        description="Real-world scale: meters per pixel.",
        examples=[0.05],
    )
    metadata_json: dict | None = Field(
        default=None,
        description="Arbitrary metadata for the floor plan.",
    )


class FloorPlanUpdate(BaseModel):
    """Partial update payload for an existing floor plan."""

    name: str | None = Field(
        default=None, min_length=1, max_length=255,
        description="Updated display name.",
    )
    building_name: str | None = Field(
        default=None, max_length=255,
        description="Updated building name.",
    )
    floor_number: int | None = Field(
        default=None,
        description="Updated floor number.",
    )
    scale_meters_per_pixel: float | None = Field(
        default=None, gt=0,
        description="Updated scale factor.",
    )
    metadata_json: dict | None = Field(
        default=None,
        description="Updated metadata.",
    )
    is_active: bool | None = Field(
        default=None,
        description="Activate or deactivate the floor plan.",
    )


class CameraPlacementResponse(BaseModel):
    """Camera placement on a floor plan."""

    model_config = ConfigDict(from_attributes=True)

    id: UUID
    floor_plan_id: UUID
    camera_id: UUID
    x_position: float
    y_position: float
    rotation_degrees: float
    fov_angle: float
    fov_range: float
    label: str | None = None
    camera_name: str | None = Field(default=None, description="Resolved camera display name.")
    camera_is_online: bool | None = Field(default=None, description="Live online status.")
    created_at: datetime
    updated_at: datetime


class ZonePlacementResponse(BaseModel):
    """Zone placement on a floor plan."""

    model_config = ConfigDict(from_attributes=True)

    id: UUID
    floor_plan_id: UUID
    zone_id: UUID
    polygon_points: list[FloorPlanPoint]
    color: str
    opacity: float
    zone_name: str | None = Field(default=None, description="Resolved zone display name.")
    zone_type: str | None = Field(default=None, description="Zone type (restricted, monitoring, etc.).")
    created_at: datetime
    updated_at: datetime


class OverlayResponse(BaseModel):
    """A generated overlay for a floor plan."""

    model_config = ConfigDict(from_attributes=True)

    id: UUID
    floor_plan_id: UUID
    overlay_type: OverlayType
    image_url: str = Field(description="URL/path to the overlay image in MinIO.")
    generated_at: datetime
    valid_until: datetime | None = None


class FloorPlanResponse(BaseModel):
    """Full floor plan representation returned by read endpoints."""

    model_config = ConfigDict(from_attributes=True)

    id: UUID
    org_id: UUID
    name: str
    building_name: str | None = None
    floor_number: int
    image_url: str = Field(description="URL/path to the floor plan image.")
    width_px: int
    height_px: int
    scale_meters_per_pixel: float | None = None
    metadata_json: dict | None = None
    is_active: bool
    camera_placements: list[CameraPlacementResponse] = Field(default_factory=list)
    zone_placements: list[ZonePlacementResponse] = Field(default_factory=list)
    overlays: list[OverlayResponse] = Field(default_factory=list)
    created_at: datetime
    updated_at: datetime


class FloorPlanListItem(BaseModel):
    """Lightweight floor plan item for list endpoints."""

    model_config = ConfigDict(from_attributes=True)

    id: UUID
    org_id: UUID
    name: str
    building_name: str | None = None
    floor_number: int
    image_url: str
    width_px: int
    height_px: int
    is_active: bool
    camera_count: int = Field(default=0, description="Number of cameras placed.")
    zone_count: int = Field(default=0, description="Number of zones placed.")
    created_at: datetime
    updated_at: datetime


class FloorPlanListResponse(BaseModel):
    """Paginated list of floor plans."""

    items: list[FloorPlanListItem]
    total: int


# -- Camera Placement CRUD ----------------------------------------------------


class CameraPlacementCreate(BaseModel):
    """Request body for placing a camera on a floor plan."""

    camera_id: UUID = Field(description="ID of the camera to place.")
    x_position: float = Field(
        ge=0.0, le=1.0,
        description="Horizontal position (0.0-1.0).",
        examples=[0.5],
    )
    y_position: float = Field(
        ge=0.0, le=1.0,
        description="Vertical position (0.0-1.0).",
        examples=[0.3],
    )
    rotation_degrees: float = Field(
        default=0.0, ge=0.0, le=360.0,
        description="Camera rotation in degrees.",
        examples=[180.0],
    )
    fov_angle: float = Field(
        default=90.0, ge=1.0, le=360.0,
        description="Field of view angle in degrees.",
        examples=[90.0],
    )
    fov_range: float = Field(
        default=100.0, ge=1.0,
        description="FOV cone range in map pixels.",
        examples=[100.0],
    )
    label: str | None = Field(
        default=None, max_length=255,
        description="Optional display label override.",
    )


class CameraPlacementUpdate(BaseModel):
    """Partial update for a camera placement."""

    x_position: float | None = Field(
        default=None, ge=0.0, le=1.0,
        description="Updated horizontal position.",
    )
    y_position: float | None = Field(
        default=None, ge=0.0, le=1.0,
        description="Updated vertical position.",
    )
    rotation_degrees: float | None = Field(
        default=None, ge=0.0, le=360.0,
        description="Updated rotation.",
    )
    fov_angle: float | None = Field(
        default=None, ge=1.0, le=360.0,
        description="Updated FOV angle.",
    )
    fov_range: float | None = Field(
        default=None, ge=1.0,
        description="Updated FOV range.",
    )
    label: str | None = Field(
        default=None, max_length=255,
        description="Updated label.",
    )


# -- Zone Placement CRUD ------------------------------------------------------


class ZonePlacementCreate(BaseModel):
    """Request body for placing a zone polygon on a floor plan."""

    zone_id: UUID = Field(description="ID of the zone to place.")
    polygon_points: list[FloorPlanPoint] = Field(
        min_length=3,
        description="Polygon vertices in relative coordinates (min 3 points).",
    )
    color: str = Field(
        default="#3B82F6", max_length=9,
        description="Hex colour for the zone polygon.",
        examples=["#3B82F6"],
    )
    opacity: float = Field(
        default=0.3, ge=0.0, le=1.0,
        description="Fill opacity (0.0 - 1.0).",
        examples=[0.3],
    )


class ZonePlacementUpdate(BaseModel):
    """Partial update for a zone placement."""

    polygon_points: list[FloorPlanPoint] | None = Field(
        default=None, min_length=3,
        description="Updated polygon vertices.",
    )
    color: str | None = Field(
        default=None, max_length=9,
        description="Updated hex colour.",
    )
    opacity: float | None = Field(
        default=None, ge=0.0, le=1.0,
        description="Updated opacity.",
    )


# -- Live Status ---------------------------------------------------------------


class CameraStatusItem(BaseModel):
    """Live status of a single camera on the floor plan."""

    camera_id: UUID
    placement_id: UUID
    camera_name: str
    x_position: float
    y_position: float
    rotation_degrees: float
    fov_angle: float
    fov_range: float
    is_online: bool
    has_active_alerts: bool
    active_alert_count: int = 0
    label: str | None = None


class ZoneOccupancyItem(BaseModel):
    """Live occupancy data for a single zone on the floor plan."""

    zone_id: UUID
    placement_id: UUID
    zone_name: str
    zone_type: str | None = None
    polygon_points: list[FloorPlanPoint]
    color: str
    opacity: float
    current_occupancy: int = 0
    capacity: int | None = None


class ActiveAlertItem(BaseModel):
    """An active alert associated with a camera on the floor plan."""

    alert_id: UUID
    camera_id: UUID
    title: str
    severity: str
    alert_type: str
    created_at: datetime


class LiveStatusResponse(BaseModel):
    """Aggregated live status data for the entire floor plan."""

    floor_plan_id: UUID
    camera_statuses: list[CameraStatusItem] = Field(default_factory=list)
    zone_occupancies: list[ZoneOccupancyItem] = Field(default_factory=list)
    active_alerts: list[ActiveAlertItem] = Field(default_factory=list)
    person_counts: dict[str, int] = Field(
        default_factory=dict,
        description="Zone ID -> current person count mapping.",
    )
    timestamp: datetime


# -- Heatmap Generation --------------------------------------------------------


class HeatmapGenerateRequest(BaseModel):
    """Request body for generating a heatmap overlay."""

    time_range: str = Field(
        default="24h",
        description="Time range for heatmap data (1h, 6h, 12h, 24h, 7d, 30d).",
        examples=["24h"],
    )
    color_scheme: str = Field(
        default="jet",
        description="Colour scheme for the heatmap.",
        examples=["jet"],
    )


# -- Person Tracks on Map -----------------------------------------------------


class PersonTrackPoint(BaseModel):
    """A single point in a person's track on the floor plan."""

    camera_id: UUID
    camera_name: str
    x_position: float
    y_position: float
    timestamp: datetime
    thumbnail_url: str | None = None


class PersonTrackResponse(BaseModel):
    """Person movement track mapped onto the floor plan."""

    floor_plan_id: UUID
    global_person_id: UUID
    track_points: list[PersonTrackPoint] = Field(default_factory=list)
    total_cameras_visited: int = 0
    first_seen: datetime | None = None
    last_seen: datetime | None = None
    total_duration_seconds: float | None = None
