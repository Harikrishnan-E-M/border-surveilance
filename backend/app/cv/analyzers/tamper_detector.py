"""
Camera Tamper Detector for VisionAI.

Detects camera tampering events by analysing frame-level image
properties.  Supports three detection modes:

1. **SSIM comparison**: Structural similarity comparison against a
   reference frame to detect large-scale scene changes (camera moved,
   spray-painted, covered).
2. **Black/white frame detection**: Identifies fully black or fully
   white frames indicating lens occlusion or sensor failure.
3. **Blur detection**: Detects excessive blur (Laplacian variance)
   suggesting defocusing or vaseline/spray attacks.

Usage::

    from app.cv.analyzers.tamper_detector import TamperDetector

    detector = TamperDetector()
    detector.set_reference_frame(reference_image)
    result = detector.analyze(current_frame)
    if result["tampered"]:
        print(f"Tamper detected: {result['reasons']}")
"""

from __future__ import annotations

from typing import Optional

import cv2
import numpy as np
import structlog

logger = structlog.stdlib.get_logger(__name__)


class TamperDetector:
    """Camera tamper detection engine.

    Analyses individual frames for signs of camera tampering.  A
    reference frame must be set before SSIM-based detection works.

    Args:
        ssim_threshold: Minimum SSIM score to consider the scene
            unchanged.  Below this threshold, tampering is flagged.
            Defaults to ``0.5``.
        black_threshold: Maximum mean pixel intensity for a frame to
            be considered "black".  Defaults to ``15``.
        white_threshold: Minimum mean pixel intensity for a frame to
            be considered "white".  Defaults to ``240``.
        blur_threshold: Minimum Laplacian variance for a frame to be
            considered "in focus".  Below this, blur tampering is
            flagged.  Defaults to ``50.0``.
        uniformity_threshold: Maximum standard deviation of pixel
            intensities for a frame to be considered uniform (solid
            colour obstruction).  Defaults to ``10.0``.
    """

    def __init__(
        self,
        ssim_threshold: float = 0.5,
        black_threshold: int = 15,
        white_threshold: int = 240,
        blur_threshold: float = 50.0,
        uniformity_threshold: float = 10.0,
    ) -> None:
        self.ssim_threshold = ssim_threshold
        self.black_threshold = black_threshold
        self.white_threshold = white_threshold
        self.blur_threshold = blur_threshold
        self.uniformity_threshold = uniformity_threshold

        self._reference_frame: Optional[np.ndarray] = None
        self._reference_gray: Optional[np.ndarray] = None

        logger.info(
            "tamper_detector.initialised",
            ssim_threshold=ssim_threshold,
            blur_threshold=blur_threshold,
        )

    def set_reference_frame(self, frame: np.ndarray) -> None:
        """Set the reference frame for SSIM comparison.

        Should be called once when the camera is known to be in a
        normal state (e.g. at startup or after manual confirmation).

        Args:
            frame: BGR reference image.
        """
        if frame is None or frame.size == 0:
            raise ValueError("Reference frame is empty or None.")

        self._reference_frame = frame.copy()
        self._reference_gray = cv2.cvtColor(frame, cv2.COLOR_BGR2GRAY)

        logger.info(
            "tamper_detector.reference_set",
            shape=frame.shape,
        )

    def analyze(self, frame: np.ndarray) -> dict:
        """Analyse a frame for tampering indicators.

        Runs all detection checks and returns a consolidated result.

        Args:
            frame: Current BGR frame to analyse.

        Returns:
            dict: Analysis result with keys:
                - ``tampered`` (*bool*): ``True`` if any tampering
                  detected.
                - ``reasons`` (*list[str]*): List of detected issues.
                - ``ssim_score`` (*float | None*): SSIM score if
                  reference is set.
                - ``blur_score`` (*float*): Laplacian variance.
                - ``mean_intensity`` (*float*): Mean pixel intensity.
                - ``std_intensity`` (*float*): Std dev of pixel intensity.
                - ``is_black`` (*bool*): Black frame detected.
                - ``is_white`` (*bool*): White frame detected.
                - ``is_blurred`` (*bool*): Excessive blur detected.
                - ``is_uniform`` (*bool*): Uniform colour detected.
                - ``scene_changed`` (*bool*): SSIM below threshold.
        """
        if frame is None or frame.size == 0:
            return {
                "tampered": True,
                "reasons": ["empty_frame"],
                "ssim_score": None,
                "blur_score": 0.0,
                "mean_intensity": 0.0,
                "std_intensity": 0.0,
                "is_black": True,
                "is_white": False,
                "is_blurred": True,
                "is_uniform": True,
                "scene_changed": True,
            }

        gray = cv2.cvtColor(frame, cv2.COLOR_BGR2GRAY)
        reasons: list[str] = []

        # ── Black/white frame detection ───────────────────────────────
        mean_intensity = float(gray.mean())
        std_intensity = float(gray.std())

        is_black = mean_intensity < self.black_threshold
        is_white = mean_intensity > self.white_threshold
        is_uniform = std_intensity < self.uniformity_threshold

        if is_black:
            reasons.append("black_frame")
        if is_white:
            reasons.append("white_frame")
        if is_uniform and not is_black and not is_white:
            reasons.append("uniform_frame")

        # ── Blur detection ────────────────────────────────────────────
        blur_score = self._compute_blur_score(gray)
        is_blurred = blur_score < self.blur_threshold

        if is_blurred and not is_black and not is_white:
            reasons.append("excessive_blur")

        # ── SSIM comparison ───────────────────────────────────────────
        ssim_score: Optional[float] = None
        scene_changed = False

        if self._reference_gray is not None:
            ssim_score = self._compute_ssim(self._reference_gray, gray)
            scene_changed = ssim_score < self.ssim_threshold

            if scene_changed and not is_black and not is_white:
                reasons.append("scene_changed")

        tampered = len(reasons) > 0

        return {
            "tampered": tampered,
            "reasons": reasons,
            "ssim_score": round(ssim_score, 4) if ssim_score is not None else None,
            "blur_score": round(blur_score, 2),
            "mean_intensity": round(mean_intensity, 2),
            "std_intensity": round(std_intensity, 2),
            "is_black": is_black,
            "is_white": is_white,
            "is_blurred": is_blurred,
            "is_uniform": is_uniform,
            "scene_changed": scene_changed,
        }

    @staticmethod
    def _compute_blur_score(gray: np.ndarray) -> float:
        """Compute the Laplacian variance as a blur metric.

        Higher values indicate sharper images; lower values indicate
        more blur.

        Args:
            gray: Grayscale image.

        Returns:
            float: Laplacian variance (non-negative).
        """
        laplacian = cv2.Laplacian(gray, cv2.CV_64F)
        return float(laplacian.var())

    @staticmethod
    def _compute_ssim(
        reference: np.ndarray,
        current: np.ndarray,
        win_size: int = 11,
    ) -> float:
        """Compute the Structural Similarity Index (SSIM) between two
        grayscale images.

        Implements the SSIM formula with Gaussian-weighted statistics
        using OpenCV's built-in functions.

        Args:
            reference: Grayscale reference image.
            current: Grayscale current image.
            win_size: Size of the Gaussian smoothing window.

        Returns:
            float: SSIM score in ``[-1, 1]``.  1.0 means identical.
        """
        # Ensure same size
        if reference.shape != current.shape:
            current = cv2.resize(current, (reference.shape[1], reference.shape[0]))

        ref = reference.astype(np.float64)
        cur = current.astype(np.float64)

        C1 = (0.01 * 255) ** 2
        C2 = (0.03 * 255) ** 2

        # Gaussian-weighted means
        mu1 = cv2.GaussianBlur(ref, (win_size, win_size), 1.5)
        mu2 = cv2.GaussianBlur(cur, (win_size, win_size), 1.5)

        mu1_sq = mu1 ** 2
        mu2_sq = mu2 ** 2
        mu1_mu2 = mu1 * mu2

        # Gaussian-weighted variances and covariance
        sigma1_sq = cv2.GaussianBlur(ref ** 2, (win_size, win_size), 1.5) - mu1_sq
        sigma2_sq = cv2.GaussianBlur(cur ** 2, (win_size, win_size), 1.5) - mu2_sq
        sigma12 = cv2.GaussianBlur(ref * cur, (win_size, win_size), 1.5) - mu1_mu2

        # SSIM formula
        numerator = (2 * mu1_mu2 + C1) * (2 * sigma12 + C2)
        denominator = (mu1_sq + mu2_sq + C1) * (sigma1_sq + sigma2_sq + C2)

        ssim_map = numerator / denominator
        return float(ssim_map.mean())

    def detect_black_frame(self, frame: np.ndarray) -> bool:
        """Check if a frame is predominantly black.

        Args:
            frame: BGR image.

        Returns:
            bool: ``True`` if the frame is black.
        """
        gray = cv2.cvtColor(frame, cv2.COLOR_BGR2GRAY)
        return float(gray.mean()) < self.black_threshold

    def detect_white_frame(self, frame: np.ndarray) -> bool:
        """Check if a frame is predominantly white.

        Args:
            frame: BGR image.

        Returns:
            bool: ``True`` if the frame is white.
        """
        gray = cv2.cvtColor(frame, cv2.COLOR_BGR2GRAY)
        return float(gray.mean()) > self.white_threshold

    def detect_blur(self, frame: np.ndarray) -> bool:
        """Check if a frame is excessively blurry.

        Args:
            frame: BGR image.

        Returns:
            bool: ``True`` if blur is detected.
        """
        gray = cv2.cvtColor(frame, cv2.COLOR_BGR2GRAY)
        score = self._compute_blur_score(gray)
        return score < self.blur_threshold

    def update_reference(self, frame: np.ndarray) -> None:
        """Update the reference frame (alias for ``set_reference_frame``).

        Args:
            frame: New BGR reference image.
        """
        self.set_reference_frame(frame)
