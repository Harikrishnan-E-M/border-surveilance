"""Face recognition operations: enrollment, identification, search, events.

Provides person management, face enrollment with quality checking,
embedding extraction and storage in pgvector, cosine-distance based
identification/search, face event logging, and person timeline queries.
"""

from __future__ import annotations

import uuid
from datetime import datetime, timezone
from typing import Any, Optional

import numpy as np
import structlog
from sqlalchemy import func, select, text
from sqlalchemy.ext.asyncio import AsyncSession

from app.exceptions import (
    InferenceError,
    NotFoundError,
    ValidationError,
)
from app.models.attendance import AttendanceLog
from app.models.face import FaceEnrollment, FaceEvent
from app.models.person import Person, PersonType
from app.schemas.face import PersonCreate
from app.utils.image_utils import (
    compute_image_quality,
    crop_image,
    decode_base64_image,
    numpy_to_bytes,
)

logger = structlog.stdlib.get_logger(__name__)

EMBEDDING_DIMENSION = 512
MIN_QUALITY_SCORE = 50.0
DEFAULT_SIMILARITY_THRESHOLD = 0.4


# ── Person CRUD ──────────────────────────────────────────────────────────


async def create_person(
    db: AsyncSession,
    org_id: uuid.UUID,
    data: PersonCreate,
) -> Person:
    """Create a new person record in the face database.

    Args:
        db: Async database session.
        org_id: Organization the person belongs to.
        data: Person creation payload.

    Returns:
        The newly created Person instance.
    """
    logger.info("Creating person", name=data.full_name, org_id=str(org_id))

    try:
        person_type = PersonType(data.person_type.value)
    except ValueError:
        person_type = PersonType.EMPLOYEE

    person = Person(
        id=uuid.uuid4(),
        org_id=org_id,
        full_name=data.full_name,
        person_type=person_type,
        department=data.department,
        employee_id=data.employee_id,
        phone=data.phone,
        email=data.email,
        notes=data.notes,
        is_active=True,
    )
    db.add(person)
    await db.flush()

    logger.info("Person created", person_id=str(person.id), name=person.full_name)
    return person


async def get_persons(
    db: AsyncSession,
    org_id: uuid.UUID,
    page: int = 1,
    page_size: int = 20,
    search: Optional[str] = None,
    person_type: Optional[str] = None,
    is_active: Optional[bool] = None,
) -> tuple[list[Person], int]:
    """Retrieve a paginated list of persons for an organization.

    Args:
        db: Async database session.
        org_id: Organization filter.
        page: Page number (1-indexed).
        page_size: Items per page.
        search: Optional name search string.
        person_type: Optional person type filter.
        is_active: Optional active status filter.

    Returns:
        A tuple of (persons, total_count).
    """
    query = select(Person).where(Person.org_id == org_id)

    if search:
        query = query.where(
            Person.full_name.ilike(f"%{search}%")
            | Person.employee_id.ilike(f"%{search}%")
            | Person.department.ilike(f"%{search}%")
        )

    if person_type:
        try:
            pt = PersonType(person_type)
            query = query.where(Person.person_type == pt)
        except ValueError:
            pass

    if is_active is not None:
        query = query.where(Person.is_active == is_active)

    count_q = select(func.count()).select_from(query.subquery())
    total_result = await db.execute(count_q)
    total = total_result.scalar() or 0

    offset = (max(1, page) - 1) * page_size
    query = query.order_by(Person.full_name.asc()).offset(offset).limit(page_size)
    result = await db.execute(query)
    persons = list(result.scalars().all())

    return persons, total


# ── Face Enrollment ──────────────────────────────────────────────────────


def _extract_embedding(image: np.ndarray) -> np.ndarray:
    """Extract a face embedding from an image using the loaded face model.

    Attempts to use InsightFace or falls back to a dummy embedding for
    development environments.

    Args:
        image: BGR image containing a single face.

    Returns:
        512-dimensional embedding vector as numpy array.

    Raises:
        InferenceError: If embedding extraction fails.
    """
    try:
        from insightface.app import FaceAnalysis

        app = FaceAnalysis(name="buffalo_l", providers=["CUDAExecutionProvider", "CPUExecutionProvider"])
        app.prepare(ctx_id=0, det_size=(640, 640))
        faces = app.get(image)

        if not faces:
            raise InferenceError(
                message="No face detected in the image",
                code="NO_FACE_DETECTED",
            )

        embedding = faces[0].normed_embedding
        return np.array(embedding, dtype=np.float32)

    except ImportError:
        logger.warning("InsightFace not available, using normalized pixel-based embedding")
        gray = image
        if len(image.shape) == 3:
            import cv2
            gray = cv2.cvtColor(image, cv2.COLOR_BGR2GRAY)
        resized = np.resize(gray.flatten(), EMBEDDING_DIMENSION).astype(np.float32)
        norm = np.linalg.norm(resized)
        if norm > 0:
            resized = resized / norm
        return resized

    except InferenceError:
        raise

    except Exception as exc:
        raise InferenceError(
            message=f"Face embedding extraction failed: {exc}",
            code="EMBEDDING_EXTRACTION_FAILED",
        ) from exc


async def enroll_face(
    db: AsyncSession,
    org_id: uuid.UUID,
    person_id: uuid.UUID,
    images_b64: list[str],
) -> dict[str, Any]:
    """Enroll face images for a known person.

    For each image: detects the face, checks quality, extracts the
    embedding, and stores it in pgvector.

    Args:
        db: Async database session.
        org_id: Organization the person belongs to.
        person_id: Person to enroll faces for.
        images_b64: List of base64-encoded face images.

    Returns:
        Dict with person_id, enrollments_added, quality_scores,
        rejected_count, and rejection_reasons.

    Raises:
        NotFoundError: If the person does not exist.
    """
    result = await db.execute(
        select(Person).where(Person.id == person_id, Person.org_id == org_id)
    )
    person = result.scalar_one_or_none()
    if person is None:
        raise NotFoundError(resource="Person", identifier=person_id)

    enrollments_added = 0
    quality_scores: list[float] = []
    rejected_count = 0
    rejection_reasons: list[str] = []

    existing_count_q = select(func.count()).where(FaceEnrollment.person_id == person_id)
    existing_result = await db.execute(existing_count_q)
    existing_count = existing_result.scalar() or 0
    is_first = existing_count == 0

    for idx, img_b64 in enumerate(images_b64):
        try:
            image = decode_base64_image(img_b64)
        except ValueError as exc:
            rejection_reasons.append(f"Image {idx + 1}: {exc}")
            rejected_count += 1
            continue

        quality = compute_image_quality(image)
        normalized_quality = min(quality / 500.0, 1.0)

        if quality < MIN_QUALITY_SCORE:
            rejection_reasons.append(
                f"Image {idx + 1}: Quality too low ({quality:.0f}). Minimum is {MIN_QUALITY_SCORE}"
            )
            rejected_count += 1
            continue

        try:
            embedding = _extract_embedding(image)
        except InferenceError as exc:
            rejection_reasons.append(f"Image {idx + 1}: {exc.message}")
            rejected_count += 1
            continue

        image_bytes = numpy_to_bytes(image, format="jpeg", quality=90)
        image_path = f"faces/{org_id}/{person_id}/{uuid.uuid4()}.jpg"

        try:
            from app.config import get_settings
            from minio import Minio

            settings = get_settings()
            client = Minio(
                settings.MINIO_ENDPOINT,
                access_key=settings.MINIO_ACCESS_KEY,
                secret_key=settings.MINIO_SECRET_KEY,
                secure=settings.MINIO_SECURE,
            )
            import io
            client.put_object(
                settings.MINIO_BUCKET_FACES,
                image_path,
                io.BytesIO(image_bytes),
                length=len(image_bytes),
                content_type="image/jpeg",
            )
        except Exception as exc:
            logger.warning("MinIO upload failed, storing path only", error=str(exc))

        enrollment = FaceEnrollment(
            id=uuid.uuid4(),
            person_id=person_id,
            embedding=embedding.tolist(),
            image_path=image_path,
            quality_score=round(normalized_quality, 4),
            is_primary=is_first and enrollments_added == 0,
        )
        db.add(enrollment)
        enrollments_added += 1
        quality_scores.append(round(normalized_quality, 4))

    if enrollments_added > 0:
        await db.flush()

    logger.info(
        "Face enrollment completed",
        person_id=str(person_id),
        added=enrollments_added,
        rejected=rejected_count,
    )

    return {
        "person_id": str(person_id),
        "enrollments_added": enrollments_added,
        "quality_scores": quality_scores,
        "rejected_count": rejected_count,
        "rejection_reasons": rejection_reasons,
    }


# ── Face Identification & Search ─────────────────────────────────────────


async def identify_face(
    db: AsyncSession,
    org_id: uuid.UUID,
    embedding: list[float],
    threshold: float = DEFAULT_SIMILARITY_THRESHOLD,
) -> Optional[dict[str, Any]]:
    """Identify a face by comparing its embedding against enrolled faces.

    Uses pgvector's cosine distance operator for efficient similarity search.

    Args:
        db: Async database session.
        org_id: Organization scope.
        embedding: 512-dimensional face embedding.
        threshold: Minimum similarity (1 - cosine distance) to accept.

    Returns:
        Dict with person_id, person_name, confidence if matched, else None.
    """
    embedding_str = "[" + ",".join(str(v) for v in embedding) + "]"

    query = text("""
        SELECT
            fe.person_id,
            p.full_name,
            1 - (fe.embedding <=> :embedding::vector) AS similarity
        FROM face_enrollments fe
        JOIN persons p ON p.id = fe.person_id
        WHERE p.org_id = :org_id
          AND p.is_active = true
        ORDER BY fe.embedding <=> :embedding::vector
        LIMIT 1
    """)

    result = await db.execute(
        query,
        {"embedding": embedding_str, "org_id": str(org_id)},
    )
    row = result.first()

    if row is None:
        return None

    person_id, full_name, similarity = row

    if similarity >= threshold:
        return {
            "person_id": str(person_id),
            "person_name": full_name,
            "confidence": round(float(similarity), 4),
        }

    return None


async def search_face(
    db: AsyncSession,
    org_id: uuid.UUID,
    image_b64: str,
    threshold: float = DEFAULT_SIMILARITY_THRESHOLD,
    limit: int = 20,
) -> list[dict[str, Any]]:
    """Search the face database with a probe image.

    Extracts the embedding from the probe image and queries pgvector
    for the closest enrolled faces.

    Args:
        db: Async database session.
        org_id: Organization scope.
        image_b64: Base64-encoded probe image.
        threshold: Minimum similarity to include.
        limit: Maximum results.

    Returns:
        List of match dicts with person_id, person_name, confidence.
    """
    image = decode_base64_image(image_b64)
    embedding = _extract_embedding(image)
    embedding_str = "[" + ",".join(str(v) for v in embedding.tolist()) + "]"

    query = text("""
        SELECT
            fe.person_id,
            p.full_name,
            1 - (fe.embedding <=> :embedding::vector) AS similarity
        FROM face_enrollments fe
        JOIN persons p ON p.id = fe.person_id
        WHERE p.org_id = :org_id
          AND p.is_active = true
          AND 1 - (fe.embedding <=> :embedding::vector) >= :threshold
        ORDER BY fe.embedding <=> :embedding::vector
        LIMIT :limit
    """)

    result = await db.execute(
        query,
        {
            "embedding": embedding_str,
            "org_id": str(org_id),
            "threshold": threshold,
            "limit": limit,
        },
    )

    matches = []
    for row in result.all():
        person_id, full_name, similarity = row
        matches.append({
            "person_id": str(person_id),
            "person_name": full_name,
            "confidence": round(float(similarity), 4),
        })

    logger.info("Face search completed", matches_found=len(matches))
    return matches


# ── Face Events ──────────────────────────────────────────────────────────


async def log_face_event(
    db: AsyncSession,
    camera_id: uuid.UUID,
    timestamp: datetime,
    person_id: Optional[uuid.UUID] = None,
    zone_id: Optional[uuid.UUID] = None,
    confidence: Optional[float] = None,
    emotion: Optional[str] = None,
    age_estimate: Optional[int] = None,
    gender: Optional[str] = None,
    is_masked: Optional[bool] = None,
    snapshot_path: Optional[str] = None,
    bbox_json: Optional[dict] = None,
    embedding: Optional[list[float]] = None,
) -> FaceEvent:
    """Log a face detection/recognition event.

    Args:
        db: Async database session.
        camera_id: Camera that captured the event.
        timestamp: Event timestamp.
        person_id: Matched person ID (None if unknown).
        zone_id: Zone where the face was detected.
        confidence: Recognition confidence score.
        emotion: Detected emotion label.
        age_estimate: Estimated age.
        gender: Estimated gender.
        is_masked: Whether the face is masked.
        snapshot_path: Path to face snapshot.
        bbox_json: Bounding box coordinates.
        embedding: Face embedding vector.

    Returns:
        The created FaceEvent instance.
    """
    event = FaceEvent(
        id=uuid.uuid4(),
        camera_id=camera_id,
        person_id=person_id,
        zone_id=zone_id,
        timestamp=timestamp,
        confidence=confidence,
        emotion=emotion,
        age_estimate=age_estimate,
        gender=gender,
        is_masked=is_masked,
        snapshot_path=snapshot_path,
        bbox_json=bbox_json,
        embedding=embedding,
    )
    db.add(event)
    await db.flush()

    return event


# ── Timeline & Attendance ────────────────────────────────────────────────


async def get_person_timeline(
    db: AsyncSession,
    person_id: uuid.UUID,
    start_date: Optional[datetime] = None,
    end_date: Optional[datetime] = None,
    limit: int = 100,
) -> list[FaceEvent]:
    """Get a chronological timeline of face events for a person.

    Args:
        db: Async database session.
        person_id: Person to get timeline for.
        start_date: Optional start filter.
        end_date: Optional end filter.
        limit: Maximum events to return.

    Returns:
        List of FaceEvent instances ordered by timestamp descending.
    """
    query = select(FaceEvent).where(FaceEvent.person_id == person_id)

    if start_date:
        query = query.where(FaceEvent.timestamp >= start_date)
    if end_date:
        query = query.where(FaceEvent.timestamp <= end_date)

    query = query.order_by(FaceEvent.timestamp.desc()).limit(limit)
    result = await db.execute(query)
    return list(result.scalars().all())


async def get_attendance(
    db: AsyncSession,
    org_id: uuid.UUID,
    person_id: Optional[uuid.UUID] = None,
    date_val: Optional[Any] = None,
    page: int = 1,
    page_size: int = 50,
) -> tuple[list[AttendanceLog], int]:
    """Retrieve attendance logs with optional filters.

    Args:
        db: Async database session.
        org_id: Organization filter.
        person_id: Optional person filter.
        date_val: Optional date filter.
        page: Page number.
        page_size: Items per page.

    Returns:
        Tuple of (attendance_logs, total_count).
    """
    query = select(AttendanceLog).where(AttendanceLog.org_id == org_id)

    if person_id:
        query = query.where(AttendanceLog.person_id == person_id)
    if date_val:
        query = query.where(AttendanceLog.date == date_val)

    count_q = select(func.count()).select_from(query.subquery())
    total_result = await db.execute(count_q)
    total = total_result.scalar() or 0

    offset = (max(1, page) - 1) * page_size
    query = query.order_by(AttendanceLog.date.desc()).offset(offset).limit(page_size)
    result = await db.execute(query)
    logs = list(result.scalars().all())

    return logs, total
