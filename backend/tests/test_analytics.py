"""Tests for analytics services and computations."""

import pytest
import numpy as np


class TestHeatmapGeneration:
    """Test heatmap generation utilities."""

    def test_heatmap_accumulation(self) -> None:
        """Test adding points to heatmap accumulator."""
        import cv2

        width, height = 640, 480
        accumulator = np.zeros((height, width), dtype=np.float64)

        # Add some detection points
        points = [(100, 200), (300, 300), (500, 100), (100, 200), (105, 195)]
        for x, y in points:
            cv2.circle(accumulator, (x, y), 20, 1.0, -1)

        assert accumulator.sum() > 0
        # Area around (100, 200) should have highest intensity (2 nearby points)
        assert accumulator[200, 100] > 0

    def test_heatmap_colormap(self) -> None:
        """Test applying colormap to heatmap."""
        import cv2

        accumulator = np.random.rand(480, 640).astype(np.float64)
        blurred = cv2.GaussianBlur(accumulator, (51, 51), 0)
        normalized = cv2.normalize(blurred, None, 0, 255, cv2.NORM_MINMAX).astype(np.uint8)
        colored = cv2.applyColorMap(normalized, cv2.COLORMAP_JET)

        assert colored.shape == (480, 640, 3)
        assert colored.dtype == np.uint8

    def test_heatmap_overlay(self) -> None:
        """Test overlaying heatmap on background image."""
        import cv2

        background = np.random.randint(0, 255, (480, 640, 3), dtype=np.uint8)
        heatmap = np.random.randint(0, 255, (480, 640, 3), dtype=np.uint8)

        alpha = 0.5
        overlay = cv2.addWeighted(background, 1 - alpha, heatmap, alpha, 0)

        assert overlay.shape == background.shape
        assert overlay.dtype == np.uint8


class TestTripwireCounting:
    """Test tripwire-based footfall counting."""

    def test_line_crossing_detection(self) -> None:
        """Test detecting when a track crosses a tripwire line."""
        from app.utils.geometry import check_tripwire_crossing

        # Track moving left to right across a vertical line at x=0.5
        line_start = (0.5, 0.0)
        line_end = (0.5, 1.0)

        # Crossing from left to right
        result = check_tripwire_crossing(
            prev_pos=(0.4, 0.5),
            curr_pos=(0.6, 0.5),
            line_start=line_start,
            line_end=line_end,
        )
        assert result is not None

        # No crossing (both on same side)
        result = check_tripwire_crossing(
            prev_pos=(0.3, 0.5),
            curr_pos=(0.4, 0.5),
            line_start=line_start,
            line_end=line_end,
        )
        assert result is None

    def test_zone_occupancy_count(self) -> None:
        """Test counting detections inside a zone polygon."""
        from app.utils.geometry import point_in_polygon

        zone = [(0.2, 0.2), (0.8, 0.2), (0.8, 0.8), (0.2, 0.8)]

        detections = [
            (0.5, 0.5),  # inside
            (0.3, 0.3),  # inside
            (0.1, 0.1),  # outside
            (0.7, 0.7),  # inside
            (0.9, 0.9),  # outside
        ]

        count = sum(1 for d in detections if point_in_polygon(d, zone))
        assert count == 3


class TestBehaviorAnalysis:
    """Test behavioral analysis functions."""

    def test_loitering_duration_tracking(self) -> None:
        """Test tracking duration of a person in a zone."""
        from datetime import datetime, timedelta

        # Simulate track history
        track_history = {}
        track_id = 1
        zone_id = "zone_1"

        # Person enters zone
        entry_time = datetime.now()
        track_history[track_id] = {"zone": zone_id, "entry_time": entry_time}

        # After 5 minutes
        current_time = entry_time + timedelta(minutes=5)
        duration = (current_time - track_history[track_id]["entry_time"]).total_seconds()

        assert duration == 300  # 5 minutes = 300 seconds

        # Check against threshold
        loiter_threshold = 180  # 3 minutes
        assert duration > loiter_threshold  # Should trigger loitering alert

    def test_speed_calculation(self) -> None:
        """Test movement speed calculation from positions."""
        fps = 30
        # Positions at consecutive frames
        positions = [(100, 200), (105, 203), (112, 208)]

        speeds = []
        for i in range(1, len(positions)):
            dx = positions[i][0] - positions[i - 1][0]
            dy = positions[i][1] - positions[i - 1][1]
            pixel_speed = np.sqrt(dx**2 + dy**2) * fps  # pixels per second
            speeds.append(pixel_speed)

        avg_speed = np.mean(speeds)
        assert avg_speed > 0
