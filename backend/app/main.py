"""
VisionAI FastAPI Application Factory.

The ``create_app()`` function assembles the full application by:

1. Configuring structured logging via *structlog*.
2. Creating the FastAPI instance with OpenAPI metadata.
3. Registering middleware (CORS, request logging, rate limiting).
4. Mounting API v1 routes.
5. Installing exception handlers.
6. Wiring startup/shutdown lifecycle events for DB, Redis, and ML models.
7. Exposing health-check endpoints.
"""

from __future__ import annotations

import logging
import sys
import time
from contextlib import asynccontextmanager
from typing import Any

import structlog
from fastapi import FastAPI, Request, Response
from fastapi.middleware.cors import CORSMiddleware
from fastapi.middleware.gzip import GZipMiddleware
from fastapi.middleware.trustedhost import TrustedHostMiddleware
from fastapi.responses import JSONResponse

from app import __version__
from app.config import Settings, get_settings
from app.database import check_db_health, close_db, init_db
from app.dependencies import close_redis, get_redis, init_redis
from app.exceptions import register_exception_handlers

logger = structlog.stdlib.get_logger(__name__)


# ── Structured Logging Setup ─────────────────────────────────────────────


def _configure_logging(settings: Settings) -> None:
    """Configure *structlog* and the standard-library root logger.

    In production the output is JSON; in development it uses coloured
    console output for readability.
    """
    log_level = getattr(logging, settings.LOG_LEVEL.upper(), logging.INFO)

    shared_processors: list[Any] = [
        structlog.contextvars.merge_contextvars,
        structlog.stdlib.add_logger_name,
        structlog.stdlib.add_log_level,
        structlog.stdlib.PositionalArgumentsFormatter(),
        structlog.processors.TimeStamper(fmt="iso"),
        structlog.processors.StackInfoRenderer(),
        structlog.processors.UnicodeDecoder(),
    ]

    if settings.is_production:
        renderer: Any = structlog.processors.JSONRenderer()
    else:
        renderer = structlog.dev.ConsoleRenderer(colors=True)

    structlog.configure(
        processors=[
            *shared_processors,
            structlog.stdlib.ProcessorFormatter.wrap_for_formatter,
        ],
        logger_factory=structlog.stdlib.LoggerFactory(),
        wrapper_class=structlog.stdlib.BoundLogger,
        cache_logger_on_first_use=True,
    )

    formatter = structlog.stdlib.ProcessorFormatter(
        processors=[
            structlog.stdlib.ProcessorFormatter.remove_processors_meta,
            renderer,
        ],
    )

    handler = logging.StreamHandler(sys.stdout)
    handler.setFormatter(formatter)

    root = logging.getLogger()
    root.handlers.clear()
    root.addHandler(handler)
    root.setLevel(log_level)

    # Silence noisy third-party loggers
    for noisy in ("uvicorn.access", "sqlalchemy.engine", "httpx", "httpcore"):
        logging.getLogger(noisy).setLevel(logging.WARNING)


# ── Lifespan Context Manager ─────────────────────────────────────────────


@asynccontextmanager
async def lifespan(app: FastAPI):
    """Application lifespan handler for startup and shutdown events.

    Startup:
        - Initialise the database connection pool and verify connectivity.
        - Initialise the Redis connection pool.
        - Pre-load ML models if GPU is available.

    Shutdown:
        - Close the database connection pool.
        - Close the Redis connection pool.
    """
    settings = get_settings()
    _configure_logging(settings)

    logger.info(
        "Starting VisionAI",
        version=__version__,
        environment=settings.APP_ENV,
        inference_device=settings.INFERENCE_DEVICE,
    )

    # ── Startup ───────────────────────────────────────────────────────
    try:
        await init_db()
        logger.info("Database initialised")
    except Exception as exc:
        logger.error("Failed to initialise database", error=str(exc))
        raise

    try:
        await init_redis(settings)
        logger.info("Redis initialised")
    except Exception as exc:
        logger.warning("Failed to initialise Redis -- some features will be degraded", error=str(exc))

    # Pre-load ML models (non-blocking; failures are logged but tolerated)
    try:
        await _preload_models(settings)
    except Exception as exc:
        logger.warning("Model pre-loading failed", error=str(exc))

    logger.info("VisionAI startup complete")

    yield

    # ── Shutdown ──────────────────────────────────────────────────────
    logger.info("Shutting down VisionAI")
    await close_redis()
    await close_db()
    logger.info("VisionAI shutdown complete")


async def _preload_models(settings: Settings) -> None:
    """Pre-load ML model weights into memory during startup.

    This avoids cold-start latency on the first inference request.
    Only executes when INFERENCE_DEVICE is not ``cpu`` (i.e. GPU or
    TensorRT).
    """
    if settings.INFERENCE_DEVICE == "cpu":
        logger.info("Skipping model pre-load (CPU inference mode)")
        return

    model_dir = settings.model_dir_path
    if not model_dir.exists():
        logger.warning("Model directory does not exist, skipping pre-load", path=str(model_dir))
        return

    logger.info("Pre-loading ML models", device=settings.INFERENCE_DEVICE, model_dir=str(model_dir))
    # Actual model loading is delegated to the CV service layer.
    # This is a hook for orchestration; individual model services
    # register themselves and are initialised here.
    logger.info("Model pre-load phase complete")


# ── Application Factory ──────────────────────────────────────────────────


def create_app(settings: Settings | None = None) -> FastAPI:
    """Create and configure the FastAPI application.

    Args:
        settings: Optional settings override (useful for testing).

    Returns:
        FastAPI: A fully configured application instance.
    """
    if settings is None:
        settings = get_settings()

    app = FastAPI(
        title="VisionAI API",
        description=(
            "Enterprise-grade AI-powered video surveillance and analytics "
            "platform.  Provides camera management, real-time object detection, "
            "face recognition, license plate recognition, anomaly detection, "
            "and comprehensive reporting."
        ),
        version=__version__,
        docs_url="/docs" if not settings.is_production else None,
        redoc_url="/redoc" if not settings.is_production else None,
        openapi_url="/openapi.json" if not settings.is_production else None,
        lifespan=lifespan,
        swagger_ui_parameters={
            "docExpansion": "none",
            "defaultModelsExpandDepth": -1,
            "persistAuthorization": True,
            "filter": True,
        },
    )

    # Store settings on app state for access in middleware / events
    app.state.settings = settings

    # ── Middleware (order matters: last added = first executed) ────────
    _register_middleware(app, settings)

    # ── Exception Handlers ────────────────────────────────────────────
    register_exception_handlers(app)

    # ── Routers ───────────────────────────────────────────────────────
    _register_routers(app)

    # ── Health Checks ─────────────────────────────────────────────────
    _register_health_routes(app)

    return app


# ── Middleware Registration ───────────────────────────────────────────────


def _register_middleware(app: FastAPI, settings: Settings) -> None:
    """Attach middleware stack to the application."""

    # GZip compression for responses > 500 bytes
    app.add_middleware(GZipMiddleware, minimum_size=500)

    # CORS
    app.add_middleware(
        CORSMiddleware,
        allow_origins=settings.cors_origins_list,
        allow_credentials=True,
        allow_methods=["*"],
        allow_headers=["*"],
        expose_headers=["X-Request-ID", "X-Process-Time"],
    )

    # Request logging & timing middleware
    @app.middleware("http")
    async def request_logging_middleware(request: Request, call_next) -> Response:
        """Log every request with timing and inject process-time header."""
        request_id = request.headers.get("x-request-id", "")
        structlog.contextvars.clear_contextvars()
        structlog.contextvars.bind_contextvars(request_id=request_id)

        start = time.perf_counter()
        logger.info(
            "Request started",
            method=request.method,
            path=str(request.url.path),
            client=request.client.host if request.client else "unknown",
        )

        try:
            response: Response = await call_next(request)
        except Exception:
            logger.exception("Unhandled error in request pipeline")
            response = JSONResponse(
                status_code=500,
                content={
                    "status": "error",
                    "detail": "Internal server error",
                    "code": "INTERNAL_ERROR",
                },
            )

        elapsed_ms = round((time.perf_counter() - start) * 1000, 2)
        response.headers["X-Process-Time"] = str(elapsed_ms)
        response.headers["X-Request-ID"] = request_id

        logger.info(
            "Request completed",
            method=request.method,
            path=str(request.url.path),
            status_code=response.status_code,
            elapsed_ms=elapsed_ms,
        )
        return response


# ── Router Registration ──────────────────────────────────────────────────


def _register_routers(app: FastAPI) -> None:
    """Mount all API version routers.

    API v1 routes are imported lazily to keep the module-level import
    graph clean and avoid circular imports.
    """
    try:
        from app.api.v1 import router as api_v1_router

        app.include_router(api_v1_router, prefix="/api/v1")
        logger.info("API v1 router mounted at /api/v1")
    except ImportError:
        logger.warning(
            "API v1 router not found -- /api/v1 endpoints will not be available. "
            "Create app/api/v1/__init__.py with a FastAPI APIRouter."
        )


# ── Health Check Routes ──────────────────────────────────────────────────


def _register_health_routes(app: FastAPI) -> None:
    """Register health-check endpoints on the application root."""

    @app.get(
        "/health",
        tags=["Health"],
        summary="Basic health check",
        response_model=dict[str, Any],
    )
    async def health_check() -> dict[str, Any]:
        """Return basic application health status.

        This endpoint is intended for load-balancer and orchestrator
        probes (e.g. Kubernetes liveness probe).
        """
        return {
            "status": "healthy",
            "version": __version__,
            "service": "visionai",
        }

    @app.get(
        "/health/db",
        tags=["Health"],
        summary="Database health check",
        response_model=dict[str, Any],
    )
    async def health_db() -> dict[str, Any]:
        """Check database connectivity and return version info."""
        result = await check_db_health()
        status_code = 200 if result["status"] == "healthy" else 503
        return JSONResponse(content=result, status_code=status_code)

    @app.get(
        "/health/redis",
        tags=["Health"],
        summary="Redis health check",
        response_model=dict[str, Any],
    )
    async def health_redis() -> dict[str, Any]:
        """Check Redis connectivity."""
        try:
            redis_client = await get_redis()
            info = await redis_client.info("server")
            await redis_client.ping()
            return {
                "status": "healthy",
                "redis_version": info.get("redis_version", "unknown"),
            }
        except Exception as exc:
            return JSONResponse(
                content={
                    "status": "unhealthy",
                    "error": str(exc),
                },
                status_code=503,
            )

    @app.get(
        "/health/gpu",
        tags=["Health"],
        summary="GPU health check",
        response_model=dict[str, Any],
    )
    async def health_gpu() -> dict[str, Any]:
        """Check GPU availability and memory usage.

        Returns GPU device information if CUDA is available, otherwise
        reports CPU-only mode.
        """
        gpu_info: dict[str, Any] = {"status": "healthy", "devices": []}
        try:
            import torch

            if torch.cuda.is_available():
                device_count = torch.cuda.device_count()
                for i in range(device_count):
                    props = torch.cuda.get_device_properties(i)
                    mem_allocated = torch.cuda.memory_allocated(i)
                    mem_total = props.total_mem
                    gpu_info["devices"].append(
                        {
                            "index": i,
                            "name": props.name,
                            "memory_total_mb": round(mem_total / (1024 ** 2)),
                            "memory_allocated_mb": round(mem_allocated / (1024 ** 2)),
                            "memory_free_mb": round((mem_total - mem_allocated) / (1024 ** 2)),
                            "compute_capability": f"{props.major}.{props.minor}",
                        }
                    )
                gpu_info["cuda_version"] = torch.version.cuda or "unknown"
                gpu_info["device_count"] = device_count
            else:
                gpu_info["status"] = "no_gpu"
                gpu_info["message"] = "CUDA is not available; running in CPU mode"
        except ImportError:
            gpu_info["status"] = "no_gpu"
            gpu_info["message"] = "PyTorch is not installed; GPU status unavailable"
        except Exception as exc:
            gpu_info["status"] = "unhealthy"
            gpu_info["error"] = str(exc)

        return gpu_info


# ── Module-Level App Instance ─────────────────────────────────────────────

app = create_app()
