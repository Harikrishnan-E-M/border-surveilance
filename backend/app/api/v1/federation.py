"""Multi-Site Federation API endpoints.

Provides site registration and management, sync operations, federated
dashboards, cross-site alert feeds, site comparison metrics, cross-site
search, and heartbeat endpoints for the federation hub.
"""

from __future__ import annotations

import math
import uuid
from datetime import datetime
from typing import Any

import structlog
from fastapi import APIRouter, Depends, HTTPException, Query, status
from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession

from app.database import get_db_session
from app.exceptions import NotFoundError, ValidationError
from app.middleware.auth import JWTBearer, TokenPayload
from app.models.user import User, UserRole
from app.schemas.common import ErrorResponse, SuccessResponse
from app.schemas.federation import (
    CrossSiteSearchRequest,
    CrossSiteSearchResult,
    CrossSiteSearchResultItem,
    FederatedAlertList,
    FederatedAlertResponse,
    FederatedDashboard,
    HeartbeatPayload,
    SiteComparisonResponse,
    SiteCreate,
    SiteListResponse,
    SiteMetrics,
    SiteResponse,
    SiteSyncStatus,
    SiteUpdate,
    SyncHistoryResponse,
    SyncTriggerRequest,
    TestConnectionRequest,
    TestConnectionResponse,
)
from app.services.federation_service import FederationService

logger = structlog.stdlib.get_logger(__name__)

router = APIRouter()

federation_service = FederationService()


# ── Helpers ──────────────────────────────────────────────────────────────


async def _get_current_user(
    token: TokenPayload = Depends(JWTBearer()),
    db: AsyncSession = Depends(get_db_session),
) -> User:
    """Resolve the current authenticated user."""
    result = await db.execute(
        select(User).where(User.id == uuid.UUID(token.sub), User.is_active.is_(True))
    )
    user = result.scalars().first()
    if not user:
        raise HTTPException(status_code=401, detail="User not found or deactivated.")
    return user


def _require_admin(user: User) -> None:
    """Raise 403 if the user lacks admin/manager role."""
    allowed = {UserRole.SUPER_ADMIN, UserRole.ORG_ADMIN, UserRole.MANAGER}
    if user.role not in allowed:
        raise HTTPException(
            status_code=status.HTTP_403_FORBIDDEN,
            detail="Admin or manager role required for federation management.",
        )


def _site_to_response(site) -> dict[str, Any]:
    """Serialize a Site model to a response dict."""
    return SiteResponse(
        id=site.id,
        org_id=site.org_id,
        name=site.name,
        code=site.code,
        address=site.address,
        city=site.city,
        state=site.state,
        country=site.country,
        latitude=site.latitude,
        longitude=site.longitude,
        timezone=site.timezone,
        api_url=site.api_url,
        is_primary=site.is_primary,
        is_online=site.is_online,
        last_heartbeat=site.last_heartbeat,
        camera_count=site.camera_count,
        alert_count_today=site.alert_count_today,
        metadata_json=site.metadata_json,
        created_at=site.created_at,
        updated_at=site.updated_at,
    ).model_dump(mode="json")


def _sync_to_response(sync) -> dict[str, Any]:
    """Serialize a SiteSync model to a response dict."""
    return SiteSyncStatus(
        id=sync.id,
        source_site_id=sync.source_site_id,
        target_site_id=sync.target_site_id,
        source_site_name=sync.source_site.name if sync.source_site else None,
        target_site_name=sync.target_site.name if sync.target_site else None,
        sync_type=sync.sync_type.value if hasattr(sync.sync_type, "value") else sync.sync_type,
        last_synced_at=sync.last_synced_at,
        status=sync.status.value if hasattr(sync.status, "value") else sync.status,
        error_message=sync.error_message,
        records_synced=sync.records_synced,
        created_at=sync.created_at,
    ).model_dump(mode="json")


# ── Site CRUD ────────────────────────────────────────────────────────────


@router.post(
    "/sites",
    response_model=SuccessResponse,
    status_code=status.HTTP_201_CREATED,
    summary="Register a new federated site",
    responses={
        403: {"model": ErrorResponse},
        409: {"model": ErrorResponse},
        422: {"model": ErrorResponse},
    },
)
async def register_site(
    body: SiteCreate,
    user: User = Depends(_get_current_user),
    db: AsyncSession = Depends(get_db_session),
) -> dict:
    """Register a new remote VisionAI site in the federation."""
    _require_admin(user)

    site = await federation_service.register_site(
        db=db,
        org_id=user.org_id,
        data=body.model_dump(),
    )

    return {
        "status": "success",
        "data": _site_to_response(site),
        "message": f"Site '{site.name}' registered successfully.",
    }


@router.get(
    "/sites",
    response_model=SuccessResponse,
    summary="List all federated sites",
)
async def list_sites(
    page: int = Query(1, ge=1),
    page_size: int = Query(50, ge=1, le=100),
    user: User = Depends(_get_current_user),
    db: AsyncSession = Depends(get_db_session),
) -> dict:
    """Return a paginated list of all sites in the federation."""
    sites, total = await federation_service.get_sites(
        db=db,
        org_id=user.org_id,
        page=page,
        page_size=page_size,
    )

    return {
        "status": "success",
        "data": [_site_to_response(s) for s in sites],
        "meta": {
            "page": page,
            "page_size": page_size,
            "total": total,
            "total_pages": math.ceil(total / page_size) if page_size else 0,
        },
    }


@router.get(
    "/sites/{site_id}",
    response_model=SuccessResponse,
    summary="Get site details",
    responses={404: {"model": ErrorResponse}},
)
async def get_site(
    site_id: uuid.UUID,
    user: User = Depends(_get_current_user),
    db: AsyncSession = Depends(get_db_session),
) -> dict:
    """Retrieve a single federated site by ID."""
    site = await federation_service.get_site(db, site_id, user.org_id)
    return {
        "status": "success",
        "data": _site_to_response(site),
    }


@router.put(
    "/sites/{site_id}",
    response_model=SuccessResponse,
    summary="Update a federated site",
    responses={404: {"model": ErrorResponse}},
)
async def update_site(
    site_id: uuid.UUID,
    body: SiteUpdate,
    user: User = Depends(_get_current_user),
    db: AsyncSession = Depends(get_db_session),
) -> dict:
    """Update an existing federated site configuration."""
    _require_admin(user)

    site = await federation_service.update_site(
        db=db,
        site_id=site_id,
        data=body.model_dump(exclude_none=True),
        org_id=user.org_id,
    )

    return {
        "status": "success",
        "data": _site_to_response(site),
        "message": f"Site '{site.name}' updated successfully.",
    }


@router.delete(
    "/sites/{site_id}",
    response_model=SuccessResponse,
    summary="Remove a federated site",
    responses={404: {"model": ErrorResponse}},
)
async def remove_site(
    site_id: uuid.UUID,
    user: User = Depends(_get_current_user),
    db: AsyncSession = Depends(get_db_session),
) -> dict:
    """Remove a site from the federation and delete its synced data."""
    _require_admin(user)

    await federation_service.remove_site(db, site_id, user.org_id)

    return {
        "status": "success",
        "data": None,
        "message": "Site removed from federation successfully.",
    }


# ── Sync Operations ─────────────────────────────────────────────────────


@router.post(
    "/sites/{site_id}/sync",
    response_model=SuccessResponse,
    summary="Trigger manual sync for a site",
    responses={404: {"model": ErrorResponse}},
)
async def trigger_sync(
    site_id: uuid.UUID,
    body: SyncTriggerRequest | None = None,
    user: User = Depends(_get_current_user),
    db: AsyncSession = Depends(get_db_session),
) -> dict:
    """Trigger a manual synchronisation with a remote site."""
    _require_admin(user)

    sync_type = body.sync_type if body else "alerts"

    # Route to the appropriate sync method
    if sync_type == "alerts":
        sync_record = await federation_service.sync_alerts(db, site_id)
    elif sync_type == "cameras":
        sync_record = await federation_service.sync_cameras(db, site_id)
    elif sync_type == "faces":
        sync_record = await federation_service.sync_faces(db, site_id)
    elif sync_type == "vehicles":
        sync_record = await federation_service.sync_vehicles(db, site_id)
    else:
        # Default: sync alerts
        sync_record = await federation_service.sync_alerts(db, site_id)

    return {
        "status": "success",
        "data": _sync_to_response(sync_record),
        "message": f"Sync ({sync_type}) completed.",
    }


@router.get(
    "/sites/{site_id}/sync-history",
    response_model=SuccessResponse,
    summary="Get sync history for a site",
    responses={404: {"model": ErrorResponse}},
)
async def get_sync_history(
    site_id: uuid.UUID,
    page: int = Query(1, ge=1),
    page_size: int = Query(20, ge=1, le=100),
    user: User = Depends(_get_current_user),
    db: AsyncSession = Depends(get_db_session),
) -> dict:
    """Return paginated sync history for a federated site."""
    # Verify site access
    await federation_service.get_site(db, site_id, user.org_id)

    records, total = await federation_service.get_sync_history(
        db=db,
        site_id=site_id,
        page=page,
        page_size=page_size,
    )

    return {
        "status": "success",
        "data": [_sync_to_response(r) for r in records],
        "meta": {
            "page": page,
            "page_size": page_size,
            "total": total,
            "total_pages": math.ceil(total / page_size) if page_size else 0,
        },
    }


# ── Federated Dashboard ─────────────────────────────────────────────────


@router.get(
    "/dashboard",
    response_model=SuccessResponse,
    summary="Federated dashboard with all-site aggregated stats",
)
async def federated_dashboard(
    user: User = Depends(_get_current_user),
    db: AsyncSession = Depends(get_db_session),
) -> dict:
    """Return aggregated dashboard statistics across all federated sites."""
    dashboard = await federation_service.get_federated_dashboard(db, user.org_id)
    return {
        "status": "success",
        "data": dashboard,
    }


# ── Federated Alerts ────────────────────────────────────────────────────


@router.get(
    "/alerts",
    response_model=SuccessResponse,
    summary="Combined alert feed from all federated sites",
)
async def federated_alerts(
    page: int = Query(1, ge=1),
    page_size: int = Query(20, ge=1, le=100),
    site_id: uuid.UUID | None = Query(None, description="Filter by specific site"),
    severity: str | None = Query(None, description="Filter by severity"),
    alert_type: str | None = Query(None, description="Filter by alert type"),
    start_date: datetime | None = Query(None, description="Start date filter"),
    end_date: datetime | None = Query(None, description="End date filter"),
    user: User = Depends(_get_current_user),
    db: AsyncSession = Depends(get_db_session),
) -> dict:
    """Return a paginated, merged alert feed from all federated sites."""
    filters: dict[str, Any] = {}
    if site_id:
        filters["site_id"] = str(site_id)
    if severity:
        filters["severity"] = severity
    if alert_type:
        filters["alert_type"] = alert_type
    if start_date:
        filters["start_date"] = start_date
    if end_date:
        filters["end_date"] = end_date

    alerts, total = await federation_service.get_federated_alerts(
        db=db,
        org_id=user.org_id,
        filters=filters,
        page=page,
        page_size=page_size,
    )

    return {
        "status": "success",
        "data": alerts,
        "meta": {
            "page": page,
            "page_size": page_size,
            "total": total,
            "total_pages": math.ceil(total / page_size) if page_size else 0,
        },
    }


# ── Cross-Site Search ───────────────────────────────────────────────────


@router.post(
    "/search",
    response_model=SuccessResponse,
    summary="Search for faces/vehicles across all federated sites",
)
async def cross_site_search(
    body: CrossSiteSearchRequest,
    user: User = Depends(_get_current_user),
    db: AsyncSession = Depends(get_db_session),
) -> dict:
    """Search for a face or vehicle plate across all federated sites."""
    result = await federation_service.search_across_sites(
        db=db,
        org_id=user.org_id,
        query=body.query,
        search_type=body.search_type,
        limit=body.limit,
    )

    return {
        "status": "success",
        "data": result,
    }


# ── Site Comparison ─────────────────────────────────────────────────────


@router.get(
    "/comparison",
    response_model=SuccessResponse,
    summary="Site-by-site metric comparison",
)
async def site_comparison(
    user: User = Depends(_get_current_user),
    db: AsyncSession = Depends(get_db_session),
) -> dict:
    """Return side-by-side metric comparison for all federated sites."""
    comparison = await federation_service.get_site_comparison(db, user.org_id)
    return {
        "status": "success",
        "data": comparison,
    }


# ── Heartbeat ───────────────────────────────────────────────────────────


@router.post(
    "/heartbeat",
    response_model=SuccessResponse,
    summary="Accept heartbeat from a remote site",
    responses={404: {"model": ErrorResponse}},
)
async def accept_heartbeat(
    body: HeartbeatPayload,
    db: AsyncSession = Depends(get_db_session),
) -> dict:
    """Accept an incoming heartbeat from a remote federated site.

    This endpoint does not require JWT authentication -- it uses
    the site code to identify the reporting site. In production,
    this should be secured via API key middleware or network policy.
    """
    site = await federation_service.accept_heartbeat(db, body.model_dump())
    return {
        "status": "success",
        "data": _site_to_response(site),
        "message": f"Heartbeat accepted from site '{site.code}'.",
    }


# ── Test Connection ─────────────────────────────────────────────────────


@router.post(
    "/test-connection",
    response_model=SuccessResponse,
    summary="Test connectivity to a remote site",
)
async def test_connection(
    body: TestConnectionRequest,
    user: User = Depends(_get_current_user),
    db: AsyncSession = Depends(get_db_session),
) -> dict:
    """Test connectivity to a remote site without registering it."""
    _require_admin(user)

    result = await FederationService.test_connection(
        api_url=body.api_url,
        api_key=body.api_key,
    )

    return {
        "status": "success",
        "data": result,
    }


# ── Site Heartbeat Check ───────────────────────────────────────────────


@router.post(
    "/sites/{site_id}/heartbeat",
    response_model=SuccessResponse,
    summary="Ping a remote site to check health",
    responses={404: {"model": ErrorResponse}},
)
async def ping_site(
    site_id: uuid.UUID,
    user: User = Depends(_get_current_user),
    db: AsyncSession = Depends(get_db_session),
) -> dict:
    """Ping a remote site to check its health and update status."""
    _require_admin(user)

    result = await federation_service.heartbeat(db, site_id)
    return {
        "status": "success",
        "data": result,
    }
