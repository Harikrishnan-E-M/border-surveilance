"""Face enrollment and face event models for facial recognition."""

import uuid
from datetime import datetime
from typing import TYPE_CHECKING, Optional

from sqlalchemy import Boolean, DateTime, Float, ForeignKey, Integer, String, func
from sqlalchemy.dialects.postgresql import JSON, UUID
from sqlalchemy.orm import Mapped, mapped_column, relationship
from pgvector.sqlalchemy import Vector

from app.database import Base, TimestampMixin

if TYPE_CHECKING:
    from app.models.camera import Camera
    from app.models.person import Person
    from app.models.zone import Zone


class FaceEnrollment(Base):
    """A face embedding enrolled for a known person.

    Each person may have multiple enrollments captured under different
    lighting conditions and angles for improved recognition accuracy.
    The primary enrollment is used as the canonical thumbnail.
    """

    __tablename__ = "face_enrollments"

    person_id: Mapped[uuid.UUID] = mapped_column(
        UUID(as_uuid=True), ForeignKey("persons.id", ondelete="CASCADE"), nullable=False, index=True
    )
    embedding = mapped_column(Vector(512), nullable=False)
    image_path: Mapped[str] = mapped_column(String(1024), nullable=False)
    quality_score: Mapped[Optional[float]] = mapped_column(Float, nullable=True)
    is_primary: Mapped[bool] = mapped_column(
        Boolean, nullable=False, default=False, server_default="false"
    )

    # ── Relationships ──────────────────────────────────────────────────
    person: Mapped["Person"] = relationship(
        "Person", back_populates="face_enrollments", lazy="selectin"
    )

    def __repr__(self) -> str:
        return f"<FaceEnrollment(id={self.id}, person_id={self.person_id}, primary={self.is_primary})>"


class FaceEvent(Base):
    """A face detection/recognition event captured from a camera feed.

    Records every face detected in the video stream along with its
    bounding box, confidence, recognized person (if any), and
    attribute estimates (emotion, age, gender, mask status).
    """

    __tablename__ = "face_events"

    camera_id: Mapped[uuid.UUID] = mapped_column(
        UUID(as_uuid=True), ForeignKey("cameras.id", ondelete="CASCADE"), nullable=False, index=True
    )
    person_id: Mapped[Optional[uuid.UUID]] = mapped_column(
        UUID(as_uuid=True), ForeignKey("persons.id", ondelete="SET NULL"), nullable=True, index=True
    )
    zone_id: Mapped[Optional[uuid.UUID]] = mapped_column(
        UUID(as_uuid=True), ForeignKey("zones.id", ondelete="SET NULL"), nullable=True, index=True
    )
    timestamp: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), nullable=False, index=True
    )
    bbox_json: Mapped[Optional[dict]] = mapped_column(JSON, nullable=True)
    confidence: Mapped[Optional[float]] = mapped_column(Float, nullable=True)
    emotion: Mapped[Optional[str]] = mapped_column(String(50), nullable=True)
    age_estimate: Mapped[Optional[int]] = mapped_column(Integer, nullable=True)
    gender: Mapped[Optional[str]] = mapped_column(String(20), nullable=True)
    is_masked: Mapped[Optional[bool]] = mapped_column(Boolean, nullable=True)
    snapshot_path: Mapped[Optional[str]] = mapped_column(String(1024), nullable=True)
    embedding = mapped_column(Vector(512), nullable=True)

    # ── Relationships ──────────────────────────────────────────────────
    camera: Mapped["Camera"] = relationship(
        "Camera", back_populates="face_events", lazy="selectin"
    )
    person: Mapped[Optional["Person"]] = relationship(
        "Person", back_populates="face_events", lazy="selectin"
    )
    zone: Mapped[Optional["Zone"]] = relationship(
        "Zone", back_populates="face_events", lazy="selectin"
    )

    def __repr__(self) -> str:
        return f"<FaceEvent(id={self.id}, camera_id={self.camera_id}, person_id={self.person_id})>"
