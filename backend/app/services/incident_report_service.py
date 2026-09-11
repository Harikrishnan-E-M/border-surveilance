"""Automated incident report generation service.

Generates structured PDF reports from alerts and analytics data.
Reports include executive summaries, event timelines, evidence (snapshots,
video clips, face matches, vehicle plates), camera coverage, and
recommendations.  Generated PDFs are uploaded to MinIO and tracked in the
database.
"""

from __future__ import annotations

import io
import math
import uuid
from datetime import date, datetime, timedelta, timezone
from typing import Any, Optional

import structlog
from sqlalchemy import and_, func, select
from sqlalchemy.ext.asyncio import AsyncSession

from app.config import get_settings
from app.exceptions import NotFoundError, ValidationError
from app.models.alert import Alert, AlertStatus
from app.models.analytics import FootfallRecord
from app.models.camera import Camera
from app.models.face import FaceEvent
from app.models.incident_report import (
    IncidentReport,
    ReportStatus,
    ReportTemplate,
    ReportType,
)
from app.models.rule import RuleSeverity, RuleType
from app.models.vehicle import VehicleEvent
from app.models.zone import Zone

logger = structlog.stdlib.get_logger(__name__)


# ── IncidentReportService ──────────────────────────────────────────────────


class IncidentReportService:
    """Service for automated generation, retrieval, and management of
    incident reports.
    """

    # ── Public: single-incident report ─────────────────────────────────

    @staticmethod
    async def generate_report(
        db: AsyncSession,
        org_id: uuid.UUID,
        alert_id: uuid.UUID,
        generated_by: uuid.UUID | None = None,
        template_id: uuid.UUID | None = None,
        include_evidence: bool = True,
    ) -> IncidentReport:
        """Auto-generate an incident report from a single alert.

        1. Fetch alert with related camera, zone, rule.
        2. Build 30-minute event timeline around the alert.
        3. Collect evidence (snapshots, clips, face matches, plates).
        4. Render a professional PDF and upload to MinIO.
        5. Persist the IncidentReport record.

        Args:
            db: Async database session.
            org_id: Organization ID.
            alert_id: The alert to generate a report for.
            generated_by: User ID who triggered the generation.
            template_id: Optional custom template.
            include_evidence: Whether to collect and embed evidence.

        Returns:
            The created IncidentReport ORM instance.
        """
        # 1. Fetch alert
        result = await db.execute(
            select(Alert).where(Alert.id == alert_id, Alert.org_id == org_id)
        )
        alert = result.scalars().first()
        if not alert:
            raise NotFoundError(resource="Alert", identifier=str(alert_id))

        # Create a generating record early so callers see progress
        title = f"Incident Report - {alert.title}"
        report = IncidentReport(
            org_id=org_id,
            title=title,
            report_type=ReportType.INCIDENT,
            alert_id=alert_id,
            status=ReportStatus.GENERATING,
            generated_by=generated_by,
            template_id=template_id,
        )
        db.add(report)
        await db.flush()

        try:
            # 2. Build timeline
            timeline = await IncidentReportService._build_timeline(db, alert, window_minutes=30)

            # 3. Collect evidence
            evidence: dict[str, Any] = {}
            if include_evidence:
                evidence = await IncidentReportService._collect_evidence(db, alert)

            # 4. Gather stats
            stats = await IncidentReportService._collect_alert_stats(db, org_id, alert)

            # 5. Build summary
            summary = IncidentReportService._build_executive_summary([alert], stats)

            # 6. Assemble report data
            report_data: dict[str, Any] = {
                "title": title,
                "report_type": "incident",
                "generated_at": datetime.now(timezone.utc).isoformat(),
                "organization": {
                    "id": str(org_id),
                    "name": alert.organization.name if alert.organization else "N/A",
                },
                "alert": {
                    "id": str(alert.id),
                    "title": alert.title,
                    "description": alert.description or "",
                    "alert_type": alert.alert_type.value if isinstance(alert.alert_type, RuleType) else str(alert.alert_type),
                    "severity": alert.severity.value if isinstance(alert.severity, RuleSeverity) else str(alert.severity),
                    "status": alert.status.value if isinstance(alert.status, AlertStatus) else str(alert.status),
                    "created_at": alert.created_at.isoformat() if alert.created_at else "",
                    "snapshot_path": alert.snapshot_path,
                    "video_clip_path": alert.video_clip_path,
                    "metadata": alert.metadata_json or {},
                },
                "camera": {
                    "id": str(alert.camera_id),
                    "name": alert.camera.name if alert.camera else "Unknown",
                    "location": getattr(alert.camera, "location", "") or "" if alert.camera else "",
                },
                "zone": {
                    "id": str(alert.zone_id) if alert.zone_id else None,
                    "name": alert.zone.name if alert.zone else "N/A",
                    "type": alert.zone.zone_type.value if alert.zone else "N/A",
                } if alert.zone_id else None,
                "rule": {
                    "id": str(alert.rule_id),
                    "name": alert.rule.name if alert.rule else "Unknown",
                    "type": alert.rule.rule_type.value if alert.rule and hasattr(alert.rule.rule_type, "value") else str(getattr(alert.rule, "rule_type", "")),
                } if alert.rule else None,
                "executive_summary": summary,
                "timeline": timeline,
                "evidence": evidence,
                "stats": stats,
            }

            # 7. Fetch optional template
            template = await IncidentReportService._get_template(db, org_id, template_id)

            # 8. Render HTML -> PDF
            html = IncidentReportService._build_html_report(report_data, template)
            pdf_bytes = IncidentReportService._render_pdf(html)

            # 9. Upload to MinIO
            file_path, download_url = IncidentReportService._upload_to_minio(
                pdf_bytes, org_id, report.id
            )

            # 10. Update report record
            report.status = ReportStatus.COMPLETED
            report.file_path = file_path
            report.file_size = len(pdf_bytes)
            report.page_count = IncidentReportService._estimate_page_count(len(pdf_bytes))
            report.summary_text = summary
            report.download_url = download_url
            report.metadata_json = {
                "alert_id": str(alert_id),
                "camera_id": str(alert.camera_id),
                "zone_id": str(alert.zone_id) if alert.zone_id else None,
                "severity": report_data["alert"]["severity"],
                "alert_type": report_data["alert"]["alert_type"],
                "timeline_events": len(timeline),
                "evidence_items": sum(len(v) if isinstance(v, list) else 1 for v in evidence.values()),
            }
            db.add(report)
            await db.flush()

            logger.info(
                "Incident report generated",
                report_id=str(report.id),
                alert_id=str(alert_id),
                file_size=report.file_size,
            )
            return report

        except Exception as exc:
            report.status = ReportStatus.FAILED
            report.error_message = str(exc)[:2000]
            db.add(report)
            await db.flush()
            logger.error(
                "Incident report generation failed",
                report_id=str(report.id),
                error=str(exc),
            )
            raise

    # ── Public: daily summary ──────────────────────────────────────────

    @staticmethod
    async def generate_daily_summary(
        db: AsyncSession,
        org_id: uuid.UUID,
        report_date: date,
        generated_by: uuid.UUID | None = None,
        template_id: uuid.UUID | None = None,
    ) -> IncidentReport:
        """Generate a daily incident summary report.

        Aggregates all alerts from the specified date into a single
        summary document with statistics, top incidents, and trends.
        """
        start_dt = datetime.combine(report_date, datetime.min.time(), tzinfo=timezone.utc)
        end_dt = start_dt + timedelta(days=1)
        title = f"Daily Security Summary - {report_date.isoformat()}"

        report = IncidentReport(
            org_id=org_id,
            title=title,
            report_type=ReportType.DAILY_SUMMARY,
            status=ReportStatus.GENERATING,
            generated_by=generated_by,
            template_id=template_id,
        )
        db.add(report)
        await db.flush()

        try:
            # Collect day's alerts
            alerts_result = await db.execute(
                select(Alert)
                .where(
                    Alert.org_id == org_id,
                    Alert.created_at >= start_dt,
                    Alert.created_at < end_dt,
                )
                .order_by(Alert.created_at.desc())
            )
            alerts = alerts_result.scalars().all()

            stats = await IncidentReportService._collect_period_stats(db, org_id, start_dt, end_dt)
            summary = IncidentReportService._build_executive_summary(alerts, stats)

            # Top incidents (critical & high severity)
            top_incidents = [
                {
                    "id": str(a.id),
                    "title": a.title,
                    "severity": a.severity.value if isinstance(a.severity, RuleSeverity) else str(a.severity),
                    "type": a.alert_type.value if isinstance(a.alert_type, RuleType) else str(a.alert_type),
                    "status": a.status.value if isinstance(a.status, AlertStatus) else str(a.status),
                    "camera": a.camera.name if a.camera else "Unknown",
                    "zone": a.zone.name if a.zone else "N/A",
                    "time": a.created_at.isoformat() if a.created_at else "",
                }
                for a in alerts[:20]
            ]

            report_data = {
                "title": title,
                "report_type": "daily_summary",
                "generated_at": datetime.now(timezone.utc).isoformat(),
                "period": {"start": start_dt.isoformat(), "end": end_dt.isoformat()},
                "executive_summary": summary,
                "stats": stats,
                "top_incidents": top_incidents,
                "total_alerts": len(alerts),
            }

            template = await IncidentReportService._get_template(db, org_id, template_id)
            html = IncidentReportService._build_html_report(report_data, template)
            pdf_bytes = IncidentReportService._render_pdf(html)
            file_path, download_url = IncidentReportService._upload_to_minio(
                pdf_bytes, org_id, report.id
            )

            report.status = ReportStatus.COMPLETED
            report.file_path = file_path
            report.file_size = len(pdf_bytes)
            report.page_count = IncidentReportService._estimate_page_count(len(pdf_bytes))
            report.summary_text = summary
            report.download_url = download_url
            report.metadata_json = {
                "date": report_date.isoformat(),
                "alert_count": len(alerts),
                "stats": stats,
            }
            db.add(report)
            await db.flush()

            logger.info("Daily summary generated", report_id=str(report.id), date=report_date.isoformat())
            return report

        except Exception as exc:
            report.status = ReportStatus.FAILED
            report.error_message = str(exc)[:2000]
            db.add(report)
            await db.flush()
            logger.error("Daily summary generation failed", error=str(exc))
            raise

    # ── Public: weekly report ──────────────────────────────────────────

    @staticmethod
    async def generate_weekly_report(
        db: AsyncSession,
        org_id: uuid.UUID,
        week_start: date,
        generated_by: uuid.UUID | None = None,
        template_id: uuid.UUID | None = None,
    ) -> IncidentReport:
        """Generate a weekly security report.

        Covers Monday through Sunday of the given week with trends,
        comparisons to the previous week, and detailed breakdowns.
        """
        start_dt = datetime.combine(week_start, datetime.min.time(), tzinfo=timezone.utc)
        end_dt = start_dt + timedelta(days=7)
        prev_start = start_dt - timedelta(days=7)
        title = f"Weekly Security Report - {week_start.isoformat()} to {(week_start + timedelta(days=6)).isoformat()}"

        report = IncidentReport(
            org_id=org_id,
            title=title,
            report_type=ReportType.WEEKLY_REPORT,
            status=ReportStatus.GENERATING,
            generated_by=generated_by,
            template_id=template_id,
        )
        db.add(report)
        await db.flush()

        try:
            alerts_result = await db.execute(
                select(Alert)
                .where(
                    Alert.org_id == org_id,
                    Alert.created_at >= start_dt,
                    Alert.created_at < end_dt,
                )
                .order_by(Alert.created_at.desc())
            )
            alerts = alerts_result.scalars().all()

            stats = await IncidentReportService._collect_period_stats(db, org_id, start_dt, end_dt)
            prev_stats = await IncidentReportService._collect_period_stats(db, org_id, prev_start, start_dt)
            summary = IncidentReportService._build_executive_summary(alerts, stats)

            # Daily breakdown
            daily_breakdown = []
            for day_offset in range(7):
                day = week_start + timedelta(days=day_offset)
                day_start = datetime.combine(day, datetime.min.time(), tzinfo=timezone.utc)
                day_end = day_start + timedelta(days=1)
                day_count_result = await db.execute(
                    select(func.count()).where(
                        Alert.org_id == org_id,
                        Alert.created_at >= day_start,
                        Alert.created_at < day_end,
                    )
                )
                day_count = day_count_result.scalar() or 0
                daily_breakdown.append({
                    "date": day.isoformat(),
                    "day_name": day.strftime("%A"),
                    "alert_count": day_count,
                })

            top_incidents = [
                {
                    "id": str(a.id),
                    "title": a.title,
                    "severity": a.severity.value if isinstance(a.severity, RuleSeverity) else str(a.severity),
                    "type": a.alert_type.value if isinstance(a.alert_type, RuleType) else str(a.alert_type),
                    "status": a.status.value if isinstance(a.status, AlertStatus) else str(a.status),
                    "camera": a.camera.name if a.camera else "Unknown",
                    "time": a.created_at.isoformat() if a.created_at else "",
                }
                for a in alerts[:30]
            ]

            # Compute week-over-week change
            prev_total = prev_stats.get("total_alerts", 0)
            curr_total = stats.get("total_alerts", 0)
            wow_change = (
                round(((curr_total - prev_total) / prev_total) * 100, 1) if prev_total > 0 else 0.0
            )

            report_data = {
                "title": title,
                "report_type": "weekly_report",
                "generated_at": datetime.now(timezone.utc).isoformat(),
                "period": {"start": start_dt.isoformat(), "end": end_dt.isoformat()},
                "executive_summary": summary,
                "stats": stats,
                "previous_week_stats": prev_stats,
                "week_over_week_change": wow_change,
                "daily_breakdown": daily_breakdown,
                "top_incidents": top_incidents,
                "total_alerts": len(alerts),
            }

            template = await IncidentReportService._get_template(db, org_id, template_id)
            html = IncidentReportService._build_html_report(report_data, template)
            pdf_bytes = IncidentReportService._render_pdf(html)
            file_path, download_url = IncidentReportService._upload_to_minio(
                pdf_bytes, org_id, report.id
            )

            report.status = ReportStatus.COMPLETED
            report.file_path = file_path
            report.file_size = len(pdf_bytes)
            report.page_count = IncidentReportService._estimate_page_count(len(pdf_bytes))
            report.summary_text = summary
            report.download_url = download_url
            report.metadata_json = {
                "week_start": week_start.isoformat(),
                "alert_count": len(alerts),
                "wow_change": wow_change,
                "stats": stats,
            }
            db.add(report)
            await db.flush()

            logger.info("Weekly report generated", report_id=str(report.id))
            return report

        except Exception as exc:
            report.status = ReportStatus.FAILED
            report.error_message = str(exc)[:2000]
            db.add(report)
            await db.flush()
            logger.error("Weekly report generation failed", error=str(exc))
            raise

    # ── Public: custom report ──────────────────────────────────────────

    @staticmethod
    async def generate_custom_report(
        db: AsyncSession,
        org_id: uuid.UUID,
        params: dict[str, Any],
        generated_by: uuid.UUID | None = None,
        template_id: uuid.UUID | None = None,
    ) -> IncidentReport:
        """Generate a custom report for a given date range, cameras, zones.

        ``params`` may include: start_date, end_date, camera_ids, zone_ids,
        title, include_evidence.
        """
        start_date = params.get("start_date")
        end_date = params.get("end_date")
        if not start_date or not end_date:
            raise ValidationError(message="start_date and end_date are required for custom reports.")

        if isinstance(start_date, str):
            start_date = datetime.fromisoformat(start_date)
        if isinstance(end_date, str):
            end_date = datetime.fromisoformat(end_date)

        if start_date >= end_date:
            raise ValidationError(message="start_date must be before end_date.")

        camera_ids = params.get("camera_ids", [])
        zone_ids = params.get("zone_ids", [])
        custom_title = params.get("title") or f"Custom Security Report - {start_date.strftime('%Y-%m-%d')} to {end_date.strftime('%Y-%m-%d')}"

        report = IncidentReport(
            org_id=org_id,
            title=custom_title,
            report_type=ReportType.CUSTOM,
            status=ReportStatus.GENERATING,
            generated_by=generated_by,
            template_id=template_id,
        )
        db.add(report)
        await db.flush()

        try:
            # Build filters
            alert_filters = [
                Alert.org_id == org_id,
                Alert.created_at >= start_date,
                Alert.created_at < end_date,
            ]
            if camera_ids:
                alert_filters.append(Alert.camera_id.in_(camera_ids))
            if zone_ids:
                alert_filters.append(Alert.zone_id.in_(zone_ids))

            alerts_result = await db.execute(
                select(Alert)
                .where(and_(*alert_filters))
                .order_by(Alert.created_at.desc())
            )
            alerts = alerts_result.scalars().all()

            stats = await IncidentReportService._collect_period_stats(
                db, org_id, start_date, end_date, camera_ids=camera_ids
            )
            summary = IncidentReportService._build_executive_summary(alerts, stats)

            top_incidents = [
                {
                    "id": str(a.id),
                    "title": a.title,
                    "severity": a.severity.value if isinstance(a.severity, RuleSeverity) else str(a.severity),
                    "type": a.alert_type.value if isinstance(a.alert_type, RuleType) else str(a.alert_type),
                    "status": a.status.value if isinstance(a.status, AlertStatus) else str(a.status),
                    "camera": a.camera.name if a.camera else "Unknown",
                    "zone": a.zone.name if a.zone else "N/A",
                    "time": a.created_at.isoformat() if a.created_at else "",
                }
                for a in alerts[:50]
            ]

            report_data = {
                "title": custom_title,
                "report_type": "custom",
                "generated_at": datetime.now(timezone.utc).isoformat(),
                "period": {"start": start_date.isoformat(), "end": end_date.isoformat()},
                "executive_summary": summary,
                "stats": stats,
                "top_incidents": top_incidents,
                "total_alerts": len(alerts),
                "filters": {
                    "camera_ids": [str(c) for c in camera_ids] if camera_ids else [],
                    "zone_ids": [str(z) for z in zone_ids] if zone_ids else [],
                },
            }

            template = await IncidentReportService._get_template(db, org_id, template_id)
            html = IncidentReportService._build_html_report(report_data, template)
            pdf_bytes = IncidentReportService._render_pdf(html)
            file_path, download_url = IncidentReportService._upload_to_minio(
                pdf_bytes, org_id, report.id
            )

            report.status = ReportStatus.COMPLETED
            report.file_path = file_path
            report.file_size = len(pdf_bytes)
            report.page_count = IncidentReportService._estimate_page_count(len(pdf_bytes))
            report.summary_text = summary
            report.download_url = download_url
            report.metadata_json = {
                "date_range": {"start": start_date.isoformat(), "end": end_date.isoformat()},
                "camera_ids": [str(c) for c in camera_ids] if camera_ids else [],
                "zone_ids": [str(z) for z in zone_ids] if zone_ids else [],
                "alert_count": len(alerts),
                "stats": stats,
            }
            db.add(report)
            await db.flush()

            logger.info("Custom report generated", report_id=str(report.id))
            return report

        except Exception as exc:
            report.status = ReportStatus.FAILED
            report.error_message = str(exc)[:2000]
            db.add(report)
            await db.flush()
            logger.error("Custom report generation failed", error=str(exc))
            raise

    # ── Public: list / get reports ─────────────────────────────────────

    @staticmethod
    async def get_reports(
        db: AsyncSession,
        org_id: uuid.UUID,
        filters: dict[str, Any] | None = None,
        page: int = 1,
        page_size: int = 20,
    ) -> dict[str, Any]:
        """List generated reports with pagination and filters."""
        query = select(IncidentReport).where(IncidentReport.org_id == org_id)

        if filters:
            if filters.get("report_type"):
                try:
                    rt = ReportType(filters["report_type"])
                    query = query.where(IncidentReport.report_type == rt)
                except ValueError:
                    pass
            if filters.get("status"):
                try:
                    rs = ReportStatus(filters["status"])
                    query = query.where(IncidentReport.status == rs)
                except ValueError:
                    pass
            if filters.get("start_date"):
                query = query.where(IncidentReport.created_at >= filters["start_date"])
            if filters.get("end_date"):
                query = query.where(IncidentReport.created_at <= filters["end_date"])
            if filters.get("generated_by"):
                query = query.where(IncidentReport.generated_by == filters["generated_by"])
            if filters.get("alert_id"):
                query = query.where(IncidentReport.alert_id == filters["alert_id"])

        count_q = select(func.count()).select_from(query.subquery())
        total = (await db.execute(count_q)).scalar() or 0

        query = (
            query.order_by(IncidentReport.created_at.desc())
            .offset((page - 1) * page_size)
            .limit(page_size)
        )
        result = await db.execute(query)
        reports = result.scalars().all()

        items = []
        for r in reports:
            item = {
                "id": str(r.id),
                "org_id": str(r.org_id),
                "title": r.title,
                "report_type": r.report_type.value if isinstance(r.report_type, ReportType) else str(r.report_type),
                "alert_id": str(r.alert_id) if r.alert_id else None,
                "status": r.status.value if isinstance(r.status, ReportStatus) else str(r.status),
                "file_path": r.file_path,
                "file_size": r.file_size,
                "page_count": r.page_count,
                "generated_by": str(r.generated_by) if r.generated_by else None,
                "generated_by_name": r.generated_by_user.full_name if r.generated_by_user else None,
                "summary_text": r.summary_text,
                "metadata_json": r.metadata_json,
                "error_message": r.error_message,
                "download_url": r.download_url,
                "template_id": str(r.template_id) if r.template_id else None,
                "created_at": r.created_at.isoformat() if r.created_at else None,
                "updated_at": r.updated_at.isoformat() if r.updated_at else None,
            }
            # Refresh presigned URL if needed
            if r.status == ReportStatus.COMPLETED and r.file_path:
                try:
                    from app.utils.storage import get_storage_client
                    storage = get_storage_client()
                    item["download_url"] = storage.get_presigned_url(r.file_path, expires=3600)
                except Exception:
                    pass
            items.append(item)

        return {
            "items": items,
            "total": total,
            "page": page,
            "page_size": page_size,
            "total_pages": math.ceil(total / page_size) if page_size else 0,
        }

    @staticmethod
    async def get_report(
        db: AsyncSession,
        org_id: uuid.UUID,
        report_id: uuid.UUID,
    ) -> dict[str, Any]:
        """Get a single report with refreshed download URL."""
        result = await db.execute(
            select(IncidentReport).where(
                IncidentReport.id == report_id,
                IncidentReport.org_id == org_id,
            )
        )
        report = result.scalars().first()
        if not report:
            raise NotFoundError(resource="IncidentReport", identifier=str(report_id))

        data = {
            "id": str(report.id),
            "org_id": str(report.org_id),
            "title": report.title,
            "report_type": report.report_type.value if isinstance(report.report_type, ReportType) else str(report.report_type),
            "alert_id": str(report.alert_id) if report.alert_id else None,
            "status": report.status.value if isinstance(report.status, ReportStatus) else str(report.status),
            "file_path": report.file_path,
            "file_size": report.file_size,
            "page_count": report.page_count,
            "generated_by": str(report.generated_by) if report.generated_by else None,
            "generated_by_name": report.generated_by_user.full_name if report.generated_by_user else None,
            "summary_text": report.summary_text,
            "metadata_json": report.metadata_json,
            "error_message": report.error_message,
            "download_url": report.download_url,
            "template_id": str(report.template_id) if report.template_id else None,
            "created_at": report.created_at.isoformat() if report.created_at else None,
            "updated_at": report.updated_at.isoformat() if report.updated_at else None,
        }

        # Refresh presigned URL
        if report.status == ReportStatus.COMPLETED and report.file_path:
            try:
                from app.utils.storage import get_storage_client
                storage = get_storage_client()
                data["download_url"] = storage.get_presigned_url(report.file_path, expires=3600)
            except Exception:
                pass

        return data

    # ── Private: timeline builder ──────────────────────────────────────

    @staticmethod
    async def _build_timeline(
        db: AsyncSession,
        alert: Alert,
        window_minutes: int = 30,
    ) -> list[dict[str, Any]]:
        """Build event timeline around the alert within +/- window."""
        if not alert.created_at:
            return []

        window_start = alert.created_at - timedelta(minutes=window_minutes)
        window_end = alert.created_at + timedelta(minutes=window_minutes)

        timeline: list[dict[str, Any]] = []

        # 1. Nearby alerts on the same camera
        nearby_alerts = await db.execute(
            select(Alert)
            .where(
                Alert.org_id == alert.org_id,
                Alert.camera_id == alert.camera_id,
                Alert.created_at >= window_start,
                Alert.created_at <= window_end,
            )
            .order_by(Alert.created_at.asc())
        )
        for a in nearby_alerts.scalars().all():
            timeline.append({
                "timestamp": a.created_at.isoformat() if a.created_at else "",
                "event_type": "alert",
                "title": a.title,
                "severity": a.severity.value if isinstance(a.severity, RuleSeverity) else str(a.severity),
                "description": a.description or "",
                "is_primary": str(a.id) == str(alert.id),
            })

        # 2. Face events in the same zone / camera
        try:
            face_filters = [
                FaceEvent.camera_id == alert.camera_id,
                FaceEvent.created_at >= window_start,
                FaceEvent.created_at <= window_end,
            ]
            if alert.zone_id:
                face_filters.append(FaceEvent.zone_id == alert.zone_id)

            face_result = await db.execute(
                select(FaceEvent)
                .where(and_(*face_filters))
                .order_by(FaceEvent.created_at.asc())
                .limit(50)
            )
            for fe in face_result.scalars().all():
                person_name = fe.person.name if hasattr(fe, "person") and fe.person else "Unknown"
                timeline.append({
                    "timestamp": fe.created_at.isoformat() if fe.created_at else "",
                    "event_type": "face_detection",
                    "title": f"Face detected: {person_name}",
                    "description": f"Confidence: {fe.confidence:.1%}" if hasattr(fe, "confidence") and fe.confidence else "",
                })
        except Exception as exc:
            logger.debug("Could not fetch face events for timeline", error=str(exc))

        # 3. Vehicle events in the same zone / camera
        try:
            vehicle_filters = [
                VehicleEvent.camera_id == alert.camera_id,
                VehicleEvent.created_at >= window_start,
                VehicleEvent.created_at <= window_end,
            ]
            vehicle_result = await db.execute(
                select(VehicleEvent)
                .where(and_(*vehicle_filters))
                .order_by(VehicleEvent.created_at.asc())
                .limit(50)
            )
            for ve in vehicle_result.scalars().all():
                plate = ve.plate_number if hasattr(ve, "plate_number") else "Unknown"
                timeline.append({
                    "timestamp": ve.created_at.isoformat() if ve.created_at else "",
                    "event_type": "vehicle_detection",
                    "title": f"Vehicle detected: {plate}",
                    "description": getattr(ve, "direction", "").value if hasattr(getattr(ve, "direction", None), "value") else "",
                })
        except Exception as exc:
            logger.debug("Could not fetch vehicle events for timeline", error=str(exc))

        # Sort by timestamp
        timeline.sort(key=lambda x: x.get("timestamp", ""))
        return timeline

    # ── Private: evidence collector ────────────────────────────────────

    @staticmethod
    async def _collect_evidence(
        db: AsyncSession,
        alert: Alert,
    ) -> dict[str, Any]:
        """Gather thumbnails, video clips, face matches, and vehicle plates."""
        evidence: dict[str, Any] = {
            "snapshots": [],
            "video_clips": [],
            "face_matches": [],
            "vehicle_plates": [],
        }

        # Alert snapshot
        if alert.snapshot_path:
            evidence["snapshots"].append({
                "path": alert.snapshot_path,
                "description": "Alert trigger snapshot",
                "timestamp": alert.created_at.isoformat() if alert.created_at else "",
            })

        # Alert video clip
        if alert.video_clip_path:
            evidence["video_clips"].append({
                "path": alert.video_clip_path,
                "description": "Alert trigger video clip",
                "timestamp": alert.created_at.isoformat() if alert.created_at else "",
            })

        # Face matches around the alert time
        if alert.created_at:
            window_start = alert.created_at - timedelta(minutes=15)
            window_end = alert.created_at + timedelta(minutes=15)

            try:
                face_result = await db.execute(
                    select(FaceEvent)
                    .where(
                        FaceEvent.camera_id == alert.camera_id,
                        FaceEvent.created_at >= window_start,
                        FaceEvent.created_at <= window_end,
                    )
                    .order_by(FaceEvent.created_at.desc())
                    .limit(10)
                )
                for fe in face_result.scalars().all():
                    person_name = fe.person.name if hasattr(fe, "person") and fe.person else "Unknown"
                    evidence["face_matches"].append({
                        "person_name": person_name,
                        "confidence": fe.confidence if hasattr(fe, "confidence") else None,
                        "image_path": fe.snapshot_path if hasattr(fe, "snapshot_path") else None,
                        "timestamp": fe.created_at.isoformat() if fe.created_at else "",
                    })
            except Exception as exc:
                logger.debug("Could not collect face evidence", error=str(exc))

            # Vehicle plates
            try:
                vehicle_result = await db.execute(
                    select(VehicleEvent)
                    .where(
                        VehicleEvent.camera_id == alert.camera_id,
                        VehicleEvent.created_at >= window_start,
                        VehicleEvent.created_at <= window_end,
                    )
                    .order_by(VehicleEvent.created_at.desc())
                    .limit(10)
                )
                for ve in vehicle_result.scalars().all():
                    evidence["vehicle_plates"].append({
                        "plate_number": ve.plate_number if hasattr(ve, "plate_number") else "Unknown",
                        "confidence": ve.confidence if hasattr(ve, "confidence") else None,
                        "image_path": ve.snapshot_path if hasattr(ve, "snapshot_path") else None,
                        "timestamp": ve.created_at.isoformat() if ve.created_at else "",
                    })
            except Exception as exc:
                logger.debug("Could not collect vehicle evidence", error=str(exc))

        return evidence

    # ── Private: statistics collectors ─────────────────────────────────

    @staticmethod
    async def _collect_alert_stats(
        db: AsyncSession,
        org_id: uuid.UUID,
        alert: Alert,
    ) -> dict[str, Any]:
        """Collect statistics relevant to a single-incident report."""
        if not alert.created_at:
            return {"total_alerts": 0, "by_severity": {}, "by_type": {}}

        day_start = datetime.combine(alert.created_at.date(), datetime.min.time(), tzinfo=timezone.utc)
        day_end = day_start + timedelta(days=1)
        return await IncidentReportService._collect_period_stats(db, org_id, day_start, day_end)

    @staticmethod
    async def _collect_period_stats(
        db: AsyncSession,
        org_id: uuid.UUID,
        start_dt: datetime,
        end_dt: datetime,
        camera_ids: list | None = None,
    ) -> dict[str, Any]:
        """Collect alert statistics for a given period."""
        base_filters = [
            Alert.org_id == org_id,
            Alert.created_at >= start_dt,
            Alert.created_at < end_dt,
        ]
        if camera_ids:
            base_filters.append(Alert.camera_id.in_(camera_ids))

        # Total
        total_q = select(func.count()).where(and_(*base_filters)).select_from(Alert)
        total = (await db.execute(total_q)).scalar() or 0

        # By severity
        sev_q = (
            select(Alert.severity, func.count().label("count"))
            .where(and_(*base_filters))
            .group_by(Alert.severity)
        )
        sev_result = await db.execute(sev_q)
        by_severity = {
            (row.severity.value if isinstance(row.severity, RuleSeverity) else str(row.severity)): row.count
            for row in sev_result.all()
        }

        # By type
        type_q = (
            select(Alert.alert_type, func.count().label("count"))
            .where(and_(*base_filters))
            .group_by(Alert.alert_type)
        )
        type_result = await db.execute(type_q)
        by_type = {
            (row.alert_type.value if isinstance(row.alert_type, RuleType) else str(row.alert_type)): row.count
            for row in type_result.all()
        }

        # By status
        status_q = (
            select(Alert.status, func.count().label("count"))
            .where(and_(*base_filters))
            .group_by(Alert.status)
        )
        status_result = await db.execute(status_q)
        by_status = {
            (row.status.value if isinstance(row.status, AlertStatus) else str(row.status)): row.count
            for row in status_result.all()
        }

        # Top cameras
        cam_q = (
            select(Camera.name, func.count(Alert.id))
            .join(Camera, Alert.camera_id == Camera.id)
            .where(and_(*base_filters))
            .group_by(Camera.name)
            .order_by(func.count(Alert.id).desc())
            .limit(10)
        )
        cam_result = await db.execute(cam_q)
        top_cameras = {row[0]: row[1] for row in cam_result.all()}

        # Resolution rate
        resolved_count = by_status.get("resolved", 0) + by_status.get("false_positive", 0)
        resolution_rate = round(resolved_count / total * 100, 1) if total > 0 else 0.0

        return {
            "total_alerts": total,
            "by_severity": by_severity,
            "by_type": by_type,
            "by_status": by_status,
            "top_cameras": top_cameras,
            "resolution_rate": resolution_rate,
        }

    # ── Private: executive summary builder ─────────────────────────────

    @staticmethod
    def _build_executive_summary(
        alerts: list[Alert] | list,
        stats: dict[str, Any],
    ) -> str:
        """Generate an executive summary text from alert data and statistics."""
        total = stats.get("total_alerts", len(alerts))
        by_severity = stats.get("by_severity", {})
        by_type = stats.get("by_type", {})
        resolution_rate = stats.get("resolution_rate", 0)

        critical_count = by_severity.get("critical", 0)
        high_count = by_severity.get("high", 0)
        medium_count = by_severity.get("medium", 0)
        low_count = by_severity.get("low", 0)

        # Determine the most common alert type
        top_type = max(by_type, key=by_type.get, default="N/A") if by_type else "N/A"  # type: ignore[arg-type]
        top_type_count = by_type.get(top_type, 0)

        summary_parts = [
            f"During the reporting period, a total of {total} security alerts were recorded.",
        ]

        if critical_count > 0 or high_count > 0:
            summary_parts.append(
                f"Of these, {critical_count} were classified as critical and {high_count} as "
                f"high severity, requiring immediate attention."
            )

        if medium_count > 0 or low_count > 0:
            summary_parts.append(
                f"Additionally, {medium_count} medium and {low_count} low severity alerts were logged."
            )

        if top_type != "N/A":
            summary_parts.append(
                f"The most frequent alert type was '{top_type.replace('_', ' ')}' "
                f"with {top_type_count} occurrences."
            )

        if resolution_rate > 0:
            summary_parts.append(
                f"The overall alert resolution rate was {resolution_rate}%."
            )

        top_cameras = stats.get("top_cameras", {})
        if top_cameras:
            top_cam = next(iter(top_cameras))
            summary_parts.append(
                f"The camera with the highest alert activity was '{top_cam}' "
                f"with {top_cameras[top_cam]} alerts."
            )

        if critical_count > 2:
            summary_parts.append(
                "RECOMMENDATION: The elevated number of critical alerts warrants an "
                "immediate review of security protocols and camera coverage for the "
                "affected areas."
            )
        elif total > 50:
            summary_parts.append(
                "RECOMMENDATION: Consider reviewing alert thresholds and rule "
                "configurations to reduce false positives and improve response times."
            )

        return " ".join(summary_parts)

    # ── Private: HTML report builder ───────────────────────────────────

    @staticmethod
    def _build_html_report(
        report_data: dict[str, Any],
        template: Optional[ReportTemplate] = None,
    ) -> str:
        """Render a complete HTML document from report data.

        Uses a professional layout suitable for WeasyPrint PDF rendering
        with headers, footers, page numbers, and a table of contents.
        """
        title = report_data.get("title", "VisionAI Security Report")
        generated_at = report_data.get("generated_at", "")
        report_type = report_data.get("report_type", "incident")

        # Custom template overrides
        custom_header = template.header_html if template and template.header_html else ""
        custom_footer = template.footer_html if template and template.footer_html else ""
        custom_css = template.css_styles if template and template.css_styles else ""

        # ── Build sections HTML ────────────────────────────────────────
        sections_html = ""

        # Executive Summary
        summary = report_data.get("executive_summary", "")
        if summary:
            sections_html += f"""
            <div class="section" id="executive-summary">
                <h2>1. Executive Summary</h2>
                <div class="summary-box">{summary}</div>
            </div>
            """

        # Incident Details (single-incident reports)
        alert_data = report_data.get("alert")
        if alert_data:
            severity_class = f"severity-{alert_data.get('severity', 'medium')}"
            sections_html += f"""
            <div class="section" id="incident-details">
                <h2>2. Incident Details</h2>
                <table class="detail-table">
                    <tr><td class="label">Alert ID</td><td>{alert_data.get('id', 'N/A')}</td></tr>
                    <tr><td class="label">Title</td><td>{alert_data.get('title', 'N/A')}</td></tr>
                    <tr><td class="label">Type</td><td>{alert_data.get('alert_type', 'N/A').replace('_', ' ').title()}</td></tr>
                    <tr><td class="label">Severity</td><td><span class="badge {severity_class}">{alert_data.get('severity', 'N/A').upper()}</span></td></tr>
                    <tr><td class="label">Status</td><td>{alert_data.get('status', 'N/A').replace('_', ' ').title()}</td></tr>
                    <tr><td class="label">Timestamp</td><td>{alert_data.get('created_at', 'N/A')}</td></tr>
                    <tr><td class="label">Description</td><td>{alert_data.get('description', 'No description provided.')}</td></tr>
                </table>
            </div>
            """

        # Camera Coverage
        camera_data = report_data.get("camera")
        zone_data = report_data.get("zone")
        if camera_data:
            zone_info = ""
            if zone_data:
                zone_info = f"""
                <tr><td class="label">Zone</td><td>{zone_data.get('name', 'N/A')}</td></tr>
                <tr><td class="label">Zone Type</td><td>{zone_data.get('type', 'N/A').replace('_', ' ').title()}</td></tr>
                """
            sections_html += f"""
            <div class="section" id="camera-coverage">
                <h2>3. Camera Coverage & Affected Zones</h2>
                <table class="detail-table">
                    <tr><td class="label">Camera</td><td>{camera_data.get('name', 'N/A')}</td></tr>
                    <tr><td class="label">Location</td><td>{camera_data.get('location', 'N/A')}</td></tr>
                    {zone_info}
                </table>
            </div>
            """

        # Timeline
        timeline = report_data.get("timeline", [])
        if timeline:
            timeline_rows = ""
            for event in timeline:
                primary_class = "primary-event" if event.get("is_primary") else ""
                sev = event.get("severity", "")
                sev_badge = f'<span class="badge severity-{sev}">{sev.upper()}</span>' if sev else ""
                timeline_rows += f"""
                <tr class="{primary_class}">
                    <td class="time-col">{event.get('timestamp', '')[:19].replace('T', ' ')}</td>
                    <td><span class="event-type">{event.get('event_type', '').replace('_', ' ').title()}</span></td>
                    <td>{event.get('title', '')} {sev_badge}</td>
                    <td>{event.get('description', '')}</td>
                </tr>
                """
            sections_html += f"""
            <div class="section" id="timeline">
                <h2>4. Timeline of Events</h2>
                <table class="data-table">
                    <thead>
                        <tr>
                            <th>Timestamp</th>
                            <th>Event Type</th>
                            <th>Details</th>
                            <th>Notes</th>
                        </tr>
                    </thead>
                    <tbody>
                        {timeline_rows}
                    </tbody>
                </table>
            </div>
            """

        # Evidence
        evidence = report_data.get("evidence", {})
        if any(evidence.get(k) for k in ("snapshots", "video_clips", "face_matches", "vehicle_plates")):
            evidence_html = ""

            snapshots = evidence.get("snapshots", [])
            if snapshots:
                snap_items = "".join(
                    f'<li>Snapshot at {s.get("timestamp", "N/A")}: {s.get("description", "")}</li>'
                    for s in snapshots
                )
                evidence_html += f"<h3>Snapshots</h3><ul>{snap_items}</ul>"

            clips = evidence.get("video_clips", [])
            if clips:
                clip_items = "".join(
                    f'<li>Video clip at {c.get("timestamp", "N/A")}: {c.get("description", "")}</li>'
                    for c in clips
                )
                evidence_html += f"<h3>Video Clips</h3><ul>{clip_items}</ul>"

            faces = evidence.get("face_matches", [])
            if faces:
                face_rows = "".join(
                    f'<tr><td>{f.get("person_name", "Unknown")}</td>'
                    f'<td>{"{:.1%}".format(f["confidence"]) if f.get("confidence") else "N/A"}</td>'
                    f'<td>{f.get("timestamp", "")[:19].replace("T", " ")}</td></tr>'
                    for f in faces
                )
                evidence_html += f"""
                <h3>Face Recognition Matches</h3>
                <table class="data-table">
                    <thead><tr><th>Person</th><th>Confidence</th><th>Timestamp</th></tr></thead>
                    <tbody>{face_rows}</tbody>
                </table>
                """

            plates = evidence.get("vehicle_plates", [])
            if plates:
                plate_rows = "".join(
                    f'<tr><td>{p.get("plate_number", "Unknown")}</td>'
                    f'<td>{"{:.1%}".format(p["confidence"]) if p.get("confidence") else "N/A"}</td>'
                    f'<td>{p.get("timestamp", "")[:19].replace("T", " ")}</td></tr>'
                    for p in plates
                )
                evidence_html += f"""
                <h3>Vehicle Plate Detections</h3>
                <table class="data-table">
                    <thead><tr><th>Plate Number</th><th>Confidence</th><th>Timestamp</th></tr></thead>
                    <tbody>{plate_rows}</tbody>
                </table>
                """

            sections_html += f"""
            <div class="section" id="evidence">
                <h2>5. Evidence</h2>
                {evidence_html}
            </div>
            """

        # Statistics
        stats = report_data.get("stats", {})
        if stats:
            section_num = "6" if alert_data else "2"

            severity_rows = "".join(
                f"<tr><td>{k.title()}</td><td>{v}</td></tr>"
                for k, v in stats.get("by_severity", {}).items()
            )
            type_rows = "".join(
                f"<tr><td>{k.replace('_', ' ').title()}</td><td>{v}</td></tr>"
                for k, v in stats.get("by_type", {}).items()
            )
            status_rows = "".join(
                f"<tr><td>{k.replace('_', ' ').title()}</td><td>{v}</td></tr>"
                for k, v in stats.get("by_status", {}).items()
            )
            camera_rows = "".join(
                f"<tr><td>{k}</td><td>{v}</td></tr>"
                for k, v in stats.get("top_cameras", {}).items()
            )

            sections_html += f"""
            <div class="section" id="statistics">
                <h2>{section_num}. Alert Statistics</h2>
                <div class="stats-grid">
                    <div class="stat-card">
                        <div class="stat-value">{stats.get('total_alerts', 0)}</div>
                        <div class="stat-label">Total Alerts</div>
                    </div>
                    <div class="stat-card">
                        <div class="stat-value">{stats.get('resolution_rate', 0)}%</div>
                        <div class="stat-label">Resolution Rate</div>
                    </div>
                    <div class="stat-card">
                        <div class="stat-value">{stats.get('by_severity', {{}}).get('critical', 0)}</div>
                        <div class="stat-label">Critical Alerts</div>
                    </div>
                    <div class="stat-card">
                        <div class="stat-value">{stats.get('by_severity', {{}}).get('high', 0)}</div>
                        <div class="stat-label">High Severity</div>
                    </div>
                </div>
                {"<h3>By Severity</h3><table class='data-table'><thead><tr><th>Severity</th><th>Count</th></tr></thead><tbody>" + severity_rows + "</tbody></table>" if severity_rows else ""}
                {"<h3>By Type</h3><table class='data-table'><thead><tr><th>Type</th><th>Count</th></tr></thead><tbody>" + type_rows + "</tbody></table>" if type_rows else ""}
                {"<h3>By Status</h3><table class='data-table'><thead><tr><th>Status</th><th>Count</th></tr></thead><tbody>" + status_rows + "</tbody></table>" if status_rows else ""}
                {"<h3>Top Cameras</h3><table class='data-table'><thead><tr><th>Camera</th><th>Alerts</th></tr></thead><tbody>" + camera_rows + "</tbody></table>" if camera_rows else ""}
            </div>
            """

        # Daily breakdown (weekly reports)
        daily_breakdown = report_data.get("daily_breakdown", [])
        if daily_breakdown:
            day_rows = "".join(
                f"<tr><td>{d.get('day_name', '')}</td><td>{d.get('date', '')}</td><td>{d.get('alert_count', 0)}</td></tr>"
                for d in daily_breakdown
            )
            wow = report_data.get("week_over_week_change", 0)
            wow_class = "trend-up" if wow > 0 else "trend-down" if wow < 0 else ""
            wow_symbol = "+" if wow > 0 else ""
            sections_html += f"""
            <div class="section" id="daily-breakdown">
                <h2>7. Daily Breakdown</h2>
                <p class="wow-change {wow_class}">Week-over-week change: {wow_symbol}{wow}%</p>
                <table class="data-table">
                    <thead><tr><th>Day</th><th>Date</th><th>Alerts</th></tr></thead>
                    <tbody>{day_rows}</tbody>
                </table>
            </div>
            """

        # Top incidents (summary / custom)
        top_incidents = report_data.get("top_incidents", [])
        if top_incidents and not alert_data:
            incident_rows = "".join(
                f"""<tr>
                    <td class="time-col">{i.get('time', '')[:19].replace('T', ' ')}</td>
                    <td>{i.get('title', '')}</td>
                    <td><span class="badge severity-{i.get('severity', '')}">{i.get('severity', '').upper()}</span></td>
                    <td>{i.get('type', '').replace('_', ' ').title()}</td>
                    <td>{i.get('camera', '')}</td>
                    <td>{i.get('status', '').replace('_', ' ').title()}</td>
                </tr>"""
                for i in top_incidents
            )
            sections_html += f"""
            <div class="section" id="top-incidents">
                <h2>Top Incidents</h2>
                <table class="data-table">
                    <thead><tr><th>Time</th><th>Title</th><th>Severity</th><th>Type</th><th>Camera</th><th>Status</th></tr></thead>
                    <tbody>{incident_rows}</tbody>
                </table>
            </div>
            """

        # Recommendations section
        sections_html += """
        <div class="section" id="recommendations">
            <h2>Recommendations</h2>
            <ul class="recommendations-list">
                <li>Review and update alert thresholds for cameras with the highest alert counts.</li>
                <li>Ensure all critical and high-severity alerts are investigated and documented within 24 hours.</li>
                <li>Verify that camera coverage adequately monitors all restricted zones.</li>
                <li>Conduct regular reviews of false positive rates and adjust detection sensitivity accordingly.</li>
                <li>Maintain incident response procedures and ensure all security personnel are trained on protocols.</li>
            </ul>
        </div>
        """

        # Period info
        period = report_data.get("period", {})
        period_html = ""
        if period:
            period_html = f"Period: {period.get('start', '')[:10]} to {period.get('end', '')[:10]}"

        # ── Assemble full HTML ─────────────────────────────────────────
        return f"""<!DOCTYPE html>
<html lang="en">
<head>
    <meta charset="utf-8">
    <title>{title}</title>
    <style>
        @page {{
            size: A4;
            margin: 20mm 15mm 25mm 15mm;
            @top-center {{
                content: "{title}";
                font-size: 8pt;
                color: #666;
            }}
            @bottom-left {{
                content: "VisionAI Platform - Confidential";
                font-size: 7pt;
                color: #999;
            }}
            @bottom-right {{
                content: "Page " counter(page) " of " counter(pages);
                font-size: 7pt;
                color: #999;
            }}
        }}
        @page :first {{
            @top-center {{ content: ""; }}
        }}

        * {{ margin: 0; padding: 0; box-sizing: border-box; }}

        body {{
            font-family: 'Helvetica Neue', 'Arial', 'Segoe UI', sans-serif;
            font-size: 10pt;
            line-height: 1.5;
            color: #1a1a2e;
            background: white;
        }}

        /* Cover page */
        .cover {{
            page-break-after: always;
            display: flex;
            flex-direction: column;
            justify-content: center;
            align-items: center;
            min-height: 250mm;
            text-align: center;
            padding: 40mm 20mm;
        }}
        .cover .logo {{
            font-size: 28pt;
            font-weight: 800;
            color: #0f3460;
            letter-spacing: 2px;
            margin-bottom: 8mm;
        }}
        .cover .logo-sub {{
            font-size: 11pt;
            color: #666;
            margin-bottom: 20mm;
            letter-spacing: 1px;
        }}
        .cover .report-title {{
            font-size: 20pt;
            font-weight: 700;
            color: #16213e;
            margin-bottom: 8mm;
            max-width: 140mm;
        }}
        .cover .report-meta {{
            font-size: 10pt;
            color: #555;
            line-height: 2;
        }}
        .cover .divider {{
            width: 60mm;
            height: 2px;
            background: linear-gradient(90deg, #0f3460, #e94560);
            margin: 10mm auto;
        }}
        .cover .confidential {{
            margin-top: 20mm;
            padding: 4mm 8mm;
            border: 1px solid #e94560;
            color: #e94560;
            font-size: 8pt;
            font-weight: 600;
            letter-spacing: 2px;
            text-transform: uppercase;
        }}

        /* TOC */
        .toc {{
            page-break-after: always;
            padding: 10mm 0;
        }}
        .toc h2 {{
            font-size: 16pt;
            color: #0f3460;
            border-bottom: 2px solid #0f3460;
            padding-bottom: 3mm;
            margin-bottom: 6mm;
        }}
        .toc ul {{
            list-style: none;
            padding-left: 0;
        }}
        .toc li {{
            padding: 2mm 0;
            border-bottom: 1px dotted #ddd;
            font-size: 10pt;
        }}
        .toc li a {{
            color: #16213e;
            text-decoration: none;
        }}

        /* Section styling */
        .section {{
            margin-bottom: 8mm;
            page-break-inside: avoid;
        }}
        .section h2 {{
            font-size: 14pt;
            color: #0f3460;
            border-bottom: 2px solid #16213e;
            padding-bottom: 2mm;
            margin-bottom: 4mm;
            page-break-after: avoid;
        }}
        .section h3 {{
            font-size: 11pt;
            color: #16213e;
            margin-top: 4mm;
            margin-bottom: 2mm;
        }}

        /* Summary box */
        .summary-box {{
            background: #f0f4ff;
            border-left: 4px solid #0f3460;
            padding: 4mm 5mm;
            margin: 3mm 0;
            font-size: 10pt;
            line-height: 1.6;
        }}

        /* Detail table (key-value) */
        .detail-table {{
            width: 100%;
            border-collapse: collapse;
            margin: 3mm 0;
        }}
        .detail-table td {{
            padding: 2mm 3mm;
            border-bottom: 1px solid #eee;
            vertical-align: top;
        }}
        .detail-table .label {{
            width: 30%;
            font-weight: 600;
            color: #333;
            background: #f8f9fa;
        }}

        /* Data table */
        .data-table {{
            width: 100%;
            border-collapse: collapse;
            margin: 3mm 0;
            font-size: 9pt;
        }}
        .data-table thead th {{
            background: #0f3460;
            color: white;
            padding: 2.5mm 3mm;
            text-align: left;
            font-weight: 600;
            font-size: 8pt;
            text-transform: uppercase;
            letter-spacing: 0.5px;
        }}
        .data-table tbody td {{
            padding: 2mm 3mm;
            border-bottom: 1px solid #eee;
        }}
        .data-table tbody tr:nth-child(even) {{
            background: #f8f9fa;
        }}
        .data-table tbody tr.primary-event {{
            background: #fff3cd;
            font-weight: 600;
        }}
        .time-col {{
            white-space: nowrap;
            font-family: 'Courier New', monospace;
            font-size: 8.5pt;
        }}

        /* Stats grid */
        .stats-grid {{
            display: flex;
            gap: 4mm;
            margin: 4mm 0;
            flex-wrap: wrap;
        }}
        .stat-card {{
            flex: 1;
            min-width: 35mm;
            background: #f8f9fa;
            border: 1px solid #e0e0e0;
            border-radius: 2mm;
            padding: 3mm 4mm;
            text-align: center;
        }}
        .stat-value {{
            font-size: 18pt;
            font-weight: 700;
            color: #0f3460;
        }}
        .stat-label {{
            font-size: 7pt;
            color: #666;
            text-transform: uppercase;
            letter-spacing: 0.5px;
            margin-top: 1mm;
        }}

        /* Badges */
        .badge {{
            display: inline-block;
            padding: 0.5mm 3mm;
            border-radius: 1mm;
            font-size: 7pt;
            font-weight: 600;
            text-transform: uppercase;
            letter-spacing: 0.5px;
        }}
        .severity-critical {{ background: #fde8e8; color: #c81e1e; }}
        .severity-high {{ background: #feecdc; color: #c05621; }}
        .severity-medium {{ background: #fef3cd; color: #92400e; }}
        .severity-low {{ background: #def7ec; color: #03543f; }}

        .event-type {{
            display: inline-block;
            padding: 0.5mm 2mm;
            background: #e8f4f8;
            border-radius: 1mm;
            font-size: 7.5pt;
            color: #0369a1;
        }}

        /* WoW change */
        .wow-change {{ font-weight: 600; font-size: 11pt; margin-bottom: 3mm; }}
        .trend-up {{ color: #c81e1e; }}
        .trend-down {{ color: #03543f; }}

        /* Recommendations */
        .recommendations-list {{
            padding-left: 6mm;
        }}
        .recommendations-list li {{
            margin-bottom: 2mm;
            line-height: 1.5;
        }}

        /* Custom header/footer */
        .custom-header {{ margin-bottom: 5mm; }}
        .custom-footer {{ margin-top: 5mm; border-top: 1px solid #ddd; padding-top: 3mm; }}

        /* Print optimizations */
        @media print {{
            body {{ -webkit-print-color-adjust: exact; print-color-adjust: exact; }}
        }}

        {custom_css}
    </style>
</head>
<body>
    <!-- Cover Page -->
    <div class="cover">
        {custom_header if custom_header else '<div class="logo">VisionAI</div><div class="logo-sub">Intelligent Video Analytics Platform</div>'}
        <div class="divider"></div>
        <div class="report-title">{title}</div>
        <div class="report-meta">
            {f'<div>{period_html}</div>' if period_html else ''}
            <div>Generated: {generated_at[:19].replace('T', ' ')} UTC</div>
            <div>Report Type: {report_type.replace('_', ' ').title()}</div>
        </div>
        <div class="confidential">Confidential</div>
    </div>

    <!-- Table of Contents -->
    <div class="toc">
        <h2>Table of Contents</h2>
        <ul>
            {"<li><a href='#executive-summary'>1. Executive Summary</a></li>" if summary else ""}
            {"<li><a href='#incident-details'>2. Incident Details</a></li>" if alert_data else ""}
            {"<li><a href='#camera-coverage'>3. Camera Coverage & Affected Zones</a></li>" if camera_data else ""}
            {"<li><a href='#timeline'>4. Timeline of Events</a></li>" if timeline else ""}
            {"<li><a href='#evidence'>5. Evidence</a></li>" if evidence else ""}
            {"<li><a href='#statistics'>Statistics</a></li>" if stats else ""}
            {"<li><a href='#daily-breakdown'>Daily Breakdown</a></li>" if daily_breakdown else ""}
            {"<li><a href='#top-incidents'>Top Incidents</a></li>" if top_incidents and not alert_data else ""}
            <li><a href="#recommendations">Recommendations</a></li>
        </ul>
    </div>

    <!-- Report Body -->
    {sections_html}

    <!-- Footer -->
    <div class="custom-footer">
        {custom_footer if custom_footer else '<p style="font-size: 8pt; color: #999; text-align: center;">Generated by VisionAI Platform. This report is confidential and intended for authorized personnel only.</p>'}
    </div>
</body>
</html>"""

    # ── Private: PDF renderer ──────────────────────────────────────────

    @staticmethod
    def _render_pdf(html: str) -> bytes:
        """Render HTML to PDF using WeasyPrint."""
        try:
            from weasyprint import HTML
            pdf_bytes = HTML(string=html).write_pdf()
            logger.info("PDF rendered successfully", size=len(pdf_bytes))
            return pdf_bytes
        except ImportError:
            logger.warning("WeasyPrint not installed, returning HTML as bytes")
            return html.encode("utf-8")
        except Exception as exc:
            logger.error("PDF rendering failed", error=str(exc))
            return html.encode("utf-8")

    # ── Private: MinIO upload ──────────────────────────────────────────

    @staticmethod
    def _upload_to_minio(
        pdf_bytes: bytes,
        org_id: uuid.UUID,
        report_id: uuid.UUID,
    ) -> tuple[str, str]:
        """Upload PDF to MinIO and return (object_key, presigned_url)."""
        timestamp = datetime.now(timezone.utc).strftime("%Y%m%d_%H%M%S")
        object_name = f"incident-reports/{org_id}/{report_id}_{timestamp}.pdf"

        try:
            from app.utils.storage import get_storage_client
            storage = get_storage_client()
            storage.upload_bytes(pdf_bytes, object_name, "application/pdf")
            download_url = storage.get_presigned_url(object_name, expires=86400)
            return object_name, download_url
        except Exception as exc:
            logger.warning("MinIO upload failed, saving locally", error=str(exc))
            import os
            local_dir = f"/tmp/visionai/incident-reports/{org_id}"
            os.makedirs(local_dir, exist_ok=True)
            local_path = os.path.join(local_dir, f"{report_id}_{timestamp}.pdf")
            with open(local_path, "wb") as f:
                f.write(pdf_bytes)
            return local_path, local_path

    # ── Private: page count estimation ─────────────────────────────────

    @staticmethod
    def _estimate_page_count(file_size: int) -> int:
        """Rough estimation of PDF page count from file size."""
        # Average PDF page ~50KB for a text-heavy report
        return max(1, round(file_size / 50_000))

    # ── Private: template resolver ─────────────────────────────────────

    @staticmethod
    async def _get_template(
        db: AsyncSession,
        org_id: uuid.UUID,
        template_id: uuid.UUID | None,
    ) -> ReportTemplate | None:
        """Fetch a specific template or the org's default."""
        if template_id:
            result = await db.execute(
                select(ReportTemplate).where(
                    ReportTemplate.id == template_id,
                    ReportTemplate.org_id == org_id,
                )
            )
            return result.scalars().first()

        # Try default template
        result = await db.execute(
            select(ReportTemplate).where(
                ReportTemplate.org_id == org_id,
                ReportTemplate.is_default.is_(True),
            )
        )
        return result.scalars().first()
