"""Rule evaluation engine for real-time detection events.

Loads active rules from the database, evaluates incoming detections and
tracks against rule conditions, enforces cooldown periods via Redis,
checks cron schedules, and produces Alert objects when rules fire.
"""

from __future__ import annotations

import uuid
from datetime import datetime, timezone
from typing import Any, Optional

import structlog
from croniter import croniter
from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession

from app.models.alert import Alert, AlertStatus
from app.models.rule import Rule, RuleSeverity, RuleType
from app.utils.geometry import bbox_center, point_in_polygon

logger = structlog.stdlib.get_logger(__name__)


class RuleEngine:
    """Evaluates detection results against active rules for a camera.

    The engine loads rules once per evaluation cycle (or can be cached),
    checks each rule's schedule and cooldown, then dispatches to the
    appropriate check method based on rule type.

    Args:
        db: Async database session for rule loading and alert creation.
        redis: Optional Redis client for cooldown tracking.
    """

    def __init__(
        self,
        db: AsyncSession,
        redis: Any = None,
    ) -> None:
        self._db = db
        self._redis = redis
        self._rules_cache: dict[uuid.UUID, list[Rule]] = {}

    async def load_rules(self, camera_id: uuid.UUID) -> list[Rule]:
        """Load active rules for a camera from the database.

        Args:
            camera_id: Camera to load rules for.

        Returns:
            List of active Rule instances.
        """
        result = await self._db.execute(
            select(Rule).where(
                Rule.camera_id == camera_id,
                Rule.is_active.is_(True),
            )
        )
        rules = list(result.scalars().all())
        self._rules_cache[camera_id] = rules
        return rules

    async def evaluate(
        self,
        camera_id: uuid.UUID,
        detections: list[dict[str, Any]],
        tracks: list[dict[str, Any]],
        timestamp: datetime,
    ) -> list[Alert]:
        """Evaluate all active rules for a camera against current detections.

        Args:
            camera_id: Camera that produced the detections.
            detections: List of detection dicts with keys: label, confidence, bbox [x1,y1,x2,y2].
            tracks: List of track dicts with keys: track_id, bbox, label, positions (history).
            timestamp: Current frame timestamp.

        Returns:
            List of Alert instances created by triggered rules.
        """
        rules = self._rules_cache.get(camera_id)
        if rules is None:
            rules = await self.load_rules(camera_id)

        alerts: list[Alert] = []

        for rule in rules:
            if not self._is_schedule_active(rule, timestamp):
                continue

            if await self._is_in_cooldown(rule):
                continue

            triggered = await self._check_rule(rule, detections, tracks, timestamp)

            if triggered:
                alert = await self._create_alert(rule, triggered, timestamp)
                alerts.append(alert)
                await self._set_cooldown(rule)

        return alerts

    # ── Schedule Checking ────────────────────────────────────────────────

    @staticmethod
    def _is_schedule_active(rule: Rule, timestamp: datetime) -> bool:
        """Check if a rule is active according to its cron schedule.

        If no schedule is set, the rule is always active.

        Args:
            rule: Rule to check.
            timestamp: Current time.

        Returns:
            True if the rule is currently active.
        """
        if not rule.schedule_cron:
            return True

        try:
            cron = croniter(rule.schedule_cron, timestamp)
            prev_fire = cron.get_prev(datetime)
            next_fire = cron.get_next(datetime)
            window = (next_fire - prev_fire).total_seconds()
            elapsed = (timestamp - prev_fire).total_seconds()
            return elapsed <= min(window * 0.5, 300)
        except (ValueError, KeyError):
            logger.warning(
                "Invalid cron expression, treating rule as always active",
                rule_id=str(rule.id),
                cron=rule.schedule_cron,
            )
            return True

    # ── Cooldown Management ──────────────────────────────────────────────

    async def _is_in_cooldown(self, rule: Rule) -> bool:
        """Check if a rule is still in cooldown from a recent trigger.

        Uses Redis if available, otherwise skips cooldown enforcement.

        Args:
            rule: Rule to check cooldown for.

        Returns:
            True if the rule is in cooldown (should not fire).
        """
        if rule.cooldown_seconds <= 0:
            return False

        if self._redis is None:
            return False

        try:
            key = f"rule:cooldown:{rule.id}"
            exists = await self._redis.exists(key)
            return bool(exists)
        except Exception as exc:
            logger.debug("Redis cooldown check failed", error=str(exc))
            return False

    async def _set_cooldown(self, rule: Rule) -> None:
        """Set the cooldown timer for a rule after it fires.

        Args:
            rule: Rule that was just triggered.
        """
        if rule.cooldown_seconds <= 0 or self._redis is None:
            return

        try:
            key = f"rule:cooldown:{rule.id}"
            await self._redis.setex(key, rule.cooldown_seconds, "1")
        except Exception as exc:
            logger.debug("Redis cooldown set failed", error=str(exc))

    # ── Rule Type Dispatcher ─────────────────────────────────────────────

    async def _check_rule(
        self,
        rule: Rule,
        detections: list[dict[str, Any]],
        tracks: list[dict[str, Any]],
        timestamp: datetime,
    ) -> Optional[dict[str, Any]]:
        """Dispatch to the appropriate check method based on rule type.

        Args:
            rule: Rule to evaluate.
            detections: Current frame detections.
            tracks: Current tracked objects.
            timestamp: Current frame time.

        Returns:
            Trigger metadata dict if the rule fires, or None.
        """
        type_handlers = {
            RuleType.INTRUSION_DETECTION: self._check_intrusion,
            RuleType.CROWD_FORMATION: self._check_crowd,
            RuleType.LOITERING: self._check_loitering,
            RuleType.PPE_VIOLATION: self._check_ppe,
            RuleType.FIRE_SMOKE: self._check_fire_smoke,
            RuleType.FALL_DETECTION: self._check_fall,
            RuleType.VIOLENCE_DETECTION: self._check_fighting,
            RuleType.CAMERA_TAMPER: self._check_tampering,
            RuleType.ANPR_BLACKLISTED: self._check_vehicle_blacklist,
            RuleType.ANPR_UNKNOWN: self._check_vehicle_unknown,
            RuleType.SPEED_VIOLATION: self._check_speed_violation,
            RuleType.ILLEGAL_PARKING: self._check_illegal_parking,
            RuleType.OBJECT_LEFT_BEHIND: self._check_object_left,
            RuleType.OBJECT_REMOVED: self._check_object_removed,
            RuleType.LINE_CROSSING: self._check_line_crossing,
            RuleType.WRONG_DIRECTION: self._check_wrong_direction,
            RuleType.FACE_RECOGNIZED: self._check_face_recognized,
            RuleType.FACE_UNKNOWN: self._check_face_unknown,
            RuleType.FACE_BLACKLISTED: self._check_face_blacklisted,
            RuleType.OCCUPANCY_THRESHOLD: self._check_occupancy_threshold,
            RuleType.NO_ENTRY_ZONE: self._check_no_entry,
            RuleType.TAILGATING: self._check_tailgating,
        }

        handler = type_handlers.get(rule.rule_type)
        if handler is None:
            logger.warning("No handler for rule type", rule_type=rule.rule_type.value)
            return None

        return handler(rule, detections, tracks, timestamp)

    # ── Individual Rule Checks ───────────────────────────────────────────

    def _get_zone_polygon(self, rule: Rule) -> Optional[list[tuple[float, float]]]:
        """Extract the zone polygon from the rule's zone relationship."""
        if rule.zone is None:
            return None
        points = rule.zone.polygon_points
        if not points:
            return None
        return [(p.get("x", p.get(0, 0)), p.get("y", p.get(1, 0))) for p in points]

    def _filter_detections_in_zone(
        self,
        detections: list[dict[str, Any]],
        polygon: Optional[list[tuple[float, float]]],
    ) -> list[dict[str, Any]]:
        """Filter detections to only those whose center falls inside the zone polygon."""
        if polygon is None:
            return detections

        result = []
        for det in detections:
            bbox = det.get("bbox")
            if bbox and len(bbox) >= 4:
                center = bbox_center((bbox[0], bbox[1], bbox[2], bbox[3]))
                if point_in_polygon(center, polygon):
                    result.append(det)
        return result

    def _check_intrusion(
        self,
        rule: Rule,
        detections: list[dict[str, Any]],
        tracks: list[dict[str, Any]],
        timestamp: datetime,
    ) -> Optional[dict[str, Any]]:
        """Detect persons entering a restricted zone."""
        params = rule.parameters or {}
        target_classes = params.get("object_classes", ["person"])
        confidence_threshold = params.get("confidence_threshold", 0.5)
        polygon = self._get_zone_polygon(rule)

        relevant = [
            d for d in detections
            if d.get("label") in target_classes and d.get("confidence", 0) >= confidence_threshold
        ]
        in_zone = self._filter_detections_in_zone(relevant, polygon)

        if in_zone:
            return {
                "type": "intrusion",
                "object_count": len(in_zone),
                "objects": [{"label": d.get("label"), "confidence": d.get("confidence")} for d in in_zone[:5]],
                "zone_id": str(rule.zone_id) if rule.zone_id else None,
            }
        return None

    def _check_crowd(
        self,
        rule: Rule,
        detections: list[dict[str, Any]],
        tracks: list[dict[str, Any]],
        timestamp: datetime,
    ) -> Optional[dict[str, Any]]:
        """Detect crowd formation exceeding a threshold."""
        params = rule.parameters or {}
        max_count = params.get("max_count", 10)
        confidence_threshold = params.get("confidence_threshold", 0.4)
        polygon = self._get_zone_polygon(rule)

        persons = [
            d for d in detections
            if d.get("label") == "person" and d.get("confidence", 0) >= confidence_threshold
        ]
        in_zone = self._filter_detections_in_zone(persons, polygon)

        if len(in_zone) >= max_count:
            return {
                "type": "crowd_formation",
                "person_count": len(in_zone),
                "threshold": max_count,
                "zone_id": str(rule.zone_id) if rule.zone_id else None,
            }
        return None

    def _check_loitering(
        self,
        rule: Rule,
        detections: list[dict[str, Any]],
        tracks: list[dict[str, Any]],
        timestamp: datetime,
    ) -> Optional[dict[str, Any]]:
        """Detect loitering (person staying in zone beyond threshold)."""
        params = rule.parameters or {}
        min_duration = params.get("min_duration_seconds", 30)
        polygon = self._get_zone_polygon(rule)

        for track in tracks:
            if track.get("label") != "person":
                continue

            positions = track.get("positions", [])
            if len(positions) < 2:
                continue

            first_time = positions[0].get("timestamp")
            last_time = positions[-1].get("timestamp")
            if first_time and last_time:
                if isinstance(first_time, (int, float)) and isinstance(last_time, (int, float)):
                    duration = last_time - first_time
                else:
                    continue

                if duration >= min_duration:
                    bbox = track.get("bbox")
                    if bbox and polygon:
                        center = bbox_center((bbox[0], bbox[1], bbox[2], bbox[3]))
                        if not point_in_polygon(center, polygon):
                            continue

                    return {
                        "type": "loitering",
                        "track_id": track.get("track_id"),
                        "duration_seconds": round(duration, 1),
                        "threshold_seconds": min_duration,
                    }
        return None

    def _check_ppe(
        self,
        rule: Rule,
        detections: list[dict[str, Any]],
        tracks: list[dict[str, Any]],
        timestamp: datetime,
    ) -> Optional[dict[str, Any]]:
        """Detect PPE (Personal Protective Equipment) violations."""
        params = rule.parameters or {}
        required_items = params.get("required_items", ["helmet", "vest"])
        confidence_threshold = params.get("confidence_threshold", 0.5)
        polygon = self._get_zone_polygon(rule)

        persons = self._filter_detections_in_zone(
            [d for d in detections if d.get("label") == "person"],
            polygon,
        )

        detected_ppe = set()
        for d in detections:
            label = d.get("label", "")
            if d.get("confidence", 0) >= confidence_threshold:
                detected_ppe.add(label.lower())

        for item in required_items:
            if item.lower() not in detected_ppe and persons:
                return {
                    "type": "ppe_violation",
                    "missing_items": [
                        i for i in required_items if i.lower() not in detected_ppe
                    ],
                    "persons_in_zone": len(persons),
                }
        return None

    def _check_fire_smoke(
        self,
        rule: Rule,
        detections: list[dict[str, Any]],
        tracks: list[dict[str, Any]],
        timestamp: datetime,
    ) -> Optional[dict[str, Any]]:
        """Detect fire or smoke."""
        params = rule.parameters or {}
        confidence_threshold = params.get("confidence_threshold", 0.5)

        fire_smoke = [
            d for d in detections
            if d.get("label") in ("fire", "smoke", "flame")
            and d.get("confidence", 0) >= confidence_threshold
        ]

        if fire_smoke:
            return {
                "type": "fire_smoke",
                "detections": [
                    {"label": d.get("label"), "confidence": d.get("confidence")}
                    for d in fire_smoke[:5]
                ],
            }
        return None

    def _check_fall(
        self,
        rule: Rule,
        detections: list[dict[str, Any]],
        tracks: list[dict[str, Any]],
        timestamp: datetime,
    ) -> Optional[dict[str, Any]]:
        """Detect fallen persons based on bbox aspect ratio and label."""
        params = rule.parameters or {}
        confidence_threshold = params.get("confidence_threshold", 0.5)

        for d in detections:
            if d.get("label") == "fall" and d.get("confidence", 0) >= confidence_threshold:
                return {
                    "type": "fall_detection",
                    "confidence": d.get("confidence"),
                    "bbox": d.get("bbox"),
                }

        for d in detections:
            if d.get("label") != "person":
                continue
            bbox = d.get("bbox")
            if bbox and len(bbox) >= 4:
                width = abs(bbox[2] - bbox[0])
                height = abs(bbox[3] - bbox[1])
                if height > 0 and width / height > 1.5 and d.get("confidence", 0) >= confidence_threshold:
                    return {
                        "type": "fall_detection",
                        "confidence": d.get("confidence"),
                        "bbox": bbox,
                        "aspect_ratio": round(width / height, 2),
                    }
        return None

    def _check_fighting(
        self,
        rule: Rule,
        detections: list[dict[str, Any]],
        tracks: list[dict[str, Any]],
        timestamp: datetime,
    ) -> Optional[dict[str, Any]]:
        """Detect violence or fighting based on explicit model label."""
        params = rule.parameters or {}
        confidence_threshold = params.get("confidence_threshold", 0.6)

        for d in detections:
            if d.get("label") in ("fight", "violence", "fighting"):
                if d.get("confidence", 0) >= confidence_threshold:
                    return {
                        "type": "violence_detection",
                        "confidence": d.get("confidence"),
                        "bbox": d.get("bbox"),
                    }
        return None

    def _check_tampering(
        self,
        rule: Rule,
        detections: list[dict[str, Any]],
        tracks: list[dict[str, Any]],
        timestamp: datetime,
    ) -> Optional[dict[str, Any]]:
        """Detect camera tampering (covered, moved, defocused)."""
        params = rule.parameters or {}
        confidence_threshold = params.get("confidence_threshold", 0.5)

        for d in detections:
            if d.get("label") in ("tamper", "camera_tamper", "covered", "defocused"):
                if d.get("confidence", 0) >= confidence_threshold:
                    return {
                        "type": "camera_tamper",
                        "confidence": d.get("confidence"),
                        "sub_type": d.get("label"),
                    }
        return None

    def _check_vehicle_blacklist(
        self,
        rule: Rule,
        detections: list[dict[str, Any]],
        tracks: list[dict[str, Any]],
        timestamp: datetime,
    ) -> Optional[dict[str, Any]]:
        """Check if a detected plate matches a blacklisted vehicle."""
        params = rule.parameters or {}
        blacklist = params.get("plate_numbers", [])

        for d in detections:
            if d.get("label") in ("plate", "license_plate"):
                plate_text = d.get("plate_text", "").upper().replace(" ", "")
                if plate_text in [p.upper().replace(" ", "") for p in blacklist]:
                    return {
                        "type": "anpr_blacklisted",
                        "plate_number": plate_text,
                        "confidence": d.get("confidence"),
                    }
        return None

    def _check_vehicle_unknown(
        self,
        rule: Rule,
        detections: list[dict[str, Any]],
        tracks: list[dict[str, Any]],
        timestamp: datetime,
    ) -> Optional[dict[str, Any]]:
        """Detect unknown (unregistered) vehicle plates."""
        params = rule.parameters or {}
        confidence_threshold = params.get("confidence_threshold", 0.7)

        for d in detections:
            if d.get("label") in ("plate", "license_plate"):
                if d.get("confidence", 0) >= confidence_threshold and not d.get("vehicle_matched"):
                    return {
                        "type": "anpr_unknown",
                        "plate_number": d.get("plate_text", ""),
                        "confidence": d.get("confidence"),
                    }
        return None

    def _check_speed_violation(
        self,
        rule: Rule,
        detections: list[dict[str, Any]],
        tracks: list[dict[str, Any]],
        timestamp: datetime,
    ) -> Optional[dict[str, Any]]:
        """Check for vehicle speed violations."""
        params = rule.parameters or {}
        max_speed = params.get("max_speed_kmh", 30)

        for track in tracks:
            if track.get("label") in ("car", "vehicle", "truck", "motorcycle", "bus"):
                speed = track.get("speed_kmh")
                if speed is not None and speed > max_speed:
                    return {
                        "type": "speed_violation",
                        "speed_kmh": round(speed, 1),
                        "max_speed_kmh": max_speed,
                        "track_id": track.get("track_id"),
                    }
        return None

    def _check_illegal_parking(
        self,
        rule: Rule,
        detections: list[dict[str, Any]],
        tracks: list[dict[str, Any]],
        timestamp: datetime,
    ) -> Optional[dict[str, Any]]:
        """Detect illegally parked vehicles in a restricted zone."""
        params = rule.parameters or {}
        min_duration = params.get("min_duration_seconds", 60)
        polygon = self._get_zone_polygon(rule)

        for track in tracks:
            if track.get("label") not in ("car", "vehicle", "truck"):
                continue

            positions = track.get("positions", [])
            if len(positions) < 2:
                continue

            first_time = positions[0].get("timestamp")
            last_time = positions[-1].get("timestamp")
            if isinstance(first_time, (int, float)) and isinstance(last_time, (int, float)):
                duration = last_time - first_time
                if duration >= min_duration:
                    bbox = track.get("bbox")
                    if bbox and polygon:
                        center = bbox_center((bbox[0], bbox[1], bbox[2], bbox[3]))
                        if not point_in_polygon(center, polygon):
                            continue
                    return {
                        "type": "illegal_parking",
                        "track_id": track.get("track_id"),
                        "duration_seconds": round(duration, 1),
                    }
        return None

    def _check_object_left(
        self,
        rule: Rule,
        detections: list[dict[str, Any]],
        tracks: list[dict[str, Any]],
        timestamp: datetime,
    ) -> Optional[dict[str, Any]]:
        """Detect an object left behind (stationary for too long)."""
        params = rule.parameters or {}
        min_duration = params.get("min_duration_seconds", 60)
        target_classes = params.get("object_classes", ["bag", "suitcase", "backpack"])
        polygon = self._get_zone_polygon(rule)

        for track in tracks:
            if track.get("label") not in target_classes:
                continue

            positions = track.get("positions", [])
            if len(positions) < 2:
                continue

            first_pos = positions[0]
            last_pos = positions[-1]

            if isinstance(first_pos.get("timestamp"), (int, float)) and isinstance(
                last_pos.get("timestamp"), (int, float)
            ):
                duration = last_pos["timestamp"] - first_pos["timestamp"]
                if duration >= min_duration:
                    first_bbox = first_pos.get("bbox", track.get("bbox"))
                    last_bbox = last_pos.get("bbox", track.get("bbox"))
                    if first_bbox and last_bbox:
                        c1 = bbox_center((first_bbox[0], first_bbox[1], first_bbox[2], first_bbox[3]))
                        c2 = bbox_center((last_bbox[0], last_bbox[1], last_bbox[2], last_bbox[3]))
                        displacement = ((c2[0] - c1[0]) ** 2 + (c2[1] - c1[1]) ** 2) ** 0.5
                        if displacement < 0.05:
                            if polygon:
                                if not point_in_polygon(c2, polygon):
                                    continue
                            return {
                                "type": "object_left_behind",
                                "track_id": track.get("track_id"),
                                "label": track.get("label"),
                                "duration_seconds": round(duration, 1),
                            }
        return None

    def _check_object_removed(
        self,
        rule: Rule,
        detections: list[dict[str, Any]],
        tracks: list[dict[str, Any]],
        timestamp: datetime,
    ) -> Optional[dict[str, Any]]:
        """Detect object removal from a monitored zone."""
        params = rule.parameters or {}
        confidence_threshold = params.get("confidence_threshold", 0.5)

        for d in detections:
            if d.get("label") == "object_removed" and d.get("confidence", 0) >= confidence_threshold:
                return {
                    "type": "object_removed",
                    "confidence": d.get("confidence"),
                    "bbox": d.get("bbox"),
                }
        return None

    def _check_line_crossing(
        self,
        rule: Rule,
        detections: list[dict[str, Any]],
        tracks: list[dict[str, Any]],
        timestamp: datetime,
    ) -> Optional[dict[str, Any]]:
        """Detect objects crossing a virtual tripwire line."""
        params = rule.parameters or {}
        line_start = params.get("line_start")
        line_end = params.get("line_end")
        target_classes = params.get("object_classes", ["person"])

        if not line_start or not line_end:
            return None

        from app.utils.geometry import check_tripwire_crossing

        for track in tracks:
            if track.get("label") not in target_classes:
                continue
            positions = track.get("positions", [])
            if len(positions) < 2:
                continue
            prev = positions[-2]
            curr = positions[-1]
            prev_bbox = prev.get("bbox")
            curr_bbox = curr.get("bbox")
            if prev_bbox and curr_bbox:
                prev_center = bbox_center(
                    (prev_bbox[0], prev_bbox[1], prev_bbox[2], prev_bbox[3])
                )
                curr_center = bbox_center(
                    (curr_bbox[0], curr_bbox[1], curr_bbox[2], curr_bbox[3])
                )
                crossing = check_tripwire_crossing(
                    prev_center,
                    curr_center,
                    tuple(line_start),
                    tuple(line_end),
                )
                if crossing:
                    return {
                        "type": "line_crossing",
                        "track_id": track.get("track_id"),
                        "direction": crossing,
                        "label": track.get("label"),
                    }
        return None

    def _check_wrong_direction(
        self,
        rule: Rule,
        detections: list[dict[str, Any]],
        tracks: list[dict[str, Any]],
        timestamp: datetime,
    ) -> Optional[dict[str, Any]]:
        """Detect objects moving in a prohibited direction."""
        params = rule.parameters or {}
        forbidden_direction = params.get("forbidden_direction", "right_to_left")
        target_classes = params.get("object_classes", ["person", "car"])

        for track in tracks:
            if track.get("label") not in target_classes:
                continue
            positions = track.get("positions", [])
            if len(positions) < 3:
                continue
            first = positions[0].get("bbox")
            last = positions[-1].get("bbox")
            if first and last:
                c1 = bbox_center((first[0], first[1], first[2], first[3]))
                c2 = bbox_center((last[0], last[1], last[2], last[3]))
                dx = c2[0] - c1[0]
                if forbidden_direction == "right_to_left" and dx < -0.05:
                    return {
                        "type": "wrong_direction",
                        "track_id": track.get("track_id"),
                        "direction": "right_to_left",
                    }
                elif forbidden_direction == "left_to_right" and dx > 0.05:
                    return {
                        "type": "wrong_direction",
                        "track_id": track.get("track_id"),
                        "direction": "left_to_right",
                    }
        return None

    def _check_face_recognized(
        self,
        rule: Rule,
        detections: list[dict[str, Any]],
        tracks: list[dict[str, Any]],
        timestamp: datetime,
    ) -> Optional[dict[str, Any]]:
        """Check for a specific recognized face (e.g., VIP arrival)."""
        params = rule.parameters or {}
        person_ids = params.get("person_ids", [])

        for d in detections:
            if d.get("label") == "face" and d.get("person_id"):
                if str(d.get("person_id")) in [str(pid) for pid in person_ids]:
                    return {
                        "type": "face_recognized",
                        "person_id": d.get("person_id"),
                        "person_name": d.get("person_name"),
                        "confidence": d.get("confidence"),
                    }
        return None

    def _check_face_unknown(
        self,
        rule: Rule,
        detections: list[dict[str, Any]],
        tracks: list[dict[str, Any]],
        timestamp: datetime,
    ) -> Optional[dict[str, Any]]:
        """Detect unknown (unrecognized) faces."""
        params = rule.parameters or {}
        confidence_threshold = params.get("confidence_threshold", 0.5)

        for d in detections:
            if d.get("label") == "face" and d.get("person_id") is None:
                if d.get("confidence", 0) >= confidence_threshold:
                    return {
                        "type": "face_unknown",
                        "confidence": d.get("confidence"),
                        "bbox": d.get("bbox"),
                    }
        return None

    def _check_face_blacklisted(
        self,
        rule: Rule,
        detections: list[dict[str, Any]],
        tracks: list[dict[str, Any]],
        timestamp: datetime,
    ) -> Optional[dict[str, Any]]:
        """Detect a blacklisted person."""
        params = rule.parameters or {}
        blacklisted_ids = params.get("blacklisted_person_ids", [])

        for d in detections:
            if d.get("label") == "face" and d.get("person_id"):
                if str(d.get("person_id")) in [str(pid) for pid in blacklisted_ids]:
                    return {
                        "type": "face_blacklisted",
                        "person_id": d.get("person_id"),
                        "person_name": d.get("person_name"),
                        "confidence": d.get("confidence"),
                    }
        return None

    def _check_occupancy_threshold(
        self,
        rule: Rule,
        detections: list[dict[str, Any]],
        tracks: list[dict[str, Any]],
        timestamp: datetime,
    ) -> Optional[dict[str, Any]]:
        """Check if occupancy exceeds the configured threshold."""
        params = rule.parameters or {}
        max_occupancy = params.get("max_occupancy", 50)
        polygon = self._get_zone_polygon(rule)

        persons = [d for d in detections if d.get("label") == "person"]
        in_zone = self._filter_detections_in_zone(persons, polygon)

        if len(in_zone) >= max_occupancy:
            return {
                "type": "occupancy_threshold",
                "current_count": len(in_zone),
                "max_occupancy": max_occupancy,
            }
        return None

    def _check_no_entry(
        self,
        rule: Rule,
        detections: list[dict[str, Any]],
        tracks: list[dict[str, Any]],
        timestamp: datetime,
    ) -> Optional[dict[str, Any]]:
        """Detect any person entering a no-entry zone."""
        return self._check_intrusion(rule, detections, tracks, timestamp)

    def _check_tailgating(
        self,
        rule: Rule,
        detections: list[dict[str, Any]],
        tracks: list[dict[str, Any]],
        timestamp: datetime,
    ) -> Optional[dict[str, Any]]:
        """Detect tailgating (two persons passing through a gate too close together)."""
        params = rule.parameters or {}
        min_gap_seconds = params.get("min_gap_seconds", 3)
        polygon = self._get_zone_polygon(rule)

        persons_in_zone = self._filter_detections_in_zone(
            [d for d in detections if d.get("label") == "person"],
            polygon,
        )

        if len(persons_in_zone) >= 2:
            return {
                "type": "tailgating",
                "person_count": len(persons_in_zone),
                "min_gap_seconds": min_gap_seconds,
            }
        return None

    # ── Alert Creation ───────────────────────────────────────────────────

    async def _create_alert(
        self,
        rule: Rule,
        trigger_data: dict[str, Any],
        timestamp: datetime,
    ) -> Alert:
        """Create an Alert from a triggered rule.

        Args:
            rule: The rule that fired.
            trigger_data: Metadata from the detection check.
            timestamp: Time of the event.

        Returns:
            The created Alert instance.
        """
        title = f"{rule.rule_type.value.replace('_', ' ').title()} detected"
        if rule.zone and rule.zone.name:
            title += f" in {rule.zone.name}"

        alert = Alert(
            id=uuid.uuid4(),
            org_id=rule.org_id,
            camera_id=rule.camera_id,
            zone_id=rule.zone_id,
            rule_id=rule.id,
            alert_type=rule.rule_type,
            severity=rule.severity,
            title=title,
            description=f"Rule '{rule.rule_type.value}' triggered at {timestamp.isoformat()}",
            metadata_json=trigger_data,
            status=AlertStatus.NEW,
        )
        self._db.add(alert)
        await self._db.flush()

        logger.info(
            "Alert created",
            alert_id=str(alert.id),
            rule_type=rule.rule_type.value,
            severity=rule.severity.value,
            camera_id=str(rule.camera_id),
        )

        return alert
