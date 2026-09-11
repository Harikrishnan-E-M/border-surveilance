"""Alert Pydantic schemas.

Covers alert responses, acknowledgement / resolution workflows,
escalation requests, and aggregate alert statistics.
"""

from __future__ import annotations


from datetime import datetime
from typing import Any
from uuid import UUID

from pydantic import BaseModel, ConfigDict, Field

from app.schemas.common import PaginatedResponse


# ── Alert Response ────────────────────────────────────────────────────────────


class AlertResponse(BaseModel):
    """Full alert representation returned by read endpoints.

    Includes denormalised camera, zone, rule, and acknowledger names
    so clients can render alerts without extra round-trips.
    """

    model_config = ConfigDict(from_attributes=True)

    id: UUID = Field(description="Alert unique identifier.")
    org_id: UUID = Field(description="Owning organization ID.")
    camera_id: UUID = Field(description="Camera that generated the alert.")
    zone_id: UUID | None = Field(
        default=None,
        description="Zone where the event was detected (if applicable).",
    )
    rule_id: UUID = Field(description="Rule that triggered this alert.")
    rule_type: str = Field(
        description="Type of the triggering rule.",
        examples=["intrusion"],
    )
    severity: str = Field(
        description="Alert severity level.",
        examples=["high"],
    )
    status: str = Field(
        description="Current alert lifecycle status: new, acknowledged, resolved, escalated.",
        examples=["new"],
    )
    title: str = Field(
        description="Short human-readable alert title.",
        examples=["Intrusion detected in Entry Gate Zone"],
    )
    description: str | None = Field(
        default=None,
        description="Detailed description of the detected event.",
    )
    snapshot_url: str | None = Field(
        default=None,
        description="URL of the snapshot captured at alert time.",
    )
    video_clip_url: str | None = Field(
        default=None,
        description="URL of the short video clip around the alert event.",
    )
    metadata: dict[str, Any] = Field(
        default_factory=dict,
        description="Arbitrary key-value metadata attached to the alert "
        "(e.g. object counts, bounding boxes).",
    )
    acknowledged_at: datetime | None = Field(
        default=None,
        description="Timestamp when the alert was acknowledged.",
    )
    acknowledged_by: UUID | None = Field(
        default=None,
        description="User ID of the acknowledger.",
    )
    resolved_at: datetime | None = Field(
        default=None,
        description="Timestamp when the alert was resolved.",
    )
    resolved_by: UUID | None = Field(
        default=None,
        description="User ID of the resolver.",
    )
    is_false_positive: bool = Field(
        default=False,
        description="Whether the alert was marked as a false positive during resolution.",
    )
    created_at: datetime = Field(description="Alert creation timestamp.")

    # Denormalised convenience fields
    camera_name: str = Field(
        description="Name of the camera that generated the alert.",
        examples=["Main Entrance"],
    )
    zone_name: str | None = Field(
        default=None,
        description="Name of the zone (if applicable).",
        examples=["Entry Gate Zone"],
    )
    rule_info: str | None = Field(
        default=None,
        description="Short description of the triggering rule.",
        examples=["Intrusion detection with 5s dwell threshold"],
    )
    acknowledged_by_name: str | None = Field(
        default=None,
        description="Full name of the user who acknowledged the alert.",
        examples=["Jane Doe"],
    )
    resolved_by_name: str | None = Field(
        default=None,
        description="Full name of the user who resolved the alert.",
        examples=["John Smith"],
    )


# ── Alert List with Summary ──────────────────────────────────────────────────


class AlertSeverityCount(BaseModel):
    """Count of alerts broken down by severity."""

    low: int = Field(default=0, description="Number of low-severity alerts.")
    medium: int = Field(default=0, description="Number of medium-severity alerts.")
    high: int = Field(default=0, description="Number of high-severity alerts.")
    critical: int = Field(default=0, description="Number of critical alerts.")


class AlertListResponse(BaseModel):
    """Paginated list of alerts with a severity summary."""

    status: str = Field(default="success", description="Response status.")
    data: list[AlertResponse] = Field(description="Alerts for the current page.")
    meta: dict[str, Any] = Field(
        description="Pagination metadata (page, page_size, total, total_pages).",
    )
    summary: AlertSeverityCount = Field(
        description="Aggregate alert counts by severity across the full result set.",
    )


# ── Alert Actions ─────────────────────────────────────────────────────────────


class AlertAcknowledge(BaseModel):
    """Request body for acknowledging an alert."""

    note: str | None = Field(
        default=None,
        max_length=2048,
        description="Optional note about the acknowledgement.",
        examples=["Reviewing CCTV footage."],
    )


class AlertResolve(BaseModel):
    """Request body for resolving (closing) an alert."""

    resolution_note: str = Field(
        min_length=1,
        max_length=4096,
        description="Explanation of how the alert was resolved.",
        examples=["Verified as a delivery person with valid access badge."],
    )
    is_false_positive: bool = Field(
        default=False,
        description="Mark the alert as a false positive. "
        "This feeds back into model accuracy tracking.",
    )


class AlertEscalateRequest(BaseModel):
    """Request body for escalating an alert to another user."""

    escalate_to_user_id: UUID = Field(
        description="ID of the user to escalate the alert to.",
    )
    reason: str = Field(
        min_length=1,
        max_length=2048,
        description="Reason for the escalation.",
        examples=["Potential security breach — needs manager approval."],
    )


# ── Alert Statistics ──────────────────────────────────────────────────────────


class AlertStats(BaseModel):
    """Aggregate alert statistics for dashboards and reporting."""

    total: int = Field(
        description="Total number of alerts in the queried period.",
        examples=[1520],
    )
    by_severity: dict[str, int] = Field(
        description="Alert count grouped by severity level.",
        examples=[{"low": 400, "medium": 600, "high": 350, "critical": 170}],
    )
    by_type: dict[str, int] = Field(
        description="Alert count grouped by rule type.",
        examples=[{"intrusion": 500, "loitering": 300, "ppe_violation": 200}],
    )
    by_camera: dict[str, int] = Field(
        description="Alert count grouped by camera name or ID.",
        examples=[{"Main Entrance": 800, "Parking Lot A": 400, "Server Room": 320}],
    )
    by_status: dict[str, int] = Field(
        default_factory=dict,
        description="Alert count grouped by lifecycle status.",
        examples=[{"new": 200, "acknowledged": 500, "resolved": 780, "escalated": 40}],
    )
    false_positive_rate: float = Field(
        default=0.0,
        ge=0.0,
        le=1.0,
        description="Fraction of resolved alerts marked as false positives.",
        examples=[0.12],
    )
