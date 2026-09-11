"""
SORT (Simple Online and Realtime Tracking) Tracker for VisionAI.

Lightweight fallback tracker based on the SORT algorithm (Bewley et al.,
ICIP 2016).  Uses a Kalman Filter for motion prediction and the
Hungarian algorithm for detection-to-track association via IoU distance.

This implementation is simpler than ByteTrack (single-stage association,
no low-confidence handling) but sufficient for scenarios with reliable
detections and lower computational budgets.

Usage::

    from app.cv.trackers.sort_tracker import SortTracker

    tracker = SortTracker()
    for frame in video:
        detections = detector.detect(frame)
        tracks = tracker.update(detections)
"""

from __future__ import annotations

from typing import Optional

import numpy as np
import structlog
from scipy.optimize import linear_sum_assignment

logger = structlog.stdlib.get_logger(__name__)


class _KalmanBoxTracker:
    """Internal tracked object with Kalman Filter state.

    Tracks a bounding box in state space ``[cx, cy, s, r, vx, vy, vs]``
    where ``s`` is the scale (area) and ``r`` is the aspect ratio.

    Attributes:
        id: Unique tracker identifier.
        bbox: Current bounding box ``[x1, y1, x2, y2]``.
        hits: Total number of successful updates.
        age: Total number of frames since creation.
        time_since_update: Frames since last successful update.
    """

    _count: int = 0

    def __init__(self, bbox: np.ndarray) -> None:
        """Initialise from an initial bounding box.

        Args:
            bbox: Detection as ``[x1, y1, x2, y2]``.
        """
        _KalmanBoxTracker._count += 1
        self.id: int = _KalmanBoxTracker._count

        # Convert bbox to state [cx, cy, s, r]
        z = self._bbox_to_z(bbox)

        # State: [cx, cy, s, r, vx, vy, vs]
        self.x = np.zeros(7, dtype=np.float64)
        self.x[:4] = z

        # State covariance
        self.P = np.eye(7, dtype=np.float64) * 10.0
        self.P[4:, 4:] *= 1000.0  # High uncertainty on velocities

        # State transition
        self.F = np.eye(7, dtype=np.float64)
        self.F[0, 4] = 1.0  # cx += vx
        self.F[1, 5] = 1.0  # cy += vy
        self.F[2, 6] = 1.0  # s += vs

        # Measurement matrix
        self.H = np.eye(4, 7, dtype=np.float64)

        # Measurement noise
        self.R = np.eye(4, dtype=np.float64)
        self.R[2, 2] *= 10.0  # scale
        self.R[3, 3] *= 10.0  # ratio

        # Process noise
        self.Q = np.eye(7, dtype=np.float64)
        self.Q[4:, 4:] *= 0.01
        self.Q[6, 6] *= 0.0001

        self.bbox = bbox.astype(np.float64)
        self.hits: int = 1
        self.age: int = 0
        self.time_since_update: int = 0

    @staticmethod
    def _bbox_to_z(bbox: np.ndarray) -> np.ndarray:
        """Convert ``[x1, y1, x2, y2]`` to ``[cx, cy, s, r]``."""
        w = bbox[2] - bbox[0]
        h = bbox[3] - bbox[1]
        cx = bbox[0] + w / 2.0
        cy = bbox[1] + h / 2.0
        s = w * h  # scale (area)
        r = w / h if h > 0 else 1.0  # aspect ratio
        return np.array([cx, cy, s, r], dtype=np.float64)

    @staticmethod
    def _z_to_bbox(z: np.ndarray) -> np.ndarray:
        """Convert ``[cx, cy, s, r]`` to ``[x1, y1, x2, y2]``."""
        s = max(z[2], 1.0)
        r = z[3]
        w = np.sqrt(s * r)
        h = s / w if w > 0 else 0.0
        return np.array([
            z[0] - w / 2.0,
            z[1] - h / 2.0,
            z[0] + w / 2.0,
            z[1] + h / 2.0,
        ], dtype=np.float64)

    def predict(self) -> np.ndarray:
        """Predict the next state and return the predicted bbox."""
        # Prevent negative area
        if self.x[2] + self.x[6] <= 0:
            self.x[6] = 0.0

        # Kalman predict
        self.x = self.F @ self.x
        self.P = self.F @ self.P @ self.F.T + self.Q

        self.age += 1
        self.time_since_update += 1

        self.bbox = self._z_to_bbox(self.x[:4])
        return self.bbox

    def update(self, bbox: np.ndarray) -> None:
        """Update the state with a matched detection.

        Args:
            bbox: Matched detection as ``[x1, y1, x2, y2]``.
        """
        z = self._bbox_to_z(bbox)

        # Kalman update
        y = z - self.H @ self.x
        S = self.H @ self.P @ self.H.T + self.R
        K = self.P @ self.H.T @ np.linalg.inv(S)

        self.x = self.x + K @ y
        self.P = (np.eye(7) - K @ self.H) @ self.P

        self.bbox = bbox.astype(np.float64)
        self.hits += 1
        self.time_since_update = 0

    @staticmethod
    def reset_count() -> None:
        """Reset the global ID counter."""
        _KalmanBoxTracker._count = 0


def _iou_batch(
    bb_a: np.ndarray,
    bb_b: np.ndarray,
) -> np.ndarray:
    """Compute IoU matrix between two sets of bounding boxes.

    Args:
        bb_a: Shape ``(M, 4)`` in ``[x1, y1, x2, y2]`` format.
        bb_b: Shape ``(N, 4)`` in ``[x1, y1, x2, y2]`` format.

    Returns:
        np.ndarray: IoU matrix of shape ``(M, N)``.
    """
    m = bb_a.shape[0]
    n = bb_b.shape[0]

    x1 = np.maximum(bb_a[:, 0].reshape(m, 1), bb_b[:, 0].reshape(1, n))
    y1 = np.maximum(bb_a[:, 1].reshape(m, 1), bb_b[:, 1].reshape(1, n))
    x2 = np.minimum(bb_a[:, 2].reshape(m, 1), bb_b[:, 2].reshape(1, n))
    y2 = np.minimum(bb_a[:, 3].reshape(m, 1), bb_b[:, 3].reshape(1, n))

    inter = np.maximum(0.0, x2 - x1) * np.maximum(0.0, y2 - y1)

    area_a = ((bb_a[:, 2] - bb_a[:, 0]) * (bb_a[:, 3] - bb_a[:, 1])).reshape(m, 1)
    area_b = ((bb_b[:, 2] - bb_b[:, 0]) * (bb_b[:, 3] - bb_b[:, 1])).reshape(1, n)

    union = area_a + area_b - inter
    return np.where(union > 0, inter / union, 0.0)


class SortTracker:
    """SORT multi-object tracker.

    Single-stage association using IoU distance and the Hungarian
    algorithm.  Simpler and faster than ByteTrack but less robust
    to occlusions and missed detections.

    Args:
        max_age: Maximum frames a track survives without updates.
        min_hits: Minimum consecutive hits before output.
        iou_threshold: Minimum IoU for a valid detection-track match.
    """

    def __init__(
        self,
        max_age: int = 30,
        min_hits: int = 3,
        iou_threshold: float = 0.3,
    ) -> None:
        self.max_age = max_age
        self.min_hits = min_hits
        self.iou_threshold = iou_threshold
        self.trackers: list[_KalmanBoxTracker] = []
        self.frame_count: int = 0

        logger.info(
            "sort_tracker.initialised",
            max_age=max_age,
            min_hits=min_hits,
            iou_threshold=iou_threshold,
        )

    def update(
        self,
        detections: np.ndarray,
    ) -> np.ndarray:
        """Update the tracker with detections from a new frame.

        Args:
            detections: Array of shape ``(N, 5)`` with columns
                ``[x1, y1, x2, y2, score]``.  Pass an empty array
                if there are no detections.

        Returns:
            np.ndarray: Array of shape ``(M, 5)`` with columns
                ``[x1, y1, x2, y2, track_id]`` for active tracks.
        """
        self.frame_count += 1

        # Ensure detections is 2D
        if detections.ndim == 1:
            if detections.size == 0:
                detections = np.empty((0, 5), dtype=np.float64)
            else:
                detections = detections.reshape(1, -1)

        # ── Predict existing trackers ─────────────────────────────────
        predicted_bboxes = np.zeros((len(self.trackers), 4), dtype=np.float64)
        to_delete: list[int] = []

        for i, trk in enumerate(self.trackers):
            predicted = trk.predict()
            predicted_bboxes[i] = predicted

            # Remove trackers with NaN predictions
            if np.any(np.isnan(predicted)):
                to_delete.append(i)

        for i in reversed(to_delete):
            self.trackers.pop(i)
            predicted_bboxes = np.delete(predicted_bboxes, i, axis=0)

        # ── Associate detections to trackers ──────────────────────────
        matched, unmatched_dets, unmatched_trks = self._associate(
            detections[:, :4] if detections.shape[0] > 0 else np.empty((0, 4)),
            predicted_bboxes,
        )

        # ── Update matched trackers ───────────────────────────────────
        for det_idx, trk_idx in matched:
            self.trackers[trk_idx].update(detections[det_idx, :4])

        # ── Create new trackers for unmatched detections ──────────────
        for det_idx in unmatched_dets:
            trk = _KalmanBoxTracker(detections[det_idx, :4])
            self.trackers.append(trk)

        # ── Build output and remove dead trackers ─────────────────────
        results: list[np.ndarray] = []
        trackers_to_keep: list[_KalmanBoxTracker] = []

        for trk in self.trackers:
            if trk.time_since_update > self.max_age:
                continue

            trackers_to_keep.append(trk)

            if trk.time_since_update == 0 and (
                trk.hits >= self.min_hits or self.frame_count <= self.min_hits
            ):
                row = np.concatenate([trk.bbox, [float(trk.id)]])
                results.append(row)

        self.trackers = trackers_to_keep

        if results:
            return np.stack(results)
        return np.empty((0, 5), dtype=np.float64)

    def _associate(
        self,
        det_bboxes: np.ndarray,
        trk_bboxes: np.ndarray,
    ) -> tuple[list[tuple[int, int]], list[int], list[int]]:
        """Associate detections with trackers using IoU.

        Args:
            det_bboxes: Detection bboxes ``(N, 4)``.
            trk_bboxes: Tracker predicted bboxes ``(M, 4)``.

        Returns:
            tuple: Matched pairs, unmatched detections, unmatched trackers.
        """
        num_dets = det_bboxes.shape[0]
        num_trks = trk_bboxes.shape[0]

        if num_trks == 0:
            return [], list(range(num_dets)), []

        if num_dets == 0:
            return [], [], list(range(num_trks))

        iou_matrix = _iou_batch(det_bboxes, trk_bboxes)
        cost_matrix = 1.0 - iou_matrix

        row_idx, col_idx = linear_sum_assignment(cost_matrix)

        matched: list[tuple[int, int]] = []
        unmatched_dets = set(range(num_dets))
        unmatched_trks = set(range(num_trks))

        for r, c in zip(row_idx, col_idx):
            if iou_matrix[r, c] < self.iou_threshold:
                continue
            matched.append((int(r), int(c)))
            unmatched_dets.discard(r)
            unmatched_trks.discard(c)

        return matched, sorted(unmatched_dets), sorted(unmatched_trks)

    def reset(self) -> None:
        """Reset the tracker state."""
        self.trackers.clear()
        self.frame_count = 0
        _KalmanBoxTracker.reset_count()
        logger.info("sort_tracker.reset")
