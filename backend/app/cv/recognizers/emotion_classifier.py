"""
Facial Emotion Classifier for VisionAI.

Classifies facial expressions into seven basic emotion categories
(angry, disgust, fear, happy, sad, surprise, neutral) using an ONNX
model.  Provides per-class probabilities and a composite sentiment
score for aggregated analytics.

The model expects a 48x48 or 64x64 grayscale face image, typically
cropped from the aligned face output of the SCRFD face detector.

Usage::

    from app.cv.recognizers.emotion_classifier import EmotionClassifier

    classifier = EmotionClassifier(session)
    emotion, confidence, probs = classifier.classify(face_image)
    sentiment = classifier.compute_sentiment_score(probs)
"""

from __future__ import annotations

from typing import Optional

import cv2
import numpy as np
import onnxruntime as ort
import structlog

from app.cv.inference_engine import ONNXInferenceEngine

logger = structlog.stdlib.get_logger(__name__)

# ── Emotion Labels ───────────────────────────────────────────────────
EMOTIONS: list[str] = [
    "angry",
    "disgust",
    "fear",
    "happy",
    "sad",
    "surprise",
    "neutral",
]

# Sentiment weights for computing a composite score.
# Positive emotions have positive weights; negative emotions have
# negative weights; neutral is zero.
SENTIMENT_WEIGHTS: dict[str, float] = {
    "angry": -0.8,
    "disgust": -0.6,
    "fear": -0.7,
    "happy": 1.0,
    "sad": -0.5,
    "surprise": 0.3,
    "neutral": 0.0,
}


class EmotionClassifier:
    """Facial emotion classification engine.

    Args:
        session: Loaded ONNX Runtime session for the emotion model.
        input_size: Expected face image size as ``(width, height)``.
            Defaults to ``(64, 64)``.
        grayscale: If ``True``, convert the input to single-channel
            grayscale before inference.  Defaults to ``True``.
    """

    def __init__(
        self,
        session: ort.InferenceSession,
        input_size: tuple[int, int] = (64, 64),
        grayscale: bool = True,
    ) -> None:
        self.engine = ONNXInferenceEngine(session)
        self.input_size = input_size
        self.grayscale = grayscale
        self.num_classes = len(EMOTIONS)

        logger.info(
            "emotion_classifier.initialised",
            input_size=input_size,
            grayscale=grayscale,
            num_classes=self.num_classes,
        )

    def classify(
        self,
        face_image: np.ndarray,
    ) -> tuple[str, float, dict[str, float]]:
        """Classify the emotion expressed in a face image.

        Args:
            face_image: BGR or grayscale face image.  Will be resized
                and converted to grayscale if ``self.grayscale`` is
                ``True``.

        Returns:
            tuple: A 3-tuple of:
                - **emotion** (*str*) -- The predicted emotion label.
                - **confidence** (*float*) -- Confidence score for the
                  predicted emotion in ``[0, 1]``.
                - **probs_dict** (*dict[str, float]*) -- Per-class
                  probability distribution.
        """
        if face_image is None or face_image.size == 0:
            return ("neutral", 0.0, {e: 0.0 for e in EMOTIONS})

        # Preprocess
        tensor = self._preprocess(face_image)

        # Inference
        outputs = self.engine.infer(tensor)
        logits = outputs[0].flatten()

        # Softmax
        probs = self._softmax(logits)

        # Build probability dictionary
        probs_dict: dict[str, float] = {}
        for i, emotion in enumerate(EMOTIONS):
            probs_dict[emotion] = float(probs[i]) if i < len(probs) else 0.0

        # Get predicted class
        predicted_idx = int(np.argmax(probs))
        predicted_emotion = EMOTIONS[predicted_idx] if predicted_idx < len(EMOTIONS) else "neutral"
        confidence = float(probs[predicted_idx])

        return (predicted_emotion, confidence, probs_dict)

    def classify_batch(
        self,
        face_images: list[np.ndarray],
    ) -> list[tuple[str, float, dict[str, float]]]:
        """Classify emotions for a batch of face images.

        Args:
            face_images: List of BGR or grayscale face images.

        Returns:
            list[tuple]: Classification results for each face.
        """
        results = []
        for face in face_images:
            results.append(self.classify(face))
        return results

    def _preprocess(self, face_image: np.ndarray) -> np.ndarray:
        """Preprocess a face image for the emotion model.

        Args:
            face_image: BGR or grayscale face image.

        Returns:
            np.ndarray: Preprocessed tensor of shape ``(1, C, H, W)``.
        """
        target_w, target_h = self.input_size

        if self.grayscale:
            # Convert to grayscale if colour
            if len(face_image.shape) == 3 and face_image.shape[2] == 3:
                gray = cv2.cvtColor(face_image, cv2.COLOR_BGR2GRAY)
            else:
                gray = face_image

            # Resize
            resized = cv2.resize(
                gray, (target_w, target_h),
                interpolation=cv2.INTER_LINEAR,
            )

            # Normalise to [0, 1]
            tensor = resized.astype(np.float32) / 255.0

            # HW -> 1CHW (single channel)
            tensor = tensor[np.newaxis, np.newaxis, :, :]
        else:
            # Colour input (RGB)
            if len(face_image.shape) == 2:
                face_image = cv2.cvtColor(face_image, cv2.COLOR_GRAY2BGR)

            resized = cv2.resize(
                face_image, (target_w, target_h),
                interpolation=cv2.INTER_LINEAR,
            )

            rgb = cv2.cvtColor(resized, cv2.COLOR_BGR2RGB)
            tensor = rgb.astype(np.float32) / 255.0
            tensor = tensor.transpose(2, 0, 1)
            tensor = tensor[np.newaxis, :, :, :]

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

    @staticmethod
    def compute_sentiment_score(
        probs_dict: dict[str, float],
    ) -> float:
        """Compute a composite sentiment score from emotion probabilities.

        The score ranges from ``-1.0`` (very negative) to ``+1.0``
        (very positive), computed as the weighted sum of emotion
        probabilities using the ``SENTIMENT_WEIGHTS``.

        Args:
            probs_dict: Per-class probability distribution as returned
                by ``classify()``.

        Returns:
            float: Sentiment score in ``[-1.0, 1.0]``.
        """
        score = 0.0
        for emotion, prob in probs_dict.items():
            weight = SENTIMENT_WEIGHTS.get(emotion, 0.0)
            score += weight * prob

        return max(-1.0, min(1.0, score))

    @staticmethod
    def get_dominant_emotions(
        probs_dict: dict[str, float],
        threshold: float = 0.15,
    ) -> list[tuple[str, float]]:
        """Get all emotions above a probability threshold.

        Args:
            probs_dict: Per-class probability distribution.
            threshold: Minimum probability threshold.

        Returns:
            list[tuple[str, float]]: List of ``(emotion, probability)``
                tuples sorted by probability descending.
        """
        dominant = [
            (emotion, prob)
            for emotion, prob in probs_dict.items()
            if prob >= threshold
        ]
        dominant.sort(key=lambda x: x[1], reverse=True)
        return dominant
