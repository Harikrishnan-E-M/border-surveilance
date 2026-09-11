"""
ArcFace Face Recognizer for VisionAI.

Extracts 512-dimensional facial embeddings from aligned face images
using an ArcFace ONNX model.  Provides cosine similarity and Euclidean
distance computations for face matching and verification.

The recognizer expects 112x112 BGR face images aligned using the SCRFD
face detector's landmark-based affine transform.

Usage::

    from app.cv.recognizers.face_recognizer import FaceRecognizer

    recognizer = FaceRecognizer(session)
    embedding = recognizer.get_embedding(aligned_face)
    similarity = recognizer.compute_similarity(embedding_a, embedding_b)
"""

from __future__ import annotations

from typing import Optional

import cv2
import numpy as np
import onnxruntime as ort
import structlog

from app.cv.inference_engine import ONNXInferenceEngine

logger = structlog.stdlib.get_logger(__name__)

# Default thresholds for face matching
DEFAULT_SIMILARITY_THRESHOLD: float = 0.45
DEFAULT_DISTANCE_THRESHOLD: float = 1.1


class FaceRecognizer:
    """ArcFace-based face recognition engine.

    Extracts normalised 512-dimensional embeddings from aligned face
    images and provides similarity/distance computation for matching.

    Args:
        session: Loaded ONNX Runtime session for an ArcFace model.
        input_size: Expected face image size as ``(width, height)``.
            Defaults to ``(112, 112)``.
        embedding_dim: Dimensionality of the output embedding vector.
            Defaults to ``512``.
    """

    def __init__(
        self,
        session: ort.InferenceSession,
        input_size: tuple[int, int] = (112, 112),
        embedding_dim: int = 512,
    ) -> None:
        self.engine = ONNXInferenceEngine(session)
        self.input_size = input_size
        self.embedding_dim = embedding_dim

        logger.info(
            "face_recognizer.initialised",
            input_size=input_size,
            embedding_dim=embedding_dim,
        )

    def get_embedding(
        self,
        aligned_face: np.ndarray,
        *,
        normalize: bool = True,
    ) -> np.ndarray:
        """Extract a face embedding from an aligned face image.

        Args:
            aligned_face: BGR face image of shape ``(112, 112, 3)``
                aligned using an affine transform.
            normalize: If ``True``, L2-normalise the embedding vector
                so that cosine similarity equals the dot product.

        Returns:
            np.ndarray: Embedding vector of shape ``(512,)`` as float32.

        Raises:
            ValueError: If the input image is invalid.
        """
        if aligned_face is None or aligned_face.size == 0:
            raise ValueError("Input face image is empty or None.")

        # Preprocess: resize if needed, BGR->RGB, normalise to [-1,1]
        tensor = self._preprocess(aligned_face)

        # Run inference
        outputs = self.engine.infer(tensor)
        embedding = outputs[0].flatten().astype(np.float32)

        # L2 normalise
        if normalize:
            norm = np.linalg.norm(embedding)
            if norm > 0:
                embedding = embedding / norm

        return embedding

    def get_embeddings_batch(
        self,
        aligned_faces: list[np.ndarray],
        *,
        normalize: bool = True,
    ) -> np.ndarray:
        """Extract embeddings for a batch of aligned face images.

        Args:
            aligned_faces: List of BGR face images, each of shape
                ``(112, 112, 3)``.
            normalize: If ``True``, L2-normalise each embedding.

        Returns:
            np.ndarray: Embedding matrix of shape ``(N, 512)``.
        """
        if not aligned_faces:
            return np.empty((0, self.embedding_dim), dtype=np.float32)

        # Preprocess batch
        tensors = [self._preprocess(face) for face in aligned_faces]
        batch = np.concatenate(tensors, axis=0)  # (N, 3, 112, 112)

        # Run inference on each sample (ONNX models may not support batch)
        embeddings = []
        for i in range(batch.shape[0]):
            single = batch[i:i + 1]
            outputs = self.engine.infer(single)
            emb = outputs[0].flatten().astype(np.float32)
            if normalize:
                norm = np.linalg.norm(emb)
                if norm > 0:
                    emb = emb / norm
            embeddings.append(emb)

        return np.array(embeddings, dtype=np.float32)

    def _preprocess(self, face_image: np.ndarray) -> np.ndarray:
        """Preprocess a face image for the ArcFace model.

        Resizes to ``input_size``, converts BGR to RGB, normalises
        pixel values to ``[-1, 1]``, and transposes to NCHW format.

        Args:
            face_image: BGR face image.

        Returns:
            np.ndarray: Preprocessed tensor of shape ``(1, 3, 112, 112)``.
        """
        h, w = face_image.shape[:2]
        target_w, target_h = self.input_size

        if (w, h) != (target_w, target_h):
            face_image = cv2.resize(
                face_image, (target_w, target_h),
                interpolation=cv2.INTER_LINEAR,
            )

        # BGR -> RGB
        rgb = cv2.cvtColor(face_image, cv2.COLOR_BGR2RGB)

        # Normalise to [-1, 1]
        tensor = rgb.astype(np.float32)
        tensor = (tensor - 127.5) / 127.5

        # HWC -> CHW -> NCHW
        tensor = tensor.transpose(2, 0, 1)
        tensor = np.expand_dims(tensor, axis=0)

        return np.ascontiguousarray(tensor)

    @staticmethod
    def compute_similarity(
        embedding_a: np.ndarray,
        embedding_b: np.ndarray,
    ) -> float:
        """Compute cosine similarity between two face embeddings.

        Both embeddings should be L2-normalised so that cosine similarity
        equals the dot product.

        Args:
            embedding_a: First embedding vector of shape ``(512,)``.
            embedding_b: Second embedding vector of shape ``(512,)``.

        Returns:
            float: Cosine similarity in ``[-1, 1]``.  Higher values
                indicate greater similarity.
        """
        a = embedding_a.flatten().astype(np.float64)
        b = embedding_b.flatten().astype(np.float64)

        norm_a = np.linalg.norm(a)
        norm_b = np.linalg.norm(b)

        if norm_a == 0 or norm_b == 0:
            return 0.0

        similarity = float(np.dot(a, b) / (norm_a * norm_b))
        return max(-1.0, min(1.0, similarity))

    @staticmethod
    def compute_distance(
        embedding_a: np.ndarray,
        embedding_b: np.ndarray,
    ) -> float:
        """Compute Euclidean distance between two face embeddings.

        Args:
            embedding_a: First embedding vector of shape ``(512,)``.
            embedding_b: Second embedding vector of shape ``(512,)``.

        Returns:
            float: Euclidean distance (non-negative).  Lower values
                indicate greater similarity.
        """
        a = embedding_a.flatten().astype(np.float64)
        b = embedding_b.flatten().astype(np.float64)
        return float(np.linalg.norm(a - b))

    def is_same_person(
        self,
        embedding_a: np.ndarray,
        embedding_b: np.ndarray,
        *,
        similarity_threshold: float | None = None,
    ) -> bool:
        """Determine whether two embeddings belong to the same person.

        Args:
            embedding_a: First embedding vector.
            embedding_b: Second embedding vector.
            similarity_threshold: Cosine similarity threshold.
                Defaults to ``DEFAULT_SIMILARITY_THRESHOLD``.

        Returns:
            bool: ``True`` if the embeddings match.
        """
        if similarity_threshold is None:
            similarity_threshold = DEFAULT_SIMILARITY_THRESHOLD
        sim = self.compute_similarity(embedding_a, embedding_b)
        return sim >= similarity_threshold

    @staticmethod
    def find_best_match(
        query_embedding: np.ndarray,
        gallery_embeddings: np.ndarray,
        gallery_ids: list[str] | None = None,
        similarity_threshold: float | None = None,
    ) -> Optional[tuple[int, float, Optional[str]]]:
        """Find the best matching face in a gallery.

        Args:
            query_embedding: Query embedding of shape ``(512,)``.
            gallery_embeddings: Gallery embeddings of shape ``(N, 512)``.
            gallery_ids: Optional list of gallery face identifiers.
            similarity_threshold: Minimum similarity for a valid match.

        Returns:
            Optional[tuple[int, float, Optional[str]]]: A 3-tuple of
                ``(gallery_index, similarity, gallery_id)`` for the
                best match, or ``None`` if no match exceeds the
                threshold.
        """
        if similarity_threshold is None:
            similarity_threshold = DEFAULT_SIMILARITY_THRESHOLD

        if gallery_embeddings.shape[0] == 0:
            return None

        query = query_embedding.flatten().astype(np.float64)
        query_norm = np.linalg.norm(query)
        if query_norm == 0:
            return None
        query = query / query_norm

        gallery = gallery_embeddings.astype(np.float64)
        gallery_norms = np.linalg.norm(gallery, axis=1, keepdims=True)
        gallery_norms = np.maximum(gallery_norms, 1e-10)
        gallery = gallery / gallery_norms

        similarities = gallery @ query
        best_idx = int(np.argmax(similarities))
        best_sim = float(similarities[best_idx])

        if best_sim < similarity_threshold:
            return None

        gallery_id = gallery_ids[best_idx] if gallery_ids else None
        return (best_idx, best_sim, gallery_id)
