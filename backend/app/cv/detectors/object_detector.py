"""
YOLOv8 Object Detector for VisionAI.

Performs general-purpose object detection using a YOLOv8 ONNX model.
Supports the standard COCO 80-class label set with full post-processing
including confidence filtering, class-aware NMS, and coordinate rescaling.

The detector handles the YOLOv8 output tensor format ``(1, 84, 8400)``
where 84 = 4 (box) + 80 (class probabilities) and 8400 is the total
number of anchor-free prediction cells across three feature map scales.

Usage::

    from app.cv.detectors.object_detector import ObjectDetector

    detector = ObjectDetector(session)
    detections = detector.detect(frame)
    for det in detections:
        print(f"{det.class_name}: {det.confidence:.2f} at {det.bbox}")
"""

from __future__ import annotations

from dataclasses import dataclass, field
from typing import Optional

import cv2
import numpy as np
import onnxruntime as ort
import structlog

from app.cv.inference_engine import (
    ONNXInferenceEngine,
    letterbox,
    nms,
    scale_boxes,
    xywh2xyxy,
)

logger = structlog.stdlib.get_logger(__name__)

# ── COCO 80-class Names ──────────────────────────────────────────────

COCO_CLASSES: list[str] = [
    "person", "bicycle", "car", "motorcycle", "airplane", "bus", "train",
    "truck", "boat", "traffic light", "fire hydrant", "stop sign",
    "parking meter", "bench", "bird", "cat", "dog", "horse", "sheep",
    "cow", "elephant", "bear", "zebra", "giraffe", "backpack", "umbrella",
    "handbag", "tie", "suitcase", "frisbee", "skis", "snowboard",
    "sports ball", "kite", "baseball bat", "baseball glove", "skateboard",
    "surfboard", "tennis racket", "bottle", "wine glass", "cup", "fork",
    "knife", "spoon", "bowl", "banana", "apple", "sandwich", "orange",
    "broccoli", "carrot", "hot dog", "pizza", "donut", "cake", "chair",
    "couch", "potted plant", "bed", "dining table", "toilet", "tv",
    "laptop", "mouse", "remote", "keyboard", "cell phone", "microwave",
    "oven", "toaster", "sink", "refrigerator", "book", "clock", "vase",
    "scissors", "teddy bear", "hair drier", "toothbrush",
]


@dataclass
class Detection:
    """A single object detection result.

    Attributes:
        bbox: Bounding box as ``(x1, y1, x2, y2)`` in pixel coordinates
            relative to the original input image.
        confidence: Detection confidence score in ``[0, 1]``.
        class_id: Integer class index into the label set.
        class_name: Human-readable class label.
        track_id: Optional tracking identifier assigned by a tracker.
    """

    bbox: tuple[float, float, float, float]
    confidence: float
    class_id: int
    class_name: str
    track_id: Optional[int] = field(default=None)

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
        return max(0.0, self.bbox[2] - self.bbox[0]) * max(0.0, self.bbox[3] - self.bbox[1])

    @property
    def width(self) -> float:
        """Return the bounding box width."""
        return max(0.0, self.bbox[2] - self.bbox[0])

    @property
    def height(self) -> float:
        """Return the bounding box height."""
        return max(0.0, self.bbox[3] - self.bbox[1])

    def to_dict(self) -> dict:
        """Serialise the detection to a plain dictionary."""
        return {
            "bbox": list(self.bbox),
            "confidence": round(self.confidence, 4),
            "class_id": self.class_id,
            "class_name": self.class_name,
            "track_id": self.track_id,
        }


class ObjectDetector:
    """YOLOv8 ONNX object detector.

    Wraps an ONNX Runtime session for a YOLOv8 detection model and
    provides a high-level ``detect()`` method that handles all pre- and
    post-processing.

    Args:
        session: A loaded ONNX Runtime inference session for a YOLOv8
            detection model.
        confidence_threshold: Minimum confidence to retain a detection.
        nms_threshold: IoU threshold for Non-Maximum Suppression.
        input_size: Model input resolution as ``(width, height)``.
        class_names: List of class labels.  Defaults to COCO 80 classes.
        target_classes: Optional set of class IDs to filter.  If
            ``None``, all classes are returned.
    """

    def __init__(
        self,
        session: ort.InferenceSession,
        confidence_threshold: float = 0.5,
        nms_threshold: float = 0.45,
        input_size: tuple[int, int] = (640, 640),
        class_names: list[str] | None = None,
        target_classes: set[int] | None = None,
    ) -> None:
        self.engine = ONNXInferenceEngine(session)
        self.confidence_threshold = confidence_threshold
        self.nms_threshold = nms_threshold
        self.input_size = input_size
        self.class_names = class_names or COCO_CLASSES
        self.num_classes = len(self.class_names)
        self.target_classes = target_classes

        logger.info(
            "object_detector.initialised",
            input_size=input_size,
            num_classes=self.num_classes,
            confidence_threshold=confidence_threshold,
            nms_threshold=nms_threshold,
        )

    def detect(self, image: np.ndarray) -> list[Detection]:
        """Run object detection on a single image.

        Args:
            image: Input BGR image as a NumPy array (H, W, C).

        Returns:
            list[Detection]: List of detections sorted by confidence
                in descending order.
        """
        if image is None or image.size == 0:
            return []

        orig_h, orig_w = image.shape[:2]

        # ── Preprocess ────────────────────────────────────────────────
        tensor, ratio, pad = self.engine.preprocess(
            image,
            input_size=self.input_size,
        )

        # ── Inference ─────────────────────────────────────────────────
        outputs = self.engine.infer(tensor)
        output = outputs[0]  # shape: (1, 84, 8400) or (1, 8400, 84)

        # ── Post-process ──────────────────────────────────────────────
        detections = self._postprocess(
            output,
            orig_shape=(orig_h, orig_w),
            ratio=ratio,
            pad=pad,
        )

        return detections

    def _postprocess(
        self,
        output: np.ndarray,
        orig_shape: tuple[int, int],
        ratio: float,
        pad: tuple[float, float],
    ) -> list[Detection]:
        """Post-process raw YOLOv8 detection output.

        Handles both ``(1, 84, 8400)`` and ``(1, 8400, 84)`` output
        shapes.  Applies confidence filtering, class-aware NMS, and
        coordinate rescaling.

        Args:
            output: Raw model output tensor.
            orig_shape: Original image ``(height, width)``.
            ratio: Letterbox scale ratio.
            pad: Letterbox padding ``(dw, dh)``.

        Returns:
            list[Detection]: Filtered and NMS-processed detections.
        """
        predictions = output[0]  # Remove batch dimension

        # YOLOv8 output is (84, 8400) -- transpose to (8400, 84)
        if predictions.shape[0] < predictions.shape[1]:
            predictions = predictions.T

        num_predictions = predictions.shape[0]
        num_outputs = predictions.shape[1]  # 4 + num_classes
        num_classes = num_outputs - 4

        # Split into boxes and class scores
        boxes_xywh = predictions[:, :4]  # (N, 4) as cx, cy, w, h
        class_scores = predictions[:, 4:]  # (N, num_classes)

        # Get best class and confidence for each prediction
        class_ids = np.argmax(class_scores, axis=1)
        confidences = class_scores[np.arange(num_predictions), class_ids]

        # Confidence filter
        mask = confidences >= self.confidence_threshold
        if self.target_classes is not None:
            class_mask = np.isin(class_ids, list(self.target_classes))
            mask = mask & class_mask

        if not np.any(mask):
            return []

        boxes_xywh = boxes_xywh[mask]
        confidences = confidences[mask]
        class_ids = class_ids[mask]

        # Convert cx,cy,w,h -> x1,y1,x2,y2
        boxes_xyxy = xywh2xyxy(boxes_xywh)

        # Class-aware NMS: offset boxes by class_id to prevent cross-class suppression
        max_coord = boxes_xyxy.max()
        offsets = class_ids.astype(np.float32) * (max_coord + 1.0)
        boxes_for_nms = boxes_xyxy.copy()
        boxes_for_nms[:, 0] += offsets
        boxes_for_nms[:, 1] += offsets
        boxes_for_nms[:, 2] += offsets
        boxes_for_nms[:, 3] += offsets

        keep_indices = nms(boxes_for_nms, confidences, self.nms_threshold)

        if len(keep_indices) == 0:
            return []

        boxes_xyxy = boxes_xyxy[keep_indices]
        confidences = confidences[keep_indices]
        class_ids = class_ids[keep_indices]

        # Scale boxes back to original image coordinates
        letterbox_shape = (self.input_size[1], self.input_size[0])
        boxes_xyxy = scale_boxes(
            boxes_xyxy,
            from_shape=letterbox_shape,
            to_shape=orig_shape,
            ratio=ratio,
            pad=pad,
        )

        # Build Detection objects
        detections: list[Detection] = []
        for i in range(len(keep_indices)):
            cid = int(class_ids[i])
            class_name = self.class_names[cid] if cid < len(self.class_names) else f"class_{cid}"

            det = Detection(
                bbox=(
                    float(boxes_xyxy[i, 0]),
                    float(boxes_xyxy[i, 1]),
                    float(boxes_xyxy[i, 2]),
                    float(boxes_xyxy[i, 3]),
                ),
                confidence=float(confidences[i]),
                class_id=cid,
                class_name=class_name,
            )
            detections.append(det)

        # Sort by confidence descending
        detections.sort(key=lambda d: d.confidence, reverse=True)

        return detections

    def detect_class(
        self,
        image: np.ndarray,
        class_name: str,
    ) -> list[Detection]:
        """Detect objects of a specific class only.

        Args:
            image: Input BGR image.
            class_name: Class name to filter for (case-insensitive).

        Returns:
            list[Detection]: Filtered detections for the given class.
        """
        all_detections = self.detect(image)
        target = class_name.lower()
        return [d for d in all_detections if d.class_name.lower() == target]

    def detect_persons(self, image: np.ndarray) -> list[Detection]:
        """Convenience method to detect only persons (class_id=0).

        Args:
            image: Input BGR image.

        Returns:
            list[Detection]: Person detections only.
        """
        all_detections = self.detect(image)
        return [d for d in all_detections if d.class_id == 0]

    def detect_vehicles(self, image: np.ndarray) -> list[Detection]:
        """Convenience method to detect vehicles (car, motorcycle, bus, truck).

        Args:
            image: Input BGR image.

        Returns:
            list[Detection]: Vehicle detections only.
        """
        vehicle_ids = {2, 3, 5, 7}  # car, motorcycle, bus, truck
        all_detections = self.detect(image)
        return [d for d in all_detections if d.class_id in vehicle_ids]
