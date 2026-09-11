"""
Video Processing Pipeline for VisionAI.

Orchestrates all computer vision modules (detectors, recognizers,
trackers, analyzers) into a unified per-frame processing pipeline.
Each camera gets its own ``VideoPipeline`` instance configured with
the appropriate set of enabled features.

The pipeline processes frames sequentially and produces a structured
``FrameResult`` containing all detections, tracks, recognition results,
and generated events/alerts.

Usage::

    from app.cv.pipeline import VideoPipeline

    config = {
        "object_detection": True,
        "face_recognition": True,
        "vehicle_recognition": True,
        "ppe_detection": False,
        "fire_detection": True,
        "pose_estimation": False,
        "plate_recognition": True,
        "zones": [...],
        "tripwires": [...],
    }

    pipeline = VideoPipeline(camera_id="cam_001", config=config)
    pipeline.start()

    result = pipeline.process_frame(frame, timestamp=1700000000.0)
    print(f"Frame {result.frame_id}: {len(result.detections)} detections")

    pipeline.stop()
"""

from __future__ import annotations

import time
import threading
from dataclasses import dataclass, field
from typing import Any, Optional

import cv2
import numpy as np
import onnxruntime as ort
import structlog

from app.cv.model_registry import ModelRegistry
from app.cv.detectors.object_detector import Detection, ObjectDetector
from app.cv.detectors.face_detector import FaceDetection, FaceDetector
from app.cv.detectors.ppe_detector import PPEDetector, ComplianceResult
from app.cv.detectors.plate_detector import PlateDetection, PlateDetector
from app.cv.detectors.fire_detector import FireDetection, FireDetector
from app.cv.detectors.pose_detector import PoseDetection, PoseDetector
from app.cv.recognizers.face_recognizer import FaceRecognizer
from app.cv.recognizers.plate_ocr import PlateOCR
from app.cv.recognizers.emotion_classifier import EmotionClassifier
from app.cv.recognizers.vehicle_classifier import VehicleClassifier
from app.cv.trackers.byte_tracker import ByteTracker, STrack
from app.cv.analyzers.zone_analyzer import ZoneAnalyzer, ZoneEvent
from app.cv.analyzers.behavior_analyzer import BehaviorAnalyzer, BehaviorEvent
from app.cv.analyzers.tamper_detector import TamperDetector
from app.cv.analyzers.heatmap_generator import HeatmapGenerator
from app.cv.analyzers.crowd_analyzer import CrowdAnalyzer, CrowdAlert

logger = structlog.stdlib.get_logger(__name__)


# ── Frame Result ─────────────────────────────────────────────────────

@dataclass
class FaceResult:
    """Face recognition result for a single detected face.

    Attributes:
        detection: Face detection with bbox and landmarks.
        embedding: Face embedding vector (512-dim).
        match_id: Matched person ID from the gallery, or ``None``.
        match_name: Matched person name, or ``None``.
        match_similarity: Similarity score with the best match.
        emotion: Predicted emotion label.
        emotion_confidence: Emotion prediction confidence.
    """

    detection: FaceDetection
    embedding: Optional[np.ndarray] = field(default=None, repr=False)
    match_id: Optional[str] = None
    match_name: Optional[str] = None
    match_similarity: float = 0.0
    emotion: Optional[str] = None
    emotion_confidence: float = 0.0

    def to_dict(self) -> dict:
        """Serialise to a dictionary (excluding embedding array)."""
        return {
            "bbox": list(self.detection.bbox),
            "confidence": round(self.detection.confidence, 4),
            "match_id": self.match_id,
            "match_name": self.match_name,
            "match_similarity": round(self.match_similarity, 4),
            "emotion": self.emotion,
            "emotion_confidence": round(self.emotion_confidence, 4),
        }


@dataclass
class VehicleResult:
    """Vehicle recognition result for a single detected vehicle.

    Attributes:
        detection: Object detection for the vehicle.
        color: Classified vehicle colour.
        vehicle_type: Classified vehicle type.
        color_confidence: Colour classification confidence.
        type_confidence: Type classification confidence.
        plate_text: Recognised license plate text, or ``None``.
        plate_confidence: Plate OCR confidence.
        plate_bbox: Plate bounding box, or ``None``.
    """

    detection: Detection
    color: str = "unknown"
    vehicle_type: str = "unknown"
    color_confidence: float = 0.0
    type_confidence: float = 0.0
    plate_text: Optional[str] = None
    plate_confidence: float = 0.0
    plate_bbox: Optional[tuple[float, float, float, float]] = None

    def to_dict(self) -> dict:
        """Serialise to a dictionary."""
        return {
            "bbox": list(self.detection.bbox),
            "confidence": round(self.detection.confidence, 4),
            "track_id": self.detection.track_id,
            "color": self.color,
            "vehicle_type": self.vehicle_type,
            "color_confidence": round(self.color_confidence, 4),
            "type_confidence": round(self.type_confidence, 4),
            "plate_text": self.plate_text,
            "plate_confidence": round(self.plate_confidence, 4),
            "plate_bbox": list(self.plate_bbox) if self.plate_bbox else None,
        }


@dataclass
class FrameResult:
    """Complete result from processing a single video frame.

    Attributes:
        frame_id: Sequential frame number.
        timestamp: Frame timestamp in seconds (Unix epoch or relative).
        processing_time_ms: Time taken to process this frame.
        detections: All object detections.
        tracks: Active tracker output.
        face_results: Face detection and recognition results.
        vehicle_results: Vehicle classification and plate results.
        pose_detections: Pose estimation results.
        ppe_compliance: PPE compliance results per person.
        fire_detections: Fire/smoke detections.
        zone_events: Zone intrusion and crossing events.
        behavior_events: Behavioural anomaly events.
        crowd_alerts: Crowd threshold alerts.
        tamper_result: Camera tamper analysis result.
        alerts: Aggregated alert list for upstream notification.
    """

    frame_id: int = 0
    timestamp: float = 0.0
    processing_time_ms: float = 0.0
    detections: list[Detection] = field(default_factory=list)
    tracks: list[STrack] = field(default_factory=list)
    face_results: list[FaceResult] = field(default_factory=list)
    vehicle_results: list[VehicleResult] = field(default_factory=list)
    pose_detections: list[PoseDetection] = field(default_factory=list)
    ppe_compliance: list[ComplianceResult] = field(default_factory=list)
    fire_detections: list[FireDetection] = field(default_factory=list)
    zone_events: list[ZoneEvent] = field(default_factory=list)
    behavior_events: list[BehaviorEvent] = field(default_factory=list)
    crowd_alerts: list[CrowdAlert] = field(default_factory=list)
    tamper_result: Optional[dict] = None
    alerts: list[dict] = field(default_factory=list)
    events: list[dict] = field(default_factory=list)

    def to_dict(self) -> dict:
        """Serialise the full frame result to a dictionary."""
        return {
            "frame_id": self.frame_id,
            "timestamp": self.timestamp,
            "processing_time_ms": round(self.processing_time_ms, 2),
            "detections": [d.to_dict() for d in self.detections],
            "tracks": [
                {
                    "track_id": t.track_id,
                    "bbox": t.bbox.tolist() if isinstance(t.bbox, np.ndarray) else list(t.bbox),
                    "score": round(t.score, 4),
                    "class_id": t.class_id,
                }
                for t in self.tracks
            ],
            "face_results": [f.to_dict() for f in self.face_results],
            "vehicle_results": [v.to_dict() for v in self.vehicle_results],
            "pose_detections": [p.to_dict() for p in self.pose_detections],
            "ppe_compliance": [c.to_dict() for c in self.ppe_compliance],
            "fire_detections": [f.to_dict() for f in self.fire_detections],
            "zone_events": [e.to_dict() for e in self.zone_events],
            "behavior_events": [e.to_dict() for e in self.behavior_events],
            "crowd_alerts": [a.to_dict() for a in self.crowd_alerts],
            "tamper_result": self.tamper_result,
            "alerts": self.alerts,
            "events": self.events,
        }


# ── Video Pipeline ───────────────────────────────────────────────────

class VideoPipeline:
    """Main video processing pipeline for a single camera.

    Orchestrates all detectors, recognizers, trackers, and analyzers
    to process each frame and produce a comprehensive ``FrameResult``.

    Args:
        camera_id: Unique camera identifier.
        config: Pipeline configuration dictionary.  Keys:
            - ``object_detection`` (bool): Enable YOLOv8 detection.
            - ``face_recognition`` (bool): Enable face detection + recognition.
            - ``vehicle_recognition`` (bool): Enable vehicle classification.
            - ``ppe_detection`` (bool): Enable PPE compliance checking.
            - ``fire_detection`` (bool): Enable fire/smoke detection.
            - ``pose_estimation`` (bool): Enable pose estimation.
            - ``plate_recognition`` (bool): Enable plate detection + OCR.
            - ``emotion_detection`` (bool): Enable emotion classification.
            - ``tamper_detection`` (bool): Enable tamper detection.
            - ``heatmap`` (bool): Enable heatmap accumulation.
            - ``crowd_analysis`` (bool): Enable crowd counting/alerting.
            - ``confidence_threshold`` (float): Detection confidence.
            - ``nms_threshold`` (float): NMS IoU threshold.
            - ``zones`` (list): Zone definitions.
            - ``tripwires`` (list): Tripwire definitions.
            - ``required_ppe`` (set): Required PPE items.
            - ``max_occupancy`` (dict): Per-zone max occupancy.
            - ``frame_width`` (int): Frame width for heatmap.
            - ``frame_height`` (int): Frame height for heatmap.
            - ``gallery_embeddings`` (np.ndarray): Face gallery.
            - ``gallery_ids`` (list): Face gallery IDs.
            - ``gallery_names`` (list): Face gallery names.
        registry: Optional ``ModelRegistry`` instance.  If ``None``,
            the singleton is used.
    """

    def __init__(
        self,
        camera_id: str,
        config: dict[str, Any],
        registry: ModelRegistry | None = None,
    ) -> None:
        self.camera_id = camera_id
        self.config = config
        self.registry = registry or ModelRegistry()

        self._frame_counter: int = 0
        self._running: bool = False
        self._lock = threading.Lock()

        # ── Feature flags ─────────────────────────────────────────────
        self._enable_object_detection = config.get("object_detection", True)
        self._enable_face_recognition = config.get("face_recognition", False)
        self._enable_vehicle_recognition = config.get("vehicle_recognition", False)
        self._enable_ppe_detection = config.get("ppe_detection", False)
        self._enable_fire_detection = config.get("fire_detection", False)
        self._enable_pose_estimation = config.get("pose_estimation", False)
        self._enable_plate_recognition = config.get("plate_recognition", False)
        self._enable_emotion_detection = config.get("emotion_detection", False)
        self._enable_tamper_detection = config.get("tamper_detection", False)
        self._enable_heatmap = config.get("heatmap", False)
        self._enable_crowd_analysis = config.get("crowd_analysis", False)

        # Configuration values
        self._conf_threshold = config.get("confidence_threshold", 0.5)
        self._nms_threshold = config.get("nms_threshold", 0.45)
        self._required_ppe = config.get("required_ppe", {"helmet", "vest"})

        # ── Module instances (initialised lazily in start()) ──────────
        self._object_detector: Optional[ObjectDetector] = None
        self._face_detector: Optional[FaceDetector] = None
        self._face_recognizer: Optional[FaceRecognizer] = None
        self._emotion_classifier: Optional[EmotionClassifier] = None
        self._ppe_detector: Optional[PPEDetector] = None
        self._plate_detector: Optional[PlateDetector] = None
        self._plate_ocr: Optional[PlateOCR] = None
        self._fire_detector: Optional[FireDetector] = None
        self._pose_detector: Optional[PoseDetector] = None
        self._vehicle_classifier: Optional[VehicleClassifier] = None

        self._tracker: Optional[ByteTracker] = None
        self._zone_analyzer: Optional[ZoneAnalyzer] = None
        self._behavior_analyzer: Optional[BehaviorAnalyzer] = None
        self._tamper_detector: Optional[TamperDetector] = None
        self._heatmap_generator: Optional[HeatmapGenerator] = None
        self._crowd_analyzer: Optional[CrowdAnalyzer] = None

        # Face gallery
        self._gallery_embeddings: Optional[np.ndarray] = config.get("gallery_embeddings")
        self._gallery_ids: Optional[list[str]] = config.get("gallery_ids")
        self._gallery_names: Optional[list[str]] = config.get("gallery_names")

        logger.info(
            "pipeline.created",
            camera_id=camera_id,
            features={
                "object_detection": self._enable_object_detection,
                "face_recognition": self._enable_face_recognition,
                "vehicle_recognition": self._enable_vehicle_recognition,
                "ppe_detection": self._enable_ppe_detection,
                "fire_detection": self._enable_fire_detection,
                "pose_estimation": self._enable_pose_estimation,
                "plate_recognition": self._enable_plate_recognition,
            },
        )

    def start(self) -> None:
        """Initialise all enabled modules and start the pipeline.

        Loads required models from the registry and instantiates
        detector/recognizer/analyzer objects.
        """
        with self._lock:
            if self._running:
                logger.warning("pipeline.already_running", camera_id=self.camera_id)
                return

            logger.info("pipeline.starting", camera_id=self.camera_id)

            # ── Initialise tracker ────────────────────────────────────
            self._tracker = ByteTracker(
                high_threshold=self._conf_threshold,
                low_threshold=max(0.1, self._conf_threshold - 0.3),
            )

            # ── Initialise detectors ──────────────────────────────────
            if self._enable_object_detection:
                try:
                    session = self.registry.get_model("yolov8n")
                    self._object_detector = ObjectDetector(
                        session,
                        confidence_threshold=self._conf_threshold,
                        nms_threshold=self._nms_threshold,
                    )
                except KeyError:
                    logger.warning("pipeline.model_not_loaded", model="yolov8n")

            if self._enable_face_recognition:
                try:
                    session = self.registry.get_model("scrfd_2.5g")
                    self._face_detector = FaceDetector(session)
                except KeyError:
                    logger.warning("pipeline.model_not_loaded", model="scrfd_2.5g")

                try:
                    session = self.registry.get_model("arcface_r100")
                    self._face_recognizer = FaceRecognizer(session)
                except KeyError:
                    logger.warning("pipeline.model_not_loaded", model="arcface_r100")

            if self._enable_emotion_detection:
                try:
                    session = self.registry.get_model("emotion")
                    self._emotion_classifier = EmotionClassifier(session)
                except KeyError:
                    logger.warning("pipeline.model_not_loaded", model="emotion")

            if self._enable_ppe_detection:
                try:
                    session = self.registry.get_model("ppe_yolov8s")
                    self._ppe_detector = PPEDetector(session)
                except KeyError:
                    logger.warning("pipeline.model_not_loaded", model="ppe_yolov8s")

            if self._enable_plate_recognition:
                try:
                    session = self.registry.get_model("plate_detector")
                    self._plate_detector = PlateDetector(session)
                except KeyError:
                    logger.warning("pipeline.model_not_loaded", model="plate_detector")

                try:
                    session = self.registry.get_model("plate_ocr")
                    self._plate_ocr = PlateOCR(session)
                except KeyError:
                    logger.warning("pipeline.model_not_loaded", model="plate_ocr")

            if self._enable_fire_detection:
                try:
                    session = self.registry.get_model("fire_detector")
                    self._fire_detector = FireDetector(session)
                except KeyError:
                    logger.warning("pipeline.model_not_loaded", model="fire_detector")

            if self._enable_pose_estimation:
                try:
                    session = self.registry.get_model("yolov8n_pose")
                    self._pose_detector = PoseDetector(session)
                except KeyError:
                    logger.warning("pipeline.model_not_loaded", model="yolov8n_pose")

            if self._enable_vehicle_recognition:
                try:
                    session = self.registry.get_model("vehicle_classifier")
                    self._vehicle_classifier = VehicleClassifier(session)
                except KeyError:
                    logger.warning("pipeline.model_not_loaded", model="vehicle_classifier")

            # ── Initialise analyzers ──────────────────────────────────
            self._zone_analyzer = ZoneAnalyzer()
            self._behavior_analyzer = BehaviorAnalyzer()

            if self._enable_tamper_detection:
                self._tamper_detector = TamperDetector()

            if self._enable_heatmap:
                frame_w = self.config.get("frame_width", 1920)
                frame_h = self.config.get("frame_height", 1080)
                self._heatmap_generator = HeatmapGenerator(
                    width=frame_w, height=frame_h,
                )

            if self._enable_crowd_analysis:
                self._crowd_analyzer = CrowdAnalyzer()

            # ── Load zone/tripwire definitions ────────────────────────
            zones = self.config.get("zones", [])
            for zone_def in zones:
                self._zone_analyzer.add_zone(
                    zone_id=zone_def.get("zone_id", ""),
                    polygon=zone_def.get("polygon", []),
                    name=zone_def.get("name", ""),
                    zone_type=zone_def.get("zone_type", "inclusion"),
                    max_occupancy=zone_def.get("max_occupancy"),
                )

            tripwires = self.config.get("tripwires", [])
            for wire_def in tripwires:
                self._zone_analyzer.add_tripwire(
                    wire_id=wire_def.get("wire_id", ""),
                    start=tuple(wire_def.get("start", (0, 0))),
                    end=tuple(wire_def.get("end", (0, 0))),
                    name=wire_def.get("name", ""),
                    direction=wire_def.get("direction", "both"),
                )

            self._running = True
            self._frame_counter = 0

            logger.info("pipeline.started", camera_id=self.camera_id)

    def stop(self) -> None:
        """Stop the pipeline and release resources."""
        with self._lock:
            if not self._running:
                return

            self._running = False

            # Reset tracker
            if self._tracker:
                self._tracker.reset()

            logger.info(
                "pipeline.stopped",
                camera_id=self.camera_id,
                frames_processed=self._frame_counter,
            )

    def process_frame(
        self,
        frame: np.ndarray,
        timestamp: float | None = None,
    ) -> FrameResult:
        """Process a single video frame through the pipeline.

        Runs all enabled detectors, recognizers, trackers, and
        analyzers in sequence.

        Args:
            frame: BGR image as a NumPy array (H, W, C).
            timestamp: Frame timestamp in seconds.  If ``None``,
                uses ``time.time()``.

        Returns:
            FrameResult: Complete processing result for this frame.
        """
        if timestamp is None:
            timestamp = time.time()

        start_time = time.perf_counter()

        self._frame_counter += 1
        result = FrameResult(
            frame_id=self._frame_counter,
            timestamp=timestamp,
        )

        if frame is None or frame.size == 0:
            return result

        # ── 1. Tamper Detection ───────────────────────────────────────
        if self._tamper_detector is not None:
            tamper = self._tamper_detector.analyze(frame)
            result.tamper_result = tamper
            if tamper.get("tampered"):
                result.alerts.append({
                    "type": "camera_tamper",
                    "camera_id": self.camera_id,
                    "reasons": tamper.get("reasons", []),
                    "timestamp": timestamp,
                })
                # If tampered, skip further processing
                if tamper.get("is_black") or tamper.get("is_white"):
                    result.processing_time_ms = (time.perf_counter() - start_time) * 1000
                    return result

            # Set reference on first frame
            if self._frame_counter == 1:
                self._tamper_detector.set_reference_frame(frame)

        # ── 2. Object Detection ───────────────────────────────────────
        detections: list[Detection] = []
        if self._object_detector is not None:
            detections = self._object_detector.detect(frame)
            result.detections = detections

        # ── 3. Tracking ───────────────────────────────────────────────
        if self._tracker is not None and detections:
            tracker_input = [
                (np.array(d.bbox), d.confidence, d.class_id)
                for d in detections
            ]
            tracks = self._tracker.update(tracker_input)
            result.tracks = tracks

            # Assign track IDs back to detections
            self._assign_track_ids(detections, tracks)

        # ── 4. Face Detection & Recognition ───────────────────────────
        if self._face_detector is not None:
            face_detections = self._face_detector.detect(frame)

            for face_det in face_detections:
                face_result = FaceResult(detection=face_det)

                # Extract embedding
                if self._face_recognizer is not None and face_det.aligned_face is not None:
                    try:
                        embedding = self._face_recognizer.get_embedding(face_det.aligned_face)
                        face_result.embedding = embedding

                        # Match against gallery
                        if self._gallery_embeddings is not None and self._gallery_embeddings.shape[0] > 0:
                            match = self._face_recognizer.find_best_match(
                                embedding,
                                self._gallery_embeddings,
                                self._gallery_ids,
                            )
                            if match is not None:
                                idx, sim, gallery_id = match
                                face_result.match_id = gallery_id
                                face_result.match_similarity = sim
                                if self._gallery_names and idx < len(self._gallery_names):
                                    face_result.match_name = self._gallery_names[idx]
                    except Exception as exc:
                        logger.debug("pipeline.face_recognition_error", error=str(exc))

                # Emotion classification
                if self._emotion_classifier is not None and face_det.aligned_face is not None:
                    try:
                        emotion, conf, _ = self._emotion_classifier.classify(
                            face_det.aligned_face,
                        )
                        face_result.emotion = emotion
                        face_result.emotion_confidence = conf
                    except Exception as exc:
                        logger.debug("pipeline.emotion_error", error=str(exc))

                result.face_results.append(face_result)

        # ── 5. Vehicle Recognition ────────────────────────────────────
        vehicle_class_ids = {2, 3, 5, 7}  # car, motorcycle, bus, truck
        vehicle_detections = [d for d in detections if d.class_id in vehicle_class_ids]

        for veh_det in vehicle_detections:
            veh_result = VehicleResult(detection=veh_det)

            # Classify vehicle attributes
            if self._vehicle_classifier is not None:
                try:
                    x1, y1, x2, y2 = [int(c) for c in veh_det.bbox]
                    h, w = frame.shape[:2]
                    x1, y1 = max(0, x1), max(0, y1)
                    x2, y2 = min(w, x2), min(h, y2)
                    if x2 > x1 and y2 > y1:
                        vehicle_crop = frame[y1:y2, x1:x2]
                        attrs = self._vehicle_classifier.classify(vehicle_crop)
                        veh_result.color = attrs["color"]
                        veh_result.vehicle_type = attrs["type"]
                        veh_result.color_confidence = attrs["color_confidence"]
                        veh_result.type_confidence = attrs["type_confidence"]
                except Exception as exc:
                    logger.debug("pipeline.vehicle_classify_error", error=str(exc))

            # Plate detection and OCR
            if self._plate_detector is not None:
                try:
                    plates = self._plate_detector.detect_in_vehicle_roi(
                        frame, veh_det.bbox,
                    )
                    if plates:
                        best_plate = plates[0]
                        veh_result.plate_bbox = best_plate.bbox

                        if self._plate_ocr is not None and best_plate.plate_image is not None:
                            text, conf = self._plate_ocr.recognize(best_plate.plate_image)
                            veh_result.plate_text = text
                            veh_result.plate_confidence = conf
                except Exception as exc:
                    logger.debug("pipeline.plate_error", error=str(exc))

            result.vehicle_results.append(veh_result)

        # ── 6. PPE Detection ──────────────────────────────────────────
        if self._ppe_detector is not None:
            person_bboxes = [
                d.bbox for d in detections if d.class_id == 0
            ]
            if person_bboxes:
                try:
                    compliance_results = self._ppe_detector.detect_and_check(
                        frame, person_bboxes, self._required_ppe,
                    )
                    result.ppe_compliance = compliance_results

                    for comp in compliance_results:
                        if not comp.compliant:
                            result.alerts.append({
                                "type": "ppe_violation",
                                "camera_id": self.camera_id,
                                "violations": comp.violations,
                                "missing": sorted(comp.missing),
                                "person_bbox": list(comp.person_bbox),
                                "timestamp": timestamp,
                            })
                except Exception as exc:
                    logger.debug("pipeline.ppe_error", error=str(exc))

        # ── 7. Fire Detection ─────────────────────────────────────────
        if self._fire_detector is not None:
            try:
                fire_dets = self._fire_detector.detect(frame)
                result.fire_detections = fire_dets

                if fire_dets:
                    alert_info = self._fire_detector.get_alert_info(
                        fire_dets, frame.shape[:2],
                    )
                    result.alerts.append({
                        "type": "fire_smoke",
                        "camera_id": self.camera_id,
                        "severity": alert_info.severity,
                        "fire_count": alert_info.fire_count,
                        "smoke_count": alert_info.smoke_count,
                        "timestamp": timestamp,
                    })
            except Exception as exc:
                logger.debug("pipeline.fire_error", error=str(exc))

        # ── 8. Pose Estimation ────────────────────────────────────────
        if self._pose_detector is not None:
            try:
                poses = self._pose_detector.detect(frame)
                result.pose_detections = poses

                # Fall detection
                falls = self._pose_detector.detect_fall(poses)
                for fall in falls:
                    result.alerts.append({
                        "type": "fall_detected",
                        "camera_id": self.camera_id,
                        "bbox": list(fall.bbox),
                        "confidence": round(fall.confidence, 4),
                        "timestamp": timestamp,
                    })

                # Fighting detection
                fights = self._pose_detector.detect_fighting(poses)
                for pair in fights:
                    result.alerts.append({
                        "type": "fighting_detected",
                        "camera_id": self.camera_id,
                        "person_a_bbox": list(pair[0].bbox),
                        "person_b_bbox": list(pair[1].bbox),
                        "timestamp": timestamp,
                    })
            except Exception as exc:
                logger.debug("pipeline.pose_error", error=str(exc))

        # ── 9. Zone Analysis ──────────────────────────────────────────
        if self._zone_analyzer is not None:
            det_dicts = [d.to_dict() for d in detections]

            # Intrusion detection
            zone_events = self._zone_analyzer.check_detections(det_dicts)
            result.zone_events.extend(zone_events)

            # Tripwire crossing
            tracked_dicts = [
                {
                    "track_id": t.track_id,
                    "bbox": t.bbox.tolist() if isinstance(t.bbox, np.ndarray) else list(t.bbox),
                }
                for t in result.tracks
            ]
            crossing_events = self._zone_analyzer.check_tripwire(tracked_dicts)
            result.zone_events.extend(crossing_events)

            # Generate alerts for zone events
            for event in zone_events + crossing_events:
                result.alerts.append({
                    "type": f"zone_{event.event_type}",
                    "camera_id": self.camera_id,
                    "zone_id": event.zone_id,
                    "zone_name": event.zone_name,
                    "track_id": event.track_id,
                    "details": event.details,
                    "timestamp": timestamp,
                })

        # ── 10. Behavior Analysis ─────────────────────────────────────
        if self._behavior_analyzer is not None:
            tracked_dicts = [
                {
                    "track_id": t.track_id,
                    "bbox": t.bbox.tolist() if isinstance(t.bbox, np.ndarray) else list(t.bbox),
                    "class_name": "person" if t.class_id == 0 else "object",
                }
                for t in result.tracks
            ]
            behavior_events = self._behavior_analyzer.analyze(
                tracked_dicts, timestamp,
            )
            result.behavior_events = behavior_events

            for event in behavior_events:
                result.alerts.append({
                    "type": f"behavior_{event.event_type}",
                    "camera_id": self.camera_id,
                    "track_id": event.track_id,
                    "confidence": round(event.confidence, 4),
                    "details": event.details,
                    "timestamp": timestamp,
                })

        # ── 11. Crowd Analysis ────────────────────────────────────────
        if self._crowd_analyzer is not None and self._zone_analyzer is not None:
            det_dicts = [d.to_dict() for d in detections]
            zones_config = self.config.get("zones", [])
            crowd_results = self._crowd_analyzer.analyze_zones(
                det_dicts, zones_config,
            )
            for zone_id, zone_data in crowd_results.items():
                if zone_data.get("alert"):
                    alert_data = zone_data["alert"]
                    crowd_alert = CrowdAlert(
                        zone_id=alert_data["zone_id"],
                        zone_name=alert_data["zone_name"],
                        current_count=alert_data["current_count"],
                        max_count=alert_data["max_count"],
                        severity=alert_data["severity"],
                        density=alert_data.get("density", 0.0),
                    )
                    result.crowd_alerts.append(crowd_alert)
                    result.alerts.append({
                        "type": "crowd_threshold",
                        "camera_id": self.camera_id,
                        "zone_id": zone_id,
                        "count": alert_data["current_count"],
                        "max_count": alert_data["max_count"],
                        "severity": alert_data["severity"],
                        "timestamp": timestamp,
                    })

        # ── 12. Heatmap Accumulation ──────────────────────────────────
        if self._heatmap_generator is not None:
            person_dets = [d.to_dict() for d in detections if d.class_id == 0]
            self._heatmap_generator.add_detections(person_dets)
            self._heatmap_generator.apply_decay()

        # ── Aggregate events ──────────────────────────────────────────
        result.events = result.alerts.copy()

        # ── Timing ────────────────────────────────────────────────────
        result.processing_time_ms = (time.perf_counter() - start_time) * 1000

        return result

    @staticmethod
    def _assign_track_ids(
        detections: list[Detection],
        tracks: list[STrack],
    ) -> None:
        """Assign track IDs to detections by matching bounding boxes.

        Uses IoU matching to find the best track for each detection
        and assigns the track ID.

        Args:
            detections: Detection list (modified in-place).
            tracks: Active track list.
        """
        if not detections or not tracks:
            return

        det_bboxes = np.array([d.bbox for d in detections], dtype=np.float64)
        trk_bboxes = np.array(
            [t.bbox if isinstance(t.bbox, (list, tuple)) else t.bbox.tolist()
             for t in tracks],
            dtype=np.float64,
        )

        # Compute IoU matrix
        m = det_bboxes.shape[0]
        n = trk_bboxes.shape[0]

        for i in range(m):
            best_iou = 0.0
            best_track_id = None

            for j in range(n):
                x1 = max(det_bboxes[i, 0], trk_bboxes[j, 0])
                y1 = max(det_bboxes[i, 1], trk_bboxes[j, 1])
                x2 = min(det_bboxes[i, 2], trk_bboxes[j, 2])
                y2 = min(det_bboxes[i, 3], trk_bboxes[j, 3])

                inter = max(0.0, x2 - x1) * max(0.0, y2 - y1)
                area_d = (det_bboxes[i, 2] - det_bboxes[i, 0]) * (det_bboxes[i, 3] - det_bboxes[i, 1])
                area_t = (trk_bboxes[j, 2] - trk_bboxes[j, 0]) * (trk_bboxes[j, 3] - trk_bboxes[j, 1])
                union = area_d + area_t - inter

                iou = inter / union if union > 0 else 0.0
                if iou > best_iou:
                    best_iou = iou
                    best_track_id = tracks[j].track_id

            if best_track_id is not None and best_iou > 0.3:
                detections[i].track_id = best_track_id

    def update_gallery(
        self,
        embeddings: np.ndarray,
        ids: list[str],
        names: list[str] | None = None,
    ) -> None:
        """Update the face recognition gallery.

        Args:
            embeddings: Gallery embeddings of shape ``(N, 512)``.
            ids: Gallery person IDs.
            names: Optional gallery person names.
        """
        self._gallery_embeddings = embeddings
        self._gallery_ids = ids
        self._gallery_names = names
        logger.info(
            "pipeline.gallery_updated",
            camera_id=self.camera_id,
            gallery_size=embeddings.shape[0],
        )

    def get_heatmap_overlay(self, frame: np.ndarray) -> Optional[np.ndarray]:
        """Generate a heatmap overlay on the given frame.

        Args:
            frame: BGR camera frame.

        Returns:
            Optional[np.ndarray]: Frame with heatmap overlay, or
                ``None`` if heatmap is not enabled.
        """
        if self._heatmap_generator is None:
            return None
        return self._heatmap_generator.generate_overlay(frame)

    @property
    def is_running(self) -> bool:
        """Return whether the pipeline is currently running."""
        return self._running

    @property
    def frames_processed(self) -> int:
        """Return the total number of frames processed."""
        return self._frame_counter
