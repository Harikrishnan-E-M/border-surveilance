"""Person model for enrolled individuals (employees, visitors, etc.)."""

import enum
import uuid
from datetime import datetime
from typing import TYPE_CHECKING, List, Optional

from sqlalchemy import Boolean, DateTime, ForeignKey, String, Text, func
from sqlalchemy import Enum as SAEnum
from sqlalchemy.dialects.postgresql import UUID
from sqlalchemy.orm import Mapped, mapped_column, relationship

from app.database import Base, TimestampMixin

if TYPE_CHECKING:
    from app.models.attendance import AttendanceLog
    from app.models.face import FaceEnrollment, FaceEvent
    from app.models.organization import Organization


class PersonType(str, enum.Enum):
    """Classification of a person's role or access level."""

    EMPLOYEE = "employee"
    VISITOR = "visitor"
    VIP = "vip"
    BLACKLISTED = "blacklisted"
    CONTRACTOR = "contractor"
    STUDENT = "student"


class Person(Base):
    """An individual enrolled in the VisionAI platform.

    Persons are identified by face recognition. They may have multiple
    face enrollments (different angles/conditions) linked to them.
    The person_type determines access rules and alert behaviour.
    """

    __tablename__ = "persons"

    org_id: Mapped[uuid.UUID] = mapped_column(
        UUID(as_uuid=True), ForeignKey("organizations.id", ondelete="CASCADE"), nullable=False, index=True
    )
    full_name: Mapped[str] = mapped_column(String(255), nullable=False)
    person_type: Mapped[PersonType] = mapped_column(
        String(32),
        nullable=False,
        default=PersonType.EMPLOYEE,
    )
    department: Mapped[Optional[str]] = mapped_column(String(255), nullable=True)
    employee_id: Mapped[Optional[str]] = mapped_column(String(100), nullable=True, index=True)
    phone: Mapped[Optional[str]] = mapped_column(String(20), nullable=True)
    email: Mapped[Optional[str]] = mapped_column(String(320), nullable=True)
    notes: Mapped[Optional[str]] = mapped_column(Text, nullable=True)
    is_active: Mapped[bool] = mapped_column(
        Boolean, nullable=False, default=True, server_default="true"
    )

    # ── Relationships ──────────────────────────────────────────────────
    organization: Mapped["Organization"] = relationship(
        "Organization", back_populates="persons", lazy="selectin"
    )
    face_enrollments: Mapped[List["FaceEnrollment"]] = relationship(
        "FaceEnrollment", back_populates="person", cascade="all, delete-orphan", lazy="selectin"
    )
    face_events: Mapped[List["FaceEvent"]] = relationship(
        "FaceEvent", back_populates="person", lazy="selectin"
    )
    attendance_logs: Mapped[List["AttendanceLog"]] = relationship(
        "AttendanceLog", back_populates="person", cascade="all, delete-orphan", lazy="selectin"
    )

    def __repr__(self) -> str:
        return f"<Person(id={self.id}, name='{self.full_name}', type={self.person_type.value})>"
