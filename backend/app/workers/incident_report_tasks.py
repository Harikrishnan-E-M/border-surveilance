"""Celery tasks for automated incident report generation.

Handles background report generation triggered by API endpoints and
scheduled periodic reports (daily summaries, weekly reports).  Also
includes a cleanup task for old report files.
"""

from __future__ import annotations

import asyncio
import os
import uuid
from datetime import date, datetime, timedelta, timezone
from typing import Any, Optional

import structlog

from app.workers.celery_app import celery_app

logger = structlog.get_logger(__name__)


def _run_async(coro):
    """Run an async coroutine in a synchronous Celery task context."""
    try:
        loop = asyncio.get_event_loop()
        if loop.is_running():
            # If already in an event loop, create a new one
            import concurrent.futures
            with concurrent.futures.ThreadPoolExecutor() as pool:
                future = pool.submit(asyncio.run, coro)
                return future.result()
        else:
            return loop.run_until_complete(coro)
    except RuntimeError:
        return asyncio.run(coro)


# ---------------------------------------------------------------------------
# Task: Generate incident report from alert
# ---------------------------------------------------------------------------


@celery_app.task(
    name="incident_reports.generate_incident_report",
    bind=True,
    max_retries=2,
    default_retry_delay=30,
    queue="reports",
)
def generate_incident_report_task(
    self,
    org_id: str,
    alert_id: str,
    report_id: str,
    user_id: str | None = None,
    template_id: str | None = None,
    include_evidence: bool = True,
) -> dict:
    """Background task to generate an incident report from an alert.

    Args:
        org_id: Organization UUID string.
        alert_id: Alert UUID string.
        report_id: Pre-created IncidentReport UUID string.
        user_id: User who triggered the generation.
        template_id: Optional template UUID string.
        include_evidence: Whether to include evidence.

    Returns:
        Dict with status, report_id, file_path, and download_url.
    """
    log = logger.bind(
        task="generate_incident_report",
        report_id=report_id,
        alert_id=alert_id,
    )
    log.info("Starting incident report generation")

    async def _generate():
        from app.database import get_db_context
        from app.models.incident_report import IncidentReport, ReportStatus
        from app.services.incident_report_service import IncidentReportService
        from sqlalchemy import select

        async with get_db_context() as db:
            # Fetch the pre-created report record
            result = await db.execute(
                select(IncidentReport).where(
                    IncidentReport.id == uuid.UUID(report_id)
                )
            )
            existing_report = result.scalars().first()

            try:
                report = await IncidentReportService.generate_report(
                    db=db,
                    org_id=uuid.UUID(org_id),
                    alert_id=uuid.UUID(alert_id),
                    generated_by=uuid.UUID(user_id) if user_id else None,
                    template_id=uuid.UUID(template_id) if template_id else None,
                    include_evidence=include_evidence,
                )

                # If we pre-created a report record in the API, update it
                if existing_report and existing_report.id != report.id:
                    existing_report.status = ReportStatus.COMPLETED
                    existing_report.file_path = report.file_path
                    existing_report.file_size = report.file_size
                    existing_report.page_count = report.page_count
                    existing_report.summary_text = report.summary_text
                    existing_report.download_url = report.download_url
                    existing_report.metadata_json = report.metadata_json
                    existing_report.title = report.title
                    db.add(existing_report)
                    await db.flush()

                return {
                    "status": "completed",
                    "report_id": str(report.id),
                    "file_path": report.file_path,
                    "file_size": report.file_size,
                    "download_url": report.download_url,
                }

            except Exception as exc:
                if existing_report:
                    existing_report.status = ReportStatus.FAILED
                    existing_report.error_message = str(exc)[:2000]
                    db.add(existing_report)
                    await db.flush()
                raise

    try:
        result = _run_async(_generate())
        log.info("Incident report generated successfully", **result)
        return result

    except Exception as exc:
        log.error("Incident report generation failed", error=str(exc))
        raise self.retry(exc=exc)


# ---------------------------------------------------------------------------
# Task: Generate daily summary
# ---------------------------------------------------------------------------


@celery_app.task(
    name="incident_reports.generate_daily_summary",
    bind=True,
    max_retries=2,
    default_retry_delay=60,
    queue="reports",
)
def generate_daily_summary_task(
    self,
    org_id: str,
    report_date_str: str | None = None,
    report_id: str | None = None,
    user_id: str | None = None,
    template_id: str | None = None,
) -> dict:
    """Generate a daily summary report.

    Runs as a scheduled task at 6 AM daily or on-demand via API.

    Args:
        org_id: Organization UUID string.
        report_date_str: Date string (YYYY-MM-DD). Defaults to yesterday.
        report_id: Pre-created report record ID (optional).
        user_id: User who triggered the report.
        template_id: Optional template ID.

    Returns:
        Dict with status and report metadata.
    """
    log = logger.bind(task="generate_daily_summary", org_id=org_id)
    log.info("Starting daily summary generation")

    if report_date_str:
        report_date = date.fromisoformat(report_date_str)
    else:
        report_date = (datetime.now(timezone.utc) - timedelta(days=1)).date()

    async def _generate():
        from app.database import get_db_context
        from app.models.incident_report import IncidentReport, ReportStatus
        from app.services.incident_report_service import IncidentReportService
        from sqlalchemy import select

        async with get_db_context() as db:
            # Update pre-created record if it exists
            existing_report = None
            if report_id:
                result = await db.execute(
                    select(IncidentReport).where(
                        IncidentReport.id == uuid.UUID(report_id)
                    )
                )
                existing_report = result.scalars().first()

            try:
                report = await IncidentReportService.generate_daily_summary(
                    db=db,
                    org_id=uuid.UUID(org_id),
                    report_date=report_date,
                    generated_by=uuid.UUID(user_id) if user_id else None,
                    template_id=uuid.UUID(template_id) if template_id else None,
                )

                if existing_report and existing_report.id != report.id:
                    existing_report.status = ReportStatus.COMPLETED
                    existing_report.file_path = report.file_path
                    existing_report.file_size = report.file_size
                    existing_report.page_count = report.page_count
                    existing_report.summary_text = report.summary_text
                    existing_report.download_url = report.download_url
                    existing_report.metadata_json = report.metadata_json
                    existing_report.title = report.title
                    db.add(existing_report)
                    await db.flush()

                return {
                    "status": "completed",
                    "report_id": str(report.id),
                    "file_path": report.file_path,
                    "file_size": report.file_size,
                    "date": report_date.isoformat(),
                }

            except Exception as exc:
                if existing_report:
                    existing_report.status = ReportStatus.FAILED
                    existing_report.error_message = str(exc)[:2000]
                    db.add(existing_report)
                    await db.flush()
                raise

    try:
        result = _run_async(_generate())
        log.info("Daily summary generated successfully", **result)
        return result

    except Exception as exc:
        log.error("Daily summary generation failed", error=str(exc))
        raise self.retry(exc=exc)


# ---------------------------------------------------------------------------
# Task: Generate weekly report
# ---------------------------------------------------------------------------


@celery_app.task(
    name="incident_reports.generate_weekly_report",
    bind=True,
    max_retries=2,
    default_retry_delay=60,
    queue="reports",
)
def generate_weekly_report_task(
    self,
    org_id: str,
    week_start_str: str | None = None,
    report_id: str | None = None,
    user_id: str | None = None,
    template_id: str | None = None,
) -> dict:
    """Generate a weekly security report.

    Runs as a scheduled task on Monday at 7 AM or on-demand via API.

    Args:
        org_id: Organization UUID string.
        week_start_str: Monday date string (YYYY-MM-DD). Defaults to last Monday.
        report_id: Pre-created report record ID (optional).
        user_id: User who triggered the report.
        template_id: Optional template ID.

    Returns:
        Dict with status and report metadata.
    """
    log = logger.bind(task="generate_weekly_report", org_id=org_id)
    log.info("Starting weekly report generation")

    if week_start_str:
        week_start = date.fromisoformat(week_start_str)
    else:
        today = datetime.now(timezone.utc).date()
        week_start = today - timedelta(days=today.weekday() + 7)  # Last Monday

    async def _generate():
        from app.database import get_db_context
        from app.models.incident_report import IncidentReport, ReportStatus
        from app.services.incident_report_service import IncidentReportService
        from sqlalchemy import select

        async with get_db_context() as db:
            existing_report = None
            if report_id:
                result = await db.execute(
                    select(IncidentReport).where(
                        IncidentReport.id == uuid.UUID(report_id)
                    )
                )
                existing_report = result.scalars().first()

            try:
                report = await IncidentReportService.generate_weekly_report(
                    db=db,
                    org_id=uuid.UUID(org_id),
                    week_start=week_start,
                    generated_by=uuid.UUID(user_id) if user_id else None,
                    template_id=uuid.UUID(template_id) if template_id else None,
                )

                if existing_report and existing_report.id != report.id:
                    existing_report.status = ReportStatus.COMPLETED
                    existing_report.file_path = report.file_path
                    existing_report.file_size = report.file_size
                    existing_report.page_count = report.page_count
                    existing_report.summary_text = report.summary_text
                    existing_report.download_url = report.download_url
                    existing_report.metadata_json = report.metadata_json
                    existing_report.title = report.title
                    db.add(existing_report)
                    await db.flush()

                return {
                    "status": "completed",
                    "report_id": str(report.id),
                    "file_path": report.file_path,
                    "file_size": report.file_size,
                    "week_start": week_start.isoformat(),
                }

            except Exception as exc:
                if existing_report:
                    existing_report.status = ReportStatus.FAILED
                    existing_report.error_message = str(exc)[:2000]
                    db.add(existing_report)
                    await db.flush()
                raise

    try:
        result = _run_async(_generate())
        log.info("Weekly report generated successfully", **result)
        return result

    except Exception as exc:
        log.error("Weekly report generation failed", error=str(exc))
        raise self.retry(exc=exc)


# ---------------------------------------------------------------------------
# Task: Scheduled daily summary for all organizations
# ---------------------------------------------------------------------------


@celery_app.task(
    name="incident_reports.scheduled_daily_summaries",
    queue="reports",
)
def scheduled_daily_summaries() -> dict:
    """Generate daily summaries for all active organizations.

    Called by Celery Beat at 6 AM daily. Iterates over all active
    organizations and dispatches individual daily summary tasks.

    Returns:
        Dict with count of dispatched tasks.
    """
    log = logger.bind(task="scheduled_daily_summaries")
    log.info("Running scheduled daily summaries")

    async def _dispatch():
        from app.database import get_db_context
        from app.models.organization import Organization
        from sqlalchemy import select

        dispatched = 0
        async with get_db_context() as db:
            result = await db.execute(
                select(Organization.id).where(Organization.is_active.is_(True))
            )
            org_ids = [str(row[0]) for row in result.all()]

        for org_id in org_ids:
            generate_daily_summary_task.delay(org_id)
            dispatched += 1

        return dispatched

    try:
        dispatched = _run_async(_dispatch())
        log.info("Scheduled daily summaries dispatched", count=dispatched)
        return {"dispatched": dispatched}
    except Exception as exc:
        log.error("Failed to dispatch daily summaries", error=str(exc))
        return {"dispatched": 0, "error": str(exc)}


# ---------------------------------------------------------------------------
# Task: Scheduled weekly reports for all organizations
# ---------------------------------------------------------------------------


@celery_app.task(
    name="incident_reports.scheduled_weekly_reports",
    queue="reports",
)
def scheduled_weekly_reports() -> dict:
    """Generate weekly reports for all active organizations.

    Called by Celery Beat on Monday at 7 AM. Dispatches individual
    weekly report tasks for each organization.

    Returns:
        Dict with count of dispatched tasks.
    """
    log = logger.bind(task="scheduled_weekly_reports")
    log.info("Running scheduled weekly reports")

    async def _dispatch():
        from app.database import get_db_context
        from app.models.organization import Organization
        from sqlalchemy import select

        dispatched = 0
        async with get_db_context() as db:
            result = await db.execute(
                select(Organization.id).where(Organization.is_active.is_(True))
            )
            org_ids = [str(row[0]) for row in result.all()]

        for org_id in org_ids:
            generate_weekly_report_task.delay(org_id)
            dispatched += 1

        return dispatched

    try:
        dispatched = _run_async(_dispatch())
        log.info("Scheduled weekly reports dispatched", count=dispatched)
        return {"dispatched": dispatched}
    except Exception as exc:
        log.error("Failed to dispatch weekly reports", error=str(exc))
        return {"dispatched": 0, "error": str(exc)}


# ---------------------------------------------------------------------------
# Task: Cleanup old reports
# ---------------------------------------------------------------------------


@celery_app.task(
    name="incident_reports.cleanup_old_reports",
    queue="maintenance",
)
def cleanup_old_reports(days: int = 180) -> dict:
    """Delete old report files from MinIO and mark records as cleaned.

    Args:
        days: Reports older than this many days will be cleaned up.

    Returns:
        Dict with count of cleaned reports.
    """
    log = logger.bind(task="cleanup_old_reports", retention_days=days)
    log.info("Starting old report cleanup")

    async def _cleanup():
        from app.database import get_db_context
        from app.models.incident_report import IncidentReport, ReportStatus
        from sqlalchemy import select

        cutoff = datetime.now(timezone.utc) - timedelta(days=days)
        cleaned = 0

        async with get_db_context() as db:
            result = await db.execute(
                select(IncidentReport).where(
                    IncidentReport.created_at < cutoff,
                    IncidentReport.file_path.isnot(None),
                )
            )
            old_reports = result.scalars().all()

            for report in old_reports:
                if report.file_path:
                    try:
                        from app.utils.storage import get_storage_client
                        storage = get_storage_client()
                        storage.delete_file(report.file_path)
                    except Exception as exc:
                        log.warning(
                            "Failed to delete report file",
                            file_path=report.file_path,
                            error=str(exc),
                        )
                        # Also try local file cleanup
                        if os.path.exists(report.file_path):
                            try:
                                os.remove(report.file_path)
                            except OSError:
                                pass

                    report.file_path = None
                    report.download_url = None
                    report.file_size = None
                    db.add(report)
                    cleaned += 1

            await db.flush()

        return cleaned

    try:
        cleaned = _run_async(_cleanup())
        log.info("Old report cleanup completed", cleaned=cleaned)
        return {"cleaned": cleaned, "retention_days": days}
    except Exception as exc:
        log.error("Report cleanup failed", error=str(exc))
        return {"cleaned": 0, "error": str(exc)}
