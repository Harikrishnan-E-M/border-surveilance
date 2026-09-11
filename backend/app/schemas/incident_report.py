"""Pydantic schemas for the incident report subsystem.

Covers request validation, response serialization, filtering, and
template management for automated incident report generation.
"""

from __future__ import annotations


from datetime import date, datetime
from typing import Any, Optional
from uuid import UUID

from pydantic import BaseModel, ConfigDict, Field

from app.schemas.common import PaginatedResponse


# ── Enums (mirrored from models for client documentation) ─────────────────


REPORT_TYPES = ("incident", "daily_summary", "weekly_report", "monthly_report", "custom")
REPORT_STATUSES = ("generating", "completed", "failed")


# ── Report Response ───────────────────────────────────────────────────────


class IncidentReportResponse(BaseModel):
    """Full incident report representation returned by read endpoints."""

    model_config = ConfigDict(from_attributes=True)

    id: UUID = Field(description="Report unique identifier.")
    org_id: UUID = Field(description="Owning organization ID.")
    title: str = Field(description="Report title.")
    report_type: str = Field(
        description="Report type: incident, daily_summary, weekly_report, monthly_report, custom.",
        examples=["incident"],
    )
    alert_id: UUID | None = Field(
        default=None,
        description="Associated alert ID (for single-incident reports).",
    )
    status: str = Field(
        description="Generation status: generating, completed, failed.",
        examples=["completed"],
    )
    file_path: str | None = Field(
        default=None,
        description="MinIO object path for the generated file.",
    )
    file_size: int | None = Field(
        default=None,
        description="File size in bytes.",
    )
    page_count: int | None = Field(
        default=None,
        description="Number of pages in the PDF.",
    )
    generated_by: UUID | None = Field(
        default=None,
        description="User who triggered the report.",
    )
    generated_by_name: str | None = Field(
        default=None,
        description="Full name of the user who triggered the report.",
    )
    summary_text: str | None = Field(
        default=None,
        description="Executive summary text.",
    )
    metadata_json: dict[str, Any] | None = Field(
        default=None,
        description="Structured metadata (cameras, zones, date_range, alert_count).",
    )
    error_message: str | None = Field(
        default=None,
        description="Error details if generation failed.",
    )
    download_url: str | None = Field(
        default=None,
        description="Pre-signed download URL.",
    )
    template_id: UUID | None = Field(
        default=None,
        description="Template used for generation.",
    )
    created_at: datetime = Field(description="Report creation timestamp.")
    updated_at: datetime = Field(description="Last update timestamp.")


class IncidentReportList(BaseModel):
    """Paginated list of incident reports."""

    status: str = Field(default="success")
    data: list[IncidentReportResponse] = Field(description="Reports for the current page.")
    meta: dict[str, Any] = Field(
        description="Pagination metadata (page, page_size, total, total_pages)."
    )


# ── Generation Requests ───────────────────────────────────────────────────


class GenerateReportRequest(BaseModel):
    """Request to generate a new incident report.

    Supply either ``alert_id`` for a single-incident report or custom
    parameters (``date_range``, ``cameras``, ``report_type``) for a
    summary / custom report.
    """

    alert_id: UUID | None = Field(
        default=None,
        description="Generate report from a specific alert.",
    )
    report_type: str = Field(
        default="incident",
        description="Report type: incident, daily_summary, weekly_report, monthly_report, custom.",
        examples=["incident"],
    )
    start_date: datetime | None = Field(
        default=None,
        description="Custom report start date (ISO-8601).",
    )
    end_date: datetime | None = Field(
        default=None,
        description="Custom report end date (ISO-8601).",
    )
    camera_ids: list[UUID] | None = Field(
        default=None,
        description="Filter by specific camera IDs.",
    )
    zone_ids: list[UUID] | None = Field(
        default=None,
        description="Filter by specific zone IDs.",
    )
    template_id: UUID | None = Field(
        default=None,
        description="Use a custom template for generation.",
    )
    title: str | None = Field(
        default=None,
        max_length=512,
        description="Custom report title (auto-generated if omitted).",
    )
    include_evidence: bool = Field(
        default=True,
        description="Include snapshot thumbnails and video clip references.",
    )


class DailySummaryRequest(BaseModel):
    """Request to generate a daily summary report."""

    report_date: date = Field(
        description="The date to summarize (YYYY-MM-DD).",
    )
    template_id: UUID | None = Field(
        default=None,
        description="Use a custom template.",
    )


class WeeklySummaryRequest(BaseModel):
    """Request to generate a weekly summary report."""

    week_start: date = Field(
        description="Monday of the week to summarize (YYYY-MM-DD).",
    )
    template_id: UUID | None = Field(
        default=None,
        description="Use a custom template.",
    )


# ── Filters ───────────────────────────────────────────────────────────────


class ReportFilters(BaseModel):
    """Query parameters for filtering the report list."""

    report_type: str | None = Field(
        default=None,
        description="Filter by report type.",
    )
    status: str | None = Field(
        default=None,
        description="Filter by generation status.",
    )
    start_date: datetime | None = Field(
        default=None,
        description="Reports created on or after this date.",
    )
    end_date: datetime | None = Field(
        default=None,
        description="Reports created on or before this date.",
    )
    generated_by: UUID | None = Field(
        default=None,
        description="Filter by generating user ID.",
    )
    alert_id: UUID | None = Field(
        default=None,
        description="Filter by associated alert ID.",
    )


# ── Template Schemas ──────────────────────────────────────────────────────


class ReportTemplateResponse(BaseModel):
    """Report template representation."""

    model_config = ConfigDict(from_attributes=True)

    id: UUID = Field(description="Template unique identifier.")
    org_id: UUID = Field(description="Owning organization ID.")
    name: str = Field(description="Template name.")
    template_type: str = Field(description="Template type.")
    header_html: str | None = Field(default=None, description="Header HTML snippet.")
    footer_html: str | None = Field(default=None, description="Footer HTML snippet.")
    css_styles: str | None = Field(default=None, description="Custom CSS styles.")
    logo_path: str | None = Field(default=None, description="Logo file path in MinIO.")
    is_default: bool = Field(description="Whether this is the default template.")
    created_at: datetime = Field(description="Template creation timestamp.")
    updated_at: datetime = Field(description="Last update timestamp.")


class ReportTemplateCreate(BaseModel):
    """Request body to create a new report template."""

    name: str = Field(
        min_length=1,
        max_length=255,
        description="Template display name.",
        examples=["Corporate Security Report"],
    )
    template_type: str = Field(
        default="incident",
        description="Template type.",
    )
    header_html: str | None = Field(
        default=None,
        max_length=10000,
        description="HTML for the page header.",
    )
    footer_html: str | None = Field(
        default=None,
        max_length=10000,
        description="HTML for the page footer.",
    )
    css_styles: str | None = Field(
        default=None,
        max_length=50000,
        description="CSS stylesheet for the report.",
    )
    logo_path: str | None = Field(
        default=None,
        max_length=1024,
        description="MinIO object key for the logo.",
    )
    is_default: bool = Field(
        default=False,
        description="Mark as the default template for this type.",
    )


class ReportTemplateUpdate(BaseModel):
    """Request body to update an existing report template."""

    name: str | None = Field(
        default=None,
        min_length=1,
        max_length=255,
        description="Updated template name.",
    )
    header_html: str | None = Field(
        default=None,
        max_length=10000,
        description="Updated header HTML.",
    )
    footer_html: str | None = Field(
        default=None,
        max_length=10000,
        description="Updated footer HTML.",
    )
    css_styles: str | None = Field(
        default=None,
        max_length=50000,
        description="Updated CSS.",
    )
    logo_path: str | None = Field(
        default=None,
        max_length=1024,
        description="Updated logo path.",
    )
    is_default: bool | None = Field(
        default=None,
        description="Update default status.",
    )
