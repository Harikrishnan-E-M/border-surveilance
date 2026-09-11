"""Detection rule Pydantic schemas.

Rules bind detection logic to cameras and zones, defining what events should
trigger alerts, at what severity, through which channels, and on what schedule.
"""

from __future__ import annotations


import enum
from datetime import datetime
from typing import Any
from uuid import UUID

from pydantic import BaseModel, ConfigDict, Field


# ── Enums ─────────────────────────────────────────────────────────────────────


class RuleType(str, enum.Enum):
    """Catalogue of detection rule types supported by the platform."""

    INTRUSION = "intrusion"
    LOITERING = "loitering"
    CROWD_DENSITY = "crowd_density"
    OBJECT_LEFT = "object_left"
    OBJECT_REMOVED = "object_removed"
    LINE_CROSSING = "line_crossing"
    WRONG_DIRECTION = "wrong_direction"
    PPE_VIOLATION = "ppe_violation"
    FACE_RECOGNIZED = "face_recognized"
    FACE_UNKNOWN = "face_unknown"
    VEHICLE_PLATE = "vehicle_plate"
    VEHICLE_WRONG_WAY = "vehicle_wrong_way"
    FIRE_SMOKE = "fire_smoke"
    FALL_DETECTION = "fall_detection"
    TAILGATING = "tailgating"
    OCCUPANCY_LIMIT = "occupancy_limit"
    CUSTOM = "custom"


class Severity(str, enum.Enum):
    """Alert severity level assigned to a triggered rule."""

    LOW = "low"
    MEDIUM = "medium"
    HIGH = "high"
    CRITICAL = "critical"


# ── Rule CRUD ─────────────────────────────────────────────────────────────────


class RuleCreate(BaseModel):
    """Request body for creating a new detection rule."""

    zone_id: UUID | None = Field(
        default=None,
        description="Zone to scope the rule to. Null means the rule applies to the entire camera view.",
    )
    camera_id: UUID = Field(description="Camera this rule is attached to.")
    rule_type: RuleType = Field(
        description="Type of detection rule.",
        examples=["intrusion"],
    )
    parameters: dict[str, Any] = Field(
        default_factory=dict,
        description="Rule-specific configuration parameters. "
        "Refer to the rule-type catalogue for required and optional keys.",
        examples=[
            {
                "min_duration_seconds": 5,
                "object_classes": ["person"],
                "confidence_threshold": 0.6,
            }
        ],
    )
    severity: Severity = Field(
        description="Alert severity when the rule triggers.",
        examples=["high"],
    )
    alert_channels: list[str] = Field(
        default_factory=list,
        description="Notification channels to fire on trigger.",
        examples=[["email", "sms", "telegram", "webhook"]],
    )
    cooldown_seconds: int = Field(
        default=300,
        ge=0,
        le=86400,
        description="Minimum seconds between repeated alerts for the same rule (0 = no cooldown).",
        examples=[300],
    )
    schedule_cron: str | None = Field(
        default=None,
        max_length=128,
        description="Cron expression defining when the rule is active. "
        "Null means always active.",
        examples=["0 22 * * 1-5"],
    )


class RuleUpdate(BaseModel):
    """Partial update for an existing rule. Only supplied fields are changed."""

    parameters: dict[str, Any] | None = Field(
        default=None,
        description="Updated rule-specific parameters.",
    )
    severity: Severity | None = Field(
        default=None,
        description="Updated alert severity.",
    )
    alert_channels: list[str] | None = Field(
        default=None,
        description="Updated notification channels.",
    )
    is_active: bool | None = Field(
        default=None,
        description="Enable or disable the rule.",
    )
    cooldown_seconds: int | None = Field(
        default=None,
        ge=0,
        le=86400,
        description="Updated cooldown between repeated alerts.",
    )
    schedule_cron: str | None = Field(
        default=None,
        max_length=128,
        description="Updated cron schedule.",
    )


class RuleResponse(BaseModel):
    """Full rule representation returned by read endpoints.

    Includes denormalised ``zone_name`` and ``camera_name`` fields for
    convenience so that clients do not need to issue additional requests.
    """

    model_config = ConfigDict(from_attributes=True)

    id: UUID = Field(description="Rule unique identifier.")
    org_id: UUID = Field(description="Owning organization ID.")
    camera_id: UUID = Field(description="Camera this rule is attached to.")
    zone_id: UUID | None = Field(
        default=None,
        description="Zone scope (null = entire camera view).",
    )
    rule_type: str = Field(
        description="Detection rule type.",
        examples=["intrusion"],
    )
    parameters: dict[str, Any] = Field(
        default_factory=dict,
        description="Rule-specific configuration parameters.",
    )
    severity: str = Field(
        description="Alert severity level.",
        examples=["high"],
    )
    alert_channels: list[str] = Field(
        default_factory=list,
        description="Notification channels.",
    )
    cooldown_seconds: int = Field(
        description="Cooldown between repeated alerts.",
        examples=[300],
    )
    schedule_cron: str | None = Field(
        default=None,
        description="Cron schedule expression.",
    )
    is_active: bool = Field(description="Whether the rule is enabled.")
    last_triggered_at: datetime | None = Field(
        default=None,
        description="Timestamp of the most recent trigger.",
    )
    trigger_count: int = Field(
        default=0,
        description="Total number of times this rule has triggered.",
    )
    created_at: datetime = Field(description="Rule creation timestamp.")
    updated_at: datetime | None = Field(
        default=None,
        description="Last update timestamp.",
    )

    # Denormalised convenience fields
    zone_name: str | None = Field(
        default=None,
        description="Name of the associated zone (if any).",
        examples=["Entry Gate Zone"],
    )
    camera_name: str = Field(
        description="Name of the associated camera.",
        examples=["Main Entrance"],
    )


# ── Rule Type Catalogue ──────────────────────────────────────────────────────


class RuleTypeInfo(BaseModel):
    """Describes a single rule type and its parameter schema.

    Returned by the rule-type catalogue endpoint so that front-end
    clients can dynamically build rule configuration forms.
    """

    rule_type: str = Field(
        description="Rule type identifier.",
        examples=["intrusion"],
    )
    description: str = Field(
        description="Human-readable explanation of what the rule detects.",
        examples=["Triggers when a person enters a restricted zone."],
    )
    required_parameters: dict[str, str] = Field(
        default_factory=dict,
        description="Map of required parameter names to their type descriptions.",
        examples=[
            {
                "object_classes": "list[str] - Object classes to detect (e.g. ['person'])",
                "confidence_threshold": "float - Minimum confidence (0.0-1.0)",
            }
        ],
    )
    optional_parameters: dict[str, str] = Field(
        default_factory=dict,
        description="Map of optional parameter names to their type descriptions.",
        examples=[
            {
                "min_duration_seconds": "int - Minimum time in zone before triggering (default 0)",
                "max_objects": "int - Maximum simultaneous objects before alert",
            }
        ],
    )
