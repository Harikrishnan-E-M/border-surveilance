"""
PPE (Personal Protective Equipment) Detector for VisionAI.

Detects safety equipment such as helmets, safety vests, safety goggles,
gloves, and boots using a YOLOv8-based ONNX model trained on PPE
classes.  Includes a compliance checker that evaluates whether a person
is wearing all required PPE items based on the spatial overlap between
the person bounding box and detected PPE items.

Usage::

    from app.cv.detectors.ppe_detector import PPEDetector

    detector = PPEDetector(session)
    ppe_items = detector.detect(frame)
    result = detector.check_compliance(person_bbox, ppe_items, required={"helmet", "vest"})
    if not result["compliant"]:
        print(f"Missing: {result['missing']}")
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
    letterbox,
    nms,
    scale_boxes,
    xywh2xyxy,
)

logger = structlog.stdlib.get_logger(__name__)

# ── PPE Class Names ──────────────────────────────────────────────────
# Ordered to match the model's class indices.
PPE_CLASSES: list[str] = [
    "helmet",
    "no_helmet",
    "vest",
    "no_vest",
    "goggles",
    "no_goggles",
    "gloves",
    "no_gloves",
    "boots",
    "no_boots",
    "mask",
    "no_mask",
    "person",
]

# Classes that represent *wearing* protective equipment
POSITIVE_PPE: set[str] = {"helmet", "vest", "goggles", "gloves", "boots", "mask"}

# Classes that represent *not wearing* protective equipment
NEGATIVE_PPE: set[str] = {"no_helmet", "no_vest", "no_goggles", "no_gloves", "no_boots", "no_mask"}

# Mapping from negative class to the positive counterpart
NEGATIVE_TO_POSITIVE: dict[str, str] = {
    "no_helmet": "helmet",
    "no_vest": "vest",
    "no_goggles": "goggles",
    "no_gloves": "gloves",
    "no_boots": "boots",
    "no_mask": "mask",
}


@dataclass
class PPEDetection:
    """A single PPE detection result.

    Attributes:
        bbox: Bounding box as ``(x1, y1, x2, y2)`` in pixel coordinates.
        confidence: Detection confidence score in ``[0, 1]``.
        class_id: Integer class index into ``PPE_CLASSES``.
        class_name: Human-readable PPE class label.
        is_positive: ``True`` if the detection represents protective
            equipment being worn (as opposed to its absence).
    """

    bbox: tuple[float, float, float, float]
    confidence: float
    class_id: int
    class_name: str
    is_positive: bool

    @property
    def center(self) -> tuple[float, float]:
        """Return the bounding box centre point."""
        return (
            (self.bbox[0] + self.bbox[2]) / 2.0,
            (self.bbox[1] + self.bbox[3]) / 2.0,
        )

    def to_dict(self) -> dict:
        """Serialise to a plain dictionary."""
        return {
            "bbox": list(self.bbox),
            "confidence": round(self.confidence, 4),
            "class_id": self.class_id,
            "class_name": self.class_name,
            "is_positive": self.is_positive,
        }


@dataclass
class ComplianceResult:
    """Result of a PPE compliance check for a single person.

    Attributes:
        compliant: ``True`` if all required PPE items are detected.
        detected: Set of PPE items detected on the person.
        missing: Set of required PPE items that are missing.
        violations: List of specific violation descriptions.
        person_bbox: The person bounding box that was checked.
    """

    compliant: bool
    detected: set[str]
    missing: set[str]
    violations: list[str]
    person_bbox: tuple[float, float, float, float]

    def to_dict(self) -> dict:
        """Serialise to a plain dictionary."""
        return {
            "compliant": self.compliant,
            "detected": sorted(self.detected),
            "missing": sorted(self.missing),
            "violations": self.violations,
            "person_bbox": list(self.person_bbox),
        }


class PPEDetector:
    """PPE detection and compliance checking engine.

    Args:
        session: Loaded ONNX Runtime session for the PPE detection model.
        confidence_threshold: Minimum confidence to retain a detection.
        nms_threshold: IoU threshold for NMS.
        input_size: Model input resolution as ``(width, height)``.
        iou_threshold_compliance: Minimum IoU between a person bbox and
            a PPE item bbox to consider the PPE item as belonging to
            that person.
    """

    def __init__(
        self,
        session: ort.InferenceSession,
        confidence_threshold: float = 0.5,
        nms_threshold: float = 0.45,
        input_size: tuple[int, int] = (640, 640),
        iou_threshold_compliance: float = 0.1,
    ) -> None:
        self.engine = ONNXInferenceEngine(session)
        self.confidence_threshold = confidence_threshold
        self.nms_threshold = nms_threshold
        self.input_size = input_size
        self.iou_threshold_compliance = iou_threshold_compliance
        self.class_names = PPE_CLASSES

        logger.info(
            "ppe_detector.initialised",
            input_size=input_size,
            num_classes=len(self.class_names),
        )

    def detect(self, image: np.ndarray) -> list[PPEDetection]:
        """Detect PPE items in a single image.

        Args:
            image: Input BGR image as a NumPy array (H, W, C).

        Returns:
            list[PPEDetection]: Detected PPE items sorted by confidence.
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
    ) -> list[PPEDetection]:
        """Post-process raw YOLOv8 PPE detection output.

        Args:
            output: Raw model output tensor.
            orig_shape: Original image ``(height, width)``.
            ratio: Letterbox scale ratio.
            pad: Letterbox padding ``(dw, dh)``.

        Returns:
            list[PPEDetection]: Filtered detections.
        """
        predictions = output[0]

        # Transpose if needed: (num_outputs, N) -> (N, num_outputs)
        if predictions.shape[0] < predictions.shape[1]:
            predictions = predictions.T

        num_predictions = predictions.shape[0]
        num_classes = predictions.shape[1] - 4

        boxes_xywh = predictions[:, :4]
        class_scores = predictions[:, 4:]

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

        # Scale to original image coordinates
        letterbox_shape = (self.input_size[1], self.input_size[0])
        boxes_xyxy = scale_boxes(
            boxes_xyxy, letterbox_shape, orig_shape, ratio, pad,
        )

        # Build detection objects
        detections: list[PPEDetection] = []
        for i in range(len(keep)):
            cid = int(class_ids[i])
            cname = self.class_names[cid] if cid < len(self.class_names) else f"class_{cid}"
            is_positive = cname in POSITIVE_PPE

            detections.append(PPEDetection(
                bbox=(
                    float(boxes_xyxy[i, 0]),
                    float(boxes_xyxy[i, 1]),
                    float(boxes_xyxy[i, 2]),
                    float(boxes_xyxy[i, 3]),
                ),
                confidence=float(confidences[i]),
                class_id=cid,
                class_name=cname,
                is_positive=is_positive,
            ))

        detections.sort(key=lambda d: d.confidence, reverse=True)
        return detections

    def check_compliance(
        self,
        person_bbox: tuple[float, float, float, float],
        ppe_detections: list[PPEDetection],
        required: set[str] | None = None,
    ) -> ComplianceResult:
        """Check PPE compliance for a single person.

        Determines which PPE items overlap with the person bounding box
        and evaluates whether all required items are present.

        Args:
            person_bbox: The person's bounding box as ``(x1, y1, x2, y2)``.
            ppe_detections: All PPE detections from the same frame.
            required: Set of required PPE item names (e.g.
                ``{"helmet", "vest"}``).  If ``None``, defaults to
                ``{"helmet", "vest"}``.

        Returns:
            ComplianceResult: Detailed compliance result.
        """
        if required is None:
            required = {"helmet", "vest"}

        detected_positive: set[str] = set()
        detected_negative: set[str] = set()

        for det in ppe_detections:
            if det.class_name == "person":
                continue

            # Check spatial overlap
            iou = self._compute_containment(person_bbox, det.bbox)
            if iou < self.iou_threshold_compliance:
                continue

            if det.is_positive:
                detected_positive.add(det.class_name)
            else:
                detected_negative.add(det.class_name)

        # Determine missing items
        missing: set[str] = set()
        violations: list[str] = []

        for item in required:
            if item not in detected_positive:
                missing.add(item)
                # Check if the negative counterpart was detected
                neg_name = f"no_{item}"
                if neg_name in detected_negative:
                    violations.append(
                        f"{item.replace('_', ' ').title()} not worn (detected '{neg_name}')"
                    )
                else:
                    violations.append(
                        f"{item.replace('_', ' ').title()} not detected"
                    )

        compliant = len(missing) == 0

        return ComplianceResult(
            compliant=compliant,
            detected=detected_positive,
            missing=missing,
            violations=violations,
            person_bbox=person_bbox,
        )

    @staticmethod
    def _compute_containment(
        outer_bbox: tuple[float, float, float, float],
        inner_bbox: tuple[float, float, float, float],
    ) -> float:
        """Compute how much of the inner bbox is contained within the
        outer bbox.

        This is the intersection area divided by the inner bbox area,
        which is more appropriate than IoU for checking whether a PPE
        item (small) belongs to a person (large).

        Args:
            outer_bbox: Larger bounding box ``(x1, y1, x2, y2)``.
            inner_bbox: Smaller bounding box ``(x1, y1, x2, y2)``.

        Returns:
            float: Containment ratio in ``[0, 1]``.
        """
        x1 = max(outer_bbox[0], inner_bbox[0])
        y1 = max(outer_bbox[1], inner_bbox[1])
        x2 = min(outer_bbox[2], inner_bbox[2])
        y2 = min(outer_bbox[3], inner_bbox[3])

        intersection = max(0.0, x2 - x1) * max(0.0, y2 - y1)

        inner_area = (
            max(0.0, inner_bbox[2] - inner_bbox[0])
            * max(0.0, inner_bbox[3] - inner_bbox[1])
        )

        if inner_area <= 0:
            return 0.0

        return intersection / inner_area

    def detect_and_check(
        self,
        image: np.ndarray,
        person_bboxes: list[tuple[float, float, float, float]],
        required: set[str] | None = None,
    ) -> list[ComplianceResult]:
        """Detect PPE items and check compliance for multiple persons.

        Args:
            image: Input BGR image.
            person_bboxes: List of person bounding boxes.
            required: Set of required PPE item names.

        Returns:
            list[ComplianceResult]: Compliance result for each person.
        """
        ppe_detections = self.detect(image)

        results: list[ComplianceResult] = []
        for person_bbox in person_bboxes:
            result = self.check_compliance(person_bbox, ppe_detections, required)
            results.append(result)

        return results
