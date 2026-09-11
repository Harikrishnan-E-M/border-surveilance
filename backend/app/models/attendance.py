"""Attendance log model for tracking person presence."""

import enum
import uuid
from datetime import date, datetime
from typing import TYPE_CHECKING, Optional

from sqlalchemy import String, Date, DateTime, Float, ForeignKey, Integer, func
from sqlalchemy import Enum as SAEnum
from sqlalchemy.dialects.postgresql import UUID
from sqlalchemy.orm import Mapped, mapped_column, relationship

from app.database import Base, TimestampMixin

if TYPE_CHECKING:
    from app.models.camera import Camera
    from app.models.organization import Organization
    from app.models.person import Person


class AttendanceStatus(str, enum.Enum):
    """Daily attendance status derived from first/last seen times."""

    PRESENT = "present"
    LATE = "late"
    ABSENT = "absent"
    HALF_DAY = "half_day"


class AttendanceLog(Base):
    """Daily attendance record for a person at a specific camera/location.

    Computed from face recognition events. Tracks first and last
    sighting times and total visible duration for the day. Status
    is derived from organization-specific attendance rules.
    """

    __tablename__ = "attendance_logs"

    person_id: Mapped[uuid.UUID] = mapped_column(
        UUID(as_uuid=True), ForeignKey("persons.id", ondelete="CASCADE"), nullable=False, index=True
    )
    camera_id: Mapped[uuid.UUID] = mapped_column(
        UUID(as_uuid=True), ForeignKey("cameras.id", ondelete="CASCADE"), nullable=False, index=True
    )
    org_id: Mapped[uuid.UUID] = mapped_column(
        UUID(as_uuid=True), ForeignKey("organizations.id", ondelete="CASCADE"), nullable=False, index=True
    )
    date: Mapped[date] = mapped_column(Date, nullable=False, index=True)
    first_seen: Mapped[Optional[datetime]] = mapped_column(
        DateTime(timezone=True), nullable=True
    )
    last_seen: Mapped[Optional[datetime]] = mapped_column(
        DateTime(timezone=True), nullable=True
    )
    total_duration_seconds: Mapped[Optional[int]] = mapped_column(Integer, nullable=True)
    status: Mapped[AttendanceStatus] = mapped_column(
        String(32),
        nullable=False,
        default=AttendanceStatus.PRESENT,
    )

    # ── Relationships ──────────────────────────────────────────────────
    person: Mapped["Person"] = relationship(
        "Person", back_populates="attendance_logs", lazy="selectin"
    )
    camera: Mapped["Camera"] = relationship(
        "Camera", back_populates="attendance_logs", lazy="selectin"
    )

    def __repr__(self) -> str:
        return f"<AttendanceLog(id={self.id}, person_id={self.person_id}, date={self.date}, status={self.status.value})>"
