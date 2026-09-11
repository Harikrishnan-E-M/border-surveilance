"""Vehicle and license-plate Pydantic schemas.

Covers vehicle CRUD, plate recognition events, vehicle access logs,
and plate search functionality.
"""

from __future__ import annotations


import enum
from datetime import datetime
from uuid import UUID

from pydantic import BaseModel, ConfigDict, Field


# ── Enums ─────────────────────────────────────────────────────────────────────


class VehicleType(str, enum.Enum):
    """Physical vehicle type classification."""

    CAR = "car"
    MOTORCYCLE = "motorcycle"
    TRUCK = "truck"
    BUS = "bus"
    VAN = "van"
    AUTO_RICKSHAW = "auto_rickshaw"
    BICYCLE = "bicycle"
    OTHER = "other"


class VehicleCategory(str, enum.Enum):
    """Access-control category for a registered vehicle."""

    AUTHORIZED = "authorized"
    VISITOR = "visitor"
    VIP = "vip"
    BLOCKLIST = "blocklist"
    DELIVERY = "delivery"
    EMERGENCY = "emergency"
    UNKNOWN = "unknown"


class VehicleDirection(str, enum.Enum):
    """Direction of vehicle movement relative to a camera / gate."""

    ENTRY = "entry"
    EXIT = "exit"
    UNKNOWN = "unknown"


# ── Vehicle CRUD ──────────────────────────────────────────────────────────────


class VehicleCreate(BaseModel):
    """Request body for registering a new vehicle in the platform."""

    plate_number: str = Field(
        min_length=1,
        max_length=20,
        description="License plate number (normalised uppercase, no spaces).",
        examples=["DL01AB1234"],
    )
    owner_name: str = Field(
        min_length=1,
        max_length=255,
        description="Name of the vehicle owner.",
        examples=["Rajesh Kumar"],
    )
    vehicle_type: VehicleType = Field(
        description="Physical vehicle type.",
        examples=["car"],
    )
    color: str | None = Field(
        default=None,
        max_length=50,
        description="Vehicle colour.",
        examples=["White"],
    )
    make: str | None = Field(
        default=None,
        max_length=100,
        description="Vehicle manufacturer.",
        examples=["Maruti Suzuki"],
    )
    model_name: str | None = Field(
        default=None,
        max_length=100,
        description="Vehicle model name.",
        examples=["Swift Dzire"],
    )
    category: VehicleCategory = Field(
        description="Access-control category.",
        examples=["authorized"],
    )
    department: str | None = Field(
        default=None,
        max_length=255,
        description="Department or division the vehicle is associated with.",
        examples=["Management"],
    )
    phone: str | None = Field(
        default=None,
        max_length=20,
        description="Owner contact phone number.",
        examples=["+919876543210"],
    )
    notes: str | None = Field(
        default=None,
        max_length=2048,
        description="Free-text notes about the vehicle.",
        examples=["Reserved parking spot B-12."],
    )


class VehicleUpdate(BaseModel):
    """Partial update for an existing vehicle record."""

    owner_name: str | None = Field(
        default=None,
        min_length=1,
        max_length=255,
        description="Updated owner name.",
    )
    category: VehicleCategory | None = Field(
        default=None,
        description="Updated access-control category.",
    )
    is_active: bool | None = Field(
        default=None,
        description="Enable or disable the vehicle record.",
    )
    notes: str | None = Field(
        default=None,
        max_length=2048,
        description="Updated notes.",
    )


class VehicleResponse(BaseModel):
    """Full vehicle representation returned by read endpoints."""

    model_config = ConfigDict(from_attributes=True)

    id: UUID = Field(description="Vehicle unique identifier.")
    org_id: UUID = Field(description="Owning organization ID.")
    plate_number: str = Field(
        description="License plate number.",
        examples=["DL01AB1234"],
    )
    owner_name: str = Field(
        description="Vehicle owner name.",
        examples=["Rajesh Kumar"],
    )
    vehicle_type: str = Field(
        description="Physical vehicle type.",
        examples=["car"],
    )
    color: str | None = Field(default=None, description="Vehicle colour.")
    make: str | None = Field(default=None, description="Vehicle manufacturer.")
    model_name: str | None = Field(default=None, description="Vehicle model.")
    category: str = Field(
        description="Access-control category.",
        examples=["authorized"],
    )
    department: str | None = Field(default=None, description="Associated department.")
    phone: str | None = Field(default=None, description="Owner phone number.")
    notes: str | None = Field(default=None, description="Notes.")
    is_active: bool = Field(description="Whether the record is active.")
    created_at: datetime = Field(description="Record creation timestamp.")
    updated_at: datetime | None = Field(
        default=None,
        description="Last update timestamp.",
    )


# ── Vehicle Events (Plate Recognition) ───────────────────────────────────────


class VehicleEventResponse(BaseModel):
    """A single automatic number-plate recognition (ANPR) event."""

    model_config = ConfigDict(from_attributes=True)

    id: UUID = Field(description="Event unique identifier.")
    camera_id: UUID = Field(description="Camera that captured the plate.")
    camera_name: str = Field(description="Camera name.", examples=["Gate B Camera"])
    plate_number: str = Field(
        description="Recognised plate number.",
        examples=["DL01AB1234"],
    )
    plate_confidence: float = Field(
        ge=0.0,
        le=1.0,
        description="OCR confidence score for the plate reading.",
        examples=[0.96],
    )
    vehicle: VehicleResponse | None = Field(
        default=None,
        description="Matched registered vehicle, or null if the plate is unregistered.",
    )
    direction: VehicleDirection = Field(
        description="Direction of travel.",
        examples=["entry"],
    )
    vehicle_type_detected: str | None = Field(
        default=None,
        description="Vehicle type detected by the vision model.",
        examples=["car"],
    )
    color_detected: str | None = Field(
        default=None,
        description="Vehicle colour detected by the vision model.",
        examples=["white"],
    )
    speed_kmh: float | None = Field(
        default=None,
        ge=0.0,
        description="Estimated vehicle speed in km/h (if available).",
        examples=[25.5],
    )
    timestamp: datetime = Field(description="Event timestamp.")
    plate_snapshot_url: str | None = Field(
        default=None,
        description="URL of the cropped plate snapshot.",
    )
    vehicle_snapshot_url: str | None = Field(
        default=None,
        description="URL of the full vehicle snapshot.",
    )


# ── Vehicle Access Log ────────────────────────────────────────────────────────


class VehicleLogResponse(BaseModel):
    """Paired entry/exit log for a vehicle visit."""

    vehicle_id: UUID | None = Field(
        default=None,
        description="Registered vehicle ID (null if unregistered).",
    )
    plate_number: str = Field(
        description="License plate number.",
        examples=["DL01AB1234"],
    )
    owner_name: str | None = Field(
        default=None,
        description="Vehicle owner name (null if unregistered).",
    )
    vehicle_type: str | None = Field(
        default=None,
        description="Vehicle type.",
        examples=["car"],
    )
    category: str | None = Field(
        default=None,
        description="Access-control category.",
        examples=["authorized"],
    )
    entry_camera: str | None = Field(
        default=None,
        description="Camera name that recorded the entry.",
        examples=["Main Gate In"],
    )
    entry_time: datetime | None = Field(
        default=None,
        description="Timestamp of the entry event.",
    )
    exit_camera: str | None = Field(
        default=None,
        description="Camera name that recorded the exit.",
        examples=["Main Gate Out"],
    )
    exit_time: datetime | None = Field(
        default=None,
        description="Timestamp of the exit event.",
    )
    duration_seconds: float | None = Field(
        default=None,
        ge=0.0,
        description="Total visit duration in seconds (null if exit not yet recorded).",
        examples=[3600.0],
    )


# ── Plate Search ──────────────────────────────────────────────────────────────


class PlateSearchRequest(BaseModel):
    """Request parameters for searching vehicle events by plate number."""

    plate_number: str = Field(
        min_length=1,
        max_length=20,
        description="Full or partial plate number to search for. "
        "Partial matches are supported (e.g. 'DL01' will match 'DL01AB1234').",
        examples=["DL01AB"],
    )
    start_date: datetime | None = Field(
        default=None,
        description="Filter events from this timestamp onwards.",
    )
    end_date: datetime | None = Field(
        default=None,
        description="Filter events up to this timestamp.",
    )
    camera_ids: list[UUID] | None = Field(
        default=None,
        description="Filter to specific cameras. Null includes all cameras.",
    )
