"""
OSNet-based Person Re-Identification Encoder for VisionAI.

Extracts 512-dimensional appearance feature vectors from person crop images
using an OSNet x1.0 ONNX model.  Provides distance computation, gallery
matching, and ranking utilities for cross-camera person re-identification.

The encoder expects BGR person crop images of arbitrary size; they are
resized internally to 256x128 (HxW) as required by OSNet.

Usage::

    from app.cv.recognizers.reid_encoder import ReIDEncoder

    encoder = ReIDEncoder(session)
    features = encoder.extract_features(person_crop)
    distance = encoder.compute_distance(features_a, features_b)
"""

from __future__ import annotations

from typing import Optional

import cv2
import numpy as np
import onnxruntime as ort
import structlog

from app.cv.inference_engine import ONNXInferenceEngine

logger = structlog.stdlib.get_logger(__name__)

# ImageNet normalisation constants
IMAGENET_MEAN: tuple[float, float, float] = (0.485, 0.456, 0.406)
IMAGENET_STD: tuple[float, float, float] = (0.229, 0.224, 0.225)

# OSNet input dimensions (height, width)
OSNET_INPUT_HEIGHT: int = 256
OSNET_INPUT_WIDTH: int = 128

# Default thresholds for person matching
DEFAULT_DISTANCE_THRESHOLD: float = 0.6
DEFAULT_SIMILARITY_THRESHOLD: float = 0.5
EMBEDDING_DIM: int = 512


class ReIDEncoder:
    """OSNet x1.0 person re-identification feature encoder.

    Extracts L2-normalised 512-dimensional appearance descriptors from
    person crop images.  The descriptors can be compared using Euclidean
    distance or cosine similarity for cross-camera person matching.

    Args:
        session: Loaded ONNX Runtime session for the OSNet x1.0 model.
        input_height: Expected input image height.  Defaults to ``256``.
        input_width: Expected input image width.  Defaults to ``128``.
        embedding_dim: Dimensionality of the output feature vector.
            Defaults to ``512``.
    """

    def __init__(
        self,
        session: ort.InferenceSession,
        input_height: int = OSNET_INPUT_HEIGHT,
        input_width: int = OSNET_INPUT_WIDTH,
        embedding_dim: int = EMBEDDING_DIM,
    ) -> None:
        self.engine = ONNXInferenceEngine(session)
        self.input_height = input_height
        self.input_width = input_width
        self.embedding_dim = embedding_dim

        logger.info(
            "reid_encoder.initialised",
            input_size=(input_height, input_width),
            embedding_dim=embedding_dim,
        )

    # ── Feature Extraction ─────────────────────────────────────────────

    def extract_features(self, person_crop: np.ndarray) -> np.ndarray:
        """Extract a re-identification feature vector from a person crop.

        The crop is resized to 256x128, normalised with ImageNet mean/std,
        converted to NCHW layout, and passed through the OSNet model.  The
        output vector is L2-normalised to unit length.

        Args:
            person_crop: BGR person image of arbitrary size with shape
                ``(H, W, 3)``.

        Returns:
            np.ndarray: L2-normalised feature vector of shape ``(512,)``
                as float32.

        Raises:
            ValueError: If the input image is empty or has invalid shape.
        """
        if person_crop is None or person_crop.size == 0:
            raise ValueError("Input person crop is empty or None.")

        if len(person_crop.shape) != 3 or person_crop.shape[2] != 3:
            raise ValueError(
                f"Expected BGR image with shape (H, W, 3), got {person_crop.shape}"
            )

        tensor = self._preprocess(person_crop)
        outputs = self.engine.infer(tensor)
        embedding = outputs[0].flatten().astype(np.float32)

        # L2 normalise
        norm = np.linalg.norm(embedding)
        if norm > 1e-6:
            embedding = embedding / norm

        return embedding

    def extract_features_batch(
        self,
        crops: list[np.ndarray],
    ) -> np.ndarray:
        """Extract feature vectors for a batch of person crops.

        Each crop is processed independently through the model since ONNX
        models may have fixed batch size constraints.

        Args:
            crops: List of BGR person images, each of shape ``(H, W, 3)``.

        Returns:
            np.ndarray: Feature matrix of shape ``(N, 512)`` as float32.
                Returns an empty array of shape ``(0, 512)`` if the input
                list is empty.
        """
        if not crops:
            return np.empty((0, self.embedding_dim), dtype=np.float32)

        embeddings: list[np.ndarray] = []

        for crop in crops:
            try:
                emb = self.extract_features(crop)
                embeddings.append(emb)
            except (ValueError, RuntimeError) as exc:
                logger.warning(
                    "reid_encoder.batch_item_failed",
                    error=str(exc),
                )
                # Insert a zero vector for failed extractions
                embeddings.append(np.zeros(self.embedding_dim, dtype=np.float32))

        return np.array(embeddings, dtype=np.float32)

    # ── Preprocessing ──────────────────────────────────────────────────

    def _preprocess(self, image: np.ndarray) -> np.ndarray:
        """Preprocess a person crop image for the OSNet model.

        Steps:
        1. Resize to ``(input_height, input_width)`` -- 256x128 default.
        2. Convert BGR to RGB.
        3. Scale pixel values to ``[0, 1]``.
        4. Normalise with ImageNet mean and standard deviation.
        5. Transpose from HWC to CHW layout.
        6. Add batch dimension to produce NCHW tensor.

        Args:
            image: BGR image of shape ``(H, W, 3)``.

        Returns:
            np.ndarray: Preprocessed tensor of shape
                ``(1, 3, input_height, input_width)`` as float32.
        """
        # Resize to model input dimensions
        resized = cv2.resize(
            image,
            (self.input_width, self.input_height),
            interpolation=cv2.INTER_LINEAR,
        )

        # BGR -> RGB
        rgb = cv2.cvtColor(resized, cv2.COLOR_BGR2RGB)

        # Scale to [0, 1]
        tensor = rgb.astype(np.float32) / 255.0

        # ImageNet normalisation
        mean = np.array(IMAGENET_MEAN, dtype=np.float32).reshape(1, 1, 3)
        std = np.array(IMAGENET_STD, dtype=np.float32).reshape(1, 1, 3)
        tensor = (tensor - mean) / std

        # HWC -> CHW -> NCHW
        tensor = tensor.transpose(2, 0, 1)
        tensor = np.expand_dims(tensor, axis=0)

        return np.ascontiguousarray(tensor)

    # ── Distance & Similarity Computation ──────────────────────────────

    @staticmethod
    def compute_distance(
        feat1: np.ndarray,
        feat2: np.ndarray,
    ) -> float:
        """Compute Euclidean distance between two feature vectors.

        For L2-normalised vectors the Euclidean distance is related to
        cosine similarity by ``d = sqrt(2 - 2 * cos_sim)``.

        Args:
            feat1: First feature vector of shape ``(512,)``.
            feat2: Second feature vector of shape ``(512,)``.

        Returns:
            float: Euclidean distance (non-negative).  Lower values
                indicate greater similarity.
        """
        a = feat1.flatten().astype(np.float64)
        b = feat2.flatten().astype(np.float64)
        return float(np.linalg.norm(a - b))

    @staticmethod
    def compute_cosine_similarity(
        feat1: np.ndarray,
        feat2: np.ndarray,
    ) -> float:
        """Compute cosine similarity between two feature vectors.

        For L2-normalised vectors this simplifies to a dot product.

        Args:
            feat1: First feature vector of shape ``(512,)``.
            feat2: Second feature vector of shape ``(512,)``.

        Returns:
            float: Cosine similarity in ``[-1, 1]``.  Higher values
                indicate greater similarity.
        """
        a = feat1.flatten().astype(np.float64)
        b = feat2.flatten().astype(np.float64)

        norm_a = np.linalg.norm(a)
        norm_b = np.linalg.norm(b)

        if norm_a < 1e-10 or norm_b < 1e-10:
            return 0.0

        similarity = float(np.dot(a, b) / (norm_a * norm_b))
        return max(-1.0, min(1.0, similarity))

    # ── Person Matching ────────────────────────────────────────────────

    def match_persons(
        self,
        query_features: np.ndarray,
        gallery_features: np.ndarray,
        threshold: float = DEFAULT_DISTANCE_THRESHOLD,
    ) -> list[dict]:
        """Match query persons against a gallery using greedy assignment.

        Computes a pairwise Euclidean distance matrix between query and
        gallery feature sets, then performs greedy (best-first) matching
        where each query is assigned to the nearest unmatched gallery
        entry if the distance is below ``threshold``.

        Args:
            query_features: Feature matrix of shape ``(Q, 512)`` for the
                query set.
            gallery_features: Feature matrix of shape ``(G, 512)`` for the
                gallery set.
            threshold: Maximum Euclidean distance for a valid match.

        Returns:
            list[dict]: List of match dictionaries, each containing:
                - ``query_idx`` (int): Index in the query set.
                - ``gallery_idx`` (int): Index in the gallery set.
                - ``distance`` (float): Euclidean distance.
                - ``similarity`` (float): Cosine similarity.
        """
        if query_features.shape[0] == 0 or gallery_features.shape[0] == 0:
            return []

        # Compute pairwise Euclidean distance matrix  (Q x G)
        q = query_features.astype(np.float64)
        g = gallery_features.astype(np.float64)

        # ||a - b||^2 = ||a||^2 + ||b||^2 - 2 * a . b
        q_sq = np.sum(q ** 2, axis=1, keepdims=True)  # (Q, 1)
        g_sq = np.sum(g ** 2, axis=1, keepdims=True)  # (G, 1)
        dist_sq = q_sq + g_sq.T - 2.0 * (q @ g.T)
        dist_sq = np.maximum(dist_sq, 0.0)
        dist_matrix = np.sqrt(dist_sq)  # (Q, G)

        # Greedy matching: assign each query to the closest unmatched gallery
        matches: list[dict] = []
        used_gallery: set[int] = set()

        # Flatten and sort all (query, gallery) pairs by distance
        q_count, g_count = dist_matrix.shape
        pairs = []
        for qi in range(q_count):
            for gi in range(g_count):
                pairs.append((dist_matrix[qi, gi], qi, gi))

        pairs.sort(key=lambda x: x[0])

        used_query: set[int] = set()
        for distance, qi, gi in pairs:
            if qi in used_query or gi in used_gallery:
                continue
            if distance > threshold:
                break

            similarity = self.compute_cosine_similarity(
                query_features[qi], gallery_features[gi]
            )

            matches.append({
                "query_idx": int(qi),
                "gallery_idx": int(gi),
                "distance": round(float(distance), 4),
                "similarity": round(similarity, 4),
            })

            used_query.add(qi)
            used_gallery.add(gi)

        return matches

    def rank_gallery(
        self,
        query_feat: np.ndarray,
        gallery: list[tuple[str, np.ndarray]],
        top_k: int = 10,
    ) -> list[dict]:
        """Rank gallery entries by similarity to a query feature vector.

        Args:
            query_feat: Query feature vector of shape ``(512,)``.
            gallery: List of ``(person_id, feature_vector)`` tuples.
            top_k: Maximum number of top-ranked results to return.

        Returns:
            list[dict]: Ranked list of matches, each containing:
                - ``person_id`` (str): Gallery person identifier.
                - ``similarity`` (float): Cosine similarity score.
                - ``distance`` (float): Euclidean distance.
                - ``rank`` (int): 1-indexed rank position.
        """
        if not gallery or query_feat.size == 0:
            return []

        results: list[tuple[str, float, float]] = []
        for person_id, feat in gallery:
            sim = self.compute_cosine_similarity(query_feat, feat)
            dist = self.compute_distance(query_feat, feat)
            results.append((person_id, sim, dist))

        # Sort by similarity descending
        results.sort(key=lambda x: x[1], reverse=True)

        ranked: list[dict] = []
        for rank, (person_id, sim, dist) in enumerate(results[:top_k], start=1):
            ranked.append({
                "person_id": person_id,
                "similarity": round(sim, 4),
                "distance": round(dist, 4),
                "rank": rank,
            })

        return ranked

    def is_same_person(
        self,
        feat1: np.ndarray,
        feat2: np.ndarray,
        *,
        distance_threshold: float | None = None,
    ) -> bool:
        """Determine whether two feature vectors belong to the same person.

        Args:
            feat1: First feature vector.
            feat2: Second feature vector.
            distance_threshold: Maximum Euclidean distance to consider a
                match.  Defaults to ``DEFAULT_DISTANCE_THRESHOLD``.

        Returns:
            bool: ``True`` if the features match.
        """
        if distance_threshold is None:
            distance_threshold = DEFAULT_DISTANCE_THRESHOLD
        dist = self.compute_distance(feat1, feat2)
        return dist <= distance_threshold

    @staticmethod
    def find_best_match(
        query_embedding: np.ndarray,
        gallery_embeddings: np.ndarray,
        gallery_ids: list[str] | None = None,
        distance_threshold: float | None = None,
    ) -> Optional[tuple[int, float, float, Optional[str]]]:
        """Find the best matching person in a gallery by Euclidean distance.

        Args:
            query_embedding: Query feature vector of shape ``(512,)``.
            gallery_embeddings: Gallery features of shape ``(N, 512)``.
            gallery_ids: Optional list of gallery person identifiers.
            distance_threshold: Maximum distance for a valid match.

        Returns:
            Optional[tuple[int, float, float, Optional[str]]]: A 4-tuple
                of ``(gallery_index, distance, similarity, gallery_id)``
                for the best match, or ``None`` if no match is within
                the threshold.
        """
        if distance_threshold is None:
            distance_threshold = DEFAULT_DISTANCE_THRESHOLD

        if gallery_embeddings.shape[0] == 0:
            return None

        query = query_embedding.flatten().astype(np.float64)
        gallery = gallery_embeddings.astype(np.float64)

        # Compute all distances
        diffs = gallery - query[np.newaxis, :]
        distances = np.linalg.norm(diffs, axis=1)

        best_idx = int(np.argmin(distances))
        best_dist = float(distances[best_idx])

        if best_dist > distance_threshold:
            return None

        # Compute cosine similarity for the best match
        q_norm = np.linalg.norm(query)
        g_norm = np.linalg.norm(gallery[best_idx])
        if q_norm > 1e-10 and g_norm > 1e-10:
            cos_sim = float(np.dot(query, gallery[best_idx]) / (q_norm * g_norm))
        else:
            cos_sim = 0.0

        gallery_id = gallery_ids[best_idx] if gallery_ids else None
        return (best_idx, best_dist, cos_sim, gallery_id)
