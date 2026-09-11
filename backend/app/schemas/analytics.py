"""Pydantic schemas for analytics endpoints."""

from __future__ import annotations


from datetime import datetime, date
from typing import Optional, Any
from uuid import UUID

from pydantic import BaseModel, Field, ConfigDict


class FootfallDataPoint(BaseModel):
    """Single footfall data point."""

    timestamp: datetime
    entries_count: int = 0
    exits_count: int = 0
    occupancy_estimate: Optional[int] = None


class FootfallResponse(BaseModel):
    """Footfall data for a camera/zone."""

    model_config = ConfigDict(from_attributes=True)

    camera_id: UUID
    camera_name: str = ""
    zone_id: Optional[UUID] = None
    zone_name: Optional[str] = None
    data: list[FootfallDataPoint] = []
    total_entries: int = 0
    total_exits: int = 0


class FootfallSummary(BaseModel):
    """Aggregated footfall summary across cameras/zones."""

    total_entries: int = 0
    total_exits: int = 0
    net_flow: int = 0
    peak_hour: Optional[str] = None
    peak_count: int = 0
    avg_hourly_entries: float = 0.0
    avg_hourly_exits: float = 0.0
    hourly_data: list[FootfallDataPoint] = []
    daily_comparison: Optional[dict[str, Any]] = None


class HeatmapRequest(BaseModel):
    """Request to generate a heatmap."""

    camera_id: UUID
    start_time: datetime
    end_time: datetime
    resolution: str = Field(default="640x480", description="Output resolution WxH")


class HeatmapResponse(BaseModel):
    """Heatmap generation response."""

    image_url: str
    camera_id: UUID
    camera_name: str = ""
    start_time: datetime
    end_time: datetime
    total_detections: int = 0


class DwellTimeResponse(BaseModel):
    """Dwell time analytics for a zone."""

    zone_id: UUID
    zone_name: str = ""
    camera_id: UUID
    avg_dwell_seconds: float = 0.0
    max_dwell_seconds: float = 0.0
    min_dwell_seconds: float = 0.0
    median_dwell_seconds: float = 0.0
    total_visits: int = 0
    period_start: datetime
    period_end: datetime


class EmotionDataPoint(BaseModel):
    """Emotion data for a time period."""

    timestamp: datetime
    emotion_distribution: dict[str, float] = Field(
        default_factory=dict,
        description="Emotion name to count/percentage mapping",
    )
    avg_sentiment_score: float = 0.0
    total_faces: int = 0


class EmotionSummary(BaseModel):
    """Aggregated emotion analytics."""

    camera_id: Optional[UUID] = None
    zone_id: Optional[UUID] = None
    period_start: datetime
    period_end: datetime
    overall_distribution: dict[str, float] = Field(
        default_factory=dict,
        description="Overall emotion distribution percentages",
    )
    avg_sentiment_score: float = 0.0
    total_faces_analyzed: int = 0
    hourly_data: list[EmotionDataPoint] = []
    peak_positive_hour: Optional[str] = None
    peak_negative_hour: Optional[str] = None


class OccupancyResponse(BaseModel):
    """Current zone occupancy."""

    zone_id: UUID
    zone_name: str = ""
    camera_id: UUID
    current_count: int = 0
    max_capacity: Optional[int] = None
    utilization_pct: Optional[float] = None
    last_updated: Optional[datetime] = None


class PatternDataPoint(BaseModel):
    """Single data point in pattern analysis."""

    label: str = Field(description="Time label, e.g., 'Monday 09:00'")
    value: float = 0.0
    is_anomaly: bool = False
    expected_range: Optional[tuple[float, float]] = None


class AnomalyInfo(BaseModel):
    """Detected anomaly in patterns."""

    timestamp: datetime
    metric: str
    actual_value: float
    expected_value: float
    deviation_pct: float
    description: str = ""


class PatternResponse(BaseModel):
    """Pattern recognition results."""

    metric: str = Field(description="footfall, alerts, vehicles, etc.")
    period: str = Field(description="daily, weekly, monthly")
    camera_id: Optional[UUID] = None
    zone_id: Optional[UUID] = None
    pattern_data: list[PatternDataPoint] = []
    anomalies: list[AnomalyInfo] = []
    summary: str = ""


class DashboardStats(BaseModel):
    """Dashboard summary statistics."""

    total_cameras: int = 0
    cameras_online: int = 0
    cameras_offline: int = 0
    cameras_degraded: int = 0
    total_alerts_today: int = 0
    critical_alerts_today: int = 0
    high_alerts_today: int = 0
    medium_alerts_today: int = 0
    low_alerts_today: int = 0
    total_persons_enrolled: int = 0
    total_vehicles_registered: int = 0
    active_rules: int = 0
    total_footfall_today: int = 0
    ppe_compliance_pct: Optional[float] = None
    alerts_trend: list[dict[str, Any]] = Field(
        default_factory=list,
        description="Last 7 days alert counts [{date, count}]",
    )
    camera_health: list[dict[str, Any]] = Field(
        default_factory=list,
        description="Camera health summary [{camera_id, name, status}]",
    )
    recent_events: list[dict[str, Any]] = Field(
        default_factory=list,
        description="Last 20 events across all cameras",
    )


class ReportRequest(BaseModel):
    """Report generation request."""

    report_type: str = Field(
        description="security_summary, attendance, ppe_compliance, vehicle_log, footfall, custom"
    )
    start_date: date
    end_date: date
    camera_ids: Optional[list[UUID]] = None
    zone_ids: Optional[list[UUID]] = None
    format: str = Field(default="pdf", description="pdf, csv, xlsx")
    include_charts: bool = True
    include_snapshots: bool = False
    title: Optional[str] = None


class ReportResponse(BaseModel):
    """Report status and details."""

    model_config = ConfigDict(from_attributes=True)

    id: UUID
    report_type: str
    status: str = Field(description="pending, generating, completed, failed")
    title: Optional[str] = None
    format: str = "pdf"
    file_size_bytes: Optional[int] = None
    download_url: Optional[str] = None
    error_message: Optional[str] = None
    created_at: datetime
    completed_at: Optional[datetime] = None


class PPEComplianceResponse(BaseModel):
    """PPE compliance analytics."""

    zone_id: Optional[UUID] = None
    zone_name: Optional[str] = None
    period_start: datetime
    period_end: datetime
    total_checks: int = 0
    compliant_count: int = 0
    violation_count: int = 0
    compliance_pct: float = 0.0
    violations_by_type: dict[str, int] = Field(
        default_factory=dict,
        description="Violation type to count: {no_helmet: 12, no_vest: 5}",
    )
    hourly_compliance: list[dict[str, Any]] = Field(
        default_factory=list,
        description="Hourly compliance data [{hour, compliance_pct, violations}]",
    )
    top_violators: list[dict[str, Any]] = Field(
        default_factory=list,
        description="Most frequent violators [{person_name, count, last_violation}]",
    )
