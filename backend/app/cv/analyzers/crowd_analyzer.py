"""
Crowd Analyzer for VisionAI.

Provides crowd counting, density estimation, and threshold-based
alerting within defined spatial zones.  Operates on detection results
from the object detector and zone definitions from the zone analyzer.

Usage::

    from app.cv.analyzers.crowd_analyzer import CrowdAnalyzer

    analyzer = CrowdAnalyzer()
    count = analyzer.count_in_zone(detections, zone_polygon)
    density = analyzer.estimate_density(detections, zone_polygon, zone_area_m2=100)
    alert = analyzer.check_threshold(detections, zone_polygon, max_count=20)
"""

from __future__ import annotations

from dataclasses import dataclass, field
from typing import Optional

import numpy as np
import structlog

logger = structlog.stdlib.get_logger(__name__)


@dataclass
class CrowdAlert:
    """Alert generated when crowd thresholds are exceeded.

    Attributes:
        zone_id: Zone identifier.
        zone_name: Human-readable zone name.
        current_count: Current number of persons in the zone.
        max_count: Configured threshold.
        severity: Alert severity (``"warning"``, ``"critical"``).
        density: Estimated density in persons per square metre.
    """

    zone_id: str
    zone_name: str
    current_count: int
    max_count: int
    severity: str
    density: float = 0.0

    def to_dict(self) -> dict:
        """Serialise to a plain dictionary."""
        return {
            "zone_id": self.zone_id,
            "zone_name": self.zone_name,
            "current_count": self.current_count,
            "max_count": self.max_count,
            "severity": self.severity,
            "density": round(self.density, 4),
        }


class CrowdAnalyzer:
    """Crowd analysis and threshold monitoring engine.

    Tracks person counts within zones and generates alerts when
    configured thresholds are breached.

    Args:
        person_class_id: Class ID for persons in the object detector
            output.  Defaults to ``0`` (COCO person).
        warning_ratio: Ratio of max_count at which a warning is raised.
            Defaults to ``0.8``.
        smoothing_window: Number of frames to average counts over for
            smoothing.  Defaults to ``5``.
    """

    def __init__(
        self,
        person_class_id: int = 0,
        warning_ratio: float = 0.8,
        smoothing_window: int = 5,
    ) -> None:
        self.person_class_id = person_class_id
        self.warning_ratio = warning_ratio
        self.smoothing_window = smoothing_window

        # Historical counts per zone for smoothing: {zone_id: [count, count, ...]}
        self._count_history: dict[str, list[int]] = {}

        logger.info(
            "crowd_analyzer.initialised",
            warning_ratio=warning_ratio,
            smoothing_window=smoothing_window,
        )

    def count_in_zone(
        self,
        detections: list[dict],
        zone_polygon: list[tuple[float, float]],
        person_only: bool = True,
    ) -> int:
        """Count detections within a zone polygon.

        Args:
            detections: List of detection dictionaries with keys
                ``bbox`` and optionally ``class_id``.
            zone_polygon: Zone polygon vertices ``[(x, y), ...]``.
            person_only: If ``True``, only count person detections.

        Returns:
            int: Number of detections inside the zone.
        """
        count = 0
        for det in detections:
            if person_only:
                cid = det.get("class_id", self.person_class_id)
                if cid != self.person_class_id:
                    continue

            bbox = det.get("bbox")
            if bbox is None:
                continue

            cx = (bbox[0] + bbox[2]) / 2.0
            cy = (bbox[1] + bbox[3]) / 2.0

            if self._point_in_polygon((cx, cy), zone_polygon):
                count += 1

        return count

    def count_in_zone_smoothed(
        self,
        detections: list[dict],
        zone_polygon: list[tuple[float, float]],
        zone_id: str,
        person_only: bool = True,
    ) -> float:
        """Count detections with temporal smoothing.

        Maintains a sliding window of counts and returns the average.

        Args:
            detections: Detection dictionaries.
            zone_polygon: Zone polygon vertices.
            zone_id: Zone identifier for history tracking.
            person_only: If ``True``, count persons only.

        Returns:
            float: Smoothed count (may be fractional).
        """
        raw_count = self.count_in_zone(detections, zone_polygon, person_only)

        if zone_id not in self._count_history:
            self._count_history[zone_id] = []

        history = self._count_history[zone_id]
        history.append(raw_count)

        # Trim to window size
        if len(history) > self.smoothing_window:
            history.pop(0)

        return float(np.mean(history))

    def estimate_density(
        self,
        detections: list[dict],
        zone_polygon: list[tuple[float, float]],
        zone_area_m2: float,
        pixels_per_metre: float = 1.0,
        person_only: bool = True,
    ) -> float:
        """Estimate crowd density within a zone.

        Density is computed as the number of persons divided by the
        real-world area of the zone.

        Args:
            detections: Detection dictionaries.
            zone_polygon: Zone polygon vertices in pixel coordinates.
            zone_area_m2: Real-world area of the zone in square metres.
                If unknown, the polygon pixel area divided by
                ``pixels_per_metre^2`` is used.
            pixels_per_metre: Scale factor for converting pixels to
                metres.  Used only if ``zone_area_m2`` is zero.
            person_only: Count persons only.

        Returns:
            float: Density in persons per square metre.
        """
        count = self.count_in_zone(detections, zone_polygon, person_only)

        if zone_area_m2 > 0:
            return count / zone_area_m2

        # Fall back to pixel area estimation
        pixel_area = self._polygon_area(zone_polygon)
        if pixel_area <= 0 or pixels_per_metre <= 0:
            return 0.0

        area_m2 = pixel_area / (pixels_per_metre ** 2)
        return count / area_m2 if area_m2 > 0 else 0.0

    def check_threshold(
        self,
        detections: list[dict],
        zone_polygon: list[tuple[float, float]],
        max_count: int,
        zone_id: str = "",
        zone_name: str = "",
        zone_area_m2: float = 0.0,
    ) -> Optional[CrowdAlert]:
        """Check if crowd count exceeds a threshold.

        Args:
            detections: Detection dictionaries.
            zone_polygon: Zone polygon vertices.
            max_count: Maximum allowed persons.
            zone_id: Zone identifier.
            zone_name: Human-readable zone name.
            zone_area_m2: Zone area for density computation.

        Returns:
            Optional[CrowdAlert]: Alert if threshold exceeded, else ``None``.
        """
        count = self.count_in_zone(detections, zone_polygon)

        if count <= 0:
            return None

        density = 0.0
        if zone_area_m2 > 0:
            density = count / zone_area_m2

        # Determine severity
        if count > max_count:
            severity = "critical"
        elif count > max_count * self.warning_ratio:
            severity = "warning"
        else:
            return None

        return CrowdAlert(
            zone_id=zone_id,
            zone_name=zone_name or zone_id,
            current_count=count,
            max_count=max_count,
            severity=severity,
            density=density,
        )

    def analyze_zones(
        self,
        detections: list[dict],
        zones: list[dict],
    ) -> dict[str, dict]:
        """Analyse crowd levels across multiple zones.

        Args:
            detections: Detection dictionaries.
            zones: List of zone configurations.  Each dict should have:
                - ``zone_id``: Zone identifier.
                - ``name``: Zone name.
                - ``polygon``: List of ``(x, y)`` vertices.
                - ``max_occupancy`` (optional): Threshold.
                - ``area_m2`` (optional): Zone area in square metres.

        Returns:
            dict[str, dict]: Per-zone analysis results.
        """
        results: dict[str, dict] = {}

        for zone in zones:
            zone_id = zone.get("zone_id", "")
            polygon = zone.get("polygon", [])
            max_occ = zone.get("max_occupancy")
            area_m2 = zone.get("area_m2", 0.0)
            name = zone.get("name", zone_id)

            count = self.count_in_zone(detections, polygon)
            smoothed = self.count_in_zone_smoothed(
                detections, polygon, zone_id,
            )

            density = 0.0
            if area_m2 > 0 and count > 0:
                density = count / area_m2

            alert = None
            if max_occ is not None and max_occ > 0:
                alert = self.check_threshold(
                    detections, polygon, max_occ,
                    zone_id=zone_id, zone_name=name,
                    zone_area_m2=area_m2,
                )

            results[zone_id] = {
                "zone_id": zone_id,
                "name": name,
                "count": count,
                "smoothed_count": round(smoothed, 1),
                "density": round(density, 4),
                "max_occupancy": max_occ,
                "overcrowded": count > max_occ if max_occ else False,
                "alert": alert.to_dict() if alert else None,
            }

        return results

    def reset_history(self, zone_id: str | None = None) -> None:
        """Reset count history.

        Args:
            zone_id: If provided, reset only the specified zone.
                If ``None``, reset all zones.
        """
        if zone_id is not None:
            self._count_history.pop(zone_id, None)
        else:
            self._count_history.clear()

    @staticmethod
    def _point_in_polygon(
        point: tuple[float, float],
        polygon: list[tuple[float, float]],
    ) -> bool:
        """Ray-casting point-in-polygon test.

        Args:
            point: Test point ``(x, y)``.
            polygon: Polygon vertices.

        Returns:
            bool: ``True`` if inside.
        """
        if len(polygon) < 3:
            return False

        px, py = point
        n = len(polygon)
        inside = False

        j = n - 1
        for i in range(n):
            xi, yi = polygon[i]
            xj, yj = polygon[j]

            if ((yi > py) != (yj > py)) and (
                px < (xj - xi) * (py - yi) / (yj - yi) + xi
            ):
                inside = not inside
            j = i

        return inside

    @staticmethod
    def _polygon_area(polygon: list[tuple[float, float]]) -> float:
        """Compute polygon area using the Shoelace formula.

        Args:
            polygon: Polygon vertices.

        Returns:
            float: Absolute polygon area.
        """
        n = len(polygon)
        if n < 3:
            return 0.0

        area = 0.0
        for i in range(n):
            j = (i + 1) % n
            area += polygon[i][0] * polygon[j][1]
            area -= polygon[j][0] * polygon[i][1]

        return abs(area) / 2.0
