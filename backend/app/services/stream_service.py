"""Stream service – bridges the API layer to the StreamManager.

Provides async-friendly wrappers for starting/stopping camera streams,
capturing snapshots, testing connections, and serving MJPEG frames.
"""

from __future__ import annotations

import asyncio
import io
import threading
import time
import uuid
from datetime import datetime, timezone
from typing import Any, AsyncGenerator, Optional

import cv2
import numpy as np
import structlog

from app.services.stream_manager import StreamManager, StreamState, get_stream_manager

logger = structlog.stdlib.get_logger(__name__)

# Globals for live stream CV detection & alert dispatching
_face_detector_instance: Any = None
_face_detector_lock = threading.Lock()
_alert_cooldowns: dict[str, float] = {}


# ── Stream lifecycle ────────────────────────────────────────────────────


async def start_stream(camera_id: str, stream_url: str | None = None, camera_name: str = "") -> dict[str, Any]:
    """Start streaming for a camera.

    If *stream_url* is not provided the function looks up the camera in the
    database to get its stream URL.  For USB cameras the stream_url is the
    device path (e.g. ``/dev/video0``).
    """
    cam_uuid = uuid.UUID(camera_id)
    mgr = get_stream_manager()

    # If stream_url not passed, look it up
    if not stream_url:
        stream_url = await _lookup_stream_url(camera_id)

    # For USB device paths, convert to integer index for OpenCV
    effective_url: str | int = stream_url
    if stream_url.startswith("/dev/video"):
        try:
            effective_url = int(stream_url.replace("/dev/video", ""))
        except ValueError:
            effective_url = stream_url

    metrics = await asyncio.to_thread(
        mgr.start_stream,
        camera_id=cam_uuid,
        stream_url=effective_url,
        camera_name=camera_name,
    )

    logger.info("Stream started via service", camera_id=camera_id)
    return metrics.to_dict()


async def stop_stream(camera_id: str) -> dict[str, Any]:
    """Stop streaming for a camera."""
    cam_uuid = uuid.UUID(camera_id)
    mgr = get_stream_manager()
    metrics = await asyncio.to_thread(mgr.stop_stream, cam_uuid)
    logger.info("Stream stopped via service", camera_id=camera_id)
    return metrics.to_dict()


async def get_stream_status(camera_id: str) -> dict[str, Any]:
    """Return stream metrics for a camera."""
    cam_uuid = uuid.UUID(camera_id)
    mgr = get_stream_manager()
    return mgr.get_stream_status(cam_uuid).to_dict()


# ── Snapshot ────────────────────────────────────────────────────────────


async def capture_snapshot(camera_id: str) -> dict[str, Any]:
    """Capture a single JPEG snapshot from the camera's stream.

    If the stream is already running, grabs the latest buffered frame.
    Otherwise opens a one-shot capture, grabs a frame, and closes.

    Returns a dict with ``url`` (data-uri) and ``timestamp``.
    """
    cam_uuid = uuid.UUID(camera_id)
    mgr = get_stream_manager()

    frame = mgr.get_frame(cam_uuid)

    # If stream isn't running, do a one-shot capture
    if frame is None:
        stream_url = await _lookup_stream_url(camera_id)
        frame = await _oneshot_capture(stream_url)

    if frame is None:
        raise RuntimeError("Could not capture frame from camera")

    # Encode to JPEG
    _, buf = cv2.imencode(".jpg", frame, [cv2.IMWRITE_JPEG_QUALITY, 85])
    import base64
    b64 = base64.b64encode(buf.tobytes()).decode("ascii")

    return {
        "url": f"data:image/jpeg;base64,{b64}",
        "timestamp": datetime.now(timezone.utc).isoformat(),
    }


# ── Stream test ─────────────────────────────────────────────────────────


async def test_camera_stream(
    stream_url: str,
    username_encrypted: str | None = None,
    password_encrypted: str | None = None,
    protocol: str = "rtsp",
) -> dict[str, Any]:
    """Test connectivity to a camera stream by opening it briefly.

    Returns resolution, fps, and a snapshot if successful.
    """
    effective_url: str | int = stream_url
    if stream_url.startswith("/dev/video"):
        try:
            effective_url = int(stream_url.replace("/dev/video", ""))
        except ValueError:
            pass

    start = time.monotonic()
    frame = await _oneshot_capture(effective_url)
    latency = round((time.monotonic() - start) * 1000, 1)

    if frame is None:
        return {"connected": False, "latency_ms": latency}

    h, w = frame.shape[:2]
    _, buf = cv2.imencode(".jpg", frame, [cv2.IMWRITE_JPEG_QUALITY, 75])
    import base64
    b64 = base64.b64encode(buf.tobytes()).decode("ascii")

    return {
        "connected": True,
        "resolution": f"{w}x{h}",
        "fps": 30,
        "codec": "mjpeg",
        "snapshot_url": f"data:image/jpeg;base64,{b64}",
        "latency_ms": latency,
    }


# ── MJPEG generator ────────────────────────────────────────────────────


async def mjpeg_frame_generator(
    camera_id: str,
    fps_target: int = 15,
    quality: int = 70,
) -> AsyncGenerator[bytes, None]:
    """Yield MJPEG multipart frames for HTTP streaming.

    This is an async generator suitable for use with
    ``StreamingResponse(media_type="multipart/x-mixed-replace; ...")``.
    Auto-starts the stream worker if it is not currently running.
    """
    cam_uuid = uuid.UUID(camera_id)
    mgr = get_stream_manager()
    interval = 1.0 / max(fps_target, 1)

    # Auto-start stream worker if not running
    status = mgr.get_stream_status(cam_uuid)
    if status.state not in (StreamState.RUNNING, StreamState.STARTING):
        try:
            stream_url = await _lookup_stream_url(camera_id)
            effective_url: str | int = stream_url
            url_str = str(stream_url).strip()
            if url_str.isdigit() or url_str in ("0", "1", "2", "3"):
                effective_url = int(url_str)
            elif url_str.startswith("/dev/video"):
                try:
                    effective_url = int(url_str.replace("/dev/video", ""))
                except ValueError:
                    pass

            await asyncio.to_thread(
                mgr.start_stream,
                camera_id=cam_uuid,
                stream_url=effective_url,
            )
        except Exception as e:
            logger.warning("Auto-start stream failed", camera_id=camera_id, error=str(e))

    while True:
        frame = mgr.get_frame(cam_uuid)

        if frame is not None:
            frame_to_send = frame
            try:
                detector = _get_face_detector()
                if detector is not None:
                    faces = detector.detect(frame, confidence_threshold=0.45)
                    if faces:
                        frame_to_send = frame.copy()
                        now = time.monotonic()

                        for face in faces:
                            x1, y1, x2, y2 = [int(v) for v in face.bbox]
                            conf_pct = int(face.confidence * 100)

                            # Green bounding box for Face
                            cv2.rectangle(frame_to_send, (x1, y1), (x2, y2), (0, 255, 0), 2)
                            cv2.putText(
                                frame_to_send,
                                f"Face ({conf_pct}%)",
                                (x1, max(y1 - 8, 20)),
                                cv2.FONT_HERSHEY_SIMPLEX,
                                0.6,
                                (0, 255, 0),
                                2,
                            )

                            # Orange bounding box for Person / Human
                            h = y2 - y1
                            w = x2 - x1
                            px1 = max(0, int(x1 - w * 0.5))
                            py1 = max(0, int(y1 - h * 0.2))
                            px2 = min(frame.shape[1], int(x2 + w * 0.5))
                            py2 = min(frame.shape[0], int(y2 + h * 2.5))
                            cv2.rectangle(frame_to_send, (px1, py1), (px2, py2), (255, 165, 0), 2)
                            cv2.putText(
                                frame_to_send,
                                f"Person ({conf_pct}%)",
                                (px1, max(py1 - 8, 20)),
                                cv2.FONT_HERSHEY_SIMPLEX,
                                0.6,
                                (255, 165, 0),
                                2,
                            )

                        # Trigger Face Alert with 8-second cooldown
                        face_cd_key = f"{camera_id}_face"
                        if now - _alert_cooldowns.get(face_cd_key, 0.0) > 8.0:
                            _alert_cooldowns[face_cd_key] = now
                            asyncio.create_task(
                                _trigger_alert_for_detection(
                                    camera_id,
                                    "face_unknown",
                                    "Face Detected: Laptop Webcam",
                                    f"Face detected in live camera stream with {int(faces[0].confidence * 100)}% confidence.",
                                )
                            )

                        # Trigger Human Alert with 8-second cooldown
                        human_cd_key = f"{camera_id}_human"
                        if now - _alert_cooldowns.get(human_cd_key, 0.0) > 8.0:
                            _alert_cooldowns[human_cd_key] = now
                            asyncio.create_task(
                                _trigger_alert_for_detection(
                                    camera_id,
                                    "intrusion_detection",
                                    "Human Detected: Laptop Webcam",
                                    "Human presence detected in live camera stream.",
                                )
                            )
            except Exception as exc:
                logger.warning("Frame detection failed", camera_id=camera_id, error=str(exc))

            _, buf = cv2.imencode(".jpg", frame_to_send, [cv2.IMWRITE_JPEG_QUALITY, quality])
            jpg_bytes = buf.tobytes()

            yield (
                b"--frame\r\n"
                b"Content-Type: image/jpeg\r\n"
                b"Content-Length: " + str(len(jpg_bytes)).encode() + b"\r\n"
                b"\r\n" + jpg_bytes + b"\r\n"
            )
        else:
            # No frame available yet; yield a small placeholder
            yield (
                b"--frame\r\n"
                b"Content-Type: text/plain\r\n\r\n"
                b"waiting for frame...\r\n"
            )

        await asyncio.sleep(interval)


# ── Internal helpers ────────────────────────────────────────────────────


async def _lookup_stream_url(camera_id: str) -> str:
    """Look up the stream_url for a camera from the database."""
    from app.database import AsyncSessionLocal as async_session_factory
    from app.models.camera import Camera

    async with async_session_factory() as session:
        from sqlalchemy import select
        result = await session.execute(
            select(Camera.stream_url).where(Camera.id == uuid.UUID(camera_id))
        )
        url = result.scalar_one_or_none()
        if not url:
            raise RuntimeError(f"Camera {camera_id} not found")
        return url


async def _oneshot_capture(stream_url: str | int) -> Optional[np.ndarray]:
    """Open a video source, grab one frame, and close."""
    def _grab():
        try:
            cap = cv2.VideoCapture(stream_url)
            if not cap.isOpened():
                return None
            cap.set(cv2.CAP_PROP_BUFFERSIZE, 1)
            ret, frame = cap.read()
            cap.release()
            return frame if ret else None
        except Exception:
            return None

    return await asyncio.to_thread(_grab)


def _get_face_detector() -> Any:
    global _face_detector_instance
    if _face_detector_instance is not None:
        return _face_detector_instance
    with _face_detector_lock:
        if _face_detector_instance is not None:
            return _face_detector_instance
        try:
            from app.cv.detectors.face_detector import FaceDetector
            from app.cv.model_registry import ModelRegistry

            registry = ModelRegistry()
            registry.load_all_models()
            session = registry.get_model("scrfd_2.5g")
            _face_detector_instance = FaceDetector(session)
            logger.info("FaceDetector initialized for live MJPEG stream")
        except Exception as exc:
            logger.warning("Failed to initialize FaceDetector for stream", error=str(exc))
            _face_detector_instance = None
    return _face_detector_instance


async def _trigger_alert_for_detection(
    camera_id_str: str,
    alert_type_val: str,
    title: str,
    description: str,
) -> None:
    """Create an Alert in the database and dispatch it via Redis & WebSockets."""
    try:
        from app.database import AsyncSessionLocal
        from app.models.camera import Camera
        from app.models.rule import Rule, RuleSeverity, RuleType
        from app.services.alert_dispatcher import create_alert
        from sqlalchemy import select

        cam_uuid = uuid.UUID(camera_id_str)

        async with AsyncSessionLocal() as session:
            cam_res = await session.execute(select(Camera).where(Camera.id == cam_uuid))
            camera = cam_res.scalar_one_or_none()
            if not camera:
                return

            rule_type_enum = RuleType(alert_type_val)
            rule_res = await session.execute(
                select(Rule).where(
                    Rule.camera_id == cam_uuid,
                    Rule.rule_type == alert_type_val,
                )
            )
            rule = rule_res.scalars().first()
            if not rule:
                rule = Rule(
                    id=uuid.uuid4(),
                    org_id=camera.org_id,
                    camera_id=cam_uuid,
                    rule_type=alert_type_val,
                    severity=(
                        RuleSeverity.INFO
                        if alert_type_val == RuleType.FACE_UNKNOWN.value
                        else RuleSeverity.HIGH
                    ),
                    is_active=True,
                    cooldown_seconds=10,
                )
                session.add(rule)
                await session.flush()

            severity_val = (
                rule.severity
                if isinstance(rule.severity, RuleSeverity)
                else RuleSeverity(rule.severity)
            )

            await create_alert(
                db=session,
                org_id=camera.org_id,
                camera_id=cam_uuid,
                rule_id=rule.id,
                alert_type=rule_type_enum,
                severity=severity_val,
                title=title,
                description=description,
                metadata_json={
                    "detection": "live_webcam",
                    "timestamp": datetime.now(timezone.utc).isoformat(),
                },
            )
            await session.commit()
            logger.info("Live detection alert created and dispatched", title=title)
    except Exception as exc:
        logger.error("Failed to trigger live detection alert", error=str(exc))

