"""
SCRFD Face Detector for VisionAI.

Implements face detection using the SCRFD (Sample and Computation
Redistribution for Efficient Face Detection) model.  Produces bounding
boxes, confidence scores, and 5-point facial landmarks for each
detected face.  Includes an affine-transform-based face alignment
routine that normalises faces to a canonical 112x112 crop suitable for
downstream recognition models (ArcFace, etc.).

Usage::

    from app.cv.detectors.face_detector import FaceDetector

    detector = FaceDetector(session)
    faces = detector.detect(frame)
    for face in faces:
        print(f"Face: conf={face.confidence:.2f}, landmarks={face.landmarks.shape}")
"""

from __future__ import annotations

from dataclasses import dataclass, field
from typing import Optional

import cv2
import numpy as np
import onnxruntime as ort
import structlog

from app.cv.inference_engine import ONNXInferenceEngine, letterbox

logger = structlog.stdlib.get_logger(__name__)

# ── Canonical landmark positions for 112x112 aligned face ────────────
# These correspond to left eye, right eye, nose tip, left mouth corner,
# right mouth corner in the standard ArcFace alignment template.
ARCFACE_REFERENCE_LANDMARKS = np.array(
    [
        [38.2946, 51.6963],
        [73.5318, 51.5014],
        [56.0252, 71.7366],
        [41.5493, 92.3655],
        [70.7299, 92.2041],
    ],
    dtype=np.float32,
)


@dataclass
class FaceDetection:
    """A single face detection result.

    Attributes:
        bbox: Bounding box as ``(x1, y1, x2, y2)`` in pixel coordinates.
        confidence: Detection confidence score in ``[0, 1]``.
        landmarks: Facial landmark coordinates as a ``(5, 2)`` NumPy
            array.  The five points are: left eye, right eye, nose tip,
            left mouth corner, right mouth corner.
        aligned_face: Optional 112x112 BGR face crop aligned using an
            affine transform.  Populated when ``align=True`` in
            ``detect()``.
    """

    bbox: tuple[float, float, float, float]
    confidence: float
    landmarks: np.ndarray  # shape (5, 2)
    aligned_face: Optional[np.ndarray] = field(default=None, repr=False)

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
    def width(self) -> float:
        """Return the bounding box width."""
        return max(0.0, self.bbox[2] - self.bbox[0])

    @property
    def height(self) -> float:
        """Return the bounding box height."""
        return max(0.0, self.bbox[3] - self.bbox[1])

    def to_dict(self) -> dict:
        """Serialise to a plain dictionary (excluding the aligned face image)."""
        return {
            "bbox": list(self.bbox),
            "confidence": round(self.confidence, 4),
            "landmarks": self.landmarks.tolist(),
        }


def align_face(
    image: np.ndarray,
    landmarks: np.ndarray,
    output_size: tuple[int, int] = (112, 112),
    reference: np.ndarray | None = None,
) -> np.ndarray:
    """Align a face using a similarity transform derived from 5 facial
    landmarks.

    Computes an affine transformation that maps the detected landmarks
    to a canonical reference template, then warps the image to produce
    a normalised face crop.

    Args:
        image: Source BGR image containing the face.
        landmarks: Detected landmark coordinates as ``(5, 2)``.
        output_size: Output face crop size as ``(width, height)``.
            Defaults to ``(112, 112)``.
        reference: Reference landmark template of shape ``(5, 2)``.
            Defaults to the ArcFace canonical positions.

    Returns:
        np.ndarray: Aligned face image of shape ``(H, W, 3)`` in BGR.
    """
    if reference is None:
        reference = ARCFACE_REFERENCE_LANDMARKS.copy()

    src_pts = landmarks.astype(np.float32)
    dst_pts = reference.astype(np.float32)

    # Estimate the similarity transform using the Umeyama algorithm
    transform_matrix = _umeyama_transform(src_pts, dst_pts)

    # Warp the image using the 2x3 affine matrix
    aligned = cv2.warpAffine(
        image,
        transform_matrix[:2],
        output_size,
        borderMode=cv2.BORDER_REPLICATE,
    )

    return aligned


def _umeyama_transform(
    src: np.ndarray,
    dst: np.ndarray,
) -> np.ndarray:
    """Estimate an affine transform between two point sets using the
    Umeyama algorithm (similarity transform with uniform scaling).

    Args:
        src: Source points of shape ``(N, 2)``.
        dst: Destination points of shape ``(N, 2)``.

    Returns:
        np.ndarray: 3x3 affine transformation matrix.
    """
    num = src.shape[0]
    dim = src.shape[1]

    # Compute means
    src_mean = src.mean(axis=0)
    dst_mean = dst.mean(axis=0)

    # Centre the points
    src_demean = src - src_mean
    dst_demean = dst - dst_mean

    # Compute covariance
    A = dst_demean.T @ src_demean / num

    # Compute SVD
    U, S, Vt = np.linalg.svd(A)

    # Construct the rotation
    d = np.ones(dim, dtype=np.float64)
    if np.linalg.det(A) < 0:
        d[dim - 1] = -1

    T = np.eye(dim + 1, dtype=np.float64)

    rank = np.linalg.matrix_rank(A)
    if rank == 0:
        return T

    if rank == dim - 1:
        if np.linalg.det(U) * np.linalg.det(Vt) > 0:
            T[:dim, :dim] = U @ Vt
        else:
            s = d[dim - 1]
            d[dim - 1] = -1
            T[:dim, :dim] = U @ np.diag(d) @ Vt
            d[dim - 1] = s
    else:
        T[:dim, :dim] = U @ np.diag(d) @ Vt

    # Compute scale
    src_var = src_demean.var(axis=0).sum()
    scale = (S * d).sum() / src_var if src_var > 0 else 1.0

    T[:dim, dim] = dst_mean - scale * (T[:dim, :dim] @ src_mean)
    T[:dim, :dim] *= scale

    return T.astype(np.float32)


class FaceDetector:
    """SCRFD-based face detector.

    Wraps an ONNX Runtime session for a SCRFD model and provides a
    high-level ``detect()`` method that returns face bounding boxes,
    landmarks, and optionally aligned face crops.

    The detector supports SCRFD models with built-in landmark prediction
    (``_bnkps`` variants) that output bounding box, score, and 5-point
    landmarks at multiple feature map strides (8, 16, 32).

    Args:
        session: Loaded ONNX Runtime session for SCRFD.
        confidence_threshold: Minimum confidence to retain a detection.
        nms_threshold: IoU threshold for NMS.
        input_size: Model input resolution as ``(width, height)``.
    """

    # Feature map strides for SCRFD anchor-free heads
    _STRIDES: list[int] = [8, 16, 32]
    _NUM_ANCHORS: int = 2  # anchors per stride

    def __init__(
        self,
        session: ort.InferenceSession,
        confidence_threshold: float = 0.5,
        nms_threshold: float = 0.4,
        input_size: tuple[int, int] = (640, 640),
    ) -> None:
        self.engine = ONNXInferenceEngine(session)
        self.confidence_threshold = confidence_threshold
        self.nms_threshold = nms_threshold
        self.input_size = input_size

        # Detect model variant from output count
        output_count = len(self.engine.output_names)
        self._has_landmarks = output_count >= 9  # 3 scores + 3 bboxes + 3 landmarks

        logger.info(
            "face_detector.initialised",
            input_size=input_size,
            has_landmarks=self._has_landmarks,
            confidence_threshold=confidence_threshold,
        )

    def detect(
        self,
        image: np.ndarray,
        *,
        align: bool = True,
        max_faces: int = 50,
    ) -> list[FaceDetection]:
        """Detect faces in a single image.

        Args:
            image: Input BGR image as a NumPy array (H, W, C).
            align: If ``True``, compute aligned 112x112 face crops.
            max_faces: Maximum number of faces to return.

        Returns:
            list[FaceDetection]: Detected faces sorted by confidence
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

        # ── Post-process ──────────────────────────────────────────────
        faces = self._postprocess(
            outputs,
            orig_shape=(orig_h, orig_w),
            ratio=ratio,
            pad=pad,
        )

        # Align faces
        if align:
            for face in faces:
                try:
                    face.aligned_face = align_face(image, face.landmarks)
                except Exception:
                    logger.debug("face_detector.alignment_failed", bbox=face.bbox)
                    face.aligned_face = None

        # Limit results
        faces = faces[:max_faces]

        return faces

    def _postprocess(
        self,
        outputs: list[np.ndarray],
        orig_shape: tuple[int, int],
        ratio: float,
        pad: tuple[float, float],
    ) -> list[FaceDetection]:
        """Post-process raw SCRFD output tensors.

        SCRFD produces 9 output tensors (for the ``_bnkps`` variant):
        - 3 score maps  (one per stride): shape ``(1, num_anchors*H*W, 1)``
        - 3 bbox maps   (one per stride): shape ``(1, num_anchors*H*W, 4)``
        - 3 landmark maps (one per stride): shape ``(1, num_anchors*H*W, 10)``

        Args:
            outputs: Raw model output tensors.
            orig_shape: Original image ``(height, width)``.
            ratio: Letterbox scale ratio.
            pad: Letterbox padding ``(dw, dh)``.

        Returns:
            list[FaceDetection]: Post-processed face detections.
        """
        input_h, input_w = self.input_size[1], self.input_size[0]
        num_strides = len(self._STRIDES)

        all_scores: list[np.ndarray] = []
        all_bboxes: list[np.ndarray] = []
        all_landmarks: list[np.ndarray] = []

        for idx, stride in enumerate(self._STRIDES):
            # Feature map dimensions
            feat_h = input_h // stride
            feat_w = input_w // stride

            # Extract tensors for this stride
            scores = outputs[idx]  # (1, N, 1)
            bbox_deltas = outputs[idx + num_strides]  # (1, N, 4)

            if self._has_landmarks:
                kps_deltas = outputs[idx + num_strides * 2]  # (1, N, 10)
            else:
                kps_deltas = None

            # Remove batch dimension
            scores = scores[0]  # (N, 1)
            bbox_deltas = bbox_deltas[0]  # (N, 4)

            # Generate anchor centres for this stride
            anchor_centres = self._generate_anchor_centres(
                feat_h, feat_w, stride,
            )

            # Decode bounding boxes: deltas are in stride units
            # bbox_deltas format: (left, top, right, bottom) distances from anchor
            x1 = anchor_centres[:, 0] - bbox_deltas[:, 0] * stride
            y1 = anchor_centres[:, 1] - bbox_deltas[:, 1] * stride
            x2 = anchor_centres[:, 0] + bbox_deltas[:, 2] * stride
            y2 = anchor_centres[:, 1] + bbox_deltas[:, 3] * stride

            bboxes = np.stack([x1, y1, x2, y2], axis=1)

            # Decode landmarks
            if kps_deltas is not None:
                kps = kps_deltas[0] if kps_deltas.ndim == 3 else kps_deltas
                if kps.ndim == 3:
                    kps = kps[0]
                kps = kps_deltas[0] if kps_deltas.ndim == 3 else kps_deltas
                landmarks = np.zeros((kps.shape[0], 5, 2), dtype=np.float32)
                for k in range(5):
                    landmarks[:, k, 0] = anchor_centres[:, 0] + kps[:, k * 2] * stride
                    landmarks[:, k, 1] = anchor_centres[:, 1] + kps[:, k * 2 + 1] * stride
            else:
                landmarks = np.zeros((bboxes.shape[0], 5, 2), dtype=np.float32)

            all_scores.append(scores.flatten())
            all_bboxes.append(bboxes)
            all_landmarks.append(landmarks)

        # Concatenate across strides
        scores = np.concatenate(all_scores, axis=0)
        bboxes = np.concatenate(all_bboxes, axis=0)
        landmarks = np.concatenate(all_landmarks, axis=0)

        # Confidence filter
        mask = scores >= self.confidence_threshold
        if not np.any(mask):
            return []

        scores = scores[mask]
        bboxes = bboxes[mask]
        landmarks = landmarks[mask]

        # NMS
        from app.cv.inference_engine import nms as apply_nms
        keep = apply_nms(bboxes, scores, self.nms_threshold)

        if len(keep) == 0:
            return []

        scores = scores[keep]
        bboxes = bboxes[keep]
        landmarks = landmarks[keep]

        # Scale coordinates back to original image
        pad_w, pad_h = pad
        bboxes[:, 0] = (bboxes[:, 0] - pad_w) / ratio
        bboxes[:, 1] = (bboxes[:, 1] - pad_h) / ratio
        bboxes[:, 2] = (bboxes[:, 2] - pad_w) / ratio
        bboxes[:, 3] = (bboxes[:, 3] - pad_h) / ratio

        landmarks[:, :, 0] = (landmarks[:, :, 0] - pad_w) / ratio
        landmarks[:, :, 1] = (landmarks[:, :, 1] - pad_h) / ratio

        # Clip to image boundaries
        orig_h, orig_w = orig_shape
        bboxes[:, 0] = np.clip(bboxes[:, 0], 0, orig_w)
        bboxes[:, 1] = np.clip(bboxes[:, 1], 0, orig_h)
        bboxes[:, 2] = np.clip(bboxes[:, 2], 0, orig_w)
        bboxes[:, 3] = np.clip(bboxes[:, 3], 0, orig_h)

        # Build FaceDetection objects
        faces: list[FaceDetection] = []
        for i in range(len(keep)):
            face = FaceDetection(
                bbox=(
                    float(bboxes[i, 0]),
                    float(bboxes[i, 1]),
                    float(bboxes[i, 2]),
                    float(bboxes[i, 3]),
                ),
                confidence=float(scores[i]),
                landmarks=landmarks[i].copy(),
            )
            faces.append(face)

        # Sort by confidence descending
        faces.sort(key=lambda f: f.confidence, reverse=True)

        return faces

    def _generate_anchor_centres(
        self,
        feat_h: int,
        feat_w: int,
        stride: int,
    ) -> np.ndarray:
        """Generate anchor centre coordinates for a single feature map.

        Args:
            feat_h: Feature map height.
            feat_w: Feature map width.
            stride: Feature stride in pixels.

        Returns:
            np.ndarray: Anchor centres of shape ``(feat_h * feat_w * num_anchors, 2)``.
        """
        # Create grid of anchor centres
        shift_x = (np.arange(feat_w) + 0.5) * stride
        shift_y = (np.arange(feat_h) + 0.5) * stride
        grid_x, grid_y = np.meshgrid(shift_x, shift_y)
        centres = np.stack([grid_x.ravel(), grid_y.ravel()], axis=1)

        # Repeat for num_anchors
        centres = np.tile(centres, (1, self._NUM_ANCHORS)).reshape(-1, 2)

        return centres.astype(np.float32)

    def get_largest_face(
        self,
        image: np.ndarray,
        **kwargs,
    ) -> Optional[FaceDetection]:
        """Detect faces and return the one with the largest bounding box area.

        Args:
            image: Input BGR image.
            **kwargs: Additional arguments passed to ``detect()``.

        Returns:
            Optional[FaceDetection]: The largest face, or ``None`` if
                no faces are detected.
        """
        faces = self.detect(image, **kwargs)
        if not faces:
            return None
        return max(faces, key=lambda f: f.area)
