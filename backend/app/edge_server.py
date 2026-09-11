"""VisionAI Edge Server -- lightweight FastAPI running ON the edge device.

This server runs on NVIDIA Jetson (or other edge GPU) devices and provides:
  - Frame-level object detection via ONNX Runtime (TensorRT EP)
  - Real-time device health monitoring (GPU temp, CPU, memory, disk)
  - Model lifecycle management (load / unload ONNX models)
  - Pipeline configuration hot-reload
  - Heartbeat reporting to the VisionAI cloud API

It is designed to be as lean as possible: no database driver, no Celery,
no heavy dependencies.  All state is held in-memory.

Environment variables (set at container runtime):
  CLOUD_API_URL  - Cloud VisionAI API base URL
  DEVICE_ID      - UUID assigned during device registration
  API_KEY        - Shared secret for cloud <-> edge authentication
  MODEL_DIR      - Directory where ONNX model files are stored
  EDGE_PORT      - Port to bind (default 8080)
  LOG_LEVEL      - Python log level (default info)
"""

from __future__ import annotations

import asyncio
import base64
import logging
import os
import signal
import sys
import time
import uuid
from contextlib import asynccontextmanager
from datetime import datetime, timezone
from pathlib import Path
from typing import Any

import numpy as np
import psutil
from fastapi import FastAPI, HTTPException, Request
from fastapi.middleware.cors import CORSMiddleware
from fastapi.responses import JSONResponse
from pydantic import BaseModel, Field

# ── Configuration ────────────────────────────────────────────────────────────

CLOUD_API_URL: str = os.getenv("CLOUD_API_URL", "")
DEVICE_ID: str = os.getenv("DEVICE_ID", "")
API_KEY: str = os.getenv("API_KEY", "")
MODEL_DIR: str = os.getenv("MODEL_DIR", "/app/models")
LOG_LEVEL: str = os.getenv("LOG_LEVEL", "info").upper()
HEARTBEAT_INTERVAL: int = int(os.getenv("HEARTBEAT_INTERVAL", "30"))
DEFAULT_CONFIDENCE: float = float(os.getenv("DEFAULT_CONFIDENCE", "0.5"))
MAX_BATCH_SIZE: int = int(os.getenv("MAX_BATCH_SIZE", "1"))

logging.basicConfig(
    level=getattr(logging, LOG_LEVEL, logging.INFO),
    format="%(asctime)s | %(levelname)-7s | %(name)s | %(message)s",
)
logger = logging.getLogger("visionai.edge")

# ── In-memory state ──────────────────────────────────────────────────────────

_start_time: float = time.time()
_inference_sessions: dict[str, Any] = {}  # model_key -> ORT session
_loaded_models: dict[str, dict[str, str]] = {}  # model_key -> {name, version, format, path}
_pipeline_config: dict[str, Any] = {
    "detection_model": None,
    "detection_confidence": DEFAULT_CONFIDENCE,
    "tracking_enabled": False,
    "tracking_algorithm": "bytetrack",
    "max_fps": 30,
    "resize_width": 640,
    "resize_height": 480,
    "classes_of_interest": [],
    "enable_tensorrt": True,
    "batch_size": MAX_BATCH_SIZE,
}
_metrics: dict[str, Any] = {
    "total_frames": 0,
    "total_detections": 0,
    "last_inference_ms": 0.0,
    "avg_inference_ms": 0.0,
    "fps": 0.0,
    "last_frame_time": 0.0,
}
_heartbeat_task: asyncio.Task | None = None


# ── Pydantic request / response models ──────────────────────────────────────


class DetectRequest(BaseModel):
    """Frame submitted for detection."""
    frame_base64: str = Field(description="Base64-encoded JPEG/PNG frame.")
    camera_id: str | None = Field(default=None, description="Source camera UUID.")
    return_annotated: bool = Field(default=False, description="Return annotated frame.")


class Detection(BaseModel):
    """Single detection result."""
    class_name: str
    confidence: float
    bbox: list[float]  # [x1, y1, x2, y2] normalised
    track_id: int | None = None


class DetectResponse(BaseModel):
    """Detection results for a single frame."""
    detections: list[Detection] = []
    inference_ms: float = 0.0
    model_name: str | None = None
    frame_annotated_base64: str | None = None


class ModelLoadRequest(BaseModel):
    """Request to load a model from disk or download from cloud."""
    model_name: str
    model_version: str
    model_format: str = "onnx"


class PipelineConfigRequest(BaseModel):
    """Pipeline configuration update."""
    detection_model: str | None = None
    detection_confidence: float | None = None
    tracking_enabled: bool | None = None
    tracking_algorithm: str | None = None
    max_fps: int | None = None
    resize_width: int | None = None
    resize_height: int | None = None
    classes_of_interest: list[str] | None = None
    enable_tensorrt: bool | None = None
    batch_size: int | None = None


# ── ONNX Runtime helpers ────────────────────────────────────────────────────


def _get_ort_providers() -> list[str | tuple[str, dict]]:
    """Build the execution provider list, preferring TensorRT > CUDA > CPU."""
    providers: list[str | tuple[str, dict]] = []
    if _pipeline_config.get("enable_tensorrt", True):
        providers.append(
            (
                "TensorrtExecutionProvider",
                {
                    "trt_max_workspace_size": 2 << 30,  # 2 GB
                    "trt_fp16_enable": True,
                    "trt_engine_cache_enable": True,
                    "trt_engine_cache_path": os.path.join(MODEL_DIR, "trt_cache"),
                },
            )
        )
    providers.append("CUDAExecutionProvider")
    providers.append("CPUExecutionProvider")
    return providers


def _load_ort_session(model_path: str) -> Any:
    """Load an ONNX model into an ORT InferenceSession."""
    try:
        import onnxruntime as ort

        opts = ort.SessionOptions()
        opts.graph_optimization_level = ort.GraphOptimizationLevel.ORT_ENABLE_ALL
        opts.intra_op_num_threads = 2
        opts.inter_op_num_threads = 2
        opts.enable_mem_pattern = True

        session = ort.InferenceSession(
            model_path,
            sess_options=opts,
            providers=_get_ort_providers(),
        )
        logger.info(
            "Model loaded: %s  providers=%s",
            model_path,
            session.get_providers(),
        )
        return session
    except ImportError:
        logger.warning("onnxruntime not available -- inference disabled")
        return None
    except Exception as exc:
        logger.error("Failed to load model %s: %s", model_path, exc)
        raise


def _run_inference(session: Any, frame: np.ndarray) -> list[Detection]:
    """Run object detection on a single preprocessed frame."""
    if session is None:
        return []

    input_name = session.get_inputs()[0].name
    input_shape = session.get_inputs()[0].shape  # e.g. [1, 3, 640, 640]

    h, w = input_shape[2], input_shape[3]
    orig_h, orig_w = frame.shape[:2]

    # Preprocess: resize, normalise, CHW, add batch dim
    import cv2

    resized = cv2.resize(frame, (w, h))
    blob = resized.astype(np.float32) / 255.0
    blob = blob.transpose(2, 0, 1)  # HWC -> CHW
    blob = np.expand_dims(blob, axis=0)  # add batch

    start = time.perf_counter()
    outputs = session.run(None, {input_name: blob})
    inference_ms = (time.perf_counter() - start) * 1000.0

    _metrics["last_inference_ms"] = round(inference_ms, 2)
    n = _metrics["total_frames"] + 1
    _metrics["avg_inference_ms"] = round(
        ((_metrics["avg_inference_ms"] * _metrics["total_frames"]) + inference_ms) / n,
        2,
    )
    _metrics["total_frames"] = n

    # Parse YOLO-style output [batch, num_detections, 6] or [batch, 6, num_detections]
    detections: list[Detection] = []
    confidence_threshold = _pipeline_config.get("detection_confidence", DEFAULT_CONFIDENCE)
    classes_of_interest = _pipeline_config.get("classes_of_interest", [])

    try:
        output = outputs[0]
        if output.ndim == 3:
            output = output[0]  # remove batch dim

        # Determine orientation: if shape is (6, N), transpose to (N, 6)
        if output.ndim == 2 and output.shape[0] < output.shape[1]:
            output = output.T

        for row in output:
            if len(row) < 6:
                continue
            x1, y1, x2, y2, conf, cls_id = row[:6]
            if float(conf) < confidence_threshold:
                continue

            cls_name = f"class_{int(cls_id)}"
            if classes_of_interest and cls_name not in classes_of_interest:
                continue

            detections.append(
                Detection(
                    class_name=cls_name,
                    confidence=round(float(conf), 4),
                    bbox=[
                        round(float(x1) / w, 4),
                        round(float(y1) / h, 4),
                        round(float(x2) / w, 4),
                        round(float(y2) / h, 4),
                    ],
                )
            )
    except Exception as exc:
        logger.warning("Error parsing inference output: %s", exc)

    _metrics["total_detections"] += len(detections)
    return detections


# ── GPU / system health ─────────────────────────────────────────────────────


def _get_gpu_temperature() -> float | None:
    """Read GPU temperature from Jetson thermal zones or nvidia-smi."""
    # Try Jetson thermal zone first
    thermal_paths = [
        "/sys/devices/virtual/thermal/thermal_zone1/temp",
        "/sys/devices/virtual/thermal/thermal_zone2/temp",
        "/sys/class/thermal/thermal_zone0/temp",
    ]
    for path in thermal_paths:
        try:
            with open(path) as f:
                raw = f.read().strip()
                temp = int(raw) / 1000.0
                if 0 < temp < 120:
                    return round(temp, 1)
        except (FileNotFoundError, ValueError, PermissionError):
            continue

    # Fallback: use psutil sensors
    try:
        temps = psutil.sensors_temperatures()
        for name, entries in temps.items():
            for entry in entries:
                if entry.current and 0 < entry.current < 120:
                    return round(entry.current, 1)
    except Exception:
        pass

    return None


def _get_gpu_usage() -> float | None:
    """Read GPU utilisation from Jetson sysfs or fallback to 0."""
    # Jetson Orin GPU load
    gpu_paths = [
        "/sys/devices/gpu.0/load",
        "/sys/devices/platform/gpu.0/load",
        "/sys/devices/17000000.ga10b/load",
    ]
    for path in gpu_paths:
        try:
            with open(path) as f:
                raw = f.read().strip()
                return round(int(raw) / 10.0, 1)  # Jetson reports 0-1000
        except (FileNotFoundError, ValueError, PermissionError):
            continue
    return None


def _get_system_health() -> dict[str, Any]:
    """Collect comprehensive system health metrics."""
    cpu_pct = psutil.cpu_percent(interval=0.1)
    mem = psutil.virtual_memory()
    disk = psutil.disk_usage("/")
    uptime = time.time() - _start_time

    return {
        "cpu_usage_pct": round(cpu_pct, 1),
        "gpu_usage_pct": _get_gpu_usage(),
        "memory_usage_pct": round(mem.percent, 1),
        "memory_used_mb": round(mem.used / (1024 * 1024), 0),
        "memory_total_mb": round(mem.total / (1024 * 1024), 0),
        "temperature_celsius": _get_gpu_temperature(),
        "disk_usage_pct": round(disk.percent, 1),
        "disk_used_gb": round(disk.used / (1024**3), 1),
        "disk_total_gb": round(disk.total / (1024**3), 1),
        "uptime_seconds": round(uptime, 0),
        "fps_processing": _metrics.get("fps", 0.0),
        "inference_latency_ms": _metrics.get("avg_inference_ms", 0.0),
        "total_frames_processed": _metrics.get("total_frames", 0),
        "total_detections": _metrics.get("total_detections", 0),
        "loaded_models": list(_loaded_models.values()),
    }


# ── Heartbeat background task ───────────────────────────────────────────────


async def _heartbeat_loop():
    """Periodically report status to the VisionAI cloud API."""
    import httpx

    if not CLOUD_API_URL or not DEVICE_ID:
        logger.info("Cloud API URL or Device ID not set -- heartbeat disabled")
        return

    logger.info(
        "Heartbeat loop started  cloud=%s  device=%s  interval=%ds",
        CLOUD_API_URL,
        DEVICE_ID,
        HEARTBEAT_INTERVAL,
    )

    while True:
        try:
            await asyncio.sleep(HEARTBEAT_INTERVAL)

            health = _get_system_health()
            payload = {
                "device_id": DEVICE_ID,
                "cpu_usage_pct": health["cpu_usage_pct"],
                "gpu_usage_pct": health["gpu_usage_pct"],
                "memory_usage_pct": health["memory_usage_pct"],
                "temperature_celsius": health["temperature_celsius"],
                "disk_usage_pct": health["disk_usage_pct"],
                "fps_processing": health["fps_processing"],
                "inference_latency_ms": health["inference_latency_ms"],
                "detections_count": health["total_detections"],
                "uptime_seconds": health["uptime_seconds"],
                "firmware_version": _get_firmware_version(),
                "deployed_models": health["loaded_models"],
            }

            headers: dict[str, str] = {"Content-Type": "application/json"}
            if API_KEY:
                headers["X-API-Key"] = API_KEY

            async with httpx.AsyncClient(timeout=10.0) as client:
                resp = await client.post(
                    f"{CLOUD_API_URL}/edge/heartbeat",
                    json=payload,
                    headers=headers,
                )
                if resp.status_code == 200:
                    data = resp.json()
                    pending = data.get("pending_commands", [])
                    if pending:
                        logger.info("Received %d pending commands", len(pending))
                        for cmd in pending:
                            await _handle_pending_command(cmd)
                else:
                    logger.warning("Heartbeat returned HTTP %d", resp.status_code)

        except asyncio.CancelledError:
            logger.info("Heartbeat loop cancelled")
            break
        except Exception as exc:
            logger.error("Heartbeat error: %s", exc)


async def _handle_pending_command(cmd: dict[str, Any]):
    """Process a command received from the cloud via heartbeat response."""
    action = cmd.get("action", "")
    logger.info("Processing pending command: %s", action)

    if action == "deploy_model":
        model_name = cmd.get("model_name", "")
        model_version = cmd.get("model_version", "")
        model_format = cmd.get("model_format", "onnx")
        if model_name and model_version:
            _do_load_model(model_name, model_version, model_format)
    elif action == "restart":
        logger.info("Restart command received -- shutting down gracefully")
        os.kill(os.getpid(), signal.SIGTERM)
    elif action == "update_config":
        config = cmd.get("config", {})
        for key, value in config.items():
            if key in _pipeline_config:
                _pipeline_config[key] = value


def _get_firmware_version() -> str:
    """Try to read JetPack / L4T version from the system."""
    version_paths = [
        "/etc/nv_tegra_release",
        "/etc/jetson_release",
    ]
    for path in version_paths:
        try:
            with open(path) as f:
                return f.read().strip()[:100]
        except (FileNotFoundError, PermissionError):
            continue
    return "unknown"


def _do_load_model(model_name: str, model_version: str, model_format: str) -> dict[str, Any]:
    """Load an ONNX model from disk into the inference session cache."""
    model_key = f"{model_name}:{model_version}"

    # Look for the model file on disk
    extensions = {
        "onnx": [".onnx"],
        "tensorrt": [".trt", ".engine"],
        "openvino": [".xml"],
    }
    exts = extensions.get(model_format, [".onnx"])

    model_path: str | None = None
    model_dir = Path(MODEL_DIR)
    for ext in exts:
        candidates = [
            model_dir / f"{model_name}-{model_version}{ext}",
            model_dir / f"{model_name}_{model_version}{ext}",
            model_dir / f"{model_name}{ext}",
            model_dir / model_name / f"{model_version}{ext}",
            model_dir / model_name / f"model{ext}",
        ]
        for candidate in candidates:
            if candidate.exists():
                model_path = str(candidate)
                break
        if model_path:
            break

    if not model_path:
        raise FileNotFoundError(
            f"Model file not found for {model_key} "
            f"(searched {MODEL_DIR} for {exts})"
        )

    # Load with ONNX Runtime
    session = _load_ort_session(model_path)
    _inference_sessions[model_key] = session
    _loaded_models[model_key] = {
        "name": model_name,
        "version": model_version,
        "format": model_format,
        "path": model_path,
    }
    _pipeline_config["detection_model"] = model_key

    file_size = os.path.getsize(model_path)
    logger.info("Model loaded: %s  size=%d bytes", model_key, file_size)

    return {
        "status": "loaded",
        "model_key": model_key,
        "path": model_path,
        "file_size": file_size,
        "providers": session.get_providers() if session else [],
    }


# ── Application lifecycle ───────────────────────────────────────────────────


@asynccontextmanager
async def lifespan(application: FastAPI):
    """Manage startup and shutdown tasks."""
    global _heartbeat_task

    logger.info("VisionAI Edge Server starting up")
    logger.info("Model directory: %s", MODEL_DIR)
    logger.info("Cloud API: %s", CLOUD_API_URL or "(not configured)")
    logger.info("Device ID: %s", DEVICE_ID or "(not configured)")

    # Start heartbeat loop
    _heartbeat_task = asyncio.create_task(_heartbeat_loop())

    yield

    # Shutdown
    logger.info("VisionAI Edge Server shutting down")
    if _heartbeat_task and not _heartbeat_task.done():
        _heartbeat_task.cancel()
        try:
            await _heartbeat_task
        except asyncio.CancelledError:
            pass

    # Release ONNX Runtime sessions
    _inference_sessions.clear()
    _loaded_models.clear()
    logger.info("All resources released")


# ── FastAPI application ─────────────────────────────────────────────────────

app = FastAPI(
    title="VisionAI Edge Server",
    description="Lightweight inference server for NVIDIA Jetson edge devices.",
    version="1.0.0",
    lifespan=lifespan,
)

app.add_middleware(
    CORSMiddleware,
    allow_origins=["*"],
    allow_credentials=True,
    allow_methods=["*"],
    allow_headers=["*"],
)


# ── API Key middleware ──────────────────────────────────────────────────────


@app.middleware("http")
async def verify_api_key(request: Request, call_next):
    """Verify the X-API-Key header for authenticated endpoints."""
    # Health endpoint is always public
    if request.url.path in ("/health", "/docs", "/openapi.json", "/"):
        return await call_next(request)

    if API_KEY:
        provided_key = request.headers.get("X-API-Key", "")
        if provided_key != API_KEY:
            return JSONResponse(
                status_code=401,
                content={"detail": "Invalid or missing API key"},
            )

    return await call_next(request)


# ── Endpoints ───────────────────────────────────────────────────────────────


@app.post("/detect", response_model=DetectResponse)
async def detect(req: DetectRequest):
    """Accept a base64-encoded frame, run CV pipeline, return detections.

    Decodes the frame, runs the currently loaded ONNX model, and
    returns bounding box detections with class labels and confidence scores.
    """
    import cv2

    # Decode frame
    try:
        frame_bytes = base64.b64decode(req.frame_base64)
        nparr = np.frombuffer(frame_bytes, np.uint8)
        frame = cv2.imdecode(nparr, cv2.IMREAD_COLOR)
        if frame is None:
            raise ValueError("Failed to decode image")
    except Exception as exc:
        raise HTTPException(status_code=400, detail=f"Invalid frame data: {exc}")

    # Find active model session
    model_key = _pipeline_config.get("detection_model")
    session = _inference_sessions.get(model_key) if model_key else None

    if session is None:
        # No model loaded -- return empty detections
        return DetectResponse(
            detections=[],
            inference_ms=0.0,
            model_name=model_key,
        )

    # Run inference
    start = time.perf_counter()
    detections = _run_inference(session, frame)
    inference_ms = round((time.perf_counter() - start) * 1000.0, 2)

    # Update FPS tracking
    now = time.time()
    if _metrics["last_frame_time"] > 0:
        delta = now - _metrics["last_frame_time"]
        if delta > 0:
            instant_fps = 1.0 / delta
            # Exponential moving average for FPS
            _metrics["fps"] = round(0.9 * _metrics.get("fps", 0) + 0.1 * instant_fps, 1)
    _metrics["last_frame_time"] = now

    return DetectResponse(
        detections=detections,
        inference_ms=inference_ms,
        model_name=model_key,
    )


@app.get("/health")
async def health():
    """Device health: GPU temp, memory, CPU, disk, uptime."""
    data = _get_system_health()
    data["status"] = "healthy"
    data["device_id"] = DEVICE_ID or None
    data["timestamp"] = datetime.now(timezone.utc).isoformat()
    return data


@app.get("/models")
async def list_models():
    """List all currently loaded models."""
    return {
        "models": list(_loaded_models.values()),
        "active_model": _pipeline_config.get("detection_model"),
        "model_directory": MODEL_DIR,
        "available_files": _list_model_files(),
    }


@app.post("/models/load")
async def load_model(req: ModelLoadRequest):
    """Load an ONNX model from disk into the inference engine.

    The model file must already exist in MODEL_DIR. Naming convention:
      {model_name}-{model_version}.onnx
    or
      {model_name}/{model_version}.onnx
    """
    try:
        result = _do_load_model(req.model_name, req.model_version, req.model_format)
        return result
    except FileNotFoundError as exc:
        raise HTTPException(status_code=404, detail=str(exc))
    except Exception as exc:
        raise HTTPException(status_code=500, detail=f"Failed to load model: {exc}")


@app.post("/pipeline/config")
async def update_pipeline_config(req: PipelineConfigRequest):
    """Update pipeline configuration (hot-reload, no restart needed)."""
    updated_fields: list[str] = []

    for field_name, value in req.model_dump(exclude_none=True).items():
        if field_name in _pipeline_config:
            _pipeline_config[field_name] = value
            updated_fields.append(field_name)

    logger.info("Pipeline config updated: %s", updated_fields)

    return {
        "status": "updated",
        "updated_fields": updated_fields,
        "current_config": _pipeline_config,
    }


@app.get("/metrics")
async def get_metrics():
    """Current processing metrics."""
    return {
        "fps": _metrics.get("fps", 0.0),
        "total_frames": _metrics.get("total_frames", 0),
        "total_detections": _metrics.get("total_detections", 0),
        "avg_inference_ms": _metrics.get("avg_inference_ms", 0.0),
        "last_inference_ms": _metrics.get("last_inference_ms", 0.0),
        "pipeline_config": _pipeline_config,
        "loaded_models_count": len(_loaded_models),
        "timestamp": datetime.now(timezone.utc).isoformat(),
    }


@app.post("/restart")
async def restart():
    """Graceful restart: stop accepting requests, then SIGTERM self."""
    logger.info("Restart requested via API")

    async def _delayed_shutdown():
        await asyncio.sleep(1.0)
        os.kill(os.getpid(), signal.SIGTERM)

    asyncio.create_task(_delayed_shutdown())

    return {
        "status": "restarting",
        "message": "Server will restart in ~1 second.",
    }


@app.post("/heartbeat")
async def send_heartbeat():
    """Manually trigger a heartbeat report to the cloud server."""
    import httpx

    if not CLOUD_API_URL or not DEVICE_ID:
        raise HTTPException(
            status_code=400,
            detail="Cloud API URL or Device ID not configured",
        )

    health = _get_system_health()
    payload = {
        "device_id": DEVICE_ID,
        "cpu_usage_pct": health["cpu_usage_pct"],
        "gpu_usage_pct": health["gpu_usage_pct"],
        "memory_usage_pct": health["memory_usage_pct"],
        "temperature_celsius": health["temperature_celsius"],
        "disk_usage_pct": health["disk_usage_pct"],
        "fps_processing": health["fps_processing"],
        "inference_latency_ms": health["inference_latency_ms"],
        "detections_count": health["total_detections"],
        "uptime_seconds": health["uptime_seconds"],
    }

    headers: dict[str, str] = {"Content-Type": "application/json"}
    if API_KEY:
        headers["X-API-Key"] = API_KEY

    try:
        async with httpx.AsyncClient(timeout=10.0) as client:
            resp = await client.post(
                f"{CLOUD_API_URL}/edge/heartbeat",
                json=payload,
                headers=headers,
            )
            return {
                "status": "sent",
                "cloud_response_status": resp.status_code,
                "cloud_response": resp.json() if resp.status_code == 200 else resp.text[:500],
            }
    except Exception as exc:
        raise HTTPException(status_code=502, detail=f"Cloud API error: {exc}")


@app.get("/")
async def root():
    """Basic info endpoint."""
    return {
        "service": "VisionAI Edge Server",
        "version": "1.0.0",
        "device_id": DEVICE_ID or None,
        "models_loaded": len(_loaded_models),
        "uptime_seconds": round(time.time() - _start_time, 0),
    }


# ── Utility helpers ─────────────────────────────────────────────────────────


def _list_model_files() -> list[dict[str, Any]]:
    """List model files in the MODEL_DIR."""
    model_dir = Path(MODEL_DIR)
    if not model_dir.exists():
        return []

    model_extensions = {".onnx", ".trt", ".engine", ".xml", ".bin"}
    files: list[dict[str, Any]] = []

    for item in sorted(model_dir.rglob("*")):
        if item.is_file() and item.suffix.lower() in model_extensions:
            files.append({
                "name": item.name,
                "path": str(item.relative_to(model_dir)),
                "size_mb": round(item.stat().st_size / (1024 * 1024), 2),
                "format": item.suffix.lstrip("."),
            })

    return files
