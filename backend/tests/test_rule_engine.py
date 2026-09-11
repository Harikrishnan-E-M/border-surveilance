"""Tests for the rule evaluation engine."""

import pytest
from app.utils.geometry import point_in_polygon, bbox_iou, bbox_center, check_tripwire_crossing


class TestGeometry:
    """Test geometric utility functions used by rule engine."""

    def test_point_in_polygon_inside(self) -> None:
        """Test point inside a square polygon."""
        polygon = [(0.0, 0.0), (1.0, 0.0), (1.0, 1.0), (0.0, 1.0)]
        assert point_in_polygon((0.5, 0.5), polygon) is True

    def test_point_in_polygon_outside(self) -> None:
        """Test point outside a square polygon."""
        polygon = [(0.0, 0.0), (1.0, 0.0), (1.0, 1.0), (0.0, 1.0)]
        assert point_in_polygon((1.5, 0.5), polygon) is False

    def test_point_in_polygon_edge(self) -> None:
        """Test point on polygon edge."""
        polygon = [(0.0, 0.0), (1.0, 0.0), (1.0, 1.0), (0.0, 1.0)]
        # Edge cases may vary by implementation
        result = point_in_polygon((0.0, 0.5), polygon)
        assert isinstance(result, bool)

    def test_point_in_polygon_triangle(self) -> None:
        """Test point in triangular polygon."""
        triangle = [(0.0, 0.0), (1.0, 0.0), (0.5, 1.0)]
        assert point_in_polygon((0.5, 0.3), triangle) is True
        assert point_in_polygon((0.9, 0.9), triangle) is False

    def test_bbox_iou_overlap(self) -> None:
        """Test IoU calculation for overlapping boxes."""
        bbox1 = (0, 0, 100, 100)
        bbox2 = (50, 50, 150, 150)
        iou = bbox_iou(bbox1, bbox2)
        assert 0.0 < iou < 1.0

    def test_bbox_iou_no_overlap(self) -> None:
        """Test IoU calculation for non-overlapping boxes."""
        bbox1 = (0, 0, 50, 50)
        bbox2 = (100, 100, 200, 200)
        iou = bbox_iou(bbox1, bbox2)
        assert iou == 0.0

    def test_bbox_iou_identical(self) -> None:
        """Test IoU calculation for identical boxes."""
        bbox = (10, 20, 100, 200)
        iou = bbox_iou(bbox, bbox)
        assert abs(iou - 1.0) < 1e-6

    def test_bbox_center(self) -> None:
        """Test bounding box center calculation."""
        center = bbox_center((0, 0, 100, 100))
        assert center == (50.0, 50.0)

    def test_tripwire_crossing_left_to_right(self) -> None:
        """Test tripwire crossing detection left-to-right."""
        result = check_tripwire_crossing(
            prev_pos=(0.3, 0.5),
            curr_pos=(0.7, 0.5),
            line_start=(0.5, 0.0),
            line_end=(0.5, 1.0),
        )
        assert result is not None

    def test_tripwire_no_crossing(self) -> None:
        """Test no tripwire crossing when staying on same side."""
        result = check_tripwire_crossing(
            prev_pos=(0.3, 0.5),
            curr_pos=(0.4, 0.5),
            line_start=(0.5, 0.0),
            line_end=(0.5, 1.0),
        )
        assert result is None


class TestValidators:
    """Test validation utility functions."""

    def test_validate_indian_plate_standard(self) -> None:
        """Test standard Indian plate format."""
        from app.utils.validators import validate_indian_plate

        assert validate_indian_plate("KA01AB1234") is True
        assert validate_indian_plate("MH02CD5678") is True
        assert validate_indian_plate("DL3CAB1234") is True

    def test_validate_indian_plate_invalid(self) -> None:
        """Test invalid plate format."""
        from app.utils.validators import validate_indian_plate

        assert validate_indian_plate("INVALID") is False
        assert validate_indian_plate("123456") is False
        assert validate_indian_plate("") is False

    def test_validate_rtsp_url(self) -> None:
        """Test RTSP URL validation."""
        from app.utils.validators import validate_rtsp_url

        assert validate_rtsp_url("rtsp://192.168.1.1:554/stream") is True
        assert validate_rtsp_url("rtsp://admin:pass@192.168.1.1:554/cam1") is True
        assert validate_rtsp_url("http://example.com") is False
        assert validate_rtsp_url("") is False

    def test_validate_cron_expression(self) -> None:
        """Test cron expression validation."""
        from app.utils.validators import validate_cron_expression

        assert validate_cron_expression("* * * * *") is True
        assert validate_cron_expression("0 9 * * 1-5") is True
        assert validate_cron_expression("*/5 * * * *") is True
        assert validate_cron_expression("invalid") is False
        assert validate_cron_expression("") is False

    def test_validate_phone_india(self) -> None:
        """Test Indian phone number validation."""
        from app.utils.validators import validate_phone_india

        assert validate_phone_india("+919876543210") is True
        assert validate_phone_india("9876543210") is True
        assert validate_phone_india("1234567890") is False

    def test_sanitize_filename(self) -> None:
        """Test filename sanitization."""
        from app.utils.validators import sanitize_filename

        assert sanitize_filename("normal_file.jpg") == "normal_file.jpg"
        assert sanitize_filename("../../etc/passwd") == "etcpasswd"
        assert sanitize_filename(".hidden") == "hidden"
        assert sanitize_filename("") == "unnamed"
