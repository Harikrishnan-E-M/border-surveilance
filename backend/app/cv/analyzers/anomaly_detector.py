"""
Unsupervised Anomaly Detection Engine for VisionAI.

Uses statistical methods (Z-score, rolling windows, spatial distribution
analysis, and trajectory deviation) to detect anomalies in real-time
surveillance analytics without requiring labelled training data.

Baselines are computed from historical data and stored in Redis for
fast access during live detection. The engine supports five anomaly
detection modes:

1. **Count anomaly** -- Z-score deviation from hourly baselines.
2. **Temporal anomaly** -- Activity during normally quiet hours.
3. **Spatial anomaly** -- Activity in normally unused zones.
4. **Behavioral anomaly** -- Unusual movement patterns (speed, direction).
5. **Frequency anomaly** -- Sudden spikes or drops in event frequency.

Usage::

    from app.cv.analyzers.anomaly_detector import AnomalyDetector

    detector = AnomalyDetector()
    baseline = detector.build_baseline(historical_data, days=30)
    events = detector.detect_count_anomaly(current_count, hour=14, day_of_week=2)
"""

from __future__ import annotations

import json
import math
import time
from dataclasses import dataclass, field
from datetime import datetime, timezone
from typing import Any, Optional

import numpy as np
import structlog

logger = structlog.stdlib.get_logger(__name__)

# -- Sensitivity defaults (can be overridden via Redis config) ---------------

DEFAULT_WARNING_SIGMA = 2.0
DEFAULT_CRITICAL_SIGMA = 3.0
DEFAULT_MIN_CONFIDENCE = 0.5
EMA_ALPHA = 0.05  # Exponential moving average smoothing factor


# -- Data classes ------------------------------------------------------------


@dataclass
class BaselineProfile:
    """Statistical baseline for a camera/zone.

    Attributes:
        hourly_counts: 24-element array of mean counts per hour of day.
        hourly_std: 24-element array of standard deviations per hour.
        day_of_week_factors: 7-element array of day-of-week scaling
            factors (Monday=0 through Sunday=6). A factor of 1.0 means
            the day matches the overall average.
        zone_baselines: Per-zone baseline dict mapping zone_id to a
            dict with ``mean`` and ``std`` keys.
        sample_count: Number of historical data points used to build
            this baseline.
        built_at: ISO-8601 timestamp when the baseline was computed.
    """

    hourly_counts: np.ndarray = field(default_factory=lambda: np.zeros(24))
    hourly_std: np.ndarray = field(default_factory=lambda: np.ones(24))
    day_of_week_factors: np.ndarray = field(default_factory=lambda: np.ones(7))
    zone_baselines: dict[str, dict[str, float]] = field(default_factory=dict)
    sample_count: int = 0
    built_at: str = ""

    def to_dict(self) -> dict[str, Any]:
        """Serialize to a JSON-compatible dictionary."""
        return {
            "hourly_counts": self.hourly_counts.tolist(),
            "hourly_std": self.hourly_std.tolist(),
            "day_of_week_factors": self.day_of_week_factors.tolist(),
            "zone_baselines": self.zone_baselines,
            "sample_count": self.sample_count,
            "built_at": self.built_at,
        }

    @classmethod
    def from_dict(cls, data: dict[str, Any]) -> BaselineProfile:
        """Deserialize from a dictionary."""
        return cls(
            hourly_counts=np.array(data.get("hourly_counts", [0.0] * 24)),
            hourly_std=np.array(data.get("hourly_std", [1.0] * 24)),
            day_of_week_factors=np.array(data.get("day_of_week_factors", [1.0] * 7)),
            zone_baselines=data.get("zone_baselines", {}),
            sample_count=data.get("sample_count", 0),
            built_at=data.get("built_at", ""),
        )


@dataclass
class AnomalyEvent:
    """A single detected anomaly event.

    Attributes:
        timestamp: ISO-8601 UTC timestamp of the detection.
        anomaly_type: One of count, temporal, spatial, behavioral, frequency.
        severity: info, warning, or critical.
        confidence: Detection confidence in [0, 1].
        description: Human-readable description of the anomaly.
        zone_id: Optional zone where the anomaly was observed.
        camera_id: Camera that produced the observation.
        baseline_value: Expected value from the baseline.
        observed_value: Actual observed value.
        deviation_sigma: Number of standard deviations from the mean.
    """

    timestamp: str
    anomaly_type: str
    severity: str
    confidence: float
    description: str
    zone_id: Optional[str] = None
    camera_id: Optional[str] = None
    baseline_value: Optional[float] = None
    observed_value: Optional[float] = None
    deviation_sigma: Optional[float] = None

    def to_dict(self) -> dict[str, Any]:
        """Serialize to a plain dictionary."""
        return {
            "timestamp": self.timestamp,
            "anomaly_type": self.anomaly_type,
            "severity": self.severity,
            "confidence": round(self.confidence, 4),
            "description": self.description,
            "zone_id": self.zone_id,
            "camera_id": self.camera_id,
            "baseline_value": round(self.baseline_value, 2) if self.baseline_value is not None else None,
            "observed_value": round(self.observed_value, 2) if self.observed_value is not None else None,
            "deviation_sigma": round(self.deviation_sigma, 2) if self.deviation_sigma is not None else None,
        }


# -- Main detector class -----------------------------------------------------


class AnomalyDetector:
    """Unsupervised anomaly detection engine using statistical methods.

    Maintains per-camera baselines and applies multiple detection
    strategies to identify deviations from normal activity patterns.

    Args:
        warning_sigma: Z-score threshold for warning-level anomalies.
        critical_sigma: Z-score threshold for critical-level anomalies.
        min_confidence: Minimum confidence score to emit an anomaly.
        redis_client: Optional async Redis client for baseline storage.
    """

    def __init__(
        self,
        warning_sigma: float = DEFAULT_WARNING_SIGMA,
        critical_sigma: float = DEFAULT_CRITICAL_SIGMA,
        min_confidence: float = DEFAULT_MIN_CONFIDENCE,
        redis_client: Any = None,
    ) -> None:
        self.warning_sigma = warning_sigma
        self.critical_sigma = critical_sigma
        self.min_confidence = min_confidence
        self._redis = redis_client
        self._baselines: dict[str, BaselineProfile] = {}

        logger.info(
            "anomaly_detector.initialised",
            warning_sigma=warning_sigma,
            critical_sigma=critical_sigma,
            min_confidence=min_confidence,
        )

    # -- Baseline management -------------------------------------------------

    def build_baseline(
        self,
        historical_data: list[dict[str, Any]],
        days: int = 30,
    ) -> BaselineProfile:
        """Compute statistical baselines from historical analytics data.

        Each entry in ``historical_data`` should have:
          - ``timestamp``: ISO-8601 datetime string
          - ``count``: Numeric count (entries, detections, etc.)
          - ``zone_id``: Optional zone identifier

        Args:
            historical_data: List of historical data points.
            days: Number of days of history to consider.

        Returns:
            BaselineProfile: Computed statistical baseline.
        """
        if not historical_data:
            logger.warning("anomaly_detector.build_baseline.empty_data")
            return BaselineProfile(
                built_at=datetime.now(timezone.utc).isoformat(),
            )

        # Parse timestamps and extract features
        hourly_buckets: dict[int, list[float]] = {h: [] for h in range(24)}
        dow_buckets: dict[int, list[float]] = {d: [] for d in range(7)}
        zone_buckets: dict[str, list[float]] = {}

        for entry in historical_data:
            ts_raw = entry.get("timestamp")
            count = float(entry.get("count", 0))

            if ts_raw is None:
                continue

            if isinstance(ts_raw, str):
                try:
                    ts = datetime.fromisoformat(ts_raw)
                except (ValueError, TypeError):
                    continue
            elif isinstance(ts_raw, datetime):
                ts = ts_raw
            else:
                continue

            hour = ts.hour
            dow = ts.weekday()  # Monday=0, Sunday=6

            hourly_buckets[hour].append(count)
            dow_buckets[dow].append(count)

            zone_id = entry.get("zone_id")
            if zone_id:
                zone_key = str(zone_id)
                if zone_key not in zone_buckets:
                    zone_buckets[zone_key] = []
                zone_buckets[zone_key].append(count)

        # Compute hourly profile
        hourly_counts = np.zeros(24)
        hourly_std = np.ones(24)
        for h in range(24):
            values = hourly_buckets[h]
            if values:
                hourly_counts[h] = float(np.mean(values))
                std = float(np.std(values))
                hourly_std[h] = std if std > 0 else 1.0

        # Compute day-of-week factors
        overall_mean = float(np.mean([
            c for bucket in hourly_buckets.values() for c in bucket
        ])) if any(hourly_buckets.values()) else 1.0

        day_of_week_factors = np.ones(7)
        for d in range(7):
            values = dow_buckets[d]
            if values and overall_mean > 0:
                dow_mean = float(np.mean(values))
                day_of_week_factors[d] = dow_mean / overall_mean if overall_mean > 0 else 1.0

        # Compute zone baselines
        zone_baselines: dict[str, dict[str, float]] = {}
        for zone_id, values in zone_buckets.items():
            arr = np.array(values)
            zone_baselines[zone_id] = {
                "mean": float(np.mean(arr)),
                "std": float(np.std(arr)) if float(np.std(arr)) > 0 else 1.0,
                "count": len(values),
            }

        sample_count = sum(len(v) for v in hourly_buckets.values())

        profile = BaselineProfile(
            hourly_counts=hourly_counts,
            hourly_std=hourly_std,
            day_of_week_factors=day_of_week_factors,
            zone_baselines=zone_baselines,
            sample_count=sample_count,
            built_at=datetime.now(timezone.utc).isoformat(),
        )

        logger.info(
            "anomaly_detector.baseline_built",
            sample_count=sample_count,
            zones=len(zone_baselines),
        )

        return profile

    def update_baseline(
        self,
        baseline: BaselineProfile,
        new_data: list[dict[str, Any]],
    ) -> BaselineProfile:
        """Update a baseline with new data using exponential moving average.

        Performs an online update without requiring full recomputation.

        Args:
            baseline: Existing baseline to update.
            new_data: New data points with timestamp, count, zone_id.

        Returns:
            BaselineProfile: Updated baseline.
        """
        if not new_data:
            return baseline

        for entry in new_data:
            ts_raw = entry.get("timestamp")
            count = float(entry.get("count", 0))

            if ts_raw is None:
                continue

            if isinstance(ts_raw, str):
                try:
                    ts = datetime.fromisoformat(ts_raw)
                except (ValueError, TypeError):
                    continue
            elif isinstance(ts_raw, datetime):
                ts = ts_raw
            else:
                continue

            hour = ts.hour
            dow = ts.weekday()

            # EMA update for hourly counts
            old_mean = baseline.hourly_counts[hour]
            new_mean = EMA_ALPHA * count + (1 - EMA_ALPHA) * old_mean
            baseline.hourly_counts[hour] = new_mean

            # EMA update for hourly std (using running approximation)
            old_std = baseline.hourly_std[hour]
            deviation = abs(count - new_mean)
            new_std = EMA_ALPHA * deviation + (1 - EMA_ALPHA) * old_std
            baseline.hourly_std[hour] = max(new_std, 0.1)

            # Update zone baselines
            zone_id = entry.get("zone_id")
            if zone_id:
                zone_key = str(zone_id)
                if zone_key in baseline.zone_baselines:
                    zb = baseline.zone_baselines[zone_key]
                    old_z_mean = zb["mean"]
                    zb["mean"] = EMA_ALPHA * count + (1 - EMA_ALPHA) * old_z_mean
                    old_z_std = zb["std"]
                    z_dev = abs(count - zb["mean"])
                    zb["std"] = max(EMA_ALPHA * z_dev + (1 - EMA_ALPHA) * old_z_std, 0.1)
                    zb["count"] = zb.get("count", 0) + 1
                else:
                    baseline.zone_baselines[zone_key] = {
                        "mean": count,
                        "std": 1.0,
                        "count": 1,
                    }

            baseline.sample_count += 1

        baseline.built_at = datetime.now(timezone.utc).isoformat()

        logger.debug(
            "anomaly_detector.baseline_updated",
            new_points=len(new_data),
            total_samples=baseline.sample_count,
        )

        return baseline

    # -- Detection methods ---------------------------------------------------

    def detect_count_anomaly(
        self,
        current_count: float,
        hour: int,
        day_of_week: int,
        zone_id: Optional[str] = None,
        baseline: Optional[BaselineProfile] = None,
        camera_id: Optional[str] = None,
    ) -> list[AnomalyEvent]:
        """Detect count anomalies using Z-score against the hourly baseline.

        Computes the expected count for the given hour and day, then
        checks if the observed count deviates beyond the sigma thresholds.

        Args:
            current_count: Observed count value.
            hour: Hour of day (0-23).
            day_of_week: Day of week (0=Monday, 6=Sunday).
            zone_id: Optional zone identifier.
            baseline: Baseline profile to use. Falls back to internal cache.
            camera_id: Camera identifier for event metadata.

        Returns:
            List of detected AnomalyEvent instances (may be empty).
        """
        events: list[AnomalyEvent] = []

        if baseline is None:
            key = f"{camera_id}:{zone_id}" if zone_id else str(camera_id)
            baseline = self._baselines.get(key)
            if baseline is None:
                return events

        # Expected value = hourly mean * day-of-week factor
        expected_mean = baseline.hourly_counts[hour] * baseline.day_of_week_factors[day_of_week]
        hourly_std = baseline.hourly_std[hour]

        if hourly_std <= 0:
            hourly_std = 1.0

        z_score = (current_count - expected_mean) / hourly_std
        abs_z = abs(z_score)

        if abs_z < self.warning_sigma:
            return events

        # Determine severity
        if abs_z >= self.critical_sigma:
            severity = "critical"
        else:
            severity = "warning"

        # Confidence scales with deviation magnitude
        confidence = min(1.0, abs_z / (self.critical_sigma * 1.5))
        if confidence < self.min_confidence:
            return events

        direction = "above" if z_score > 0 else "below"
        description = (
            f"Count anomaly: observed {current_count:.0f} is {abs_z:.1f} sigma "
            f"{direction} expected {expected_mean:.1f} for hour {hour:02d} "
            f"on day {day_of_week}"
        )

        events.append(AnomalyEvent(
            timestamp=datetime.now(timezone.utc).isoformat(),
            anomaly_type="count",
            severity=severity,
            confidence=confidence,
            description=description,
            zone_id=zone_id,
            camera_id=camera_id,
            baseline_value=expected_mean,
            observed_value=current_count,
            deviation_sigma=z_score,
        ))

        return events

    def detect_temporal_anomaly(
        self,
        events_timeline: list[dict[str, Any]],
        baseline: Optional[BaselineProfile] = None,
        camera_id: Optional[str] = None,
    ) -> list[AnomalyEvent]:
        """Detect unusual activity during normally quiet hours.

        Examines recent events and flags any that occur during hours
        where the baseline shows very low activity (mean < 1 and the
        observed count is significantly above zero).

        Args:
            events_timeline: List of dicts with ``timestamp`` and ``count``.
            baseline: Baseline profile to use.
            camera_id: Camera identifier.

        Returns:
            List of detected AnomalyEvent instances.
        """
        anomalies: list[AnomalyEvent] = []

        if baseline is None or not events_timeline:
            return anomalies

        for entry in events_timeline:
            ts_raw = entry.get("timestamp")
            count = float(entry.get("count", 0))

            if ts_raw is None or count <= 0:
                continue

            if isinstance(ts_raw, str):
                try:
                    ts = datetime.fromisoformat(ts_raw)
                except (ValueError, TypeError):
                    continue
            elif isinstance(ts_raw, datetime):
                ts = ts_raw
            else:
                continue

            hour = ts.hour
            expected_mean = baseline.hourly_counts[hour]
            hourly_std = baseline.hourly_std[hour]

            # Flag as temporal anomaly if this hour is normally quiet
            # (mean < 1.0) but activity is observed
            is_quiet_hour = expected_mean < 1.0 and hourly_std < 1.0

            if is_quiet_hour and count > 0:
                # The quieter the hour normally is, the more anomalous
                confidence = min(1.0, count / max(expected_mean + hourly_std, 0.5))

                if confidence < self.min_confidence:
                    continue

                severity = "critical" if count > 5 else "warning" if count > 2 else "info"

                description = (
                    f"Temporal anomaly: {count:.0f} events detected at hour {hour:02d} "
                    f"which normally has {expected_mean:.1f} avg activity"
                )

                anomalies.append(AnomalyEvent(
                    timestamp=ts.isoformat() if isinstance(ts, datetime) else ts_raw,
                    anomaly_type="temporal",
                    severity=severity,
                    confidence=confidence,
                    description=description,
                    camera_id=camera_id,
                    baseline_value=expected_mean,
                    observed_value=count,
                    deviation_sigma=count / max(hourly_std, 0.1),
                ))

        return anomalies

    def detect_spatial_anomaly(
        self,
        detections: list[dict[str, Any]],
        zone_baselines: Optional[dict[str, dict[str, float]]] = None,
        camera_id: Optional[str] = None,
    ) -> list[AnomalyEvent]:
        """Detect unusual spatial distribution of activity.

        Checks if activity appears in zones that are normally inactive,
        or if an active zone has significantly more/less activity than
        expected.

        Args:
            detections: List of dicts with ``zone_id`` and ``count``.
            zone_baselines: Per-zone baseline stats (mean, std, count).
            camera_id: Camera identifier.

        Returns:
            List of detected AnomalyEvent instances.
        """
        anomalies: list[AnomalyEvent] = []

        if not detections or not zone_baselines:
            return anomalies

        # Aggregate counts per zone from current detections
        zone_counts: dict[str, float] = {}
        for det in detections:
            zid = str(det.get("zone_id", ""))
            if not zid:
                continue
            zone_counts[zid] = zone_counts.get(zid, 0) + float(det.get("count", 1))

        for zone_id, observed_count in zone_counts.items():
            zb = zone_baselines.get(zone_id)

            if zb is None:
                # Activity in a zone with no baseline = new zone activity
                if observed_count > 0:
                    anomalies.append(AnomalyEvent(
                        timestamp=datetime.now(timezone.utc).isoformat(),
                        anomaly_type="spatial",
                        severity="warning",
                        confidence=0.7,
                        description=(
                            f"Spatial anomaly: {observed_count:.0f} detections in zone "
                            f"{zone_id} which has no historical baseline"
                        ),
                        zone_id=zone_id,
                        camera_id=camera_id,
                        baseline_value=0.0,
                        observed_value=observed_count,
                        deviation_sigma=None,
                    ))
                continue

            zone_mean = zb.get("mean", 0.0)
            zone_std = zb.get("std", 1.0)

            if zone_std <= 0:
                zone_std = 1.0

            z_score = (observed_count - zone_mean) / zone_std
            abs_z = abs(z_score)

            if abs_z < self.warning_sigma:
                continue

            severity = "critical" if abs_z >= self.critical_sigma else "warning"
            confidence = min(1.0, abs_z / (self.critical_sigma * 1.5))

            if confidence < self.min_confidence:
                continue

            direction = "above" if z_score > 0 else "below"
            description = (
                f"Spatial anomaly: zone {zone_id} has {observed_count:.0f} detections, "
                f"{abs_z:.1f} sigma {direction} baseline mean {zone_mean:.1f}"
            )

            anomalies.append(AnomalyEvent(
                timestamp=datetime.now(timezone.utc).isoformat(),
                anomaly_type="spatial",
                severity=severity,
                confidence=confidence,
                description=description,
                zone_id=zone_id,
                camera_id=camera_id,
                baseline_value=zone_mean,
                observed_value=observed_count,
                deviation_sigma=z_score,
            ))

        return anomalies

    def detect_behavioral_anomaly(
        self,
        tracks: list[dict[str, Any]],
        camera_id: Optional[str] = None,
    ) -> list[AnomalyEvent]:
        """Detect unusual movement patterns from tracked objects.

        Analyses track data for abnormal speed, erratic direction
        changes, and trajectory deviations from typical paths.

        Each track dict should contain:
          - ``track_id``: Unique track identifier
          - ``positions``: List of ``[x, y, timestamp]`` tuples
          - ``class_name``: Object class (e.g. ``"person"``)

        Args:
            tracks: List of track data dictionaries.
            camera_id: Camera identifier.

        Returns:
            List of detected AnomalyEvent instances.
        """
        anomalies: list[AnomalyEvent] = []

        if not tracks:
            return anomalies

        for track in tracks:
            positions = track.get("positions", [])
            track_id = track.get("track_id", "unknown")

            if len(positions) < 3:
                continue

            # Convert positions to numpy array
            try:
                pos_array = np.array(positions, dtype=np.float64)
                if pos_array.ndim != 2 or pos_array.shape[1] < 2:
                    continue
            except (ValueError, TypeError):
                continue

            coords = pos_array[:, :2]  # x, y columns

            # Compute inter-frame displacements
            deltas = np.diff(coords, axis=0)
            distances = np.linalg.norm(deltas, axis=1)

            if len(distances) < 2:
                continue

            # Speed analysis
            mean_speed = float(np.mean(distances))
            std_speed = float(np.std(distances))

            if std_speed > 0 and mean_speed > 0:
                # Check for sudden speed spikes
                max_speed = float(np.max(distances))
                speed_z = (max_speed - mean_speed) / std_speed

                if speed_z > self.critical_sigma:
                    confidence = min(1.0, speed_z / (self.critical_sigma * 2))
                    if confidence >= self.min_confidence:
                        anomalies.append(AnomalyEvent(
                            timestamp=datetime.now(timezone.utc).isoformat(),
                            anomaly_type="behavioral",
                            severity="critical" if speed_z > self.critical_sigma * 1.5 else "warning",
                            confidence=confidence,
                            description=(
                                f"Behavioral anomaly: track {track_id} exhibited "
                                f"sudden speed spike ({max_speed:.1f} px/frame vs "
                                f"avg {mean_speed:.1f}), {speed_z:.1f} sigma above normal"
                            ),
                            camera_id=camera_id,
                            baseline_value=mean_speed,
                            observed_value=max_speed,
                            deviation_sigma=speed_z,
                        ))

            # Direction analysis - detect erratic direction changes
            if len(deltas) >= 3:
                # Compute angle changes between consecutive displacement vectors
                angles = []
                for i in range(len(deltas) - 1):
                    v1 = deltas[i]
                    v2 = deltas[i + 1]
                    norm1 = np.linalg.norm(v1)
                    norm2 = np.linalg.norm(v2)
                    if norm1 > 1e-6 and norm2 > 1e-6:
                        cos_angle = np.clip(
                            np.dot(v1, v2) / (norm1 * norm2), -1.0, 1.0
                        )
                        angle = float(np.degrees(np.arccos(cos_angle)))
                        angles.append(angle)

                if angles:
                    mean_angle_change = float(np.mean(angles))
                    # Erratic movement: large average direction changes
                    if mean_angle_change > 90.0:
                        confidence = min(1.0, mean_angle_change / 180.0)
                        if confidence >= self.min_confidence:
                            anomalies.append(AnomalyEvent(
                                timestamp=datetime.now(timezone.utc).isoformat(),
                                anomaly_type="behavioral",
                                severity="warning",
                                confidence=confidence,
                                description=(
                                    f"Behavioral anomaly: track {track_id} shows "
                                    f"erratic movement with avg direction change "
                                    f"of {mean_angle_change:.1f} degrees"
                                ),
                                camera_id=camera_id,
                                baseline_value=45.0,
                                observed_value=mean_angle_change,
                                deviation_sigma=(mean_angle_change - 45.0) / 30.0,
                            ))

        return anomalies

    def detect_frequency_anomaly(
        self,
        event_counts: list[dict[str, Any]],
        window_minutes: int = 15,
        camera_id: Optional[str] = None,
    ) -> list[AnomalyEvent]:
        """Detect sudden spikes or drops in event frequency.

        Uses a rolling window to compare recent event frequency
        against the preceding window of the same length.

        Args:
            event_counts: Time-ordered list of dicts with ``timestamp``
                and ``count`` keys.
            window_minutes: Length of comparison window in minutes.
            camera_id: Camera identifier.

        Returns:
            List of detected AnomalyEvent instances.
        """
        anomalies: list[AnomalyEvent] = []

        if len(event_counts) < 4:
            return anomalies

        # Extract time-ordered counts
        counts = []
        timestamps = []
        for entry in event_counts:
            ts_raw = entry.get("timestamp")
            count = float(entry.get("count", 0))
            if ts_raw is None:
                continue

            if isinstance(ts_raw, str):
                try:
                    ts = datetime.fromisoformat(ts_raw)
                except (ValueError, TypeError):
                    continue
            elif isinstance(ts_raw, datetime):
                ts = ts_raw
            else:
                continue

            counts.append(count)
            timestamps.append(ts)

        if len(counts) < 4:
            return anomalies

        arr = np.array(counts)

        # Split into two halves: "previous" window and "current" window
        midpoint = len(arr) // 2
        previous_window = arr[:midpoint]
        current_window = arr[midpoint:]

        prev_mean = float(np.mean(previous_window))
        prev_std = float(np.std(previous_window))
        curr_mean = float(np.mean(current_window))

        if prev_std <= 0:
            prev_std = max(prev_mean * 0.1, 1.0)

        # Z-score of current window mean against previous distribution
        z_score = (curr_mean - prev_mean) / prev_std
        abs_z = abs(z_score)

        if abs_z < self.warning_sigma:
            return anomalies

        severity = "critical" if abs_z >= self.critical_sigma else "warning"
        confidence = min(1.0, abs_z / (self.critical_sigma * 1.5))

        if confidence < self.min_confidence:
            return anomalies

        direction = "spike" if z_score > 0 else "drop"
        change_pct = ((curr_mean - prev_mean) / max(prev_mean, 0.01)) * 100

        description = (
            f"Frequency anomaly: {direction} detected in {window_minutes}min window. "
            f"Current avg {curr_mean:.1f} vs previous avg {prev_mean:.1f} "
            f"({change_pct:+.0f}%), {abs_z:.1f} sigma deviation"
        )

        anomalies.append(AnomalyEvent(
            timestamp=datetime.now(timezone.utc).isoformat(),
            anomaly_type="frequency",
            severity=severity,
            confidence=confidence,
            description=description,
            camera_id=camera_id,
            baseline_value=prev_mean,
            observed_value=curr_mean,
            deviation_sigma=z_score,
        ))

        return anomalies

    # -- Composite scoring ---------------------------------------------------

    def get_anomaly_score(
        self,
        observations: dict[str, Any],
        baseline: Optional[BaselineProfile] = None,
        camera_id: Optional[str] = None,
    ) -> tuple[float, list[AnomalyEvent]]:
        """Compute a composite anomaly score by running all detectors.

        Combines results from all five detection methods into a single
        score in [0, 1] along with the list of individual anomaly events.

        Args:
            observations: Dictionary containing:
                - ``current_count``: Current observation count.
                - ``hour``: Current hour (0-23).
                - ``day_of_week``: Day of week (0-6).
                - ``zone_id``: Optional zone identifier.
                - ``events_timeline``: List of recent event dicts.
                - ``detections``: List of zone detection dicts.
                - ``tracks``: List of track dicts.
                - ``event_counts``: Time-ordered frequency data.
            baseline: Baseline profile to use.
            camera_id: Camera identifier.

        Returns:
            Tuple of (composite_score, list_of_anomaly_events).
        """
        all_events: list[AnomalyEvent] = []

        # 1. Count anomaly
        current_count = observations.get("current_count")
        hour = observations.get("hour")
        day_of_week = observations.get("day_of_week")
        zone_id = observations.get("zone_id")

        if current_count is not None and hour is not None and day_of_week is not None:
            count_events = self.detect_count_anomaly(
                current_count=current_count,
                hour=hour,
                day_of_week=day_of_week,
                zone_id=zone_id,
                baseline=baseline,
                camera_id=camera_id,
            )
            all_events.extend(count_events)

        # 2. Temporal anomaly
        events_timeline = observations.get("events_timeline", [])
        if events_timeline and baseline:
            temporal_events = self.detect_temporal_anomaly(
                events_timeline=events_timeline,
                baseline=baseline,
                camera_id=camera_id,
            )
            all_events.extend(temporal_events)

        # 3. Spatial anomaly
        detections = observations.get("detections", [])
        zone_baselines = baseline.zone_baselines if baseline else None
        if detections and zone_baselines:
            spatial_events = self.detect_spatial_anomaly(
                detections=detections,
                zone_baselines=zone_baselines,
                camera_id=camera_id,
            )
            all_events.extend(spatial_events)

        # 4. Behavioral anomaly
        tracks = observations.get("tracks", [])
        if tracks:
            behavioral_events = self.detect_behavioral_anomaly(
                tracks=tracks,
                camera_id=camera_id,
            )
            all_events.extend(behavioral_events)

        # 5. Frequency anomaly
        event_counts = observations.get("event_counts", [])
        if event_counts:
            frequency_events = self.detect_frequency_anomaly(
                event_counts=event_counts,
                camera_id=camera_id,
            )
            all_events.extend(frequency_events)

        # Composite score: weighted average of individual confidences
        if not all_events:
            return 0.0, []

        severity_weights = {"critical": 1.0, "warning": 0.6, "info": 0.3}
        weighted_scores = [
            e.confidence * severity_weights.get(e.severity, 0.5)
            for e in all_events
        ]

        # Use max-weighted score plus small contribution from event count
        max_score = max(weighted_scores)
        count_bonus = min(0.2, len(all_events) * 0.03)
        composite = min(1.0, max_score + count_bonus)

        return composite, all_events

    # -- Redis baseline persistence ------------------------------------------

    async def save_baseline_to_redis(
        self,
        camera_id: str,
        zone_id: Optional[str],
        baseline: BaselineProfile,
    ) -> None:
        """Persist a baseline profile to Redis for fast access.

        Args:
            camera_id: Camera identifier.
            zone_id: Optional zone identifier.
            baseline: Baseline profile to store.
        """
        if self._redis is None:
            logger.warning("anomaly_detector.save_baseline.no_redis")
            return

        key = f"visionai:anomaly:baseline:{camera_id}"
        if zone_id:
            key = f"{key}:{zone_id}"

        data = json.dumps(baseline.to_dict())
        await self._redis.set(key, data, ex=86400 * 7)  # 7-day TTL

        # Cache in memory too
        cache_key = f"{camera_id}:{zone_id}" if zone_id else camera_id
        self._baselines[cache_key] = baseline

        logger.debug(
            "anomaly_detector.baseline_saved",
            camera_id=camera_id,
            zone_id=zone_id,
            key=key,
        )

    async def load_baseline_from_redis(
        self,
        camera_id: str,
        zone_id: Optional[str] = None,
    ) -> Optional[BaselineProfile]:
        """Load a baseline profile from Redis.

        Args:
            camera_id: Camera identifier.
            zone_id: Optional zone identifier.

        Returns:
            BaselineProfile if found, else None.
        """
        if self._redis is None:
            logger.warning("anomaly_detector.load_baseline.no_redis")
            return None

        key = f"visionai:anomaly:baseline:{camera_id}"
        if zone_id:
            key = f"{key}:{zone_id}"

        data = await self._redis.get(key)
        if data is None:
            return None

        try:
            profile = BaselineProfile.from_dict(json.loads(data))
            # Cache in memory
            cache_key = f"{camera_id}:{zone_id}" if zone_id else camera_id
            self._baselines[cache_key] = profile
            return profile
        except (json.JSONDecodeError, KeyError, TypeError) as exc:
            logger.warning(
                "anomaly_detector.load_baseline.parse_error",
                key=key,
                error=str(exc),
            )
            return None

    def set_thresholds(
        self,
        warning_sigma: Optional[float] = None,
        critical_sigma: Optional[float] = None,
        min_confidence: Optional[float] = None,
    ) -> None:
        """Update detection thresholds at runtime.

        Args:
            warning_sigma: New warning threshold (sigma).
            critical_sigma: New critical threshold (sigma).
            min_confidence: New minimum confidence.
        """
        if warning_sigma is not None:
            self.warning_sigma = warning_sigma
        if critical_sigma is not None:
            self.critical_sigma = critical_sigma
        if min_confidence is not None:
            self.min_confidence = min_confidence

        logger.info(
            "anomaly_detector.thresholds_updated",
            warning_sigma=self.warning_sigma,
            critical_sigma=self.critical_sigma,
            min_confidence=self.min_confidence,
        )
