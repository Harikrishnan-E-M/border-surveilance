"""
License Plate Detector for VisionAI.

Detects license plates in images using a YOLOv8-based ONNX model.
Produces bounding boxes, confidence scores, and cropped plate images
ready for downstream OCR processing.

The detector includes perspective correction and contrast enhancement
to improve OCR accuracy on angled or low-quality plate images.

Usage::

    from app.cv.detectors.plate_detector import PlateDetector

    detector = PlateDetector(session)
    plates = detector.detect(frame)
    for plate in plates:
        print(f"Plate at {plate.bbox}, confidence={plate.confidence:.2f}")
        ocr_input = plate.plate_image  # Pre-cropped and enhanced
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
    nms,
    scale_boxes,
    xywh2xyxy,
)

logger = structlog.stdlib.get_logger(__name__)


@dataclass
class PlateDetection:
    """A single license plate detection result.

    Attributes:
        bbox: Bounding box as ``(x1, y1, x2, y2)`` in pixel coordinates.
        confidence: Detection confidence score in ``[0, 1]``.
        plate_image: Cropped and enhanced plate image ready for OCR,
            or ``None`` if cropping failed.
    """

    bbox: tuple[float, float, float, float]
    confidence: float
    plate_image: Optional[np.ndarray] = field(default=None, repr=False)

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

    def to_dict(self) -> dict:
        """Serialise to a dictionary (excluding the plate image array)."""
        return {
            "bbox": list(self.bbox),
            "confidence": round(self.confidence, 4),
            "has_plate_image": self.plate_image is not None,
        }


class PlateDetector:
    """License plate detector using a YOLOv8-based ONNX model.

    Args:
        session: Loaded ONNX Runtime session for the plate detection model.
        confidence_threshold: Minimum confidence to retain a detection.
        nms_threshold: IoU threshold for NMS.
        input_size: Model input resolution as ``(width, height)``.
        plate_target_width: Width to resize cropped plates to for OCR.
        plate_target_height: Height to resize cropped plates to for OCR.
    """

    def __init__(
        self,
        session: ort.InferenceSession,
        confidence_threshold: float = 0.5,
        nms_threshold: float = 0.45,
        input_size: tuple[int, int] = (640, 640),
        plate_target_width: int = 240,
        plate_target_height: int = 80,
    ) -> None:
        self.engine = ONNXInferenceEngine(session)
        self.confidence_threshold = confidence_threshold
        self.nms_threshold = nms_threshold
        self.input_size = input_size
        self.plate_target_width = plate_target_width
        self.plate_target_height = plate_target_height

        logger.info(
            "plate_detector.initialised",
            input_size=input_size,
            confidence_threshold=confidence_threshold,
        )

    def detect(
        self,
        image: np.ndarray,
        *,
        crop: bool = True,
    ) -> list[PlateDetection]:
        """Detect license plates in a single image.

        Args:
            image: Input BGR image as a NumPy array (H, W, C).
            crop: If ``True``, crop and enhance each detected plate.

        Returns:
            list[PlateDetection]: Detected plates sorted by confidence.
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
        detections = self._postprocess(output, (orig_h, orig_w), ratio, pad)

        # Crop plates
        if crop:
            for det in detections:
                det.plate_image = self.crop_plate(image, det.bbox)

        return detections

    def _postprocess(
        self,
        output: np.ndarray,
        orig_shape: tuple[int, int],
        ratio: float,
        pad: tuple[float, float],
    ) -> list[PlateDetection]:
        """Post-process raw model output.

        Args:
            output: Raw model output tensor.
            orig_shape: Original image ``(height, width)``.
            ratio: Letterbox scale ratio.
            pad: Letterbox padding ``(dw, dh)``.

        Returns:
            list[PlateDetection]: Filtered detections.
        """
        predictions = output[0]

        # Transpose if needed
        if predictions.shape[0] < predictions.shape[1]:
            predictions = predictions.T

        num_predictions = predictions.shape[0]
        num_classes = predictions.shape[1] - 4

        boxes_xywh = predictions[:, :4]

        if num_classes == 1:
            # Single-class plate detector
            confidences = predictions[:, 4]
        else:
            # Multi-class: take best class confidence
            class_scores = predictions[:, 4:]
            confidences = np.max(class_scores, axis=1)

        # Confidence filter
        mask = confidences >= self.confidence_threshold
        if not np.any(mask):
            return []

        boxes_xywh = boxes_xywh[mask]
        confidences = confidences[mask]

        # Convert to xyxy
        boxes_xyxy = xywh2xyxy(boxes_xywh)

        # NMS
        keep = nms(boxes_xyxy, confidences, self.nms_threshold)
        if len(keep) == 0:
            return []

        boxes_xyxy = boxes_xyxy[keep]
        confidences = confidences[keep]

        # Scale to original image
        letterbox_shape = (self.input_size[1], self.input_size[0])
        boxes_xyxy = scale_boxes(
            boxes_xyxy, letterbox_shape, orig_shape, ratio, pad,
        )

        # Build detection objects
        detections: list[PlateDetection] = []
        for i in range(len(keep)):
            detections.append(PlateDetection(
                bbox=(
                    float(boxes_xyxy[i, 0]),
                    float(boxes_xyxy[i, 1]),
                    float(boxes_xyxy[i, 2]),
                    float(boxes_xyxy[i, 3]),
                ),
                confidence=float(confidences[i]),
            ))

        detections.sort(key=lambda d: d.confidence, reverse=True)
        return detections

    def crop_plate(
        self,
        image: np.ndarray,
        bbox: tuple[float, float, float, float],
        *,
        padding_ratio: float = 0.05,
    ) -> Optional[np.ndarray]:
        """Crop and enhance a license plate region from the image.

        Applies padding around the bounding box, crops the region,
        resizes to a standard dimension, and enhances contrast using
        CLAHE for improved OCR performance.

        Args:
            image: Source BGR image.
            bbox: Bounding box as ``(x1, y1, x2, y2)``.
            padding_ratio: Relative padding to add around the crop
                (as a fraction of the box dimensions).

        Returns:
            Optional[np.ndarray]: Enhanced plate image, or ``None`` if
                the crop is invalid.
        """
        h, w = image.shape[:2]
        x1, y1, x2, y2 = bbox

        # Add padding
        pad_w = (x2 - x1) * padding_ratio
        pad_h = (y2 - y1) * padding_ratio

        x1 = max(0, int(x1 - pad_w))
        y1 = max(0, int(y1 - pad_h))
        x2 = min(w, int(x2 + pad_w))
        y2 = min(h, int(y2 + pad_h))

        if x2 <= x1 or y2 <= y1:
            return None

        plate_crop = image[y1:y2, x1:x2].copy()

        if plate_crop.size == 0:
            return None

        # Resize to target dimensions
        plate_resized = cv2.resize(
            plate_crop,
            (self.plate_target_width, self.plate_target_height),
            interpolation=cv2.INTER_CUBIC,
        )

        # Enhance contrast with CLAHE
        plate_enhanced = self._enhance_plate(plate_resized)

        return plate_enhanced

    @staticmethod
    def _enhance_plate(plate_image: np.ndarray) -> np.ndarray:
        """Enhance a plate image for improved OCR readability.

        Applies CLAHE (Contrast Limited Adaptive Histogram Equalisation)
        on the L channel in LAB colour space, followed by bilateral
        filtering for noise reduction while preserving edges.

        Args:
            plate_image: BGR plate image.

        Returns:
            np.ndarray: Enhanced BGR plate image.
        """
        # Convert to LAB colour space
        lab = cv2.cvtColor(plate_image, cv2.COLOR_BGR2LAB)

        # Apply CLAHE to the L channel
        clahe = cv2.createCLAHE(clipLimit=3.0, tileGridSize=(4, 4))
        lab[:, :, 0] = clahe.apply(lab[:, :, 0])

        # Convert back to BGR
        enhanced = cv2.cvtColor(lab, cv2.COLOR_LAB2BGR)

        # Bilateral filter for noise reduction (preserves edges)
        enhanced = cv2.bilateralFilter(enhanced, d=5, sigmaColor=50, sigmaSpace=50)

        return enhanced

    def detect_in_vehicle_roi(
        self,
        image: np.ndarray,
        vehicle_bbox: tuple[float, float, float, float],
    ) -> list[PlateDetection]:
        """Detect license plates within a vehicle's bounding box region.

        Crops the vehicle region from the image and runs plate detection
        on the cropped area.  Coordinates in the returned detections are
        mapped back to the full image coordinate system.

        Args:
            image: Full BGR image.
            vehicle_bbox: Vehicle bounding box as ``(x1, y1, x2, y2)``.

        Returns:
            list[PlateDetection]: Plate detections with coordinates
                in the full image space.
        """
        h, w = image.shape[:2]
        vx1 = max(0, int(vehicle_bbox[0]))
        vy1 = max(0, int(vehicle_bbox[1]))
        vx2 = min(w, int(vehicle_bbox[2]))
        vy2 = min(h, int(vehicle_bbox[3]))

        if vx2 <= vx1 or vy2 <= vy1:
            return []

        vehicle_crop = image[vy1:vy2, vx1:vx2]
        detections = self.detect(vehicle_crop)

        # Map coordinates back to full image
        for det in detections:
            det.bbox = (
                det.bbox[0] + vx1,
                det.bbox[1] + vy1,
                det.bbox[2] + vx1,
                det.bbox[3] + vy1,
            )
            # Re-crop from full image for better quality
            det.plate_image = self.crop_plate(image, det.bbox)

        return detections
