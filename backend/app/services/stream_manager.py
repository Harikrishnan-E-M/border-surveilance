"""Stream lifecycle management for camera feeds.

Provides a threaded per-camera stream worker with OpenCV VideoCapture,
a bounded frame queue with drop-oldest semantics, exponential backoff
auto-reconnect, and health metrics reporting to Redis.

The StreamManager singleton orchestrates all active stream workers and
exposes start/stop/restart/status methods for the API layer.
"""

from __future__ import annotations

import asyncio
import threading
import time
import uuid
from collections import deque
from dataclasses import dataclass, field
from datetime import datetime, timezone
from enum import Enum
from typing import Any, Optional

import cv2
import numpy as np
import structlog

logger = structlog.stdlib.get_logger(__name__)

MAX_QUEUE_SIZE = 30
INITIAL_RECONNECT_DELAY = 1.0
MAX_RECONNECT_DELAY = 60.0
RECONNECT_BACKOFF_MULTIPLIER = 2.0
HEALTH_REPORT_INTERVAL = 10.0


class StreamState(str, Enum):
    """Possible states of a camera stream worker."""

    STARTING = "starting"
    RUNNING = "running"
    RECONNECTING = "reconnecting"
    STOPPED = "stopped"
    ERROR = "error"


@dataclass
class StreamMetrics:
    """Health metrics for a camera stream."""

    state: StreamState = StreamState.STOPPED
    fps_actual: float = 0.0
    frames_read: int = 0
    frames_dropped: int = 0
    reconnect_count: int = 0
    last_frame_at: Optional[datetime] = None
    last_error: Optional[str] = None
    uptime_seconds: float = 0.0
    started_at: Optional[datetime] = None

    def to_dict(self) -> dict[str, Any]:
        """Serialize metrics to a dictionary for Redis storage."""
        return {
            "state": self.state.value,
            "fps_actual": round(self.fps_actual, 2),
            "frames_read": self.frames_read,
            "frames_dropped": self.frames_dropped,
            "reconnect_count": self.reconnect_count,
            "last_frame_at": self.last_frame_at.isoformat() if self.last_frame_at else None,
            "last_error": self.last_error,
            "uptime_seconds": round(self.uptime_seconds, 2),
            "started_at": self.started_at.isoformat() if self.started_at else None,
        }


class CameraStreamWorker:
    """Threaded per-camera worker that reads frames from an RTSP/video source.

    Maintains a bounded deque of frames (drop-oldest when full), handles
    auto-reconnection with exponential backoff, and reports health metrics
    to Redis at a configurable interval.

    Args:
        camera_id: Unique identifier of the camera.
        stream_url: Decrypted stream URL to connect to.
        camera_name: Human-readable camera name for logging.
        max_queue_size: Maximum number of frames to buffer.
    """

    def __init__(
        self,
        camera_id: uuid.UUID,
        stream_url: str | int,
        camera_name: str = "",
        max_queue_size: int = MAX_QUEUE_SIZE,
    ) -> None:
        self.camera_id = camera_id
        self.stream_url = stream_url
        self.camera_name = camera_name or str(camera_id)[:8]
        self.max_queue_size = max_queue_size

        self._frames: deque[np.ndarray] = deque(maxlen=max_queue_size)
        self._lock = threading.Lock()
        self._stop_event = threading.Event()
        self._thread: Optional[threading.Thread] = None
        self._metrics = StreamMetrics()
        self._cap: Optional[cv2.VideoCapture] = None

    @property
    def metrics(self) -> StreamMetrics:
        """Return the current stream metrics (thread-safe copy)."""
        return self._metrics

    @property
    def state(self) -> StreamState:
        """Return the current stream state."""
        return self._metrics.state

    @property
    def is_running(self) -> bool:
        """Whether the worker thread is alive and actively reading frames."""
        return (
            self._thread is not None
            and self._thread.is_alive()
            and self._metrics.state in (StreamState.RUNNING, StreamState.RECONNECTING)
        )

    def get_frame(self) -> Optional[np.ndarray]:
        """Retrieve the most recent frame from the buffer.

        Returns:
            The latest frame as a numpy array, or None if the buffer is empty.
        """
        with self._lock:
            if self._frames:
                return self._frames[-1]
            return None

    def get_all_frames(self) -> list[np.ndarray]:
        """Retrieve and drain all buffered frames.

        Returns:
            List of frames in chronological order. The buffer is cleared.
        """
        with self._lock:
            frames = list(self._frames)
            self._frames.clear()
            return frames

    def start(self) -> None:
        """Start the stream worker thread."""
        if self._thread is not None and self._thread.is_alive():
            logger.warning("Stream worker already running", camera=self.camera_name)
            return

        self._stop_event.clear()
        self._metrics = StreamMetrics(
            state=StreamState.STARTING,
            started_at=datetime.now(timezone.utc),
        )

        self._thread = threading.Thread(
            target=self._run,
            name=f"stream-{self.camera_name}",
            daemon=True,
        )
        self._thread.start()
        logger.info("Stream worker started", camera=self.camera_name)

    def stop(self) -> None:
        """Signal the worker thread to stop and wait for it to terminate."""
        if self._thread is None or not self._thread.is_alive():
            self._metrics.state = StreamState.STOPPED
            return

        logger.info("Stopping stream worker", camera=self.camera_name)
        self._stop_event.set()
        self._thread.join(timeout=10.0)

        if self._thread.is_alive():
            logger.warning("Stream worker did not terminate cleanly", camera=self.camera_name)

        self._metrics.state = StreamState.STOPPED
        self._release_capture()
        logger.info("Stream worker stopped", camera=self.camera_name)

    def _release_capture(self) -> None:
        """Release the OpenCV VideoCapture resource."""
        if self._cap is not None:
            try:
                self._cap.release()
            except Exception:
                pass
            self._cap = None

    def _connect(self) -> bool:
        """Attempt to open the video stream.

        Returns:
            True if the connection was established successfully.
        """
        self._release_capture()

        try:
            url_str = str(self.stream_url).strip()
            if url_str.isdigit() or url_str in ("0", "1", "2", "3"):
                source: int | str = int(url_str)
            else:
                source = self.stream_url

            from app.utils.video_utils import open_opencv_capture
            self._cap = open_opencv_capture(source)
            if self._cap.isOpened():
                self._cap.set(cv2.CAP_PROP_BUFFERSIZE, 1)
                return True

            self._metrics.last_error = "VideoCapture failed to open stream"
            return False

        except Exception as exc:
            self._metrics.last_error = str(exc)
            logger.error("Stream connection error", camera=self.camera_name, error=str(exc))
            return False

    def _run(self) -> None:
        """Main worker loop: connect, read frames, handle reconnection."""
        reconnect_delay = INITIAL_RECONNECT_DELAY
        frame_times: deque[float] = deque(maxlen=30)
        last_health_report = 0.0

        while not self._stop_event.is_set():
            # ── Connect phase ────────────────────────────────────────
            self._metrics.state = StreamState.RECONNECTING if self._metrics.reconnect_count > 0 else StreamState.STARTING

            if not self._connect():
                self._metrics.reconnect_count += 1
                logger.warning(
                    "Stream connection failed, backing off",
                    camera=self.camera_name,
                    delay=reconnect_delay,
                    attempt=self._metrics.reconnect_count,
                )
                if self._metrics.reconnect_count >= 3:
                    self._metrics.state = StreamState.ERROR
                    logger.warning(
                        "Max reconnect attempts reached (3/3), marking ERROR state",
                        camera=self.camera_name,
                    )
                    break

                if self._stop_event.wait(timeout=reconnect_delay):
                    break
                reconnect_delay = min(reconnect_delay * RECONNECT_BACKOFF_MULTIPLIER, MAX_RECONNECT_DELAY)
                continue

            # Connection successful -- reset backoff
            reconnect_delay = INITIAL_RECONNECT_DELAY
            self._metrics.state = StreamState.RUNNING
            logger.info("Stream connected", camera=self.camera_name)

            # ── Read loop ────────────────────────────────────────────
            consecutive_failures = 0

            while not self._stop_event.is_set():
                try:
                    ret, frame = self._cap.read()
                except Exception as exc:
                    self._metrics.last_error = str(exc)
                    logger.error("Frame read exception", camera=self.camera_name, error=str(exc))
                    break

                if not ret or frame is None:
                    consecutive_failures += 1
                    if consecutive_failures >= 10:
                        self._metrics.last_error = "Too many consecutive read failures"
                        logger.warning("Too many read failures, reconnecting", camera=self.camera_name)
                        break
                    continue

                consecutive_failures = 0
                now = time.monotonic()

                # Buffer the frame (drop-oldest via deque maxlen)
                with self._lock:
                    if len(self._frames) >= self.max_queue_size:
                        self._metrics.frames_dropped += 1
                    self._frames.append(frame)

                self._metrics.frames_read += 1
                self._metrics.last_frame_at = datetime.now(timezone.utc)

                # FPS calculation
                frame_times.append(now)
                if len(frame_times) >= 2:
                    elapsed = frame_times[-1] - frame_times[0]
                    if elapsed > 0:
                        self._metrics.fps_actual = (len(frame_times) - 1) / elapsed

                # Update uptime
                if self._metrics.started_at:
                    self._metrics.uptime_seconds = (
                        datetime.now(timezone.utc) - self._metrics.started_at
                    ).total_seconds()

                # Periodic health reporting to Redis
                if now - last_health_report >= HEALTH_REPORT_INTERVAL:
                    last_health_report = now
                    self._report_health_to_redis()

            # Exited read loop -- will reconnect
            self._metrics.reconnect_count += 1
            self._metrics.state = StreamState.RECONNECTING

        # Worker exiting
        self._release_capture()
        self._metrics.state = StreamState.STOPPED

    def _report_health_to_redis(self) -> None:
        """Publish stream health metrics to Redis (fire-and-forget)."""
        try:
            import redis as sync_redis

            from app.config import get_settings

            settings = get_settings()
            r = sync_redis.from_url(settings.REDIS_URL, decode_responses=True)
            key = f"stream:health:{self.camera_id}"
            r.hset(key, mapping=self._metrics.to_dict())
            r.expire(key, 60)
            r.close()
        except Exception:
            pass


class StreamManager:
    """Singleton manager orchestrating all active camera stream workers.

    Provides start/stop/restart/status operations and bulk lifecycle
    management for camera feeds.
    """

    _instance: Optional[StreamManager] = None
    _lock = threading.Lock()

    def __init__(self) -> None:
        self._workers: dict[uuid.UUID, CameraStreamWorker] = {}
        self._workers_lock = threading.Lock()

    @classmethod
    def get_instance(cls) -> StreamManager:
        """Return the singleton StreamManager instance (thread-safe)."""
        if cls._instance is None:
            with cls._lock:
                if cls._instance is None:
                    cls._instance = StreamManager()
        return cls._instance

    def start_stream(
        self,
        camera_id: uuid.UUID,
        stream_url: str | int,
        camera_name: str = "",
    ) -> StreamMetrics:
        """Start streaming from a camera.

        If a worker already exists for this camera, it is stopped first.

        Args:
            camera_id: Unique camera identifier.
            stream_url: Decrypted stream URL.
            camera_name: Human-readable camera name.

        Returns:
            Current StreamMetrics for the camera.
        """
        with self._workers_lock:
            existing = self._workers.get(camera_id)
            if existing is not None and existing.is_running:
                existing.stop()

            worker = CameraStreamWorker(
                camera_id=camera_id,
                stream_url=stream_url,
                camera_name=camera_name,
            )
            self._workers[camera_id] = worker
            worker.start()

            logger.info("Stream started via manager", camera=camera_name, camera_id=str(camera_id))
            return worker.metrics

    def stop_stream(self, camera_id: uuid.UUID) -> StreamMetrics:
        """Stop streaming from a camera.

        Args:
            camera_id: Camera to stop.

        Returns:
            Final StreamMetrics for the camera.
        """
        with self._workers_lock:
            worker = self._workers.get(camera_id)
            if worker is None:
                return StreamMetrics(state=StreamState.STOPPED)

            worker.stop()
            logger.info("Stream stopped via manager", camera_id=str(camera_id))
            return worker.metrics

    def restart_stream(
        self,
        camera_id: uuid.UUID,
        stream_url: str | int,
        camera_name: str = "",
    ) -> StreamMetrics:
        """Restart streaming for a camera (stop + start).

        Args:
            camera_id: Camera to restart.
            stream_url: Decrypted stream URL.
            camera_name: Human-readable camera name.

        Returns:
            New StreamMetrics after restart.
        """
        self.stop_stream(camera_id)
        return self.start_stream(camera_id, stream_url, camera_name)

    def get_stream_status(self, camera_id: uuid.UUID) -> StreamMetrics:
        """Get the current status and metrics of a camera stream.

        Args:
            camera_id: Camera to query.

        Returns:
            StreamMetrics for the camera, or a STOPPED metric if not found.
        """
        with self._workers_lock:
            worker = self._workers.get(camera_id)
            if worker is None:
                return StreamMetrics(state=StreamState.STOPPED)
            return worker.metrics

    def get_frame(self, camera_id: uuid.UUID) -> Optional[np.ndarray]:
        """Retrieve the latest frame from a camera's buffer.

        Args:
            camera_id: Camera to get frame from.

        Returns:
            Latest frame as numpy array, or None.
        """
        with self._workers_lock:
            worker = self._workers.get(camera_id)
            if worker is None:
                return None
            return worker.get_frame()

    def get_all_active_streams(self) -> dict[uuid.UUID, StreamMetrics]:
        """Return metrics for all active streams.

        Returns:
            Dict mapping camera_id to StreamMetrics for running workers.
        """
        with self._workers_lock:
            return {
                cam_id: worker.metrics
                for cam_id, worker in self._workers.items()
                if worker.is_running
            }

    def get_all_stream_statuses(self) -> dict[uuid.UUID, StreamMetrics]:
        """Return metrics for all known streams (active and stopped).

        Returns:
            Dict mapping camera_id to StreamMetrics.
        """
        with self._workers_lock:
            return {cam_id: worker.metrics for cam_id, worker in self._workers.items()}

    def stop_all(self) -> int:
        """Stop all active streams.

        Returns:
            Number of streams that were stopped.
        """
        stopped = 0
        with self._workers_lock:
            for cam_id, worker in self._workers.items():
                if worker.is_running:
                    worker.stop()
                    stopped += 1

        logger.info("All streams stopped", count=stopped)
        return stopped

    def cleanup(self) -> int:
        """Remove stopped workers from the registry.

        Returns:
            Number of workers removed.
        """
        removed = 0
        with self._workers_lock:
            to_remove = [
                cam_id
                for cam_id, worker in self._workers.items()
                if not worker.is_running
            ]
            for cam_id in to_remove:
                del self._workers[cam_id]
                removed += 1

        if removed:
            logger.info("Cleaned up stopped workers", count=removed)
        return removed

    @property
    def active_count(self) -> int:
        """Number of currently running stream workers."""
        with self._workers_lock:
            return sum(1 for w in self._workers.values() if w.is_running)

    @property
    def total_count(self) -> int:
        """Total number of registered stream workers (active and stopped)."""
        with self._workers_lock:
            return len(self._workers)


def get_stream_manager() -> StreamManager:
    """Return the singleton StreamManager instance.

    This is the canonical way to access the stream manager from
    anywhere in the application (API routes, background workers, etc.).

    Returns:
        The StreamManager singleton.
    """
    return StreamManager.get_instance()
