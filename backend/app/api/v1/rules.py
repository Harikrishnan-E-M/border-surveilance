"""
Rule management API endpoints.

Provides CRUD for detection/analytics rules, including rule type cataloging,
toggling, and filtering by camera, zone, or type.
"""

from __future__ import annotations

import math
import uuid
from datetime import datetime, timezone
from typing import Optional

import structlog
from fastapi import APIRouter, Depends, HTTPException, Query, status
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
from app.models.camera import Camera
from app.models.rule import Rule, RuleSeverity, RuleType
from app.models.user import User, UserRole
from app.models.zone import Zone
from app.schemas.common import ErrorResponse, SuccessResponse

logger = structlog.stdlib.get_logger(__name__)

router = APIRouter()


# ---------------------------------------------------------------------------
# Pydantic schemas (endpoint-local)
# ---------------------------------------------------------------------------


class RuleCreate(BaseModel):
    camera_id: uuid.UUID
    zone_id: uuid.UUID | None = None
    rule_type: str = Field(..., description="Detection rule type")
    parameters: dict | None = None
    severity: str = Field(default="medium", description="Alert severity: critical, high, medium, low, info")
    alert_channels: dict | None = None
    cooldown_seconds: int = Field(default=300, ge=0)
    schedule_cron: str | None = None


class RuleUpdate(BaseModel):
    zone_id: uuid.UUID | None = None
    rule_type: str | None = None
    parameters: dict | None = None
    severity: str | None = None
    alert_channels: dict | None = None
    cooldown_seconds: int | None = Field(default=None, ge=0)
    schedule_cron: str | None = None
    is_active: bool | None = None


class RuleResponse(BaseModel):
    id: uuid.UUID
    org_id: uuid.UUID
    camera_id: uuid.UUID
    zone_id: uuid.UUID | None = None
    rule_type: str
    parameters: dict | None = None
    severity: str
    alert_channels: dict | None = None
    is_active: bool
    cooldown_seconds: int
    schedule_cron: str | None = None
    created_at: datetime
    updated_at: datetime

    class Config:
        from_attributes = True


# ---------------------------------------------------------------------------
# Rule type catalog with descriptions and parameter schemas
# ---------------------------------------------------------------------------

RULE_TYPE_CATALOG = {
    "intrusion_detection": {
        "name": "Intrusion Detection",
        "description": "Detects unauthorized persons entering a restricted zone.",
        "parameters_schema": {
            "sensitivity": {"type": "float", "default": 0.7, "min": 0.0, "max": 1.0},
            "min_duration_seconds": {"type": "int", "default": 2},
        },
    },
    "loitering": {
        "name": "Loitering Detection",
        "description": "Detects persons lingering in an area beyond a time threshold.",
        "parameters_schema": {
            "max_dwell_seconds": {"type": "int", "default": 60},
            "sensitivity": {"type": "float", "default": 0.5, "min": 0.0, "max": 1.0},
        },
    },
    "crowd_formation": {
        "name": "Crowd Formation",
        "description": "Alerts when a group of people exceeds a threshold count in a zone.",
        "parameters_schema": {
            "max_persons": {"type": "int", "default": 10},
            "duration_seconds": {"type": "int", "default": 5},
        },
    },
    "object_left_behind": {
        "name": "Object Left Behind",
        "description": "Detects stationary objects that appear and remain for a set time.",
        "parameters_schema": {
            "min_stationary_seconds": {"type": "int", "default": 30},
        },
    },
    "object_removed": {
        "name": "Object Removed",
        "description": "Detects when a tracked object disappears from its expected location.",
        "parameters_schema": {
            "reference_objects": {"type": "list", "description": "List of object labels to monitor"},
        },
    },
    "wrong_direction": {
        "name": "Wrong Direction",
        "description": "Detects movement against the expected flow direction.",
        "parameters_schema": {
            "expected_direction": {"type": "string", "enum": ["left", "right", "up", "down"]},
        },
    },
    "line_crossing": {
        "name": "Line Crossing",
        "description": "Counts or alerts when objects cross a virtual line.",
        "parameters_schema": {
            "line_points": {"type": "list", "description": "Two [x,y] points defining the line"},
            "direction": {"type": "string", "enum": ["both", "left_to_right", "right_to_left"]},
        },
    },
    "face_recognized": {
        "name": "Face Recognized",
        "description": "Triggers when a known enrolled face is detected.",
        "parameters_schema": {
            "person_types": {"type": "list", "default": ["vip", "blacklisted"]},
            "min_confidence": {"type": "float", "default": 0.8},
        },
    },
    "face_unknown": {
        "name": "Unknown Face",
        "description": "Alerts when an unrecognized face is detected in a restricted area.",
        "parameters_schema": {
            "min_confidence": {"type": "float", "default": 0.6},
        },
    },
    "face_blacklisted": {
        "name": "Blacklisted Face",
        "description": "Triggers an alert when a blacklisted person is recognized.",
        "parameters_schema": {
            "min_confidence": {"type": "float", "default": 0.85},
        },
    },
    "anpr_blacklisted": {
        "name": "Blacklisted Vehicle",
        "description": "Alerts when a blacklisted vehicle plate is detected.",
        "parameters_schema": {},
    },
    "anpr_unknown": {
        "name": "Unknown Vehicle",
        "description": "Alerts when an unregistered plate is detected at a controlled entry.",
        "parameters_schema": {},
    },
    "ppe_violation": {
        "name": "PPE Violation",
        "description": "Detects missing PPE (helmet, vest, gloves, etc.) in a designated zone.",
        "parameters_schema": {
            "required_ppe": {"type": "list", "default": ["helmet", "vest"]},
            "min_confidence": {"type": "float", "default": 0.6},
        },
    },
    "fire_smoke": {
        "name": "Fire & Smoke Detection",
        "description": "Detects fire or smoke in the camera view.",
        "parameters_schema": {
            "min_confidence": {"type": "float", "default": 0.7},
        },
    },
    "fall_detection": {
        "name": "Fall Detection",
        "description": "Detects when a person falls down.",
        "parameters_schema": {
            "min_confidence": {"type": "float", "default": 0.6},
            "confirm_seconds": {"type": "int", "default": 3},
        },
    },
    "violence_detection": {
        "name": "Violence Detection",
        "description": "Detects aggressive behaviour or fights.",
        "parameters_schema": {
            "min_confidence": {"type": "float", "default": 0.7},
        },
    },
    "occupancy_threshold": {
        "name": "Occupancy Threshold",
        "description": "Alerts when zone occupancy exceeds or drops below a threshold.",
        "parameters_schema": {
            "max_occupancy": {"type": "int", "default": 50},
            "min_occupancy": {"type": "int", "default": 0},
        },
    },
    "camera_tamper": {
        "name": "Camera Tamper",
        "description": "Detects camera obstruction, defocus, or sudden view change.",
        "parameters_schema": {
            "sensitivity": {"type": "float", "default": 0.8, "min": 0.0, "max": 1.0},
        },
    },
    "no_entry_zone": {
        "name": "No Entry Zone",
        "description": "Alerts when any person enters a forbidden area.",
        "parameters_schema": {},
    },
    "tailgating": {
        "name": "Tailgating Detection",
        "description": "Detects multiple persons passing through a gate on a single access event.",
        "parameters_schema": {
            "max_gap_seconds": {"type": "float", "default": 2.0},
        },
    },
    "speed_violation": {
        "name": "Speed Violation",
        "description": "Detects vehicles exceeding a speed limit.",
        "parameters_schema": {
            "max_speed_kmh": {"type": "float", "default": 30.0},
        },
    },
    "illegal_parking": {
        "name": "Illegal Parking",
        "description": "Detects vehicles parked in no-parking zones beyond a threshold duration.",
        "parameters_schema": {
            "max_duration_seconds": {"type": "int", "default": 120},
        },
    },
}


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


def _require_manager(user: User) -> None:
    allowed = {UserRole.SUPER_ADMIN, UserRole.ORG_ADMIN, UserRole.MANAGER}
    if user.role not in allowed:
        raise AuthorizationError(message="Manager role or higher is required.")


def _rule_to_response(rule: Rule) -> dict:
    return RuleResponse(
        id=rule.id,
        org_id=rule.org_id,
        camera_id=rule.camera_id,
        zone_id=rule.zone_id,
        rule_type=rule.rule_type.value if isinstance(rule.rule_type, RuleType) else rule.rule_type,
        parameters=rule.parameters,
        severity=rule.severity.value if isinstance(rule.severity, RuleSeverity) else rule.severity,
        alert_channels=rule.alert_channels,
        is_active=rule.is_active,
        cooldown_seconds=rule.cooldown_seconds,
        schedule_cron=rule.schedule_cron,
        created_at=rule.created_at,
        updated_at=rule.updated_at,
    ).model_dump(mode="json")


# ---------------------------------------------------------------------------
# GET / - List rules
# ---------------------------------------------------------------------------


@router.get(
    "/",
    response_model=SuccessResponse,
    summary="List rules (paginated, filterable)",
)
async def list_rules(
    camera_id: uuid.UUID | None = Query(None),
    zone_id: uuid.UUID | None = Query(None),
    rule_type: str | None = Query(None),
    is_active: bool | None = Query(None),
    page: int = Query(1, ge=1),
    page_size: int = Query(20, ge=1, le=100),
    user: User = Depends(_get_current_user),
    db: AsyncSession = Depends(get_db_session),
) -> dict:
    """Return a paginated list of rules for the user's organization."""
    query = select(Rule).where(Rule.org_id == user.org_id)

    if camera_id is not None:
        query = query.where(Rule.camera_id == camera_id)
    if zone_id is not None:
        query = query.where(Rule.zone_id == zone_id)
    if rule_type is not None:
        try:
            query = query.where(Rule.rule_type == RuleType(rule_type))
        except ValueError:
            raise ValidationError(message=f"Invalid rule_type: {rule_type}")
    if is_active is not None:
        query = query.where(Rule.is_active == is_active)

    count_q = select(func.count()).select_from(query.subquery())
    total = (await db.execute(count_q)).scalar() or 0

    query = query.order_by(Rule.created_at.desc()).offset((page - 1) * page_size).limit(page_size)
    result = await db.execute(query)
    rules = result.scalars().all()

    return {
        "status": "success",
        "data": [_rule_to_response(r) for r in rules],
        "meta": {
            "page": page,
            "page_size": page_size,
            "total": total,
            "total_pages": math.ceil(total / page_size) if page_size else 0,
        },
    }


# ---------------------------------------------------------------------------
# POST / - Create rule
# ---------------------------------------------------------------------------


@router.post(
    "/",
    response_model=SuccessResponse,
    status_code=status.HTTP_201_CREATED,
    summary="Create a new rule",
    responses={403: {"model": ErrorResponse}},
)
async def create_rule(
    body: RuleCreate,
    user: User = Depends(_get_current_user),
    db: AsyncSession = Depends(get_db_session),
) -> dict:
    """Create a new detection rule. Requires manager role or above."""
    _require_manager(user)

    # Verify camera belongs to org
    cam_result = await db.execute(
        select(Camera).where(Camera.id == body.camera_id, Camera.org_id == user.org_id)
    )
    if not cam_result.scalars().first():
        raise NotFoundError(resource="Camera", identifier=str(body.camera_id))

    # Verify zone belongs to camera if specified
    if body.zone_id:
        zone_result = await db.execute(
            select(Zone).where(Zone.id == body.zone_id, Zone.camera_id == body.camera_id)
        )
        if not zone_result.scalars().first():
            raise NotFoundError(resource="Zone", identifier=str(body.zone_id))

    # Validate rule type
    try:
        rule_type = RuleType(body.rule_type)
    except ValueError:
        valid = [t.value for t in RuleType]
        raise ValidationError(message=f"Invalid rule_type '{body.rule_type}'. Must be one of: {', '.join(valid)}")

    # Validate severity
    try:
        severity = RuleSeverity(body.severity)
    except ValueError:
        valid = [s.value for s in RuleSeverity]
        raise ValidationError(message=f"Invalid severity '{body.severity}'. Must be one of: {', '.join(valid)}")

    rule = Rule(
        org_id=user.org_id,
        camera_id=body.camera_id,
        zone_id=body.zone_id,
        rule_type=rule_type,
        parameters=body.parameters,
        severity=severity,
        alert_channels=body.alert_channels,
        cooldown_seconds=body.cooldown_seconds,
        schedule_cron=body.schedule_cron,
    )
    db.add(rule)
    await db.flush()

    logger.info("Rule created", rule_id=str(rule.id), rule_type=rule.rule_type.value)

    return {
        "status": "success",
        "data": _rule_to_response(rule),
        "message": "Rule created successfully.",
    }


# ---------------------------------------------------------------------------
# GET /types - List all available rule types
# ---------------------------------------------------------------------------


@router.get(
    "/types",
    response_model=SuccessResponse,
    summary="List available rule types with descriptions",
)
async def list_rule_types(
    user: User = Depends(_get_current_user),
) -> dict:
    """Return all supported detection rule types with descriptions and parameter schemas."""
    return {
        "status": "success",
        "data": RULE_TYPE_CATALOG,
    }


# ---------------------------------------------------------------------------
# GET /{rule_id} - Get rule details
# ---------------------------------------------------------------------------


@router.get(
    "/{rule_id}",
    response_model=SuccessResponse,
    summary="Get rule details",
    responses={404: {"model": ErrorResponse}},
)
async def get_rule(
    rule_id: uuid.UUID,
    user: User = Depends(_get_current_user),
    db: AsyncSession = Depends(get_db_session),
) -> dict:
    """Retrieve a single rule by ID."""
    result = await db.execute(
        select(Rule).where(Rule.id == rule_id, Rule.org_id == user.org_id)
    )
    rule = result.scalars().first()
    if not rule:
        raise NotFoundError(resource="Rule", identifier=str(rule_id))

    return {
        "status": "success",
        "data": _rule_to_response(rule),
    }


# ---------------------------------------------------------------------------
# PUT /{rule_id} - Update rule
# ---------------------------------------------------------------------------


@router.put(
    "/{rule_id}",
    response_model=SuccessResponse,
    summary="Update rule",
    responses={403: {"model": ErrorResponse}, 404: {"model": ErrorResponse}},
)
async def update_rule(
    rule_id: uuid.UUID,
    body: RuleUpdate,
    user: User = Depends(_get_current_user),
    db: AsyncSession = Depends(get_db_session),
) -> dict:
    """Update rule fields. Requires manager role or above."""
    _require_manager(user)

    result = await db.execute(
        select(Rule).where(Rule.id == rule_id, Rule.org_id == user.org_id)
    )
    rule = result.scalars().first()
    if not rule:
        raise NotFoundError(resource="Rule", identifier=str(rule_id))

    update_data = body.model_dump(exclude_unset=True)

    if "rule_type" in update_data:
        try:
            update_data["rule_type"] = RuleType(update_data["rule_type"])
        except ValueError:
            raise ValidationError(message=f"Invalid rule_type: {update_data['rule_type']}")

    if "severity" in update_data:
        try:
            update_data["severity"] = RuleSeverity(update_data["severity"])
        except ValueError:
            raise ValidationError(message=f"Invalid severity: {update_data['severity']}")

    if "zone_id" in update_data and update_data["zone_id"] is not None:
        zone_result = await db.execute(
            select(Zone).where(Zone.id == update_data["zone_id"], Zone.camera_id == rule.camera_id)
        )
        if not zone_result.scalars().first():
            raise NotFoundError(resource="Zone", identifier=str(update_data["zone_id"]))

    for field, value in update_data.items():
        setattr(rule, field, value)

    db.add(rule)
    await db.flush()

    logger.info("Rule updated", rule_id=str(rule.id))

    return {
        "status": "success",
        "data": _rule_to_response(rule),
        "message": "Rule updated successfully.",
    }


# ---------------------------------------------------------------------------
# DELETE /{rule_id} - Delete rule
# ---------------------------------------------------------------------------


@router.delete(
    "/{rule_id}",
    response_model=SuccessResponse,
    summary="Delete rule",
    responses={403: {"model": ErrorResponse}, 404: {"model": ErrorResponse}},
)
async def delete_rule(
    rule_id: uuid.UUID,
    user: User = Depends(_get_current_user),
    db: AsyncSession = Depends(get_db_session),
) -> dict:
    """Delete a rule. Requires manager role or above."""
    _require_manager(user)

    result = await db.execute(
        select(Rule).where(Rule.id == rule_id, Rule.org_id == user.org_id)
    )
    rule = result.scalars().first()
    if not rule:
        raise NotFoundError(resource="Rule", identifier=str(rule_id))

    await db.delete(rule)
    await db.flush()

    logger.info("Rule deleted", rule_id=str(rule_id))

    return {
        "status": "success",
        "data": None,
        "message": "Rule deleted successfully.",
    }


# ---------------------------------------------------------------------------
# PUT /{rule_id}/toggle - Enable/disable rule
# ---------------------------------------------------------------------------


@router.put(
    "/{rule_id}/toggle",
    response_model=SuccessResponse,
    summary="Toggle rule active status",
)
async def toggle_rule(
    rule_id: uuid.UUID,
    user: User = Depends(_get_current_user),
    db: AsyncSession = Depends(get_db_session),
) -> dict:
    """Toggle a rule's is_active status. Any authenticated user can toggle."""
    result = await db.execute(
        select(Rule).where(Rule.id == rule_id, Rule.org_id == user.org_id)
    )
    rule = result.scalars().first()
    if not rule:
        raise NotFoundError(resource="Rule", identifier=str(rule_id))

    rule.is_active = not rule.is_active
    db.add(rule)
    await db.flush()

    state = "enabled" if rule.is_active else "disabled"
    logger.info("Rule toggled", rule_id=str(rule.id), is_active=rule.is_active)

    return {
        "status": "success",
        "data": _rule_to_response(rule),
        "message": f"Rule {state} successfully.",
    }
