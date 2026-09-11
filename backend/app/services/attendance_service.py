"""Attendance tracking service.

Provides attendance logging from face recognition events, daily status
computation, attendance report generation, and CSV/Excel export.
"""

from __future__ import annotations

import csv
import io
import uuid
from datetime import date, datetime, time, timedelta, timezone
from typing import Any, Optional

import structlog
from sqlalchemy import and_, func, select
from sqlalchemy.ext.asyncio import AsyncSession

from app.exceptions import NotFoundError, ValidationError
from app.models.attendance import AttendanceLog, AttendanceStatus
from app.models.person import Person

logger = structlog.stdlib.get_logger(__name__)

DEFAULT_WORK_START_HOUR = 9
DEFAULT_WORK_START_MINUTE = 0
DEFAULT_LATE_THRESHOLD_MINUTES = 15
DEFAULT_HALF_DAY_HOURS = 4


# ── Attendance Logging ───────────────────────────────────────────────────


async def log_attendance(
    db: AsyncSession,
    org_id: uuid.UUID,
    person_id: uuid.UUID,
    camera_id: uuid.UUID,
    timestamp: datetime,
) -> AttendanceLog:
    """Log an attendance event (first-seen / last-seen update).

    If an attendance record already exists for this person on the given
    date, the last_seen and duration are updated. Otherwise a new record
    is created.

    Args:
        db: Async database session.
        org_id: Organization ID.
        person_id: Person who was detected.
        camera_id: Camera that captured the detection.
        timestamp: Detection timestamp.

    Returns:
        The created or updated AttendanceLog instance.
    """
    event_date = timestamp.date()

    result = await db.execute(
        select(AttendanceLog).where(
            AttendanceLog.person_id == person_id,
            AttendanceLog.org_id == org_id,
            AttendanceLog.date == event_date,
        )
    )
    log = result.scalar_one_or_none()

    if log is not None:
        log.last_seen = timestamp
        log.camera_id = camera_id

        if log.first_seen:
            duration = (timestamp - log.first_seen).total_seconds()
            log.total_duration_seconds = int(duration)

        log.status = compute_attendance_status(log.first_seen, log.last_seen)
        await db.flush()

        logger.debug(
            "Attendance updated",
            person_id=str(person_id),
            date=str(event_date),
            duration=log.total_duration_seconds,
        )
        return log

    new_log = AttendanceLog(
        id=uuid.uuid4(),
        person_id=person_id,
        camera_id=camera_id,
        org_id=org_id,
        date=event_date,
        first_seen=timestamp,
        last_seen=timestamp,
        total_duration_seconds=0,
        status=compute_attendance_status(timestamp, timestamp),
    )
    db.add(new_log)
    await db.flush()

    logger.info(
        "Attendance logged",
        person_id=str(person_id),
        date=str(event_date),
        first_seen=timestamp.isoformat(),
    )
    return new_log


# ── Status Computation ───────────────────────────────────────────────────


def compute_attendance_status(
    first_seen: Optional[datetime],
    last_seen: Optional[datetime],
    work_start_hour: int = DEFAULT_WORK_START_HOUR,
    work_start_minute: int = DEFAULT_WORK_START_MINUTE,
    late_threshold_minutes: int = DEFAULT_LATE_THRESHOLD_MINUTES,
    half_day_hours: int = DEFAULT_HALF_DAY_HOURS,
) -> AttendanceStatus:
    """Compute the attendance status from first/last seen times.

    Rules:
    - ABSENT: No sightings (first_seen is None).
    - LATE: First seen after work_start + late_threshold.
    - HALF_DAY: Total duration less than half_day_hours.
    - PRESENT: Otherwise.

    Args:
        first_seen: Earliest detection timestamp.
        last_seen: Latest detection timestamp.
        work_start_hour: Expected work start hour (24h format).
        work_start_minute: Expected work start minute.
        late_threshold_minutes: Minutes after start before marking late.
        half_day_hours: Minimum hours for a full day.

    Returns:
        Computed AttendanceStatus.
    """
    if first_seen is None:
        return AttendanceStatus.ABSENT

    work_start = first_seen.replace(
        hour=work_start_hour,
        minute=work_start_minute,
        second=0,
        microsecond=0,
    )
    late_cutoff = work_start + timedelta(minutes=late_threshold_minutes)

    if last_seen and first_seen:
        total_hours = (last_seen - first_seen).total_seconds() / 3600
        if total_hours < half_day_hours:
            return AttendanceStatus.HALF_DAY

    if first_seen > late_cutoff:
        return AttendanceStatus.LATE

    return AttendanceStatus.PRESENT


# ── Attendance Queries ───────────────────────────────────────────────────


async def get_daily_attendance(
    db: AsyncSession,
    org_id: uuid.UUID,
    target_date: date,
    department: Optional[str] = None,
    page: int = 1,
    page_size: int = 50,
) -> tuple[list[dict[str, Any]], int]:
    """Get daily attendance records for an organization.

    Args:
        db: Async database session.
        org_id: Organization filter.
        target_date: Date to retrieve attendance for.
        department: Optional department filter.
        page: Page number.
        page_size: Items per page.

    Returns:
        Tuple of (attendance_records as dicts, total_count).
    """
    query = (
        select(AttendanceLog, Person)
        .join(Person, AttendanceLog.person_id == Person.id)
        .where(
            AttendanceLog.org_id == org_id,
            AttendanceLog.date == target_date,
        )
    )

    if department:
        query = query.where(Person.department == department)

    count_q = select(func.count()).select_from(query.subquery())
    total_result = await db.execute(count_q)
    total = total_result.scalar() or 0

    offset = (max(1, page) - 1) * page_size
    query = query.order_by(Person.full_name.asc()).offset(offset).limit(page_size)
    result = await db.execute(query)
    rows = result.all()

    records = []
    for log, person in rows:
        records.append({
            "person_id": str(person.id),
            "person_name": person.full_name,
            "department": person.department,
            "date": str(log.date),
            "first_seen": log.first_seen.isoformat() if log.first_seen else None,
            "last_seen": log.last_seen.isoformat() if log.last_seen else None,
            "total_duration_seconds": log.total_duration_seconds,
            "status": log.status.value,
        })

    return records, total


async def get_attendance_report(
    db: AsyncSession,
    org_id: uuid.UUID,
    start_date: date,
    end_date: date,
    department: Optional[str] = None,
    person_ids: Optional[list[uuid.UUID]] = None,
) -> list[dict[str, Any]]:
    """Generate a detailed attendance report for a date range.

    Aggregates attendance data per person across the given period
    with counts of present, late, absent, and half-day occurrences.

    Args:
        db: Async database session.
        org_id: Organization filter.
        start_date: Report start date (inclusive).
        end_date: Report end date (inclusive).
        department: Optional department filter.
        person_ids: Optional filter to specific persons.

    Returns:
        List of report dicts per person.
    """
    if start_date > end_date:
        raise ValidationError(
            message="start_date must be before or equal to end_date",
            code="INVALID_DATE_RANGE",
        )

    person_query = select(Person).where(
        Person.org_id == org_id,
        Person.is_active.is_(True),
    )
    if department:
        person_query = person_query.where(Person.department == department)
    if person_ids:
        person_query = person_query.where(Person.id.in_(person_ids))

    person_result = await db.execute(person_query)
    persons = list(person_result.scalars().all())

    total_days = (end_date - start_date).days + 1
    report: list[dict[str, Any]] = []

    for person in persons:
        log_query = select(AttendanceLog).where(
            AttendanceLog.person_id == person.id,
            AttendanceLog.org_id == org_id,
            AttendanceLog.date >= start_date,
            AttendanceLog.date <= end_date,
        )
        log_result = await db.execute(log_query)
        logs = list(log_result.scalars().all())

        status_counts = {
            "present": 0,
            "late": 0,
            "absent": 0,
            "half_day": 0,
        }

        total_duration = 0
        logged_dates = set()

        for log in logs:
            status_counts[log.status.value] = status_counts.get(log.status.value, 0) + 1
            total_duration += log.total_duration_seconds or 0
            logged_dates.add(log.date)

        status_counts["absent"] = total_days - len(logged_dates)

        avg_duration = total_duration / len(logged_dates) if logged_dates else 0

        report.append({
            "person_id": str(person.id),
            "person_name": person.full_name,
            "employee_id": person.employee_id,
            "department": person.department,
            "total_days": total_days,
            "days_present": len(logged_dates),
            "days_absent": status_counts["absent"],
            "days_late": status_counts["late"],
            "days_half_day": status_counts["half_day"],
            "total_duration_seconds": total_duration,
            "avg_daily_duration_seconds": round(avg_duration),
            "attendance_percentage": round(len(logged_dates) / total_days * 100, 1) if total_days > 0 else 0,
        })

    return report


# ── Export ───────────────────────────────────────────────────────────────


async def export_attendance(
    db: AsyncSession,
    org_id: uuid.UUID,
    start_date: date,
    end_date: date,
    format: str = "csv",
    department: Optional[str] = None,
    person_ids: Optional[list[uuid.UUID]] = None,
) -> bytes:
    """Export attendance report as CSV or Excel bytes.

    Args:
        db: Async database session.
        org_id: Organization filter.
        start_date: Report start date.
        end_date: Report end date.
        format: Export format ('csv' or 'excel').
        department: Optional department filter.
        person_ids: Optional person filter.

    Returns:
        Raw bytes of the exported file (CSV or XLSX).

    Raises:
        ValidationError: If the format is unsupported.
    """
    report = await get_attendance_report(
        db, org_id, start_date, end_date, department, person_ids
    )

    if format == "csv":
        return _render_csv(report, start_date, end_date)
    elif format == "excel":
        return _render_excel(report, start_date, end_date)
    else:
        raise ValidationError(
            message=f"Unsupported export format: {format}. Use 'csv' or 'excel'.",
            code="UNSUPPORTED_FORMAT",
        )


def _render_csv(
    report: list[dict[str, Any]],
    start_date: date,
    end_date: date,
) -> bytes:
    """Render an attendance report as CSV bytes."""
    output = io.StringIO()
    fieldnames = [
        "person_name",
        "employee_id",
        "department",
        "total_days",
        "days_present",
        "days_absent",
        "days_late",
        "days_half_day",
        "attendance_percentage",
        "avg_daily_duration_hours",
    ]
    writer = csv.DictWriter(output, fieldnames=fieldnames)

    writer.writerow({f: f.replace("_", " ").title() for f in fieldnames})

    for row in report:
        writer.writerow({
            "person_name": row["person_name"],
            "employee_id": row.get("employee_id", ""),
            "department": row.get("department", ""),
            "total_days": row["total_days"],
            "days_present": row["days_present"],
            "days_absent": row["days_absent"],
            "days_late": row["days_late"],
            "days_half_day": row["days_half_day"],
            "attendance_percentage": row["attendance_percentage"],
            "avg_daily_duration_hours": round(row["avg_daily_duration_seconds"] / 3600, 1),
        })

    return output.getvalue().encode("utf-8")


def _render_excel(
    report: list[dict[str, Any]],
    start_date: date,
    end_date: date,
) -> bytes:
    """Render an attendance report as Excel (XLSX) bytes."""
    try:
        from openpyxl import Workbook
        from openpyxl.styles import Alignment, Font
    except ImportError:
        logger.warning("openpyxl not installed, falling back to CSV")
        return _render_csv(report, start_date, end_date)

    wb = Workbook()
    ws = wb.active
    ws.title = "Attendance Report"

    header_font = Font(bold=True, size=12)
    title_font = Font(bold=True, size=14)

    ws.merge_cells("A1:J1")
    ws["A1"] = f"Attendance Report: {start_date} to {end_date}"
    ws["A1"].font = title_font
    ws["A1"].alignment = Alignment(horizontal="center")

    headers = [
        "Name",
        "Employee ID",
        "Department",
        "Total Days",
        "Present",
        "Absent",
        "Late",
        "Half Day",
        "Attendance %",
        "Avg Hours/Day",
    ]
    for col, header in enumerate(headers, start=1):
        cell = ws.cell(row=3, column=col, value=header)
        cell.font = header_font

    for row_idx, row in enumerate(report, start=4):
        ws.cell(row=row_idx, column=1, value=row["person_name"])
        ws.cell(row=row_idx, column=2, value=row.get("employee_id", ""))
        ws.cell(row=row_idx, column=3, value=row.get("department", ""))
        ws.cell(row=row_idx, column=4, value=row["total_days"])
        ws.cell(row=row_idx, column=5, value=row["days_present"])
        ws.cell(row=row_idx, column=6, value=row["days_absent"])
        ws.cell(row=row_idx, column=7, value=row["days_late"])
        ws.cell(row=row_idx, column=8, value=row["days_half_day"])
        ws.cell(row=row_idx, column=9, value=row["attendance_percentage"])
        ws.cell(row=row_idx, column=10, value=round(row["avg_daily_duration_seconds"] / 3600, 1))

    for col in range(1, 11):
        ws.column_dimensions[ws.cell(row=3, column=col).column_letter].width = 15

    output = io.BytesIO()
    wb.save(output)
    return output.getvalue()
