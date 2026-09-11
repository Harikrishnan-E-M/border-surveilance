"""Multi-Site Federation models.

Provides the database schema for federated multi-site management
including remote site registration, cross-site synchronisation tracking,
and federated alert aggregation.
"""

import enum
import uuid
from datetime import datetime
from typing import TYPE_CHECKING, List, Optional

from sqlalchemy import (
    Boolean,
    DateTime,
    Float,
    ForeignKey,
    Integer,
    String,
    Text,
    UniqueConstraint,
    func,
)
from sqlalchemy import Enum as SAEnum
from sqlalchemy.dialects.postgresql import JSON, UUID
from sqlalchemy.orm import Mapped, mapped_column, relationship

from app.database import Base, TimestampMixin

if TYPE_CHECKING:
    from app.models.organization import Organization


# ── Enumerations ────────────────────────────────────────────────────────────


class SyncType(str, enum.Enum):
    """Types of data that can be synchronised between sites."""

    ALERTS = "alerts"
    CAMERAS = "cameras"
    FACES = "faces"
    VEHICLES = "vehicles"
    ANALYTICS = "analytics"


class SyncStatus(str, enum.Enum):
    """Current status of a synchronisation operation."""

    SYNCING = "syncing"
    SYNCED = "synced"
    FAILED = "failed"


# ── Site ────────────────────────────────────────────────────────────────────


class Site(Base):
    """A registered VisionAI site within a federated deployment.

    Each site represents a distinct physical location running its own
    VisionAI instance.  The primary site acts as the federation hub
    that aggregates data from all remote sites.  Communication is
    authenticated via encrypted API keys and secured over HTTPS.
    """

    __tablename__ = "sites"

    org_id: Mapped[uuid.UUID] = mapped_column(
        UUID(as_uuid=True),
        ForeignKey("organizations.id", ondelete="CASCADE"),
        nullable=False,
        index=True,
    )
    name: Mapped[str] = mapped_column(
        String(255),
        nullable=False,
        doc="Human-readable site name (e.g. 'Downtown HQ').",
    )
    code: Mapped[str] = mapped_column(
        String(64),
        nullable=False,
        unique=True,
        index=True,
        doc="URL-safe slug that uniquely identifies this site (e.g. 'downtown-hq').",
    )
    address: Mapped[Optional[str]] = mapped_column(
        String(512),
        nullable=True,
        doc="Street address of the site.",
    )
    city: Mapped[Optional[str]] = mapped_column(
        String(128),
        nullable=True,
    )
    state: Mapped[Optional[str]] = mapped_column(
        String(128),
        nullable=True,
    )
    country: Mapped[Optional[str]] = mapped_column(
        String(128),
        nullable=True,
    )
    latitude: Mapped[Optional[float]] = mapped_column(
        Float,
        nullable=True,
        doc="GPS latitude for map display.",
    )
    longitude: Mapped[Optional[float]] = mapped_column(
        Float,
        nullable=True,
        doc="GPS longitude for map display.",
    )
    timezone: Mapped[str] = mapped_column(
        String(64),
        nullable=False,
        default="UTC",
        server_default="UTC",
        doc="IANA timezone identifier (e.g. 'America/New_York').",
    )
    api_url: Mapped[str] = mapped_column(
        String(2048),
        nullable=False,
        doc="Base URL of the remote VisionAI API (e.g. 'https://site2.example.com/api/v1').",
    )
    api_key_encrypted: Mapped[str] = mapped_column(
        Text,
        nullable=False,
        doc="Fernet-encrypted API key for authenticating with the remote site.",
    )
    is_primary: Mapped[bool] = mapped_column(
        Boolean,
        nullable=False,
        default=False,
        server_default="false",
        doc="Whether this is the primary (hub) site in the federation.",
    )
    is_online: Mapped[bool] = mapped_column(
        Boolean,
        nullable=False,
        default=False,
        server_default="false",
        doc="Whether the site responded to the last heartbeat check.",
    )
    last_heartbeat: Mapped[Optional[datetime]] = mapped_column(
        DateTime(timezone=True),
        nullable=True,
        doc="Timestamp of the last successful heartbeat.",
    )
    camera_count: Mapped[int] = mapped_column(
        Integer,
        nullable=False,
        default=0,
        server_default="0",
        doc="Number of cameras at this site (updated during sync).",
    )
    alert_count_today: Mapped[int] = mapped_column(
        Integer,
        nullable=False,
        default=0,
        server_default="0",
        doc="Alert count for the current UTC day (refreshed on sync).",
    )
    metadata_json: Mapped[Optional[dict]] = mapped_column(
        JSON,
        nullable=True,
        default=None,
        doc="Arbitrary site metadata (version, capabilities, etc.).",
    )

    # ── Relationships ──────────────────────────────────────────────────
    organization: Mapped["Organization"] = relationship(
        "Organization",
        lazy="selectin",
    )
    sync_records_as_source: Mapped[List["SiteSync"]] = relationship(
        "SiteSync",
        foreign_keys="SiteSync.source_site_id",
        back_populates="source_site",
        cascade="all, delete-orphan",
        lazy="noload",
    )
    sync_records_as_target: Mapped[List["SiteSync"]] = relationship(
        "SiteSync",
        foreign_keys="SiteSync.target_site_id",
        back_populates="target_site",
        cascade="all, delete-orphan",
        lazy="noload",
    )
    federated_alerts: Mapped[List["FederatedAlert"]] = relationship(
        "FederatedAlert",
        back_populates="source_site",
        cascade="all, delete-orphan",
        lazy="noload",
    )

    def __repr__(self) -> str:
        return f"<Site(id={self.id}, code='{self.code}', online={self.is_online})>"


# ── SiteSync ───────────────────────────────────────────────────────────────


class SiteSync(Base):
    """Tracks synchronisation operations between two sites.

    Each record represents one sync session for a specific data type.
    The source site is where data originates and the target site is
    where it is replicated to.
    """

    __tablename__ = "site_syncs"

    source_site_id: Mapped[uuid.UUID] = mapped_column(
        UUID(as_uuid=True),
        ForeignKey("sites.id", ondelete="CASCADE"),
        nullable=False,
        index=True,
    )
    target_site_id: Mapped[uuid.UUID] = mapped_column(
        UUID(as_uuid=True),
        ForeignKey("sites.id", ondelete="CASCADE"),
        nullable=False,
        index=True,
    )
    sync_type: Mapped[SyncType] = mapped_column(
        String(32),
        nullable=False,
        doc="Category of data being synchronised.",
    )
    last_synced_at: Mapped[Optional[datetime]] = mapped_column(
        DateTime(timezone=True),
        nullable=True,
        doc="Timestamp when the sync completed (success or failure).",
    )
    status: Mapped[SyncStatus] = mapped_column(
        String(32),
        nullable=False,
        default=SyncStatus.SYNCING,
        server_default=SyncStatus.SYNCING.value,
    )
    error_message: Mapped[Optional[str]] = mapped_column(
        Text,
        nullable=True,
        doc="Error details if the sync failed.",
    )
    records_synced: Mapped[int] = mapped_column(
        Integer,
        nullable=False,
        default=0,
        server_default="0",
        doc="Number of records transferred in this sync session.",
    )

    # ── Relationships ──────────────────────────────────────────────────
    source_site: Mapped["Site"] = relationship(
        "Site",
        foreign_keys=[source_site_id],
        back_populates="sync_records_as_source",
        lazy="selectin",
    )
    target_site: Mapped["Site"] = relationship(
        "Site",
        foreign_keys=[target_site_id],
        back_populates="sync_records_as_target",
        lazy="selectin",
    )

    def __repr__(self) -> str:
        return (
            f"<SiteSync(id={self.id}, type={self.sync_type.value}, "
            f"status={self.status.value})>"
        )


# ── FederatedAlert ─────────────────────────────────────────────────────────


class FederatedAlert(Base):
    """An alert that has been pulled from a remote federated site.

    Stores a denormalised copy of the alert so that the hub site can
    render a unified, cross-site alert feed without querying every
    remote instance in real-time.
    """

    __tablename__ = "federated_alerts"

    org_id: Mapped[uuid.UUID] = mapped_column(
        UUID(as_uuid=True),
        ForeignKey("organizations.id", ondelete="CASCADE"),
        nullable=False,
        index=True,
    )
    source_site_id: Mapped[uuid.UUID] = mapped_column(
        UUID(as_uuid=True),
        ForeignKey("sites.id", ondelete="CASCADE"),
        nullable=False,
        index=True,
    )
    original_alert_id: Mapped[str] = mapped_column(
        String(64),
        nullable=False,
        index=True,
        doc="The alert ID on the originating remote site.",
    )
    alert_type: Mapped[str] = mapped_column(
        String(64),
        nullable=False,
        doc="Alert type from the remote site (e.g. 'intrusion', 'loitering').",
    )
    severity: Mapped[str] = mapped_column(
        String(32),
        nullable=False,
        doc="Severity level from the remote site.",
    )
    camera_name: Mapped[str] = mapped_column(
        String(255),
        nullable=False,
        doc="Camera name from the remote site.",
    )
    description: Mapped[Optional[str]] = mapped_column(
        Text,
        nullable=True,
    )
    thumbnail_path: Mapped[Optional[str]] = mapped_column(
        String(1024),
        nullable=True,
        doc="Path or URL to the alert thumbnail/snapshot.",
    )
    original_created_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True),
        nullable=False,
        doc="Original creation timestamp from the remote site.",
    )
    synced_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True),
        nullable=False,
        default=lambda: datetime.now(__import__("datetime").timezone.utc),
        server_default=func.now(),
        doc="Timestamp when this alert was pulled into the hub.",
    )

    __table_args__ = (
        UniqueConstraint(
            "source_site_id",
            "original_alert_id",
            name="uq_federated_alerts_source_original",
        ),
    )

    # ── Relationships ──────────────────────────────────────────────────
    organization: Mapped["Organization"] = relationship(
        "Organization",
        lazy="selectin",
    )
    source_site: Mapped["Site"] = relationship(
        "Site",
        back_populates="federated_alerts",
        lazy="selectin",
    )

    def __repr__(self) -> str:
        return (
            f"<FederatedAlert(id={self.id}, site={self.source_site_id}, "
            f"type={self.alert_type}, severity={self.severity})>"
        )
