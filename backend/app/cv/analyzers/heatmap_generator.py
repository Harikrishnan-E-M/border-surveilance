"""
Heatmap Generator for VisionAI.

Generates spatial density heatmaps by accumulating detection positions
over time.  Points are Gaussian-blurred and colour-mapped for visual
overlay on camera frames.

Supports time-windowed accumulation, normalisation, and both real-time
overlay generation and periodic snapshot export.

Usage::

    from app.cv.analyzers.heatmap_generator import HeatmapGenerator

    generator = HeatmapGenerator(width=1920, height=1080)
    generator.add_point(500, 300)
    generator.add_point(500, 310)
    overlay = generator.generate_overlay(frame)
"""

from __future__ import annotations

from typing import Optional

import cv2
import numpy as np
import structlog

logger = structlog.stdlib.get_logger(__name__)


class HeatmapGenerator:
    """Spatial density heatmap generator.

    Accumulates detection centre points into a 2D histogram and
    produces colour-mapped heatmap overlays.

    Args:
        width: Frame width in pixels.
        height: Frame height in pixels.
        resolution_scale: Downsample factor for the accumulator
            (e.g. ``4`` means the accumulator is 1/4 of the frame
            resolution for efficiency).  Defaults to ``4``.
        blur_kernel_size: Size of the Gaussian blur kernel applied
            to the accumulator.  Must be odd.  Defaults to ``51``.
        decay_rate: Per-frame decay factor applied to the accumulator
            to fade old detections.  Set to ``1.0`` to disable decay.
            Defaults to ``0.995``.
        colormap: OpenCV colormap constant.  Defaults to
            ``cv2.COLORMAP_JET``.
    """

    def __init__(
        self,
        width: int = 1920,
        height: int = 1080,
        resolution_scale: int = 4,
        blur_kernel_size: int = 51,
        decay_rate: float = 0.995,
        colormap: int = cv2.COLORMAP_JET,
    ) -> None:
        self.width = width
        self.height = height
        self.resolution_scale = resolution_scale
        self.blur_kernel_size = blur_kernel_size | 1  # Ensure odd
        self.decay_rate = decay_rate
        self.colormap = colormap

        # Internal accumulator at reduced resolution
        self._acc_h = max(1, height // resolution_scale)
        self._acc_w = max(1, width // resolution_scale)
        self._accumulator = np.zeros(
            (self._acc_h, self._acc_w), dtype=np.float64,
        )

        self._total_points: int = 0
        self._frame_count: int = 0

        logger.info(
            "heatmap_generator.initialised",
            frame_size=(width, height),
            accumulator_size=(self._acc_w, self._acc_h),
            decay_rate=decay_rate,
        )

    def add_point(self, x: float, y: float, weight: float = 1.0) -> None:
        """Add a single detection point to the accumulator.

        Args:
            x: X coordinate in pixel space.
            y: Y coordinate in pixel space.
            weight: Intensity weight for this point.  Defaults to ``1.0``.
        """
        # Map to accumulator coordinates
        ax = int(x / self.resolution_scale)
        ay = int(y / self.resolution_scale)

        # Bounds check
        if 0 <= ax < self._acc_w and 0 <= ay < self._acc_h:
            self._accumulator[ay, ax] += weight
            self._total_points += 1

    def add_points(
        self,
        points: list[tuple[float, float]],
        weight: float = 1.0,
    ) -> None:
        """Add multiple detection points to the accumulator.

        Args:
            points: List of ``(x, y)`` coordinates.
            weight: Intensity weight for all points.
        """
        for x, y in points:
            self.add_point(x, y, weight)

    def add_detections(
        self,
        detections: list[dict],
    ) -> None:
        """Add detection centre points from a list of detection dicts.

        Each detection should have a ``bbox`` key with ``[x1, y1, x2, y2]``.

        Args:
            detections: List of detection dictionaries.
        """
        for det in detections:
            bbox = det.get("bbox")
            if bbox is None:
                continue
            cx = (bbox[0] + bbox[2]) / 2.0
            cy = (bbox[1] + bbox[3]) / 2.0
            self.add_point(cx, cy)

    def apply_decay(self) -> None:
        """Apply temporal decay to the accumulator.

        Multiplies all accumulator values by the decay rate, causing
        older detections to gradually fade.
        """
        if self.decay_rate < 1.0:
            self._accumulator *= self.decay_rate
        self._frame_count += 1

    def get_heatmap(self) -> np.ndarray:
        """Generate the raw heatmap image (before overlay).

        Returns:
            np.ndarray: BGR heatmap image at full frame resolution.
        """
        # Apply Gaussian blur
        blurred = cv2.GaussianBlur(
            self._accumulator.astype(np.float32),
            (self.blur_kernel_size, self.blur_kernel_size),
            0,
        )

        # Normalise to [0, 255]
        max_val = blurred.max()
        if max_val > 0:
            normalised = (blurred / max_val * 255).astype(np.uint8)
        else:
            normalised = np.zeros(
                (self._acc_h, self._acc_w), dtype=np.uint8,
            )

        # Apply colour map
        coloured = cv2.applyColorMap(normalised, self.colormap)

        # Upscale to full resolution
        heatmap = cv2.resize(
            coloured,
            (self.width, self.height),
            interpolation=cv2.INTER_LINEAR,
        )

        return heatmap

    def generate_overlay(
        self,
        frame: np.ndarray,
        alpha: float = 0.4,
        min_intensity: int = 10,
    ) -> np.ndarray:
        """Generate a heatmap overlay on a camera frame.

        Blends the colour-mapped heatmap with the original frame using
        alpha compositing.  Areas with no detections remain unchanged.

        Args:
            frame: BGR camera frame.
            alpha: Heatmap opacity (0.0 = invisible, 1.0 = fully opaque).
                Defaults to ``0.4``.
            min_intensity: Minimum heatmap intensity to overlay.
                Pixels below this threshold are not blended.

        Returns:
            np.ndarray: BGR image with heatmap overlay.
        """
        heatmap = self.get_heatmap()

        # Ensure same size as frame
        if heatmap.shape[:2] != frame.shape[:2]:
            heatmap = cv2.resize(
                heatmap,
                (frame.shape[1], frame.shape[0]),
                interpolation=cv2.INTER_LINEAR,
            )

        # Create mask for areas with significant heat
        gray_heat = cv2.cvtColor(heatmap, cv2.COLOR_BGR2GRAY)
        mask = gray_heat > min_intensity

        # Blend only where heatmap is significant
        output = frame.copy()
        if np.any(mask):
            mask_3ch = np.stack([mask, mask, mask], axis=-1)
            blended = cv2.addWeighted(frame, 1.0 - alpha, heatmap, alpha, 0)
            output = np.where(mask_3ch, blended, frame)

        return output

    def reset(self) -> None:
        """Reset the accumulator to zero."""
        self._accumulator[:] = 0.0
        self._total_points = 0
        self._frame_count = 0
        logger.info("heatmap_generator.reset")

    def get_statistics(self) -> dict:
        """Return current heatmap statistics.

        Returns:
            dict: Statistics including total points, max intensity,
                mean intensity, and frame count.
        """
        return {
            "total_points": self._total_points,
            "frame_count": self._frame_count,
            "max_intensity": float(self._accumulator.max()),
            "mean_intensity": float(self._accumulator.mean()),
            "accumulator_shape": (self._acc_h, self._acc_w),
        }

    def get_density_map(self) -> np.ndarray:
        """Return the normalised density map as a float32 array.

        Values are in ``[0, 1]`` where 1.0 corresponds to the maximum
        accumulated intensity.

        Returns:
            np.ndarray: Density map at accumulator resolution.
        """
        blurred = cv2.GaussianBlur(
            self._accumulator.astype(np.float32),
            (self.blur_kernel_size, self.blur_kernel_size),
            0,
        )

        max_val = blurred.max()
        if max_val > 0:
            return blurred / max_val
        return blurred

    def export_snapshot(self) -> np.ndarray:
        """Export the current heatmap as a standalone image.

        Includes a colour bar legend on the right side.

        Returns:
            np.ndarray: BGR heatmap image with colour bar.
        """
        heatmap = self.get_heatmap()

        # Create colour bar
        bar_width = 30
        bar_height = heatmap.shape[0]
        gradient = np.linspace(255, 0, bar_height).astype(np.uint8)
        gradient = gradient.reshape(bar_height, 1)
        gradient = np.tile(gradient, (1, bar_width))
        colour_bar = cv2.applyColorMap(gradient, self.colormap)

        # Add border
        border = np.full((bar_height, 2, 3), 128, dtype=np.uint8)

        # Concatenate
        snapshot = np.concatenate([heatmap, border, colour_bar], axis=1)

        return snapshot
