"""
Fire and Smoke Detector for VisionAI.

Detects fire and smoke in images using a YOLOv8-based ONNX model
trained on fire/smoke classes.  Designed for early warning in
industrial, warehouse, and building surveillance scenarios.

The detector provides severity estimation based on the number and
size of detections relative to the frame area.

Usage::

    from app.cv.detectors.fire_detector import FireDetector

    detector = FireDetector(session)
    detections = detector.detect(frame)
    for det in detections:
        print(f"{det.class_name}: {det.confidence:.2f}")
"""

from __future__ import annotations

from dataclasses import dataclass
from typing import Optional

import cv2
import numpy as np
import onnxruntime as ort
import structlog

from app.cv.inference_engine import (
    ONNXInferenceEngine,
    nms,
    scale_boxes,
    xywh2xyxy,
)

logger = structlog.stdlib.get_logger(__name__)

# ── Fire/Smoke Class Names ───────────────────────────────────────────
FIRE_SMOKE_CLASSES: list[str] = ["fire", "smoke"]


@dataclass
class FireDetection:
    """A single fire or smoke detection result.

    Attributes:
        bbox: Bounding box as ``(x1, y1, x2, y2)`` in pixel coordinates.
        confidence: Detection confidence score in ``[0, 1]``.
        class_id: Integer class index (0=fire, 1=smoke).
        class_name: ``"fire"`` or ``"smoke"``.
    """

    bbox: tuple[float, float, float, float]
    confidence: float
    class_id: int
    class_name: str

    @property
    def center(self) -> tuple[float, float]:
        """Return the bounding box centre point."""
        return (
            (self.bbox[0] + self.bbox[2]) / 2.0,
            (self.bbox[1] + self.bbox[3]) / 2.0,
        )

    @property
    def area(self) -> float:
        """Return the bounding box area in pixels squared."""
        w = max(0.0, self.bbox[2] - self.bbox[0])
        h = max(0.0, self.bbox[3] - self.bbox[1])
        return w * h

    @property
    def is_fire(self) -> bool:
        """Return True if this is a fire detection."""
        return self.class_name == "fire"

    @property
    def is_smoke(self) -> bool:
        """Return True if this is a smoke detection."""
        return self.class_name == "smoke"

    def to_dict(self) -> dict:
        """Serialise to a plain dictionary."""
        return {
            "bbox": list(self.bbox),
            "confidence": round(self.confidence, 4),
            "class_id": self.class_id,
            "class_name": self.class_name,
        }


@dataclass
class FireAlertInfo:
    """Aggregated fire/smoke alert information for a single frame.

    Attributes:
        has_fire: ``True`` if any fire detections exist.
        has_smoke: ``True`` if any smoke detections exist.
        fire_count: Number of fire detections.
        smoke_count: Number of smoke detections.
        max_fire_confidence: Highest fire confidence score.
        max_smoke_confidence: Highest smoke confidence score.
        severity: Estimated severity level (``"low"``, ``"medium"``,
            ``"high"``, ``"critical"``).
        total_fire_area_ratio: Ratio of total fire area to frame area.
    """

    has_fire: bool
    has_smoke: bool
    fire_count: int
    smoke_count: int
    max_fire_confidence: float
    max_smoke_confidence: float
    severity: str
    total_fire_area_ratio: float

    def to_dict(self) -> dict:
        """Serialise to a plain dictionary."""
        return {
            "has_fire": self.has_fire,
            "has_smoke": self.has_smoke,
            "fire_count": self.fire_count,
            "smoke_count": self.smoke_count,
            "max_fire_confidence": round(self.max_fire_confidence, 4),
            "max_smoke_confidence": round(self.max_smoke_confidence, 4),
            "severity": self.severity,
            "total_fire_area_ratio": round(self.total_fire_area_ratio, 6),
        }


class FireDetector:
    """Fire and smoke detector using a YOLOv8-based ONNX model.

    Args:
        session: Loaded ONNX Runtime session for the fire detection model.
        confidence_threshold: Minimum confidence to retain a detection.
        nms_threshold: IoU threshold for NMS.
        input_size: Model input resolution as ``(width, height)``.
    """

    def __init__(
        self,
        session: ort.InferenceSession,
        confidence_threshold: float = 0.45,
        nms_threshold: float = 0.45,
        input_size: tuple[int, int] = (640, 640),
    ) -> None:
        self.engine = ONNXInferenceEngine(session)
        self.confidence_threshold = confidence_threshold
        self.nms_threshold = nms_threshold
        self.input_size = input_size
        self.class_names = FIRE_SMOKE_CLASSES

        logger.info(
            "fire_detector.initialised",
            input_size=input_size,
            classes=self.class_names,
        )

    def detect(self, image: np.ndarray) -> list[FireDetection]:
        """Detect fire and smoke in a single image.

        Args:
            image: Input BGR image as a NumPy array (H, W, C).

        Returns:
            list[FireDetection]: Detected fire/smoke instances sorted
                by confidence.
        """
        if image is None or image.size == 0:
            return []

        orig_h, orig_w = image.shape[:2]

        # Preprocess
        tensor, ratio, pad = self.engine.preprocess(
            image, input_size=self.input_size,
        )

        # Inference
        outputs = self.engine.infer(tensor)
        output = outputs[0]

        # Post-process
        return self._postprocess(output, (orig_h, orig_w), ratio, pad)

    def _postprocess(
        self,
        output: np.ndarray,
        orig_shape: tuple[int, int],
        ratio: float,
        pad: tuple[float, float],
    ) -> list[FireDetection]:
        """Post-process raw model output.

        Args:
            output: Raw model output tensor.
            orig_shape: Original image ``(height, width)``.
            ratio: Letterbox scale ratio.
            pad: Letterbox padding ``(dw, dh)``.

        Returns:
            list[FireDetection]: Filtered detections.
        """
        predictions = output[0]

        if predictions.shape[0] < predictions.shape[1]:
            predictions = predictions.T

        num_predictions = predictions.shape[0]
        num_classes = predictions.shape[1] - 4

        boxes_xywh = predictions[:, :4]
        class_scores = predictions[:, 4:]

        if num_classes == 1:
            # Single-class model: treat all as fire
            class_ids = np.zeros(num_predictions, dtype=np.int64)
            confidences = class_scores[:, 0]
        else:
            class_ids = np.argmax(class_scores, axis=1)
            confidences = class_scores[np.arange(num_predictions), class_ids]

        # Confidence filter
        mask = confidences >= self.confidence_threshold
        if not np.any(mask):
            return []

        boxes_xywh = boxes_xywh[mask]
        confidences = confidences[mask]
        class_ids = class_ids[mask]

        # Convert to xyxy
        boxes_xyxy = xywh2xyxy(boxes_xywh)

        # Class-aware NMS
        max_coord = boxes_xyxy.max() if boxes_xyxy.size > 0 else 1.0
        offsets = class_ids.astype(np.float32) * (max_coord + 1.0)
        boxes_nms = boxes_xyxy.copy()
        boxes_nms[:, 0] += offsets
        boxes_nms[:, 1] += offsets
        boxes_nms[:, 2] += offsets
        boxes_nms[:, 3] += offsets

        keep = nms(boxes_nms, confidences, self.nms_threshold)
        if len(keep) == 0:
            return []

        boxes_xyxy = boxes_xyxy[keep]
        confidences = confidences[keep]
        class_ids = class_ids[keep]

        # Scale to original image
        letterbox_shape = (self.input_size[1], self.input_size[0])
        boxes_xyxy = scale_boxes(
            boxes_xyxy, letterbox_shape, orig_shape, ratio, pad,
        )

        # Build detection objects
        detections: list[FireDetection] = []
        for i in range(len(keep)):
            cid = int(class_ids[i])
            cname = self.class_names[cid] if cid < len(self.class_names) else f"class_{cid}"

            detections.append(FireDetection(
                bbox=(
                    float(boxes_xyxy[i, 0]),
                    float(boxes_xyxy[i, 1]),
                    float(boxes_xyxy[i, 2]),
                    float(boxes_xyxy[i, 3]),
                ),
                confidence=float(confidences[i]),
                class_id=cid,
                class_name=cname,
            ))

        detections.sort(key=lambda d: d.confidence, reverse=True)
        return detections

    def get_alert_info(
        self,
        detections: list[FireDetection],
        frame_shape: tuple[int, int],
    ) -> FireAlertInfo:
        """Compute aggregated alert information from detections.

        Estimates severity based on the number and relative size of
        fire/smoke detections.

        Args:
            detections: List of fire/smoke detections.
            frame_shape: Frame dimensions as ``(height, width)``.

        Returns:
            FireAlertInfo: Aggregated alert information.
        """
        frame_area = float(frame_shape[0] * frame_shape[1])

        fire_dets = [d for d in detections if d.is_fire]
        smoke_dets = [d for d in detections if d.is_smoke]

        fire_count = len(fire_dets)
        smoke_count = len(smoke_dets)

        max_fire_conf = max((d.confidence for d in fire_dets), default=0.0)
        max_smoke_conf = max((d.confidence for d in smoke_dets), default=0.0)

        total_fire_area = sum(d.area for d in fire_dets)
        fire_area_ratio = total_fire_area / frame_area if frame_area > 0 else 0.0

        # Estimate severity
        severity = self._estimate_severity(
            fire_count, smoke_count, fire_area_ratio, max_fire_conf,
        )

        return FireAlertInfo(
            has_fire=fire_count > 0,
            has_smoke=smoke_count > 0,
            fire_count=fire_count,
            smoke_count=smoke_count,
            max_fire_confidence=max_fire_conf,
            max_smoke_confidence=max_smoke_conf,
            severity=severity,
            total_fire_area_ratio=fire_area_ratio,
        )

    @staticmethod
    def _estimate_severity(
        fire_count: int,
        smoke_count: int,
        fire_area_ratio: float,
        max_fire_confidence: float,
    ) -> str:
        """Estimate fire severity level.

        Args:
            fire_count: Number of fire detections.
            smoke_count: Number of smoke detections.
            fire_area_ratio: Ratio of fire area to frame area.
            max_fire_confidence: Highest fire detection confidence.

        Returns:
            str: One of ``"none"``, ``"low"``, ``"medium"``, ``"high"``,
                ``"critical"``.
        """
        if fire_count == 0 and smoke_count == 0:
            return "none"

        # Only smoke detected
        if fire_count == 0:
            return "low"

        # Fire detected: classify by area and count
        if fire_area_ratio > 0.15 or fire_count >= 5:
            return "critical"
        elif fire_area_ratio > 0.05 or fire_count >= 3:
            return "high"
        elif fire_area_ratio > 0.01 or fire_count >= 2:
            return "medium"
        else:
            return "low"
