"""Organization model for multi-tenant VisionAI platform."""

import enum
import uuid
from datetime import datetime
from typing import TYPE_CHECKING, List, Optional

from sqlalchemy import Boolean, DateTime, Integer, String, Text, func
from sqlalchemy import Enum as SAEnum
from sqlalchemy.orm import Mapped, mapped_column, relationship

from app.database import Base, TimestampMixin

if TYPE_CHECKING:
    from app.models.alert import Alert
    from app.models.camera import Camera
    from app.models.person import Person
    from app.models.recording import Recording
    from app.models.rule import Rule
    from app.models.user import APIKey, User
    from app.models.vehicle import Vehicle


class SubscriptionTier(str, enum.Enum):
    """Subscription tier levels for organizations."""

    FREE = "free"
    PRO = "pro"
    ENTERPRISE = "enterprise"


class Organization(Base):
    """Represents a tenant organization in the VisionAI platform.

    Each organization has its own set of users, cameras, persons, vehicles,
    and all associated data. This is the root entity for multi-tenancy.
    """

    __tablename__ = "organizations"

    name: Mapped[str] = mapped_column(String(255), nullable=False)
    slug: Mapped[str] = mapped_column(String(255), unique=True, nullable=False, index=True)
    subscription_tier: Mapped[str] = mapped_column(
        String(32),
        nullable=False,
        default=SubscriptionTier.FREE.value,
        server_default="free",
    )
    max_cameras: Mapped[int] = mapped_column(Integer, nullable=False, default=4, server_default="4")
    max_users: Mapped[int] = mapped_column(Integer, nullable=False, default=5, server_default="5")
    logo_url: Mapped[Optional[str]] = mapped_column(String(1024), nullable=True)
    timezone: Mapped[str] = mapped_column(
        String(64), nullable=False, default="Asia/Kolkata", server_default="Asia/Kolkata"
    )
    is_active: Mapped[bool] = mapped_column(
        Boolean, nullable=False, default=True, server_default="true"
    )

    # ── Relationships ──────────────────────────────────────────────────
    users: Mapped[List["User"]] = relationship(
        "User", back_populates="organization", cascade="all, delete-orphan", lazy="selectin"
    )
    cameras: Mapped[List["Camera"]] = relationship(
        "Camera", back_populates="organization", cascade="all, delete-orphan", lazy="selectin"
    )
    api_keys: Mapped[List["APIKey"]] = relationship(
        "APIKey", back_populates="organization", cascade="all, delete-orphan", lazy="selectin"
    )
    persons: Mapped[List["Person"]] = relationship(
        "Person", back_populates="organization", cascade="all, delete-orphan", lazy="selectin"
    )
    vehicles: Mapped[List["Vehicle"]] = relationship(
        "Vehicle", back_populates="organization", cascade="all, delete-orphan", lazy="selectin"
    )
    rules: Mapped[List["Rule"]] = relationship(
        "Rule", back_populates="organization", cascade="all, delete-orphan", lazy="selectin"
    )
    alerts: Mapped[List["Alert"]] = relationship(
        "Alert", back_populates="organization", cascade="all, delete-orphan", lazy="selectin"
    )
    recordings: Mapped[List["Recording"]] = relationship(
        "Recording", back_populates="organization", cascade="all, delete-orphan", lazy="selectin"
    )

    def __repr__(self) -> str:
        return f"<Organization(id={self.id}, name='{self.name}', slug='{self.slug}')>"
