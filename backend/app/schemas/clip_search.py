"""Pydantic schemas for CLIP-powered natural language video search.

Covers text and image search requests, search results, indexing status,
and search history for the video search API endpoints.
"""

from __future__ import annotations


from datetime import datetime
from typing import Optional
from uuid import UUID

from pydantic import BaseModel, ConfigDict, Field


# ── Search Requests ───────────────────────────────────────────────────────────


class TextSearchRequest(BaseModel):
    """Request body for natural language text search."""

    query: str = Field(
        min_length=1,
        max_length=500,
        description="Natural language search query.",
        examples=["person wearing red jacket near entrance"],
    )
    cameras: list[UUID] | None = Field(
        default=None,
        description="Filter results to specific camera IDs. None means all cameras.",
        examples=[["550e8400-e29b-41d4-a716-446655440000"]],
    )
    date_from: datetime | None = Field(
        default=None,
        description="Filter results from this date/time (inclusive, ISO-8601).",
        examples=["2025-01-01T00:00:00Z"],
    )
    date_to: datetime | None = Field(
        default=None,
        description="Filter results up to this date/time (inclusive, ISO-8601).",
        examples=["2025-01-31T23:59:59Z"],
    )
    time_of_day_start: str | None = Field(
        default=None,
        description="Filter by time of day start (HH:MM format).",
        examples=["08:00"],
    )
    time_of_day_end: str | None = Field(
        default=None,
        description="Filter by time of day end (HH:MM format).",
        examples=["18:00"],
    )
    top_k: int = Field(
        default=50,
        ge=1,
        le=500,
        description="Maximum number of results to return.",
        examples=[50],
    )
    min_similarity: float = Field(
        default=0.15,
        ge=0.0,
        le=1.0,
        description="Minimum cosine similarity threshold for results.",
        examples=[0.15],
    )


class ImageSearchRequest(BaseModel):
    """Schema documentation for image similarity search.

    The actual image file is sent as multipart/form-data; this schema
    documents the additional JSON fields sent alongside the file.
    """

    cameras: list[UUID] | None = Field(
        default=None,
        description="Filter results to specific camera IDs.",
    )
    date_from: datetime | None = Field(
        default=None,
        description="Filter results from this date/time.",
    )
    date_to: datetime | None = Field(
        default=None,
        description="Filter results up to this date/time.",
    )
    top_k: int = Field(
        default=50,
        ge=1,
        le=500,
        description="Maximum number of results to return.",
    )
    min_similarity: float = Field(
        default=0.15,
        ge=0.0,
        le=1.0,
        description="Minimum similarity threshold.",
    )


# ── Search Results ────────────────────────────────────────────────────────────


class SearchResult(BaseModel):
    """A single search result with frame metadata and similarity score."""

    model_config = ConfigDict(from_attributes=True)

    frame_id: UUID = Field(
        description="Unique identifier of the matched frame embedding.",
    )
    camera_id: UUID = Field(
        description="Camera that captured this frame.",
    )
    camera_name: str = Field(
        description="Human-readable camera name.",
        examples=["Main Entrance"],
    )
    recording_id: UUID = Field(
        description="Recording containing this frame.",
    )
    frame_number: int = Field(
        description="Frame number within the recording.",
        examples=[150],
    )
    timestamp: datetime = Field(
        description="Timestamp when the frame was captured.",
    )
    similarity_score: float = Field(
        description="Cosine similarity score (0.0 to 1.0).",
        examples=[0.87],
    )
    thumbnail_url: str | None = Field(
        default=None,
        description="URL to the frame thumbnail image in MinIO.",
    )
    detected_objects: list[str] = Field(
        default_factory=list,
        description="List of objects detected in the frame.",
        examples=[["person", "car", "backpack"]],
    )
    scene_description: str | None = Field(
        default=None,
        description="AI-generated scene description.",
    )


class SearchResponse(BaseModel):
    """Response wrapper for search results."""

    results: list[SearchResult] = Field(
        description="Ranked list of matching frames.",
    )
    query: str | None = Field(
        default=None,
        description="The search query that produced these results.",
    )
    total_matches: int = Field(
        description="Total number of frames above the similarity threshold.",
        examples=[42],
    )
    search_duration_ms: float = Field(
        description="Time taken to execute the search in milliseconds.",
        examples=[125.5],
    )


# ── Indexing Status ───────────────────────────────────────────────────────────


class IndexingStatus(BaseModel):
    """Indexing progress for a single camera."""

    model_config = ConfigDict(from_attributes=True)

    camera_id: UUID = Field(
        description="Camera identifier.",
    )
    camera_name: str = Field(
        description="Camera display name.",
        examples=["Main Entrance"],
    )
    frames_indexed: int = Field(
        description="Total number of frames indexed for this camera.",
        examples=[15000],
    )
    recordings_indexed: int = Field(
        default=0,
        description="Number of recordings fully indexed.",
        examples=[25],
    )
    last_indexed: datetime | None = Field(
        default=None,
        description="Timestamp of the most recently indexed frame.",
    )
    is_indexing: bool = Field(
        default=False,
        description="Whether indexing is currently in progress.",
    )
    coverage_hours: float = Field(
        default=0.0,
        description="Total hours of video content indexed.",
        examples=[120.5],
    )


class IndexingStatusResponse(BaseModel):
    """Response wrapper for indexing status across all cameras."""

    cameras: list[IndexingStatus] = Field(
        description="Per-camera indexing status.",
    )
    total_frames: int = Field(
        description="Total frames indexed across all cameras.",
        examples=[150000],
    )
    total_cameras: int = Field(
        description="Number of cameras with indexed content.",
        examples=[12],
    )


# ── Search History ────────────────────────────────────────────────────────────


class SearchHistoryItem(BaseModel):
    """A previous search query from the user's history."""

    model_config = ConfigDict(from_attributes=True)

    id: UUID = Field(
        description="Search query identifier.",
    )
    query: str | None = Field(
        default=None,
        description="The search query text (None for image searches).",
    )
    query_type: str = Field(
        default="text",
        description="Search type: 'text' or 'image'.",
        examples=["text"],
    )
    timestamp: datetime = Field(
        description="When the search was performed.",
    )
    result_count: int = Field(
        description="Number of results returned.",
        examples=[15],
    )
    search_duration_ms: float | None = Field(
        default=None,
        description="Search execution time in milliseconds.",
    )


class SearchHistoryResponse(BaseModel):
    """Response wrapper for search history."""

    queries: list[SearchHistoryItem] = Field(
        description="Recent search queries, newest first.",
    )
    total: int = Field(
        description="Total number of search queries in history.",
    )


# ── Indexing Trigger ──────────────────────────────────────────────────────────


class IndexRecordingRequest(BaseModel):
    """Request to trigger indexing of a specific recording."""

    fps: float = Field(
        default=1.0,
        ge=0.1,
        le=5.0,
        description="Frames per second to extract and index.",
        examples=[1.0],
    )


class IndexCameraRequest(BaseModel):
    """Request to start continuous frame indexing for a camera."""

    fps: float = Field(
        default=1.0,
        ge=0.1,
        le=5.0,
        description="Frames per second to index from the live stream.",
        examples=[1.0],
    )


class IndexingTaskResponse(BaseModel):
    """Response for indexing task submission."""

    task_id: str = Field(
        description="Celery task ID for tracking progress.",
    )
    status: str = Field(
        description="Current task status.",
        examples=["queued"],
    )
    message: str = Field(
        description="Human-readable status message.",
    )
