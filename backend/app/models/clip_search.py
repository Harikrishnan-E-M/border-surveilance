"""CLIP search models for natural language video search.

Stores frame-level CLIP embeddings in pgvector for efficient cosine
similarity search, along with search query history for analytics
and user experience improvements.
"""

import uuid
from datetime import datetime
from typing import TYPE_CHECKING, Optional

from sqlalchemy import (
    Boolean,
    DateTime,
    Float,
    ForeignKey,
    Index,
    Integer,
    String,
    Text,
    func,
)
from sqlalchemy.dialects.postgresql import JSON, UUID
from sqlalchemy.orm import Mapped, mapped_column, relationship
from pgvector.sqlalchemy import Vector

from app.database import Base, TimestampMixin

if TYPE_CHECKING:
    from app.models.camera import Camera
    from app.models.organization import Organization
    from app.models.recording import Recording
    from app.models.user import User


class FrameEmbedding(Base):
    """A CLIP embedding for a single video frame.

    Each row stores the 512-dimensional CLIP ViT-B/32 embedding for a
    frame extracted from a recording, along with a MinIO thumbnail path
    and metadata about detected objects and scene content.

    The embedding column is indexed using pgvector's IVFFlat index with
    cosine distance for efficient approximate nearest-neighbour search.
    """

    __tablename__ = "frame_embeddings"

    org_id: Mapped[uuid.UUID] = mapped_column(
        UUID(as_uuid=True),
        ForeignKey("organizations.id", ondelete="CASCADE"),
        nullable=False,
        index=True,
    )
    camera_id: Mapped[uuid.UUID] = mapped_column(
        UUID(as_uuid=True),
        ForeignKey("cameras.id", ondelete="CASCADE"),
        nullable=False,
        index=True,
    )
    recording_id: Mapped[uuid.UUID] = mapped_column(
        UUID(as_uuid=True),
        ForeignKey("recordings.id", ondelete="CASCADE"),
        nullable=False,
        index=True,
    )
    frame_number: Mapped[int] = mapped_column(
        Integer,
        nullable=False,
    )
    timestamp: Mapped[datetime] = mapped_column(
        DateTime(timezone=True),
        nullable=False,
        index=True,
    )
    embedding = mapped_column(
        Vector(512),
        nullable=False,
    )
    thumbnail_path: Mapped[Optional[str]] = mapped_column(
        String(1024),
        nullable=True,
    )
    metadata_json: Mapped[Optional[dict]] = mapped_column(
        JSON,
        nullable=True,
        doc="Detected objects, scene description, and other frame metadata",
    )
    is_keyframe: Mapped[bool] = mapped_column(
        Boolean,
        nullable=False,
        default=False,
        server_default="false",
    )

    # ── Relationships ──────────────────────────────────────────────────
    camera: Mapped["Camera"] = relationship(
        "Camera",
        lazy="selectin",
    )
    recording: Mapped["Recording"] = relationship(
        "Recording",
        lazy="selectin",
    )
    organization: Mapped["Organization"] = relationship(
        "Organization",
        lazy="selectin",
    )

    # ── Table-level indexes ────────────────────────────────────────────
    __table_args__ = (
        Index(
            "ix_frame_embeddings_camera_timestamp",
            "camera_id",
            "timestamp",
        ),
        Index(
            "ix_frame_embeddings_org_camera",
            "org_id",
            "camera_id",
        ),
        Index(
            "ix_frame_embeddings_recording_frame",
            "recording_id",
            "frame_number",
        ),
    )

    def __repr__(self) -> str:
        return (
            f"<FrameEmbedding(id={self.id}, camera_id={self.camera_id}, "
            f"frame={self.frame_number}, ts={self.timestamp})>"
        )


class SearchQuery(Base):
    """A user's search query and its metadata.

    Tracks natural language and image-based search queries for analytics,
    suggested-search generation, and search history display.
    """

    __tablename__ = "search_querys"

    org_id: Mapped[uuid.UUID] = mapped_column(
        UUID(as_uuid=True),
        ForeignKey("organizations.id", ondelete="CASCADE"),
        nullable=False,
        index=True,
    )
    user_id: Mapped[uuid.UUID] = mapped_column(
        UUID(as_uuid=True),
        ForeignKey("users.id", ondelete="CASCADE"),
        nullable=False,
        index=True,
    )
    query_text: Mapped[Optional[str]] = mapped_column(
        Text,
        nullable=True,
    )
    query_type: Mapped[str] = mapped_column(
        String(20),
        nullable=False,
        default="text",
        server_default="text",
        doc="Search type: 'text' or 'image'",
    )
    query_embedding = mapped_column(
        Vector(512),
        nullable=True,
    )
    result_count: Mapped[int] = mapped_column(
        Integer,
        nullable=False,
        default=0,
    )
    search_duration_ms: Mapped[Optional[float]] = mapped_column(
        Float,
        nullable=True,
    )
    filters_json: Mapped[Optional[dict]] = mapped_column(
        JSON,
        nullable=True,
        doc="Applied search filters (cameras, date range, etc.)",
    )

    # ── Relationships ──────────────────────────────────────────────────
    user: Mapped["User"] = relationship(
        "User",
        lazy="selectin",
    )
    organization: Mapped["Organization"] = relationship(
        "Organization",
        lazy="selectin",
    )

    # ── Table-level indexes ────────────────────────────────────────────
    __table_args__ = (
        Index(
            "ix_search_querys_user_created",
            "user_id",
            "created_at",
        ),
    )

    def __repr__(self) -> str:
        return (
            f"<SearchQuery(id={self.id}, user_id={self.user_id}, "
            f"query='{(self.query_text or '')[:40]}')>"
        )


# ── pgvector IVFFlat index DDL (to be run via Alembic migration) ──────────
#
# The IVFFlat index enables approximate nearest-neighbour search on the
# embedding column using cosine distance. The `lists` parameter controls
# the number of Voronoi cells; 100 is a good default for up to ~1M rows.
#
# Run this in an Alembic migration or manually:
#
#   CREATE INDEX IF NOT EXISTS ix_frame_embeddings_embedding_cosine
#   ON frame_embeddings
#   USING ivfflat (embedding vector_cosine_ops)
#   WITH (lists = 100);
#
# For HNSW (better recall, more memory):
#
#   CREATE INDEX IF NOT EXISTS ix_frame_embeddings_embedding_hnsw
#   ON frame_embeddings
#   USING hnsw (embedding vector_cosine_ops)
#   WITH (m = 16, ef_construction = 64);
