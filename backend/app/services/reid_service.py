"""Cross-camera person re-identification service.

Provides the full lifecycle for person tracking across cameras:
track registration with embedding extraction, global identity
assignment via pgvector similarity search, journey aggregation,
person search by image, and manual merge/split operations.
"""

from __future__ import annotations

import uuid
from datetime import datetime, timezone
from typing import Any, Optional

import numpy as np
import structlog
from sqlalchemy import and_, case, delete, distinct, func, select, text, update
from sqlalchemy.ext.asyncio import AsyncSession

from app.exceptions import InferenceError, NotFoundError, ValidationError
from app.models.reid import PersonJourney, PersonTrack, ReIDMatch
from app.utils.image_utils import decode_base64_image, numpy_to_bytes

logger = structlog.stdlib.get_logger(__name__)

# ── Constants ──────────────────────────────────────────────────────────────

EMBEDDING_DIMENSION: int = 512
DEFAULT_SIMILARITY_THRESHOLD: float = 0.55
EMA_ALPHA: float = 0.3  # Exponential moving average weight for embedding updates


# ── Feature Extraction Helpers ─────────────────────────────────────────────


def _get_reid_encoder():
    """Lazily import and instantiate the ReID encoder.

    Defers model loading until first use to avoid startup overhead
    in processes that do not need the encoder (e.g. web workers
    serving read-only endpoints).

    Returns:
        ReIDEncoder: The singleton encoder instance.
    """
    try:
        from app.cv.model_registry import ModelRegistry
        from app.cv.recognizers.reid_encoder import ReIDEncoder

        registry = ModelRegistry()
        session = registry.get_model("osnet_x1_0")
        return ReIDEncoder(session)
    except KeyError:
        logger.warning("reid_service.encoder_not_loaded", hint="OSNet model not in registry")
        return None
    except Exception as exc:
        logger.error("reid_service.encoder_init_failed", error=str(exc))
        return None


def _extract_embedding(crop: np.ndarray) -> np.ndarray:
    """Extract a 512-dim ReID feature vector from a person crop.

    Falls back to a normalised pixel hash when the ONNX model is
    unavailable (development/testing environments).

    Args:
        crop: BGR person image of shape (H, W, 3).

    Returns:
        512-dimensional L2-normalised numpy float32 vector.
    """
    encoder = _get_reid_encoder()
    if encoder is not None:
        return encoder.extract_features(crop)

    # Fallback: deterministic pseudo-embedding for dev environments
    import cv2

    logger.debug("reid_service.using_fallback_embedding")
    resized = cv2.resize(crop, (128, 256), interpolation=cv2.INTER_LINEAR)
    gray = cv2.cvtColor(resized, cv2.COLOR_BGR2GRAY)
    flat = gray.flatten().astype(np.float32)
    embedding = np.resize(flat, EMBEDDING_DIMENSION).astype(np.float32)
    norm = np.linalg.norm(embedding)
    if norm > 1e-6:
        embedding = embedding / norm
    return embedding


# ── Track Registration ─────────────────────────────────────────────────────


async def register_track(
    db: AsyncSession,
    org_id: uuid.UUID,
    camera_id: uuid.UUID,
    track_id: int,
    crop: np.ndarray,
    timestamp: datetime,
    metadata: dict | None = None,
    thumbnail_path: str | None = None,
) -> PersonTrack:
    """Register a new person track and assign a global identity.

    Extracts the appearance embedding from the person crop, searches
    existing global persons within the organization using pgvector
    cosine distance, and either assigns an existing
    ``global_person_id`` or creates a new one.

    Args:
        db: Async database session.
        org_id: Organization scope.
        camera_id: Camera that captured the track.
        track_id: Local tracker ID.
        crop: BGR person crop image.
        timestamp: Detection timestamp.
        metadata: Optional extra metadata (bbox, confidence).
        thumbnail_path: Optional pre-uploaded MinIO path.

    Returns:
        The created PersonTrack record.
    """
    embedding = _extract_embedding(crop)
    embedding_list = embedding.tolist()
    embedding_str = "[" + ",".join(str(v) for v in embedding_list) + "]"

    # Search for the most similar existing person in this org
    similarity_query = text("""
        SELECT
            global_person_id,
            1 - (embedding <=> :embedding::vector) AS similarity
        FROM person_tracks
        WHERE org_id = :org_id
          AND is_active = true
          AND camera_id != :camera_id
        ORDER BY embedding <=> :embedding::vector
        LIMIT 1
    """)

    result = await db.execute(
        similarity_query,
        {
            "embedding": embedding_str,
            "org_id": str(org_id),
            "camera_id": str(camera_id),
        },
    )
    best_match = result.first()

    global_person_id: uuid.UUID
    matched_existing = False

    if best_match and best_match.similarity >= DEFAULT_SIMILARITY_THRESHOLD:
        global_person_id = best_match.global_person_id
        matched_existing = True
        logger.info(
            "reid_service.matched_existing_person",
            global_person_id=str(global_person_id),
            similarity=round(best_match.similarity, 4),
            camera_id=str(camera_id),
        )
    else:
        # Also check same-camera tracks (different track_id)
        same_cam_query = text("""
            SELECT
                global_person_id,
                1 - (embedding <=> :embedding::vector) AS similarity
            FROM person_tracks
            WHERE org_id = :org_id
              AND camera_id = :camera_id
              AND track_id != :track_id
              AND is_active = true
            ORDER BY embedding <=> :embedding::vector
            LIMIT 1
        """)
        same_cam_result = await db.execute(
            same_cam_query,
            {
                "embedding": embedding_str,
                "org_id": str(org_id),
                "camera_id": str(camera_id),
                "track_id": track_id,
            },
        )
        same_match = same_cam_result.first()

        if same_match and same_match.similarity >= DEFAULT_SIMILARITY_THRESHOLD:
            global_person_id = same_match.global_person_id
            matched_existing = True
        else:
            global_person_id = uuid.uuid4()
            logger.info(
                "reid_service.new_person_identity",
                global_person_id=str(global_person_id),
                camera_id=str(camera_id),
            )

    # Create the track record
    track = PersonTrack(
        id=uuid.uuid4(),
        org_id=org_id,
        global_person_id=global_person_id,
        camera_id=camera_id,
        track_id=track_id,
        first_seen=timestamp,
        last_seen=timestamp,
        embedding=embedding_list,
        thumbnail_path=thumbnail_path,
        metadata_json=metadata or {},
        is_active=True,
    )
    db.add(track)
    await db.flush()

    # If matched across cameras, create a ReIDMatch record
    if matched_existing:
        existing_track_q = (
            select(PersonTrack)
            .where(
                PersonTrack.org_id == org_id,
                PersonTrack.global_person_id == global_person_id,
                PersonTrack.id != track.id,
            )
            .order_by(PersonTrack.last_seen.desc())
            .limit(1)
        )
        existing_result = await db.execute(existing_track_q)
        existing_track = existing_result.scalar_one_or_none()

        if existing_track and existing_track.camera_id != camera_id:
            from app.cv.recognizers.reid_encoder import ReIDEncoder

            sim_score = ReIDEncoder.compute_cosine_similarity(
                embedding, np.array(existing_track.embedding, dtype=np.float32)
            )
            match_record = ReIDMatch(
                id=uuid.uuid4(),
                org_id=org_id,
                track_a_id=existing_track.id,
                track_b_id=track.id,
                similarity_score=round(float(sim_score), 4),
                matched_at=datetime.now(timezone.utc),
            )
            db.add(match_record)

    # Update or create journey
    await _update_journey(db, org_id, global_person_id, camera_id, timestamp, thumbnail_path)

    await db.flush()
    logger.info(
        "reid_service.track_registered",
        track_id=str(track.id),
        global_person_id=str(global_person_id),
        matched_existing=matched_existing,
    )
    return track


# ── Track Updates ──────────────────────────────────────────────────────────


async def update_track(
    db: AsyncSession,
    track_db_id: uuid.UUID,
    crop: np.ndarray,
    timestamp: datetime,
) -> PersonTrack | None:
    """Update an existing track with a new appearance observation.

    The embedding is updated using an exponential moving average (EMA)
    to smooth out frame-to-frame variation while adapting to appearance
    changes over time.

    Args:
        db: Async database session.
        track_db_id: Database ID of the track to update.
        crop: New BGR person crop.
        timestamp: Current detection timestamp.

    Returns:
        The updated PersonTrack, or None if not found.
    """
    result = await db.execute(
        select(PersonTrack).where(PersonTrack.id == track_db_id)
    )
    track = result.scalar_one_or_none()
    if track is None:
        logger.warning("reid_service.track_not_found", track_id=str(track_db_id))
        return None

    new_embedding = _extract_embedding(crop)
    old_embedding = np.array(track.embedding, dtype=np.float32)

    # EMA update: new = alpha * new + (1 - alpha) * old
    updated_embedding = EMA_ALPHA * new_embedding + (1.0 - EMA_ALPHA) * old_embedding
    # Re-normalise
    norm = np.linalg.norm(updated_embedding)
    if norm > 1e-6:
        updated_embedding = updated_embedding / norm

    track.embedding = updated_embedding.tolist()
    track.last_seen = timestamp
    db.add(track)
    await db.flush()

    return track


async def end_track(
    db: AsyncSession,
    track_db_id: uuid.UUID,
) -> PersonTrack | None:
    """Mark a track as inactive and finalise its journey record.

    Called when the tracker loses the person or the stream ends.

    Args:
        db: Async database session.
        track_db_id: Database ID of the track.

    Returns:
        The updated PersonTrack, or None if not found.
    """
    result = await db.execute(
        select(PersonTrack).where(PersonTrack.id == track_db_id)
    )
    track = result.scalar_one_or_none()
    if track is None:
        return None

    track.is_active = False
    db.add(track)

    # Finalise journey
    await _update_journey(
        db,
        track.org_id,
        track.global_person_id,
        track.camera_id,
        track.last_seen,
        track.thumbnail_path,
    )

    await db.flush()
    logger.info(
        "reid_service.track_ended",
        track_id=str(track.id),
        global_person_id=str(track.global_person_id),
    )
    return track


# ── Journey Management ─────────────────────────────────────────────────────


async def _update_journey(
    db: AsyncSession,
    org_id: uuid.UUID,
    global_person_id: uuid.UUID,
    camera_id: uuid.UUID,
    timestamp: datetime,
    thumbnail_path: str | None = None,
) -> PersonJourney:
    """Create or update the journey record for a global person identity.

    Args:
        db: Async database session.
        org_id: Organization scope.
        global_person_id: Person identity.
        camera_id: Camera of the current sighting.
        timestamp: Current detection timestamp.
        thumbnail_path: Person crop path (optional).

    Returns:
        The updated or created PersonJourney.
    """
    result = await db.execute(
        select(PersonJourney).where(
            PersonJourney.global_person_id == global_person_id
        )
    )
    journey = result.scalar_one_or_none()

    # Resolve camera name
    from app.models.camera import Camera

    cam_result = await db.execute(
        select(Camera.name).where(Camera.id == camera_id)
    )
    camera_name = cam_result.scalar_one_or_none() or "Unknown Camera"

    event_entry = {
        "camera_id": str(camera_id),
        "camera_name": camera_name,
        "timestamp": timestamp.isoformat(),
        "thumbnail_path": thumbnail_path,
        "zone_id": None,
    }

    if journey is None:
        journey = PersonJourney(
            id=uuid.uuid4(),
            org_id=org_id,
            global_person_id=global_person_id,
            events=[event_entry],
            first_camera_id=camera_id,
            last_camera_id=camera_id,
            first_seen=timestamp,
            last_seen=timestamp,
            total_cameras_visited=1,
            total_duration_seconds=0.0,
        )
        db.add(journey)
    else:
        events = journey.events or []

        # Check if camera already recorded recently (avoid duplicates)
        camera_already_latest = (
            events
            and events[-1].get("camera_id") == str(camera_id)
        )

        if not camera_already_latest:
            events.append(event_entry)
            journey.events = events

        journey.last_camera_id = camera_id
        journey.last_seen = timestamp

        # Count distinct cameras
        distinct_cameras = set()
        for evt in events:
            distinct_cameras.add(evt.get("camera_id"))
        journey.total_cameras_visited = len(distinct_cameras)

        # Update duration
        if journey.first_seen:
            delta = (timestamp - journey.first_seen).total_seconds()
            journey.total_duration_seconds = max(0.0, delta)

        db.add(journey)

    return journey


async def get_person_journey(
    db: AsyncSession,
    org_id: uuid.UUID,
    global_person_id: uuid.UUID,
) -> dict[str, Any]:
    """Retrieve the full cross-camera journey for a person.

    Args:
        db: Async database session.
        org_id: Organization scope.
        global_person_id: Person identity UUID.

    Returns:
        Journey data dict with events, stats, and tracks.

    Raises:
        NotFoundError: If no journey exists for this person.
    """
    result = await db.execute(
        select(PersonJourney).where(
            PersonJourney.org_id == org_id,
            PersonJourney.global_person_id == global_person_id,
        )
    )
    journey = result.scalar_one_or_none()
    if journey is None:
        raise NotFoundError(resource="PersonJourney", identifier=str(global_person_id))

    # Get all tracks for this person
    tracks_result = await db.execute(
        select(PersonTrack)
        .where(
            PersonTrack.org_id == org_id,
            PersonTrack.global_person_id == global_person_id,
        )
        .order_by(PersonTrack.first_seen.asc())
    )
    tracks = list(tracks_result.scalars().all())

    tracks_data = []
    for t in tracks:
        camera_name = "Unknown"
        if t.camera:
            camera_name = t.camera.name
        tracks_data.append({
            "id": str(t.id),
            "camera_id": str(t.camera_id),
            "camera_name": camera_name,
            "track_id": t.track_id,
            "first_seen": t.first_seen.isoformat() if t.first_seen else None,
            "last_seen": t.last_seen.isoformat() if t.last_seen else None,
            "thumbnail_path": t.thumbnail_path,
            "is_active": t.is_active,
        })

    return {
        "id": str(journey.id),
        "org_id": str(journey.org_id),
        "global_person_id": str(journey.global_person_id),
        "events": journey.events or [],
        "first_camera_id": str(journey.first_camera_id) if journey.first_camera_id else None,
        "last_camera_id": str(journey.last_camera_id) if journey.last_camera_id else None,
        "first_seen": journey.first_seen.isoformat() if journey.first_seen else None,
        "last_seen": journey.last_seen.isoformat() if journey.last_seen else None,
        "total_cameras_visited": journey.total_cameras_visited,
        "total_duration_seconds": journey.total_duration_seconds,
        "tracks": tracks_data,
    }


# ── Person Search ──────────────────────────────────────────────────────────


async def search_person(
    db: AsyncSession,
    org_id: uuid.UUID,
    image: np.ndarray,
    top_k: int = 20,
    threshold: float = DEFAULT_SIMILARITY_THRESHOLD,
) -> list[dict[str, Any]]:
    """Search for a person across all cameras by image.

    Extracts the appearance embedding from the query image and
    searches all person tracks using pgvector cosine distance.

    Args:
        db: Async database session.
        org_id: Organization scope.
        image: BGR query image.
        top_k: Max results to return.
        threshold: Minimum similarity score.

    Returns:
        List of match dicts with global_person_id, similarity, camera info.
    """
    embedding = _extract_embedding(image)
    embedding_str = "[" + ",".join(str(v) for v in embedding.tolist()) + "]"

    query = text("""
        SELECT
            pt.global_person_id,
            pt.camera_id,
            c.name AS camera_name,
            pt.thumbnail_path,
            pt.first_seen,
            pt.last_seen,
            1 - (pt.embedding <=> :embedding::vector) AS similarity
        FROM person_tracks pt
        JOIN cameras c ON c.id = pt.camera_id
        WHERE pt.org_id = :org_id
          AND 1 - (pt.embedding <=> :embedding::vector) >= :threshold
        ORDER BY pt.embedding <=> :embedding::vector
        LIMIT :top_k
    """)

    result = await db.execute(
        query,
        {
            "embedding": embedding_str,
            "org_id": str(org_id),
            "threshold": threshold,
            "top_k": top_k,
        },
    )

    # Deduplicate by global_person_id, keeping best similarity
    seen_persons: dict[str, dict] = {}
    for row in result.all():
        pid = str(row.global_person_id)
        sim = float(row.similarity)
        if pid not in seen_persons or sim > seen_persons[pid]["similarity"]:
            seen_persons[pid] = {
                "global_person_id": pid,
                "similarity": round(sim, 4),
                "camera_id": str(row.camera_id),
                "camera_name": row.camera_name,
                "thumbnail_path": row.thumbnail_path,
                "first_seen": row.first_seen.isoformat() if row.first_seen else None,
                "last_seen": row.last_seen.isoformat() if row.last_seen else None,
            }

    # Get camera visit counts for matched persons
    results_list = list(seen_persons.values())
    for item in results_list:
        pid = item["global_person_id"]
        count_q = select(func.count(distinct(PersonTrack.camera_id))).where(
            PersonTrack.global_person_id == uuid.UUID(pid),
            PersonTrack.org_id == org_id,
        )
        count_result = await db.execute(count_q)
        item["cameras_visited"] = count_result.scalar() or 1

    results_list.sort(key=lambda x: x["similarity"], reverse=True)
    return results_list[:top_k]


# ── Cross-Camera Matches ──────────────────────────────────────────────────


async def get_cross_camera_matches(
    db: AsyncSession,
    org_id: uuid.UUID,
    start_date: datetime | None = None,
    end_date: datetime | None = None,
    camera_ids: list[uuid.UUID] | None = None,
    min_similarity: float = 0.0,
    is_confirmed: bool | None = None,
    page: int = 1,
    page_size: int = 20,
) -> tuple[list[dict[str, Any]], int]:
    """List cross-camera identity matches with optional filters.

    Args:
        db: Async database session.
        org_id: Organization scope.
        start_date: Optional start date filter.
        end_date: Optional end date filter.
        camera_ids: Optional camera filter.
        min_similarity: Minimum similarity score.
        is_confirmed: Filter by confirmation status.
        page: Page number (1-indexed).
        page_size: Items per page.

    Returns:
        Tuple of (matches, total_count).
    """
    base_filter = [ReIDMatch.org_id == org_id]

    if start_date:
        base_filter.append(ReIDMatch.matched_at >= start_date)
    if end_date:
        base_filter.append(ReIDMatch.matched_at <= end_date)
    if min_similarity > 0:
        base_filter.append(ReIDMatch.similarity_score >= min_similarity)
    if is_confirmed is not None:
        base_filter.append(ReIDMatch.is_confirmed == is_confirmed)

    # Count total
    count_q = select(func.count()).where(and_(*base_filter)).select_from(ReIDMatch)
    total = (await db.execute(count_q)).scalar() or 0

    # Fetch page
    query = (
        select(ReIDMatch)
        .where(and_(*base_filter))
        .order_by(ReIDMatch.matched_at.desc())
        .offset((max(1, page) - 1) * page_size)
        .limit(page_size)
    )
    result = await db.execute(query)
    matches = list(result.scalars().all())

    # Enrich with track details
    items = []
    for m in matches:
        item = {
            "id": str(m.id),
            "org_id": str(m.org_id),
            "track_a_id": str(m.track_a_id),
            "track_b_id": str(m.track_b_id),
            "similarity_score": round(m.similarity_score, 4),
            "is_confirmed": m.is_confirmed,
            "confirmed_by": str(m.confirmed_by) if m.confirmed_by else None,
            "matched_at": m.matched_at.isoformat() if m.matched_at else None,
            "created_at": m.created_at.isoformat() if m.created_at else None,
            "track_a_camera_name": None,
            "track_a_thumbnail": None,
            "track_a_first_seen": None,
            "track_b_camera_name": None,
            "track_b_thumbnail": None,
            "track_b_first_seen": None,
        }
        if m.track_a:
            item["track_a_thumbnail"] = m.track_a.thumbnail_path
            item["track_a_first_seen"] = (
                m.track_a.first_seen.isoformat() if m.track_a.first_seen else None
            )
            if m.track_a.camera:
                item["track_a_camera_name"] = m.track_a.camera.name
        if m.track_b:
            item["track_b_thumbnail"] = m.track_b.thumbnail_path
            item["track_b_first_seen"] = (
                m.track_b.first_seen.isoformat() if m.track_b.first_seen else None
            )
            if m.track_b.camera:
                item["track_b_camera_name"] = m.track_b.camera.name

        # Filter by camera if specified
        if camera_ids:
            track_cams = set()
            if m.track_a:
                track_cams.add(m.track_a.camera_id)
            if m.track_b:
                track_cams.add(m.track_b.camera_id)
            if not track_cams.intersection(set(camera_ids)):
                continue

        items.append(item)

    return items, total


# ── Match Confirmation ─────────────────────────────────────────────────────


async def confirm_match(
    db: AsyncSession,
    match_id: uuid.UUID,
    user_id: uuid.UUID,
    confirmed: bool = True,
) -> dict[str, Any]:
    """Confirm or reject a cross-camera match.

    Args:
        db: Async database session.
        match_id: ID of the match to update.
        user_id: ID of the reviewing user.
        confirmed: True to confirm, False to reject.

    Returns:
        Updated match data.

    Raises:
        NotFoundError: If the match does not exist.
    """
    result = await db.execute(
        select(ReIDMatch).where(ReIDMatch.id == match_id)
    )
    match = result.scalar_one_or_none()
    if match is None:
        raise NotFoundError(resource="ReIDMatch", identifier=str(match_id))

    match.is_confirmed = confirmed
    match.confirmed_by = user_id
    db.add(match)

    # If rejected, consider splitting the identities
    if not confirmed and match.track_a and match.track_b:
        if match.track_a.global_person_id == match.track_b.global_person_id:
            new_person_id = uuid.uuid4()
            match.track_b.global_person_id = new_person_id
            db.add(match.track_b)
            logger.info(
                "reid_service.match_rejected_split",
                match_id=str(match_id),
                new_person_id=str(new_person_id),
            )

    await db.flush()

    return {
        "id": str(match.id),
        "is_confirmed": match.is_confirmed,
        "confirmed_by": str(match.confirmed_by) if match.confirmed_by else None,
    }


# ── Merge / Split Operations ──────────────────────────────────────────────


async def merge_identities(
    db: AsyncSession,
    org_id: uuid.UUID,
    target_global_person_id: uuid.UUID,
    source_global_person_id: uuid.UUID,
) -> dict[str, Any]:
    """Merge two person identities into one.

    All tracks and journey events from ``source`` are moved to ``target``.

    Args:
        db: Async database session.
        org_id: Organization scope.
        target_global_person_id: Identity to keep.
        source_global_person_id: Identity to dissolve.

    Returns:
        Summary of the merge operation.
    """
    if target_global_person_id == source_global_person_id:
        raise ValidationError(message="Cannot merge a person with themselves.")

    # Update all source tracks to target identity
    update_q = (
        update(PersonTrack)
        .where(
            PersonTrack.org_id == org_id,
            PersonTrack.global_person_id == source_global_person_id,
        )
        .values(global_person_id=target_global_person_id)
    )
    result = await db.execute(update_q)
    tracks_moved = result.rowcount

    # Merge journey events
    source_journey_q = await db.execute(
        select(PersonJourney).where(
            PersonJourney.org_id == org_id,
            PersonJourney.global_person_id == source_global_person_id,
        )
    )
    source_journey = source_journey_q.scalar_one_or_none()

    target_journey_q = await db.execute(
        select(PersonJourney).where(
            PersonJourney.org_id == org_id,
            PersonJourney.global_person_id == target_global_person_id,
        )
    )
    target_journey = target_journey_q.scalar_one_or_none()

    if source_journey and target_journey:
        # Merge events and sort by timestamp
        merged_events = (target_journey.events or []) + (source_journey.events or [])
        merged_events.sort(key=lambda e: e.get("timestamp", ""))
        target_journey.events = merged_events

        # Update aggregate fields
        distinct_cameras = set()
        for evt in merged_events:
            distinct_cameras.add(evt.get("camera_id"))
        target_journey.total_cameras_visited = len(distinct_cameras)

        if source_journey.first_seen and (
            target_journey.first_seen is None
            or source_journey.first_seen < target_journey.first_seen
        ):
            target_journey.first_seen = source_journey.first_seen
            target_journey.first_camera_id = source_journey.first_camera_id

        if source_journey.last_seen and (
            target_journey.last_seen is None
            or source_journey.last_seen > target_journey.last_seen
        ):
            target_journey.last_seen = source_journey.last_seen
            target_journey.last_camera_id = source_journey.last_camera_id

        if target_journey.first_seen and target_journey.last_seen:
            target_journey.total_duration_seconds = (
                target_journey.last_seen - target_journey.first_seen
            ).total_seconds()

        db.add(target_journey)
        await db.delete(source_journey)

    elif source_journey and not target_journey:
        source_journey.global_person_id = target_global_person_id
        db.add(source_journey)

    await db.flush()

    logger.info(
        "reid_service.identities_merged",
        target=str(target_global_person_id),
        source=str(source_global_person_id),
        tracks_moved=tracks_moved,
    )

    return {
        "target_global_person_id": str(target_global_person_id),
        "source_global_person_id": str(source_global_person_id),
        "tracks_moved": tracks_moved,
    }


async def split_identity(
    db: AsyncSession,
    org_id: uuid.UUID,
    global_person_id: uuid.UUID,
    track_ids: list[uuid.UUID],
) -> dict[str, Any]:
    """Split selected tracks from a person identity into a new identity.

    Args:
        db: Async database session.
        org_id: Organization scope.
        global_person_id: Current person identity.
        track_ids: Track IDs to move to a new identity.

    Returns:
        Summary including the new identity UUID.
    """
    new_person_id = uuid.uuid4()

    update_q = (
        update(PersonTrack)
        .where(
            PersonTrack.org_id == org_id,
            PersonTrack.global_person_id == global_person_id,
            PersonTrack.id.in_(track_ids),
        )
        .values(global_person_id=new_person_id)
    )
    result = await db.execute(update_q)
    tracks_moved = result.rowcount

    if tracks_moved == 0:
        raise ValidationError(
            message="No matching tracks found for the given IDs."
        )

    # Create a new journey for the split tracks
    split_tracks_q = await db.execute(
        select(PersonTrack)
        .where(PersonTrack.global_person_id == new_person_id)
        .order_by(PersonTrack.first_seen.asc())
    )
    split_tracks = list(split_tracks_q.scalars().all())

    if split_tracks:
        events = []
        distinct_cameras = set()
        for t in split_tracks:
            camera_name = t.camera.name if t.camera else "Unknown"
            events.append({
                "camera_id": str(t.camera_id),
                "camera_name": camera_name,
                "timestamp": t.first_seen.isoformat() if t.first_seen else None,
                "thumbnail_path": t.thumbnail_path,
                "zone_id": None,
            })
            distinct_cameras.add(str(t.camera_id))

        new_journey = PersonJourney(
            id=uuid.uuid4(),
            org_id=org_id,
            global_person_id=new_person_id,
            events=events,
            first_camera_id=split_tracks[0].camera_id,
            last_camera_id=split_tracks[-1].camera_id,
            first_seen=split_tracks[0].first_seen,
            last_seen=split_tracks[-1].last_seen,
            total_cameras_visited=len(distinct_cameras),
            total_duration_seconds=(
                (split_tracks[-1].last_seen - split_tracks[0].first_seen).total_seconds()
                if split_tracks[0].first_seen and split_tracks[-1].last_seen
                else 0.0
            ),
        )
        db.add(new_journey)

    # Update the original journey
    await _rebuild_journey(db, org_id, global_person_id)

    await db.flush()

    logger.info(
        "reid_service.identity_split",
        original=str(global_person_id),
        new_person_id=str(new_person_id),
        tracks_moved=tracks_moved,
    )

    return {
        "original_global_person_id": str(global_person_id),
        "new_global_person_id": str(new_person_id),
        "tracks_moved": tracks_moved,
    }


async def _rebuild_journey(
    db: AsyncSession,
    org_id: uuid.UUID,
    global_person_id: uuid.UUID,
) -> None:
    """Rebuild a journey record from its remaining tracks.

    Used after split or merge operations to ensure the journey
    accurately reflects the current set of tracks.
    """
    tracks_q = await db.execute(
        select(PersonTrack)
        .where(
            PersonTrack.org_id == org_id,
            PersonTrack.global_person_id == global_person_id,
        )
        .order_by(PersonTrack.first_seen.asc())
    )
    tracks = list(tracks_q.scalars().all())

    journey_q = await db.execute(
        select(PersonJourney).where(
            PersonJourney.org_id == org_id,
            PersonJourney.global_person_id == global_person_id,
        )
    )
    journey = journey_q.scalar_one_or_none()

    if not tracks:
        if journey:
            await db.delete(journey)
        return

    events = []
    distinct_cameras = set()
    for t in tracks:
        camera_name = t.camera.name if t.camera else "Unknown"
        events.append({
            "camera_id": str(t.camera_id),
            "camera_name": camera_name,
            "timestamp": t.first_seen.isoformat() if t.first_seen else None,
            "thumbnail_path": t.thumbnail_path,
            "zone_id": None,
        })
        distinct_cameras.add(str(t.camera_id))

    if journey is None:
        journey = PersonJourney(
            id=uuid.uuid4(),
            org_id=org_id,
            global_person_id=global_person_id,
        )
        db.add(journey)

    journey.events = events
    journey.first_camera_id = tracks[0].camera_id
    journey.last_camera_id = tracks[-1].camera_id
    journey.first_seen = tracks[0].first_seen
    journey.last_seen = tracks[-1].last_seen
    journey.total_cameras_visited = len(distinct_cameras)
    journey.total_duration_seconds = (
        (tracks[-1].last_seen - tracks[0].first_seen).total_seconds()
        if tracks[0].first_seen and tracks[-1].last_seen
        else 0.0
    )
    db.add(journey)


# ── Active Persons ─────────────────────────────────────────────────────────


async def get_active_persons(
    db: AsyncSession,
    org_id: uuid.UUID,
) -> list[dict[str, Any]]:
    """Get all persons currently visible across cameras.

    Returns the latest active track per global_person_id along with
    camera and journey statistics.

    Args:
        db: Async database session.
        org_id: Organization scope.

    Returns:
        List of active person dicts.
    """
    from app.models.camera import Camera

    # Subquery: latest active track per global_person_id
    latest_sq = (
        select(
            PersonTrack.global_person_id,
            func.max(PersonTrack.last_seen).label("max_last_seen"),
        )
        .where(
            PersonTrack.org_id == org_id,
            PersonTrack.is_active == True,  # noqa: E712
        )
        .group_by(PersonTrack.global_person_id)
        .subquery()
    )

    query = (
        select(PersonTrack, Camera.name.label("camera_name"))
        .join(Camera, PersonTrack.camera_id == Camera.id)
        .join(
            latest_sq,
            and_(
                PersonTrack.global_person_id == latest_sq.c.global_person_id,
                PersonTrack.last_seen == latest_sq.c.max_last_seen,
            ),
        )
        .where(
            PersonTrack.org_id == org_id,
            PersonTrack.is_active == True,  # noqa: E712
        )
        .order_by(PersonTrack.last_seen.desc())
    )

    result = await db.execute(query)
    rows = result.all()

    persons: list[dict[str, Any]] = []
    seen_global_ids: set[str] = set()

    for row in rows:
        track = row[0]
        camera_name = row[1]
        gid = str(track.global_person_id)

        if gid in seen_global_ids:
            continue
        seen_global_ids.add(gid)

        # Get journey stats
        journey_q = await db.execute(
            select(PersonJourney).where(
                PersonJourney.global_person_id == track.global_person_id
            )
        )
        journey = journey_q.scalar_one_or_none()

        persons.append({
            "global_person_id": gid,
            "current_camera_id": str(track.camera_id),
            "current_camera_name": camera_name,
            "thumbnail_path": track.thumbnail_path,
            "first_seen": (
                journey.first_seen.isoformat()
                if journey and journey.first_seen
                else track.first_seen.isoformat()
            ),
            "last_seen": track.last_seen.isoformat(),
            "cameras_visited": (
                journey.total_cameras_visited if journey else 1
            ),
            "total_duration_seconds": (
                journey.total_duration_seconds if journey else 0.0
            ),
        })

    return persons


# ── Heatmap ────────────────────────────────────────────────────────────────


async def get_person_heatmap(
    db: AsyncSession,
    org_id: uuid.UUID,
    global_person_id: uuid.UUID,
) -> list[dict[str, Any]]:
    """Get camera visit frequency heatmap for a person.

    Args:
        db: Async database session.
        org_id: Organization scope.
        global_person_id: Person identity UUID.

    Returns:
        List of {camera_id, camera_name, visit_count, total_duration}.
    """
    from app.models.camera import Camera

    query = (
        select(
            PersonTrack.camera_id,
            Camera.name.label("camera_name"),
            func.count(PersonTrack.id).label("visit_count"),
            func.sum(
                func.extract(
                    "epoch",
                    PersonTrack.last_seen - PersonTrack.first_seen,
                )
            ).label("total_duration_seconds"),
        )
        .join(Camera, PersonTrack.camera_id == Camera.id)
        .where(
            PersonTrack.org_id == org_id,
            PersonTrack.global_person_id == global_person_id,
        )
        .group_by(PersonTrack.camera_id, Camera.name)
        .order_by(func.count(PersonTrack.id).desc())
    )

    result = await db.execute(query)
    return [
        {
            "camera_id": str(row.camera_id),
            "camera_name": row.camera_name,
            "visit_count": row.visit_count,
            "total_duration_seconds": round(float(row.total_duration_seconds or 0), 1),
        }
        for row in result.all()
    ]


# ── Statistics ─────────────────────────────────────────────────────────────


async def get_reid_stats(
    db: AsyncSession,
    org_id: uuid.UUID,
) -> dict[str, Any]:
    """Compute aggregate ReID statistics for the organization.

    Args:
        db: Async database session.
        org_id: Organization scope.

    Returns:
        Statistics dict with totals, averages, and top paths.
    """
    # Total unique persons
    total_persons_q = select(
        func.count(distinct(PersonTrack.global_person_id))
    ).where(PersonTrack.org_id == org_id)
    total_persons = (await db.execute(total_persons_q)).scalar() or 0

    # Active persons
    active_persons_q = select(
        func.count(distinct(PersonTrack.global_person_id))
    ).where(
        PersonTrack.org_id == org_id,
        PersonTrack.is_active == True,  # noqa: E712
    )
    active_persons = (await db.execute(active_persons_q)).scalar() or 0

    # Total tracks
    total_tracks_q = select(func.count(PersonTrack.id)).where(
        PersonTrack.org_id == org_id
    )
    total_tracks = (await db.execute(total_tracks_q)).scalar() or 0

    # Total matches
    total_matches_q = select(func.count(ReIDMatch.id)).where(
        ReIDMatch.org_id == org_id
    )
    total_matches = (await db.execute(total_matches_q)).scalar() or 0

    # Pending review
    pending_q = select(func.count(ReIDMatch.id)).where(
        ReIDMatch.org_id == org_id,
        ReIDMatch.is_confirmed.is_(None),
    )
    pending_review = (await db.execute(pending_q)).scalar() or 0

    # Average cameras visited
    avg_cameras_q = select(
        func.avg(PersonJourney.total_cameras_visited)
    ).where(PersonJourney.org_id == org_id)
    avg_cameras = (await db.execute(avg_cameras_q)).scalar() or 0.0

    # Most traversed path (consecutive camera pairs from journeys)
    most_traversed_path = None
    peak_crossing_hour = None

    try:
        journeys_q = await db.execute(
            select(PersonJourney.events).where(
                PersonJourney.org_id == org_id,
                PersonJourney.total_cameras_visited >= 2,
            )
        )

        path_counts: dict[str, int] = {}
        hour_counts: dict[int, int] = {}

        for (events,) in journeys_q.all():
            if not events or len(events) < 2:
                continue
            for i in range(len(events) - 1):
                cam_a = events[i].get("camera_name", "?")
                cam_b = events[i + 1].get("camera_name", "?")
                path = f"{cam_a} -> {cam_b}"
                path_counts[path] = path_counts.get(path, 0) + 1

                ts_str = events[i + 1].get("timestamp", "")
                if ts_str:
                    try:
                        from datetime import datetime as dt
                        ts = dt.fromisoformat(ts_str.replace("Z", "+00:00"))
                        hour_counts[ts.hour] = hour_counts.get(ts.hour, 0) + 1
                    except (ValueError, TypeError):
                        pass

        if path_counts:
            most_traversed_path = max(path_counts, key=path_counts.get)

        if hour_counts:
            peak_hour = max(hour_counts, key=hour_counts.get)
            peak_crossing_hour = f"{peak_hour:02d}:00"

    except Exception as exc:
        logger.warning("reid_service.stats_path_analysis_failed", error=str(exc))

    return {
        "total_persons": total_persons,
        "active_persons": active_persons,
        "total_tracks": total_tracks,
        "total_matches": total_matches,
        "pending_review": pending_review,
        "avg_cameras_visited": round(float(avg_cameras), 2),
        "most_traversed_path": most_traversed_path,
        "peak_crossing_hour": peak_crossing_hour,
    }
