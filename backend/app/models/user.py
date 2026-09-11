"""User and API key models for authentication and authorization."""

import enum
import uuid
from datetime import datetime
from typing import TYPE_CHECKING, List, Optional

from sqlalchemy import Boolean, DateTime, ForeignKey, Integer, String, Text, func
from sqlalchemy import Enum as SAEnum
from sqlalchemy.dialects.postgresql import JSON, UUID
from sqlalchemy.orm import Mapped, mapped_column, relationship

from app.database import Base, TimestampMixin

if TYPE_CHECKING:
    from app.models.alert import Alert, AlertEscalation
    from app.models.organization import Organization


class UserRole(str, enum.Enum):
    """Role-based access control roles."""

    SUPER_ADMIN = "super_admin"
    ORG_ADMIN = "org_admin"
    MANAGER = "manager"
    OPERATOR = "operator"
    VIEWER = "viewer"


class User(Base):
    """Platform user belonging to an organization.

    Users authenticate via email/password and are assigned roles that
    determine their permissions within the organization.
    """

    __tablename__ = "users"

    org_id: Mapped[uuid.UUID] = mapped_column(
        UUID(as_uuid=True), ForeignKey("organizations.id", ondelete="CASCADE"), nullable=False, index=True
    )
    email: Mapped[str] = mapped_column(String(320), unique=True, nullable=False, index=True)
    hashed_password: Mapped[str] = mapped_column(String(512), nullable=False)
    full_name: Mapped[str] = mapped_column(String(255), nullable=False)
    phone: Mapped[Optional[str]] = mapped_column(String(20), nullable=True)
    role: Mapped[UserRole] = mapped_column(
        String(32),
        nullable=False,
        default=UserRole.VIEWER,
    )
    avatar_url: Mapped[Optional[str]] = mapped_column(String(1024), nullable=True)
    is_active: Mapped[bool] = mapped_column(
        Boolean, nullable=False, default=True, server_default="true"
    )
    last_login: Mapped[Optional[datetime]] = mapped_column(
        DateTime(timezone=True), nullable=True
    )

    # ── Relationships ──────────────────────────────────────────────────
    organization: Mapped["Organization"] = relationship(
        "Organization", back_populates="users", lazy="selectin"
    )
    api_keys: Mapped[List["APIKey"]] = relationship(
        "APIKey", back_populates="user", cascade="all, delete-orphan", lazy="selectin"
    )
    acknowledged_alerts: Mapped[List["Alert"]] = relationship(
        "Alert",
        back_populates="acknowledged_by_user",
        foreign_keys="[Alert.acknowledged_by]",
        lazy="selectin",
    )
    resolved_alerts: Mapped[List["Alert"]] = relationship(
        "Alert",
        back_populates="resolved_by_user",
        foreign_keys="[Alert.resolved_by]",
        lazy="selectin",
    )
    escalations_received: Mapped[List["AlertEscalation"]] = relationship(
        "AlertEscalation",
        back_populates="escalated_to_user",
        foreign_keys="[AlertEscalation.escalated_to]",
        lazy="selectin",
    )
    escalations_sent: Mapped[List["AlertEscalation"]] = relationship(
        "AlertEscalation",
        back_populates="escalated_by_user",
        foreign_keys="[AlertEscalation.escalated_by]",
        lazy="selectin",
    )

    def __repr__(self) -> str:
        return f"<User(id={self.id}, email='{self.email}', role={self.role.value})>"


class APIKey(Base):
    """API key for programmatic access to the platform.

    Keys are stored as hashed values; the plaintext is only shown once
    at creation time.
    """

    __tablename__ = "api_keys"

    org_id: Mapped[uuid.UUID] = mapped_column(
        UUID(as_uuid=True), ForeignKey("organizations.id", ondelete="CASCADE"), nullable=False, index=True
    )
    user_id: Mapped[uuid.UUID] = mapped_column(
        UUID(as_uuid=True), ForeignKey("users.id", ondelete="CASCADE"), nullable=False, index=True
    )
    key_hash: Mapped[str] = mapped_column(String(512), nullable=False, unique=True)
    name: Mapped[str] = mapped_column(String(255), nullable=False)
    permissions: Mapped[Optional[dict]] = mapped_column(JSON, nullable=True)
    expires_at: Mapped[Optional[datetime]] = mapped_column(
        DateTime(timezone=True), nullable=True
    )
    is_active: Mapped[bool] = mapped_column(
        Boolean, nullable=False, default=True, server_default="true"
    )

    # ── Relationships ──────────────────────────────────────────────────
    organization: Mapped["Organization"] = relationship(
        "Organization", back_populates="api_keys", lazy="selectin"
    )
    user: Mapped["User"] = relationship(
        "User", back_populates="api_keys", lazy="selectin"
    )

    def __repr__(self) -> str:
        return f"<APIKey(id={self.id}, name='{self.name}', user_id={self.user_id})>"
