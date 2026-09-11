"""Cross-camera person re-identification Pydantic schemas.

Covers person track responses, journey timelines, ReID match review,
person search by image, active-person listings, merge/split operations,
and cross-camera filter parameters.
"""

from __future__ import annotations


from datetime import datetime
from uuid import UUID

from pydantic import BaseModel, ConfigDict, Field


# ── Journey Events ─────────────────────────────────────────────────────────────


class JourneyEvent(BaseModel):
    """A single camera appearance within a person's cross-camera journey."""

    camera_id: UUID = Field(description="Camera that captured the appearance.")
    camera_name: str = Field(
        description="Human-readable camera name.",
        examples=["Main Entrance"],
    )
    timestamp: datetime = Field(
        description="Time of first detection at this camera.",
    )
    thumbnail_path: str | None = Field(
        default=None,
        description="MinIO path to the person crop at this camera.",
    )
    zone_id: UUID | None = Field(
        default=None,
        description="Zone within the camera where the person was detected.",
    )


# ── Person Track ───────────────────────────────────────────────────────────────


class PersonTrackResponse(BaseModel):
    """Response schema for a single person track."""

    model_config = ConfigDict(from_attributes=True)

    id: UUID = Field(description="Track unique identifier.")
    org_id: UUID = Field(description="Owning organization ID.")
    global_person_id: UUID = Field(
        description="Consistent identity across all cameras.",
    )
    camera_id: UUID = Field(description="Camera that captured this track.")
    camera_name: str | None = Field(
        default=None,
        description="Camera name (joined from camera table).",
    )
    track_id: int = Field(description="Local tracker ID within the camera.")
    first_seen: datetime = Field(description="First detection timestamp.")
    last_seen: datetime = Field(description="Most recent detection timestamp.")
    thumbnail_path: str | None = Field(
        default=None,
        description="MinIO path to person crop thumbnail.",
    )
    metadata_json: dict | None = Field(
        default=None,
        description="Extra metadata (bbox, confidence, etc.).",
    )
    is_active: bool = Field(
        description="Whether the track is still being updated.",
    )
    created_at: datetime = Field(description="Record creation time.")
    updated_at: datetime = Field(description="Last update time.")


class PersonTrackList(BaseModel):
    """Paginated list of person tracks."""

    items: list[PersonTrackResponse] = Field(
        description="List of person tracks.",
    )
    total: int = Field(description="Total matching tracks.")
    page: int = Field(description="Current page number.")
    page_size: int = Field(description="Items per page.")
    total_pages: int = Field(description="Total number of pages.")


# ── Person Journey ─────────────────────────────────────────────────────────────


class PersonJourneyResponse(BaseModel):
    """Full cross-camera journey for a single person identity."""

    model_config = ConfigDict(from_attributes=True)

    id: UUID = Field(description="Journey record ID.")
    org_id: UUID = Field(description="Owning organization ID.")
    global_person_id: UUID = Field(description="Person identity UUID.")
    events: list[JourneyEvent] = Field(
        default_factory=list,
        description="Ordered list of camera appearances.",
    )
    first_camera_id: UUID | None = Field(
        default=None,
        description="Camera where the person was first seen.",
    )
    last_camera_id: UUID | None = Field(
        default=None,
        description="Camera where the person was most recently seen.",
    )
    first_seen: datetime | None = Field(
        default=None,
        description="Earliest detection across all cameras.",
    )
    last_seen: datetime | None = Field(
        default=None,
        description="Most recent detection across all cameras.",
    )
    total_cameras_visited: int = Field(
        default=0,
        description="Number of distinct cameras visited.",
    )
    total_duration_seconds: float | None = Field(
        default=None,
        description="Total time span from first to last sighting.",
    )
    created_at: datetime | None = Field(default=None)
    updated_at: datetime | None = Field(default=None)


# ── ReID Match ─────────────────────────────────────────────────────────────────


class ReIDMatchResponse(BaseModel):
    """Response schema for a cross-camera identity match."""

    model_config = ConfigDict(from_attributes=True)

    id: UUID = Field(description="Match record ID.")
    org_id: UUID = Field(description="Owning organization ID.")
    track_a_id: UUID = Field(description="First track ID.")
    track_b_id: UUID = Field(description="Second track ID.")
    similarity_score: float = Field(
        ge=0.0,
        le=1.0,
        description="Cosine similarity between appearance embeddings.",
        examples=[0.87],
    )
    is_confirmed: bool | None = Field(
        default=None,
        description="null=pending, true=confirmed, false=rejected.",
    )
    confirmed_by: UUID | None = Field(
        default=None,
        description="User who confirmed or rejected the match.",
    )
    matched_at: datetime = Field(
        description="Timestamp when the match was detected.",
    )

    # Enriched fields populated by the API layer
    track_a_camera_name: str | None = Field(default=None)
    track_a_thumbnail: str | None = Field(default=None)
    track_a_first_seen: datetime | None = Field(default=None)
    track_b_camera_name: str | None = Field(default=None)
    track_b_thumbnail: str | None = Field(default=None)
    track_b_first_seen: datetime | None = Field(default=None)

    created_at: datetime | None = Field(default=None)


class ReIDMatchList(BaseModel):
    """Paginated list of cross-camera matches."""

    items: list[ReIDMatchResponse] = Field(
        description="List of ReID matches.",
    )
    total: int = Field(description="Total matching records.")
    page: int = Field(description="Current page number.")
    page_size: int = Field(description="Items per page.")
    total_pages: int = Field(description="Total number of pages.")


# ── Person Search ──────────────────────────────────────────────────────────────


class PersonSearchRequest(BaseModel):
    """Search for a person across all cameras using an image upload.

    The image should be base64-encoded; alternatively the multipart
    upload endpoint accepts a file directly.
    """

    image: str = Field(
        description="Base64-encoded image of the person to search for.",
    )
    threshold: float = Field(
        default=0.4,
        ge=0.0,
        le=1.0,
        description="Minimum similarity score (0-1).",
        examples=[0.4],
    )
    top_k: int = Field(
        default=20,
        ge=1,
        le=100,
        description="Maximum number of results to return.",
        examples=[20],
    )


class PersonSearchResult(BaseModel):
    """Single result from a person image search."""

    global_person_id: UUID = Field(
        description="Matched person global identity.",
    )
    similarity: float = Field(
        ge=0.0,
        le=1.0,
        description="Cosine similarity to the query image.",
        examples=[0.91],
    )
    camera_id: UUID = Field(
        description="Camera where this match was found.",
    )
    camera_name: str = Field(
        description="Camera name.",
        examples=["Lobby Camera 1"],
    )
    thumbnail_path: str | None = Field(
        default=None,
        description="MinIO path to the matched person crop.",
    )
    first_seen: datetime | None = Field(
        default=None,
        description="First time this identity was seen.",
    )
    last_seen: datetime | None = Field(
        default=None,
        description="Last time this identity was seen.",
    )
    cameras_visited: int = Field(
        default=1,
        description="Number of cameras this person has been seen at.",
    )


# ── Active Persons ─────────────────────────────────────────────────────────────


class ActivePersonResponse(BaseModel):
    """A person currently visible in at least one camera."""

    global_person_id: UUID = Field(
        description="Consistent person identity.",
    )
    current_camera_id: UUID = Field(
        description="Camera currently seeing this person.",
    )
    current_camera_name: str = Field(
        description="Name of the current camera.",
        examples=["East Corridor"],
    )
    thumbnail_path: str | None = Field(
        default=None,
        description="Most recent person crop.",
    )
    first_seen: datetime = Field(
        description="Earliest sighting across any camera.",
    )
    last_seen: datetime = Field(
        description="Most recent sighting.",
    )
    cameras_visited: int = Field(
        default=1,
        description="Number of distinct cameras visited so far.",
    )
    total_duration_seconds: float = Field(
        default=0.0,
        description="Total time tracked in seconds.",
    )


# ── Merge / Split Operations ──────────────────────────────────────────────────


class MergeRequest(BaseModel):
    """Request to merge two separate person identities into one.

    All tracks belonging to ``source_global_person_id`` will be
    reassigned to ``target_global_person_id``.
    """

    target_global_person_id: UUID = Field(
        description="The identity to keep (tracks merged into this).",
    )
    source_global_person_id: UUID = Field(
        description="The identity to dissolve (tracks moved away from this).",
    )


class SplitRequest(BaseModel):
    """Request to split incorrectly merged tracks from a person identity.

    The specified ``track_ids`` are removed from the current identity
    and assigned a new ``global_person_id``.
    """

    global_person_id: UUID = Field(
        description="Current person identity to split tracks from.",
    )
    track_ids: list[UUID] = Field(
        min_length=1,
        description="Track IDs to separate into a new identity.",
    )


# ── Cross-Camera Filters ──────────────────────────────────────────────────────


class CrossCameraFilters(BaseModel):
    """Query filters for the cross-camera match listing."""

    start_date: datetime | None = Field(
        default=None,
        description="Inclusive lower bound for match timestamp.",
        examples=["2025-01-01T00:00:00Z"],
    )
    end_date: datetime | None = Field(
        default=None,
        description="Inclusive upper bound for match timestamp.",
        examples=["2025-12-31T23:59:59Z"],
    )
    camera_ids: list[UUID] | None = Field(
        default=None,
        description="Filter to matches involving these cameras.",
    )
    min_similarity: float = Field(
        default=0.0,
        ge=0.0,
        le=1.0,
        description="Minimum similarity score for matches.",
        examples=[0.5],
    )
    is_confirmed: bool | None = Field(
        default=None,
        description="Filter by confirmation status (null=all).",
    )


# ── Statistics ─────────────────────────────────────────────────────────────────


class ReIDStatsResponse(BaseModel):
    """Aggregate ReID statistics for the organization."""

    total_persons: int = Field(
        default=0,
        description="Total unique person identities tracked.",
    )
    active_persons: int = Field(
        default=0,
        description="Persons currently visible in at least one camera.",
    )
    total_tracks: int = Field(
        default=0,
        description="Total track segments across all cameras.",
    )
    total_matches: int = Field(
        default=0,
        description="Total cross-camera matches detected.",
    )
    pending_review: int = Field(
        default=0,
        description="Matches awaiting operator confirmation.",
    )
    avg_cameras_visited: float = Field(
        default=0.0,
        description="Average number of cameras each person visits.",
    )
    most_traversed_path: str | None = Field(
        default=None,
        description="Most common camera-to-camera transition.",
        examples=["Main Entrance -> Lobby -> East Corridor"],
    )
    peak_crossing_hour: str | None = Field(
        default=None,
        description="Hour with the most cross-camera transitions.",
        examples=["09:00"],
    )
