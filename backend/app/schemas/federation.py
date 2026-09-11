"""Pydantic schemas for the Multi-Site Federation feature.

Covers site registration and management, synchronisation status,
federated alert aggregation, cross-site dashboards, site comparison
metrics, and cross-site search operations.
"""

from __future__ import annotations


from datetime import datetime
from typing import Any, Optional
from uuid import UUID

from pydantic import BaseModel, ConfigDict, Field, field_validator


# ── Site Schemas ────────────────────────────────────────────────────────────


class SiteCreate(BaseModel):
    """Request body for registering a new remote site."""

    name: str = Field(
        min_length=1,
        max_length=255,
        description="Human-readable site name.",
        examples=["Downtown Headquarters"],
    )
    code: str = Field(
        min_length=1,
        max_length=64,
        pattern=r"^[a-z0-9][a-z0-9\-]*[a-z0-9]$",
        description="Unique URL-safe slug for the site.",
        examples=["downtown-hq"],
    )
    address: str | None = Field(
        default=None,
        max_length=512,
        description="Street address of the site.",
        examples=["123 Main Street"],
    )
    city: str | None = Field(
        default=None,
        max_length=128,
        examples=["New York"],
    )
    state: str | None = Field(
        default=None,
        max_length=128,
        examples=["NY"],
    )
    country: str | None = Field(
        default=None,
        max_length=128,
        examples=["US"],
    )
    latitude: float | None = Field(
        default=None,
        ge=-90.0,
        le=90.0,
        description="GPS latitude.",
        examples=[40.7128],
    )
    longitude: float | None = Field(
        default=None,
        ge=-180.0,
        le=180.0,
        description="GPS longitude.",
        examples=[-74.0060],
    )
    timezone: str = Field(
        default="UTC",
        max_length=64,
        description="IANA timezone identifier.",
        examples=["America/New_York"],
    )
    api_url: str = Field(
        min_length=1,
        max_length=2048,
        description="Base URL of the remote VisionAI API.",
        examples=["https://site2.example.com/api/v1"],
    )
    api_key: str = Field(
        min_length=1,
        description="API key for authenticating with the remote site (will be encrypted at rest).",
    )
    is_primary: bool = Field(
        default=False,
        description="Whether this is the primary federation hub site.",
    )
    metadata_json: dict[str, Any] | None = Field(
        default=None,
        description="Arbitrary metadata (capabilities, version, etc.).",
    )

    @field_validator("api_url")
    @classmethod
    def validate_api_url(cls, v: str) -> str:
        if not v.startswith(("https://", "http://")):
            raise ValueError("API URL must start with https:// or http://")
        return v.rstrip("/")

    @field_validator("code")
    @classmethod
    def validate_code(cls, v: str) -> str:
        if len(v) < 2:
            raise ValueError("Site code must be at least 2 characters long")
        return v.lower()


class SiteUpdate(BaseModel):
    """Request body for updating a site configuration."""

    name: str | None = Field(default=None, min_length=1, max_length=255)
    address: str | None = Field(default=None, max_length=512)
    city: str | None = Field(default=None, max_length=128)
    state: str | None = Field(default=None, max_length=128)
    country: str | None = Field(default=None, max_length=128)
    latitude: float | None = Field(default=None, ge=-90.0, le=90.0)
    longitude: float | None = Field(default=None, ge=-180.0, le=180.0)
    timezone: str | None = Field(default=None, max_length=64)
    api_url: str | None = Field(default=None, min_length=1, max_length=2048)
    api_key: str | None = Field(
        default=None,
        description="New API key (will replace the existing one, encrypted at rest).",
    )
    is_primary: bool | None = Field(default=None)
    metadata_json: dict[str, Any] | None = Field(default=None)

    @field_validator("api_url")
    @classmethod
    def validate_api_url(cls, v: str | None) -> str | None:
        if v is not None and not v.startswith(("https://", "http://")):
            raise ValueError("API URL must start with https:// or http://")
        return v.rstrip("/") if v else v


class SiteResponse(BaseModel):
    """Full site representation returned by read endpoints."""

    model_config = ConfigDict(from_attributes=True)

    id: UUID = Field(description="Site unique identifier.")
    org_id: UUID = Field(description="Owning organization ID.")
    name: str = Field(description="Human-readable site name.")
    code: str = Field(description="Unique URL-safe slug.")
    address: str | None = Field(default=None)
    city: str | None = Field(default=None)
    state: str | None = Field(default=None)
    country: str | None = Field(default=None)
    latitude: float | None = Field(default=None)
    longitude: float | None = Field(default=None)
    timezone: str = Field(description="IANA timezone.")
    api_url: str = Field(description="Remote API base URL.")
    is_primary: bool = Field(description="Whether this is the hub site.")
    is_online: bool = Field(description="Whether the site is currently reachable.")
    last_heartbeat: datetime | None = Field(default=None, description="Last heartbeat timestamp.")
    camera_count: int = Field(default=0, description="Number of cameras at this site.")
    alert_count_today: int = Field(default=0, description="Alert count for the current day.")
    metadata_json: dict[str, Any] | None = Field(default=None)
    created_at: datetime = Field(description="Registration timestamp.")
    updated_at: datetime = Field(description="Last update timestamp.")


class SiteListResponse(BaseModel):
    """Paginated list of sites."""

    status: str = Field(default="success")
    data: list[SiteResponse] = Field(description="Sites for the current page.")
    meta: dict[str, Any] = Field(description="Pagination metadata.")


# ── Sync Schemas ────────────────────────────────────────────────────────────


class SiteSyncStatus(BaseModel):
    """Status of a single sync operation."""

    model_config = ConfigDict(from_attributes=True)

    id: UUID = Field(description="Sync record ID.")
    source_site_id: UUID = Field(description="Source site ID.")
    target_site_id: UUID = Field(description="Target site ID.")
    source_site_name: str | None = Field(default=None, description="Source site name.")
    target_site_name: str | None = Field(default=None, description="Target site name.")
    sync_type: str = Field(description="Data type being synced.")
    last_synced_at: datetime | None = Field(default=None, description="Last sync timestamp.")
    status: str = Field(description="Sync status: syncing, synced, failed.")
    error_message: str | None = Field(default=None)
    records_synced: int = Field(default=0, description="Records transferred.")
    created_at: datetime = Field(description="Sync record creation timestamp.")


class SyncTriggerRequest(BaseModel):
    """Request body for manually triggering a site sync."""

    sync_type: str = Field(
        default="alerts",
        description="Type of data to sync: alerts, cameras, faces, vehicles, analytics.",
        examples=["alerts"],
    )

    @field_validator("sync_type")
    @classmethod
    def validate_sync_type(cls, v: str) -> str:
        valid = {"alerts", "cameras", "faces", "vehicles", "analytics"}
        if v not in valid:
            raise ValueError(f"Invalid sync type '{v}'. Must be one of: {', '.join(sorted(valid))}")
        return v


class SyncHistoryResponse(BaseModel):
    """Paginated sync history for a site."""

    status: str = Field(default="success")
    data: list[SiteSyncStatus] = Field(description="Sync history records.")
    meta: dict[str, Any] = Field(description="Pagination metadata.")


# ── Federated Alert Schemas ────────────────────────────────────────────────


class FederatedAlertResponse(BaseModel):
    """A single federated alert from a remote site."""

    model_config = ConfigDict(from_attributes=True)

    id: UUID = Field(description="Federated alert record ID.")
    org_id: UUID = Field(description="Organization ID.")
    source_site_id: UUID = Field(description="Originating site ID.")
    source_site_name: str | None = Field(default=None, description="Originating site name.")
    source_site_code: str | None = Field(default=None, description="Originating site code.")
    original_alert_id: str = Field(description="Alert ID on the remote site.")
    alert_type: str = Field(description="Alert type.")
    severity: str = Field(description="Severity level.")
    camera_name: str = Field(description="Camera name from remote site.")
    description: str | None = Field(default=None)
    thumbnail_path: str | None = Field(default=None)
    original_created_at: datetime = Field(description="When the alert was created on the remote site.")
    synced_at: datetime = Field(description="When the alert was synced to this hub.")


class FederatedAlertList(BaseModel):
    """Paginated list of federated alerts."""

    status: str = Field(default="success")
    data: list[FederatedAlertResponse] = Field(description="Federated alerts.")
    meta: dict[str, Any] = Field(description="Pagination metadata.")


# ── Federated Dashboard ───────────────────────────────────────────────────


class SiteDashboardStats(BaseModel):
    """Per-site statistics for the federated dashboard."""

    site_id: UUID = Field(description="Site ID.")
    site_name: str = Field(description="Site name.")
    site_code: str = Field(description="Site code.")
    is_online: bool = Field(description="Whether the site is online.")
    camera_count: int = Field(default=0, description="Number of cameras.")
    alert_count_today: int = Field(default=0, description="Alerts today.")
    last_heartbeat: datetime | None = Field(default=None)
    timezone: str = Field(default="UTC")


class FederatedDashboard(BaseModel):
    """Aggregated dashboard statistics across all federated sites."""

    status: str = Field(default="success")
    total_sites: int = Field(description="Total registered sites.")
    sites_online: int = Field(description="Number of sites currently online.")
    sites_offline: int = Field(description="Number of sites currently offline.")
    total_cameras: int = Field(description="Total cameras across all sites.")
    total_alerts_today: int = Field(description="Total alerts today across all sites.")
    total_federated_alerts: int = Field(description="Total federated alerts stored.")
    sites: list[SiteDashboardStats] = Field(description="Per-site breakdown.")


# ── Site Comparison ───────────────────────────────────────────────────────


class SiteMetrics(BaseModel):
    """Metrics for a single site used in side-by-side comparison."""

    site_id: UUID = Field(description="Site ID.")
    site_name: str = Field(description="Site name.")
    site_code: str = Field(description="Site code.")
    camera_count: int = Field(default=0)
    alert_count_today: int = Field(default=0)
    total_federated_alerts: int = Field(default=0, description="Total federated alerts from this site.")
    is_online: bool = Field(default=False)
    last_heartbeat: datetime | None = Field(default=None)


class SiteComparisonResponse(BaseModel):
    """Side-by-side metric comparison across all sites."""

    status: str = Field(default="success")
    data: list[SiteMetrics] = Field(description="Metrics per site.")


# ── Cross-Site Search ─────────────────────────────────────────────────────


class CrossSiteSearchRequest(BaseModel):
    """Request body for searching across all federated sites."""

    query: str = Field(
        min_length=1,
        max_length=512,
        description="Search term: face name, vehicle plate number, or keyword.",
        examples=["ABC-1234"],
    )
    search_type: str = Field(
        default="all",
        description="Type of search: faces, vehicles, alerts, or all.",
        examples=["vehicles"],
    )
    limit: int = Field(
        default=20,
        ge=1,
        le=100,
        description="Maximum results per site.",
    )

    @field_validator("search_type")
    @classmethod
    def validate_search_type(cls, v: str) -> str:
        valid = {"faces", "vehicles", "alerts", "all"}
        if v not in valid:
            raise ValueError(f"Invalid search type '{v}'. Must be one of: {', '.join(sorted(valid))}")
        return v


class CrossSiteSearchResultItem(BaseModel):
    """A single search result from a federated site."""

    site_id: UUID = Field(description="Site where the result was found.")
    site_name: str = Field(description="Site name.")
    site_code: str = Field(description="Site code.")
    result_type: str = Field(description="Type of match: face, vehicle, alert.")
    result_id: str = Field(description="ID of the matched record on the remote site.")
    title: str = Field(description="Display title of the match.")
    description: str | None = Field(default=None)
    confidence: float | None = Field(default=None, description="Match confidence if applicable.")
    thumbnail_url: str | None = Field(default=None)
    matched_at: datetime | None = Field(default=None, description="Timestamp of the matching event.")
    metadata: dict[str, Any] | None = Field(default=None, description="Additional match details.")


class CrossSiteSearchResult(BaseModel):
    """Aggregated cross-site search results."""

    status: str = Field(default="success")
    query: str = Field(description="The original search query.")
    search_type: str = Field(description="Type of search performed.")
    total_results: int = Field(description="Total number of results across all sites.")
    sites_searched: int = Field(description="Number of sites that were queried.")
    sites_responded: int = Field(description="Number of sites that responded successfully.")
    results: list[CrossSiteSearchResultItem] = Field(description="Search results.")


# ── Heartbeat ─────────────────────────────────────────────────────────────


class HeartbeatPayload(BaseModel):
    """Payload received from a remote site's heartbeat ping."""

    site_code: str = Field(
        min_length=1,
        description="Unique code of the reporting site.",
    )
    camera_count: int = Field(
        default=0,
        ge=0,
        description="Current number of cameras at the site.",
    )
    alert_count_today: int = Field(
        default=0,
        ge=0,
        description="Alert count for the current UTC day.",
    )
    version: str | None = Field(
        default=None,
        description="VisionAI version running on the remote site.",
    )
    metadata: dict[str, Any] | None = Field(
        default=None,
        description="Additional health/status metadata.",
    )


class TestConnectionRequest(BaseModel):
    """Request body for testing connection to a remote site."""

    api_url: str = Field(
        min_length=1,
        description="Base URL to test.",
    )
    api_key: str = Field(
        min_length=1,
        description="API key for authentication.",
    )

    @field_validator("api_url")
    @classmethod
    def validate_api_url(cls, v: str) -> str:
        if not v.startswith(("https://", "http://")):
            raise ValueError("API URL must start with https:// or http://")
        return v.rstrip("/")


class TestConnectionResponse(BaseModel):
    """Result of a connection test to a remote site."""

    status: str = Field(default="success")
    reachable: bool = Field(description="Whether the remote site responded.")
    latency_ms: float | None = Field(default=None, description="Round-trip latency in milliseconds.")
    version: str | None = Field(default=None, description="Remote site VisionAI version.")
    error: str | None = Field(default=None, description="Error message if unreachable.")
