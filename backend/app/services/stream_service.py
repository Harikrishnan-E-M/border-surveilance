"""Stream service – bridges the API layer to the StreamManager.

Provides async-friendly wrappers for starting/stopping camera streams,
capturing snapshots, testing connections, and serving MJPEG frames.
"""

from __future__ import annotations

import asyncio
import io
import time
import uuid
from datetime import datetime, timezone
from typing import Any, AsyncGenerator, Optional

import cv2
import numpy as np
import structlog

from app.services.stream_manager import StreamManager, StreamState, get_stream_manager

logger = structlog.stdlib.get_logger(__name__)


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
    """
    cam_uuid = uuid.UUID(camera_id)
    mgr = get_stream_manager()
    interval = 1.0 / max(fps_target, 1)

    while True:
        frame = mgr.get_frame(cam_uuid)

        if frame is not None:
            _, buf = cv2.imencode(".jpg", frame, [cv2.IMWRITE_JPEG_QUALITY, quality])
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
