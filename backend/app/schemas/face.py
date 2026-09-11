"""Face recognition, person management, and attendance Pydantic schemas.

Covers person CRUD, face enrollment, face search, face event logs,
and attendance tracking / reporting.
"""

import enum
import datetime as _dt
from datetime import date, datetime
from uuid import UUID

from pydantic import BaseModel, ConfigDict, EmailStr, Field


# ── Enums ─────────────────────────────────────────────────────────────────────


class PersonType(str, enum.Enum):
    """Classification of a known person in the face database."""

    EMPLOYEE = "employee"
    VISITOR = "visitor"
    VIP = "vip"
    CONTRACTOR = "contractor"
    BLOCKLIST = "blocklist"
    UNKNOWN = "unknown"


class AttendanceStatus(str, enum.Enum):
    """Derived attendance status for a person on a given day."""

    PRESENT = "present"
    ABSENT = "absent"
    LATE = "late"
    HALF_DAY = "half_day"


# ── Person CRUD ───────────────────────────────────────────────────────────────


class PersonCreate(BaseModel):
    """Request body for registering a new person in the face database."""

    full_name: str = Field(
        min_length=1,
        max_length=255,
        description="Full name of the person.",
        examples=["Priya Sharma"],
    )
    person_type: PersonType = Field(
        description="Classification of the person.",
        examples=["employee"],
    )
    department: str | None = Field(
        default=None,
        max_length=255,
        description="Department or division.",
        examples=["Engineering"],
    )
    employee_id: str | None = Field(
        default=None,
        max_length=100,
        description="Internal employee or badge ID.",
        examples=["EMP-2024-0042"],
    )
    phone: str | None = Field(
        default=None,
        max_length=20,
        description="Contact phone number.",
        examples=["+919876543210"],
    )
    email: EmailStr | None = Field(
        default=None,
        description="Contact email address.",
        examples=["priya.sharma@acme.com"],
    )
    notes: str | None = Field(
        default=None,
        max_length=2048,
        description="Free-text notes about the person.",
    )


class PersonUpdate(BaseModel):
    """Partial update for an existing person record."""

    full_name: str | None = Field(
        default=None,
        min_length=1,
        max_length=255,
        description="Updated full name.",
    )
    person_type: PersonType | None = Field(
        default=None,
        description="Updated person type.",
    )
    department: str | None = Field(
        default=None,
        max_length=255,
        description="Updated department.",
    )
    is_active: bool | None = Field(
        default=None,
        description="Enable or disable the person record.",
    )


class PersonResponse(BaseModel):
    """Full person representation returned by read endpoints."""

    model_config = ConfigDict(from_attributes=True)

    id: UUID = Field(description="Person unique identifier.")
    org_id: UUID = Field(description="Owning organization ID.")
    full_name: str = Field(description="Full name.", examples=["Priya Sharma"])
    person_type: str = Field(
        description="Person classification.",
        examples=["employee"],
    )
    department: str | None = Field(
        default=None,
        description="Department or division.",
    )
    employee_id: str | None = Field(
        default=None,
        description="Internal employee / badge ID.",
    )
    phone: str | None = Field(default=None, description="Phone number.")
    email: str | None = Field(default=None, description="Email address.")
    notes: str | None = Field(default=None, description="Notes.")
    is_active: bool = Field(description="Whether the person record is active.")
    enrollment_count: int = Field(
        default=0,
        description="Number of face embeddings enrolled for this person.",
        examples=[3],
    )
    last_seen: datetime | None = Field(
        default=None,
        description="Timestamp when this person was last detected by any camera.",
    )
    created_at: datetime = Field(description="Record creation timestamp.")
    updated_at: datetime | None = Field(
        default=None,
        description="Last update timestamp.",
    )


# ── Face Enrollment ───────────────────────────────────────────────────────────


class FaceEnrollRequest(BaseModel):
    """Request body for enrolling face images for a known person.

    Images can be provided as base64-encoded strings. For file uploads,
    use the ``multipart/form-data`` endpoint variant instead.
    """

    person_id: UUID = Field(
        description="ID of the person to enroll faces for.",
    )
    images: list[str] = Field(
        min_length=1,
        description="List of base64-encoded face images (JPEG or PNG). "
        "Each image should contain a single clearly visible face.",
    )


class FaceEnrollResponse(BaseModel):
    """Result of a face enrollment operation."""

    person_id: UUID = Field(description="Person the faces were enrolled for.")
    enrollments_added: int = Field(
        description="Number of face embeddings successfully added.",
        examples=[3],
    )
    quality_scores: list[float] = Field(
        description="Quality score (0.0-1.0) for each successfully enrolled face.",
        examples=[[0.92, 0.88, 0.95]],
    )
    rejected_count: int = Field(
        default=0,
        description="Number of images rejected due to poor quality or no face detected.",
    )
    rejection_reasons: list[str] = Field(
        default_factory=list,
        description="Reason for each rejected image.",
        examples=[["No face detected in image 4"]],
    )


# ── Face Search ───────────────────────────────────────────────────────────────


class FaceSearchRequest(BaseModel):
    """Request body for searching the face database with a probe image."""

    image: str = Field(
        description="Base64-encoded probe image containing the face to search for.",
    )
    threshold: float = Field(
        default=0.4,
        ge=0.0,
        le=1.0,
        description="Minimum similarity score to include in results (0.0-1.0).",
        examples=[0.4],
    )
    limit: int = Field(
        default=20,
        ge=1,
        le=100,
        description="Maximum number of search results to return.",
        examples=[20],
    )


class FaceSearchResult(BaseModel):
    """Single result from a face search operation."""

    person_id: UUID | None = Field(
        default=None,
        description="Matched person ID (null if unknown).",
    )
    person_name: str | None = Field(
        default=None,
        description="Matched person name (null if unknown).",
        examples=["Priya Sharma"],
    )
    confidence: float = Field(
        ge=0.0,
        le=1.0,
        description="Similarity score between the probe face and the match.",
        examples=[0.94],
    )
    camera_id: UUID = Field(description="Camera where the face was seen.")
    camera_name: str = Field(description="Camera name.", examples=["Main Entrance"])
    timestamp: datetime = Field(
        description="Timestamp when the face was captured.",
    )
    snapshot_url: str | None = Field(
        default=None,
        description="URL of the face snapshot.",
    )


# ── Face Events ───────────────────────────────────────────────────────────────


class FaceEventResponse(BaseModel):
    """A single face detection/recognition event from the event log."""

    model_config = ConfigDict(from_attributes=True)

    id: UUID = Field(description="Event unique identifier.")
    camera_id: UUID = Field(description="Camera that captured the event.")
    camera_name: str = Field(description="Camera name.", examples=["Main Entrance"])
    person_id: UUID | None = Field(
        default=None,
        description="Matched person ID (null if unrecognized).",
    )
    person_name: str | None = Field(
        default=None,
        description="Matched person name (null if unrecognized).",
    )
    confidence: float = Field(
        ge=0.0,
        le=1.0,
        description="Recognition confidence score.",
        examples=[0.91],
    )
    emotion: str | None = Field(
        default=None,
        description="Detected emotion label.",
        examples=["happy", "neutral", "angry", "sad", "surprised"],
    )
    age_estimate: int | None = Field(
        default=None,
        ge=0,
        le=150,
        description="Estimated age of the person.",
        examples=[32],
    )
    gender: str | None = Field(
        default=None,
        description="Estimated gender.",
        examples=["male", "female"],
    )
    timestamp: datetime = Field(description="Event timestamp.")
    snapshot_url: str | None = Field(
        default=None,
        description="URL of the captured face snapshot.",
    )


# ── Attendance ────────────────────────────────────────────────────────────────


class AttendanceResponse(BaseModel):
    """Attendance record for a person on a specific date."""

    person_id: UUID = Field(description="Person identifier.")
    person_name: str = Field(
        description="Person full name.",
        examples=["Priya Sharma"],
    )
    record_date: date = Field(
        alias="date",
        description="Date of the attendance record.",
        examples=["2025-06-15"],
    )
    first_seen: datetime = Field(
        description="Earliest face detection timestamp on this date.",
    )
    last_seen: datetime = Field(
        description="Latest face detection timestamp on this date.",
    )
    total_duration_seconds: float = Field(
        description="Total time the person was present in seconds.",
        examples=[28800.0],
    )
    status: AttendanceStatus = Field(
        description="Derived attendance status.",
        examples=["present"],
    )
    department: str | None = Field(
        default=None,
        description="Person's department for filtering convenience.",
    )


class AttendanceReportRequest(BaseModel):
    """Request parameters for generating an attendance report."""

    start_date: date = Field(
        description="Report period start date (inclusive).",
        examples=["2025-06-01"],
    )
    end_date: date = Field(
        description="Report period end date (inclusive).",
        examples=["2025-06-30"],
    )
    department: str | None = Field(
        default=None,
        description="Filter by department. Null includes all departments.",
        examples=["Engineering"],
    )
    person_ids: list[UUID] | None = Field(
        default=None,
        description="Filter to specific person IDs. Null includes all persons.",
    )
