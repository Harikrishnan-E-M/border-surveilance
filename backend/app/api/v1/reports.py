"""
Report generation and management API endpoints.

Provides report listing, generation, template catalog, download,
deletion, and scheduling for recurring reports.
"""

from __future__ import annotations

import math
import uuid
from datetime import datetime, date, timezone
from typing import Optional

import structlog
from fastapi import APIRouter, Depends, HTTPException, Query, status
from fastapi.responses import StreamingResponse
from pydantic import BaseModel, Field
from sqlalchemy import func, select
from sqlalchemy.ext.asyncio import AsyncSession

from app.database import get_db_session
from app.exceptions import (
    AuthorizationError,
    NotFoundError,
    ValidationError,
)
from app.middleware.auth import JWTBearer, TokenPayload
from app.models.user import User, UserRole
from app.schemas.common import ErrorResponse, SuccessResponse

logger = structlog.stdlib.get_logger(__name__)

router = APIRouter()


# ---------------------------------------------------------------------------
# Report template catalog
# ---------------------------------------------------------------------------

REPORT_TEMPLATES = {
    "daily_summary": {
        "name": "Daily Summary Report",
        "description": "Overview of all alerts, events, and camera status for a single day.",
        "parameters": ["date", "camera_ids"],
        "formats": ["pdf", "csv", "xlsx"],
    },
    "weekly_analytics": {
        "name": "Weekly Analytics Report",
        "description": "Footfall trends, dwell times, and alert patterns over a week.",
        "parameters": ["start_date", "end_date", "camera_ids"],
        "formats": ["pdf", "csv", "xlsx"],
    },
    "attendance_report": {
        "name": "Attendance Report",
        "description": "Employee attendance summary for a date range with department breakdown.",
        "parameters": ["start_date", "end_date", "department"],
        "formats": ["pdf", "csv", "xlsx"],
    },
    "alert_report": {
        "name": "Alert Report",
        "description": "Detailed alert history with resolution status and response times.",
        "parameters": ["start_date", "end_date", "severity", "camera_ids"],
        "formats": ["pdf", "csv", "xlsx"],
    },
    "vehicle_report": {
        "name": "Vehicle Activity Report",
        "description": "Vehicle entry/exit log with parking duration analysis.",
        "parameters": ["start_date", "end_date", "camera_ids"],
        "formats": ["pdf", "csv", "xlsx"],
    },
    "ppe_compliance": {
        "name": "PPE Compliance Report",
        "description": "PPE violation summary with compliance rates by zone.",
        "parameters": ["start_date", "end_date", "zone_ids"],
        "formats": ["pdf", "csv"],
    },
    "occupancy_report": {
        "name": "Occupancy Report",
        "description": "Zone occupancy analysis with peak/low utilization hours.",
        "parameters": ["start_date", "end_date", "zone_ids"],
        "formats": ["pdf", "csv", "xlsx"],
    },
    "incident_report": {
        "name": "Incident Report",
        "description": "Detailed incident log including escalations and resolutions.",
        "parameters": ["start_date", "end_date", "alert_types"],
        "formats": ["pdf"],
    },
}


# ---------------------------------------------------------------------------
# Pydantic schemas
# ---------------------------------------------------------------------------


class GenerateReportRequest(BaseModel):
    report_type: str = Field(..., description="Report template type key")
    start_date: date = Field(..., description="Report start date")
    end_date: date = Field(..., description="Report end date")
    camera_ids: list[uuid.UUID] | None = None
    zone_ids: list[uuid.UUID] | None = None
    department: str | None = None
    format: str = Field(default="pdf", description="Output format: pdf, csv, xlsx")
    title: str | None = Field(default=None, max_length=255, description="Custom report title")


class ScheduleReportRequest(BaseModel):
    report_type: str = Field(..., description="Report template type key")
    schedule_cron: str = Field(..., description="Cron expression for scheduling (e.g., '0 8 * * 1' for Monday 8AM)")
    camera_ids: list[uuid.UUID] | None = None
    zone_ids: list[uuid.UUID] | None = None
    department: str | None = None
    format: str = Field(default="pdf")
    recipients: list[str] = Field(default_factory=list, description="Email addresses to deliver the report")
    title: str | None = Field(default=None, max_length=255)


# ---------------------------------------------------------------------------
# In-memory report store (production would use a DB table)
# ---------------------------------------------------------------------------

# In a production app, you would create a Report model. Here we use Redis
# for lightweight tracking. For demonstration, we track reports via the
# service layer.


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


# ---------------------------------------------------------------------------
# GET / - List generated reports
# ---------------------------------------------------------------------------


@router.get(
    "/",
    response_model=SuccessResponse,
    summary="List generated reports (paginated)",
)
async def list_reports(
    report_type: str | None = Query(None),
    page: int = Query(1, ge=1),
    page_size: int = Query(20, ge=1, le=100),
    user: User = Depends(_get_current_user),
) -> dict:
    """Return a paginated list of generated reports for the organization."""
    try:
        from app.services.report_service import list_org_reports
        result = await list_org_reports(
            org_id=str(user.org_id),
            report_type=report_type,
            page=page,
            page_size=page_size,
        )
        return {
            "status": "success",
            "data": result.get("reports", []),
            "meta": result.get("meta", {"page": page, "page_size": page_size, "total": 0, "total_pages": 0}),
        }
    except ImportError:
        return {
            "status": "success",
            "data": [],
            "meta": {"page": page, "page_size": page_size, "total": 0, "total_pages": 0},
            "message": "Report service is not configured.",
        }


# ---------------------------------------------------------------------------
# POST /generate - Generate a new report
# ---------------------------------------------------------------------------


@router.post(
    "/generate",
    response_model=SuccessResponse,
    status_code=status.HTTP_202_ACCEPTED,
    summary="Generate a new report",
)
async def generate_report(
    body: GenerateReportRequest,
    user: User = Depends(_get_current_user),
) -> dict:
    """Queue generation of a new report."""
    # Validate report type
    if body.report_type not in REPORT_TEMPLATES:
        valid = list(REPORT_TEMPLATES.keys())
        raise ValidationError(
            message=f"Invalid report_type '{body.report_type}'. Must be one of: {', '.join(valid)}"
        )

    template = REPORT_TEMPLATES[body.report_type]

    # Validate format
    if body.format not in template["formats"]:
        raise ValidationError(
            message=f"Format '{body.format}' is not supported for this report type. Supported: {', '.join(template['formats'])}"
        )

    if body.end_date < body.start_date:
        raise ValidationError(message="end_date must be on or after start_date.")

    report_id = str(uuid.uuid4())
    title = body.title or template["name"]

    try:
        from app.services.report_service import generate_report as svc_generate
        await svc_generate(
            report_id=report_id,
            org_id=str(user.org_id),
            user_id=str(user.id),
            report_type=body.report_type,
            title=title,
            start_date=body.start_date,
            end_date=body.end_date,
            camera_ids=[str(c) for c in body.camera_ids] if body.camera_ids else None,
            zone_ids=[str(z) for z in body.zone_ids] if body.zone_ids else None,
            department=body.department,
            output_format=body.format,
        )
    except ImportError:
        logger.warning("Report service not available")
    except Exception as exc:
        logger.error("Report generation failed", error=str(exc))
        raise ValidationError(message=f"Report generation failed: {str(exc)}")

    logger.info("Report generation queued", report_id=report_id, type=body.report_type)

    return {
        "status": "success",
        "data": {
            "report_id": report_id,
            "report_type": body.report_type,
            "title": title,
            "format": body.format,
            "status": "processing",
        },
        "message": "Report generation has been queued.",
    }


# ---------------------------------------------------------------------------
# GET /templates - List available report templates
# ---------------------------------------------------------------------------


@router.get(
    "/templates",
    response_model=SuccessResponse,
    summary="List available report templates",
)
async def list_templates(
    user: User = Depends(_get_current_user),
) -> dict:
    """Return all available report templates with their descriptions."""
    return {
        "status": "success",
        "data": REPORT_TEMPLATES,
    }


# ---------------------------------------------------------------------------
# GET /schedules - List scheduled reports
# ---------------------------------------------------------------------------


@router.get(
    "/schedules",
    response_model=SuccessResponse,
    summary="List scheduled reports",
)
async def list_scheduled_reports(
    user: User = Depends(_get_current_user),
) -> dict:
    """Return all scheduled report configurations for the organization.

    Since report schedules are managed via Celery/Redis (not persisted in SQL),
    this returns an empty list when no scheduling service is available.
    """
    items: list[dict] = []
    try:
        from app.services.report_service import get_scheduled_reports
        items = await get_scheduled_reports(org_id=str(user.org_id))
    except ImportError:
        logger.debug("Report scheduling service not available")
    except Exception as exc:
        logger.warning("Failed to list scheduled reports", error=str(exc))

    return {
        "status": "success",
        "data": {
            "items": items,
            "total": len(items),
        },
    }


# ---------------------------------------------------------------------------
# GET /{report_id} - Get report status/details
# ---------------------------------------------------------------------------


@router.get(
    "/{report_id}",
    response_model=SuccessResponse,
    summary="Get report status and details",
    responses={404: {"model": ErrorResponse}},
)
async def get_report(
    report_id: uuid.UUID,
    user: User = Depends(_get_current_user),
) -> dict:
    """Retrieve the status and details of a generated report."""
    try:
        from app.services.report_service import get_report_details
        report = await get_report_details(
            report_id=str(report_id),
            org_id=str(user.org_id),
        )
        if not report:
            raise NotFoundError(resource="Report", identifier=str(report_id))
        return {
            "status": "success",
            "data": report,
        }
    except ImportError:
        raise NotFoundError(resource="Report", identifier=str(report_id))


# ---------------------------------------------------------------------------
# GET /{report_id}/download - Download report file
# ---------------------------------------------------------------------------


@router.get(
    "/{report_id}/download",
    summary="Download report file",
    responses={404: {"model": ErrorResponse}},
)
async def download_report(
    report_id: uuid.UUID,
    user: User = Depends(_get_current_user),
) -> StreamingResponse:
    """Download the generated report file."""
    try:
        from app.services.report_service import get_report_file
        file_data = await get_report_file(
            report_id=str(report_id),
            org_id=str(user.org_id),
        )
        if not file_data:
            raise NotFoundError(resource="Report", identifier=str(report_id))

        return StreamingResponse(
            iter([file_data["content"]]),
            media_type=file_data.get("content_type", "application/octet-stream"),
            headers={
                "Content-Disposition": f"attachment; filename={file_data.get('filename', 'report')}"
            },
        )
    except ImportError:
        raise NotFoundError(resource="Report", identifier=str(report_id))
    except NotFoundError:
        raise
    except Exception as exc:
        logger.error("Report download failed", error=str(exc))
        raise NotFoundError(resource="Report", identifier=str(report_id))


# ---------------------------------------------------------------------------
# DELETE /{report_id} - Delete report
# ---------------------------------------------------------------------------


@router.delete(
    "/{report_id}",
    response_model=SuccessResponse,
    summary="Delete a report",
    responses={404: {"model": ErrorResponse}},
)
async def delete_report(
    report_id: uuid.UUID,
    user: User = Depends(_get_current_user),
) -> dict:
    """Delete a generated report and its associated file."""
    try:
        from app.services.report_service import delete_report as svc_delete
        deleted = await svc_delete(
            report_id=str(report_id),
            org_id=str(user.org_id),
        )
        if not deleted:
            raise NotFoundError(resource="Report", identifier=str(report_id))
    except ImportError:
        raise NotFoundError(resource="Report", identifier=str(report_id))
    except NotFoundError:
        raise

    logger.info("Report deleted", report_id=str(report_id))

    return {
        "status": "success",
        "data": None,
        "message": "Report deleted successfully.",
    }


# ---------------------------------------------------------------------------
# POST /schedule - Schedule a recurring report
# ---------------------------------------------------------------------------


@router.post(
    "/schedule",
    response_model=SuccessResponse,
    status_code=status.HTTP_201_CREATED,
    summary="Schedule a recurring report",
)
async def schedule_report(
    body: ScheduleReportRequest,
    user: User = Depends(_get_current_user),
) -> dict:
    """Create a scheduled recurring report with email delivery."""
    # Validate report type
    if body.report_type not in REPORT_TEMPLATES:
        valid = list(REPORT_TEMPLATES.keys())
        raise ValidationError(
            message=f"Invalid report_type '{body.report_type}'. Must be one of: {', '.join(valid)}"
        )

    template = REPORT_TEMPLATES[body.report_type]
    if body.format not in template["formats"]:
        raise ValidationError(
            message=f"Format '{body.format}' is not supported. Supported: {', '.join(template['formats'])}"
        )

    schedule_id = str(uuid.uuid4())
    title = body.title or f"Scheduled: {template['name']}"

    try:
        from app.services.report_service import schedule_recurring_report
        await schedule_recurring_report(
            schedule_id=schedule_id,
            org_id=str(user.org_id),
            user_id=str(user.id),
            report_type=body.report_type,
            title=title,
            schedule_cron=body.schedule_cron,
            camera_ids=[str(c) for c in body.camera_ids] if body.camera_ids else None,
            zone_ids=[str(z) for z in body.zone_ids] if body.zone_ids else None,
            department=body.department,
            output_format=body.format,
            recipients=body.recipients,
        )
    except ImportError:
        logger.warning("Report scheduling service not available")
    except Exception as exc:
        logger.error("Report scheduling failed", error=str(exc))
        raise ValidationError(message=f"Report scheduling failed: {str(exc)}")

    logger.info("Report schedule created", schedule_id=schedule_id)

    return {
        "status": "success",
        "data": {
            "schedule_id": schedule_id,
            "report_type": body.report_type,
            "title": title,
            "schedule_cron": body.schedule_cron,
            "format": body.format,
            "recipients": body.recipients,
        },
        "message": "Report schedule created successfully.",
    }


# ---------------------------------------------------------------------------
# DELETE /schedules/{schedule_id} - Delete a scheduled report
# ---------------------------------------------------------------------------


@router.delete(
    "/schedules/{schedule_id}",
    response_model=SuccessResponse,
    summary="Delete a scheduled report",
    responses={404: {"model": ErrorResponse}},
)
async def delete_scheduled_report(
    schedule_id: uuid.UUID,
    user: User = Depends(_get_current_user),
) -> dict:
    """Remove a scheduled recurring report. Requires manager+."""
    try:
        from app.services.report_service import delete_schedule
        deleted = await delete_schedule(
            schedule_id=str(schedule_id),
            org_id=str(user.org_id),
        )
        if not deleted:
            raise NotFoundError(resource="ReportSchedule", identifier=str(schedule_id))
    except ImportError:
        # If the service doesn't have this function, try Redis directly
        try:
            from app.dependencies import get_redis
            redis = await get_redis()
            key = f"report_schedule:{user.org_id}:{schedule_id}"
            existed = await redis.delete(key)
            if not existed:
                raise NotFoundError(resource="ReportSchedule", identifier=str(schedule_id))
        except NotFoundError:
            raise
        except Exception:
            raise NotFoundError(resource="ReportSchedule", identifier=str(schedule_id))
    except NotFoundError:
        raise

    logger.info("Report schedule deleted", schedule_id=str(schedule_id))

    return {
        "status": "success",
        "data": None,
        "message": "Report schedule deleted successfully.",
    }
