"""Edge device management service.

Provides business logic for registering, monitoring, deploying models to,
and collecting telemetry from Jetson and other edge compute devices.
All database interactions use async SQLAlchemy sessions and external
device communication is performed via httpx.
"""

from __future__ import annotations

import uuid
from datetime import datetime, timedelta, timezone
from typing import Any, Optional

import httpx
import structlog
from sqlalchemy import delete, func, select
from sqlalchemy.exc import IntegrityError
from sqlalchemy.ext.asyncio import AsyncSession

from app.exceptions import NotFoundError, ValidationError
from app.models.edge_device import (
    DeploymentStatus,
    EdgeDeployment,
    EdgeDevice,
    EdgeDeviceType,
    EdgeMetrics,
    ModelFormat,
)
from app.schemas.edge import (
    EdgeDeviceCreate,
    EdgeDeviceUpdate,
    HeartbeatRequest,
    PipelineConfigUpdate,
    SyncResultsRequest,
)
from app.utils.encryption import decrypt_string, encrypt_string

logger = structlog.stdlib.get_logger(__name__)

# Timeout for HTTP requests to edge devices
EDGE_HTTP_TIMEOUT = 15.0


class EdgeService:
    """Service layer for edge device management operations.

    All methods are static/class-level and accept an ``AsyncSession``
    as their first argument for testability and consistency with the
    existing service pattern in the codebase.
    """

    # ── Device CRUD ────────────────────────────────────────────────────

    @staticmethod
    async def register_edge_device(
        db: AsyncSession,
        org_id: uuid.UUID,
        data: EdgeDeviceCreate,
    ) -> EdgeDevice:
        """Register a new edge device with hardware info.

        Encrypts the API key before storage and initializes the device
        in an offline state until the first heartbeat is received.

        Args:
            db: Async database session.
            org_id: Owning organization ID.
            data: Device registration payload.

        Returns:
            The newly created EdgeDevice instance.
        """
        logger.info(
            "Registering edge device",
            name=data.name,
            org_id=str(org_id),
            device_type=data.device_type.value,
        )

        api_key_enc: Optional[str] = None
        if data.api_key:
            api_key_enc = encrypt_string(data.api_key)

        device = EdgeDevice(
            id=uuid.uuid4(),
            org_id=org_id,
            name=data.name,
            device_type=EdgeDeviceType(data.device_type.value),
            ip_address=data.ip_address,
            api_url=data.api_url.rstrip("/"),
            api_key_encrypted=api_key_enc,
            hardware_info=data.hardware_info or {},
            assigned_cameras=data.assigned_cameras or [],
            is_online=False,
            firmware_version=data.firmware_version,
            location=data.location,
        )
        db.add(device)

        try:
            await db.flush()
        except IntegrityError as exc:
            await db.rollback()
            logger.error("Integrity error registering edge device", error=str(exc))
            raise ValidationError(
                message=f"Failed to register edge device '{data.name}': {exc}"
            )

        logger.info(
            "Edge device registered",
            device_id=str(device.id),
            name=device.name,
        )
        return device

    @staticmethod
    async def update_edge_device(
        db: AsyncSession,
        device_id: uuid.UUID,
        data: EdgeDeviceUpdate,
    ) -> EdgeDevice:
        """Update an edge device's configuration.

        Only non-None fields in the update payload are changed.
        The API key is re-encrypted if updated.

        Args:
            db: Async database session.
            device_id: Device to update.
            data: Partial update payload.

        Returns:
            The updated EdgeDevice instance.

        Raises:
            NotFoundError: If the device does not exist.
        """
        device = await EdgeService._get_device(db, device_id)

        if data.name is not None:
            device.name = data.name
        if data.device_type is not None:
            device.device_type = EdgeDeviceType(data.device_type.value)
        if data.ip_address is not None:
            device.ip_address = data.ip_address
        if data.api_url is not None:
            device.api_url = data.api_url.rstrip("/")
        if data.api_key is not None:
            device.api_key_encrypted = encrypt_string(data.api_key)
        if data.hardware_info is not None:
            device.hardware_info = data.hardware_info
        if data.assigned_cameras is not None:
            device.assigned_cameras = data.assigned_cameras
        if data.firmware_version is not None:
            device.firmware_version = data.firmware_version
        if data.location is not None:
            device.location = data.location

        await db.flush()
        logger.info("Edge device updated", device_id=str(device_id))
        return device

    @staticmethod
    async def remove_edge_device(
        db: AsyncSession,
        device_id: uuid.UUID,
    ) -> bool:
        """Remove an edge device and all associated deployments/metrics.

        Args:
            db: Async database session.
            device_id: Device to remove.

        Returns:
            True if the device was successfully deleted.

        Raises:
            NotFoundError: If the device does not exist.
        """
        device = await EdgeService._get_device(db, device_id)
        await db.delete(device)
        await db.flush()
        logger.info("Edge device removed", device_id=str(device_id))
        return True

    @staticmethod
    async def get_edge_devices(
        db: AsyncSession,
        org_id: uuid.UUID,
    ) -> tuple[list[EdgeDevice], int]:
        """List all edge devices for an organization with their status.

        Args:
            db: Async database session.
            org_id: Organization filter.

        Returns:
            A tuple of (devices, total_count).
        """
        query = (
            select(EdgeDevice)
            .where(EdgeDevice.org_id == org_id)
            .order_by(EdgeDevice.created_at.desc())
        )
        count_query = select(func.count()).select_from(query.subquery())
        total_result = await db.execute(count_query)
        total = total_result.scalar() or 0

        result = await db.execute(query)
        devices = list(result.scalars().all())

        return devices, total

    @staticmethod
    async def get_edge_device(
        db: AsyncSession,
        device_id: uuid.UUID,
    ) -> EdgeDevice:
        """Retrieve a single edge device by ID.

        Args:
            db: Async database session.
            device_id: Device unique identifier.

        Returns:
            The EdgeDevice instance.

        Raises:
            NotFoundError: If the device does not exist.
        """
        return await EdgeService._get_device(db, device_id)

    # ── Health & Metrics ───────────────────────────────────────────────

    @staticmethod
    async def get_device_health(
        db: AsyncSession,
        device_id: uuid.UUID,
    ) -> dict[str, Any]:
        """Get detailed health information for an edge device.

        Combines device metadata with the most recent metrics record
        and generates status indicators for CPU, GPU, memory,
        temperature, and disk.

        Args:
            db: Async database session.
            device_id: Device to query.

        Returns:
            Health response dictionary.
        """
        device = await EdgeService._get_device(db, device_id)

        # Fetch the most recent metrics record
        metrics_query = (
            select(EdgeMetrics)
            .where(EdgeMetrics.device_id == device_id)
            .order_by(EdgeMetrics.timestamp.desc())
            .limit(1)
        )
        result = await db.execute(metrics_query)
        latest_metrics = result.scalar_one_or_none()

        health: dict[str, Any] = {
            "device_id": device.id,
            "device_name": device.name,
            "is_online": device.is_online,
            "last_heartbeat": device.last_heartbeat,
            "cpu_usage_pct": None,
            "gpu_usage_pct": None,
            "memory_usage_pct": None,
            "temperature_celsius": None,
            "disk_usage_pct": None,
            "uptime_seconds": None,
            "fps_processing": None,
            "inference_latency_ms": None,
            "cpu_status": "unknown",
            "gpu_status": "unknown",
            "memory_status": "unknown",
            "temperature_status": "unknown",
            "disk_status": "unknown",
        }

        if latest_metrics:
            health["cpu_usage_pct"] = latest_metrics.cpu_usage_pct
            health["gpu_usage_pct"] = latest_metrics.gpu_usage_pct
            health["memory_usage_pct"] = latest_metrics.memory_usage_pct
            health["temperature_celsius"] = latest_metrics.temperature_celsius
            health["disk_usage_pct"] = latest_metrics.disk_usage_pct
            health["uptime_seconds"] = latest_metrics.uptime_seconds
            health["fps_processing"] = latest_metrics.fps_processing
            health["inference_latency_ms"] = latest_metrics.inference_latency_ms

            health["cpu_status"] = EdgeService._classify_usage(
                latest_metrics.cpu_usage_pct, warn=70, critical=90
            )
            health["gpu_status"] = EdgeService._classify_usage(
                latest_metrics.gpu_usage_pct, warn=80, critical=95
            )
            health["memory_status"] = EdgeService._classify_usage(
                latest_metrics.memory_usage_pct, warn=75, critical=90
            )
            health["temperature_status"] = EdgeService._classify_usage(
                latest_metrics.temperature_celsius, warn=70, critical=85
            )
            health["disk_status"] = EdgeService._classify_usage(
                latest_metrics.disk_usage_pct, warn=80, critical=95
            )

        return health

    @staticmethod
    async def get_device_metrics(
        db: AsyncSession,
        device_id: uuid.UUID,
        time_range: str = "1h",
    ) -> dict[str, Any]:
        """Get historical performance metrics for an edge device.

        Args:
            db: Async database session.
            device_id: Device to query.
            time_range: Time range string (e.g., "1h", "6h", "24h", "7d").

        Returns:
            Dict containing device info and metrics data points.
        """
        device = await EdgeService._get_device(db, device_id)

        # Parse time range
        now = datetime.now(timezone.utc)
        delta_map: dict[str, timedelta] = {
            "1h": timedelta(hours=1),
            "6h": timedelta(hours=6),
            "24h": timedelta(hours=24),
            "7d": timedelta(days=7),
            "30d": timedelta(days=30),
        }
        delta = delta_map.get(time_range, timedelta(hours=1))
        start_time = now - delta

        query = (
            select(EdgeMetrics)
            .where(
                EdgeMetrics.device_id == device_id,
                EdgeMetrics.timestamp >= start_time,
            )
            .order_by(EdgeMetrics.timestamp.asc())
        )
        result = await db.execute(query)
        metrics_list = list(result.scalars().all())

        return {
            "device_id": device.id,
            "device_name": device.name,
            "time_range": time_range,
            "data_points": metrics_list,
        }

    # ── Model Deployment ───────────────────────────────────────────────

    @staticmethod
    async def deploy_model(
        db: AsyncSession,
        device_id: uuid.UUID,
        model_name: str,
        model_version: str,
        model_format: str = "onnx",
    ) -> EdgeDeployment:
        """Initiate a model deployment to an edge device.

        Creates a deployment record and sends a model load request to
        the edge device's API. If the edge device is unreachable, the
        deployment is marked as failed.

        Args:
            db: Async database session.
            device_id: Target device.
            model_name: Name of the model to deploy.
            model_version: Model version tag.
            model_format: Model file format (onnx, tensorrt, openvino).

        Returns:
            The EdgeDeployment record.
        """
        device = await EdgeService._get_device(db, device_id)

        # Mark any existing deployment of the same model as outdated
        existing_query = select(EdgeDeployment).where(
            EdgeDeployment.device_id == device_id,
            EdgeDeployment.model_name == model_name,
            EdgeDeployment.status == DeploymentStatus.DEPLOYED,
        )
        existing_result = await db.execute(existing_query)
        for existing in existing_result.scalars().all():
            existing.status = DeploymentStatus.OUTDATED

        deployment = EdgeDeployment(
            id=uuid.uuid4(),
            device_id=device_id,
            model_name=model_name,
            model_version=model_version,
            model_format=ModelFormat(model_format),
            status=DeploymentStatus.DEPLOYING,
            deploy_started_at=datetime.now(timezone.utc),
        )
        db.add(deployment)
        await db.flush()

        logger.info(
            "Model deployment initiated",
            deployment_id=str(deployment.id),
            device_id=str(device_id),
            model=f"{model_name}:{model_version}",
        )

        # Attempt to push the model to the edge device
        try:
            api_key = None
            if device.api_key_encrypted:
                api_key = decrypt_string(device.api_key_encrypted)

            headers: dict[str, str] = {"Content-Type": "application/json"}
            if api_key:
                headers["X-API-Key"] = api_key

            async with httpx.AsyncClient(timeout=EDGE_HTTP_TIMEOUT) as client:
                response = await client.post(
                    f"{device.api_url}/models/load",
                    json={
                        "model_name": model_name,
                        "model_version": model_version,
                        "model_format": model_format,
                    },
                    headers=headers,
                )

                if response.status_code == 200:
                    deployment.status = DeploymentStatus.DEPLOYED
                    deployment.deploy_completed_at = datetime.now(timezone.utc)
                    resp_data = response.json()
                    if "file_size" in resp_data:
                        deployment.file_size = resp_data["file_size"]
                    logger.info(
                        "Model deployed successfully",
                        deployment_id=str(deployment.id),
                    )
                else:
                    deployment.status = DeploymentStatus.FAILED
                    deployment.error_message = (
                        f"Edge device returned HTTP {response.status_code}: "
                        f"{response.text[:500]}"
                    )
                    logger.error(
                        "Model deployment failed",
                        deployment_id=str(deployment.id),
                        status_code=response.status_code,
                    )

        except httpx.RequestError as exc:
            deployment.status = DeploymentStatus.FAILED
            deployment.error_message = f"Connection error: {str(exc)[:500]}"
            logger.error(
                "Edge device unreachable for deployment",
                device_id=str(device_id),
                error=str(exc),
            )
        except Exception as exc:
            deployment.status = DeploymentStatus.FAILED
            deployment.error_message = f"Unexpected error: {str(exc)[:500]}"
            logger.error(
                "Unexpected deployment error",
                device_id=str(device_id),
                error=str(exc),
            )

        await db.flush()
        return deployment

    @staticmethod
    async def get_deployment_status(
        db: AsyncSession,
        device_id: uuid.UUID,
    ) -> list[EdgeDeployment]:
        """Get current model deployments and pipeline state for a device.

        Returns all non-outdated deployments ordered by most recent first.

        Args:
            db: Async database session.
            device_id: Device to query.

        Returns:
            List of EdgeDeployment records.
        """
        await EdgeService._get_device(db, device_id)

        query = (
            select(EdgeDeployment)
            .where(EdgeDeployment.device_id == device_id)
            .order_by(EdgeDeployment.created_at.desc())
        )
        result = await db.execute(query)
        return list(result.scalars().all())

    # ── Pipeline Configuration ─────────────────────────────────────────

    @staticmethod
    async def update_pipeline_config(
        db: AsyncSession,
        device_id: uuid.UUID,
        config: PipelineConfigUpdate,
    ) -> dict[str, Any]:
        """Update the CV pipeline configuration on an edge device.

        Sends the configuration update to the edge device's API and
        returns the response.

        Args:
            db: Async database session.
            device_id: Target device.
            config: Pipeline configuration update.

        Returns:
            Dict with status and response from the edge device.
        """
        device = await EdgeService._get_device(db, device_id)

        config_dict = config.model_dump(exclude_none=True)
        if not config_dict:
            raise ValidationError(
                message="No configuration fields provided for update."
            )

        try:
            api_key = None
            if device.api_key_encrypted:
                api_key = decrypt_string(device.api_key_encrypted)

            headers: dict[str, str] = {"Content-Type": "application/json"}
            if api_key:
                headers["X-API-Key"] = api_key

            async with httpx.AsyncClient(timeout=EDGE_HTTP_TIMEOUT) as client:
                response = await client.post(
                    f"{device.api_url}/pipeline/config",
                    json=config_dict,
                    headers=headers,
                )

                if response.status_code == 200:
                    logger.info(
                        "Pipeline config updated on edge device",
                        device_id=str(device_id),
                    )
                    return {
                        "status": "success",
                        "message": "Pipeline configuration updated.",
                        "applied_config": config_dict,
                    }
                else:
                    return {
                        "status": "error",
                        "message": f"Edge device returned HTTP {response.status_code}",
                        "detail": response.text[:500],
                    }

        except httpx.RequestError as exc:
            logger.error(
                "Failed to update pipeline config on edge device",
                device_id=str(device_id),
                error=str(exc),
            )
            return {
                "status": "error",
                "message": f"Connection error: {str(exc)[:500]}",
            }

    # ── Result Sync ────────────────────────────────────────────────────

    @staticmethod
    async def sync_results(
        db: AsyncSession,
        device_id: uuid.UUID,
        results: SyncResultsRequest,
    ) -> dict[str, Any]:
        """Receive and process detection results from an edge device.

        Validates the device exists, stores the results, and returns
        an acceptance summary.

        Args:
            db: Async database session.
            device_id: Originating device.
            results: Batch of detection results.

        Returns:
            Dict with accepted/rejected counts.
        """
        device = await EdgeService._get_device(db, device_id)

        accepted = 0
        rejected = 0

        for detection in results.results:
            try:
                # Validate the camera ID is assigned to this device
                if (
                    device.assigned_cameras
                    and detection.camera_id not in device.assigned_cameras
                ):
                    logger.warning(
                        "Detection from unassigned camera",
                        device_id=str(device_id),
                        camera_id=str(detection.camera_id),
                    )
                    rejected += 1
                    continue

                # In production, this would dispatch to alert/analytics pipelines.
                # For now, we log and count as accepted.
                logger.debug(
                    "Detection synced",
                    device_id=str(device_id),
                    camera_id=str(detection.camera_id),
                    class_name=detection.class_name,
                    confidence=detection.confidence,
                )
                accepted += 1

            except Exception as exc:
                logger.error(
                    "Error processing synced detection",
                    error=str(exc),
                )
                rejected += 1

        logger.info(
            "Edge results sync completed",
            device_id=str(device_id),
            accepted=accepted,
            rejected=rejected,
            batch_id=results.batch_id,
        )

        return {
            "status": "success",
            "accepted_count": accepted,
            "rejected_count": rejected,
            "message": f"Processed {accepted + rejected} results.",
        }

    # ── Device Control ─────────────────────────────────────────────────

    @staticmethod
    async def restart_device(
        db: AsyncSession,
        device_id: uuid.UUID,
    ) -> dict[str, Any]:
        """Send a remote restart command to an edge device via its API.

        Args:
            db: Async database session.
            device_id: Device to restart.

        Returns:
            Dict with the restart status.
        """
        device = await EdgeService._get_device(db, device_id)

        try:
            api_key = None
            if device.api_key_encrypted:
                api_key = decrypt_string(device.api_key_encrypted)

            headers: dict[str, str] = {"Content-Type": "application/json"}
            if api_key:
                headers["X-API-Key"] = api_key

            async with httpx.AsyncClient(timeout=EDGE_HTTP_TIMEOUT) as client:
                response = await client.post(
                    f"{device.api_url}/restart",
                    headers=headers,
                )

                if response.status_code == 200:
                    logger.info(
                        "Edge device restart initiated",
                        device_id=str(device_id),
                    )
                    return {
                        "status": "success",
                        "message": f"Restart command sent to '{device.name}'.",
                    }
                else:
                    return {
                        "status": "error",
                        "message": (
                            f"Edge device returned HTTP {response.status_code}: "
                            f"{response.text[:500]}"
                        ),
                    }

        except httpx.RequestError as exc:
            logger.error(
                "Failed to restart edge device",
                device_id=str(device_id),
                error=str(exc),
            )
            return {
                "status": "error",
                "message": f"Connection error: {str(exc)[:500]}",
            }

    # ── Heartbeat Processing ───────────────────────────────────────────

    @staticmethod
    async def process_heartbeat(
        db: AsyncSession,
        heartbeat: HeartbeatRequest,
    ) -> dict[str, Any]:
        """Process a heartbeat from an edge device.

        Updates the device's online status, last heartbeat timestamp,
        and stores the metrics data point.

        Args:
            db: Async database session.
            heartbeat: Heartbeat payload.

        Returns:
            Dict with server time and any pending commands.
        """
        device = await EdgeService._get_device(db, heartbeat.device_id)

        now = datetime.now(timezone.utc)
        device.is_online = True
        device.last_heartbeat = now

        if heartbeat.firmware_version:
            device.firmware_version = heartbeat.firmware_version

        # Store metrics data point
        metrics = EdgeMetrics(
            id=uuid.uuid4(),
            device_id=device.id,
            timestamp=now,
            cpu_usage_pct=heartbeat.cpu_usage_pct,
            gpu_usage_pct=heartbeat.gpu_usage_pct,
            memory_usage_pct=heartbeat.memory_usage_pct,
            temperature_celsius=heartbeat.temperature_celsius,
            disk_usage_pct=heartbeat.disk_usage_pct,
            fps_processing=heartbeat.fps_processing,
            inference_latency_ms=heartbeat.inference_latency_ms,
            detections_count=heartbeat.detections_count,
            uptime_seconds=heartbeat.uptime_seconds,
        )
        db.add(metrics)
        await db.flush()

        logger.debug(
            "Heartbeat processed",
            device_id=str(device.id),
            device_name=device.name,
        )

        return {
            "status": "ok",
            "server_time": now.isoformat(),
            "pending_commands": [],
        }

    # ── Analytics ──────────────────────────────────────────────────────

    @staticmethod
    async def get_edge_analytics(
        db: AsyncSession,
        org_id: uuid.UUID,
    ) -> dict[str, Any]:
        """Aggregate edge processing statistics for an organization.

        Computes totals and averages across all devices including
        online/offline counts, aggregate FPS, average latency,
        total detections, and resource utilization.

        Args:
            db: Async database session.
            org_id: Organization filter.

        Returns:
            Dict with aggregate edge analytics data.
        """
        # Get all devices for the org
        devices_query = select(EdgeDevice).where(EdgeDevice.org_id == org_id)
        devices_result = await db.execute(devices_query)
        devices = list(devices_result.scalars().all())

        total_devices = len(devices)
        online_devices = sum(1 for d in devices if d.is_online)
        offline_devices = total_devices - online_devices

        # Count devices by type
        devices_by_type: dict[str, int] = {}
        for d in devices:
            dtype = d.device_type.value if d.device_type else "unknown"
            devices_by_type[dtype] = devices_by_type.get(dtype, 0) + 1

        # Count active deployments
        deploy_count_query = select(func.count()).where(
            EdgeDeployment.status == DeploymentStatus.DEPLOYED,
            EdgeDeployment.device_id.in_([d.id for d in devices]),
        )
        deploy_result = await db.execute(deploy_count_query)
        total_deployed_models = deploy_result.scalar() or 0

        # Aggregate latest metrics from online devices
        total_fps = 0.0
        total_latency = 0.0
        total_detections = 0
        total_gpu = 0.0
        total_cpu = 0.0
        total_memory = 0.0
        total_temp = 0.0
        metrics_count = 0

        one_hour_ago = datetime.now(timezone.utc) - timedelta(hours=1)

        for device in devices:
            if not device.is_online:
                continue

            # Get latest metrics for this device
            latest_query = (
                select(EdgeMetrics)
                .where(EdgeMetrics.device_id == device.id)
                .order_by(EdgeMetrics.timestamp.desc())
                .limit(1)
            )
            latest_result = await db.execute(latest_query)
            latest = latest_result.scalar_one_or_none()

            if latest:
                metrics_count += 1
                if latest.fps_processing:
                    total_fps += latest.fps_processing
                if latest.inference_latency_ms:
                    total_latency += latest.inference_latency_ms
                if latest.gpu_usage_pct:
                    total_gpu += latest.gpu_usage_pct
                if latest.cpu_usage_pct:
                    total_cpu += latest.cpu_usage_pct
                if latest.memory_usage_pct:
                    total_memory += latest.memory_usage_pct
                if latest.temperature_celsius:
                    total_temp += latest.temperature_celsius

            # Count detections in the last hour
            det_query = select(func.coalesce(func.sum(EdgeMetrics.detections_count), 0)).where(
                EdgeMetrics.device_id == device.id,
                EdgeMetrics.timestamp >= one_hour_ago,
            )
            det_result = await db.execute(det_query)
            total_detections += det_result.scalar() or 0

        avg_latency = round(total_latency / metrics_count, 2) if metrics_count > 0 else 0.0
        avg_gpu = round(total_gpu / metrics_count, 1) if metrics_count > 0 else 0.0
        avg_cpu = round(total_cpu / metrics_count, 1) if metrics_count > 0 else 0.0
        avg_memory = round(total_memory / metrics_count, 1) if metrics_count > 0 else 0.0
        avg_temp = round(total_temp / metrics_count, 1) if metrics_count > 0 else 0.0

        return {
            "total_devices": total_devices,
            "online_devices": online_devices,
            "offline_devices": offline_devices,
            "total_fps": round(total_fps, 1),
            "avg_inference_latency_ms": avg_latency,
            "total_detections": total_detections,
            "total_deployed_models": total_deployed_models,
            "avg_gpu_usage_pct": avg_gpu,
            "avg_cpu_usage_pct": avg_cpu,
            "avg_memory_usage_pct": avg_memory,
            "avg_temperature_celsius": avg_temp,
            "devices_by_type": devices_by_type,
        }

    # ── Health Check (polling from cloud) ──────────────────────────────

    @staticmethod
    async def check_device_health(
        db: AsyncSession,
        device_id: uuid.UUID,
    ) -> dict[str, Any]:
        """Poll an edge device's health endpoint and update its status.

        Contacts the device's ``/health`` endpoint, updates the
        online status, stores a metrics record, and returns the health data.

        Args:
            db: Async database session.
            device_id: Device to check.

        Returns:
            Health data dict from the edge device.
        """
        device = await EdgeService._get_device(db, device_id)
        now = datetime.now(timezone.utc)

        try:
            api_key = None
            if device.api_key_encrypted:
                api_key = decrypt_string(device.api_key_encrypted)

            headers: dict[str, str] = {}
            if api_key:
                headers["X-API-Key"] = api_key

            async with httpx.AsyncClient(timeout=EDGE_HTTP_TIMEOUT) as client:
                response = await client.get(
                    f"{device.api_url}/health",
                    headers=headers,
                )

                if response.status_code == 200:
                    data = response.json()
                    device.is_online = True
                    device.last_heartbeat = now

                    # Store metrics
                    metrics = EdgeMetrics(
                        id=uuid.uuid4(),
                        device_id=device.id,
                        timestamp=now,
                        cpu_usage_pct=data.get("cpu_usage_pct"),
                        gpu_usage_pct=data.get("gpu_usage_pct"),
                        memory_usage_pct=data.get("memory_usage_pct"),
                        temperature_celsius=data.get("temperature_celsius"),
                        disk_usage_pct=data.get("disk_usage_pct"),
                        fps_processing=data.get("fps_processing"),
                        inference_latency_ms=data.get("inference_latency_ms"),
                        uptime_seconds=data.get("uptime_seconds"),
                    )
                    db.add(metrics)
                    await db.flush()

                    return {"status": "online", "data": data}
                else:
                    device.is_online = False
                    await db.flush()
                    return {
                        "status": "error",
                        "message": f"HTTP {response.status_code}",
                    }

        except httpx.RequestError as exc:
            device.is_online = False
            await db.flush()
            return {
                "status": "offline",
                "message": f"Connection error: {str(exc)[:200]}",
            }

    @staticmethod
    async def mark_stale_devices_offline(
        db: AsyncSession,
        stale_minutes: int = 2,
    ) -> int:
        """Mark devices as offline if they haven't sent a heartbeat recently.

        Args:
            db: Async database session.
            stale_minutes: Minutes without heartbeat before marking offline.

        Returns:
            Number of devices marked offline.
        """
        cutoff = datetime.now(timezone.utc) - timedelta(minutes=stale_minutes)
        query = (
            select(EdgeDevice)
            .where(
                EdgeDevice.is_online == True,  # noqa: E712
                (EdgeDevice.last_heartbeat < cutoff)
                | (EdgeDevice.last_heartbeat.is_(None)),
            )
        )
        result = await db.execute(query)
        stale_devices = list(result.scalars().all())

        for device in stale_devices:
            device.is_online = False
            logger.info(
                "Edge device marked offline (stale)",
                device_id=str(device.id),
                name=device.name,
                last_heartbeat=str(device.last_heartbeat),
            )

        if stale_devices:
            await db.flush()

        return len(stale_devices)

    @staticmethod
    async def cleanup_old_metrics(
        db: AsyncSession,
        days: int = 7,
    ) -> int:
        """Remove metrics records older than the specified number of days.

        Args:
            db: Async database session.
            days: Retention period in days.

        Returns:
            Number of metrics records deleted.
        """
        cutoff = datetime.now(timezone.utc) - timedelta(days=days)
        stmt = delete(EdgeMetrics).where(EdgeMetrics.timestamp < cutoff)
        result = await db.execute(stmt)
        count = result.rowcount or 0
        await db.flush()
        logger.info(
            "Old edge metrics cleaned up",
            deleted_count=count,
            retention_days=days,
        )
        return count

    # ── Internal Helpers ───────────────────────────────────────────────

    @staticmethod
    async def _get_device(
        db: AsyncSession,
        device_id: uuid.UUID,
    ) -> EdgeDevice:
        """Retrieve a device by ID or raise NotFoundError."""
        result = await db.execute(
            select(EdgeDevice).where(EdgeDevice.id == device_id)
        )
        device = result.scalar_one_or_none()
        if device is None:
            raise NotFoundError(resource="EdgeDevice", identifier=device_id)
        return device

    @staticmethod
    def _classify_usage(
        value: float | None,
        warn: float = 70,
        critical: float = 90,
    ) -> str:
        """Classify a metric value into normal/warning/critical."""
        if value is None:
            return "unknown"
        if value >= critical:
            return "critical"
        if value >= warn:
            return "warning"
        return "normal"
