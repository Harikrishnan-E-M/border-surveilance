"""Report generation service.

Provides report creation, listing, and rendering in PDF (WeasyPrint),
CSV, and Excel (openpyxl) formats. Reports aggregate data from alerts,
attendance, footfall, and other analytics modules.
"""

from __future__ import annotations

import csv
import io
import uuid
from datetime import date, datetime, timedelta, timezone
from typing import Any, Optional

import structlog
from sqlalchemy import func, select
from sqlalchemy.ext.asyncio import AsyncSession

from app.exceptions import NotFoundError, ValidationError
from app.models.alert import Alert
from app.models.analytics import FootfallRecord
from app.models.attendance import AttendanceLog
from app.models.camera import Camera
from app.models.person import Person

logger = structlog.stdlib.get_logger(__name__)


# ── Report Data Collection ───────────────────────────────────────────────


async def _collect_alert_summary(
    db: AsyncSession,
    org_id: uuid.UUID,
    start_date: datetime,
    end_date: datetime,
) -> dict[str, Any]:
    """Collect alert summary data for the report period."""
    total_q = select(func.count()).where(
        Alert.org_id == org_id,
        Alert.created_at >= start_date,
        Alert.created_at <= end_date,
    )
    total = (await db.execute(total_q)).scalar() or 0

    by_severity_q = (
        select(Alert.severity, func.count())
        .where(
            Alert.org_id == org_id,
            Alert.created_at >= start_date,
            Alert.created_at <= end_date,
        )
        .group_by(Alert.severity)
    )
    sev_result = await db.execute(by_severity_q)
    by_severity = {row[0].value: row[1] for row in sev_result.all()}

    by_type_q = (
        select(Alert.alert_type, func.count())
        .where(
            Alert.org_id == org_id,
            Alert.created_at >= start_date,
            Alert.created_at <= end_date,
        )
        .group_by(Alert.alert_type)
    )
    type_result = await db.execute(by_type_q)
    by_type = {row[0].value: row[1] for row in type_result.all()}

    by_camera_q = (
        select(Camera.name, func.count(Alert.id))
        .join(Camera, Alert.camera_id == Camera.id)
        .where(
            Alert.org_id == org_id,
            Alert.created_at >= start_date,
            Alert.created_at <= end_date,
        )
        .group_by(Camera.name)
        .order_by(func.count(Alert.id).desc())
        .limit(10)
    )
    cam_result = await db.execute(by_camera_q)
    by_camera = {row[0]: row[1] for row in cam_result.all()}

    return {
        "total_alerts": total,
        "by_severity": by_severity,
        "by_type": by_type,
        "top_cameras": by_camera,
    }


async def _collect_footfall_summary(
    db: AsyncSession,
    org_id: uuid.UUID,
    start_date: datetime,
    end_date: datetime,
) -> dict[str, Any]:
    """Collect footfall summary data for the report period."""
    total_entries_q = (
        select(func.coalesce(func.sum(FootfallRecord.entries_count), 0))
        .join(Camera, FootfallRecord.camera_id == Camera.id)
        .where(
            Camera.org_id == org_id,
            FootfallRecord.timestamp >= start_date,
            FootfallRecord.timestamp <= end_date,
        )
    )
    total_exits_q = (
        select(func.coalesce(func.sum(FootfallRecord.exits_count), 0))
        .join(Camera, FootfallRecord.camera_id == Camera.id)
        .where(
            Camera.org_id == org_id,
            FootfallRecord.timestamp >= start_date,
            FootfallRecord.timestamp <= end_date,
        )
    )

    total_entries = (await db.execute(total_entries_q)).scalar() or 0
    total_exits = (await db.execute(total_exits_q)).scalar() or 0

    return {
        "total_entries": int(total_entries),
        "total_exits": int(total_exits),
        "net_flow": int(total_entries) - int(total_exits),
    }


async def _collect_attendance_summary(
    db: AsyncSession,
    org_id: uuid.UUID,
    start_date: datetime,
    end_date: datetime,
) -> dict[str, Any]:
    """Collect attendance summary data for the report period."""
    total_q = select(func.count()).where(
        AttendanceLog.org_id == org_id,
        AttendanceLog.date >= start_date.date(),
        AttendanceLog.date <= end_date.date(),
    )
    total = (await db.execute(total_q)).scalar() or 0

    by_status_q = (
        select(AttendanceLog.status, func.count())
        .where(
            AttendanceLog.org_id == org_id,
            AttendanceLog.date >= start_date.date(),
            AttendanceLog.date <= end_date.date(),
        )
        .group_by(AttendanceLog.status)
    )
    status_result = await db.execute(by_status_q)
    by_status = {row[0].value: row[1] for row in status_result.all()}

    return {
        "total_records": total,
        "by_status": by_status,
    }


# ── Report Generation ───────────────────────────────────────────────────


async def generate_report(
    db: AsyncSession,
    org_id: uuid.UUID,
    report_type: str,
    start_date: datetime,
    end_date: datetime,
    format: str = "pdf",
    title: Optional[str] = None,
    filters: Optional[dict[str, Any]] = None,
) -> dict[str, Any]:
    """Generate a comprehensive report.

    Collects data from multiple subsystems and renders the output in
    the requested format (PDF, CSV, or Excel).

    Args:
        db: Async database session.
        org_id: Organization to generate report for.
        report_type: Type of report ('summary', 'alerts', 'attendance', 'footfall').
        start_date: Report period start.
        end_date: Report period end.
        format: Output format ('pdf', 'csv', 'excel').
        title: Optional custom report title.
        filters: Optional additional filters.

    Returns:
        Dict with report_id, title, format, content_bytes (base64), and metadata.

    Raises:
        ValidationError: If parameters are invalid.
    """
    if start_date >= end_date:
        raise ValidationError(
            message="start_date must be before end_date",
            code="INVALID_DATE_RANGE",
        )

    valid_types = ("summary", "alerts", "attendance", "footfall")
    if report_type not in valid_types:
        raise ValidationError(
            message=f"Invalid report type. Must be one of: {', '.join(valid_types)}",
            code="INVALID_REPORT_TYPE",
        )

    valid_formats = ("pdf", "csv", "excel")
    if format not in valid_formats:
        raise ValidationError(
            message=f"Invalid format. Must be one of: {', '.join(valid_formats)}",
            code="INVALID_FORMAT",
        )

    report_title = title or f"{report_type.title()} Report"
    report_id = str(uuid.uuid4())

    logger.info(
        "Generating report",
        report_id=report_id,
        report_type=report_type,
        format=format,
    )

    # Collect data
    data: dict[str, Any] = {
        "report_id": report_id,
        "title": report_title,
        "report_type": report_type,
        "period": {
            "start": start_date.isoformat(),
            "end": end_date.isoformat(),
        },
        "generated_at": datetime.now(timezone.utc).isoformat(),
    }

    if report_type in ("summary", "alerts"):
        data["alerts"] = await _collect_alert_summary(db, org_id, start_date, end_date)

    if report_type in ("summary", "footfall"):
        data["footfall"] = await _collect_footfall_summary(db, org_id, start_date, end_date)

    if report_type in ("summary", "attendance"):
        data["attendance"] = await _collect_attendance_summary(db, org_id, start_date, end_date)

    # Render output
    if format == "pdf":
        content_bytes = render_pdf_report(data)
    elif format == "csv":
        content_bytes = render_csv_report(data)
    elif format == "excel":
        content_bytes = render_excel_report(data)
    else:
        content_bytes = render_csv_report(data)

    import base64

    return {
        "report_id": report_id,
        "title": report_title,
        "report_type": report_type,
        "format": format,
        "content_base64": base64.b64encode(content_bytes).decode("utf-8"),
        "size_bytes": len(content_bytes),
        "generated_at": data["generated_at"],
        "period": data["period"],
    }


async def get_reports(
    db: AsyncSession,
    org_id: uuid.UUID,
    page: int = 1,
    page_size: int = 20,
) -> list[dict[str, Any]]:
    """List previously generated reports.

    Returns a list of report metadata entries. Actual report content
    would be stored in object storage (MinIO) in production.

    Args:
        db: Async database session.
        org_id: Organization filter.
        page: Page number.
        page_size: Items per page.

    Returns:
        List of report metadata dicts.
    """
    try:
        from app.dependencies import get_redis

        redis = await get_redis()
        key = f"reports:{org_id}"
        start = (page - 1) * page_size
        end = start + page_size - 1
        raw_reports = await redis.lrange(key, start, end)

        import json
        return [json.loads(r) for r in raw_reports]
    except Exception as exc:
        logger.debug("Failed to fetch reports from Redis", error=str(exc))
        return []


# ── PDF Rendering ────────────────────────────────────────────────────────


def render_pdf_report(data: dict[str, Any]) -> bytes:
    """Render a report as PDF using WeasyPrint.

    Falls back to a simple HTML-to-bytes representation if WeasyPrint
    is not installed.

    Args:
        data: Report data dictionary.

    Returns:
        PDF file bytes.
    """
    html = _build_report_html(data)

    try:
        from weasyprint import HTML

        pdf_bytes = HTML(string=html).write_pdf()
        logger.info("PDF report rendered", size=len(pdf_bytes))
        return pdf_bytes

    except ImportError:
        logger.warning("WeasyPrint not installed, returning HTML as bytes")
        return html.encode("utf-8")

    except Exception as exc:
        logger.error("PDF rendering failed", error=str(exc))
        return html.encode("utf-8")


def _build_report_html(data: dict[str, Any]) -> str:
    """Build an HTML report from collected data."""
    title = data.get("title", "VisionAI Report")
    period = data.get("period", {})
    generated_at = data.get("generated_at", "")

    sections_html = ""

    alerts = data.get("alerts")
    if alerts:
        severity_rows = "".join(
            f"<tr><td>{k}</td><td>{v}</td></tr>"
            for k, v in alerts.get("by_severity", {}).items()
        )
        type_rows = "".join(
            f"<tr><td>{k}</td><td>{v}</td></tr>"
            for k, v in alerts.get("by_type", {}).items()
        )
        sections_html += f"""
        <h2>Alert Summary</h2>
        <p>Total Alerts: <strong>{alerts.get('total_alerts', 0)}</strong></p>
        <h3>By Severity</h3>
        <table><tr><th>Severity</th><th>Count</th></tr>{severity_rows}</table>
        <h3>By Type</h3>
        <table><tr><th>Type</th><th>Count</th></tr>{type_rows}</table>
        """

    footfall = data.get("footfall")
    if footfall:
        sections_html += f"""
        <h2>Footfall Summary</h2>
        <p>Total Entries: <strong>{footfall.get('total_entries', 0)}</strong></p>
        <p>Total Exits: <strong>{footfall.get('total_exits', 0)}</strong></p>
        <p>Net Flow: <strong>{footfall.get('net_flow', 0)}</strong></p>
        """

    attendance = data.get("attendance")
    if attendance:
        status_rows = "".join(
            f"<tr><td>{k}</td><td>{v}</td></tr>"
            for k, v in attendance.get("by_status", {}).items()
        )
        sections_html += f"""
        <h2>Attendance Summary</h2>
        <p>Total Records: <strong>{attendance.get('total_records', 0)}</strong></p>
        <table><tr><th>Status</th><th>Count</th></tr>{status_rows}</table>
        """

    return f"""<!DOCTYPE html>
<html>
<head>
    <meta charset="utf-8">
    <title>{title}</title>
    <style>
        body {{ font-family: 'Helvetica Neue', Arial, sans-serif; margin: 40px; color: #333; }}
        h1 {{ color: #1a1a2e; border-bottom: 2px solid #16213e; padding-bottom: 10px; }}
        h2 {{ color: #16213e; margin-top: 30px; }}
        h3 {{ color: #0f3460; }}
        table {{ border-collapse: collapse; width: 100%; margin: 10px 0 20px 0; }}
        th, td {{ border: 1px solid #ddd; padding: 8px 12px; text-align: left; }}
        th {{ background-color: #16213e; color: white; }}
        tr:nth-child(even) {{ background-color: #f2f2f2; }}
        .meta {{ color: #666; font-size: 0.9em; }}
    </style>
</head>
<body>
    <h1>{title}</h1>
    <p class="meta">
        Period: {period.get('start', '')} to {period.get('end', '')}<br>
        Generated: {generated_at}
    </p>
    {sections_html}
    <hr>
    <p class="meta">Generated by VisionAI Platform</p>
</body>
</html>"""


# ── CSV Rendering ────────────────────────────────────────────────────────


def render_csv_report(data: dict[str, Any]) -> bytes:
    """Render a report as CSV bytes.

    Flattens the report data into a tabular format suitable for CSV.

    Args:
        data: Report data dictionary.

    Returns:
        CSV file bytes.
    """
    output = io.StringIO()
    writer = csv.writer(output)

    writer.writerow(["VisionAI Report"])
    writer.writerow(["Title", data.get("title", "")])
    writer.writerow(["Period", data.get("period", {}).get("start", ""), "to", data.get("period", {}).get("end", "")])
    writer.writerow(["Generated", data.get("generated_at", "")])
    writer.writerow([])

    alerts = data.get("alerts")
    if alerts:
        writer.writerow(["ALERT SUMMARY"])
        writer.writerow(["Total Alerts", alerts.get("total_alerts", 0)])
        writer.writerow([])
        writer.writerow(["Severity", "Count"])
        for sev, count in alerts.get("by_severity", {}).items():
            writer.writerow([sev, count])
        writer.writerow([])
        writer.writerow(["Alert Type", "Count"])
        for atype, count in alerts.get("by_type", {}).items():
            writer.writerow([atype, count])
        writer.writerow([])

    footfall = data.get("footfall")
    if footfall:
        writer.writerow(["FOOTFALL SUMMARY"])
        writer.writerow(["Total Entries", footfall.get("total_entries", 0)])
        writer.writerow(["Total Exits", footfall.get("total_exits", 0)])
        writer.writerow(["Net Flow", footfall.get("net_flow", 0)])
        writer.writerow([])

    attendance = data.get("attendance")
    if attendance:
        writer.writerow(["ATTENDANCE SUMMARY"])
        writer.writerow(["Total Records", attendance.get("total_records", 0)])
        writer.writerow(["Status", "Count"])
        for status, count in attendance.get("by_status", {}).items():
            writer.writerow([status, count])
        writer.writerow([])

    return output.getvalue().encode("utf-8")


# ── Excel Rendering ──────────────────────────────────────────────────────


def render_excel_report(data: dict[str, Any]) -> bytes:
    """Render a report as Excel (XLSX) bytes using openpyxl.

    Creates separate worksheets for each data section.

    Args:
        data: Report data dictionary.

    Returns:
        XLSX file bytes.
    """
    try:
        from openpyxl import Workbook
        from openpyxl.styles import Alignment, Font, PatternFill
    except ImportError:
        logger.warning("openpyxl not installed, falling back to CSV")
        return render_csv_report(data)

    wb = Workbook()

    header_font = Font(bold=True, size=11, color="FFFFFF")
    header_fill = PatternFill(start_color="16213E", end_color="16213E", fill_type="solid")
    title_font = Font(bold=True, size=14)

    # ── Overview Sheet ─────────────────────────────────────────────────
    ws = wb.active
    ws.title = "Overview"
    ws.merge_cells("A1:D1")
    ws["A1"] = data.get("title", "VisionAI Report")
    ws["A1"].font = title_font

    ws["A3"] = "Period"
    ws["B3"] = f"{data.get('period', {}).get('start', '')} to {data.get('period', {}).get('end', '')}"
    ws["A4"] = "Generated"
    ws["B4"] = data.get("generated_at", "")

    # ── Alerts Sheet ─────────────────────────────────────────────────
    alerts = data.get("alerts")
    if alerts:
        ws_alerts = wb.create_sheet("Alerts")
        ws_alerts["A1"] = "Alert Summary"
        ws_alerts["A1"].font = title_font

        ws_alerts["A3"] = "Total Alerts"
        ws_alerts["B3"] = alerts.get("total_alerts", 0)

        row = 5
        headers = ["Severity", "Count"]
        for col, h in enumerate(headers, 1):
            cell = ws_alerts.cell(row=row, column=col, value=h)
            cell.font = header_font
            cell.fill = header_fill

        for sev, count in alerts.get("by_severity", {}).items():
            row += 1
            ws_alerts.cell(row=row, column=1, value=sev)
            ws_alerts.cell(row=row, column=2, value=count)

        row += 2
        headers = ["Alert Type", "Count"]
        for col, h in enumerate(headers, 1):
            cell = ws_alerts.cell(row=row, column=col, value=h)
            cell.font = header_font
            cell.fill = header_fill

        for atype, count in alerts.get("by_type", {}).items():
            row += 1
            ws_alerts.cell(row=row, column=1, value=atype)
            ws_alerts.cell(row=row, column=2, value=count)

    # ── Footfall Sheet ───────────────────────────────────────────────
    footfall = data.get("footfall")
    if footfall:
        ws_ff = wb.create_sheet("Footfall")
        ws_ff["A1"] = "Footfall Summary"
        ws_ff["A1"].font = title_font
        ws_ff["A3"] = "Total Entries"
        ws_ff["B3"] = footfall.get("total_entries", 0)
        ws_ff["A4"] = "Total Exits"
        ws_ff["B4"] = footfall.get("total_exits", 0)
        ws_ff["A5"] = "Net Flow"
        ws_ff["B5"] = footfall.get("net_flow", 0)

    # ── Attendance Sheet ─────────────────────────────────────────────
    attendance = data.get("attendance")
    if attendance:
        ws_att = wb.create_sheet("Attendance")
        ws_att["A1"] = "Attendance Summary"
        ws_att["A1"].font = title_font
        ws_att["A3"] = "Total Records"
        ws_att["B3"] = attendance.get("total_records", 0)

        row = 5
        headers = ["Status", "Count"]
        for col, h in enumerate(headers, 1):
            cell = ws_att.cell(row=row, column=col, value=h)
            cell.font = header_font
            cell.fill = header_fill

        for status, count in attendance.get("by_status", {}).items():
            row += 1
            ws_att.cell(row=row, column=1, value=status)
            ws_att.cell(row=row, column=2, value=count)

    output = io.BytesIO()
    wb.save(output)
    return output.getvalue()
