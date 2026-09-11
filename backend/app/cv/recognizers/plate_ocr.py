"""
License Plate OCR for VisionAI.

Recognises alphanumeric text on license plate images using an ONNX-based
optical character recognition model.  Includes Indian license plate
validation and pre-processing routines optimised for plate recognition.

The model uses a CTC-based sequence recognition architecture that
processes a fixed-height plate image and outputs a character sequence
with per-character confidence scores.

Usage::

    from app.cv.recognizers.plate_ocr import PlateOCR

    ocr = PlateOCR(session)
    text, confidence = ocr.recognize(plate_image)
    is_valid = ocr.validate_indian_plate(text)
"""

from __future__ import annotations

import re
from typing import Optional

import cv2
import numpy as np
import onnxruntime as ort
import structlog

from app.cv.inference_engine import ONNXInferenceEngine

logger = structlog.stdlib.get_logger(__name__)

# ── Character Set ────────────────────────────────────────────────────
# The CTC vocabulary: index 0 is the blank token, followed by digits
# and uppercase letters.
PLATE_CHARSET: str = "-0123456789ABCDEFGHIJKLMNOPQRSTUVWXYZ"

# Indian license plate format: SS NN SS NNNN
# where S = state code letter, N = digit
# e.g., MH 12 AB 1234, KA 01 MG 5678
INDIAN_PLATE_PATTERN = re.compile(
    r"^[A-Z]{2}\s?\d{1,2}\s?[A-Z]{1,3}\s?\d{1,4}$"
)

# Broader pattern that accepts some OCR errors
INDIAN_PLATE_PATTERN_RELAXED = re.compile(
    r"^[A-Z0-9]{2}\s?\d{1,2}\s?[A-Z0-9]{1,3}\s?\d{1,4}$"
)


class PlateOCR:
    """License plate text recognition engine.

    Args:
        session: Loaded ONNX Runtime session for the OCR model.
        input_width: Expected input image width.
        input_height: Expected input image height.
        charset: Character vocabulary.  Index 0 must be the CTC blank.
    """

    def __init__(
        self,
        session: ort.InferenceSession,
        input_width: int = 240,
        input_height: int = 80,
        charset: str | None = None,
    ) -> None:
        self.engine = ONNXInferenceEngine(session)
        self.input_width = input_width
        self.input_height = input_height
        self.charset = charset or PLATE_CHARSET

        logger.info(
            "plate_ocr.initialised",
            input_size=(input_width, input_height),
            charset_length=len(self.charset),
        )

    def recognize(
        self,
        plate_image: np.ndarray,
    ) -> tuple[str, float]:
        """Recognise text on a license plate image.

        Args:
            plate_image: BGR plate image (ideally cropped and enhanced).

        Returns:
            tuple[str, float]: A 2-tuple of:
                - **text** (*str*) -- The recognised plate text.
                - **confidence** (*float*) -- Average character-level
                  confidence in ``[0, 1]``.
        """
        if plate_image is None or plate_image.size == 0:
            return ("", 0.0)

        # Preprocess the plate image
        tensor = self.preprocess_plate(plate_image)

        # Run inference
        outputs = self.engine.infer(tensor)
        logits = outputs[0]  # (1, seq_len, num_chars) or (seq_len, num_chars)

        # Decode CTC output
        text, confidence = self._ctc_decode(logits)

        return (text, confidence)

    def preprocess_plate(
        self,
        plate_image: np.ndarray,
    ) -> np.ndarray:
        """Preprocess a plate image for the OCR model.

        Converts to grayscale, applies adaptive thresholding for
        binarisation, resizes to the model's expected dimensions,
        and prepares the NCHW tensor.

        Args:
            plate_image: BGR plate image.

        Returns:
            np.ndarray: Preprocessed tensor of shape ``(1, 1, H, W)``
                as float32.
        """
        # Convert to grayscale
        if len(plate_image.shape) == 3 and plate_image.shape[2] == 3:
            gray = cv2.cvtColor(plate_image, cv2.COLOR_BGR2GRAY)
        else:
            gray = plate_image

        # Resize to expected dimensions
        resized = cv2.resize(
            gray,
            (self.input_width, self.input_height),
            interpolation=cv2.INTER_CUBIC,
        )

        # Normalise to [0, 1]
        tensor = resized.astype(np.float32) / 255.0

        # Add channel and batch dimensions: HW -> 1CHW
        tensor = tensor[np.newaxis, np.newaxis, :, :]

        return np.ascontiguousarray(tensor)

    def _ctc_decode(
        self,
        logits: np.ndarray,
    ) -> tuple[str, float]:
        """Decode CTC logits using greedy (best-path) decoding.

        Selects the most probable character at each time step and
        collapses repeated characters and blanks.

        Args:
            logits: Raw model output of shape ``(1, T, C)`` or
                ``(T, C)`` where T is the sequence length and C is
                the number of character classes.

        Returns:
            tuple[str, float]: Decoded text and average confidence.
        """
        # Remove batch dimension if present
        if logits.ndim == 3:
            logits = logits[0]

        # Softmax to get probabilities
        exp_logits = np.exp(logits - np.max(logits, axis=1, keepdims=True))
        probs = exp_logits / np.sum(exp_logits, axis=1, keepdims=True)

        # Greedy decode: take argmax at each time step
        char_indices = np.argmax(probs, axis=1)
        char_probs = probs[np.arange(len(char_indices)), char_indices]

        # CTC collapse: remove consecutive duplicates and blanks
        decoded_chars: list[str] = []
        decoded_probs: list[float] = []
        prev_idx = -1

        for t in range(len(char_indices)):
            idx = int(char_indices[t])
            if idx == prev_idx:
                continue
            prev_idx = idx
            if idx == 0:
                # Blank token -- skip
                continue
            if idx < len(self.charset):
                decoded_chars.append(self.charset[idx])
                decoded_probs.append(float(char_probs[t]))

        text = "".join(decoded_chars).strip()
        confidence = float(np.mean(decoded_probs)) if decoded_probs else 0.0

        return (text, confidence)

    @staticmethod
    def validate_indian_plate(text: str) -> bool:
        """Validate whether a string matches the Indian license plate format.

        Indian plates follow the pattern: ``SS NN SS NNNN`` where S is
        a letter and N is a digit.  Common formats include:

        - ``MH 12 AB 1234``
        - ``KA01MG5678``
        - ``DL 1C AB 1234``

        Args:
            text: Plate text to validate (spaces are optional).

        Returns:
            bool: ``True`` if the text matches a valid Indian plate
                format.
        """
        cleaned = text.upper().strip()
        return bool(INDIAN_PLATE_PATTERN.match(cleaned))

    @staticmethod
    def format_indian_plate(text: str) -> str:
        """Format a recognised plate text into the standard Indian format.

        Inserts spaces at the canonical positions: ``SS NN SS NNNN``.

        Args:
            text: Raw plate text (spaces removed).

        Returns:
            str: Formatted plate text, or the original text if it
                cannot be parsed.
        """
        cleaned = re.sub(r"\s+", "", text.upper().strip())

        if len(cleaned) < 6:
            return cleaned

        # Try to split: first 2 chars (state), next 1-2 digits (district),
        # next 1-3 letters (series), remaining digits (number)
        match = re.match(
            r"^([A-Z]{2})(\d{1,2})([A-Z]{1,3})(\d{1,4})$",
            cleaned,
        )
        if match:
            state, district, series, number = match.groups()
            return f"{state} {district} {series} {number}"

        return cleaned

    def recognize_enhanced(
        self,
        plate_image: np.ndarray,
    ) -> dict:
        """Run OCR with additional post-processing and validation.

        Returns enriched results including formatted text, validation
        status, and multiple preprocessing attempts for robustness.

        Args:
            plate_image: BGR plate image.

        Returns:
            dict: Dictionary with keys ``text``, ``confidence``,
                ``formatted``, ``is_valid_indian``, ``raw_attempts``.
        """
        if plate_image is None or plate_image.size == 0:
            return {
                "text": "",
                "confidence": 0.0,
                "formatted": "",
                "is_valid_indian": False,
                "raw_attempts": [],
            }

        attempts: list[tuple[str, float]] = []

        # Attempt 1: Standard preprocessing
        text, conf = self.recognize(plate_image)
        attempts.append((text, conf))

        # Attempt 2: Enhanced contrast
        enhanced = self._enhance_contrast(plate_image)
        text2, conf2 = self.recognize(enhanced)
        attempts.append((text2, conf2))

        # Attempt 3: Inverted image (white plate, dark text)
        inverted = cv2.bitwise_not(plate_image)
        text3, conf3 = self.recognize(inverted)
        attempts.append((text3, conf3))

        # Select the best result by confidence
        best_text, best_conf = max(attempts, key=lambda x: x[1])

        formatted = self.format_indian_plate(best_text)
        is_valid = self.validate_indian_plate(best_text)

        return {
            "text": best_text,
            "confidence": round(best_conf, 4),
            "formatted": formatted,
            "is_valid_indian": is_valid,
            "raw_attempts": [
                {"text": t, "confidence": round(c, 4)}
                for t, c in attempts
            ],
        }

    @staticmethod
    def _enhance_contrast(image: np.ndarray) -> np.ndarray:
        """Apply CLAHE contrast enhancement to a plate image.

        Args:
            image: BGR plate image.

        Returns:
            np.ndarray: Contrast-enhanced BGR image.
        """
        lab = cv2.cvtColor(image, cv2.COLOR_BGR2LAB)
        clahe = cv2.createCLAHE(clipLimit=4.0, tileGridSize=(8, 8))
        lab[:, :, 0] = clahe.apply(lab[:, :, 0])
        return cv2.cvtColor(lab, cv2.COLOR_LAB2BGR)
