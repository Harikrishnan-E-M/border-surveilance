"""Tests for ANPR (Automatic Number Plate Recognition) pipeline."""

import pytest
from app.utils.validators import validate_indian_plate, normalize_indian_plate


class TestPlateValidation:
    """Test Indian license plate format validation."""

    @pytest.mark.parametrize(
        "plate,expected",
        [
            ("KA01AB1234", True),   # Karnataka standard
            ("MH02CD5678", True),   # Maharashtra
            ("DL3CAB1234", True),   # Delhi
            ("TN01Z9999", True),    # Tamil Nadu
            ("KL01AV0001", True),   # Kerala
            ("AP09AB1234", True),   # Andhra Pradesh
            ("TS08AB1234", True),   # Telangana
            ("INVALID", False),
            ("123456", False),
            ("", False),
            ("AB", False),
            ("KA01", False),
        ],
    )
    def test_plate_formats(self, plate: str, expected: bool) -> None:
        """Test various Indian plate number formats."""
        assert validate_indian_plate(plate) is expected

    def test_normalize_plate(self) -> None:
        """Test plate number normalization."""
        assert normalize_indian_plate("ka-01-ab-1234") == "KA01AB1234"
        assert normalize_indian_plate("  MH 02 CD 5678  ") == "MH02CD5678"
        assert normalize_indian_plate("dl.3c.ab.1234") == "DL3CAB1234"

    def test_plate_with_spaces(self) -> None:
        """Test plate validation with spaces (as OCR might produce)."""
        # After normalization, should be valid
        normalized = normalize_indian_plate("KA 01 AB 1234")
        assert validate_indian_plate(normalized) is True


class TestPlateOCRPreprocessing:
    """Test plate image preprocessing."""

    def test_grayscale_conversion(self) -> None:
        """Test color to grayscale conversion for plate OCR."""
        import cv2
        import numpy as np

        color_plate = np.random.randint(0, 255, (50, 200, 3), dtype=np.uint8)
        gray = cv2.cvtColor(color_plate, cv2.COLOR_BGR2GRAY)

        assert gray.ndim == 2
        assert gray.shape == (50, 200)

    def test_adaptive_threshold(self) -> None:
        """Test adaptive thresholding for plate text extraction."""
        import cv2
        import numpy as np

        gray = np.random.randint(100, 200, (50, 200), dtype=np.uint8)
        binary = cv2.adaptiveThreshold(
            gray, 255, cv2.ADAPTIVE_THRESH_GAUSSIAN_C, cv2.THRESH_BINARY, 11, 2
        )

        assert binary.shape == gray.shape
        assert set(np.unique(binary)).issubset({0, 255})
