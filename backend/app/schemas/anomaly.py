"""Pydantic schemas for anomaly detection endpoints."""

from __future__ import annotations


import math
from datetime import datetime
from enum import Enum
from typing import Any, Optional
from uuid import UUID

from pydantic import BaseModel, ConfigDict, Field


# -- Enums for request validation -------------------------------------------


class AnomalyTypeEnum(str, Enum):
    """Anomaly type filter values."""

    COUNT = "count"
    TEMPORAL = "temporal"
    SPATIAL = "spatial"
    BEHAVIORAL = "behavioral"
    FREQUENCY = "frequency"


class AnomalySeverityEnum(str, Enum):
    """Anomaly severity filter values."""

    INFO = "info"
    WARNING = "warning"
    CRITICAL = "critical"


class SensitivityLevel(str, Enum):
    """Detection sensitivity presets."""

    LOW = "low"
    MEDIUM = "medium"
    HIGH = "high"


# -- Response schemas -------------------------------------------------------


class AnomalyEventResponse(BaseModel):
    """Single anomaly event returned from the API."""

    model_config = ConfigDict(from_attributes=True)

    id: UUID
    org_id: UUID
    camera_id: UUID
    camera_name: str = ""
    zone_id: Optional[UUID] = None
    zone_name: Optional[str] = None
    anomaly_type: str
    severity: str
    confidence: float
    description: str
    baseline_value: Optional[float] = None
    observed_value: Optional[float] = None
    deviation_sigma: Optional[float] = None
    metadata_json: Optional[dict[str, Any]] = None
    thumbnail_path: Optional[str] = None
    is_acknowledged: bool = False
    acknowledged_by: Optional[UUID] = None
    acknowledged_at: Optional[datetime] = None
    created_at: datetime


class AnomalyEventList(BaseModel):
    """Paginated list of anomaly events."""

    status: str = "success"
    data: list[AnomalyEventResponse] = []
    meta: dict[str, Any] = Field(default_factory=dict)

    @classmethod
    def create(
        cls,
        items: list[AnomalyEventResponse],
        total: int,
        page: int,
        page_size: int,
    ) -> AnomalyEventList:
        """Convenience factory that builds the paginated response."""
        return cls(
            data=items,
            meta={
                "page": page,
                "page_size": page_size,
                "total": total,
                "total_pages": math.ceil(total / page_size) if page_size else 0,
            },
        )


class AnomalyStatsBreakdown(BaseModel):
    """Breakdown of anomaly stats by a single dimension."""

    label: str
    count: int = 0


class AnomalyTimelinePoint(BaseModel):
    """Single point on the anomaly timeline chart."""

    timestamp: str
    count: int = 0
    critical: int = 0
    warning: int = 0
    info: int = 0


class AnomalyStats(BaseModel):
    """Aggregated anomaly statistics."""

    total: int = 0
    by_type: list[AnomalyStatsBreakdown] = Field(default_factory=list)
    by_severity: list[AnomalyStatsBreakdown] = Field(default_factory=list)
    by_camera: list[AnomalyStatsBreakdown] = Field(default_factory=list)
    acknowledged_count: int = 0
    unacknowledged_count: int = 0
    avg_confidence: float = 0.0
    timeline: list[AnomalyTimelinePoint] = Field(default_factory=list)


class AnomalyStatsResponse(BaseModel):
    """Wrapper response for anomaly stats."""

    status: str = "success"
    data: AnomalyStats = Field(default_factory=AnomalyStats)


# -- Filter schemas ---------------------------------------------------------


class AnomalyFilters(BaseModel):
    """Query filters for anomaly events."""

    start_date: Optional[datetime] = Field(
        default=None,
        description="Inclusive lower bound (ISO-8601 with timezone).",
    )
    end_date: Optional[datetime] = Field(
        default=None,
        description="Inclusive upper bound (ISO-8601 with timezone).",
    )
    camera_id: Optional[UUID] = None
    zone_id: Optional[UUID] = None
    anomaly_type: Optional[AnomalyTypeEnum] = None
    severity: Optional[AnomalySeverityEnum] = None
    is_acknowledged: Optional[bool] = None


# -- Baseline schemas -------------------------------------------------------


class BaselineCameraStatus(BaseModel):
    """Baseline status for a single camera."""

    camera_id: UUID
    camera_name: str = ""
    zone_id: Optional[UUID] = None
    zone_name: Optional[str] = None
    baseline_type: str = "footfall"
    sample_count: int = 0
    is_stale: bool = True
    updated_at: Optional[datetime] = None


class BaselineStatusResponse(BaseModel):
    """Baseline health status across cameras."""

    status: str = "success"
    data: list[BaselineCameraStatus] = Field(default_factory=list)


class BaselineRebuildRequest(BaseModel):
    """Request body to trigger a baseline rebuild."""

    camera_ids: Optional[list[UUID]] = Field(
        default=None,
        description="List of camera IDs to rebuild baselines for. If null, rebuilds all.",
    )
    days: int = Field(
        default=30,
        ge=7,
        le=365,
        description="Number of historical days to use for baseline computation.",
    )


# -- Sensitivity schemas ----------------------------------------------------


SENSITIVITY_PRESETS: dict[str, dict[str, float]] = {
    "low": {
        "warning_sigma": 3.0,
        "critical_sigma": 4.0,
        "min_confidence": 0.7,
    },
    "medium": {
        "warning_sigma": 2.0,
        "critical_sigma": 3.0,
        "min_confidence": 0.5,
    },
    "high": {
        "warning_sigma": 1.5,
        "critical_sigma": 2.5,
        "min_confidence": 0.3,
    },
}


class SensitivityConfig(BaseModel):
    """Detection sensitivity configuration."""

    camera_id: Optional[UUID] = Field(
        default=None,
        description="Target camera. If null, applies org-wide.",
    )
    sensitivity: SensitivityLevel = Field(
        default=SensitivityLevel.MEDIUM,
        description="Sensitivity preset: low, medium, or high.",
    )
    warning_sigma: Optional[float] = Field(
        default=None,
        description="Custom warning threshold in standard deviations. Overrides preset.",
    )
    critical_sigma: Optional[float] = Field(
        default=None,
        description="Custom critical threshold in standard deviations. Overrides preset.",
    )
    min_confidence: Optional[float] = Field(
        default=None,
        ge=0.0,
        le=1.0,
        description="Minimum confidence score to emit an anomaly. Overrides preset.",
    )

    def resolve(self) -> dict[str, float]:
        """Resolve the effective thresholds by merging preset with overrides."""
        preset = SENSITIVITY_PRESETS[self.sensitivity.value].copy()
        if self.warning_sigma is not None:
            preset["warning_sigma"] = self.warning_sigma
        if self.critical_sigma is not None:
            preset["critical_sigma"] = self.critical_sigma
        if self.min_confidence is not None:
            preset["min_confidence"] = self.min_confidence
        return preset


class SensitivityConfigResponse(BaseModel):
    """Response after updating sensitivity configuration."""

    status: str = "success"
    data: dict[str, Any] = Field(default_factory=dict)
    message: str = ""


# -- Acknowledge schema -----------------------------------------------------


class AcknowledgeRequest(BaseModel):
    """Request body for acknowledging an anomaly event."""

    notes: Optional[str] = Field(
        default=None,
        max_length=2048,
        description="Optional notes about the acknowledgement.",
    )
