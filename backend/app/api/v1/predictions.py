"""
Predictive Analytics API endpoints.

Provides footfall/alert/occupancy forecasting, risk assessment,
trend detection, intelligent staff scheduling, and accuracy metrics.
"""

from __future__ import annotations

import uuid
from datetime import date, datetime, timezone

import structlog
from fastapi import APIRouter, Depends, HTTPException, Query, status
from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession

from app.database import get_db_session
from app.middleware.auth import JWTBearer, TokenPayload
from app.models.camera import Camera
from app.models.prediction import StaffSchedule
from app.models.user import User
from app.models.zone import Zone
from app.schemas.common import ErrorResponse, SuccessResponse
from app.schemas.predictive import (
    AlertPredictionResponse,
    FootfallPredictionResponse,
    OccupancyPredictionResponse,
    PredictionAccuracyResponse,
    RiskForecastResponse,
    StaffScheduleRequest,
    StaffScheduleResponse,
    TrendAnalysisResponse,
)
from app.services.predictive_service import PredictiveService

logger = structlog.stdlib.get_logger(__name__)

router = APIRouter()


# ---------------------------------------------------------------------------
# Helpers
# ---------------------------------------------------------------------------


async def _get_current_user(
    token: TokenPayload = Depends(JWTBearer()),
    db: AsyncSession = Depends(get_db_session),
) -> User:
    """Extract and validate the current user from the JWT token."""
    result = await db.execute(
        select(User).where(User.id == uuid.UUID(token.sub), User.is_active.is_(True))
    )
    user = result.scalars().first()
    if not user:
        raise HTTPException(status_code=401, detail="User not found or deactivated.")
    return user


# ---------------------------------------------------------------------------
# GET /predictions/footfall/{camera_id}
# ---------------------------------------------------------------------------


@router.get(
    "/footfall/{camera_id}",
    response_model=SuccessResponse,
    summary="Predict footfall for a camera",
    responses={404: {"model": ErrorResponse}},
)
async def predict_footfall(
    camera_id: uuid.UUID,
    hours_ahead: int = Query(
        default=24,
        ge=1,
        le=168,
        description="Number of hours to forecast (1-168)",
    ),
    user: User = Depends(_get_current_user),
    db: AsyncSession = Depends(get_db_session),
) -> dict:
    """Generate footfall predictions for a specific camera.

    Uses Holt-Winters triple exponential smoothing on 30 days of
    historical hourly footfall data.  Returns predicted values with
    80% and 95% confidence intervals.
    """
    # Verify camera belongs to org
    cam_result = await db.execute(
        select(Camera).where(Camera.id == camera_id, Camera.org_id == user.org_id)
    )
    camera = cam_result.scalars().first()
    if not camera:
        raise HTTPException(status_code=404, detail="Camera not found.")

    try:
        result = await PredictiveService.predict_footfall(
            db, user.org_id, camera_id, hours_ahead
        )
        result["camera_name"] = camera.name
        return {"status": "success", "data": result}
    except Exception as exc:
        logger.error("Footfall prediction failed", error=str(exc), camera_id=str(camera_id))
        raise HTTPException(
            status_code=500,
            detail=f"Prediction failed: {str(exc)}",
        )


# ---------------------------------------------------------------------------
# GET /predictions/alerts
# ---------------------------------------------------------------------------


@router.get(
    "/alerts",
    response_model=SuccessResponse,
    summary="Predict alert volumes by type",
)
async def predict_alerts(
    hours_ahead: int = Query(
        default=24,
        ge=1,
        le=168,
        description="Number of hours to forecast (1-168)",
    ),
    user: User = Depends(_get_current_user),
    db: AsyncSession = Depends(get_db_session),
) -> dict:
    """Predict future alert volumes grouped by alert type.

    Uses historical frequency analysis with day-of-week and
    hour-of-day seasonality patterns.
    """
    try:
        result = await PredictiveService.predict_alerts(db, user.org_id, hours_ahead)
        return {"status": "success", "data": result}
    except Exception as exc:
        logger.error("Alert prediction failed", error=str(exc))
        raise HTTPException(
            status_code=500,
            detail=f"Prediction failed: {str(exc)}",
        )


# ---------------------------------------------------------------------------
# GET /predictions/occupancy/{zone_id}
# ---------------------------------------------------------------------------


@router.get(
    "/occupancy/{zone_id}",
    response_model=SuccessResponse,
    summary="Predict occupancy for a zone",
    responses={404: {"model": ErrorResponse}},
)
async def predict_occupancy(
    zone_id: uuid.UUID,
    hours_ahead: int = Query(
        default=8,
        ge=1,
        le=48,
        description="Number of hours to forecast (1-48)",
    ),
    user: User = Depends(_get_current_user),
    db: AsyncSession = Depends(get_db_session),
) -> dict:
    """Generate occupancy predictions for a specific zone.

    Uses Holt-Winters smoothing on historical occupancy data.
    """
    # Verify zone belongs to org
    zone_result = await db.execute(
        select(Zone)
        .join(Camera, Zone.camera_id == Camera.id)
        .where(Zone.id == zone_id, Camera.org_id == user.org_id)
    )
    zone = zone_result.scalars().first()
    if not zone:
        raise HTTPException(status_code=404, detail="Zone not found.")

    try:
        result = await PredictiveService.predict_occupancy(
            db, user.org_id, zone_id, hours_ahead
        )
        return {"status": "success", "data": result}
    except Exception as exc:
        logger.error("Occupancy prediction failed", error=str(exc), zone_id=str(zone_id))
        raise HTTPException(
            status_code=500,
            detail=f"Prediction failed: {str(exc)}",
        )


# ---------------------------------------------------------------------------
# GET /predictions/risk-forecast
# ---------------------------------------------------------------------------


@router.get(
    "/risk-forecast",
    response_model=SuccessResponse,
    summary="Get risk scores for all cameras/zones",
)
async def risk_forecast(
    user: User = Depends(_get_current_user),
    db: AsyncSession = Depends(get_db_session),
) -> dict:
    """Compute and return risk forecasts for all cameras.

    Each camera receives a risk score (0-1) and categorical level
    (low/medium/high/critical) based on alert history, anomaly
    baselines, and camera health status.
    """
    try:
        result = await PredictiveService.get_risk_forecast(db, user.org_id)
        return {"status": "success", "data": result}
    except Exception as exc:
        logger.error("Risk forecast failed", error=str(exc))
        raise HTTPException(
            status_code=500,
            detail=f"Risk forecast failed: {str(exc)}",
        )


# ---------------------------------------------------------------------------
# GET /predictions/trends
# ---------------------------------------------------------------------------


@router.get(
    "/trends",
    response_model=SuccessResponse,
    summary="Detect trends for a metric",
)
async def detect_trends(
    metric: str = Query(
        default="footfall",
        description="Metric to analyse: footfall, alerts, or occupancy",
    ),
    period_days: int = Query(
        default=30,
        ge=7,
        le=365,
        description="Number of days to analyse (7-365)",
    ),
    user: User = Depends(_get_current_user),
    db: AsyncSession = Depends(get_db_session),
) -> dict:
    """Perform trend analysis on the specified metric.

    Combines linear regression for slope/direction with the Mann-Kendall
    non-parametric test for statistical significance.
    """
    allowed_metrics = {"footfall", "alerts", "occupancy"}
    if metric not in allowed_metrics:
        raise HTTPException(
            status_code=400,
            detail=f"Invalid metric. Allowed: {', '.join(allowed_metrics)}",
        )

    try:
        result = await PredictiveService.detect_trends(
            db, user.org_id, metric, period_days
        )
        return {"status": "success", "data": result}
    except Exception as exc:
        logger.error("Trend detection failed", error=str(exc))
        raise HTTPException(
            status_code=500,
            detail=f"Trend analysis failed: {str(exc)}",
        )


# ---------------------------------------------------------------------------
# POST /predictions/staff-schedule
# ---------------------------------------------------------------------------


@router.post(
    "/staff-schedule",
    response_model=SuccessResponse,
    summary="Generate staff schedule for a date",
    status_code=status.HTTP_201_CREATED,
)
async def generate_staff_schedule(
    body: StaffScheduleRequest,
    user: User = Depends(_get_current_user),
    db: AsyncSession = Depends(get_db_session),
) -> dict:
    """Generate an optimal staff schedule for the specified date.

    Based on predicted alert volumes and footfall patterns, recommends
    hourly staff allocations with risk-level indicators.
    """
    try:
        result = await PredictiveService.generate_staff_schedule(
            db, user.org_id, body.date, user_id=user.id
        )
        return {"status": "success", "data": result}
    except Exception as exc:
        logger.error("Staff schedule generation failed", error=str(exc))
        raise HTTPException(
            status_code=500,
            detail=f"Schedule generation failed: {str(exc)}",
        )


# ---------------------------------------------------------------------------
# GET /predictions/staff-schedule/{date}
# ---------------------------------------------------------------------------


@router.get(
    "/staff-schedule/{schedule_date}",
    response_model=SuccessResponse,
    summary="Get generated staff schedule for a date",
    responses={404: {"model": ErrorResponse}},
)
async def get_staff_schedule(
    schedule_date: date,
    user: User = Depends(_get_current_user),
    db: AsyncSession = Depends(get_db_session),
) -> dict:
    """Retrieve a previously generated staff schedule for a specific date."""
    result = await db.execute(
        select(StaffSchedule)
        .where(
            StaffSchedule.org_id == user.org_id,
            StaffSchedule.date == schedule_date,
        )
        .order_by(StaffSchedule.hour)
    )
    schedules = result.scalars().all()

    if not schedules:
        raise HTTPException(
            status_code=404,
            detail=f"No staff schedule found for {schedule_date.isoformat()}",
        )

    hourly_allocations = []
    total_staff_hours = 0
    peak_staff = 0
    peak_hour = None

    for s in schedules:
        hourly_allocations.append({
            "hour": s.hour,
            "recommended_staff": s.recommended_staff,
            "risk_level": s.risk_level.value if hasattr(s.risk_level, "value") else s.risk_level,
            "predicted_alerts": s.predicted_alerts,
            "predicted_footfall": s.predicted_footfall,
            "notes": s.notes,
        })
        total_staff_hours += s.recommended_staff
        if s.recommended_staff > peak_staff:
            peak_staff = s.recommended_staff
            peak_hour = s.hour

    return {
        "status": "success",
        "data": {
            "org_id": str(user.org_id),
            "date": schedule_date.isoformat(),
            "hourly_allocations": hourly_allocations,
            "total_staff_hours": total_staff_hours,
            "peak_hour": peak_hour,
            "peak_staff": peak_staff,
        },
    }


# ---------------------------------------------------------------------------
# GET /predictions/accuracy
# ---------------------------------------------------------------------------


@router.get(
    "/accuracy",
    response_model=SuccessResponse,
    summary="Get prediction accuracy metrics",
)
async def prediction_accuracy(
    user: User = Depends(_get_current_user),
    db: AsyncSession = Depends(get_db_session),
) -> dict:
    """Retrieve prediction accuracy metrics (MAPE) for all prediction types.

    Compares past predictions with actual observed values and returns
    the mean absolute percentage error per prediction type.
    """
    try:
        result = await PredictiveService.get_predictions_accuracy(db, user.org_id)
        return {"status": "success", "data": result}
    except Exception as exc:
        logger.error("Accuracy computation failed", error=str(exc))
        raise HTTPException(
            status_code=500,
            detail=f"Accuracy computation failed: {str(exc)}",
        )
