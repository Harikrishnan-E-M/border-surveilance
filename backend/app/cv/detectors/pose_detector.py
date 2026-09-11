"""
YOLOv8-Pose Human Pose Estimator for VisionAI.

Detects human poses using a YOLOv8-Pose ONNX model that produces
bounding boxes and 17-keypoint COCO skeleton estimates in a single
forward pass.  Includes heuristic analysers for fall detection and
fighting/aggression detection based on keypoint geometry.

The model outputs a tensor of shape ``(1, 56, 8400)`` where
56 = 4 (box) + 1 (confidence) + 51 (17 keypoints * 3: x, y, score).

Usage::

    from app.cv.detectors.pose_detector import PoseDetector

    detector = PoseDetector(session)
    poses = detector.detect(frame)
    for pose in poses:
        print(f"Person at {pose.bbox}, keypoints shape={pose.keypoints.shape}")

    falls = detector.detect_fall(poses)
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
    letterbox,
    nms,
    scale_boxes,
    scale_keypoints,
    xywh2xyxy,
)

logger = structlog.stdlib.get_logger(__name__)

# ── COCO 17-keypoint Names ───────────────────────────────────────────
KEYPOINT_NAMES: list[str] = [
    "nose",
    "left_eye",
    "right_eye",
    "left_ear",
    "right_ear",
    "left_shoulder",
    "right_shoulder",
    "left_elbow",
    "right_elbow",
    "left_wrist",
    "right_wrist",
    "left_hip",
    "right_hip",
    "left_knee",
    "right_knee",
    "left_ankle",
    "right_ankle",
]

# Skeleton connections for drawing (pairs of keypoint indices)
SKELETON_CONNECTIONS: list[tuple[int, int]] = [
    (0, 1), (0, 2), (1, 3), (2, 4),       # Head
    (5, 6),                                  # Shoulders
    (5, 7), (7, 9),                          # Left arm
    (6, 8), (8, 10),                         # Right arm
    (5, 11), (6, 12),                        # Torso
    (11, 12),                                # Hips
    (11, 13), (13, 15),                      # Left leg
    (12, 14), (14, 16),                      # Right leg
]

# Keypoint index constants for readability
_NOSE = 0
_LEFT_EYE = 1
_RIGHT_EYE = 2
_LEFT_SHOULDER = 5
_RIGHT_SHOULDER = 6
_LEFT_ELBOW = 7
_RIGHT_ELBOW = 8
_LEFT_WRIST = 9
_RIGHT_WRIST = 10
_LEFT_HIP = 11
_RIGHT_HIP = 12
_LEFT_KNEE = 13
_RIGHT_KNEE = 14
_LEFT_ANKLE = 15
_RIGHT_ANKLE = 16


@dataclass
class PoseDetection:
    """A single human pose detection result.

    Attributes:
        bbox: Bounding box as ``(x1, y1, x2, y2)`` in pixel coordinates.
        confidence: Overall detection confidence in ``[0, 1]``.
        keypoints: Keypoint array of shape ``(17, 3)`` where each row
            is ``[x, y, visibility_score]``.  Keypoint order follows
            the COCO convention defined by ``KEYPOINT_NAMES``.
        track_id: Optional tracking identifier.
    """

    bbox: tuple[float, float, float, float]
    confidence: float
    keypoints: np.ndarray  # shape (17, 3)
    track_id: Optional[int] = field(default=None)

    @property
    def center(self) -> tuple[float, float]:
        """Return the bounding box centre point."""
        return (
            (self.bbox[0] + self.bbox[2]) / 2.0,
            (self.bbox[1] + self.bbox[3]) / 2.0,
        )

    @property
    def height(self) -> float:
        """Return the bounding box height."""
        return max(0.0, self.bbox[3] - self.bbox[1])

    @property
    def width(self) -> float:
        """Return the bounding box width."""
        return max(0.0, self.bbox[2] - self.bbox[0])

    @property
    def aspect_ratio(self) -> float:
        """Return the bounding box aspect ratio (width / height)."""
        h = self.height
        return self.width / h if h > 0 else 0.0

    def get_keypoint(self, name: str) -> Optional[tuple[float, float, float]]:
        """Get a keypoint by name.

        Args:
            name: Keypoint name (e.g. ``"left_shoulder"``).

        Returns:
            Optional[tuple]: ``(x, y, score)`` or ``None`` if name not found.
        """
        try:
            idx = KEYPOINT_NAMES.index(name)
            kp = self.keypoints[idx]
            return (float(kp[0]), float(kp[1]), float(kp[2]))
        except (ValueError, IndexError):
            return None

    def to_dict(self) -> dict:
        """Serialise to a plain dictionary."""
        return {
            "bbox": list(self.bbox),
            "confidence": round(self.confidence, 4),
            "keypoints": self.keypoints.tolist(),
            "track_id": self.track_id,
        }


class PoseDetector:
    """YOLOv8-Pose based human pose estimator.

    Args:
        session: Loaded ONNX Runtime session for a YOLOv8-Pose model.
        confidence_threshold: Minimum confidence to retain a detection.
        nms_threshold: IoU threshold for NMS.
        input_size: Model input resolution as ``(width, height)``.
        keypoint_threshold: Minimum keypoint visibility score to
            consider a keypoint as detected.
    """

    def __init__(
        self,
        session: ort.InferenceSession,
        confidence_threshold: float = 0.5,
        nms_threshold: float = 0.45,
        input_size: tuple[int, int] = (640, 640),
        keypoint_threshold: float = 0.5,
    ) -> None:
        self.engine = ONNXInferenceEngine(session)
        self.confidence_threshold = confidence_threshold
        self.nms_threshold = nms_threshold
        self.input_size = input_size
        self.keypoint_threshold = keypoint_threshold

        logger.info(
            "pose_detector.initialised",
            input_size=input_size,
            confidence_threshold=confidence_threshold,
        )

    def detect(self, image: np.ndarray) -> list[PoseDetection]:
        """Detect human poses in a single image.

        Args:
            image: Input BGR image as a NumPy array (H, W, C).

        Returns:
            list[PoseDetection]: Detected poses sorted by confidence.
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
    ) -> list[PoseDetection]:
        """Post-process raw YOLOv8-Pose output.

        YOLOv8-Pose output shape: ``(1, 56, 8400)`` where
        56 = 4 (xywh) + 1 (obj_conf) + 51 (17 * 3 keypoints).

        Args:
            output: Raw model output tensor.
            orig_shape: Original image ``(height, width)``.
            ratio: Letterbox scale ratio.
            pad: Letterbox padding ``(dw, dh)``.

        Returns:
            list[PoseDetection]: Post-processed pose detections.
        """
        predictions = output[0]

        # Transpose if needed: (56, 8400) -> (8400, 56)
        if predictions.shape[0] < predictions.shape[1]:
            predictions = predictions.T

        num_predictions = predictions.shape[0]

        # Split into components
        boxes_xywh = predictions[:, :4]         # (N, 4)
        confidences = predictions[:, 4]          # (N,)
        keypoints_raw = predictions[:, 5:]       # (N, 51)

        # Confidence filter
        mask = confidences >= self.confidence_threshold
        if not np.any(mask):
            return []

        boxes_xywh = boxes_xywh[mask]
        confidences = confidences[mask]
        keypoints_raw = keypoints_raw[mask]

        # Convert boxes to xyxy
        boxes_xyxy = xywh2xyxy(boxes_xywh)

        # NMS
        keep = nms(boxes_xyxy, confidences, self.nms_threshold)
        if len(keep) == 0:
            return []

        boxes_xyxy = boxes_xyxy[keep]
        confidences = confidences[keep]
        keypoints_raw = keypoints_raw[keep]

        # Reshape keypoints: (N, 51) -> (N, 17, 3)
        num_kept = len(keep)
        keypoints = keypoints_raw.reshape(num_kept, 17, 3)

        # Scale boxes back to original image
        letterbox_shape = (self.input_size[1], self.input_size[0])
        boxes_xyxy = scale_boxes(
            boxes_xyxy, letterbox_shape, orig_shape, ratio, pad,
        )

        # Scale keypoint coordinates
        keypoints = scale_keypoints(keypoints, ratio, pad)

        # Clip keypoints to image boundaries
        orig_h, orig_w = orig_shape
        keypoints[:, :, 0] = np.clip(keypoints[:, :, 0], 0, orig_w)
        keypoints[:, :, 1] = np.clip(keypoints[:, :, 1], 0, orig_h)

        # Build PoseDetection objects
        detections: list[PoseDetection] = []
        for i in range(num_kept):
            det = PoseDetection(
                bbox=(
                    float(boxes_xyxy[i, 0]),
                    float(boxes_xyxy[i, 1]),
                    float(boxes_xyxy[i, 2]),
                    float(boxes_xyxy[i, 3]),
                ),
                confidence=float(confidences[i]),
                keypoints=keypoints[i].copy(),
            )
            detections.append(det)

        detections.sort(key=lambda d: d.confidence, reverse=True)
        return detections

    def detect_fall(
        self,
        poses: list[PoseDetection],
        aspect_ratio_threshold: float = 1.2,
        hip_ankle_threshold: float = 0.3,
    ) -> list[PoseDetection]:
        """Detect potential fall events from pose detections.

        A fall is indicated when:
        1. The bounding box aspect ratio (width/height) exceeds the
           threshold (person is more horizontal than vertical).
        2. The vertical distance between hips and ankles is small
           relative to the bounding box height (person is on the ground).

        Args:
            poses: List of pose detections from the current frame.
            aspect_ratio_threshold: Minimum width/height ratio to
                consider a potential fall.
            hip_ankle_threshold: Maximum hip-to-ankle vertical distance
                relative to bbox height to confirm a fall.

        Returns:
            list[PoseDetection]: Subset of poses classified as falls.
        """
        fall_poses: list[PoseDetection] = []

        for pose in poses:
            bbox_h = pose.height
            bbox_w = pose.width
            if bbox_h <= 0:
                continue

            ar = bbox_w / bbox_h

            # Check aspect ratio (wider than tall)
            if ar < aspect_ratio_threshold:
                continue

            # Check hip-to-ankle vertical distance
            kp = pose.keypoints
            left_hip = kp[_LEFT_HIP]
            right_hip = kp[_RIGHT_HIP]
            left_ankle = kp[_LEFT_ANKLE]
            right_ankle = kp[_RIGHT_ANKLE]

            # Use keypoints only if visible
            visible_hips = []
            visible_ankles = []

            if left_hip[2] > self.keypoint_threshold:
                visible_hips.append(left_hip[1])
            if right_hip[2] > self.keypoint_threshold:
                visible_hips.append(right_hip[1])
            if left_ankle[2] > self.keypoint_threshold:
                visible_ankles.append(left_ankle[1])
            if right_ankle[2] > self.keypoint_threshold:
                visible_ankles.append(right_ankle[1])

            if visible_hips and visible_ankles:
                avg_hip_y = np.mean(visible_hips)
                avg_ankle_y = np.mean(visible_ankles)
                vertical_dist = abs(avg_ankle_y - avg_hip_y)
                relative_dist = vertical_dist / bbox_h

                if relative_dist < hip_ankle_threshold:
                    fall_poses.append(pose)
            elif ar > aspect_ratio_threshold * 1.5:
                # High aspect ratio with no visible lower body
                fall_poses.append(pose)

        return fall_poses

    def detect_fighting(
        self,
        poses: list[PoseDetection],
        proximity_threshold: float = 100.0,
        arm_extension_threshold: float = 0.7,
    ) -> list[tuple[PoseDetection, PoseDetection]]:
        """Detect potential fighting/aggression between pairs of persons.

        Fighting is indicated when:
        1. Two persons are in close proximity (centres are near).
        2. At least one person has extended arms (wrist far from shoulder).
        3. The extended arm reaches into the other person's bounding box.

        Args:
            poses: List of pose detections from the current frame.
            proximity_threshold: Maximum distance between person centres
                to consider a potential interaction (in pixels).
            arm_extension_threshold: Minimum wrist-to-shoulder distance
                relative to torso length to consider an arm extended.

        Returns:
            list[tuple[PoseDetection, PoseDetection]]: Pairs of poses
                classified as fighting.
        """
        fighting_pairs: list[tuple[PoseDetection, PoseDetection]] = []

        if len(poses) < 2:
            return fighting_pairs

        for i in range(len(poses)):
            for j in range(i + 1, len(poses)):
                pose_a = poses[i]
                pose_b = poses[j]

                # Check proximity
                cx_a, cy_a = pose_a.center
                cx_b, cy_b = pose_b.center
                distance = np.sqrt((cx_a - cx_b) ** 2 + (cy_a - cy_b) ** 2)

                if distance > proximity_threshold:
                    continue

                # Check arm extension for both persons
                a_aggressive = self._check_arm_extension(
                    pose_a, pose_b, arm_extension_threshold,
                )
                b_aggressive = self._check_arm_extension(
                    pose_b, pose_a, arm_extension_threshold,
                )

                if a_aggressive or b_aggressive:
                    fighting_pairs.append((pose_a, pose_b))

        return fighting_pairs

    def _check_arm_extension(
        self,
        aggressor: PoseDetection,
        target: PoseDetection,
        threshold: float,
    ) -> bool:
        """Check if a person's arm is extended towards another person.

        Args:
            aggressor: The person whose arms are checked.
            target: The other person.
            threshold: Arm extension threshold.

        Returns:
            bool: True if arm extension towards target is detected.
        """
        kp = aggressor.keypoints
        thresh = self.keypoint_threshold

        # Check both arms
        for shoulder_idx, wrist_idx in [
            (_LEFT_SHOULDER, _LEFT_WRIST),
            (_RIGHT_SHOULDER, _RIGHT_WRIST),
        ]:
            shoulder = kp[shoulder_idx]
            wrist = kp[wrist_idx]

            if shoulder[2] < thresh or wrist[2] < thresh:
                continue

            # Compute arm extension distance
            arm_length = np.sqrt(
                (wrist[0] - shoulder[0]) ** 2 + (wrist[1] - shoulder[1]) ** 2
            )

            # Estimate torso length from shoulder to hip
            left_hip = kp[_LEFT_HIP]
            right_hip = kp[_RIGHT_HIP]

            if left_hip[2] > thresh:
                torso_length = abs(shoulder[1] - left_hip[1])
            elif right_hip[2] > thresh:
                torso_length = abs(shoulder[1] - right_hip[1])
            else:
                torso_length = aggressor.height * 0.4

            if torso_length <= 0:
                continue

            relative_extension = arm_length / torso_length
            if relative_extension < threshold:
                continue

            # Check if wrist is within or near the target's bbox
            tx1, ty1, tx2, ty2 = target.bbox
            margin = (tx2 - tx1) * 0.3
            if (tx1 - margin <= wrist[0] <= tx2 + margin
                    and ty1 - margin <= wrist[1] <= ty2 + margin):
                return True

        return False

    def draw_poses(
        self,
        image: np.ndarray,
        poses: list[PoseDetection],
        draw_bbox: bool = True,
        draw_skeleton: bool = True,
    ) -> np.ndarray:
        """Draw detected poses on an image.

        Args:
            image: Input BGR image (a copy is made).
            poses: List of pose detections.
            draw_bbox: If ``True``, draw bounding boxes.
            draw_skeleton: If ``True``, draw skeleton connections.

        Returns:
            np.ndarray: Image with drawn poses.
        """
        output = image.copy()
        colors = [
            (255, 0, 0), (0, 255, 0), (0, 0, 255),
            (255, 255, 0), (255, 0, 255), (0, 255, 255),
        ]

        for idx, pose in enumerate(poses):
            color = colors[idx % len(colors)]

            if draw_bbox:
                x1, y1, x2, y2 = [int(c) for c in pose.bbox]
                cv2.rectangle(output, (x1, y1), (x2, y2), color, 2)

            if draw_skeleton:
                kp = pose.keypoints
                # Draw keypoints
                for k in range(17):
                    if kp[k, 2] > self.keypoint_threshold:
                        cx, cy = int(kp[k, 0]), int(kp[k, 1])
                        cv2.circle(output, (cx, cy), 4, color, -1)

                # Draw skeleton connections
                for k1, k2 in SKELETON_CONNECTIONS:
                    if kp[k1, 2] > self.keypoint_threshold and kp[k2, 2] > self.keypoint_threshold:
                        pt1 = (int(kp[k1, 0]), int(kp[k1, 1]))
                        pt2 = (int(kp[k2, 0]), int(kp[k2, 1]))
                        cv2.line(output, pt1, pt2, color, 2)

        return output
