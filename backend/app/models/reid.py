"""Cross-camera person re-identification models.

Tracks person appearances across multiple cameras, stores appearance
embeddings (pgvector 512-dim), builds journey timelines, and records
cross-camera identity matches for review and confirmation.
"""

import uuid
from datetime import datetime
from typing import TYPE_CHECKING, Optional

from sqlalchemy import (
    Boolean,
    DateTime,
    Float,
    ForeignKey,
    Integer,
    String,
    Text,
    func,
)
from sqlalchemy.dialects.postgresql import JSON, JSONB, UUID
from sqlalchemy.orm import Mapped, mapped_column, relationship
from pgvector.sqlalchemy import Vector

from app.database import Base, TimestampMixin

if TYPE_CHECKING:
    from app.models.camera import Camera
    from app.models.user import User


class PersonTrack(Base):
    """A tracked person appearance within a single camera.

    Each row represents one continuous track from a single camera.
    The ``global_person_id`` links tracks across cameras that belong
    to the same physical person, determined by embedding similarity.

    The ``embedding`` column stores a 512-dimensional L2-normalised
    feature vector extracted by the OSNet ReID encoder.  Embedding
    similarity is computed via pgvector operators (cosine distance
    ``<=>``, L2 distance ``<->``).
    """

    __tablename__ = "person_tracks"

    org_id: Mapped[uuid.UUID] = mapped_column(
        UUID(as_uuid=True), nullable=False, index=True,
    )
    global_person_id: Mapped[uuid.UUID] = mapped_column(
        UUID(as_uuid=True), nullable=False, index=True,
        comment="Consistent identity across cameras",
    )
    camera_id: Mapped[uuid.UUID] = mapped_column(
        UUID(as_uuid=True),
        ForeignKey("cameras.id", ondelete="CASCADE"),
        nullable=False,
        index=True,
    )
    track_id: Mapped[int] = mapped_column(
        Integer, nullable=False,
        comment="Local tracker ID within the camera stream",
    )
    first_seen: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), nullable=False, index=True,
    )
    last_seen: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), nullable=False, index=True,
    )
    embedding = mapped_column(
        Vector(512), nullable=False,
        comment="512-dim L2-normalised appearance feature vector",
    )
    thumbnail_path: Mapped[Optional[str]] = mapped_column(
        String(1024), nullable=True,
        comment="MinIO path to best-quality person crop thumbnail",
    )
    metadata_json: Mapped[Optional[dict]] = mapped_column(
        JSONB, nullable=True, default=dict,
        comment="Extra metadata: bbox, confidence, clothing colour, etc.",
    )
    is_active: Mapped[bool] = mapped_column(
        Boolean, nullable=False, default=True, server_default="true",
        comment="True while the track is still being updated",
    )

    # ── Relationships ──────────────────────────────────────────────────
    camera: Mapped["Camera"] = relationship(
        "Camera", lazy="selectin",
    )

    def __repr__(self) -> str:
        return (
            f"<PersonTrack(id={self.id}, global_person_id={self.global_person_id}, "
            f"camera_id={self.camera_id}, track_id={self.track_id}, active={self.is_active})>"
        )


class PersonJourney(Base):
    """Aggregated cross-camera journey for a single physical person.

    Summarises all camera appearances for a ``global_person_id``,
    storing the ordered sequence of camera visits in ``events`` JSONB.
    Updated in real time as new tracks are assigned to this identity.
    """

    __tablename__ = "person_journeys"

    org_id: Mapped[uuid.UUID] = mapped_column(
        UUID(as_uuid=True), nullable=False, index=True,
    )
    global_person_id: Mapped[uuid.UUID] = mapped_column(
        UUID(as_uuid=True), nullable=False, unique=True, index=True,
    )
    events: Mapped[Optional[list]] = mapped_column(
        JSONB, nullable=True, default=list,
        comment=(
            "Ordered array of {camera_id, camera_name, timestamp, "
            "thumbnail_path, zone_id}"
        ),
    )
    first_camera_id: Mapped[Optional[uuid.UUID]] = mapped_column(
        UUID(as_uuid=True), nullable=True,
    )
    last_camera_id: Mapped[Optional[uuid.UUID]] = mapped_column(
        UUID(as_uuid=True), nullable=True,
    )
    first_seen: Mapped[Optional[datetime]] = mapped_column(
        DateTime(timezone=True), nullable=True, index=True,
    )
    last_seen: Mapped[Optional[datetime]] = mapped_column(
        DateTime(timezone=True), nullable=True, index=True,
    )
    total_cameras_visited: Mapped[int] = mapped_column(
        Integer, nullable=False, default=0, server_default="0",
    )
    total_duration_seconds: Mapped[Optional[float]] = mapped_column(
        Float, nullable=True, default=0.0,
    )

    def __repr__(self) -> str:
        return (
            f"<PersonJourney(id={self.id}, global_person_id={self.global_person_id}, "
            f"cameras={self.total_cameras_visited})>"
        )


class ReIDMatch(Base):
    """A cross-camera identity match between two person tracks.

    Created automatically when the system detects that tracks from
    different cameras likely belong to the same person.  Operators
    can confirm or reject matches through the review UI.
    """

    __tablename__ = "re_id_matchs"

    org_id: Mapped[uuid.UUID] = mapped_column(
        UUID(as_uuid=True), nullable=False, index=True,
    )
    track_a_id: Mapped[uuid.UUID] = mapped_column(
        UUID(as_uuid=True),
        ForeignKey("person_tracks.id", ondelete="CASCADE"),
        nullable=False,
        index=True,
    )
    track_b_id: Mapped[uuid.UUID] = mapped_column(
        UUID(as_uuid=True),
        ForeignKey("person_tracks.id", ondelete="CASCADE"),
        nullable=False,
        index=True,
    )
    similarity_score: Mapped[float] = mapped_column(
        Float, nullable=False,
        comment="Cosine similarity between track embeddings (0-1)",
    )
    is_confirmed: Mapped[Optional[bool]] = mapped_column(
        Boolean, nullable=True, default=None,
        comment="null=pending, true=confirmed, false=rejected",
    )
    confirmed_by: Mapped[Optional[uuid.UUID]] = mapped_column(
        UUID(as_uuid=True),
        ForeignKey("users.id", ondelete="SET NULL"),
        nullable=True,
    )
    matched_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), nullable=False, default=func.now(),
        server_default=func.now(), index=True,
    )

    # ── Relationships ──────────────────────────────────────────────────
    track_a: Mapped["PersonTrack"] = relationship(
        "PersonTrack", foreign_keys=[track_a_id], lazy="selectin",
    )
    track_b: Mapped["PersonTrack"] = relationship(
        "PersonTrack", foreign_keys=[track_b_id], lazy="selectin",
    )
    confirmed_by_user: Mapped[Optional["User"]] = relationship(
        "User", lazy="selectin",
    )

    def __repr__(self) -> str:
        return (
            f"<ReIDMatch(id={self.id}, track_a={self.track_a_id}, "
            f"track_b={self.track_b_id}, similarity={self.similarity_score:.3f}, "
            f"confirmed={self.is_confirmed})>"
        )
