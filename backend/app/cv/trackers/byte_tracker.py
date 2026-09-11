"""
ByteTrack Multi-Object Tracker for VisionAI.

Full implementation of the ByteTrack algorithm (Zhang et al., ECCV 2022)
for online multi-object tracking.  Features a Kalman Filter for motion
prediction, two-stage association using high and low confidence
detections, and the Hungarian algorithm for optimal assignment.

The tracker maintains three track states:
- **Tracked**: Actively matched with detections.
- **Lost**: Not matched for a short period (may be recovered).
- **Removed**: Lost for too long and permanently discarded.

Usage::

    from app.cv.trackers.byte_tracker import ByteTracker

    tracker = ByteTracker()
    for frame in video:
        detections = detector.detect(frame)
        tracks = tracker.update(detections)
        for track in tracks:
            print(f"ID={track.track_id}, bbox={track.bbox}")
"""

from __future__ import annotations

import enum
from dataclasses import dataclass, field
from typing import Optional

import numpy as np
import structlog
from scipy.optimize import linear_sum_assignment

logger = structlog.stdlib.get_logger(__name__)


# ── Track States ─────────────────────────────────────────────────────

class TrackState(enum.IntEnum):
    """Lifecycle states for a single track."""

    NEW = 0
    TRACKED = 1
    LOST = 2
    REMOVED = 3


# ── Kalman Filter ────────────────────────────────────────────────────

class KalmanFilter:
    """Linear Kalman Filter for bounding box state estimation.

    Tracks bounding boxes in 8-dimensional state space:
    ``[cx, cy, a, h, vx, vy, va, vh]`` where ``(cx, cy)`` is the centre,
    ``a`` is the aspect ratio (width/height), ``h`` is the height, and
    ``(vx, vy, va, vh)`` are the corresponding velocities.

    The measurement vector is ``[cx, cy, a, h]``.

    Uses a constant-velocity motion model with process noise proportional
    to the state values.
    """

    def __init__(self) -> None:
        ndim = 4
        dt = 1.0  # time step

        # State transition matrix (constant velocity model)
        self._F = np.eye(2 * ndim, dtype=np.float64)
        for i in range(ndim):
            self._F[i, ndim + i] = dt

        # Measurement matrix (observe position only, not velocity)
        self._H = np.eye(ndim, 2 * ndim, dtype=np.float64)

        # Process noise weight factors
        self._std_weight_position = 1.0 / 20.0
        self._std_weight_velocity = 1.0 / 160.0

    def initiate(
        self,
        measurement: np.ndarray,
    ) -> tuple[np.ndarray, np.ndarray]:
        """Initialise a new track from a measurement.

        Args:
            measurement: Initial measurement ``[cx, cy, a, h]``.

        Returns:
            tuple: A 2-tuple of:
                - **mean** (*np.ndarray*) -- Initial state mean (8,).
                - **covariance** (*np.ndarray*) -- Initial state
                  covariance (8, 8).
        """
        mean_pos = measurement.astype(np.float64)
        mean_vel = np.zeros(4, dtype=np.float64)
        mean = np.concatenate([mean_pos, mean_vel])

        h = measurement[3]
        std = np.array([
            2 * self._std_weight_position * h,  # cx
            2 * self._std_weight_position * h,  # cy
            1e-2,                                 # a
            2 * self._std_weight_position * h,  # h
            10 * self._std_weight_velocity * h, # vx
            10 * self._std_weight_velocity * h, # vy
            1e-5,                                 # va
            10 * self._std_weight_velocity * h, # vh
        ], dtype=np.float64)

        covariance = np.diag(std ** 2)
        return mean, covariance

    def predict(
        self,
        mean: np.ndarray,
        covariance: np.ndarray,
    ) -> tuple[np.ndarray, np.ndarray]:
        """Run the Kalman Filter prediction step.

        Args:
            mean: Prior state mean (8,).
            covariance: Prior state covariance (8, 8).

        Returns:
            tuple: Predicted mean and covariance.
        """
        h = mean[3]
        std_pos = np.array([
            self._std_weight_position * h,
            self._std_weight_position * h,
            1e-2,
            self._std_weight_position * h,
        ], dtype=np.float64)

        std_vel = np.array([
            self._std_weight_velocity * h,
            self._std_weight_velocity * h,
            1e-5,
            self._std_weight_velocity * h,
        ], dtype=np.float64)

        Q = np.diag(np.concatenate([std_pos, std_vel]) ** 2)

        mean_pred = self._F @ mean
        cov_pred = self._F @ covariance @ self._F.T + Q

        return mean_pred, cov_pred

    def update(
        self,
        mean: np.ndarray,
        covariance: np.ndarray,
        measurement: np.ndarray,
    ) -> tuple[np.ndarray, np.ndarray]:
        """Run the Kalman Filter update step.

        Args:
            mean: Predicted state mean (8,).
            covariance: Predicted state covariance (8, 8).
            measurement: Observed measurement ``[cx, cy, a, h]``.

        Returns:
            tuple: Updated mean and covariance.
        """
        h = mean[3]
        std = np.array([
            self._std_weight_position * h,
            self._std_weight_position * h,
            1e-1,
            self._std_weight_position * h,
        ], dtype=np.float64)

        R = np.diag(std ** 2)

        # Innovation
        y = measurement.astype(np.float64) - self._H @ mean

        # Innovation covariance
        S = self._H @ covariance @ self._H.T + R

        # Kalman gain
        K = covariance @ self._H.T @ np.linalg.inv(S)

        # Update
        new_mean = mean + K @ y
        new_cov = (np.eye(len(mean)) - K @ self._H) @ covariance

        return new_mean, new_cov

    def project(
        self,
        mean: np.ndarray,
        covariance: np.ndarray,
    ) -> tuple[np.ndarray, np.ndarray]:
        """Project the state distribution into measurement space.

        Args:
            mean: State mean (8,).
            covariance: State covariance (8, 8).

        Returns:
            tuple: Projected mean (4,) and covariance (4, 4).
        """
        h = mean[3]
        std = np.array([
            self._std_weight_position * h,
            self._std_weight_position * h,
            1e-1,
            self._std_weight_position * h,
        ], dtype=np.float64)

        R = np.diag(std ** 2)

        proj_mean = self._H @ mean
        proj_cov = self._H @ covariance @ self._H.T + R

        return proj_mean, proj_cov


# ── Single Track ─────────────────────────────────────────────────────

class STrack:
    """Single object track with Kalman Filter state estimation.

    Attributes:
        track_id: Unique track identifier.
        bbox: Current bounding box as ``(x1, y1, x2, y2)``.
        score: Current detection confidence.
        state: Current lifecycle state.
        frame_id: Frame number of the last update.
        start_frame: Frame number when the track was created.
        tracklet_len: Number of frames this track has been active.
    """

    _next_id: int = 1
    _kf: KalmanFilter = KalmanFilter()

    def __init__(
        self,
        bbox: np.ndarray,
        score: float,
        class_id: int = 0,
    ) -> None:
        """Initialise a new track.

        Args:
            bbox: Detection bounding box ``[x1, y1, x2, y2]``.
            score: Detection confidence score.
            class_id: Detection class identifier.
        """
        self.track_id: int = 0  # Assigned on activation
        self.bbox: np.ndarray = bbox.astype(np.float64)
        self.score: float = score
        self.class_id: int = class_id
        self.state: TrackState = TrackState.NEW

        self._mean: Optional[np.ndarray] = None
        self._covariance: Optional[np.ndarray] = None

        self.frame_id: int = 0
        self.start_frame: int = 0
        self.tracklet_len: int = 0
        self._is_activated: bool = False

    @staticmethod
    def _bbox_to_xyah(bbox: np.ndarray) -> np.ndarray:
        """Convert ``[x1, y1, x2, y2]`` to ``[cx, cy, a, h]``."""
        cx = (bbox[0] + bbox[2]) / 2.0
        cy = (bbox[1] + bbox[3]) / 2.0
        w = bbox[2] - bbox[0]
        h = bbox[3] - bbox[1]
        a = w / h if h > 0 else 0.0
        return np.array([cx, cy, a, h], dtype=np.float64)

    @staticmethod
    def _xyah_to_bbox(xyah: np.ndarray) -> np.ndarray:
        """Convert ``[cx, cy, a, h]`` to ``[x1, y1, x2, y2]``."""
        cx, cy, a, h = xyah
        w = a * h
        return np.array([
            cx - w / 2.0,
            cy - h / 2.0,
            cx + w / 2.0,
            cy + h / 2.0,
        ], dtype=np.float64)

    def activate(self, frame_id: int) -> None:
        """Activate the track and assign an ID.

        Args:
            frame_id: Current frame number.
        """
        self.track_id = STrack._next_id
        STrack._next_id += 1

        measurement = self._bbox_to_xyah(self.bbox)
        self._mean, self._covariance = self._kf.initiate(measurement)

        self.state = TrackState.TRACKED
        self._is_activated = True
        self.frame_id = frame_id
        self.start_frame = frame_id
        self.tracklet_len = 0

    def re_activate(self, new_bbox: np.ndarray, new_score: float, frame_id: int) -> None:
        """Re-activate a lost track with a new detection.

        Args:
            new_bbox: New detection bbox ``[x1, y1, x2, y2]``.
            new_score: New detection confidence.
            frame_id: Current frame number.
        """
        measurement = self._bbox_to_xyah(new_bbox)
        self._mean, self._covariance = self._kf.update(
            self._mean, self._covariance, measurement,
        )

        self.bbox = new_bbox.astype(np.float64)
        self.score = new_score
        self.state = TrackState.TRACKED
        self._is_activated = True
        self.frame_id = frame_id
        self.tracklet_len = 0

    def predict(self) -> None:
        """Run the Kalman Filter prediction step."""
        if self._mean is None:
            return

        self._mean, self._covariance = self._kf.predict(
            self._mean, self._covariance,
        )

        # Update bbox from predicted state
        self.bbox = self._xyah_to_bbox(self._mean[:4])

    def update(self, new_bbox: np.ndarray, new_score: float, frame_id: int) -> None:
        """Update the track with a matched detection.

        Args:
            new_bbox: Matched detection bbox ``[x1, y1, x2, y2]``.
            new_score: Matched detection confidence.
            frame_id: Current frame number.
        """
        measurement = self._bbox_to_xyah(new_bbox)
        self._mean, self._covariance = self._kf.update(
            self._mean, self._covariance, measurement,
        )

        self.bbox = new_bbox.astype(np.float64)
        self.score = new_score
        self.state = TrackState.TRACKED
        self._is_activated = True
        self.frame_id = frame_id
        self.tracklet_len += 1

    def mark_lost(self) -> None:
        """Mark the track as lost."""
        self.state = TrackState.LOST

    def mark_removed(self) -> None:
        """Mark the track as removed."""
        self.state = TrackState.REMOVED

    @property
    def is_activated(self) -> bool:
        """Return whether the track has been activated."""
        return self._is_activated

    @staticmethod
    def reset_id() -> None:
        """Reset the global track ID counter."""
        STrack._next_id = 1


# ── IoU Distance ─────────────────────────────────────────────────────

def _compute_iou_matrix(
    bboxes_a: np.ndarray,
    bboxes_b: np.ndarray,
) -> np.ndarray:
    """Compute the IoU distance matrix between two sets of bounding boxes.

    Args:
        bboxes_a: Array of shape ``(M, 4)`` in ``[x1, y1, x2, y2]`` format.
        bboxes_b: Array of shape ``(N, 4)`` in ``[x1, y1, x2, y2]`` format.

    Returns:
        np.ndarray: IoU matrix of shape ``(M, N)`` where each element
            is ``1 - IoU`` (i.e. distance, lower is better).
    """
    m = bboxes_a.shape[0]
    n = bboxes_b.shape[0]

    if m == 0 or n == 0:
        return np.empty((m, n), dtype=np.float64)

    # Broadcast computation
    a_x1 = bboxes_a[:, 0].reshape(m, 1)
    a_y1 = bboxes_a[:, 1].reshape(m, 1)
    a_x2 = bboxes_a[:, 2].reshape(m, 1)
    a_y2 = bboxes_a[:, 3].reshape(m, 1)

    b_x1 = bboxes_b[:, 0].reshape(1, n)
    b_y1 = bboxes_b[:, 1].reshape(1, n)
    b_x2 = bboxes_b[:, 2].reshape(1, n)
    b_y2 = bboxes_b[:, 3].reshape(1, n)

    inter_x1 = np.maximum(a_x1, b_x1)
    inter_y1 = np.maximum(a_y1, b_y1)
    inter_x2 = np.minimum(a_x2, b_x2)
    inter_y2 = np.minimum(a_y2, b_y2)

    inter_area = np.maximum(0.0, inter_x2 - inter_x1) * np.maximum(0.0, inter_y2 - inter_y1)

    area_a = (a_x2 - a_x1) * (a_y2 - a_y1)
    area_b = (b_x2 - b_x1) * (b_y2 - b_y1)

    union = area_a + area_b - inter_area
    iou = np.where(union > 0, inter_area / union, 0.0)

    # Return distance (1 - IoU)
    return 1.0 - iou


# ── ByteTracker ──────────────────────────────────────────────────────

class ByteTracker:
    """ByteTrack multi-object tracker.

    Implements the two-stage association strategy from ByteTrack:

    1. **First association**: Match high-confidence detections to existing
       tracks using IoU distance and the Hungarian algorithm.
    2. **Second association**: Match remaining (low-confidence) detections
       to unmatched tracks from the first stage.

    Args:
        high_threshold: Confidence threshold separating high and low
            confidence detections.  Defaults to ``0.6``.
        low_threshold: Minimum confidence to consider any detection.
            Defaults to ``0.1``.
        match_threshold: Maximum IoU distance for a valid match.
            Defaults to ``0.8``.
        max_lost_frames: Number of frames a lost track is kept before
            removal.  Defaults to ``30``.
        min_hits: Minimum consecutive hits before a track is confirmed.
            Defaults to ``3``.
    """

    def __init__(
        self,
        high_threshold: float = 0.6,
        low_threshold: float = 0.1,
        match_threshold: float = 0.8,
        max_lost_frames: int = 30,
        min_hits: int = 3,
    ) -> None:
        self.high_threshold = high_threshold
        self.low_threshold = low_threshold
        self.match_threshold = match_threshold
        self.max_lost_frames = max_lost_frames
        self.min_hits = min_hits

        self.tracked_tracks: list[STrack] = []
        self.lost_tracks: list[STrack] = []
        self.removed_tracks: list[STrack] = []

        self.frame_id: int = 0

        logger.info(
            "byte_tracker.initialised",
            high_threshold=high_threshold,
            low_threshold=low_threshold,
            match_threshold=match_threshold,
            max_lost_frames=max_lost_frames,
        )

    def update(
        self,
        detections: list[tuple[np.ndarray, float, int]],
    ) -> list[STrack]:
        """Update the tracker with a new set of detections.

        Args:
            detections: List of ``(bbox, score, class_id)`` tuples where
                ``bbox`` is a NumPy array ``[x1, y1, x2, y2]``.

        Returns:
            list[STrack]: Currently active tracks (tracked state).
        """
        self.frame_id += 1

        # ── Split detections by confidence ────────────────────────────
        high_dets: list[STrack] = []
        low_dets: list[STrack] = []

        for bbox, score, class_id in detections:
            if score >= self.high_threshold:
                high_dets.append(STrack(np.asarray(bbox), score, class_id))
            elif score >= self.low_threshold:
                low_dets.append(STrack(np.asarray(bbox), score, class_id))

        # ── Predict existing tracks ───────────────────────────────────
        # Pool tracked + lost tracks for matching
        tracked_stracks = [t for t in self.tracked_tracks if t.state == TrackState.TRACKED]
        lost_stracks = list(self.lost_tracks)

        all_active = tracked_stracks + lost_stracks

        for track in all_active:
            track.predict()

        # ── First association: high-confidence dets vs active tracks ──
        iou_dist = self._compute_distance_matrix(all_active, high_dets)
        matched_a, unmatched_tracks_a, unmatched_dets_a = self._linear_assignment(
            iou_dist, all_active, high_dets, self.match_threshold,
        )

        # Update matched tracks
        for track_idx, det_idx in matched_a:
            track = all_active[track_idx]
            det = high_dets[det_idx]
            if track.state == TrackState.TRACKED:
                track.update(det.bbox, det.score, self.frame_id)
            else:
                track.re_activate(det.bbox, det.score, self.frame_id)

        # ── Second association: low-confidence dets vs remaining tracks ─
        remaining_tracks = [all_active[i] for i in unmatched_tracks_a]
        # Only use tracked (not lost) tracks for second association
        r_tracked = [t for t in remaining_tracks if t.state == TrackState.TRACKED]

        iou_dist_2 = self._compute_distance_matrix(r_tracked, low_dets)
        matched_b, unmatched_tracks_b, unmatched_dets_b = self._linear_assignment(
            iou_dist_2, r_tracked, low_dets, 0.5,
        )

        for track_idx, det_idx in matched_b:
            track = r_tracked[track_idx]
            det = low_dets[det_idx]
            track.update(det.bbox, det.score, self.frame_id)

        # Tracks not matched in either round become lost
        for idx in unmatched_tracks_b:
            track = r_tracked[idx]
            if track.state != TrackState.LOST:
                track.mark_lost()

        # Mark remaining lost tracks from first round
        for i in unmatched_tracks_a:
            track = all_active[i]
            if track not in r_tracked and track.state == TrackState.TRACKED:
                track.mark_lost()

        # ── Initialise new tracks from unmatched high-conf detections ─
        for det_idx in unmatched_dets_a:
            det = high_dets[det_idx]
            det.activate(self.frame_id)
            self.tracked_tracks.append(det)

        # ── Update track lists ────────────────────────────────────────
        new_tracked: list[STrack] = []
        new_lost: list[STrack] = []

        for track in self.tracked_tracks:
            if track.state == TrackState.TRACKED:
                new_tracked.append(track)
            elif track.state == TrackState.LOST:
                new_lost.append(track)

        for track in self.lost_tracks:
            if track.state == TrackState.TRACKED:
                new_tracked.append(track)
            elif track.state == TrackState.LOST:
                if self.frame_id - track.frame_id <= self.max_lost_frames:
                    new_lost.append(track)
                else:
                    track.mark_removed()
                    self.removed_tracks.append(track)

        self.tracked_tracks = new_tracked
        self.lost_tracks = new_lost

        # Limit removed track history
        if len(self.removed_tracks) > 1000:
            self.removed_tracks = self.removed_tracks[-500:]

        # Return only confirmed tracks
        output = [
            t for t in self.tracked_tracks
            if t.is_activated and t.tracklet_len >= self.min_hits
        ]

        return output

    @staticmethod
    def _compute_distance_matrix(
        tracks: list[STrack],
        detections: list[STrack],
    ) -> np.ndarray:
        """Compute IoU distance matrix between tracks and detections.

        Args:
            tracks: List of existing tracks.
            detections: List of new detections (as STrack objects).

        Returns:
            np.ndarray: Distance matrix of shape ``(len(tracks), len(detections))``.
        """
        if len(tracks) == 0 or len(detections) == 0:
            return np.empty((len(tracks), len(detections)), dtype=np.float64)

        track_bboxes = np.array([t.bbox for t in tracks], dtype=np.float64)
        det_bboxes = np.array([d.bbox for d in detections], dtype=np.float64)

        return _compute_iou_matrix(track_bboxes, det_bboxes)

    @staticmethod
    def _linear_assignment(
        cost_matrix: np.ndarray,
        tracks: list[STrack],
        detections: list[STrack],
        threshold: float,
    ) -> tuple[list[tuple[int, int]], list[int], list[int]]:
        """Perform optimal assignment using the Hungarian algorithm.

        Args:
            cost_matrix: Distance matrix of shape ``(M, N)``.
            tracks: Track list (rows).
            detections: Detection list (columns).
            threshold: Maximum cost for a valid assignment.

        Returns:
            tuple: A 3-tuple of:
                - **matches** -- List of ``(track_idx, det_idx)`` pairs.
                - **unmatched_tracks** -- Indices of unmatched tracks.
                - **unmatched_dets** -- Indices of unmatched detections.
        """
        num_tracks = len(tracks)
        num_dets = len(detections)

        if num_tracks == 0:
            return (
                [],
                [],
                list(range(num_dets)),
            )

        if num_dets == 0:
            return (
                [],
                list(range(num_tracks)),
                [],
            )

        # Solve the assignment problem
        row_indices, col_indices = linear_sum_assignment(cost_matrix)

        matches: list[tuple[int, int]] = []
        unmatched_tracks = set(range(num_tracks))
        unmatched_dets = set(range(num_dets))

        for row, col in zip(row_indices, col_indices):
            if cost_matrix[row, col] > threshold:
                continue
            matches.append((int(row), int(col)))
            unmatched_tracks.discard(row)
            unmatched_dets.discard(col)

        return (
            matches,
            sorted(unmatched_tracks),
            sorted(unmatched_dets),
        )

    def reset(self) -> None:
        """Reset the tracker state and ID counter."""
        self.tracked_tracks.clear()
        self.lost_tracks.clear()
        self.removed_tracks.clear()
        self.frame_id = 0
        STrack.reset_id()
        logger.info("byte_tracker.reset")
