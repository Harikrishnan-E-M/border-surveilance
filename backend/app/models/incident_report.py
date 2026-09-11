"""Incident report and report template models.

Stores metadata for auto-generated incident reports (PDF files stored in
MinIO) as well as customisable HTML/CSS templates that control the visual
appearance of those reports.
"""

import enum
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
from sqlalchemy import Enum as SAEnum
from sqlalchemy.dialects.postgresql import JSON, UUID
from sqlalchemy.orm import Mapped, mapped_column, relationship

from app.database import Base, TimestampMixin

if TYPE_CHECKING:
    from app.models.alert import Alert
    from app.models.organization import Organization
    from app.models.user import User


# ── Enums ──────────────────────────────────────────────────────────────────


class ReportType(str, enum.Enum):
    """Category of generated report."""

    INCIDENT = "incident"
    DAILY_SUMMARY = "daily_summary"
    WEEKLY_REPORT = "weekly_report"
    MONTHLY_REPORT = "monthly_report"
    CUSTOM = "custom"


class ReportStatus(str, enum.Enum):
    """Lifecycle status of a report generation job."""

    GENERATING = "generating"
    COMPLETED = "completed"
    FAILED = "failed"


# ── IncidentReport Model ──────────────────────────────────────────────────


class IncidentReport(Base):
    """A generated incident / summary report.

    Each record represents a PDF (or other format) that has been produced
    either on-demand from a specific alert or as a scheduled summary.  The
    actual file is stored in MinIO; ``file_path`` holds the object key.
    """

    __tablename__ = "incident_reports"

    org_id: Mapped[uuid.UUID] = mapped_column(
        UUID(as_uuid=True),
        ForeignKey("organizations.id", ondelete="CASCADE"),
        nullable=False,
        index=True,
    )
    title: Mapped[str] = mapped_column(String(512), nullable=False)
    report_type: Mapped[ReportType] = mapped_column(
        String(32),
        nullable=False,
        index=True,
    )
    alert_id: Mapped[Optional[uuid.UUID]] = mapped_column(
        UUID(as_uuid=True),
        ForeignKey("alerts.id", ondelete="SET NULL"),
        nullable=True,
        index=True,
    )
    status: Mapped[ReportStatus] = mapped_column(
        String(32),
        nullable=False,
        default=ReportStatus.GENERATING,
        server_default=ReportStatus.GENERATING.value,
    )
    file_path: Mapped[Optional[str]] = mapped_column(
        String(1024), nullable=True, doc="MinIO object key for the generated PDF"
    )
    file_size: Mapped[Optional[int]] = mapped_column(
        Integer, nullable=True, doc="File size in bytes"
    )
    page_count: Mapped[Optional[int]] = mapped_column(
        Integer, nullable=True, doc="Number of pages in the report"
    )
    generated_by: Mapped[Optional[uuid.UUID]] = mapped_column(
        UUID(as_uuid=True),
        ForeignKey("users.id", ondelete="SET NULL"),
        nullable=True,
        index=True,
    )
    summary_text: Mapped[Optional[str]] = mapped_column(
        Text, nullable=True, doc="Executive summary / abstract"
    )
    metadata_json: Mapped[Optional[dict]] = mapped_column(
        JSON,
        nullable=True,
        doc="Structured metadata: cameras, zones, date_range, alert_count, etc.",
    )
    error_message: Mapped[Optional[str]] = mapped_column(
        Text, nullable=True, doc="Error details if generation failed"
    )
    download_url: Mapped[Optional[str]] = mapped_column(
        String(2048),
        nullable=True,
        doc="Pre-signed download URL (refreshed on access)",
    )
    template_id: Mapped[Optional[uuid.UUID]] = mapped_column(
        UUID(as_uuid=True),
        ForeignKey("report_templates.id", ondelete="SET NULL"),
        nullable=True,
    )

    # ── Relationships ──────────────────────────────────────────────────
    organization: Mapped["Organization"] = relationship(
        "Organization", lazy="selectin"
    )
    alert: Mapped[Optional["Alert"]] = relationship(
        "Alert", lazy="selectin"
    )
    generated_by_user: Mapped[Optional["User"]] = relationship(
        "User", lazy="selectin", foreign_keys=[generated_by]
    )
    template: Mapped[Optional["ReportTemplate"]] = relationship(
        "ReportTemplate", lazy="selectin", foreign_keys=[template_id]
    )

    def __repr__(self) -> str:
        return (
            f"<IncidentReport(id={self.id}, type={self.report_type.value}, "
            f"status={self.status.value})>"
        )


# ── ReportTemplate Model ──────────────────────────────────────────────────


class ReportTemplate(Base):
    """A reusable HTML template for generating PDF reports.

    Organisations can create custom templates with their own branding,
    header/footer, and CSS.  One template per organisation may be marked
    as the default.
    """

    __tablename__ = "report_templates"

    org_id: Mapped[uuid.UUID] = mapped_column(
        UUID(as_uuid=True),
        ForeignKey("organizations.id", ondelete="CASCADE"),
        nullable=False,
        index=True,
    )
    name: Mapped[str] = mapped_column(String(255), nullable=False)
    template_type: Mapped[ReportType] = mapped_column(
        String(32),
        nullable=False,
    )
    header_html: Mapped[Optional[str]] = mapped_column(
        Text, nullable=True, doc="HTML snippet for the page header"
    )
    footer_html: Mapped[Optional[str]] = mapped_column(
        Text, nullable=True, doc="HTML snippet for the page footer"
    )
    css_styles: Mapped[Optional[str]] = mapped_column(
        Text, nullable=True, doc="Additional CSS to inject into the report"
    )
    logo_path: Mapped[Optional[str]] = mapped_column(
        String(1024), nullable=True, doc="MinIO object key for the logo image"
    )
    is_default: Mapped[bool] = mapped_column(
        Boolean,
        nullable=False,
        default=False,
        server_default="false",
    )

    # ── Relationships ──────────────────────────────────────────────────
    organization: Mapped["Organization"] = relationship(
        "Organization", lazy="selectin"
    )

    def __repr__(self) -> str:
        return f"<ReportTemplate(id={self.id}, name='{self.name}', default={self.is_default})>"
