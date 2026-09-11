"""Edge device management API routes.

Provides REST endpoints for registering, monitoring, deploying models to,
and collecting telemetry from edge compute devices (NVIDIA Jetson, etc.).
"""

from __future__ import annotations

from uuid import UUID

import structlog
from fastapi import APIRouter, Depends, Query
from sqlalchemy.ext.asyncio import AsyncSession

from app.database import get_db_session
from app.dependencies import get_current_active_user, require_role
from app.schemas.edge import (
    DeployModelRequest,
    EdgeAnalyticsResponse,
    EdgeDeploymentResponse,
    EdgeDeviceCreate,
    EdgeDeviceListResponse,
    EdgeDeviceResponse,
    EdgeDeviceUpdate,
    EdgeHealthResponse,
    EdgeMetricsResponse,
    HeartbeatRequest,
    HeartbeatResponse,
    MetricsHistory,
    PipelineConfigUpdate,
    SyncResultsRequest,
    SyncResultsResponse,
)
from app.services.edge_service import EdgeService

logger = structlog.stdlib.get_logger(__name__)

router = APIRouter()


# ── Device CRUD ────────────────────────────────────────────────────────────────


@router.post(
    "/devices",
    response_model=EdgeDeviceResponse,
    status_code=201,
    summary="Register an edge device",
    description="Register a new Jetson or edge compute device with hardware info.",
)
async def register_edge_device(
    data: EdgeDeviceCreate,
    db: AsyncSession = Depends(get_db_session),
    current_user=Depends(require_role(["super_admin", "org_admin"])),
):
    """Register a new edge device."""
    device = await EdgeService.register_edge_device(
        db=db,
        org_id=current_user.org_id,
        data=data,
    )
    return EdgeDeviceResponse.model_validate(device)


@router.get(
    "/devices",
    response_model=EdgeDeviceListResponse,
    summary="List edge devices",
    description="List all edge devices for the current organization with health status.",
)
async def list_edge_devices(
    db: AsyncSession = Depends(get_db_session),
    current_user=Depends(get_current_active_user),
):
    """List all edge devices for the organization."""
    devices, total = await EdgeService.get_edge_devices(
        db=db,
        org_id=current_user.org_id,
    )
    return EdgeDeviceListResponse(
        status="success",
        data=[EdgeDeviceResponse.model_validate(d) for d in devices],
        total=total,
    )


@router.get(
    "/devices/{device_id}",
    response_model=EdgeDeviceResponse,
    summary="Get edge device details",
    description="Retrieve detailed information for a specific edge device.",
)
async def get_edge_device(
    device_id: UUID,
    db: AsyncSession = Depends(get_db_session),
    current_user=Depends(get_current_active_user),
):
    """Get a specific edge device by ID."""
    device = await EdgeService.get_edge_device(db=db, device_id=device_id)
    return EdgeDeviceResponse.model_validate(device)


@router.put(
    "/devices/{device_id}",
    response_model=EdgeDeviceResponse,
    summary="Update edge device",
    description="Update the configuration of an existing edge device.",
)
async def update_edge_device(
    device_id: UUID,
    data: EdgeDeviceUpdate,
    db: AsyncSession = Depends(get_db_session),
    current_user=Depends(require_role(["super_admin", "org_admin"])),
):
    """Update an edge device's configuration."""
    device = await EdgeService.update_edge_device(
        db=db,
        device_id=device_id,
        data=data,
    )
    return EdgeDeviceResponse.model_validate(device)


@router.delete(
    "/devices/{device_id}",
    status_code=204,
    summary="Remove edge device",
    description="Remove an edge device and all associated deployments and metrics.",
)
async def remove_edge_device(
    device_id: UUID,
    db: AsyncSession = Depends(get_db_session),
    current_user=Depends(require_role(["super_admin", "org_admin"])),
):
    """Remove an edge device."""
    await EdgeService.remove_edge_device(db=db, device_id=device_id)
    return None


# ── Health & Metrics ───────────────────────────────────────────────────────────


@router.get(
    "/devices/{device_id}/health",
    response_model=EdgeHealthResponse,
    summary="Get device health",
    description="Get detailed health metrics (CPU, GPU, memory, temp, disk, uptime) for an edge device.",
)
async def get_device_health(
    device_id: UUID,
    db: AsyncSession = Depends(get_db_session),
    current_user=Depends(get_current_active_user),
):
    """Get detailed health for an edge device."""
    health = await EdgeService.get_device_health(db=db, device_id=device_id)
    return EdgeHealthResponse(**health)


@router.get(
    "/devices/{device_id}/metrics",
    response_model=MetricsHistory,
    summary="Get device metrics history",
    description="Retrieve historical performance metrics for an edge device over a time range.",
)
async def get_device_metrics(
    device_id: UUID,
    time_range: str = Query(
        default="1h",
        description="Time range: 1h, 6h, 24h, 7d, 30d.",
        regex="^(1h|6h|24h|7d|30d)$",
    ),
    db: AsyncSession = Depends(get_db_session),
    current_user=Depends(get_current_active_user),
):
    """Get historical metrics for an edge device."""
    metrics_data = await EdgeService.get_device_metrics(
        db=db,
        device_id=device_id,
        time_range=time_range,
    )
    return MetricsHistory(
        device_id=metrics_data["device_id"],
        device_name=metrics_data["device_name"],
        time_range=metrics_data["time_range"],
        data_points=[
            EdgeMetricsResponse.model_validate(m) for m in metrics_data["data_points"]
        ],
    )


# ── Model Deployment ──────────────────────────────────────────────────────────


@router.post(
    "/devices/{device_id}/deploy",
    response_model=EdgeDeploymentResponse,
    status_code=201,
    summary="Deploy model to device",
    description="Push an ONNX/TensorRT model to an edge device via its API.",
)
async def deploy_model(
    device_id: UUID,
    data: DeployModelRequest,
    db: AsyncSession = Depends(get_db_session),
    current_user=Depends(require_role(["super_admin", "org_admin"])),
):
    """Deploy a model to an edge device."""
    deployment = await EdgeService.deploy_model(
        db=db,
        device_id=device_id,
        model_name=data.model_name,
        model_version=data.model_version,
        model_format=data.model_format.value,
    )
    return EdgeDeploymentResponse.model_validate(deployment)


@router.get(
    "/devices/{device_id}/deployments",
    response_model=list[EdgeDeploymentResponse],
    summary="List deployed models",
    description="List all model deployments on an edge device.",
)
async def list_deployments(
    device_id: UUID,
    db: AsyncSession = Depends(get_db_session),
    current_user=Depends(get_current_active_user),
):
    """List all model deployments for a device."""
    deployments = await EdgeService.get_deployment_status(
        db=db,
        device_id=device_id,
    )
    return [EdgeDeploymentResponse.model_validate(d) for d in deployments]


# ── Pipeline Configuration ────────────────────────────────────────────────────


@router.put(
    "/devices/{device_id}/pipeline-config",
    summary="Update pipeline config",
    description="Update the CV pipeline configuration on an edge device.",
)
async def update_pipeline_config(
    device_id: UUID,
    config: PipelineConfigUpdate,
    db: AsyncSession = Depends(get_db_session),
    current_user=Depends(require_role(["super_admin", "org_admin"])),
):
    """Update pipeline configuration on an edge device."""
    result = await EdgeService.update_pipeline_config(
        db=db,
        device_id=device_id,
        config=config,
    )
    return result


# ── Result Synchronization ────────────────────────────────────────────────────


@router.post(
    "/devices/{device_id}/sync",
    response_model=SyncResultsResponse,
    summary="Sync detection results",
    description="Receive detection results from an edge device.",
)
async def sync_results(
    device_id: UUID,
    data: SyncResultsRequest,
    db: AsyncSession = Depends(get_db_session),
):
    """Receive detection results from an edge device.

    This endpoint does not require user authentication as it is called
    by the edge device itself.  API key authentication is handled at
    the edge service level.
    """
    result = await EdgeService.sync_results(
        db=db,
        device_id=device_id,
        results=data,
    )
    return SyncResultsResponse(**result)


# ── Device Control ────────────────────────────────────────────────────────────


@router.post(
    "/devices/{device_id}/restart",
    summary="Restart edge device",
    description="Send a remote restart command to an edge device.",
)
async def restart_device(
    device_id: UUID,
    db: AsyncSession = Depends(get_db_session),
    current_user=Depends(require_role(["super_admin", "org_admin"])),
):
    """Remote restart an edge device."""
    result = await EdgeService.restart_device(db=db, device_id=device_id)
    return result


# ── Analytics ─────────────────────────────────────────────────────────────────


@router.get(
    "/analytics",
    response_model=EdgeAnalyticsResponse,
    summary="Get edge analytics",
    description="Aggregate edge processing statistics for the organization.",
)
async def get_edge_analytics(
    db: AsyncSession = Depends(get_db_session),
    current_user=Depends(get_current_active_user),
):
    """Get aggregate edge deployment statistics."""
    analytics = await EdgeService.get_edge_analytics(
        db=db,
        org_id=current_user.org_id,
    )
    return EdgeAnalyticsResponse(**analytics)


# ── Heartbeat ─────────────────────────────────────────────────────────────────


@router.post(
    "/heartbeat",
    response_model=HeartbeatResponse,
    summary="Accept heartbeat",
    description="Accept a heartbeat from an edge device reporting its current status.",
)
async def accept_heartbeat(
    data: HeartbeatRequest,
    db: AsyncSession = Depends(get_db_session),
):
    """Accept heartbeat from an edge device.

    This endpoint does not require user authentication as it is called
    directly by edge devices.  The device is identified by its device_id
    in the request body.
    """
    from datetime import datetime, timezone

    result = await EdgeService.process_heartbeat(db=db, heartbeat=data)
    return HeartbeatResponse(
        status=result["status"],
        server_time=datetime.now(timezone.utc),
        pending_commands=result.get("pending_commands", []),
    )
