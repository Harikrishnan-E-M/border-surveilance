"""Zone (region of interest) Pydantic schemas.

Zones are polygonal regions drawn on a camera's field of view. They are
referenced by detection rules to scope analytics to specific areas.
"""

from __future__ import annotations


import enum
from datetime import datetime
from uuid import UUID

from pydantic import BaseModel, ConfigDict, Field, field_validator


# ── Enums ─────────────────────────────────────────────────────────────────────


class ZoneType(str, enum.Enum):
    """Semantic type of a zone drawn on a camera view."""

    DETECTION = "detection"
    EXCLUSION = "exclusion"
    COUNTING_LINE = "counting_line"
    INTRUSION = "intrusion"
    LOITERING = "loitering"
    PARKING = "parking"
    CROSSWALK = "crosswalk"
    CUSTOM = "custom"


# ── Point Schema ──────────────────────────────────────────────────────────────


class PointSchema(BaseModel):
    """A normalised 2-D point on the camera frame.

    Coordinates are expressed as fractions of the frame dimensions so that
    zones remain valid across different resolutions.
    """

    x: float = Field(
        ge=0.0,
        le=1.0,
        description="Horizontal position as a fraction of frame width (0.0 = left, 1.0 = right).",
        examples=[0.25],
    )
    y: float = Field(
        ge=0.0,
        le=1.0,
        description="Vertical position as a fraction of frame height (0.0 = top, 1.0 = bottom).",
        examples=[0.75],
    )


# ── Zone CRUD ─────────────────────────────────────────────────────────────────


class ZoneCreate(BaseModel):
    """Request body for creating a new zone on a camera."""

    camera_id: UUID = Field(description="Camera this zone belongs to.")
    name: str = Field(
        min_length=1,
        max_length=255,
        description="Human-readable zone name.",
        examples=["Entry Gate Zone"],
    )
    zone_type: ZoneType = Field(
        description="Semantic type of the zone.",
        examples=["detection"],
    )
    polygon_points: list[PointSchema] = Field(
        min_length=3,
        description="Ordered list of polygon vertices (minimum 3). "
        "Points are connected in order, with the last point "
        "implicitly connected back to the first.",
        examples=[
            [
                {"x": 0.1, "y": 0.1},
                {"x": 0.9, "y": 0.1},
                {"x": 0.9, "y": 0.9},
                {"x": 0.1, "y": 0.9},
            ]
        ],
    )
    color_hex: str | None = Field(
        default=None,
        max_length=7,
        description="Hex colour code for rendering the zone overlay.",
        examples=["#FF5733"],
    )

    @field_validator("color_hex")
    @classmethod
    def validate_color_hex(cls, v: str | None) -> str | None:
        """Ensure the colour code is a valid 6-digit hex with a leading '#'."""
        if v is not None:
            import re

            if not re.fullmatch(r"#[0-9A-Fa-f]{6}", v):
                raise ValueError(
                    "color_hex must be a valid hex colour code (e.g. #FF5733)"
                )
        return v


class ZoneUpdate(BaseModel):
    """Partial update for an existing zone. Only supplied fields are changed."""

    name: str | None = Field(
        default=None,
        min_length=1,
        max_length=255,
        description="Updated zone name.",
    )
    zone_type: ZoneType | None = Field(
        default=None,
        description="Updated zone type.",
    )
    polygon_points: list[PointSchema] | None = Field(
        default=None,
        min_length=3,
        description="Updated polygon vertices (minimum 3 points).",
    )
    color_hex: str | None = Field(
        default=None,
        max_length=7,
        description="Updated hex colour code.",
    )
    is_active: bool | None = Field(
        default=None,
        description="Enable or disable the zone.",
    )

    @field_validator("color_hex")
    @classmethod
    def validate_color_hex(cls, v: str | None) -> str | None:
        """Ensure the colour code is a valid 6-digit hex with a leading '#'."""
        if v is not None:
            import re

            if not re.fullmatch(r"#[0-9A-Fa-f]{6}", v):
                raise ValueError(
                    "color_hex must be a valid hex colour code (e.g. #FF5733)"
                )
        return v


class ZoneResponse(BaseModel):
    """Full zone representation returned by read endpoints."""

    model_config = ConfigDict(from_attributes=True)

    id: UUID = Field(description="Zone unique identifier.")
    org_id: UUID = Field(description="Owning organization ID.")
    camera_id: UUID = Field(description="Camera this zone is drawn on.")
    name: str = Field(description="Zone name.", examples=["Entry Gate Zone"])
    zone_type: str = Field(
        description="Semantic zone type.",
        examples=["detection"],
    )
    polygon_points: list[PointSchema] = Field(
        description="Ordered list of polygon vertices.",
    )
    color_hex: str | None = Field(
        default=None,
        description="Hex colour code for rendering.",
        examples=["#FF5733"],
    )
    is_active: bool = Field(description="Whether the zone is enabled.")
    created_at: datetime = Field(description="Zone creation timestamp.")
    updated_at: datetime | None = Field(
        default=None,
        description="Last update timestamp.",
    )
