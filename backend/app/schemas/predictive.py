"""Pydantic schemas for predictive analytics endpoints.

Covers prediction points, footfall/alert/occupancy forecasts,
staff scheduling, risk assessment, trend analysis, and accuracy metrics.
"""

from datetime import date, datetime
from typing import Any, Optional
from uuid import UUID

from pydantic import BaseModel, ConfigDict, Field


# ---------------------------------------------------------------------------
# Base prediction point
# ---------------------------------------------------------------------------


class PredictionPoint(BaseModel):
    """A single predicted value at a specific timestamp."""

    timestamp: datetime = Field(description="Target prediction timestamp")
    value: float = Field(description="Predicted value")
    confidence_lower: Optional[float] = Field(
        default=None,
        description="Lower bound of the 80% confidence interval",
    )
    confidence_upper: Optional[float] = Field(
        default=None,
        description="Upper bound of the 80% confidence interval",
    )
    confidence_lower_95: Optional[float] = Field(
        default=None,
        description="Lower bound of the 95% confidence interval",
    )
    confidence_upper_95: Optional[float] = Field(
        default=None,
        description="Upper bound of the 95% confidence interval",
    )


# ---------------------------------------------------------------------------
# Footfall prediction
# ---------------------------------------------------------------------------


class FootfallPredictionResponse(BaseModel):
    """Footfall prediction result for a specific camera."""

    model_config = ConfigDict(from_attributes=True)

    camera_id: UUID
    camera_name: str = ""
    predictions: list[PredictionPoint] = Field(default_factory=list)
    accuracy_pct: Optional[float] = Field(
        default=None,
        description="Historical prediction accuracy (100 - MAPE) if available",
    )
    model_version: str = "hw_v1"
    generated_at: datetime = Field(default_factory=datetime.utcnow)


# ---------------------------------------------------------------------------
# Alert prediction
# ---------------------------------------------------------------------------


class AlertTypePrediction(BaseModel):
    """Predicted alert count for a specific alert type."""

    alert_type: str
    predicted_count: float = 0.0
    probability: float = Field(
        default=0.0,
        ge=0.0,
        le=1.0,
        description="Probability of at least one alert of this type",
    )
    hourly_breakdown: list[PredictionPoint] = Field(default_factory=list)


class AlertPredictionResponse(BaseModel):
    """Alert volume predictions grouped by alert type."""

    model_config = ConfigDict(from_attributes=True)

    org_id: UUID
    predictions: list[AlertTypePrediction] = Field(default_factory=list)
    total_predicted: float = 0.0
    hours_ahead: int = 24
    generated_at: datetime = Field(default_factory=datetime.utcnow)


# ---------------------------------------------------------------------------
# Occupancy prediction
# ---------------------------------------------------------------------------


class OccupancyPredictionResponse(BaseModel):
    """Occupancy prediction for a specific zone."""

    model_config = ConfigDict(from_attributes=True)

    zone_id: UUID
    zone_name: str = ""
    camera_id: Optional[UUID] = None
    predictions: list[PredictionPoint] = Field(default_factory=list)
    current_occupancy: Optional[int] = None
    max_capacity: Optional[int] = None
    peak_predicted: Optional[float] = None
    peak_time: Optional[datetime] = None
    generated_at: datetime = Field(default_factory=datetime.utcnow)


# ---------------------------------------------------------------------------
# Staff scheduling
# ---------------------------------------------------------------------------


class HourlyAllocation(BaseModel):
    """Recommended staff allocation for a single hour."""

    hour: int = Field(ge=0, le=23, description="Hour of the day (0-23)")
    recommended_staff: int = Field(ge=0)
    risk_level: str = Field(description="low, medium, high, or critical")
    predicted_alerts: float = 0.0
    predicted_footfall: float = 0.0
    notes: Optional[str] = None


class StaffScheduleRequest(BaseModel):
    """Request body for generating a staff schedule."""

    schedule_date: date = Field(alias="date", description="Date for which to generate the schedule")


class StaffScheduleResponse(BaseModel):
    """Complete staff schedule for a date."""

    model_config = ConfigDict(from_attributes=True, populate_by_name=True)

    org_id: UUID
    schedule_date: date = Field(alias="date")
    hourly_allocations: list[HourlyAllocation] = Field(default_factory=list)
    total_staff_hours: int = 0
    peak_hour: Optional[int] = None
    peak_staff: Optional[int] = None
    generated_at: datetime = Field(default_factory=datetime.utcnow)


# ---------------------------------------------------------------------------
# Risk forecast
# ---------------------------------------------------------------------------


class ContributingFactor(BaseModel):
    """A single factor contributing to a risk score."""

    factor: str = Field(description="Name of the contributing factor")
    weight: float = Field(description="Weight of this factor (0-1)")
    value: float = Field(description="Current value of the factor")
    description: str = ""


class RiskForecastItem(BaseModel):
    """Risk forecast for a single camera or zone."""

    model_config = ConfigDict(from_attributes=True)

    camera_id: Optional[UUID] = None
    camera_name: Optional[str] = None
    zone_id: Optional[UUID] = None
    zone_name: Optional[str] = None
    risk_level: str = Field(description="low, medium, high, or critical")
    risk_score: float = Field(ge=0.0, le=1.0)
    contributing_factors: list[ContributingFactor] = Field(default_factory=list)
    valid_until: Optional[datetime] = None


class RiskForecastResponse(BaseModel):
    """Risk forecasts for all cameras/zones in the organization."""

    model_config = ConfigDict(from_attributes=True)

    org_id: UUID
    forecasts: list[RiskForecastItem] = Field(default_factory=list)
    overall_risk_level: str = "low"
    overall_risk_score: float = 0.0
    generated_at: datetime = Field(default_factory=datetime.utcnow)


# ---------------------------------------------------------------------------
# Trend analysis
# ---------------------------------------------------------------------------


class TrendAnalysis(BaseModel):
    """Statistical trend analysis for a specific metric."""

    metric: str = Field(description="The metric analyzed (footfall, alerts, occupancy, etc.)")
    direction: str = Field(description="increasing, decreasing, or stable")
    slope: float = Field(description="Linear regression slope (units per day)")
    p_value: float = Field(
        ge=0.0,
        le=1.0,
        description="Mann-Kendall test p-value for trend significance",
    )
    confidence: str = Field(
        description="Confidence level: very_high (p<0.01), high (p<0.05), moderate (p<0.10), low"
    )
    description: str = Field(description="Human-readable trend summary")
    period_days: int = 30
    data_points: int = 0
    change_pct: Optional[float] = Field(
        default=None,
        description="Percentage change from start to end of the period",
    )


class TrendAnalysisResponse(BaseModel):
    """Trend analysis response wrapping one or more trends."""

    org_id: UUID
    trends: list[TrendAnalysis] = Field(default_factory=list)
    generated_at: datetime = Field(default_factory=datetime.utcnow)


# ---------------------------------------------------------------------------
# Prediction accuracy
# ---------------------------------------------------------------------------


class PredictionAccuracy(BaseModel):
    """Accuracy metrics for a single prediction type."""

    metric: str = Field(description="Prediction type (footfall, alerts, occupancy, risk)")
    mape: float = Field(description="Mean Absolute Percentage Error")
    accuracy_pct: float = Field(description="100 - MAPE, clamped to [0, 100]")
    predictions_count: int = Field(description="Number of predictions evaluated")
    period_days: int = Field(
        default=30,
        description="Lookback window for evaluation",
    )


class PredictionAccuracyResponse(BaseModel):
    """Accuracy metrics for all prediction types."""

    org_id: UUID
    metrics: list[PredictionAccuracy] = Field(default_factory=list)
    overall_accuracy_pct: Optional[float] = None
    evaluated_at: datetime = Field(default_factory=datetime.utcnow)
