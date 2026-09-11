"""
Predictive Analytics Service for VisionAI.

Provides time-series forecasting (Holt-Winters triple exponential smoothing),
alert volume prediction, occupancy forecasting, intelligent staff scheduling,
trend detection (linear regression + Mann-Kendall test), risk forecasting,
and accuracy evaluation.

All statistical computations use numpy -- no scikit-learn dependency.
"""

from __future__ import annotations

import math
import uuid
from collections import defaultdict
from datetime import date, datetime, timedelta, timezone
from typing import Any, Optional

import numpy as np
import structlog
from sqlalchemy import and_, delete, func, select, text
from sqlalchemy.ext.asyncio import AsyncSession

from app.models.alert import Alert
from app.models.analytics import FootfallRecord
from app.models.anomaly import AnomalyBaseline, AnomalyEvent
from app.models.camera import Camera
from app.models.prediction import (
    Prediction,
    PredictionType,
    RiskForecast,
    RiskLevel,
    StaffSchedule,
)
from app.models.zone import Zone

logger = structlog.stdlib.get_logger(__name__)


# ---------------------------------------------------------------------------
# Holt-Winters Triple Exponential Smoothing (additive seasonality)
# ---------------------------------------------------------------------------


def _holt_winters_additive(
    series: np.ndarray,
    season_length: int = 24,
    alpha: float = 0.3,
    beta: float = 0.1,
    gamma: float = 0.2,
    n_forecast: int = 24,
) -> tuple[np.ndarray, np.ndarray, np.ndarray, np.ndarray]:
    """Holt-Winters triple exponential smoothing with additive seasonality.

    Parameters
    ----------
    series : np.ndarray
        Historical time-series values (at least 2 * season_length).
    season_length : int
        Seasonal period (default 24 for hourly data with daily cycle).
    alpha : float
        Level smoothing parameter (0 < alpha < 1).
    beta : float
        Trend smoothing parameter (0 < beta < 1).
    gamma : float
        Seasonal smoothing parameter (0 < gamma < 1).
    n_forecast : int
        Number of periods to forecast ahead.

    Returns
    -------
    forecast : np.ndarray
        Predicted values for the next ``n_forecast`` periods.
    lower_80 : np.ndarray
        Lower bound of the 80% confidence interval.
    upper_80 : np.ndarray
        Upper bound of the 80% confidence interval.
    lower_95 : np.ndarray
        Lower bound of the 95% confidence interval.
    upper_95 : np.ndarray
        Upper bound of the 95% confidence interval.
    """
    n = len(series)
    if n < 2 * season_length:
        # Not enough data; fall back to naive seasonal forecast
        return _naive_seasonal_forecast(series, season_length, n_forecast)

    # Initialise level, trend, and seasonal components
    # Level: average of first season
    level = np.mean(series[:season_length])
    # Trend: average slope across two seasons
    trend = (np.mean(series[season_length: 2 * season_length]) - np.mean(series[:season_length])) / season_length

    # Seasonal indices: average deviation from level for each position
    seasonal = np.zeros(season_length)
    for i in range(season_length):
        vals = series[i::season_length]
        seasonal[i] = np.mean(vals) - np.mean(series[:season_length])

    # Store fitted values for residual computation
    fitted = np.zeros(n)
    fitted[:season_length] = level + trend * np.arange(season_length) + seasonal

    residuals = []

    # Apply Holt-Winters recursion
    for t in range(season_length, n):
        y = series[t]
        season_idx = t % season_length
        prev_seasonal = seasonal[season_idx]

        new_level = alpha * (y - prev_seasonal) + (1 - alpha) * (level + trend)
        new_trend = beta * (new_level - level) + (1 - beta) * trend
        new_seasonal = gamma * (y - new_level) + (1 - gamma) * prev_seasonal

        fitted[t] = new_level + new_trend + new_seasonal
        residuals.append(y - fitted[t])

        level = new_level
        trend = new_trend
        seasonal[season_idx] = new_seasonal

    # Forecast
    forecast = np.zeros(n_forecast)
    for h in range(1, n_forecast + 1):
        season_idx = (n + h - 1) % season_length
        forecast[h - 1] = level + h * trend + seasonal[season_idx]

    # Confidence intervals based on residual standard deviation
    if len(residuals) > 1:
        sigma = float(np.std(residuals, ddof=1))
    else:
        sigma = float(np.std(series)) if np.std(series) > 0 else 1.0

    # Widen confidence intervals as forecast horizon increases
    horizons = np.arange(1, n_forecast + 1)
    widths = sigma * np.sqrt(horizons)

    z_80 = 1.2816  # scipy.stats.norm.ppf(0.90)
    z_95 = 1.9600  # scipy.stats.norm.ppf(0.975)

    lower_80 = np.maximum(forecast - z_80 * widths, 0)
    upper_80 = forecast + z_80 * widths
    lower_95 = np.maximum(forecast - z_95 * widths, 0)
    upper_95 = forecast + z_95 * widths

    return forecast, lower_80, upper_80, lower_95, upper_95


def _naive_seasonal_forecast(
    series: np.ndarray,
    season_length: int,
    n_forecast: int,
) -> tuple[np.ndarray, np.ndarray, np.ndarray, np.ndarray, np.ndarray]:
    """Fall-back forecast using simple seasonal repetition.

    Used when there is insufficient data for Holt-Winters.
    """
    n = len(series)
    forecast = np.zeros(n_forecast)
    for h in range(n_forecast):
        idx = (n + h) % max(n, 1)
        forecast[h] = series[idx % n] if n > 0 else 0.0

    sigma = float(np.std(series)) if len(series) > 1 else 1.0
    horizons = np.arange(1, n_forecast + 1)
    widths = sigma * np.sqrt(horizons / max(n, 1))

    lower_80 = np.maximum(forecast - 1.2816 * widths, 0)
    upper_80 = forecast + 1.2816 * widths
    lower_95 = np.maximum(forecast - 1.9600 * widths, 0)
    upper_95 = forecast + 1.9600 * widths

    return forecast, lower_80, upper_80, lower_95, upper_95


# ---------------------------------------------------------------------------
# Mann-Kendall trend test
# ---------------------------------------------------------------------------


def _mann_kendall_test(series: np.ndarray) -> tuple[float, float, str]:
    """Mann-Kendall trend test for monotonic trend detection.

    Parameters
    ----------
    series : np.ndarray
        Time-series values.

    Returns
    -------
    tau : float
        Kendall's tau statistic (-1 to 1).
    p_value : float
        Two-sided p-value for the test.
    trend : str
        ``"increasing"``, ``"decreasing"``, or ``"stable"``.
    """
    n = len(series)
    if n < 4:
        return 0.0, 1.0, "stable"

    # Compute S statistic
    s = 0
    for k in range(n - 1):
        for j in range(k + 1, n):
            diff = series[j] - series[k]
            if diff > 0:
                s += 1
            elif diff < 0:
                s -= 1

    # Compute variance
    # Count ties
    unique, counts = np.unique(series, return_counts=True)
    tie_sum = 0
    for t in counts:
        if t > 1:
            tie_sum += t * (t - 1) * (2 * t + 5)

    var_s = (n * (n - 1) * (2 * n + 5) - tie_sum) / 18.0

    if var_s == 0:
        return 0.0, 1.0, "stable"

    # Compute Z statistic
    if s > 0:
        z = (s - 1) / math.sqrt(var_s)
    elif s < 0:
        z = (s + 1) / math.sqrt(var_s)
    else:
        z = 0.0

    # Two-sided p-value using normal approximation
    p_value = 2.0 * _normal_cdf(-abs(z))

    # Kendall's tau
    n_pairs = n * (n - 1) / 2
    tau = s / n_pairs if n_pairs > 0 else 0.0

    # Determine trend direction
    if p_value < 0.05:
        trend = "increasing" if s > 0 else "decreasing"
    else:
        trend = "stable"

    return tau, p_value, trend


def _normal_cdf(x: float) -> float:
    """Cumulative distribution function for the standard normal distribution.

    Uses the Abramowitz and Stegun approximation (error < 7.5e-8).
    """
    if x < -8.0:
        return 0.0
    if x > 8.0:
        return 1.0
    a1, a2, a3, a4, a5 = (
        0.254829592,
        -0.284496736,
        1.421413741,
        -1.453152027,
        1.061405429,
    )
    p = 0.3275911
    sign = 1.0 if x >= 0 else -1.0
    x_abs = abs(x)
    t = 1.0 / (1.0 + p * x_abs)
    y = 1.0 - (((((a5 * t + a4) * t) + a3) * t + a2) * t + a1) * t * math.exp(-x_abs * x_abs / 2.0)
    return 0.5 * (1.0 + sign * y)


# ---------------------------------------------------------------------------
# PredictiveService
# ---------------------------------------------------------------------------


class PredictiveService:
    """Predictive analytics engine for VisionAI.

    All methods are async and accept an ``AsyncSession`` as the first
    parameter (following the repository/service pattern used throughout
    the codebase).
    """

    # ── Footfall Prediction ────────────────────────────────────────────

    @staticmethod
    async def predict_footfall(
        db: AsyncSession,
        org_id: uuid.UUID,
        camera_id: uuid.UUID,
        hours_ahead: int = 24,
    ) -> dict[str, Any]:
        """Predict footfall for a camera using Holt-Winters smoothing.

        Loads 30 days of historical hourly footfall data, applies triple
        exponential smoothing with a 24-hour seasonal period, and returns
        predicted values with 80% and 95% confidence intervals.
        """
        log = logger.bind(org_id=str(org_id), camera_id=str(camera_id))
        now = datetime.now(timezone.utc)
        lookback = now - timedelta(days=30)

        # Load historical hourly entries
        result = await db.execute(
            select(
                func.date_trunc("hour", FootfallRecord.timestamp).label("hour"),
                func.sum(FootfallRecord.entries_count).label("total_entries"),
            )
            .join(Camera, FootfallRecord.camera_id == Camera.id)
            .where(
                Camera.org_id == org_id,
                FootfallRecord.camera_id == camera_id,
                FootfallRecord.timestamp >= lookback,
            )
            .group_by("hour")
            .order_by("hour")
        )
        rows = result.all()

        if len(rows) < 24:
            log.warning("Insufficient footfall data for prediction", data_points=len(rows))
            return {
                "camera_id": str(camera_id),
                "predictions": [],
                "accuracy_pct": None,
                "model_version": "hw_v1",
                "message": "Insufficient historical data (need at least 24 hourly data points)",
            }

        # Build a dense hourly time-series, filling gaps with 0
        hour_map: dict[datetime, float] = {}
        for row in rows:
            hour_map[row.hour] = float(row.total_entries or 0)

        min_hour = min(hour_map.keys())
        max_hour = max(hour_map.keys())
        total_hours = int((max_hour - min_hour).total_seconds() / 3600) + 1
        series = np.zeros(total_hours)
        for i in range(total_hours):
            h = min_hour + timedelta(hours=i)
            series[i] = hour_map.get(h, 0.0)

        # Run Holt-Winters
        forecast, lower_80, upper_80, lower_95, upper_95 = _holt_winters_additive(
            series, season_length=24, n_forecast=hours_ahead
        )

        # Build prediction points
        predictions = []
        base_time = max_hour + timedelta(hours=1)
        for i in range(hours_ahead):
            ts = base_time + timedelta(hours=i)
            predictions.append({
                "timestamp": ts.isoformat(),
                "value": round(max(float(forecast[i]), 0), 2),
                "confidence_lower": round(max(float(lower_80[i]), 0), 2),
                "confidence_upper": round(float(upper_80[i]), 2),
                "confidence_lower_95": round(max(float(lower_95[i]), 0), 2),
                "confidence_upper_95": round(float(upper_95[i]), 2),
            })

        # Persist predictions to database
        for pred in predictions:
            db.add(Prediction(
                org_id=org_id,
                prediction_type=PredictionType.FOOTFALL,
                camera_id=camera_id,
                target_timestamp=datetime.fromisoformat(pred["timestamp"]),
                predicted_value=pred["value"],
                confidence_lower=pred["confidence_lower"],
                confidence_upper=pred["confidence_upper"],
                confidence_lower_95=pred["confidence_lower_95"],
                confidence_upper_95=pred["confidence_upper_95"],
                model_version="hw_v1",
            ))

        # Fetch historical accuracy
        accuracy = await PredictiveService._compute_mape(
            db, org_id, PredictionType.FOOTFALL, camera_id=camera_id
        )

        log.info("Footfall prediction generated", hours_ahead=hours_ahead, points=len(predictions))

        return {
            "camera_id": str(camera_id),
            "predictions": predictions,
            "accuracy_pct": round(100 - accuracy, 2) if accuracy is not None else None,
            "model_version": "hw_v1",
        }

    # ── Alert Prediction ───────────────────────────────────────────────

    @staticmethod
    async def predict_alerts(
        db: AsyncSession,
        org_id: uuid.UUID,
        hours_ahead: int = 24,
    ) -> dict[str, Any]:
        """Predict alert volume by type using historical frequency analysis.

        Analyses 30-day alert history considering day-of-week and
        hour-of-day seasonality, and returns predicted counts per alert
        type with probabilities.
        """
        log = logger.bind(org_id=str(org_id))
        now = datetime.now(timezone.utc)
        lookback = now - timedelta(days=30)

        # Get alert counts by type and hour
        result = await db.execute(
            select(
                Alert.alert_type,
                func.date_trunc("hour", Alert.created_at).label("hour"),
                func.count().label("cnt"),
            )
            .where(
                Alert.org_id == org_id,
                Alert.created_at >= lookback,
            )
            .group_by(Alert.alert_type, "hour")
            .order_by("hour")
        )
        rows = result.all()

        if not rows:
            log.warning("No historical alert data for prediction")
            return {
                "org_id": str(org_id),
                "predictions": [],
                "total_predicted": 0.0,
                "hours_ahead": hours_ahead,
            }

        # Organise by alert type
        type_series: dict[str, dict[datetime, float]] = defaultdict(dict)
        for row in rows:
            alert_type_val = row.alert_type.value if hasattr(row.alert_type, "value") else str(row.alert_type)
            type_series[alert_type_val][row.hour] = float(row.cnt)

        predictions_by_type = []
        total_predicted = 0.0

        for alert_type, hour_counts in type_series.items():
            if not hour_counts:
                continue

            # Build hourly profile: average count per hour-of-day
            hourly_profile = np.zeros(24)
            hourly_counts_list: dict[int, list[float]] = defaultdict(list)

            for h, cnt in hour_counts.items():
                hourly_counts_list[h.hour].append(cnt)

            for hour_idx in range(24):
                vals = hourly_counts_list.get(hour_idx, [0.0])
                hourly_profile[hour_idx] = np.mean(vals) if vals else 0.0

            # Day-of-week scaling factor
            dow_profile = np.ones(7)
            dow_counts: dict[int, list[float]] = defaultdict(list)
            for h, cnt in hour_counts.items():
                dow_counts[h.weekday()].append(cnt)

            overall_mean = np.mean(list(hour_counts.values())) if hour_counts else 1.0
            for dow in range(7):
                vals = dow_counts.get(dow, [])
                if vals:
                    dow_profile[dow] = np.mean(vals) / max(overall_mean, 0.01)

            # Generate hourly predictions
            hourly_predictions = []
            type_total = 0.0
            base_time = now.replace(minute=0, second=0, microsecond=0) + timedelta(hours=1)

            for h_offset in range(hours_ahead):
                future_time = base_time + timedelta(hours=h_offset)
                hour_of_day = future_time.hour
                day_of_week = future_time.weekday()

                predicted = hourly_profile[hour_of_day] * dow_profile[day_of_week]
                predicted = max(predicted, 0.0)
                type_total += predicted

                hourly_predictions.append({
                    "timestamp": future_time.isoformat(),
                    "value": round(predicted, 2),
                    "confidence_lower": round(max(predicted * 0.5, 0), 2),
                    "confidence_upper": round(predicted * 1.8, 2),
                    "confidence_lower_95": round(max(predicted * 0.25, 0), 2),
                    "confidence_upper_95": round(predicted * 2.2, 2),
                })

            # Probability of at least one alert in the forecast period
            # Use Poisson approximation: P(X >= 1) = 1 - e^(-lambda)
            prob_at_least_one = 1.0 - math.exp(-type_total) if type_total > 0 else 0.0

            predictions_by_type.append({
                "alert_type": alert_type,
                "predicted_count": round(type_total, 2),
                "probability": round(min(prob_at_least_one, 1.0), 4),
                "hourly_breakdown": hourly_predictions,
            })
            total_predicted += type_total

        # Persist summary predictions
        base_time = now.replace(minute=0, second=0, microsecond=0) + timedelta(hours=1)
        for pred_type in predictions_by_type:
            for hp in pred_type["hourly_breakdown"]:
                db.add(Prediction(
                    org_id=org_id,
                    prediction_type=PredictionType.ALERTS,
                    target_timestamp=datetime.fromisoformat(hp["timestamp"]),
                    predicted_value=hp["value"],
                    confidence_lower=hp["confidence_lower"],
                    confidence_upper=hp["confidence_upper"],
                    confidence_lower_95=hp["confidence_lower_95"],
                    confidence_upper_95=hp["confidence_upper_95"],
                    model_version="freq_v1",
                    alert_type_name=pred_type["alert_type"],
                ))

        log.info(
            "Alert prediction generated",
            types=len(predictions_by_type),
            total=round(total_predicted, 2),
        )

        return {
            "org_id": str(org_id),
            "predictions": predictions_by_type,
            "total_predicted": round(total_predicted, 2),
            "hours_ahead": hours_ahead,
        }

    # ── Occupancy Prediction ───────────────────────────────────────────

    @staticmethod
    async def predict_occupancy(
        db: AsyncSession,
        org_id: uuid.UUID,
        zone_id: uuid.UUID,
        hours_ahead: int = 8,
    ) -> dict[str, Any]:
        """Predict zone occupancy using Holt-Winters on historical data."""
        log = logger.bind(org_id=str(org_id), zone_id=str(zone_id))
        now = datetime.now(timezone.utc)
        lookback = now - timedelta(days=14)

        # Get zone info
        zone_result = await db.execute(
            select(Zone).where(Zone.id == zone_id)
        )
        zone = zone_result.scalars().first()
        zone_name = zone.name if zone else ""
        zone_camera_id = zone.camera_id if zone else None

        # Load historical occupancy estimates
        result = await db.execute(
            select(
                func.date_trunc("hour", FootfallRecord.timestamp).label("hour"),
                func.avg(FootfallRecord.occupancy_estimate).label("avg_occupancy"),
            )
            .join(Camera, FootfallRecord.camera_id == Camera.id)
            .where(
                Camera.org_id == org_id,
                FootfallRecord.zone_id == zone_id,
                FootfallRecord.timestamp >= lookback,
                FootfallRecord.occupancy_estimate.isnot(None),
            )
            .group_by("hour")
            .order_by("hour")
        )
        rows = result.all()

        if len(rows) < 12:
            # Fall back to entry-exit based estimation
            result2 = await db.execute(
                select(
                    func.date_trunc("hour", FootfallRecord.timestamp).label("hour"),
                    func.sum(FootfallRecord.entries_count - FootfallRecord.exits_count).label("net_flow"),
                )
                .join(Camera, FootfallRecord.camera_id == Camera.id)
                .where(
                    Camera.org_id == org_id,
                    FootfallRecord.zone_id == zone_id,
                    FootfallRecord.timestamp >= lookback,
                )
                .group_by("hour")
                .order_by("hour")
            )
            rows2 = result2.all()
            if len(rows2) < 12:
                return {
                    "zone_id": str(zone_id),
                    "zone_name": zone_name,
                    "predictions": [],
                    "message": "Insufficient data for occupancy prediction",
                }
            # Use cumulative net flow as proxy
            values = np.array([float(r.net_flow or 0) for r in rows2])
            series = np.cumsum(np.maximum(values, 0))
        else:
            series = np.array([float(r.avg_occupancy or 0) for r in rows])

        # Run Holt-Winters with 24h seasonality
        forecast, lower_80, upper_80, lower_95, upper_95 = _holt_winters_additive(
            series, season_length=min(24, len(series) // 2), n_forecast=hours_ahead
        )

        # Build prediction points
        base_time = now.replace(minute=0, second=0, microsecond=0) + timedelta(hours=1)
        predictions = []
        peak_val = 0.0
        peak_time = None

        for i in range(hours_ahead):
            ts = base_time + timedelta(hours=i)
            val = max(float(forecast[i]), 0)
            if val > peak_val:
                peak_val = val
                peak_time = ts

            predictions.append({
                "timestamp": ts.isoformat(),
                "value": round(val, 2),
                "confidence_lower": round(max(float(lower_80[i]), 0), 2),
                "confidence_upper": round(float(upper_80[i]), 2),
                "confidence_lower_95": round(max(float(lower_95[i]), 0), 2),
                "confidence_upper_95": round(float(upper_95[i]), 2),
            })

            db.add(Prediction(
                org_id=org_id,
                prediction_type=PredictionType.OCCUPANCY,
                zone_id=zone_id,
                camera_id=zone_camera_id,
                target_timestamp=ts,
                predicted_value=round(val, 2),
                confidence_lower=round(max(float(lower_80[i]), 0), 2),
                confidence_upper=round(float(upper_80[i]), 2),
                confidence_lower_95=round(max(float(lower_95[i]), 0), 2),
                confidence_upper_95=round(float(upper_95[i]), 2),
                model_version="hw_v1",
            ))

        log.info("Occupancy prediction generated", zone_id=str(zone_id), hours=hours_ahead)

        return {
            "zone_id": str(zone_id),
            "zone_name": zone_name,
            "camera_id": str(zone_camera_id) if zone_camera_id else None,
            "predictions": predictions,
            "peak_predicted": round(peak_val, 2),
            "peak_time": peak_time.isoformat() if peak_time else None,
        }

    # ── Staff Schedule Generation ──────────────────────────────────────

    @staticmethod
    async def generate_staff_schedule(
        db: AsyncSession,
        org_id: uuid.UUID,
        target_date: date,
        user_id: Optional[uuid.UUID] = None,
    ) -> dict[str, Any]:
        """Generate optimal guard/operator scheduling for a date.

        Based on predicted alert volumes and footfall, assigns more
        staff to high-risk time periods and returns hourly allocation
        recommendations.
        """
        log = logger.bind(org_id=str(org_id), date=target_date.isoformat())
        now = datetime.now(timezone.utc)

        # Analyse 4 weeks of historical patterns for the same day-of-week
        target_dow = target_date.weekday()
        lookback = now - timedelta(days=28)

        # Historical alert counts by hour for same day-of-week
        alert_result = await db.execute(
            select(
                func.extract("hour", Alert.created_at).label("hour"),
                func.count().label("cnt"),
            )
            .where(
                Alert.org_id == org_id,
                Alert.created_at >= lookback,
                func.extract("dow", Alert.created_at) == target_dow,
            )
            .group_by("hour")
        )
        alert_by_hour: dict[int, float] = {
            int(r.hour): float(r.cnt) for r in alert_result.all()
        }

        # Historical footfall by hour for same day-of-week
        footfall_result = await db.execute(
            select(
                func.extract("hour", FootfallRecord.timestamp).label("hour"),
                func.avg(FootfallRecord.entries_count).label("avg_entries"),
            )
            .join(Camera, FootfallRecord.camera_id == Camera.id)
            .where(
                Camera.org_id == org_id,
                FootfallRecord.timestamp >= lookback,
                func.extract("dow", FootfallRecord.timestamp) == target_dow,
            )
            .group_by("hour")
        )
        footfall_by_hour: dict[int, float] = {
            int(r.hour): float(r.avg_entries or 0) for r in footfall_result.all()
        }

        # Normalise metrics to [0, 1] for risk scoring
        max_alerts = max(alert_by_hour.values()) if alert_by_hour else 1.0
        max_footfall = max(footfall_by_hour.values()) if footfall_by_hour else 1.0

        # Delete existing schedule for this date
        await db.execute(
            delete(StaffSchedule).where(
                StaffSchedule.org_id == org_id,
                StaffSchedule.date == target_date,
            )
        )

        hourly_allocations = []
        total_staff_hours = 0
        peak_staff = 0
        peak_hour = None

        for hour in range(24):
            alerts_norm = (alert_by_hour.get(hour, 0) / max_alerts) if max_alerts > 0 else 0
            footfall_norm = (footfall_by_hour.get(hour, 0) / max_footfall) if max_footfall > 0 else 0

            # Combined risk score: 60% alerts, 40% footfall
            combined_score = 0.6 * alerts_norm + 0.4 * footfall_norm

            # Time-of-day weighting: late night gets lower baseline
            if 0 <= hour < 6:
                time_weight = 0.5
            elif 6 <= hour < 9:
                time_weight = 0.8
            elif 9 <= hour < 18:
                time_weight = 1.0
            elif 18 <= hour < 22:
                time_weight = 0.9
            else:
                time_weight = 0.6

            risk_score = combined_score * time_weight

            # Map risk score to level and staff count
            if risk_score >= 0.75:
                risk_level = RiskLevel.CRITICAL
                recommended = 5
                notes = "Peak risk period - maximum staffing recommended"
            elif risk_score >= 0.5:
                risk_level = RiskLevel.HIGH
                recommended = 4
                notes = "Elevated risk - above-average staffing recommended"
            elif risk_score >= 0.25:
                risk_level = RiskLevel.MEDIUM
                recommended = 2
                notes = "Moderate activity expected"
            else:
                risk_level = RiskLevel.LOW
                recommended = 1
                notes = "Low activity period - minimal staffing sufficient"

            # Ensure minimum 1 staff for all hours
            recommended = max(recommended, 1)

            schedule = StaffSchedule(
                org_id=org_id,
                date=target_date,
                hour=hour,
                recommended_staff=recommended,
                risk_level=risk_level,
                predicted_alerts=round(alert_by_hour.get(hour, 0), 2),
                predicted_footfall=round(footfall_by_hour.get(hour, 0), 2),
                notes=notes,
                created_by=user_id,
            )
            db.add(schedule)

            total_staff_hours += recommended
            if recommended > peak_staff:
                peak_staff = recommended
                peak_hour = hour

            hourly_allocations.append({
                "hour": hour,
                "recommended_staff": recommended,
                "risk_level": risk_level.value,
                "predicted_alerts": round(alert_by_hour.get(hour, 0), 2),
                "predicted_footfall": round(footfall_by_hour.get(hour, 0), 2),
                "notes": notes,
            })

        log.info(
            "Staff schedule generated",
            total_staff_hours=total_staff_hours,
            peak_staff=peak_staff,
        )

        return {
            "org_id": str(org_id),
            "date": target_date.isoformat(),
            "hourly_allocations": hourly_allocations,
            "total_staff_hours": total_staff_hours,
            "peak_hour": peak_hour,
            "peak_staff": peak_staff,
        }

    # ── Trend Detection ────────────────────────────────────────────────

    @staticmethod
    async def detect_trends(
        db: AsyncSession,
        org_id: uuid.UUID,
        metric: str = "footfall",
        period_days: int = 30,
    ) -> dict[str, Any]:
        """Detect trends using linear regression and Mann-Kendall test.

        Supports metrics: footfall, alerts, occupancy.
        Returns trend direction/slope, Mann-Kendall p-value, and confidence.
        """
        log = logger.bind(org_id=str(org_id), metric=metric)
        now = datetime.now(timezone.utc)
        lookback = now - timedelta(days=period_days)

        # Build daily series based on metric
        if metric == "footfall":
            result = await db.execute(
                select(
                    func.date_trunc("day", FootfallRecord.timestamp).label("day"),
                    func.sum(FootfallRecord.entries_count).label("value"),
                )
                .join(Camera, FootfallRecord.camera_id == Camera.id)
                .where(Camera.org_id == org_id, FootfallRecord.timestamp >= lookback)
                .group_by("day")
                .order_by("day")
            )
        elif metric == "alerts":
            result = await db.execute(
                select(
                    func.date_trunc("day", Alert.created_at).label("day"),
                    func.count().label("value"),
                )
                .where(Alert.org_id == org_id, Alert.created_at >= lookback)
                .group_by("day")
                .order_by("day")
            )
        elif metric == "occupancy":
            result = await db.execute(
                select(
                    func.date_trunc("day", FootfallRecord.timestamp).label("day"),
                    func.avg(FootfallRecord.occupancy_estimate).label("value"),
                )
                .join(Camera, FootfallRecord.camera_id == Camera.id)
                .where(
                    Camera.org_id == org_id,
                    FootfallRecord.timestamp >= lookback,
                    FootfallRecord.occupancy_estimate.isnot(None),
                )
                .group_by("day")
                .order_by("day")
            )
        else:
            return {
                "metric": metric,
                "direction": "stable",
                "slope": 0.0,
                "p_value": 1.0,
                "confidence": "low",
                "description": f"Unsupported metric: {metric}",
                "period_days": period_days,
                "data_points": 0,
            }

        rows = result.all()
        if len(rows) < 4:
            return {
                "metric": metric,
                "direction": "stable",
                "slope": 0.0,
                "p_value": 1.0,
                "confidence": "low",
                "description": f"Insufficient data for {metric} trend analysis (need >= 4 daily points)",
                "period_days": period_days,
                "data_points": len(rows),
            }

        values = np.array([float(r.value or 0) for r in rows])

        # Linear regression for slope
        x = np.arange(len(values), dtype=np.float64)
        coeffs = np.polyfit(x, values, 1)
        slope = float(coeffs[0])

        # Mann-Kendall test
        tau, p_value, mk_trend = _mann_kendall_test(values)

        # Determine confidence level
        if p_value < 0.01:
            confidence = "very_high"
        elif p_value < 0.05:
            confidence = "high"
        elif p_value < 0.10:
            confidence = "moderate"
        else:
            confidence = "low"

        # Calculate percentage change
        first_val = float(values[0]) if values[0] != 0 else 1.0
        change_pct = ((float(values[-1]) - float(values[0])) / abs(first_val)) * 100

        # Human-readable description
        if mk_trend == "increasing":
            desc = (
                f"{metric.capitalize()} is trending upward with a slope of "
                f"{slope:.2f} units/day ({confidence} confidence, p={p_value:.4f}). "
                f"Change over period: {change_pct:+.1f}%."
            )
        elif mk_trend == "decreasing":
            desc = (
                f"{metric.capitalize()} is trending downward with a slope of "
                f"{slope:.2f} units/day ({confidence} confidence, p={p_value:.4f}). "
                f"Change over period: {change_pct:+.1f}%."
            )
        else:
            desc = (
                f"{metric.capitalize()} shows no significant trend over the last "
                f"{period_days} days (p={p_value:.4f})."
            )

        log.info("Trend analysis completed", direction=mk_trend, slope=round(slope, 4), p_value=round(p_value, 4))

        return {
            "metric": metric,
            "direction": mk_trend,
            "slope": round(slope, 4),
            "p_value": round(p_value, 4),
            "confidence": confidence,
            "description": desc,
            "period_days": period_days,
            "data_points": len(rows),
            "change_pct": round(change_pct, 2),
        }

    # ── Risk Forecast ──────────────────────────────────────────────────

    @staticmethod
    async def get_risk_forecast(
        db: AsyncSession,
        org_id: uuid.UUID,
    ) -> dict[str, Any]:
        """Compute overall risk score per camera/zone.

        Weighted combination of:
          - Predicted alert volume (40%)
          - Anomaly baseline deviation (30%)
          - Recent incident severity (30%)
        """
        log = logger.bind(org_id=str(org_id))
        now = datetime.now(timezone.utc)
        lookback_7d = now - timedelta(days=7)
        lookback_24h = now - timedelta(hours=24)

        # Get all cameras for the org
        cam_result = await db.execute(
            select(Camera).where(Camera.org_id == org_id, Camera.is_active.is_(True))
        )
        cameras = cam_result.scalars().all()

        if not cameras:
            return {
                "org_id": str(org_id),
                "forecasts": [],
                "overall_risk_level": "low",
                "overall_risk_score": 0.0,
            }

        # Alert counts per camera in last 7 days
        alert_result = await db.execute(
            select(
                Alert.camera_id,
                func.count().label("cnt"),
                func.count().filter(Alert.severity.in_(["critical", "high"])).label("severe_cnt"),
            )
            .where(Alert.org_id == org_id, Alert.created_at >= lookback_7d)
            .group_by(Alert.camera_id)
        )
        alert_by_camera: dict[uuid.UUID, dict] = {}
        for r in alert_result.all():
            alert_by_camera[r.camera_id] = {"total": r.cnt, "severe": r.severe_cnt}

        # Anomaly events per camera in last 24h
        anomaly_result = await db.execute(
            select(
                AnomalyEvent.camera_id,
                func.count().label("cnt"),
                func.avg(AnomalyEvent.deviation_sigma).label("avg_deviation"),
            )
            .where(AnomalyEvent.org_id == org_id, AnomalyEvent.created_at >= lookback_24h)
            .group_by(AnomalyEvent.camera_id)
        )
        anomaly_by_camera: dict[uuid.UUID, dict] = {}
        for r in anomaly_result.all():
            anomaly_by_camera[r.camera_id] = {
                "count": r.cnt,
                "avg_deviation": float(r.avg_deviation or 0),
            }

        # Compute max values for normalisation
        max_alerts = max((v["total"] for v in alert_by_camera.values()), default=1)
        max_anomalies = max((v["count"] for v in anomaly_by_camera.values()), default=1)

        # Delete old forecasts
        await db.execute(
            delete(RiskForecast).where(
                RiskForecast.org_id == org_id,
                RiskForecast.valid_until <= now,
            )
        )

        forecasts = []
        total_score = 0.0
        valid_until = now + timedelta(hours=6)

        for camera in cameras:
            alerts_data = alert_by_camera.get(camera.id, {"total": 0, "severe": 0})
            anomaly_data = anomaly_by_camera.get(camera.id, {"count": 0, "avg_deviation": 0})

            # Normalised components
            alert_norm = alerts_data["total"] / max(max_alerts, 1)
            severe_norm = alerts_data["severe"] / max(alerts_data["total"], 1) if alerts_data["total"] > 0 else 0
            anomaly_norm = anomaly_data["count"] / max(max_anomalies, 1)
            deviation_norm = min(anomaly_data["avg_deviation"] / 3.0, 1.0)

            # Weighted risk score
            risk_score = (
                0.30 * alert_norm
                + 0.15 * severe_norm
                + 0.25 * anomaly_norm
                + 0.15 * deviation_norm
                + 0.15 * (1.0 if not camera.is_online else 0.0)
            )
            risk_score = min(max(risk_score, 0.0), 1.0)

            # Map to risk level
            if risk_score >= 0.75:
                risk_level = RiskLevel.CRITICAL
            elif risk_score >= 0.50:
                risk_level = RiskLevel.HIGH
            elif risk_score >= 0.25:
                risk_level = RiskLevel.MEDIUM
            else:
                risk_level = RiskLevel.LOW

            # Contributing factors
            factors = []
            if alerts_data["total"] > 0:
                factors.append({
                    "factor": "Alert volume (7d)",
                    "weight": 0.30,
                    "value": alerts_data["total"],
                    "description": f"{alerts_data['total']} alerts in the last 7 days",
                })
            if alerts_data["severe"] > 0:
                factors.append({
                    "factor": "Severe alerts ratio",
                    "weight": 0.15,
                    "value": round(severe_norm, 2),
                    "description": f"{alerts_data['severe']} critical/high severity alerts",
                })
            if anomaly_data["count"] > 0:
                factors.append({
                    "factor": "Anomaly events (24h)",
                    "weight": 0.25,
                    "value": anomaly_data["count"],
                    "description": f"{anomaly_data['count']} anomalies detected in the last 24h",
                })
            if anomaly_data["avg_deviation"] > 0:
                factors.append({
                    "factor": "Anomaly deviation",
                    "weight": 0.15,
                    "value": round(anomaly_data["avg_deviation"], 2),
                    "description": f"Average deviation of {anomaly_data['avg_deviation']:.1f} sigma",
                })
            if not camera.is_online:
                factors.append({
                    "factor": "Camera offline",
                    "weight": 0.15,
                    "value": 1.0,
                    "description": "Camera is currently offline -- cannot monitor",
                })

            # Persist forecast
            rf = RiskForecast(
                org_id=org_id,
                camera_id=camera.id,
                risk_level=risk_level,
                risk_score=round(risk_score, 4),
                contributing_factors={"factors": factors},
                valid_until=valid_until,
            )
            db.add(rf)

            forecasts.append({
                "camera_id": str(camera.id),
                "camera_name": camera.name,
                "zone_id": None,
                "zone_name": None,
                "risk_level": risk_level.value,
                "risk_score": round(risk_score, 4),
                "contributing_factors": factors,
                "valid_until": valid_until.isoformat(),
            })
            total_score += risk_score

        overall_score = total_score / len(cameras) if cameras else 0.0
        if overall_score >= 0.75:
            overall_level = "critical"
        elif overall_score >= 0.50:
            overall_level = "high"
        elif overall_score >= 0.25:
            overall_level = "medium"
        else:
            overall_level = "low"

        log.info(
            "Risk forecast generated",
            cameras=len(cameras),
            overall_score=round(overall_score, 4),
        )

        return {
            "org_id": str(org_id),
            "forecasts": forecasts,
            "overall_risk_level": overall_level,
            "overall_risk_score": round(overall_score, 4),
        }

    # ── Prediction Accuracy ────────────────────────────────────────────

    @staticmethod
    async def get_predictions_accuracy(
        db: AsyncSession,
        org_id: uuid.UUID,
    ) -> dict[str, Any]:
        """Compare past predictions with actuals and return MAPE per type."""
        log = logger.bind(org_id=str(org_id))

        metrics = []
        overall_accuracy_sum = 0.0
        overall_count = 0

        for ptype in PredictionType:
            mape = await PredictiveService._compute_mape(db, org_id, ptype)
            if mape is not None:
                accuracy = max(100 - mape, 0)
                count_result = await db.execute(
                    select(func.count()).where(
                        Prediction.org_id == org_id,
                        Prediction.prediction_type == ptype,
                        Prediction.actual_value.isnot(None),
                    )
                )
                count = count_result.scalar() or 0
                metrics.append({
                    "metric": ptype.value,
                    "mape": round(mape, 2),
                    "accuracy_pct": round(accuracy, 2),
                    "predictions_count": count,
                    "period_days": 30,
                })
                overall_accuracy_sum += accuracy
                overall_count += 1

        overall = round(overall_accuracy_sum / overall_count, 2) if overall_count > 0 else None

        log.info("Prediction accuracy computed", metrics_count=len(metrics))

        return {
            "org_id": str(org_id),
            "metrics": metrics,
            "overall_accuracy_pct": overall,
        }

    # ── Internal Helpers ───────────────────────────────────────────────

    @staticmethod
    async def _compute_mape(
        db: AsyncSession,
        org_id: uuid.UUID,
        prediction_type: PredictionType,
        camera_id: Optional[uuid.UUID] = None,
    ) -> Optional[float]:
        """Compute Mean Absolute Percentage Error for completed predictions.

        Returns None if no predictions have been evaluated yet.
        """
        filters = [
            Prediction.org_id == org_id,
            Prediction.prediction_type == prediction_type,
            Prediction.actual_value.isnot(None),
            Prediction.actual_value > 0,
        ]
        if camera_id is not None:
            filters.append(Prediction.camera_id == camera_id)

        result = await db.execute(
            select(
                func.avg(
                    func.abs(Prediction.predicted_value - Prediction.actual_value)
                    / Prediction.actual_value
                    * 100
                ).label("mape")
            ).where(and_(*filters))
        )
        row = result.first()
        if row and row.mape is not None:
            return float(row.mape)
        return None

    @staticmethod
    async def evaluate_prediction_accuracy(
        db: AsyncSession,
        org_id: uuid.UUID,
    ) -> dict[str, Any]:
        """Back-fill actual values for past predictions by comparing with real data.

        This is called periodically (e.g. hourly) to fill in the
        ``actual_value`` column for predictions whose target_timestamp
        has passed.
        """
        log = logger.bind(org_id=str(org_id))
        now = datetime.now(timezone.utc)
        updated = 0

        # Footfall predictions: fill actual from footfall_records
        footfall_preds = await db.execute(
            select(Prediction).where(
                Prediction.org_id == org_id,
                Prediction.prediction_type == PredictionType.FOOTFALL,
                Prediction.actual_value.is_(None),
                Prediction.target_timestamp <= now,
            )
        )

        for pred in footfall_preds.scalars().all():
            hour_start = pred.target_timestamp.replace(minute=0, second=0, microsecond=0)
            hour_end = hour_start + timedelta(hours=1)

            actual_result = await db.execute(
                select(func.sum(FootfallRecord.entries_count)).where(
                    FootfallRecord.camera_id == pred.camera_id,
                    FootfallRecord.timestamp >= hour_start,
                    FootfallRecord.timestamp < hour_end,
                )
            )
            actual = actual_result.scalar()
            if actual is not None:
                pred.actual_value = float(actual)
                updated += 1

        # Alert predictions: fill actual from alerts table
        alert_preds = await db.execute(
            select(Prediction).where(
                Prediction.org_id == org_id,
                Prediction.prediction_type == PredictionType.ALERTS,
                Prediction.actual_value.is_(None),
                Prediction.target_timestamp <= now,
            )
        )

        for pred in alert_preds.scalars().all():
            hour_start = pred.target_timestamp.replace(minute=0, second=0, microsecond=0)
            hour_end = hour_start + timedelta(hours=1)

            filters = [
                Alert.org_id == org_id,
                Alert.created_at >= hour_start,
                Alert.created_at < hour_end,
            ]

            actual_result = await db.execute(
                select(func.count()).where(and_(*filters))
            )
            actual = actual_result.scalar()
            if actual is not None:
                pred.actual_value = float(actual)
                updated += 1

        # Occupancy predictions: fill from footfall records
        occ_preds = await db.execute(
            select(Prediction).where(
                Prediction.org_id == org_id,
                Prediction.prediction_type == PredictionType.OCCUPANCY,
                Prediction.actual_value.is_(None),
                Prediction.target_timestamp <= now,
            )
        )

        for pred in occ_preds.scalars().all():
            hour_start = pred.target_timestamp.replace(minute=0, second=0, microsecond=0)
            hour_end = hour_start + timedelta(hours=1)

            actual_result = await db.execute(
                select(func.avg(FootfallRecord.occupancy_estimate)).where(
                    FootfallRecord.zone_id == pred.zone_id,
                    FootfallRecord.timestamp >= hour_start,
                    FootfallRecord.timestamp < hour_end,
                    FootfallRecord.occupancy_estimate.isnot(None),
                )
            )
            actual = actual_result.scalar()
            if actual is not None:
                pred.actual_value = float(actual)
                updated += 1

        log.info("Prediction accuracy evaluation completed", predictions_updated=updated)
        return {"org_id": str(org_id), "predictions_updated": updated}
