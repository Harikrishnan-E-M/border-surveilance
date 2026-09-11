"""
ONNX Model Registry for VisionAI.

Singleton registry that manages the lifecycle of ONNX Runtime inference
sessions.  Models are loaded once and reused across all pipeline instances
to minimise GPU memory consumption and startup latency.

The registry auto-detects available execution providers (CUDA, TensorRT,
CPU) and selects the optimal provider chain for the current hardware.

Usage::

    from app.cv.model_registry import ModelRegistry

    registry = ModelRegistry()
    registry.load_model("yolov8n", "/opt/visionai/models/yolov8n.onnx")
    session = registry.get_model("yolov8n")
"""

from __future__ import annotations

import threading
from pathlib import Path
from typing import Optional

import numpy as np
import onnxruntime as ort
import structlog

logger = structlog.stdlib.get_logger(__name__)

# Default model catalogue mapping logical names to file names.
# Paths are resolved relative to the configured MODEL_DIR.
DEFAULT_MODEL_CATALOGUE: dict[str, str] = {
    "yolov8n": "yolov8n.onnx",
    "yolov8n_pose": "yolov8n-pose.onnx",
    "scrfd_2.5g": "scrfd_2.5g_bnkps.onnx",
    "arcface_r100": "arcface_r100.onnx",
    "ppe_yolov8s": "ppe_yolov8s.onnx",
    "plate_detector": "plate_detect.onnx",
    "plate_ocr": "plate_ocr.onnx",
    "fire_detector": "fire_yolov8n.onnx",
    "emotion": "emotion_fer.onnx",
    "vehicle_classifier": "vehicle_attr.onnx",
}


class ModelRegistry:
    """Singleton registry that manages ONNX Runtime inference sessions.

    Thread-safe by design: a reentrant lock guards all mutations to the
    internal session dictionary.  Reading a loaded model does not acquire
    the lock for performance reasons (dict reads are atomic in CPython).

    Attributes:
        _sessions: Mapping of model names to loaded ONNX sessions.
        _providers: Ordered list of execution providers to use.
        _session_options: Shared session options for all models.
    """

    _instance: Optional[ModelRegistry] = None
    _init_lock: threading.Lock = threading.Lock()

    def __new__(cls) -> ModelRegistry:
        """Create or return the singleton instance."""
        if cls._instance is None:
            with cls._init_lock:
                if cls._instance is None:
                    instance = super().__new__(cls)
                    instance._initialised = False
                    cls._instance = instance
        return cls._instance

    def __init__(self) -> None:
        if self._initialised:
            return
        self._lock = threading.RLock()
        self._sessions: dict[str, ort.InferenceSession] = {}
        self._model_paths: dict[str, str] = {}
        self._providers = self._resolve_providers()
        self._session_options = self._build_session_options()
        self._initialised = True
        logger.info(
            "model_registry.initialised",
            providers=self._providers,
        )

    # ── Provider Detection ────────────────────────────────────────────

    @staticmethod
    def get_available_providers() -> list[str]:
        """Return the list of ONNX Runtime execution providers available
        on this system.

        Returns:
            list[str]: Provider names such as ``CUDAExecutionProvider``,
                ``TensorrtExecutionProvider``, ``CPUExecutionProvider``.
        """
        return ort.get_available_providers()

    def _resolve_providers(self) -> list[str]:
        """Determine the best provider chain for the current hardware.

        Priority order:
        1. TensorrtExecutionProvider (if available)
        2. CUDAExecutionProvider (if available)
        3. CPUExecutionProvider (always available)

        Returns:
            list[str]: Ordered list of providers to pass to
                ``InferenceSession``.
        """
        available = ort.get_available_providers()
        providers: list[str] = []

        if "TensorrtExecutionProvider" in available:
            providers.append("TensorrtExecutionProvider")
        if "CUDAExecutionProvider" in available:
            providers.append("CUDAExecutionProvider")
        providers.append("CPUExecutionProvider")

        logger.info(
            "model_registry.providers_resolved",
            available=available,
            selected=providers,
        )
        return providers

    @staticmethod
    def _build_session_options() -> ort.SessionOptions:
        """Build shared session options with sensible defaults.

        Returns:
            ort.SessionOptions: Configured session options.
        """
        opts = ort.SessionOptions()
        opts.graph_optimization_level = ort.GraphOptimizationLevel.ORT_ENABLE_ALL
        opts.execution_mode = ort.ExecutionMode.ORT_SEQUENTIAL
        opts.enable_mem_pattern = True
        opts.enable_cpu_mem_arena = True
        # Use half the available cores for intra-op parallelism
        import os
        cpu_count = os.cpu_count() or 4
        opts.intra_op_num_threads = max(1, cpu_count // 2)
        opts.inter_op_num_threads = max(1, cpu_count // 4)
        opts.log_severity_level = 3  # ERROR only
        return opts

    # ── Model Loading ─────────────────────────────────────────────────

    def load_model(
        self,
        name: str,
        path: str | Path,
        *,
        providers: list[str] | None = None,
    ) -> ort.InferenceSession:
        """Load an ONNX model from disk and register it under ``name``.

        If a model with the same name is already loaded, the existing
        session is returned without reloading.

        Args:
            name: Logical name for the model (e.g. ``"yolov8n"``).
            path: Absolute or relative path to the ``.onnx`` file.
            providers: Optional override of execution providers.  If not
                supplied, the auto-detected provider chain is used.

        Returns:
            ort.InferenceSession: The loaded inference session.

        Raises:
            FileNotFoundError: If the model file does not exist.
            RuntimeError: If ONNX Runtime fails to create the session.
        """
        with self._lock:
            if name in self._sessions:
                logger.debug("model_registry.already_loaded", model=name)
                return self._sessions[name]

            model_path = Path(path)
            if not model_path.exists():
                raise FileNotFoundError(
                    f"Model file not found: {model_path}"
                )

            selected_providers = providers or self._providers

            try:
                session = ort.InferenceSession(
                    str(model_path),
                    sess_options=self._session_options,
                    providers=selected_providers,
                )
            except Exception as exc:
                logger.error(
                    "model_registry.load_failed",
                    model=name,
                    path=str(model_path),
                    error=str(exc),
                )
                raise RuntimeError(
                    f"Failed to load ONNX model '{name}' from {model_path}: {exc}"
                ) from exc

            self._sessions[name] = session
            self._model_paths[name] = str(model_path)

            # Log model metadata
            inputs = [
                {"name": inp.name, "shape": inp.shape, "type": inp.type}
                for inp in session.get_inputs()
            ]
            outputs = [
                {"name": out.name, "shape": out.shape, "type": out.type}
                for out in session.get_outputs()
            ]
            active_provider = session.get_providers()[0] if session.get_providers() else "unknown"

            logger.info(
                "model_registry.model_loaded",
                model=name,
                path=str(model_path),
                provider=active_provider,
                inputs=inputs,
                outputs=outputs,
            )

            return session

    def get_model(self, name: str) -> ort.InferenceSession:
        """Retrieve a previously loaded model session by name.

        Args:
            name: Logical name of the model.

        Returns:
            ort.InferenceSession: The inference session.

        Raises:
            KeyError: If no model with the given name has been loaded.
        """
        session = self._sessions.get(name)
        if session is None:
            available = list(self._sessions.keys())
            raise KeyError(
                f"Model '{name}' is not loaded. "
                f"Available models: {available}"
            )
        return session

    def load_all_models(
        self,
        model_dir: str | Path | None = None,
        catalogue: dict[str, str] | None = None,
    ) -> dict[str, bool]:
        """Attempt to load all models from the default catalogue.

        Models that are not found on disk are skipped with a warning
        rather than raising an exception, allowing partial deployments.

        Args:
            model_dir: Base directory containing model files.  Defaults
                to ``/opt/visionai/models``.
            catalogue: Optional override mapping ``{name: filename}``.
                Defaults to ``DEFAULT_MODEL_CATALOGUE``.

        Returns:
            dict[str, bool]: Mapping of model name to load success status.
        """
        if model_dir is None:
            model_dir = Path("/opt/visionai/models")
        else:
            model_dir = Path(model_dir)

        if catalogue is None:
            catalogue = DEFAULT_MODEL_CATALOGUE

        results: dict[str, bool] = {}

        for name, filename in catalogue.items():
            model_path = model_dir / filename
            try:
                self.load_model(name, model_path)
                results[name] = True
            except FileNotFoundError:
                logger.warning(
                    "model_registry.model_not_found",
                    model=name,
                    path=str(model_path),
                )
                results[name] = False
            except RuntimeError as exc:
                logger.error(
                    "model_registry.load_error",
                    model=name,
                    error=str(exc),
                )
                results[name] = False

        loaded = sum(1 for v in results.values() if v)
        total = len(results)
        logger.info(
            "model_registry.load_all_complete",
            loaded=loaded,
            total=total,
            results=results,
        )

        return results

    def unload_model(self, name: str) -> None:
        """Unload a model and free its resources.

        Args:
            name: Logical name of the model to unload.

        Raises:
            KeyError: If no model with the given name is loaded.
        """
        with self._lock:
            if name not in self._sessions:
                raise KeyError(f"Model '{name}' is not loaded.")
            del self._sessions[name]
            del self._model_paths[name]
            logger.info("model_registry.model_unloaded", model=name)

    def unload_all(self) -> None:
        """Unload all models and free their resources."""
        with self._lock:
            count = len(self._sessions)
            self._sessions.clear()
            self._model_paths.clear()
            logger.info("model_registry.all_unloaded", count=count)

    @property
    def loaded_models(self) -> list[str]:
        """Return a list of currently loaded model names."""
        return list(self._sessions.keys())

    def get_model_info(self, name: str) -> dict:
        """Return metadata about a loaded model.

        Args:
            name: Logical name of the model.

        Returns:
            dict: Dictionary containing input/output shapes, provider,
                and file path.

        Raises:
            KeyError: If the model is not loaded.
        """
        session = self.get_model(name)
        return {
            "name": name,
            "path": self._model_paths.get(name, ""),
            "providers": session.get_providers(),
            "inputs": [
                {
                    "name": inp.name,
                    "shape": inp.shape,
                    "type": inp.type,
                }
                for inp in session.get_inputs()
            ],
            "outputs": [
                {
                    "name": out.name,
                    "shape": out.shape,
                    "type": out.type,
                }
                for out in session.get_outputs()
            ],
        }
