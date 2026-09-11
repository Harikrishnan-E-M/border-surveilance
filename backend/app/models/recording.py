"""Recording and recording segment models for video storage."""

import enum
import uuid
from datetime import datetime
from typing import TYPE_CHECKING, List, Optional

from sqlalchemy import (
    BigInteger,
    Boolean,
    DateTime,
    Float,
    ForeignKey,
    Integer,
    String,
    func,
)
from sqlalchemy import Enum as SAEnum
from sqlalchemy.dialects.postgresql import UUID
from sqlalchemy.orm import Mapped, mapped_column, relationship

from app.database import Base, TimestampMixin

if TYPE_CHECKING:
    from app.models.camera import Camera
    from app.models.organization import Organization


class RecordingType(str, enum.Enum):
    """Type of recording trigger."""

    CONTINUOUS = "continuous"
    EVENT = "event"


class Recording(Base):
    """A video recording from a camera.

    Recordings are created either continuously or triggered by events.
    They may be archived for long-term storage and have an expiration
    date after which they are eligible for cleanup.
    """

    __tablename__ = "recordings"

    camera_id: Mapped[uuid.UUID] = mapped_column(
        UUID(as_uuid=True), ForeignKey("cameras.id", ondelete="CASCADE"), nullable=False, index=True
    )
    org_id: Mapped[uuid.UUID] = mapped_column(
        UUID(as_uuid=True), ForeignKey("organizations.id", ondelete="CASCADE"), nullable=False, index=True
    )
    recording_type: Mapped[RecordingType] = mapped_column(
        String(32),
        nullable=False,
    )
    start_time: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), nullable=False, index=True
    )
    end_time: Mapped[Optional[datetime]] = mapped_column(
        DateTime(timezone=True), nullable=True
    )
    duration_seconds: Mapped[Optional[float]] = mapped_column(Float, nullable=True)
    file_path: Mapped[str] = mapped_column(String(1024), nullable=False)
    file_size_bytes: Mapped[Optional[int]] = mapped_column(BigInteger, nullable=True)
    is_archived: Mapped[bool] = mapped_column(
        Boolean, nullable=False, default=False, server_default="false"
    )
    expires_at: Mapped[Optional[datetime]] = mapped_column(
        DateTime(timezone=True), nullable=True
    )

    # ── Relationships ──────────────────────────────────────────────────
    camera: Mapped["Camera"] = relationship(
        "Camera", back_populates="recordings", lazy="selectin"
    )
    organization: Mapped["Organization"] = relationship(
        "Organization", back_populates="recordings", lazy="selectin"
    )
    segments: Mapped[List["RecordingSegment"]] = relationship(
        "RecordingSegment", back_populates="recording", cascade="all, delete-orphan", lazy="selectin"
    )

    def __repr__(self) -> str:
        return f"<Recording(id={self.id}, camera_id={self.camera_id}, type={self.recording_type.value})>"


class RecordingSegment(Base):
    """A segment (chunk) of a longer recording.

    Long recordings are split into fixed-duration segments for
    efficient storage, retrieval, and streaming. Each segment
    is a self-contained video file.
    """

    __tablename__ = "recording_segments"

    recording_id: Mapped[uuid.UUID] = mapped_column(
        UUID(as_uuid=True), ForeignKey("recordings.id", ondelete="CASCADE"), nullable=False, index=True
    )
    segment_number: Mapped[int] = mapped_column(Integer, nullable=False)
    file_path: Mapped[str] = mapped_column(String(1024), nullable=False)
    start_time: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), nullable=False
    )
    end_time: Mapped[Optional[datetime]] = mapped_column(
        DateTime(timezone=True), nullable=True
    )
    duration_seconds: Mapped[Optional[float]] = mapped_column(Float, nullable=True)
    file_size_bytes: Mapped[Optional[int]] = mapped_column(BigInteger, nullable=True)

    # ── Relationships ──────────────────────────────────────────────────
    recording: Mapped["Recording"] = relationship(
        "Recording", back_populates="segments", lazy="selectin"
    )

    def __repr__(self) -> str:
        return (
            f"<RecordingSegment(id={self.id}, recording_id={self.recording_id}, "
            f"segment={self.segment_number})>"
        )
