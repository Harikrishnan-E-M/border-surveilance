"""
Face recognition API endpoints.

Provides person enrollment, face image management, face search,
face event listing, person timeline, and attendance tracking.
"""

from __future__ import annotations

import csv
import io
import math
import uuid
from datetime import date, datetime, timedelta, timezone
from typing import Optional

import structlog
from fastapi import APIRouter, Depends, File, HTTPException, Query, UploadFile, status
from fastapi.responses import StreamingResponse
from pydantic import BaseModel, Field
from sqlalchemy import and_, func, select, desc
from sqlalchemy.ext.asyncio import AsyncSession

from app.database import get_db_session
from app.exceptions import (
    AuthorizationError,
    NotFoundError,
    ValidationError,
)
from app.middleware.auth import JWTBearer, TokenPayload
from app.models.attendance import AttendanceLog, AttendanceStatus
from app.models.camera import Camera
from app.models.face import FaceEnrollment, FaceEvent
from app.models.person import Person, PersonType
from app.models.user import User, UserRole
from app.schemas.common import ErrorResponse, SuccessResponse

logger = structlog.stdlib.get_logger(__name__)

router = APIRouter()


# ---------------------------------------------------------------------------
# Pydantic schemas
# ---------------------------------------------------------------------------


class PersonCreate(BaseModel):
    full_name: str = Field(..., min_length=1, max_length=255)
    person_type: str = Field(default="employee", description="Person type: employee, visitor, vip, blacklisted, contractor, student")
    department: str | None = Field(default=None, max_length=255)
    employee_id: str | None = Field(default=None, max_length=100)
    phone: str | None = Field(default=None, max_length=20)
    email: str | None = Field(default=None, max_length=320)
    notes: str | None = None


class PersonUpdate(BaseModel):
    full_name: str | None = Field(default=None, max_length=255)
    person_type: str | None = None
    department: str | None = Field(default=None, max_length=255)
    employee_id: str | None = Field(default=None, max_length=100)
    phone: str | None = Field(default=None, max_length=20)
    email: str | None = Field(default=None, max_length=320)
    notes: str | None = None
    is_active: bool | None = None


class PersonResponse(BaseModel):
    id: uuid.UUID
    org_id: uuid.UUID
    full_name: str
    person_type: str
    department: str | None = None
    employee_id: str | None = None
    phone: str | None = None
    email: str | None = None
    notes: str | None = None
    is_active: bool
    enrollment_count: int = 0
    created_at: datetime
    updated_at: datetime

    class Config:
        from_attributes = True


class FaceEventResponse(BaseModel):
    id: uuid.UUID
    camera_id: uuid.UUID
    person_id: uuid.UUID | None = None
    zone_id: uuid.UUID | None = None
    timestamp: datetime
    confidence: float | None = None
    emotion: str | None = None
    age_estimate: int | None = None
    gender: str | None = None
    is_masked: bool | None = None
    snapshot_path: str | None = None
    created_at: datetime

    class Config:
        from_attributes = True


class AttendanceExportRequest(BaseModel):
    start_date: date
    end_date: date
    department: str | None = None
    format: str = Field(default="csv", description="Export format: csv")


# ---------------------------------------------------------------------------
# Helpers
# ---------------------------------------------------------------------------


async def _get_current_user(
    token: TokenPayload = Depends(JWTBearer()),
    db: AsyncSession = Depends(get_db_session),
) -> User:
    result = await db.execute(
        select(User).where(User.id == uuid.UUID(token.sub), User.is_active.is_(True))
    )
    user = result.scalars().first()
    if not user:
        raise HTTPException(status_code=401, detail="User not found or deactivated.")
    return user


def _require_manager(user: User) -> None:
    allowed = {UserRole.SUPER_ADMIN, UserRole.ORG_ADMIN, UserRole.MANAGER}
    if user.role not in allowed:
        raise AuthorizationError(message="Manager role or higher is required.")


def _person_to_response(person: Person, enrollment_count: int = 0) -> dict:
    return PersonResponse(
        id=person.id,
        org_id=person.org_id,
        full_name=person.full_name,
        person_type=person.person_type.value if isinstance(person.person_type, PersonType) else person.person_type,
        department=person.department,
        employee_id=person.employee_id,
        phone=person.phone,
        email=person.email,
        notes=person.notes,
        is_active=person.is_active,
        enrollment_count=enrollment_count,
        created_at=person.created_at,
        updated_at=person.updated_at,
    ).model_dump(mode="json")


def _face_event_to_response(event: FaceEvent) -> dict:
    return FaceEventResponse(
        id=event.id,
        camera_id=event.camera_id,
        person_id=event.person_id,
        zone_id=event.zone_id,
        timestamp=event.timestamp,
        confidence=event.confidence,
        emotion=event.emotion,
        age_estimate=event.age_estimate,
        gender=event.gender,
        is_masked=event.is_masked,
        snapshot_path=event.snapshot_path,
        created_at=event.created_at,
    ).model_dump(mode="json")


# ---------------------------------------------------------------------------
# GET /persons - List enrolled persons
# ---------------------------------------------------------------------------


@router.get(
    "/persons",
    response_model=SuccessResponse,
    summary="List enrolled persons (paginated, searchable)",
)
async def list_persons(
    search: str | None = Query(None, description="Search by name or employee ID"),
    person_type: str | None = Query(None),
    department: str | None = Query(None),
    is_active: bool | None = Query(None),
    page: int = Query(1, ge=1),
    page_size: int = Query(20, ge=1, le=100),
    user: User = Depends(_get_current_user),
    db: AsyncSession = Depends(get_db_session),
) -> dict:
    """Return a paginated list of enrolled persons."""
    query = select(Person).where(Person.org_id == user.org_id)

    if search:
        query = query.where(
            Person.full_name.ilike(f"%{search}%") | Person.employee_id.ilike(f"%{search}%")
        )
    if person_type:
        try:
            query = query.where(Person.person_type == PersonType(person_type))
        except ValueError:
            raise ValidationError(message=f"Invalid person_type: {person_type}")
    if department:
        query = query.where(Person.department.ilike(f"%{department}%"))
    if is_active is not None:
        query = query.where(Person.is_active == is_active)

    count_q = select(func.count()).select_from(query.subquery())
    total = (await db.execute(count_q)).scalar() or 0

    query = query.order_by(Person.full_name.asc()).offset((page - 1) * page_size).limit(page_size)
    result = await db.execute(query)
    persons = result.scalars().all()

    # Get enrollment counts for each person
    person_ids = [p.id for p in persons]
    enrollment_counts = {}
    if person_ids:
        ec_query = (
            select(FaceEnrollment.person_id, func.count().label("count"))
            .where(FaceEnrollment.person_id.in_(person_ids))
            .group_by(FaceEnrollment.person_id)
        )
        ec_result = await db.execute(ec_query)
        enrollment_counts = {row.person_id: row.count for row in ec_result.all()}

    return {
        "status": "success",
        "data": [_person_to_response(p, enrollment_counts.get(p.id, 0)) for p in persons],
        "meta": {
            "page": page,
            "page_size": page_size,
            "total": total,
            "total_pages": math.ceil(total / page_size) if page_size else 0,
        },
    }


# ---------------------------------------------------------------------------
# POST /persons - Create person
# ---------------------------------------------------------------------------


@router.post(
    "/persons",
    response_model=SuccessResponse,
    status_code=status.HTTP_201_CREATED,
    summary="Create a new person",
    responses={403: {"model": ErrorResponse}},
)
async def create_person(
    body: PersonCreate,
    user: User = Depends(_get_current_user),
    db: AsyncSession = Depends(get_db_session),
) -> dict:
    """Create a new person record. Requires manager role or above."""
    _require_manager(user)

    try:
        person_type = PersonType(body.person_type)
    except ValueError:
        valid = [t.value for t in PersonType]
        raise ValidationError(message=f"Invalid person_type '{body.person_type}'. Must be one of: {', '.join(valid)}")

    person = Person(
        org_id=user.org_id,
        full_name=body.full_name,
        person_type=person_type,
        department=body.department,
        employee_id=body.employee_id,
        phone=body.phone,
        email=body.email,
        notes=body.notes,
    )
    db.add(person)
    await db.flush()

    logger.info("Person created", person_id=str(person.id), name=person.full_name)

    return {
        "status": "success",
        "data": _person_to_response(person),
        "message": "Person created successfully.",
    }


# ---------------------------------------------------------------------------
# GET /persons/{person_id} - Get person details
# ---------------------------------------------------------------------------


@router.get(
    "/persons/{person_id}",
    response_model=SuccessResponse,
    summary="Get person details with enrollment count",
    responses={404: {"model": ErrorResponse}},
)
async def get_person(
    person_id: uuid.UUID,
    user: User = Depends(_get_current_user),
    db: AsyncSession = Depends(get_db_session),
) -> dict:
    """Retrieve a single person by ID with enrollment count."""
    result = await db.execute(
        select(Person).where(Person.id == person_id, Person.org_id == user.org_id)
    )
    person = result.scalars().first()
    if not person:
        raise NotFoundError(resource="Person", identifier=str(person_id))

    # Get enrollment count
    ec_result = await db.execute(
        select(func.count()).where(FaceEnrollment.person_id == person_id)
    )
    enrollment_count = ec_result.scalar() or 0

    # Get enrollments
    enrollments_result = await db.execute(
        select(FaceEnrollment).where(FaceEnrollment.person_id == person_id).order_by(FaceEnrollment.created_at.desc())
    )
    enrollments = enrollments_result.scalars().all()
    enrollment_data = [
        {
            "id": str(e.id),
            "image_path": e.image_path,
            "quality_score": e.quality_score,
            "is_primary": e.is_primary,
            "created_at": e.created_at.isoformat() if e.created_at else None,
        }
        for e in enrollments
    ]

    person_data = _person_to_response(person, enrollment_count)
    person_data["enrollments"] = enrollment_data

    return {
        "status": "success",
        "data": person_data,
    }


# ---------------------------------------------------------------------------
# PUT /persons/{person_id} - Update person
# ---------------------------------------------------------------------------


@router.put(
    "/persons/{person_id}",
    response_model=SuccessResponse,
    summary="Update person details",
    responses={404: {"model": ErrorResponse}},
)
async def update_person(
    person_id: uuid.UUID,
    body: PersonUpdate,
    user: User = Depends(_get_current_user),
    db: AsyncSession = Depends(get_db_session),
) -> dict:
    """Update person fields."""
    result = await db.execute(
        select(Person).where(Person.id == person_id, Person.org_id == user.org_id)
    )
    person = result.scalars().first()
    if not person:
        raise NotFoundError(resource="Person", identifier=str(person_id))

    update_data = body.model_dump(exclude_unset=True)

    if "person_type" in update_data:
        try:
            update_data["person_type"] = PersonType(update_data["person_type"])
        except ValueError:
            raise ValidationError(message=f"Invalid person_type: {update_data['person_type']}")

    for field, value in update_data.items():
        setattr(person, field, value)

    db.add(person)
    await db.flush()

    logger.info("Person updated", person_id=str(person.id))

    return {
        "status": "success",
        "data": _person_to_response(person),
        "message": "Person updated successfully.",
    }


# ---------------------------------------------------------------------------
# DELETE /persons/{person_id} - Delete person
# ---------------------------------------------------------------------------


@router.delete(
    "/persons/{person_id}",
    response_model=SuccessResponse,
    summary="Delete person (soft delete)",
    responses={403: {"model": ErrorResponse}, 404: {"model": ErrorResponse}},
)
async def delete_person(
    person_id: uuid.UUID,
    user: User = Depends(_get_current_user),
    db: AsyncSession = Depends(get_db_session),
) -> dict:
    """Soft-delete a person by deactivating. Requires manager role or above."""
    _require_manager(user)

    result = await db.execute(
        select(Person).where(Person.id == person_id, Person.org_id == user.org_id)
    )
    person = result.scalars().first()
    if not person:
        raise NotFoundError(resource="Person", identifier=str(person_id))

    person.is_active = False
    db.add(person)
    await db.flush()

    logger.info("Person deactivated", person_id=str(person.id))

    return {
        "status": "success",
        "data": None,
        "message": "Person deleted successfully.",
    }


# ---------------------------------------------------------------------------
# POST /enroll - Enroll face images for a person
# ---------------------------------------------------------------------------


@router.post(
    "/enroll",
    response_model=SuccessResponse,
    status_code=status.HTTP_201_CREATED,
    summary="Enroll face images for a person",
)
async def enroll_faces(
    person_id: uuid.UUID = Query(..., description="Person ID to enroll faces for"),
    images: list[UploadFile] = File(..., description="Face images to enroll"),
    user: User = Depends(_get_current_user),
    db: AsyncSession = Depends(get_db_session),
) -> dict:
    """Upload face images for enrollment. Processes images to extract embeddings."""
    # Verify person belongs to org
    result = await db.execute(
        select(Person).where(Person.id == person_id, Person.org_id == user.org_id)
    )
    person = result.scalars().first()
    if not person:
        raise NotFoundError(resource="Person", identifier=str(person_id))

    if not images:
        raise ValidationError(message="At least one image is required for enrollment.")

    enrolled = []
    errors = []

    for idx, image in enumerate(images):
        # Validate image type
        if not image.content_type or not image.content_type.startswith("image/"):
            errors.append({"index": idx, "filename": image.filename, "error": "Not a valid image file"})
            continue

        try:
            image_bytes = await image.read()

            # Store image and generate embedding via service
            image_path = f"faces/{user.org_id}/{person_id}/{uuid.uuid4()}.jpg"
            embedding = None
            quality_score = None

            try:
                from app.services.face_service import process_enrollment_image
                result_data = await process_enrollment_image(
                    image_bytes=image_bytes,
                    storage_path=image_path,
                )
                embedding = result_data.get("embedding")
                quality_score = result_data.get("quality_score")
                image_path = result_data.get("image_path", image_path)
            except ImportError:
                logger.warning("Face service not available; creating enrollment record without embedding")
            except Exception as exc:
                errors.append({"index": idx, "filename": image.filename, "error": str(exc)})
                continue

            # Check if this is the first enrollment (make it primary)
            existing_count = await db.execute(
                select(func.count()).where(FaceEnrollment.person_id == person_id)
            )
            is_first = (existing_count.scalar() or 0) == 0

            enrollment = FaceEnrollment(
                person_id=person_id,
                embedding=embedding if embedding else [0.0] * 512,
                image_path=image_path,
                quality_score=quality_score,
                is_primary=is_first,
            )
            db.add(enrollment)
            enrolled.append({"filename": image.filename, "image_path": image_path})

        except Exception as exc:
            errors.append({"index": idx, "filename": image.filename, "error": str(exc)})

    if enrolled:
        await db.flush()

    logger.info(
        "Face enrollment completed",
        person_id=str(person_id),
        enrolled_count=len(enrolled),
        error_count=len(errors),
    )

    return {
        "status": "success",
        "data": {
            "person_id": str(person_id),
            "enrolled": len(enrolled),
            "failed": len(errors),
            "enrollments": enrolled,
            "errors": errors[:20],
        },
        "message": f"Enrolled {len(enrolled)} face image(s) for {person.full_name}.",
    }


# ---------------------------------------------------------------------------
# POST /search - Search by face image upload
# ---------------------------------------------------------------------------


@router.post(
    "/search",
    response_model=SuccessResponse,
    summary="Search by face image upload",
)
async def search_face(
    image: UploadFile = File(..., description="Face image to search"),
    threshold: float = Query(0.6, ge=0.0, le=1.0, description="Matching threshold"),
    limit: int = Query(10, ge=1, le=50),
    user: User = Depends(_get_current_user),
    db: AsyncSession = Depends(get_db_session),
) -> dict:
    """Upload a face image to search for matching face events and enrolled persons."""
    if not image.content_type or not image.content_type.startswith("image/"):
        raise ValidationError(message="Uploaded file is not a valid image.")

    image_bytes = await image.read()

    try:
        from app.services.face_service import search_face_by_image
        results = await search_face_by_image(
            image_bytes=image_bytes,
            org_id=str(user.org_id),
            threshold=threshold,
            limit=limit,
        )
    except ImportError:
        logger.warning("Face service not available for search")
        results = {"matches": [], "message": "Face search service is not available."}
    except Exception as exc:
        logger.error("Face search failed", error=str(exc))
        raise ValidationError(message=f"Face search failed: {str(exc)}")

    return {
        "status": "success",
        "data": results,
    }


# ---------------------------------------------------------------------------
# GET /events - List face events
# ---------------------------------------------------------------------------


@router.get(
    "/events",
    response_model=SuccessResponse,
    summary="List face events (paginated, filterable)",
)
async def list_face_events(
    camera_id: uuid.UUID | None = Query(None),
    person_id: uuid.UUID | None = Query(None),
    start_date: datetime | None = Query(None),
    end_date: datetime | None = Query(None),
    recognized_only: bool | None = Query(None, description="Filter for recognized faces only"),
    page: int = Query(1, ge=1),
    page_size: int = Query(20, ge=1, le=100),
    user: User = Depends(_get_current_user),
    db: AsyncSession = Depends(get_db_session),
) -> dict:
    """Return a paginated list of face detection events."""
    query = (
        select(FaceEvent)
        .join(Camera, FaceEvent.camera_id == Camera.id)
        .where(Camera.org_id == user.org_id)
    )

    if camera_id:
        query = query.where(FaceEvent.camera_id == camera_id)
    if person_id:
        query = query.where(FaceEvent.person_id == person_id)
    if start_date:
        query = query.where(FaceEvent.timestamp >= start_date)
    if end_date:
        query = query.where(FaceEvent.timestamp <= end_date)
    if recognized_only is True:
        query = query.where(FaceEvent.person_id.isnot(None))
    elif recognized_only is False:
        query = query.where(FaceEvent.person_id.is_(None))

    count_q = select(func.count()).select_from(query.subquery())
    total = (await db.execute(count_q)).scalar() or 0

    query = query.order_by(FaceEvent.timestamp.desc()).offset((page - 1) * page_size).limit(page_size)
    result = await db.execute(query)
    events = result.scalars().all()

    return {
        "status": "success",
        "data": [_face_event_to_response(e) for e in events],
        "meta": {
            "page": page,
            "page_size": page_size,
            "total": total,
            "total_pages": math.ceil(total / page_size) if page_size else 0,
        },
    }


# ---------------------------------------------------------------------------
# GET /persons/{person_id}/timeline - Person sighting timeline
# ---------------------------------------------------------------------------


@router.get(
    "/persons/{person_id}/timeline",
    response_model=SuccessResponse,
    summary="Get person sighting timeline",
    responses={404: {"model": ErrorResponse}},
)
async def person_timeline(
    person_id: uuid.UUID,
    start_date: datetime | None = Query(None),
    end_date: datetime | None = Query(None),
    limit: int = Query(50, ge=1, le=200),
    user: User = Depends(_get_current_user),
    db: AsyncSession = Depends(get_db_session),
) -> dict:
    """Get a timeline of sightings for a specific person."""
    # Verify person belongs to org
    person_result = await db.execute(
        select(Person).where(Person.id == person_id, Person.org_id == user.org_id)
    )
    person = person_result.scalars().first()
    if not person:
        raise NotFoundError(resource="Person", identifier=str(person_id))

    query = select(FaceEvent).where(FaceEvent.person_id == person_id)

    if start_date:
        query = query.where(FaceEvent.timestamp >= start_date)
    if end_date:
        query = query.where(FaceEvent.timestamp <= end_date)

    query = query.order_by(FaceEvent.timestamp.desc()).limit(limit)
    result = await db.execute(query)
    events = result.scalars().all()

    timeline = [
        {
            "id": str(e.id),
            "camera_id": str(e.camera_id),
            "zone_id": str(e.zone_id) if e.zone_id else None,
            "timestamp": e.timestamp.isoformat(),
            "confidence": e.confidence,
            "emotion": e.emotion,
            "snapshot_path": e.snapshot_path,
        }
        for e in events
    ]

    return {
        "status": "success",
        "data": {
            "person": _person_to_response(person),
            "timeline": timeline,
            "total_sightings": len(timeline),
        },
    }


# ---------------------------------------------------------------------------
# GET /attendance - Attendance records
# ---------------------------------------------------------------------------


@router.get(
    "/attendance",
    response_model=SuccessResponse,
    summary="Get attendance records",
)
async def list_attendance(
    target_date: date | None = Query(None, alias="date", description="Filter by date (YYYY-MM-DD)"),
    department: str | None = Query(None),
    attendance_status: str | None = Query(None, alias="status"),
    page: int = Query(1, ge=1),
    page_size: int = Query(20, ge=1, le=100),
    user: User = Depends(_get_current_user),
    db: AsyncSession = Depends(get_db_session),
) -> dict:
    """Return attendance records filtered by date and department."""
    query = select(AttendanceLog).where(AttendanceLog.org_id == user.org_id)

    if target_date:
        query = query.where(AttendanceLog.date == target_date)
    if department:
        query = query.join(Person, AttendanceLog.person_id == Person.id).where(
            Person.department.ilike(f"%{department}%")
        )
    if attendance_status:
        try:
            query = query.where(AttendanceLog.status == AttendanceStatus(attendance_status))
        except ValueError:
            raise ValidationError(message=f"Invalid attendance status: {attendance_status}")

    count_q = select(func.count()).select_from(query.subquery())
    total = (await db.execute(count_q)).scalar() or 0

    query = query.order_by(AttendanceLog.date.desc(), AttendanceLog.first_seen.desc())
    query = query.offset((page - 1) * page_size).limit(page_size)
    result = await db.execute(query)
    records = result.scalars().all()

    data = [
        {
            "id": str(r.id),
            "person_id": str(r.person_id),
            "camera_id": str(r.camera_id),
            "date": r.date.isoformat(),
            "first_seen": r.first_seen.isoformat() if r.first_seen else None,
            "last_seen": r.last_seen.isoformat() if r.last_seen else None,
            "total_duration_seconds": r.total_duration_seconds,
            "status": r.status.value if isinstance(r.status, AttendanceStatus) else r.status,
            "created_at": r.created_at.isoformat() if r.created_at else None,
        }
        for r in records
    ]

    return {
        "status": "success",
        "data": data,
        "meta": {
            "page": page,
            "page_size": page_size,
            "total": total,
            "total_pages": math.ceil(total / page_size) if page_size else 0,
        },
    }


# ---------------------------------------------------------------------------
# GET /attendance/report - Attendance report summary
# ---------------------------------------------------------------------------


@router.get(
    "/attendance/report",
    response_model=SuccessResponse,
    summary="Get attendance report summary",
)
async def attendance_report(
    start_date: date = Query(..., description="Report start date"),
    end_date: date = Query(..., description="Report end date"),
    department: str | None = Query(None),
    user: User = Depends(_get_current_user),
    db: AsyncSession = Depends(get_db_session),
) -> dict:
    """Generate an attendance summary report for a date range."""
    base_filter = [
        AttendanceLog.org_id == user.org_id,
        AttendanceLog.date >= start_date,
        AttendanceLog.date <= end_date,
    ]

    if department:
        base_filter.append(
            AttendanceLog.person_id.in_(
                select(Person.id).where(Person.department.ilike(f"%{department}%"))
            )
        )

    # Total unique persons with attendance
    persons_q = (
        select(func.count(func.distinct(AttendanceLog.person_id)))
        .where(and_(*base_filter))
    )
    total_persons = (await db.execute(persons_q)).scalar() or 0

    # Counts by status
    status_q = (
        select(AttendanceLog.status, func.count().label("count"))
        .where(and_(*base_filter))
        .group_by(AttendanceLog.status)
    )
    status_result = await db.execute(status_q)
    by_status = {
        (row.status.value if isinstance(row.status, AttendanceStatus) else row.status): row.count
        for row in status_result.all()
    }

    # Average first-seen time
    avg_first_seen_q = (
        select(func.avg(func.extract("epoch", AttendanceLog.first_seen)))
        .where(and_(*base_filter))
        .where(AttendanceLog.first_seen.isnot(None))
    )
    avg_first_epoch = (await db.execute(avg_first_seen_q)).scalar()

    return {
        "status": "success",
        "data": {
            "start_date": start_date.isoformat(),
            "end_date": end_date.isoformat(),
            "department": department,
            "total_persons": total_persons,
            "by_status": by_status,
            "total_records": sum(by_status.values()) if by_status else 0,
            "avg_first_seen_epoch": avg_first_epoch,
        },
    }


# ---------------------------------------------------------------------------
# POST /attendance/export - Export attendance CSV
# ---------------------------------------------------------------------------


@router.post(
    "/attendance/export",
    summary="Export attendance data as CSV",
)
async def export_attendance(
    body: AttendanceExportRequest,
    user: User = Depends(_get_current_user),
    db: AsyncSession = Depends(get_db_session),
) -> StreamingResponse:
    """Export attendance records as CSV file."""
    query = (
        select(AttendanceLog, Person.full_name, Person.department, Person.employee_id)
        .join(Person, AttendanceLog.person_id == Person.id)
        .where(
            AttendanceLog.org_id == user.org_id,
            AttendanceLog.date >= body.start_date,
            AttendanceLog.date <= body.end_date,
        )
    )

    if body.department:
        query = query.where(Person.department.ilike(f"%{body.department}%"))

    query = query.order_by(AttendanceLog.date.asc(), Person.full_name.asc())
    result = await db.execute(query)
    rows = result.all()

    # Build CSV
    output = io.StringIO()
    writer = csv.writer(output)
    writer.writerow([
        "Date", "Employee ID", "Full Name", "Department",
        "First Seen", "Last Seen", "Duration (seconds)", "Status",
    ])

    for row in rows:
        log = row[0]
        writer.writerow([
            log.date.isoformat(),
            row.employee_id or "",
            row.full_name,
            row.department or "",
            log.first_seen.isoformat() if log.first_seen else "",
            log.last_seen.isoformat() if log.last_seen else "",
            log.total_duration_seconds or "",
            log.status.value if isinstance(log.status, AttendanceStatus) else log.status,
        ])

    output.seek(0)
    filename = f"attendance_{body.start_date}_{body.end_date}.csv"

    return StreamingResponse(
        iter([output.getvalue()]),
        media_type="text/csv",
        headers={"Content-Disposition": f"attachment; filename={filename}"},
    )
