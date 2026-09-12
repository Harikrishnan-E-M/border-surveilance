"""
Incident report management API endpoints.

Provides endpoints for generating, listing, downloading, and managing
automated incident reports and their templates.
"""

from __future__ import annotations

import math
import uuid
from datetime import date, datetime, timezone
from typing import Optional

import structlog
from fastapi import APIRouter, Depends, HTTPException, Query, status
from fastapi.responses import StreamingResponse
from pydantic import BaseModel, Field
from sqlalchemy import and_, func, select
from sqlalchemy.ext.asyncio import AsyncSession

from app.database import get_db_session
from app.exceptions import (
    AuthorizationError,
    NotFoundError,
    ValidationError,
)
from app.middleware.auth import JWTBearer, TokenPayload
from app.models.incident_report import (
    IncidentReport,
    ReportStatus,
    ReportTemplate,
    ReportType,
)
from app.models.user import User, UserRole
from app.schemas.common import ErrorResponse, SuccessResponse
from app.services.incident_report_service import IncidentReportService

logger = structlog.stdlib.get_logger(__name__)

router = APIRouter()


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


def _require_operator(user: User) -> None:
    """Raise 403 if the user is below operator role."""
    allowed = {UserRole.SUPER_ADMIN, UserRole.ORG_ADMIN, UserRole.MANAGER, UserRole.OPERATOR}
    if user.role not in allowed:
        raise AuthorizationError(message="Operator role or higher is required.")


def _require_manager(user: User) -> None:
    """Raise 403 if the user is below manager role."""
    allowed = {UserRole.SUPER_ADMIN, UserRole.ORG_ADMIN, UserRole.MANAGER}
    if user.role not in allowed:
        raise AuthorizationError(message="Manager role or higher is required.")


# ---------------------------------------------------------------------------
# POST /generate - Generate incident report from alert
# ---------------------------------------------------------------------------


class GenerateFromAlertRequest(BaseModel):
    alert_id: uuid.UUID = Field(..., description="Alert ID to generate report from")
    template_id: uuid.UUID | None = Field(default=None, description="Custom template ID")
    include_evidence: bool = Field(default=True, description="Include evidence in report")
    title: str | None = Field(default=None, max_length=512, description="Custom title")


@router.post(
    "/generate",
    response_model=SuccessResponse,
    summary="Generate incident report from an alert",
    status_code=status.HTTP_202_ACCEPTED,
)
async def generate_incident_report(
    body: GenerateFromAlertRequest,
    user: User = Depends(_get_current_user),
    db: AsyncSession = Depends(get_db_session),
) -> dict:
    """Generate a detailed incident report from a specific alert.

    The report is generated asynchronously. Returns the report record
    with status 'generating'. Poll the report detail endpoint to check
    when generation is complete.
    """
    _require_operator(user)

    try:
        # Dispatch to Celery for background generation
        from app.workers.incident_report_tasks import generate_incident_report_task

        report = IncidentReport(
            org_id=user.org_id,
            title=body.title or "Incident Report (generating...)",
            report_type=ReportType.INCIDENT,
            alert_id=body.alert_id,
            status=ReportStatus.GENERATING,
            generated_by=user.id,
            template_id=body.template_id,
        )
        db.add(report)
        await db.flush()

        try:
            generate_incident_report_task.delay(
                str(user.org_id),
                str(body.alert_id),
                str(report.id),
                str(user.id),
                str(body.template_id) if body.template_id else None,
                body.include_evidence,
            )
            logger.info(
                "Incident report generation queued",
                report_id=str(report.id),
                alert_id=str(body.alert_id),
            )
            return {
                "status": "success",
                "data": {
                    "id": str(report.id),
                    "title": report.title,
                    "report_type": report.report_type.value,
                    "status": report.status.value,
                    "alert_id": str(report.alert_id),
                },
                "message": "Incident report generation has been queued.",
            }
        except Exception as exc:
            logger.warning("Celery queue failed, completing report generation synchronously", error=str(exc))
            report = await IncidentReportService.generate_report(
                db=db,
                org_id=user.org_id,
                alert_id=body.alert_id,
                generated_by=user.id,
                template_id=body.template_id,
                include_evidence=body.include_evidence,
            )
            report_data = await IncidentReportService.get_report(db, user.org_id, report.id)
            return {
                "status": "success",
                "data": report_data,
                "message": "Incident report generated successfully.",
            }

    except Exception as exc:
        logger.warning("Generating incident report synchronously", error=str(exc))
        report = await IncidentReportService.generate_report(
            db=db,
            org_id=user.org_id,
            alert_id=body.alert_id,
            generated_by=user.id,
            template_id=body.template_id,
            include_evidence=body.include_evidence,
        )
        report_data = await IncidentReportService.get_report(db, user.org_id, report.id)
        return {
            "status": "success",
            "data": report_data,
            "message": "Incident report generated successfully.",
        }


# ---------------------------------------------------------------------------
# POST /daily-summary - Generate daily summary
# ---------------------------------------------------------------------------


class DailySummaryBody(BaseModel):
    report_date: date = Field(..., description="Date to summarize (YYYY-MM-DD)")
    template_id: uuid.UUID | None = Field(default=None)


@router.post(
    "/daily-summary",
    response_model=SuccessResponse,
    summary="Generate daily summary report",
    status_code=status.HTTP_202_ACCEPTED,
)
async def generate_daily_summary(
    body: DailySummaryBody,
    user: User = Depends(_get_current_user),
    db: AsyncSession = Depends(get_db_session),
) -> dict:
    """Generate a daily security summary report for the specified date."""
    _require_operator(user)

    try:
        report = await IncidentReportService.generate_daily_summary(
            db=db,
            org_id=user.org_id,
            report_date=body.report_date,
            generated_by=user.id,
            template_id=body.template_id,
        )
        report_data = await IncidentReportService.get_report(db, user.org_id, report.id)
        return {
            "status": "success",
            "data": report_data,
            "message": "Daily summary report generated successfully.",
        }
    except Exception as exc:
        logger.error("Daily summary generation error", error=str(exc))
        raise ValidationError(message=f"Daily summary report generation failed: {str(exc)}")


# ---------------------------------------------------------------------------
# POST /weekly - Generate weekly report
# ---------------------------------------------------------------------------


class WeeklyReportBody(BaseModel):
    week_start: date = Field(..., description="Monday of the week (YYYY-MM-DD)")
    template_id: uuid.UUID | None = Field(default=None)


@router.post(
    "/weekly",
    response_model=SuccessResponse,
    summary="Generate weekly security report",
    status_code=status.HTTP_202_ACCEPTED,
)
async def generate_weekly_report(
    body: WeeklyReportBody,
    user: User = Depends(_get_current_user),
    db: AsyncSession = Depends(get_db_session),
) -> dict:
    """Generate a weekly security report starting from the given Monday."""
    _require_operator(user)

    try:
        report = await IncidentReportService.generate_weekly_report(
            db=db,
            org_id=user.org_id,
            week_start=body.week_start,
            generated_by=user.id,
            template_id=body.template_id,
        )
        report_data = await IncidentReportService.get_report(db, user.org_id, report.id)
        return {
            "status": "success",
            "data": report_data,
            "message": "Weekly report generated successfully.",
        }
    except Exception as exc:
        logger.error("Weekly report generation error", error=str(exc))
        raise ValidationError(message=f"Weekly report generation failed: {str(exc)}")


# ---------------------------------------------------------------------------
# GET / - List reports with filters
# ---------------------------------------------------------------------------


@router.get(
    "/",
    response_model=SuccessResponse,
    summary="List incident reports (paginated, filterable)",
)
async def list_incident_reports(
    report_type: str | None = Query(None, description="Filter by report type"),
    report_status: str | None = Query(None, alias="status", description="Filter by status"),
    start_date: datetime | None = Query(None, description="Reports created after this date"),
    end_date: datetime | None = Query(None, description="Reports created before this date"),
    generated_by: uuid.UUID | None = Query(None, description="Filter by user who generated"),
    alert_id: uuid.UUID | None = Query(None, description="Filter by alert ID"),
    page: int = Query(1, ge=1),
    page_size: int = Query(20, ge=1, le=100),
    user: User = Depends(_get_current_user),
    db: AsyncSession = Depends(get_db_session),
) -> dict:
    """Return a paginated list of incident reports for the user's organization."""
    filters = {
        "report_type": report_type,
        "status": report_status,
        "start_date": start_date,
        "end_date": end_date,
        "generated_by": generated_by,
        "alert_id": alert_id,
    }
    # Remove None values
    filters = {k: v for k, v in filters.items() if v is not None}

    result = await IncidentReportService.get_reports(
        db=db,
        org_id=user.org_id,
        filters=filters if filters else None,
        page=page,
        page_size=page_size,
    )

    return {
        "status": "success",
        "data": result["items"],
        "meta": {
            "page": result["page"],
            "page_size": result["page_size"],
            "total": result["total"],
            "total_pages": result["total_pages"],
        },
    }


# ---------------------------------------------------------------------------
# GET /{report_id} - Get report details
# ---------------------------------------------------------------------------


@router.get(
    "/{report_id}",
    response_model=SuccessResponse,
    summary="Get incident report details",
    responses={404: {"model": ErrorResponse}},
)
async def get_incident_report(
    report_id: uuid.UUID,
    user: User = Depends(_get_current_user),
    db: AsyncSession = Depends(get_db_session),
) -> dict:
    """Retrieve a single incident report with download URL."""
    report_data = await IncidentReportService.get_report(
        db=db,
        org_id=user.org_id,
        report_id=report_id,
    )
    return {
        "status": "success",
        "data": report_data,
    }


# ---------------------------------------------------------------------------
# GET /{report_id}/download - Download PDF
# ---------------------------------------------------------------------------


@router.get(
    "/{report_id}/download",
    summary="Download incident report PDF",
    responses={
        200: {"content": {"application/pdf": {}}},
        404: {"model": ErrorResponse},
    },
)
async def download_incident_report(
    report_id: uuid.UUID,
    user: User = Depends(_get_current_user),
    db: AsyncSession = Depends(get_db_session),
):
    """Download the generated PDF file for an incident report."""
    result = await db.execute(
        select(IncidentReport).where(
            IncidentReport.id == report_id,
            IncidentReport.org_id == user.org_id,
        )
    )
    report = result.scalars().first()
    if not report:
        raise NotFoundError(resource="IncidentReport", identifier=str(report_id))

    if report.status != ReportStatus.COMPLETED:
        raise ValidationError(
            message=f"Report is not ready for download (status: {report.status.value})."
        )

    if not report.file_path:
        raise ValidationError(message="Report file path is missing.")

    # Try to stream from MinIO
    try:
        from app.utils.storage import get_storage_client
        storage = get_storage_client()
        pdf_bytes = storage.download_bytes(report.file_path)
        if pdf_bytes is None:
            raise ValidationError(message="Failed to retrieve report file from storage.")

        import io
        safe_title = report.title.replace("/", "_").replace("\\", "_")[:100]
        return StreamingResponse(
            io.BytesIO(pdf_bytes),
            media_type="application/pdf",
            headers={
                "Content-Disposition": f'attachment; filename="{safe_title}.pdf"',
                "Content-Length": str(len(pdf_bytes)),
            },
        )
    except ImportError:
        raise ValidationError(message="Storage client not available.")
    except Exception as exc:
        # Try local file fallback
        import os
        if os.path.exists(report.file_path):
            with open(report.file_path, "rb") as f:
                pdf_bytes = f.read()
            import io
            safe_title = report.title.replace("/", "_").replace("\\", "_")[:100]
            return StreamingResponse(
                io.BytesIO(pdf_bytes),
                media_type="application/pdf",
                headers={
                    "Content-Disposition": f'attachment; filename="{safe_title}.pdf"',
                    "Content-Length": str(len(pdf_bytes)),
                },
            )
        logger.error("Report download failed", report_id=str(report_id), error=str(exc))
        raise ValidationError(message="Failed to download report file.")


# ---------------------------------------------------------------------------
# GET /templates - List templates
# ---------------------------------------------------------------------------


@router.get(
    "/templates/list",
    response_model=SuccessResponse,
    summary="List report templates",
)
async def list_report_templates(
    user: User = Depends(_get_current_user),
    db: AsyncSession = Depends(get_db_session),
) -> dict:
    """Return all report templates for the user's organization."""
    result = await db.execute(
        select(ReportTemplate)
        .where(ReportTemplate.org_id == user.org_id)
        .order_by(ReportTemplate.is_default.desc(), ReportTemplate.name.asc())
    )
    templates = result.scalars().all()

    items = [
        {
            "id": str(t.id),
            "org_id": str(t.org_id),
            "name": t.name,
            "template_type": t.template_type.value if isinstance(t.template_type, ReportType) else str(t.template_type),
            "header_html": t.header_html,
            "footer_html": t.footer_html,
            "css_styles": t.css_styles,
            "logo_path": t.logo_path,
            "is_default": t.is_default,
            "created_at": t.created_at.isoformat() if t.created_at else None,
            "updated_at": t.updated_at.isoformat() if t.updated_at else None,
        }
        for t in templates
    ]

    return {
        "status": "success",
        "data": items,
    }


# ---------------------------------------------------------------------------
# POST /templates - Create template
# ---------------------------------------------------------------------------


class CreateTemplateBody(BaseModel):
    name: str = Field(..., min_length=1, max_length=255)
    template_type: str = Field(default="incident")
    header_html: str | None = Field(default=None, max_length=10000)
    footer_html: str | None = Field(default=None, max_length=10000)
    css_styles: str | None = Field(default=None, max_length=50000)
    logo_path: str | None = Field(default=None, max_length=1024)
    is_default: bool = Field(default=False)


@router.post(
    "/templates",
    response_model=SuccessResponse,
    summary="Create a report template",
    status_code=status.HTTP_201_CREATED,
)
async def create_report_template(
    body: CreateTemplateBody,
    user: User = Depends(_get_current_user),
    db: AsyncSession = Depends(get_db_session),
) -> dict:
    """Create a new report template for the organization."""
    _require_manager(user)

    try:
        rt = ReportType(body.template_type)
    except ValueError:
        raise ValidationError(message=f"Invalid template_type: {body.template_type}")

    # If setting as default, unset other defaults for this type
    if body.is_default:
        existing = await db.execute(
            select(ReportTemplate).where(
                ReportTemplate.org_id == user.org_id,
                ReportTemplate.template_type == rt,
                ReportTemplate.is_default.is_(True),
            )
        )
        for t in existing.scalars().all():
            t.is_default = False
            db.add(t)

    template = ReportTemplate(
        org_id=user.org_id,
        name=body.name,
        template_type=rt,
        header_html=body.header_html,
        footer_html=body.footer_html,
        css_styles=body.css_styles,
        logo_path=body.logo_path,
        is_default=body.is_default,
    )
    db.add(template)
    await db.flush()

    logger.info("Report template created", template_id=str(template.id), name=body.name)

    return {
        "status": "success",
        "data": {
            "id": str(template.id),
            "name": template.name,
            "template_type": template.template_type.value,
            "is_default": template.is_default,
            "created_at": template.created_at.isoformat() if template.created_at else None,
        },
        "message": "Report template created successfully.",
    }


# ---------------------------------------------------------------------------
# PUT /templates/{template_id} - Update template
# ---------------------------------------------------------------------------


class UpdateTemplateBody(BaseModel):
    name: str | None = Field(default=None, min_length=1, max_length=255)
    header_html: str | None = Field(default=None, max_length=10000)
    footer_html: str | None = Field(default=None, max_length=10000)
    css_styles: str | None = Field(default=None, max_length=50000)
    logo_path: str | None = Field(default=None, max_length=1024)
    is_default: bool | None = Field(default=None)


@router.put(
    "/templates/{template_id}",
    response_model=SuccessResponse,
    summary="Update a report template",
    responses={404: {"model": ErrorResponse}},
)
async def update_report_template(
    template_id: uuid.UUID,
    body: UpdateTemplateBody,
    user: User = Depends(_get_current_user),
    db: AsyncSession = Depends(get_db_session),
) -> dict:
    """Update an existing report template."""
    _require_manager(user)

    result = await db.execute(
        select(ReportTemplate).where(
            ReportTemplate.id == template_id,
            ReportTemplate.org_id == user.org_id,
        )
    )
    template = result.scalars().first()
    if not template:
        raise NotFoundError(resource="ReportTemplate", identifier=str(template_id))

    if body.name is not None:
        template.name = body.name
    if body.header_html is not None:
        template.header_html = body.header_html
    if body.footer_html is not None:
        template.footer_html = body.footer_html
    if body.css_styles is not None:
        template.css_styles = body.css_styles
    if body.logo_path is not None:
        template.logo_path = body.logo_path
    if body.is_default is not None:
        if body.is_default:
            # Unset other defaults
            existing = await db.execute(
                select(ReportTemplate).where(
                    ReportTemplate.org_id == user.org_id,
                    ReportTemplate.template_type == template.template_type,
                    ReportTemplate.is_default.is_(True),
                    ReportTemplate.id != template_id,
                )
            )
            for t in existing.scalars().all():
                t.is_default = False
                db.add(t)
        template.is_default = body.is_default

    db.add(template)
    await db.flush()

    logger.info("Report template updated", template_id=str(template_id))

    return {
        "status": "success",
        "data": {
            "id": str(template.id),
            "name": template.name,
            "template_type": template.template_type.value if isinstance(template.template_type, ReportType) else str(template.template_type),
            "header_html": template.header_html,
            "footer_html": template.footer_html,
            "css_styles": template.css_styles,
            "logo_path": template.logo_path,
            "is_default": template.is_default,
            "updated_at": template.updated_at.isoformat() if template.updated_at else None,
        },
        "message": "Report template updated successfully.",
    }
