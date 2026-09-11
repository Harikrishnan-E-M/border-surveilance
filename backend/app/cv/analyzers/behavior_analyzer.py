"""
Behavior Analyzer for VisionAI.

Analyses tracked object movement patterns over time to detect
behavioural anomalies such as loitering, wrong-direction movement,
tailgating, and idle persons.

The analyzer maintains a sliding-window history of track positions
and computes temporal features (dwell time, speed, direction) for
each tracked object.

Usage::

    from app.cv.analyzers.behavior_analyzer import BehaviorAnalyzer

    analyzer = BehaviorAnalyzer()
    events = analyzer.analyze(tracked_objects, frame_id, timestamp)
    for event in events:
        print(f"{event['type']}: track {event['track_id']}")
"""

from __future__ import annotations

import time
from collections import defaultdict
from dataclasses import dataclass, field
from typing import Optional

import numpy as np
import structlog

logger = structlog.stdlib.get_logger(__name__)


@dataclass
class TrackHistory:
    """Position and timing history for a single track.

    Attributes:
        track_id: Unique track identifier.
        positions: List of ``(x, y, timestamp)`` tuples.
        first_seen: Timestamp when the track was first observed.
        last_seen: Timestamp of the most recent observation.
        class_name: Object class (e.g. ``"person"``).
    """

    track_id: int
    positions: list[tuple[float, float, float]] = field(default_factory=list)
    first_seen: float = 0.0
    last_seen: float = 0.0
    class_name: str = "unknown"

    @property
    def dwell_time(self) -> float:
        """Return total dwell time in seconds."""
        return self.last_seen - self.first_seen

    @property
    def position_count(self) -> int:
        """Return number of recorded positions."""
        return len(self.positions)


@dataclass
class BehaviorEvent:
    """A detected behavioural anomaly event.

    Attributes:
        event_type: Type of behaviour (e.g. ``"loitering"``).
        track_id: Track identifier of the subject.
        confidence: Confidence level of the detection.
        details: Additional event details.
        timestamp: Event timestamp.
    """

    event_type: str
    track_id: int
    confidence: float
    details: dict = field(default_factory=dict)
    timestamp: float = 0.0

    def to_dict(self) -> dict:
        """Serialise to a plain dictionary."""
        return {
            "event_type": self.event_type,
            "track_id": self.track_id,
            "confidence": round(self.confidence, 4),
            "details": self.details,
            "timestamp": self.timestamp,
        }


class BehaviorAnalyzer:
    """Track-based behaviour analysis engine.

    Maintains position history for each tracked object and applies
    heuristic rules to detect anomalous behaviours.

    Args:
        history_max_seconds: Maximum seconds of history to retain per
            track.  Defaults to ``120``.
        history_max_points: Maximum number of position samples per
            track.  Defaults to ``3600`` (30 fps * 120 s).
        loiter_time_threshold: Seconds of low displacement before
            loitering is flagged.  Defaults to ``30``.
        loiter_distance_threshold: Maximum displacement (pixels) during
            the loiter window.  Defaults to ``50``.
        idle_time_threshold: Seconds a person must be stationary to be
            flagged as idle.  Defaults to ``60``.
        idle_distance_threshold: Maximum displacement for idle detection.
            Defaults to ``20``.
        tailgate_distance_threshold: Maximum inter-person distance
            (pixels) to consider tailgating.  Defaults to ``80``.
        tailgate_time_threshold: Seconds of close proximity to trigger.
            Defaults to ``3``.
    """

    def __init__(
        self,
        history_max_seconds: float = 120.0,
        history_max_points: int = 3600,
        loiter_time_threshold: float = 30.0,
        loiter_distance_threshold: float = 50.0,
        idle_time_threshold: float = 60.0,
        idle_distance_threshold: float = 20.0,
        tailgate_distance_threshold: float = 80.0,
        tailgate_time_threshold: float = 3.0,
    ) -> None:
        self.history_max_seconds = history_max_seconds
        self.history_max_points = history_max_points
        self.loiter_time_threshold = loiter_time_threshold
        self.loiter_distance_threshold = loiter_distance_threshold
        self.idle_time_threshold = idle_time_threshold
        self.idle_distance_threshold = idle_distance_threshold
        self.tailgate_distance_threshold = tailgate_distance_threshold
        self.tailgate_time_threshold = tailgate_time_threshold

        self._histories: dict[int, TrackHistory] = {}
        # Track pairs that have been close: {(id_a, id_b): first_close_timestamp}
        self._close_pairs: dict[tuple[int, int], float] = {}
        # Set of already-alerted events to avoid duplicates
        self._alerted_loiter: set[int] = set()
        self._alerted_idle: set[int] = set()

        logger.info(
            "behavior_analyzer.initialised",
            loiter_time=loiter_time_threshold,
            idle_time=idle_time_threshold,
        )

    def update_track(
        self,
        track_id: int,
        position: tuple[float, float],
        timestamp: float,
        class_name: str = "person",
    ) -> None:
        """Record a new position for a tracked object.

        Args:
            track_id: Unique track identifier.
            position: Centre point ``(x, y)`` in pixel coordinates.
            timestamp: Current timestamp in seconds.
            class_name: Object class label.
        """
        if track_id not in self._histories:
            self._histories[track_id] = TrackHistory(
                track_id=track_id,
                first_seen=timestamp,
                class_name=class_name,
            )

        history = self._histories[track_id]
        history.positions.append((position[0], position[1], timestamp))
        history.last_seen = timestamp
        history.class_name = class_name

        # Trim old entries
        self._trim_history(history, timestamp)

    def _trim_history(self, history: TrackHistory, current_time: float) -> None:
        """Remove position entries older than the retention window.

        Args:
            history: Track history to trim.
            current_time: Current timestamp.
        """
        cutoff = current_time - self.history_max_seconds

        # Remove entries older than cutoff
        while (
            history.positions
            and history.positions[0][2] < cutoff
        ):
            history.positions.pop(0)

        # Enforce max points
        if len(history.positions) > self.history_max_points:
            excess = len(history.positions) - self.history_max_points
            history.positions = history.positions[excess:]

    def detect_loitering(
        self,
        track_id: int,
        timestamp: float,
    ) -> Optional[BehaviorEvent]:
        """Detect loitering for a specific track.

        Loitering is detected when a person stays within a small
        spatial area for longer than the configured threshold.

        Args:
            track_id: Track to check.
            timestamp: Current timestamp.

        Returns:
            Optional[BehaviorEvent]: Loitering event or ``None``.
        """
        history = self._histories.get(track_id)
        if history is None:
            return None

        if track_id in self._alerted_loiter:
            return None

        dwell = history.dwell_time
        if dwell < self.loiter_time_threshold:
            return None

        # Compute displacement over the loiter window
        window_start = timestamp - self.loiter_time_threshold
        positions_in_window = [
            (p[0], p[1]) for p in history.positions
            if p[2] >= window_start
        ]

        if len(positions_in_window) < 2:
            return None

        positions_array = np.array(positions_in_window, dtype=np.float64)
        centroid = positions_array.mean(axis=0)
        max_displacement = np.max(
            np.linalg.norm(positions_array - centroid, axis=1)
        )

        if max_displacement <= self.loiter_distance_threshold:
            self._alerted_loiter.add(track_id)
            return BehaviorEvent(
                event_type="loitering",
                track_id=track_id,
                confidence=min(1.0, dwell / (self.loiter_time_threshold * 2)),
                details={
                    "dwell_time_seconds": round(dwell, 1),
                    "max_displacement_px": round(float(max_displacement), 1),
                    "centroid": [round(centroid[0], 1), round(centroid[1], 1)],
                },
                timestamp=timestamp,
            )

        return None

    def detect_wrong_direction(
        self,
        track_id: int,
        expected_direction: tuple[float, float],
        timestamp: float,
        angle_tolerance: float = 90.0,
    ) -> Optional[BehaviorEvent]:
        """Detect if a tracked object is moving in the wrong direction.

        Computes the average movement vector over recent history and
        compares it to the expected direction.

        Args:
            track_id: Track to check.
            expected_direction: Expected movement direction as a unit
                vector ``(dx, dy)``.
            timestamp: Current timestamp.
            angle_tolerance: Maximum angular deviation (degrees) before
                triggering.

        Returns:
            Optional[BehaviorEvent]: Wrong-direction event or ``None``.
        """
        history = self._histories.get(track_id)
        if history is None or len(history.positions) < 5:
            return None

        # Use the last N positions to compute movement direction
        recent = history.positions[-10:]
        if len(recent) < 3:
            return None

        # Average displacement vector
        start = np.array([recent[0][0], recent[0][1]], dtype=np.float64)
        end = np.array([recent[-1][0], recent[-1][1]], dtype=np.float64)
        movement = end - start

        movement_norm = np.linalg.norm(movement)
        if movement_norm < 5.0:
            # Too little movement to determine direction
            return None

        movement_unit = movement / movement_norm
        expected = np.array(expected_direction, dtype=np.float64)
        expected_norm = np.linalg.norm(expected)
        if expected_norm == 0:
            return None
        expected_unit = expected / expected_norm

        # Compute angle between vectors
        dot_product = np.clip(np.dot(movement_unit, expected_unit), -1.0, 1.0)
        angle_deg = float(np.degrees(np.arccos(dot_product)))

        if angle_deg > angle_tolerance:
            return BehaviorEvent(
                event_type="wrong_direction",
                track_id=track_id,
                confidence=min(1.0, angle_deg / 180.0),
                details={
                    "angle_deviation_deg": round(angle_deg, 1),
                    "movement_vector": [round(movement_unit[0], 3), round(movement_unit[1], 3)],
                    "expected_vector": [round(expected_unit[0], 3), round(expected_unit[1], 3)],
                },
                timestamp=timestamp,
            )

        return None

    def detect_tailgating(
        self,
        tracked_objects: list[dict],
        timestamp: float,
    ) -> list[BehaviorEvent]:
        """Detect tailgating between pairs of tracked persons.

        Tailgating is flagged when two persons remain within close
        proximity for longer than the threshold, suggesting one is
        following another closely (e.g. through a secure door).

        Args:
            tracked_objects: List of tracked object dicts with keys
                ``track_id``, ``bbox``, ``class_name``.
            timestamp: Current timestamp.

        Returns:
            list[BehaviorEvent]: Tailgating events.
        """
        events: list[BehaviorEvent] = []

        # Filter to persons only
        persons = [
            obj for obj in tracked_objects
            if obj.get("class_name", "").lower() == "person"
        ]

        if len(persons) < 2:
            return events

        # Compute pairwise distances
        for i in range(len(persons)):
            for j in range(i + 1, len(persons)):
                obj_a = persons[i]
                obj_b = persons[j]

                bbox_a = obj_a["bbox"]
                bbox_b = obj_b["bbox"]

                cx_a = (bbox_a[0] + bbox_a[2]) / 2.0
                cy_a = (bbox_a[1] + bbox_a[3]) / 2.0
                cx_b = (bbox_b[0] + bbox_b[2]) / 2.0
                cy_b = (bbox_b[1] + bbox_b[3]) / 2.0

                distance = np.sqrt((cx_a - cx_b) ** 2 + (cy_a - cy_b) ** 2)

                id_a = obj_a["track_id"]
                id_b = obj_b["track_id"]
                pair_key = (min(id_a, id_b), max(id_a, id_b))

                if distance <= self.tailgate_distance_threshold:
                    if pair_key not in self._close_pairs:
                        self._close_pairs[pair_key] = timestamp
                    else:
                        close_duration = timestamp - self._close_pairs[pair_key]
                        if close_duration >= self.tailgate_time_threshold:
                            events.append(BehaviorEvent(
                                event_type="tailgating",
                                track_id=id_a,
                                confidence=min(1.0, close_duration / (self.tailgate_time_threshold * 3)),
                                details={
                                    "other_track_id": id_b,
                                    "distance_px": round(float(distance), 1),
                                    "duration_seconds": round(close_duration, 1),
                                },
                                timestamp=timestamp,
                            ))
                            # Reset to avoid repeated alerts
                            del self._close_pairs[pair_key]
                else:
                    # No longer close, remove pair
                    self._close_pairs.pop(pair_key, None)

        return events

    def detect_idle_person(
        self,
        track_id: int,
        timestamp: float,
    ) -> Optional[BehaviorEvent]:
        """Detect a person who has been stationary for too long.

        Similar to loitering but with a longer time threshold and
        stricter displacement limit (person is nearly motionless).

        Args:
            track_id: Track to check.
            timestamp: Current timestamp.

        Returns:
            Optional[BehaviorEvent]: Idle event or ``None``.
        """
        history = self._histories.get(track_id)
        if history is None:
            return None

        if track_id in self._alerted_idle:
            return None

        dwell = history.dwell_time
        if dwell < self.idle_time_threshold:
            return None

        # Compute displacement over the idle window
        window_start = timestamp - self.idle_time_threshold
        positions_in_window = [
            (p[0], p[1]) for p in history.positions
            if p[2] >= window_start
        ]

        if len(positions_in_window) < 2:
            return None

        positions_array = np.array(positions_in_window, dtype=np.float64)
        centroid = positions_array.mean(axis=0)
        max_displacement = np.max(
            np.linalg.norm(positions_array - centroid, axis=1)
        )

        if max_displacement <= self.idle_distance_threshold:
            self._alerted_idle.add(track_id)
            return BehaviorEvent(
                event_type="idle_person",
                track_id=track_id,
                confidence=min(1.0, dwell / (self.idle_time_threshold * 2)),
                details={
                    "dwell_time_seconds": round(dwell, 1),
                    "max_displacement_px": round(float(max_displacement), 1),
                    "centroid": [round(centroid[0], 1), round(centroid[1], 1)],
                },
                timestamp=timestamp,
            )

        return None

    def analyze(
        self,
        tracked_objects: list[dict],
        timestamp: float,
        expected_direction: tuple[float, float] | None = None,
    ) -> list[BehaviorEvent]:
        """Run all behaviour analyses on the current set of tracked objects.

        Updates track histories and checks for all configured anomalies.

        Args:
            tracked_objects: List of tracked object dicts with keys
                ``track_id``, ``bbox``, ``class_name``.
            timestamp: Current timestamp in seconds.
            expected_direction: Optional expected movement direction
                for wrong-direction detection.

        Returns:
            list[BehaviorEvent]: All detected behaviour events.
        """
        events: list[BehaviorEvent] = []

        # Update histories
        for obj in tracked_objects:
            track_id = obj.get("track_id")
            bbox = obj.get("bbox")
            if track_id is None or bbox is None:
                continue

            cx = (bbox[0] + bbox[2]) / 2.0
            cy = (bbox[1] + bbox[3]) / 2.0
            class_name = obj.get("class_name", "unknown")

            self.update_track(track_id, (cx, cy), timestamp, class_name)

        # Check behaviours for each person track
        for obj in tracked_objects:
            track_id = obj.get("track_id")
            if track_id is None:
                continue

            class_name = obj.get("class_name", "unknown")
            if class_name.lower() != "person":
                continue

            # Loitering
            event = self.detect_loitering(track_id, timestamp)
            if event:
                events.append(event)

            # Idle
            event = self.detect_idle_person(track_id, timestamp)
            if event:
                events.append(event)

            # Wrong direction
            if expected_direction is not None:
                event = self.detect_wrong_direction(
                    track_id, expected_direction, timestamp,
                )
                if event:
                    events.append(event)

        # Tailgating
        tailgate_events = self.detect_tailgating(tracked_objects, timestamp)
        events.extend(tailgate_events)

        return events

    def cleanup(self, active_track_ids: set[int]) -> None:
        """Remove history for tracks that are no longer active.

        Args:
            active_track_ids: Set of currently active track IDs.
        """
        stale = [
            tid for tid in self._histories
            if tid not in active_track_ids
        ]
        for tid in stale:
            del self._histories[tid]
            self._alerted_loiter.discard(tid)
            self._alerted_idle.discard(tid)

        # Clean up close pairs
        stale_pairs = [
            pair for pair in self._close_pairs
            if pair[0] not in active_track_ids or pair[1] not in active_track_ids
        ]
        for pair in stale_pairs:
            del self._close_pairs[pair]

    def get_track_stats(self, track_id: int) -> Optional[dict]:
        """Return statistics for a specific track.

        Args:
            track_id: Track identifier.

        Returns:
            Optional[dict]: Track statistics or ``None``.
        """
        history = self._histories.get(track_id)
        if history is None:
            return None

        positions = history.positions
        if len(positions) < 2:
            total_distance = 0.0
            avg_speed = 0.0
        else:
            deltas = []
            for i in range(1, len(positions)):
                dx = positions[i][0] - positions[i - 1][0]
                dy = positions[i][1] - positions[i - 1][1]
                deltas.append(np.sqrt(dx ** 2 + dy ** 2))
            total_distance = sum(deltas)
            time_span = positions[-1][2] - positions[0][2]
            avg_speed = total_distance / time_span if time_span > 0 else 0.0

        return {
            "track_id": track_id,
            "class_name": history.class_name,
            "dwell_time_seconds": round(history.dwell_time, 1),
            "total_distance_px": round(total_distance, 1),
            "avg_speed_px_per_sec": round(avg_speed, 1),
            "position_count": history.position_count,
        }
