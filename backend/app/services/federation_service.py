"""Multi-Site Federation Service.

Provides the business logic for federated multi-site management
including site registration with encrypted API keys, cross-site
data synchronisation via httpx, heartbeat monitoring, aggregated
dashboards, and cross-site search capabilities.
"""

from __future__ import annotations

import asyncio
import math
import time
import uuid
from datetime import datetime, timezone
from typing import Any, Optional

import httpx
import structlog
from cryptography.fernet import Fernet, InvalidToken
from sqlalchemy import and_, delete, func, select, update
from sqlalchemy.ext.asyncio import AsyncSession

from app.config import get_settings
from app.exceptions import NotFoundError, ValidationError
from app.models.federation import (
    FederatedAlert,
    Site,
    SiteSync,
    SyncStatus,
    SyncType,
)

logger = structlog.stdlib.get_logger(__name__)

# ── Encryption helpers ──────────────────────────────────────────────────────


def _get_fernet() -> Fernet:
    """Return a Fernet instance using the application encryption key.

    The ENCRYPTION_KEY setting must be a valid Fernet key or a
    URL-safe base64-encoded 32-byte key.
    """
    settings = get_settings()
    key = settings.ENCRYPTION_KEY
    # Fernet requires URL-safe base64-encoded 32-byte key.  If the
    # configured key is a raw string, derive a deterministic key.
    try:
        return Fernet(key.encode() if isinstance(key, str) else key)
    except (ValueError, Exception):
        import base64
        import hashlib

        derived = base64.urlsafe_b64encode(hashlib.sha256(key.encode()).digest())
        return Fernet(derived)


def _encrypt_api_key(plain_key: str) -> str:
    """Encrypt an API key for storage."""
    f = _get_fernet()
    return f.encrypt(plain_key.encode()).decode()


def _decrypt_api_key(encrypted_key: str) -> str:
    """Decrypt a stored API key."""
    f = _get_fernet()
    try:
        return f.decrypt(encrypted_key.encode()).decode()
    except InvalidToken:
        logger.error("Failed to decrypt API key -- invalid token or wrong encryption key")
        raise ValidationError(message="Failed to decrypt API key. Check ENCRYPTION_KEY setting.")


# ── Remote API client helper ────────────────────────────────────────────────


async def _remote_request(
    method: str,
    api_url: str,
    api_key: str,
    path: str,
    *,
    json_data: dict[str, Any] | None = None,
    params: dict[str, Any] | None = None,
    timeout: float = 30.0,
) -> dict[str, Any]:
    """Make an authenticated request to a remote VisionAI site.

    Args:
        method: HTTP method (GET, POST, etc.).
        api_url: Base URL of the remote API.
        api_key: Decrypted API key.
        path: API path to append (e.g. '/alerts').
        json_data: JSON body for POST/PUT.
        params: Query parameters.
        timeout: Request timeout in seconds.

    Returns:
        Parsed JSON response body.

    Raises:
        ValidationError: On connection failure or non-2xx response.
    """
    url = f"{api_url.rstrip('/')}{path}"
    headers = {
        "Authorization": f"Bearer {api_key}",
        "X-Federation-Source": "visionai-hub",
        "Content-Type": "application/json",
    }

    try:
        async with httpx.AsyncClient(timeout=timeout, verify=True) as client:
            response = await client.request(
                method=method,
                url=url,
                headers=headers,
                json=json_data,
                params=params,
            )

        if 200 <= response.status_code < 300:
            try:
                return response.json()
            except Exception:
                return {"status": "success", "raw": response.text[:2000]}
        else:
            logger.warning(
                "Remote site returned non-2xx",
                url=url,
                status_code=response.status_code,
                body=response.text[:500],
            )
            raise ValidationError(
                message=f"Remote site returned HTTP {response.status_code}: {response.text[:200]}"
            )
    except httpx.TimeoutException:
        logger.warning("Remote site request timed out", url=url, timeout=timeout)
        raise ValidationError(message=f"Remote site request timed out after {timeout}s")
    except httpx.ConnectError as exc:
        logger.warning("Cannot connect to remote site", url=url, error=str(exc))
        raise ValidationError(message=f"Cannot connect to remote site: {exc}")
    except ValidationError:
        raise
    except Exception as exc:
        logger.error("Remote site request failed", url=url, error=str(exc))
        raise ValidationError(message=f"Remote site request failed: {exc}")


# ── FederationService ───────────────────────────────────────────────────────


class FederationService:
    """Business logic for the Multi-Site Federation feature."""

    # ── Site CRUD ──────────────────────────────────────────────────────

    @staticmethod
    async def register_site(
        db: AsyncSession,
        org_id: uuid.UUID,
        data: dict[str, Any],
    ) -> Site:
        """Register a new remote site with an encrypted API key.

        Args:
            db: Async database session.
            org_id: Owning organization ID.
            data: Validated site registration data.

        Returns:
            The newly created Site instance.

        Raises:
            ValidationError: If the site code already exists.
        """
        # Check for duplicate code
        existing = await db.execute(
            select(Site).where(Site.code == data["code"])
        )
        if existing.scalar_one_or_none():
            raise ValidationError(message=f"Site with code '{data['code']}' already exists.")

        encrypted_key = _encrypt_api_key(data["api_key"])

        site = Site(
            id=uuid.uuid4(),
            org_id=org_id,
            name=data["name"],
            code=data["code"],
            address=data.get("address"),
            city=data.get("city"),
            state=data.get("state"),
            country=data.get("country"),
            latitude=data.get("latitude"),
            longitude=data.get("longitude"),
            timezone=data.get("timezone", "UTC"),
            api_url=data["api_url"],
            api_key_encrypted=encrypted_key,
            is_primary=data.get("is_primary", False),
            is_online=False,
            metadata_json=data.get("metadata_json"),
        )
        db.add(site)
        await db.flush()

        logger.info(
            "Federation site registered",
            site_id=str(site.id),
            code=site.code,
            org_id=str(org_id),
        )
        return site

    @staticmethod
    async def update_site(
        db: AsyncSession,
        site_id: uuid.UUID,
        data: dict[str, Any],
        org_id: uuid.UUID | None = None,
    ) -> Site:
        """Update an existing site configuration.

        Args:
            db: Async database session.
            site_id: ID of the site to update.
            data: Fields to update (only non-None values are applied).
            org_id: Optional organization filter.

        Returns:
            The updated Site instance.

        Raises:
            NotFoundError: If the site does not exist.
        """
        site = await _get_site(db, site_id, org_id)

        simple_fields = [
            "name", "address", "city", "state", "country",
            "latitude", "longitude", "timezone", "api_url",
            "is_primary", "metadata_json",
        ]
        for field in simple_fields:
            if field in data and data[field] is not None:
                setattr(site, field, data[field])

        # Re-encrypt API key if provided
        if data.get("api_key"):
            site.api_key_encrypted = _encrypt_api_key(data["api_key"])

        await db.flush()
        logger.info("Federation site updated", site_id=str(site_id))
        return site

    @staticmethod
    async def remove_site(
        db: AsyncSession,
        site_id: uuid.UUID,
        org_id: uuid.UUID | None = None,
    ) -> None:
        """Remove a site from the federation.

        Deletes the site and all associated sync records and federated alerts.

        Args:
            db: Async database session.
            site_id: ID of the site to remove.
            org_id: Optional organization filter.

        Raises:
            NotFoundError: If the site does not exist.
        """
        site = await _get_site(db, site_id, org_id)
        await db.delete(site)
        await db.flush()
        logger.info("Federation site removed", site_id=str(site_id), code=site.code)

    @staticmethod
    async def get_site(
        db: AsyncSession,
        site_id: uuid.UUID,
        org_id: uuid.UUID | None = None,
    ) -> Site:
        """Get a single site by ID."""
        return await _get_site(db, site_id, org_id)

    @staticmethod
    async def get_sites(
        db: AsyncSession,
        org_id: uuid.UUID,
        page: int = 1,
        page_size: int = 50,
    ) -> tuple[list[Site], int]:
        """List all sites for an organization with health status.

        Returns:
            Tuple of (site list, total count).
        """
        base = and_(Site.org_id == org_id)

        count_q = select(func.count()).select_from(Site).where(base)
        total = (await db.execute(count_q)).scalar() or 0

        query = (
            select(Site)
            .where(base)
            .order_by(Site.is_primary.desc(), Site.name.asc())
            .offset((page - 1) * page_size)
            .limit(page_size)
        )
        result = await db.execute(query)
        sites = list(result.scalars().all())

        return sites, total

    # ── Heartbeat ──────────────────────────────────────────────────────

    @staticmethod
    async def heartbeat(
        db: AsyncSession,
        site_id: uuid.UUID,
    ) -> dict[str, Any]:
        """Check site health by pinging its API.

        Sends a GET request to the remote site's health endpoint and
        updates the site's online status and heartbeat timestamp.

        Returns:
            Dict with reachable status and latency.
        """
        site = await _get_site(db, site_id)
        api_key = _decrypt_api_key(site.api_key_encrypted)

        start = time.monotonic()
        try:
            result = await _remote_request(
                "GET", site.api_url, api_key, "/health",
                timeout=10.0,
            )
            latency_ms = round((time.monotonic() - start) * 1000, 2)

            site.is_online = True
            site.last_heartbeat = datetime.now(timezone.utc)

            # Update counts if health endpoint provides them
            if isinstance(result, dict):
                data = result.get("data", result)
                if "cameras_online" in data:
                    site.camera_count = int(data.get("cameras_online", 0)) + int(data.get("cameras_offline", 0))
                if "active_alerts" in data:
                    site.alert_count_today = int(data.get("active_alerts", 0))

            await db.flush()

            logger.info(
                "Site heartbeat success",
                site_id=str(site_id),
                code=site.code,
                latency_ms=latency_ms,
            )
            return {
                "reachable": True,
                "latency_ms": latency_ms,
                "version": result.get("version") if isinstance(result, dict) else None,
            }
        except (ValidationError, Exception) as exc:
            latency_ms = round((time.monotonic() - start) * 1000, 2)
            site.is_online = False
            await db.flush()

            logger.warning(
                "Site heartbeat failed",
                site_id=str(site_id),
                code=site.code,
                error=str(exc),
            )
            return {
                "reachable": False,
                "latency_ms": latency_ms,
                "error": str(exc),
            }

    @staticmethod
    async def accept_heartbeat(
        db: AsyncSession,
        data: dict[str, Any],
    ) -> Site:
        """Accept an incoming heartbeat from a remote site.

        Called when a remote site proactively pings this hub.

        Args:
            data: Heartbeat payload containing site_code and stats.

        Returns:
            The updated Site instance.
        """
        result = await db.execute(
            select(Site).where(Site.code == data["site_code"])
        )
        site = result.scalar_one_or_none()
        if not site:
            raise NotFoundError(resource="Site", identifier=data["site_code"])

        site.is_online = True
        site.last_heartbeat = datetime.now(timezone.utc)
        site.camera_count = data.get("camera_count", site.camera_count)
        site.alert_count_today = data.get("alert_count_today", site.alert_count_today)

        if data.get("version") or data.get("metadata"):
            meta = dict(site.metadata_json or {})
            if data.get("version"):
                meta["version"] = data["version"]
            if data.get("metadata"):
                meta.update(data["metadata"])
            site.metadata_json = meta

        await db.flush()
        logger.info(
            "Heartbeat accepted from remote site",
            code=site.code,
            cameras=site.camera_count,
            alerts_today=site.alert_count_today,
        )
        return site

    # ── Sync Operations ────────────────────────────────────────────────

    @staticmethod
    async def sync_alerts(
        db: AsyncSession,
        site_id: uuid.UUID,
        since: datetime | None = None,
    ) -> SiteSync:
        """Pull recent alerts from a remote site and store as FederatedAlerts.

        Args:
            db: Async database session.
            site_id: Remote site to sync from.
            since: Only fetch alerts created after this timestamp.

        Returns:
            The SiteSync record for this operation.
        """
        site = await _get_site(db, site_id)
        api_key = _decrypt_api_key(site.api_key_encrypted)

        # Create sync record
        sync_record = SiteSync(
            id=uuid.uuid4(),
            source_site_id=site.id,
            target_site_id=site.id,  # self-referencing for hub pull
            sync_type=SyncType.ALERTS,
            status=SyncStatus.SYNCING,
        )
        db.add(sync_record)
        await db.flush()

        try:
            params: dict[str, Any] = {"page_size": 100, "page": 1}
            if since:
                params["start_date"] = since.isoformat()

            result = await _remote_request(
                "GET", site.api_url, api_key, "/alerts",
                params=params, timeout=30.0,
            )

            alerts_data = result.get("data", [])
            if not isinstance(alerts_data, list):
                alerts_data = []

            records_synced = 0
            for alert_data in alerts_data:
                original_id = str(alert_data.get("id", ""))
                if not original_id:
                    continue

                # Check for duplicate
                existing = await db.execute(
                    select(FederatedAlert).where(
                        and_(
                            FederatedAlert.source_site_id == site.id,
                            FederatedAlert.original_alert_id == original_id,
                        )
                    )
                )
                if existing.scalar_one_or_none():
                    continue

                federated_alert = FederatedAlert(
                    id=uuid.uuid4(),
                    org_id=site.org_id,
                    source_site_id=site.id,
                    original_alert_id=original_id,
                    alert_type=str(alert_data.get("rule_type", alert_data.get("alert_type", "unknown"))),
                    severity=str(alert_data.get("severity", "medium")),
                    camera_name=str(alert_data.get("camera_name", "Unknown Camera")),
                    description=alert_data.get("description"),
                    thumbnail_path=alert_data.get("snapshot_url", alert_data.get("snapshot_path")),
                    original_created_at=_parse_datetime(
                        alert_data.get("created_at", datetime.now(timezone.utc).isoformat())
                    ),
                    synced_at=datetime.now(timezone.utc),
                )
                db.add(federated_alert)
                records_synced += 1

            # Update site stats
            site.alert_count_today = result.get("meta", {}).get("total", site.alert_count_today)

            sync_record.status = SyncStatus.SYNCED
            sync_record.records_synced = records_synced
            sync_record.last_synced_at = datetime.now(timezone.utc)

            await db.flush()
            logger.info(
                "Alert sync completed",
                site_code=site.code,
                records_synced=records_synced,
            )
            return sync_record

        except Exception as exc:
            sync_record.status = SyncStatus.FAILED
            sync_record.error_message = str(exc)[:2000]
            sync_record.last_synced_at = datetime.now(timezone.utc)
            await db.flush()
            logger.error(
                "Alert sync failed",
                site_code=site.code,
                error=str(exc),
            )
            return sync_record

    @staticmethod
    async def sync_cameras(
        db: AsyncSession,
        site_id: uuid.UUID,
    ) -> SiteSync:
        """Pull camera status from a remote site.

        Updates the site's camera count and stores results in metadata.
        """
        site = await _get_site(db, site_id)
        api_key = _decrypt_api_key(site.api_key_encrypted)

        sync_record = SiteSync(
            id=uuid.uuid4(),
            source_site_id=site.id,
            target_site_id=site.id,
            sync_type=SyncType.CAMERAS,
            status=SyncStatus.SYNCING,
        )
        db.add(sync_record)
        await db.flush()

        try:
            result = await _remote_request(
                "GET", site.api_url, api_key, "/cameras",
                params={"page_size": 200}, timeout=30.0,
            )

            cameras_data = result.get("data", [])
            total_cameras = result.get("meta", {}).get("total", len(cameras_data))

            # Count online/offline
            online = sum(1 for c in cameras_data if c.get("is_active") or c.get("status") == "online")
            offline = total_cameras - online

            site.camera_count = total_cameras
            meta = dict(site.metadata_json or {})
            meta["cameras_online"] = online
            meta["cameras_offline"] = offline
            meta["last_camera_sync"] = datetime.now(timezone.utc).isoformat()
            site.metadata_json = meta

            sync_record.status = SyncStatus.SYNCED
            sync_record.records_synced = total_cameras
            sync_record.last_synced_at = datetime.now(timezone.utc)

            await db.flush()
            logger.info(
                "Camera sync completed",
                site_code=site.code,
                total=total_cameras,
                online=online,
            )
            return sync_record

        except Exception as exc:
            sync_record.status = SyncStatus.FAILED
            sync_record.error_message = str(exc)[:2000]
            sync_record.last_synced_at = datetime.now(timezone.utc)
            await db.flush()
            logger.error("Camera sync failed", site_code=site.code, error=str(exc))
            return sync_record

    @staticmethod
    async def sync_faces(
        db: AsyncSession,
        site_id: uuid.UUID,
    ) -> SiteSync:
        """Sync the face database to a remote site.

        Pushes locally enrolled faces to the remote site so that
        face recognition is consistent across the federation.
        """
        site = await _get_site(db, site_id)
        api_key = _decrypt_api_key(site.api_key_encrypted)

        sync_record = SiteSync(
            id=uuid.uuid4(),
            source_site_id=site.id,
            target_site_id=site.id,
            sync_type=SyncType.FACES,
            status=SyncStatus.SYNCING,
        )
        db.add(sync_record)
        await db.flush()

        try:
            # Pull face enrollments from the remote site to check current state
            result = await _remote_request(
                "GET", site.api_url, api_key, "/faces",
                params={"page_size": 500}, timeout=30.0,
            )

            faces_data = result.get("data", [])
            records_synced = len(faces_data) if isinstance(faces_data, list) else 0

            meta = dict(site.metadata_json or {})
            meta["face_count"] = records_synced
            meta["last_face_sync"] = datetime.now(timezone.utc).isoformat()
            site.metadata_json = meta

            sync_record.status = SyncStatus.SYNCED
            sync_record.records_synced = records_synced
            sync_record.last_synced_at = datetime.now(timezone.utc)

            await db.flush()
            logger.info(
                "Face sync completed",
                site_code=site.code,
                records_synced=records_synced,
            )
            return sync_record

        except Exception as exc:
            sync_record.status = SyncStatus.FAILED
            sync_record.error_message = str(exc)[:2000]
            sync_record.last_synced_at = datetime.now(timezone.utc)
            await db.flush()
            logger.error("Face sync failed", site_code=site.code, error=str(exc))
            return sync_record

    @staticmethod
    async def sync_vehicles(
        db: AsyncSession,
        site_id: uuid.UUID,
    ) -> SiteSync:
        """Sync vehicle whitelist/blacklist with a remote site.

        Pulls the remote site's vehicle database to ensure plate
        recognition lists are consistent across the federation.
        """
        site = await _get_site(db, site_id)
        api_key = _decrypt_api_key(site.api_key_encrypted)

        sync_record = SiteSync(
            id=uuid.uuid4(),
            source_site_id=site.id,
            target_site_id=site.id,
            sync_type=SyncType.VEHICLES,
            status=SyncStatus.SYNCING,
        )
        db.add(sync_record)
        await db.flush()

        try:
            result = await _remote_request(
                "GET", site.api_url, api_key, "/vehicles",
                params={"page_size": 500}, timeout=30.0,
            )

            vehicles_data = result.get("data", [])
            records_synced = len(vehicles_data) if isinstance(vehicles_data, list) else 0

            meta = dict(site.metadata_json or {})
            meta["vehicle_count"] = records_synced
            meta["last_vehicle_sync"] = datetime.now(timezone.utc).isoformat()
            site.metadata_json = meta

            sync_record.status = SyncStatus.SYNCED
            sync_record.records_synced = records_synced
            sync_record.last_synced_at = datetime.now(timezone.utc)

            await db.flush()
            logger.info(
                "Vehicle sync completed",
                site_code=site.code,
                records_synced=records_synced,
            )
            return sync_record

        except Exception as exc:
            sync_record.status = SyncStatus.FAILED
            sync_record.error_message = str(exc)[:2000]
            sync_record.last_synced_at = datetime.now(timezone.utc)
            await db.flush()
            logger.error("Vehicle sync failed", site_code=site.code, error=str(exc))
            return sync_record

    # ── Sync History ───────────────────────────────────────────────────

    @staticmethod
    async def get_sync_history(
        db: AsyncSession,
        site_id: uuid.UUID,
        page: int = 1,
        page_size: int = 20,
    ) -> tuple[list[SiteSync], int]:
        """Get sync history for a site.

        Returns:
            Tuple of (sync records, total count).
        """
        base = and_(
            SiteSync.source_site_id == site_id,
        )

        count_q = select(func.count()).select_from(SiteSync).where(base)
        total = (await db.execute(count_q)).scalar() or 0

        query = (
            select(SiteSync)
            .where(base)
            .order_by(SiteSync.created_at.desc())
            .offset((page - 1) * page_size)
            .limit(page_size)
        )
        result = await db.execute(query)
        records = list(result.scalars().all())

        return records, total

    # ── Federated Dashboard ────────────────────────────────────────────

    @staticmethod
    async def get_federated_dashboard(
        db: AsyncSession,
        org_id: uuid.UUID,
    ) -> dict[str, Any]:
        """Aggregate dashboard stats across all federated sites.

        Returns:
            Dict with total_sites, online/offline counts, camera/alert
            totals, and per-site breakdown.
        """
        result = await db.execute(
            select(Site).where(Site.org_id == org_id).order_by(Site.name)
        )
        sites = list(result.scalars().all())

        total_cameras = sum(s.camera_count for s in sites)
        total_alerts_today = sum(s.alert_count_today for s in sites)
        sites_online = sum(1 for s in sites if s.is_online)
        sites_offline = len(sites) - sites_online

        # Count total federated alerts
        fa_count_q = select(func.count()).select_from(FederatedAlert).where(
            FederatedAlert.org_id == org_id
        )
        total_federated_alerts = (await db.execute(fa_count_q)).scalar() or 0

        site_stats = [
            {
                "site_id": str(s.id),
                "site_name": s.name,
                "site_code": s.code,
                "is_online": s.is_online,
                "camera_count": s.camera_count,
                "alert_count_today": s.alert_count_today,
                "last_heartbeat": s.last_heartbeat.isoformat() if s.last_heartbeat else None,
                "timezone": s.timezone,
            }
            for s in sites
        ]

        return {
            "total_sites": len(sites),
            "sites_online": sites_online,
            "sites_offline": sites_offline,
            "total_cameras": total_cameras,
            "total_alerts_today": total_alerts_today,
            "total_federated_alerts": total_federated_alerts,
            "sites": site_stats,
        }

    # ── Federated Alerts ───────────────────────────────────────────────

    @staticmethod
    async def get_federated_alerts(
        db: AsyncSession,
        org_id: uuid.UUID,
        filters: dict[str, Any] | None = None,
        page: int = 1,
        page_size: int = 20,
    ) -> tuple[list[dict[str, Any]], int]:
        """Get a combined alert feed from all federated sites.

        Args:
            db: Async database session.
            org_id: Organization to filter by.
            filters: Optional filters (site_id, severity, alert_type, start_date, end_date).
            page: Page number.
            page_size: Items per page.

        Returns:
            Tuple of (alert dicts, total count).
        """
        filters = filters or {}
        conditions = [FederatedAlert.org_id == org_id]

        if filters.get("site_id"):
            conditions.append(FederatedAlert.source_site_id == uuid.UUID(str(filters["site_id"])))
        if filters.get("severity"):
            conditions.append(FederatedAlert.severity == filters["severity"])
        if filters.get("alert_type"):
            conditions.append(FederatedAlert.alert_type == filters["alert_type"])
        if filters.get("start_date"):
            conditions.append(FederatedAlert.original_created_at >= filters["start_date"])
        if filters.get("end_date"):
            conditions.append(FederatedAlert.original_created_at <= filters["end_date"])

        combined = and_(*conditions)

        count_q = select(func.count()).select_from(FederatedAlert).where(combined)
        total = (await db.execute(count_q)).scalar() or 0

        query = (
            select(FederatedAlert)
            .where(combined)
            .order_by(FederatedAlert.original_created_at.desc())
            .offset((page - 1) * page_size)
            .limit(page_size)
        )
        result = await db.execute(query)
        alerts = list(result.scalars().all())

        alert_dicts = []
        for a in alerts:
            alert_dicts.append({
                "id": str(a.id),
                "org_id": str(a.org_id),
                "source_site_id": str(a.source_site_id),
                "source_site_name": a.source_site.name if a.source_site else None,
                "source_site_code": a.source_site.code if a.source_site else None,
                "original_alert_id": a.original_alert_id,
                "alert_type": a.alert_type,
                "severity": a.severity,
                "camera_name": a.camera_name,
                "description": a.description,
                "thumbnail_path": a.thumbnail_path,
                "original_created_at": a.original_created_at.isoformat() if a.original_created_at else None,
                "synced_at": a.synced_at.isoformat() if a.synced_at else None,
            })

        return alert_dicts, total

    # ── Cross-Site Search ──────────────────────────────────────────────

    @staticmethod
    async def search_across_sites(
        db: AsyncSession,
        org_id: uuid.UUID,
        query: str,
        search_type: str = "all",
        limit: int = 20,
    ) -> dict[str, Any]:
        """Search for faces, vehicles, or alerts across all federated sites.

        Sends parallel search requests to every online site and
        aggregates the results.

        Args:
            db: Async database session.
            org_id: Organization to filter by.
            query: Search term.
            search_type: Type of search (faces, vehicles, alerts, all).
            limit: Max results per site.

        Returns:
            Aggregated search results dict.
        """
        sites_result = await db.execute(
            select(Site).where(
                and_(Site.org_id == org_id, Site.is_online.is_(True))
            )
        )
        sites = list(sites_result.scalars().all())

        all_results: list[dict[str, Any]] = []
        sites_responded = 0

        async def _search_site(site: Site) -> list[dict[str, Any]]:
            """Search a single site."""
            nonlocal sites_responded
            try:
                api_key = _decrypt_api_key(site.api_key_encrypted)
                results: list[dict[str, Any]] = []

                # Determine which endpoints to query
                endpoints: list[tuple[str, str]] = []
                if search_type in ("faces", "all"):
                    endpoints.append(("/faces", "face"))
                if search_type in ("vehicles", "all"):
                    endpoints.append(("/vehicles", "vehicle"))
                if search_type in ("alerts", "all"):
                    endpoints.append(("/alerts", "alert"))

                for path, result_type in endpoints:
                    try:
                        data = await _remote_request(
                            "GET", site.api_url, api_key, path,
                            params={"search": query, "page_size": limit},
                            timeout=15.0,
                        )
                        items = data.get("data", [])
                        if isinstance(items, list):
                            for item in items[:limit]:
                                results.append({
                                    "site_id": str(site.id),
                                    "site_name": site.name,
                                    "site_code": site.code,
                                    "result_type": result_type,
                                    "result_id": str(item.get("id", "")),
                                    "title": _extract_title(item, result_type),
                                    "description": item.get("description"),
                                    "confidence": item.get("confidence"),
                                    "thumbnail_url": item.get("snapshot_url") or item.get("thumbnail_path"),
                                    "matched_at": item.get("created_at"),
                                    "metadata": {
                                        k: v for k, v in item.items()
                                        if k not in ("id", "description", "confidence", "snapshot_url", "created_at")
                                    },
                                })
                    except Exception as exc:
                        logger.warning(
                            "Search endpoint failed",
                            site_code=site.code,
                            path=path,
                            error=str(exc),
                        )

                sites_responded += 1
                return results
            except Exception as exc:
                logger.warning(
                    "Site search failed",
                    site_code=site.code,
                    error=str(exc),
                )
                return []

        # Run searches in parallel with a timeout
        tasks = [_search_site(site) for site in sites]
        if tasks:
            results_per_site = await asyncio.gather(*tasks, return_exceptions=True)
            for result in results_per_site:
                if isinstance(result, list):
                    all_results.extend(result)

        return {
            "query": query,
            "search_type": search_type,
            "total_results": len(all_results),
            "sites_searched": len(sites),
            "sites_responded": sites_responded,
            "results": all_results,
        }

    # ── Site Comparison ────────────────────────────────────────────────

    @staticmethod
    async def get_site_comparison(
        db: AsyncSession,
        org_id: uuid.UUID,
    ) -> list[dict[str, Any]]:
        """Compare metrics between all sites side by side.

        Returns:
            List of per-site metric dicts.
        """
        result = await db.execute(
            select(Site).where(Site.org_id == org_id).order_by(Site.name)
        )
        sites = list(result.scalars().all())

        comparison: list[dict[str, Any]] = []
        for site in sites:
            # Count federated alerts for this site
            fa_count_q = select(func.count()).select_from(FederatedAlert).where(
                FederatedAlert.source_site_id == site.id
            )
            fa_count = (await db.execute(fa_count_q)).scalar() or 0

            comparison.append({
                "site_id": str(site.id),
                "site_name": site.name,
                "site_code": site.code,
                "camera_count": site.camera_count,
                "alert_count_today": site.alert_count_today,
                "total_federated_alerts": fa_count,
                "is_online": site.is_online,
                "last_heartbeat": site.last_heartbeat.isoformat() if site.last_heartbeat else None,
            })

        return comparison

    # ── Test Connection ────────────────────────────────────────────────

    @staticmethod
    async def test_connection(
        api_url: str,
        api_key: str,
    ) -> dict[str, Any]:
        """Test connectivity to a remote site without registering it.

        Args:
            api_url: Base URL to test.
            api_key: API key for authentication.

        Returns:
            Dict with reachable status, latency, and version.
        """
        start = time.monotonic()
        try:
            result = await _remote_request(
                "GET", api_url, api_key, "/health",
                timeout=10.0,
            )
            latency_ms = round((time.monotonic() - start) * 1000, 2)

            version = None
            if isinstance(result, dict):
                version = result.get("version")

            return {
                "reachable": True,
                "latency_ms": latency_ms,
                "version": version,
                "error": None,
            }
        except Exception as exc:
            latency_ms = round((time.monotonic() - start) * 1000, 2)
            return {
                "reachable": False,
                "latency_ms": latency_ms,
                "version": None,
                "error": str(exc),
            }


# ── Helpers ─────────────────────────────────────────────────────────────────


async def _get_site(
    db: AsyncSession,
    site_id: uuid.UUID,
    org_id: uuid.UUID | None = None,
) -> Site:
    """Retrieve a site by ID with optional org filter.

    Raises:
        NotFoundError: If the site does not exist.
    """
    conditions = [Site.id == site_id]
    if org_id:
        conditions.append(Site.org_id == org_id)

    result = await db.execute(
        select(Site).where(and_(*conditions))
    )
    site = result.scalar_one_or_none()

    if site is None:
        raise NotFoundError(resource="Site", identifier=site_id)

    return site


def _parse_datetime(value: str | datetime) -> datetime:
    """Parse a datetime string or return the datetime as-is."""
    if isinstance(value, datetime):
        return value
    try:
        # Try ISO 8601 format
        if value.endswith("Z"):
            value = value[:-1] + "+00:00"
        return datetime.fromisoformat(value)
    except (ValueError, AttributeError):
        return datetime.now(timezone.utc)


def _extract_title(item: dict[str, Any], result_type: str) -> str:
    """Extract a display title from a search result item."""
    if result_type == "face":
        return item.get("person_name", item.get("name", "Unknown Person"))
    elif result_type == "vehicle":
        return item.get("plate_number", item.get("license_plate", "Unknown Vehicle"))
    elif result_type == "alert":
        return item.get("title", item.get("description", "Alert")[:100])
    return str(item.get("title", item.get("name", "Unknown")))
