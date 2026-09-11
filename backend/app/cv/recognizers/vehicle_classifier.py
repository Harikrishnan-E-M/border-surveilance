"""
Vehicle Attribute Classifier for VisionAI.

Classifies vehicle colour and type from a cropped vehicle image using
an ONNX multi-task model with two classification heads.  Supports
12 colours and 10 vehicle types including Indian-specific categories
like auto-rickshaw.

Usage::

    from app.cv.recognizers.vehicle_classifier import VehicleClassifier

    classifier = VehicleClassifier(session)
    result = classifier.classify(vehicle_image)
    print(f"Color: {result['color']}, Type: {result['type']}")
"""

from __future__ import annotations

from typing import Optional

import cv2
import numpy as np
import onnxruntime as ort
import structlog

from app.cv.inference_engine import ONNXInferenceEngine

logger = structlog.stdlib.get_logger(__name__)

# ── Vehicle Colour Labels ────────────────────────────────────────────
VEHICLE_COLORS: list[str] = [
    "black",
    "blue",
    "brown",
    "gold",
    "green",
    "grey",
    "orange",
    "red",
    "silver",
    "tan",
    "white",
    "yellow",
]

# ── Vehicle Type Labels ──────────────────────────────────────────────
VEHICLE_TYPES: list[str] = [
    "sedan",
    "suv",
    "hatchback",
    "truck",
    "bus",
    "van",
    "motorcycle",
    "auto_rickshaw",
    "pickup",
    "minivan",
]


class VehicleClassifier:
    """Multi-task vehicle attribute classifier.

    Classifies both colour and type from a single vehicle crop image.
    The underlying ONNX model has two output heads:

    - Output 0: Colour logits of shape ``(1, 12)``
    - Output 1: Type logits of shape ``(1, 10)``

    If the model has a single output, it is treated as a concatenation
    of colour and type logits.

    Args:
        session: Loaded ONNX Runtime session for the vehicle
            attribute model.
        input_size: Expected input image size as ``(width, height)``.
        color_labels: Optional override for colour class names.
        type_labels: Optional override for type class names.
    """

    def __init__(
        self,
        session: ort.InferenceSession,
        input_size: tuple[int, int] = (224, 224),
        color_labels: list[str] | None = None,
        type_labels: list[str] | None = None,
    ) -> None:
        self.engine = ONNXInferenceEngine(session)
        self.input_size = input_size
        self.color_labels = color_labels or VEHICLE_COLORS
        self.type_labels = type_labels or VEHICLE_TYPES
        self.num_colors = len(self.color_labels)
        self.num_types = len(self.type_labels)

        logger.info(
            "vehicle_classifier.initialised",
            input_size=input_size,
            num_colors=self.num_colors,
            num_types=self.num_types,
        )

    def classify(
        self,
        vehicle_image: np.ndarray,
    ) -> dict:
        """Classify vehicle colour and type from a cropped image.

        Args:
            vehicle_image: BGR vehicle image (cropped from the original
                frame using the object detector's bounding box).

        Returns:
            dict: Classification results with keys:
                - ``color`` (*str*): Predicted colour label.
                - ``color_confidence`` (*float*): Colour confidence.
                - ``color_probs`` (*dict[str, float]*): Per-colour
                  probability distribution.
                - ``type`` (*str*): Predicted vehicle type label.
                - ``type_confidence`` (*float*): Type confidence.
                - ``type_probs`` (*dict[str, float]*): Per-type
                  probability distribution.
        """
        if vehicle_image is None or vehicle_image.size == 0:
            return self._empty_result()

        # Preprocess
        tensor = self._preprocess(vehicle_image)

        # Inference
        outputs = self.engine.infer(tensor)

        # Parse outputs
        if len(outputs) >= 2:
            color_logits = outputs[0].flatten()
            type_logits = outputs[1].flatten()
        else:
            # Single concatenated output
            combined = outputs[0].flatten()
            color_logits = combined[: self.num_colors]
            type_logits = combined[self.num_colors : self.num_colors + self.num_types]

        # Softmax
        color_probs = self._softmax(color_logits)
        type_probs = self._softmax(type_logits)

        # Get predictions
        color_idx = int(np.argmax(color_probs))
        type_idx = int(np.argmax(type_probs))

        color_name = (
            self.color_labels[color_idx]
            if color_idx < len(self.color_labels)
            else f"color_{color_idx}"
        )
        type_name = (
            self.type_labels[type_idx]
            if type_idx < len(self.type_labels)
            else f"type_{type_idx}"
        )

        color_probs_dict = {
            label: float(color_probs[i])
            for i, label in enumerate(self.color_labels)
            if i < len(color_probs)
        }
        type_probs_dict = {
            label: float(type_probs[i])
            for i, label in enumerate(self.type_labels)
            if i < len(type_probs)
        }

        return {
            "color": color_name,
            "color_confidence": float(color_probs[color_idx]),
            "color_probs": color_probs_dict,
            "type": type_name,
            "type_confidence": float(type_probs[type_idx]),
            "type_probs": type_probs_dict,
        }

    def classify_batch(
        self,
        vehicle_images: list[np.ndarray],
    ) -> list[dict]:
        """Classify attributes for a batch of vehicle images.

        Args:
            vehicle_images: List of BGR vehicle images.

        Returns:
            list[dict]: Classification results for each vehicle.
        """
        return [self.classify(img) for img in vehicle_images]

    def _preprocess(self, image: np.ndarray) -> np.ndarray:
        """Preprocess a vehicle image for classification.

        Resizes to ``input_size``, converts BGR to RGB, normalises
        with ImageNet mean/std, and transposes to NCHW.

        Args:
            image: BGR vehicle image.

        Returns:
            np.ndarray: Preprocessed tensor of shape ``(1, 3, H, W)``.
        """
        target_w, target_h = self.input_size

        # Resize
        resized = cv2.resize(
            image, (target_w, target_h),
            interpolation=cv2.INTER_LINEAR,
        )

        # BGR -> RGB
        rgb = cv2.cvtColor(resized, cv2.COLOR_BGR2RGB)

        # Normalise with ImageNet mean/std
        tensor = rgb.astype(np.float32) / 255.0
        mean = np.array([0.485, 0.456, 0.406], dtype=np.float32).reshape(1, 1, 3)
        std = np.array([0.229, 0.224, 0.225], dtype=np.float32).reshape(1, 1, 3)
        tensor = (tensor - mean) / std

        # HWC -> CHW -> NCHW
        tensor = tensor.transpose(2, 0, 1)
        tensor = np.expand_dims(tensor, axis=0)

        return np.ascontiguousarray(tensor)

    @staticmethod
    def _softmax(logits: np.ndarray) -> np.ndarray:
        """Compute softmax probabilities.

        Args:
            logits: Raw logit values.

        Returns:
            np.ndarray: Probability distribution.
        """
        exp_logits = np.exp(logits - np.max(logits))
        return exp_logits / np.sum(exp_logits)

    def _empty_result(self) -> dict:
        """Return an empty classification result."""
        return {
            "color": "unknown",
            "color_confidence": 0.0,
            "color_probs": {label: 0.0 for label in self.color_labels},
            "type": "unknown",
            "type_confidence": 0.0,
            "type_probs": {label: 0.0 for label in self.type_labels},
        }
