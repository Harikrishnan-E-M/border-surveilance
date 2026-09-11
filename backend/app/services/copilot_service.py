"""AI Surveillance Copilot Service.

Provides a conversational AI interface powered by the Anthropic Claude API
with tool-use capabilities for querying surveillance data.  Conversations
are persisted in Redis with a configurable TTL.

Usage::

    from app.services.copilot_service import CopilotService

    service = CopilotService()
    response = await service.chat(db, org_id, user_id, "How many alerts today?")
"""

from __future__ import annotations

import json
import uuid
from datetime import datetime, timedelta, timezone
from typing import Any, Optional

import anthropic
import structlog
from sqlalchemy import and_, case, distinct, func, or_, select
from sqlalchemy.ext.asyncio import AsyncSession

from app.config import get_settings
from app.models.alert import Alert, AlertStatus
from app.models.analytics import DwellRecord, FootfallRecord, HeatmapRecord
from app.models.camera import Camera, CameraHealth, CameraHealthStatus
from app.models.face import FaceEnrollment, FaceEvent
from app.models.organization import Organization
from app.models.person import Person, PersonType
from app.models.recording import Recording
from app.models.rule import Rule, RuleSeverity, RuleType
from app.models.user import User
from app.models.vehicle import Vehicle, VehicleEvent, VehicleLog

logger = structlog.stdlib.get_logger(__name__)

# Redis key prefixes
_CONV_PREFIX = "copilot:conv:"
_CONV_LIST_PREFIX = "copilot:convlist:"
_CONV_TTL = 60 * 60 * 24 * 7  # 7 days

# Maximum number of conversation turns kept in context
_MAX_HISTORY_TURNS = 40


def _utcnow_naive() -> datetime:
    """Return current UTC time as a naive datetime (no tzinfo).

    This is required because the database uses TIMESTAMP WITHOUT TIME ZONE
    columns, so all comparisons must use naive datetimes.
    """
    return datetime.now(timezone.utc).replace(tzinfo=None)

# Claude model to use
_CLAUDE_MODEL = "claude-sonnet-4-20250514"

# Tool definitions exposed to Claude
_TOOL_DEFINITIONS: list[dict[str, Any]] = [
    {
        "name": "query_alerts",
        "description": (
            "Query alert records for the organization. "
            "Can filter by severity, type, status, camera, date range, and limit. "
            "Returns a list of matching alerts with counts."
        ),
        "input_schema": {
            "type": "object",
            "properties": {
                "severity": {
                    "type": "string",
                    "enum": ["low", "medium", "high", "critical"],
                    "description": "Filter by alert severity level.",
                },
                "alert_type": {
                    "type": "string",
                    "description": "Filter by alert type (e.g. intrusion_detection, loitering, fire_smoke).",
                },
                "status": {
                    "type": "string",
                    "enum": ["new", "acknowledged", "resolved", "false_positive", "escalated"],
                    "description": "Filter by alert status.",
                },
                "camera_name": {
                    "type": "string",
                    "description": "Filter by camera name (partial match).",
                },
                "hours_ago": {
                    "type": "integer",
                    "description": "Only include alerts from the last N hours. Defaults to 24.",
                },
                "limit": {
                    "type": "integer",
                    "description": "Maximum number of alerts to return. Defaults to 20.",
                },
            },
            "required": [],
        },
    },
    {
        "name": "query_cameras",
        "description": (
            "Query camera information for the organization. "
            "Returns camera names, statuses (online/offline), locations, "
            "and configuration details."
        ),
        "input_schema": {
            "type": "object",
            "properties": {
                "status": {
                    "type": "string",
                    "enum": ["online", "offline", "all"],
                    "description": "Filter cameras by online status.",
                },
                "name": {
                    "type": "string",
                    "description": "Filter by camera name (partial match).",
                },
            },
            "required": [],
        },
    },
    {
        "name": "query_analytics",
        "description": (
            "Query footfall, occupancy, and dwell-time analytics data. "
            "Returns aggregated counts and trends for the requested period."
        ),
        "input_schema": {
            "type": "object",
            "properties": {
                "metric": {
                    "type": "string",
                    "enum": ["footfall", "occupancy", "dwell_time"],
                    "description": "Type of analytics metric to query.",
                },
                "camera_name": {
                    "type": "string",
                    "description": "Filter by camera name (partial match).",
                },
                "hours_ago": {
                    "type": "integer",
                    "description": "Query data from the last N hours. Defaults to 24.",
                },
                "limit": {
                    "type": "integer",
                    "description": "Maximum records to return. Defaults to 20.",
                },
            },
            "required": [],
        },
    },
    {
        "name": "query_faces",
        "description": (
            "Search the enrolled face database and recent face recognition events. "
            "Can search by person name, type (employee, visitor, blacklisted), "
            "or recent events."
        ),
        "input_schema": {
            "type": "object",
            "properties": {
                "search_type": {
                    "type": "string",
                    "enum": ["enrolled", "events"],
                    "description": "Search enrolled persons or recent face events.",
                },
                "person_name": {
                    "type": "string",
                    "description": "Filter by person name (partial match).",
                },
                "person_type": {
                    "type": "string",
                    "enum": ["employee", "visitor", "vip", "blacklisted", "contractor", "student"],
                    "description": "Filter by person type.",
                },
                "hours_ago": {
                    "type": "integer",
                    "description": "For events: only include events from the last N hours.",
                },
                "limit": {
                    "type": "integer",
                    "description": "Maximum records to return. Defaults to 20.",
                },
            },
            "required": [],
        },
    },
    {
        "name": "query_vehicles",
        "description": (
            "Search the vehicle database and recent ANPR detection events. "
            "Can search by plate number, category (whitelist, blacklist), "
            "or recent detection events."
        ),
        "input_schema": {
            "type": "object",
            "properties": {
                "search_type": {
                    "type": "string",
                    "enum": ["registered", "events", "logs"],
                    "description": "Search registered vehicles, ANPR events, or entry/exit logs.",
                },
                "plate_number": {
                    "type": "string",
                    "description": "Filter by plate number (partial match).",
                },
                "category": {
                    "type": "string",
                    "enum": ["whitelist", "blacklist", "visitor", "employee", "vip"],
                    "description": "Filter registered vehicles by category.",
                },
                "hours_ago": {
                    "type": "integer",
                    "description": "For events: only include from the last N hours.",
                },
                "limit": {
                    "type": "integer",
                    "description": "Maximum records to return. Defaults to 20.",
                },
            },
            "required": [],
        },
    },
    {
        "name": "search_recordings",
        "description": (
            "Search video recordings by camera name, time range, "
            "and recording type (continuous or event-triggered)."
        ),
        "input_schema": {
            "type": "object",
            "properties": {
                "camera_name": {
                    "type": "string",
                    "description": "Filter by camera name (partial match).",
                },
                "recording_type": {
                    "type": "string",
                    "enum": ["continuous", "event"],
                    "description": "Filter by recording type.",
                },
                "hours_ago": {
                    "type": "integer",
                    "description": "Recordings from the last N hours. Defaults to 24.",
                },
                "limit": {
                    "type": "integer",
                    "description": "Maximum recordings to return. Defaults to 10.",
                },
            },
            "required": [],
        },
    },
    {
        "name": "get_system_health",
        "description": (
            "Get overall system health status including camera counts, "
            "alert statistics, storage usage, and service status."
        ),
        "input_schema": {
            "type": "object",
            "properties": {},
            "required": [],
        },
    },
]


class CopilotService:
    """AI Surveillance Copilot backed by Claude with tool-use capabilities.

    The copilot can answer natural-language questions about the surveillance
    system by calling tool functions that execute real database queries.
    Conversations are stored in Redis for history and context management.
    """

    def __init__(self) -> None:
        """Initialise the Anthropic client using the API key from settings."""
        settings = get_settings()
        api_key = getattr(settings, "ANTHROPIC_API_KEY", None) or ""
        self._client = anthropic.Anthropic(api_key=api_key)
        self._redis: Any = None

    async def _get_redis(self) -> Any:
        """Lazily obtain the Redis connection pool."""
        if self._redis is None:
            from app.dependencies import get_redis
            self._redis = await get_redis()
        return self._redis

    # ------------------------------------------------------------------
    # Main Chat
    # ------------------------------------------------------------------

    async def chat(
        self,
        db: AsyncSession,
        org_id: uuid.UUID,
        user_id: uuid.UUID,
        message: str,
        conversation_id: Optional[str] = None,
    ) -> dict[str, Any]:
        """Process a user message and return the AI copilot response.

        1. Load or create conversation history from Redis.
        2. Build a context-rich system prompt.
        3. Send to Claude with tool definitions.
        4. Handle tool_use blocks by executing DB queries.
        5. Return the final text response with data cards.
        6. Persist the updated conversation.

        Args:
            db: Async database session.
            org_id: The user's organization ID.
            user_id: The authenticated user ID.
            message: The user's natural-language message.
            conversation_id: Optional existing conversation to continue.

        Returns:
            Dict with response_text, conversation_id, data_cards, and
            suggested_followups.
        """
        redis = await self._get_redis()

        # Resolve or create conversation
        if conversation_id is None:
            conversation_id = f"conv_{uuid.uuid4().hex[:16]}"

        conv_key = f"{_CONV_PREFIX}{user_id}:{conversation_id}"
        raw_history = await redis.get(conv_key)
        history: list[dict[str, Any]] = json.loads(raw_history) if raw_history else []

        # Append the new user message
        now_iso = datetime.now(timezone.utc).isoformat()
        history.append({
            "role": "user",
            "content": message,
            "data_cards": [],
            "timestamp": now_iso,
        })

        # Build the system prompt with live surveillance context
        system_prompt = await self._build_system_prompt(db, org_id)

        # Build Claude messages from history (trim to max turns)
        claude_messages = self._history_to_claude_messages(history)

        # Call Claude with tools
        data_cards: list[dict[str, Any]] = []
        response_text = ""

        try:
            response = self._client.messages.create(
                model=_CLAUDE_MODEL,
                max_tokens=4096,
                system=system_prompt,
                tools=_TOOL_DEFINITIONS,
                messages=claude_messages,
            )

            # Handle tool-use loop (Claude may request multiple tools)
            max_iterations = 10
            iteration = 0
            while response.stop_reason == "tool_use" and iteration < max_iterations:
                iteration += 1
                tool_results = []

                for block in response.content:
                    if block.type == "tool_use":
                        logger.info(
                            "Copilot tool call",
                            tool=block.name,
                            input=block.input,
                            conversation_id=conversation_id,
                        )
                        tool_result, cards = await self._execute_tool(
                            db, org_id, block.name, block.input
                        )
                        data_cards.extend(cards)
                        tool_results.append({
                            "type": "tool_result",
                            "tool_use_id": block.id,
                            "content": json.dumps(tool_result, default=str),
                        })

                # Continue the conversation with tool results
                claude_messages.append({"role": "assistant", "content": response.content})
                claude_messages.append({"role": "user", "content": tool_results})

                response = self._client.messages.create(
                    model=_CLAUDE_MODEL,
                    max_tokens=4096,
                    system=system_prompt,
                    tools=_TOOL_DEFINITIONS,
                    messages=claude_messages,
                )

            # Extract final text response
            for block in response.content:
                if hasattr(block, "text"):
                    response_text += block.text

        except anthropic.APIConnectionError:
            logger.error("Anthropic API connection error", conversation_id=conversation_id)
            response_text = (
                "I'm currently unable to connect to my AI backend. "
                "Please try again in a moment."
            )
        except anthropic.RateLimitError:
            logger.warning("Anthropic rate limit hit", conversation_id=conversation_id)
            response_text = (
                "I've hit my rate limit. Please wait a moment and try again."
            )
        except anthropic.APIStatusError as exc:
            logger.error(
                "Anthropic API error",
                status=exc.status_code,
                conversation_id=conversation_id,
            )
            response_text = (
                "I encountered an error while processing your request. "
                "Please try again."
            )
        except Exception as exc:
            logger.exception("Unexpected copilot error", error=str(exc))
            response_text = (
                "An unexpected error occurred. Please try again or rephrase your question."
            )

        # Generate suggested follow-up questions
        suggested_followups = self._generate_followups(message, response_text)

        # Append assistant message to history
        history.append({
            "role": "assistant",
            "content": response_text,
            "data_cards": data_cards,
            "timestamp": datetime.now(timezone.utc).isoformat(),
        })

        # Trim history to prevent unbounded growth
        if len(history) > _MAX_HISTORY_TURNS:
            history = history[-_MAX_HISTORY_TURNS:]

        # Persist conversation to Redis
        await redis.set(conv_key, json.dumps(history, default=str), ex=_CONV_TTL)

        # Update the conversation list index
        await self._update_conversation_index(
            redis, user_id, conversation_id, message, len(history)
        )

        return {
            "response_text": response_text,
            "conversation_id": conversation_id,
            "data_cards": data_cards,
            "suggested_followups": suggested_followups,
        }

    # ------------------------------------------------------------------
    # System Prompt
    # ------------------------------------------------------------------

    async def _build_system_prompt(
        self,
        db: AsyncSession,
        org_id: uuid.UUID,
    ) -> str:
        """Build a context-rich system prompt with live surveillance data.

        Includes organization info, camera count, active alert summary,
        and other contextual details that help the AI provide relevant
        answers.
        """
        # Gather contextual statistics
        org_result = await db.execute(
            select(Organization).where(Organization.id == org_id)
        )
        org = org_result.scalar_one_or_none()
        org_name = org.name if org else "Unknown"
        org_timezone = org.timezone if org else "UTC"

        # Camera counts
        total_cameras = (await db.execute(
            select(func.count()).select_from(Camera).where(Camera.org_id == org_id)
        )).scalar() or 0

        online_cameras = (await db.execute(
            select(func.count()).select_from(Camera).where(
                Camera.org_id == org_id, Camera.is_online.is_(True)
            )
        )).scalar() or 0

        offline_cameras = total_cameras - online_cameras

        # Today's alert counts (naive datetime for TIMESTAMP WITHOUT TIME ZONE)
        today_start = _utcnow_naive().replace(
            hour=0, minute=0, second=0, microsecond=0
        )

        total_alerts_today = (await db.execute(
            select(func.count()).select_from(Alert).where(
                Alert.org_id == org_id, Alert.created_at >= today_start
            )
        )).scalar() or 0

        critical_alerts = (await db.execute(
            select(func.count()).select_from(Alert).where(
                Alert.org_id == org_id,
                Alert.created_at >= today_start,
                Alert.severity == RuleSeverity.CRITICAL,
            )
        )).scalar() or 0

        unresolved_alerts = (await db.execute(
            select(func.count()).select_from(Alert).where(
                Alert.org_id == org_id,
                Alert.status.in_([AlertStatus.NEW, AlertStatus.ESCALATED]),
            )
        )).scalar() or 0

        # Enrolled persons count
        total_persons = (await db.execute(
            select(func.count()).select_from(Person).where(Person.org_id == org_id)
        )).scalar() or 0

        # Registered vehicles count
        total_vehicles = (await db.execute(
            select(func.count()).select_from(Vehicle).where(Vehicle.org_id == org_id)
        )).scalar() or 0

        # Active rules count
        active_rules = (await db.execute(
            select(func.count()).select_from(Rule).where(
                Rule.org_id == org_id, Rule.is_active.is_(True)
            )
        )).scalar() or 0

        current_time = datetime.now(timezone.utc).strftime("%Y-%m-%d %H:%M UTC")

        return f"""You are the IBVAP Border Surveillance Copilot, an intelligent AI assistant embedded in the IBVAP (Intelligent Border Video Analytics Platform) system for border surveillance. You help border security forces, outpost commanders, and operators query and monitor border perimeter security through natural language.

## Your Capabilities
- Query and analyze alerts (intrusion, loitering, fire/smoke, PPE violations, etc.)
- Check camera status and health across the facility
- Retrieve footfall analytics, occupancy data, and dwell-time metrics
- Search the face recognition database and recent face events
- Search ANPR (Automatic Number Plate Recognition) vehicle records
- Find and reference video recordings
- Report on overall system health

## Current Context
- **Organization**: {org_name}
- **Timezone**: {org_timezone}
- **Current Time**: {current_time}
- **Cameras**: {total_cameras} total ({online_cameras} online, {offline_cameras} offline)
- **Today's Alerts**: {total_alerts_today} ({critical_alerts} critical, {unresolved_alerts} unresolved)
- **Enrolled Persons**: {total_persons}
- **Registered Vehicles**: {total_vehicles}
- **Active Detection Rules**: {active_rules}

## Guidelines
1. Always use the provided tools to query real data before answering data-related questions. Never fabricate surveillance data.
2. Present data clearly using tables and structured formats when appropriate.
3. When showing alert or event data, include severity levels, timestamps, and camera names.
4. For time-based queries, clarify the time range you're searching.
5. If a query returns no results, say so clearly and suggest alternative queries.
6. Be concise but thorough. Security operators need actionable information quickly.
7. When discussing camera offline issues, suggest checking network connectivity and camera power.
8. For critical alerts, emphasize urgency and recommend immediate review.
9. Never expose sensitive system credentials, IP addresses, or internal network details.
10. You can use markdown formatting (bold, lists, tables, code blocks) in your responses.
11. When presenting numerical data, include totals and percentages where useful.
12. If the user asks something outside your surveillance domain, politely redirect to relevant topics."""

    # ------------------------------------------------------------------
    # History Conversion
    # ------------------------------------------------------------------

    @staticmethod
    def _history_to_claude_messages(
        history: list[dict[str, Any]],
    ) -> list[dict[str, Any]]:
        """Convert the internal conversation history to Claude message format.

        Only keeps the last ``_MAX_HISTORY_TURNS`` messages and strips
        metadata (data_cards, timestamps) that Claude doesn't need.
        """
        recent = history[-_MAX_HISTORY_TURNS:]
        messages: list[dict[str, Any]] = []
        for entry in recent:
            role = entry.get("role", "user")
            content = entry.get("content", "")
            if role in ("user", "assistant") and content:
                messages.append({"role": role, "content": content})

        # Ensure messages start with a user message (Claude requirement)
        while messages and messages[0]["role"] != "user":
            messages.pop(0)

        # Ensure no consecutive same-role messages (merge if needed)
        merged: list[dict[str, Any]] = []
        for msg in messages:
            if merged and merged[-1]["role"] == msg["role"]:
                merged[-1]["content"] += "\n\n" + msg["content"]
            else:
                merged.append(msg)

        return merged

    # ------------------------------------------------------------------
    # Tool Execution
    # ------------------------------------------------------------------

    async def _execute_tool(
        self,
        db: AsyncSession,
        org_id: uuid.UUID,
        tool_name: str,
        tool_input: dict[str, Any],
    ) -> tuple[Any, list[dict[str, Any]]]:
        """Execute a tool function and return the result plus data cards.

        Args:
            db: Async database session.
            org_id: Organization ID for data scoping.
            tool_name: Name of the tool to execute.
            tool_input: Input parameters for the tool.

        Returns:
            Tuple of (tool_result_data, list_of_data_cards).
        """
        tool_map = {
            "query_alerts": self._query_alerts,
            "query_cameras": self._query_cameras,
            "query_analytics": self._query_analytics,
            "query_faces": self._query_faces,
            "query_vehicles": self._query_vehicles,
            "search_recordings": self._search_recordings,
            "get_system_health": self._get_system_health,
        }

        handler = tool_map.get(tool_name)
        if handler is None:
            logger.warning("Unknown tool requested", tool_name=tool_name)
            return {"error": f"Unknown tool: {tool_name}"}, []

        try:
            return await handler(db, org_id, tool_input)
        except Exception as exc:
            logger.exception("Tool execution failed", tool=tool_name, error=str(exc))
            return {"error": f"Tool execution failed: {str(exc)}"}, []

    # ------------------------------------------------------------------
    # Tool Implementations
    # ------------------------------------------------------------------

    async def _query_alerts(
        self,
        db: AsyncSession,
        org_id: uuid.UUID,
        params: dict[str, Any],
    ) -> tuple[dict[str, Any], list[dict[str, Any]]]:
        """Query alerts with optional filters."""
        hours_ago = params.get("hours_ago", 24)
        limit = min(params.get("limit", 20), 50)
        since = _utcnow_naive() - timedelta(hours=hours_ago)

        query = (
            select(Alert)
            .where(Alert.org_id == org_id, Alert.created_at >= since)
        )

        if params.get("severity"):
            try:
                query = query.where(Alert.severity == RuleSeverity(params["severity"]))
            except ValueError:
                pass

        if params.get("alert_type"):
            try:
                query = query.where(Alert.alert_type == RuleType(params["alert_type"]))
            except ValueError:
                pass

        if params.get("status"):
            try:
                query = query.where(Alert.status == AlertStatus(params["status"]))
            except ValueError:
                pass

        if params.get("camera_name"):
            camera_sub = select(Camera.id).where(
                Camera.org_id == org_id,
                Camera.name.ilike(f"%{params['camera_name']}%"),
            )
            query = query.where(Alert.camera_id.in_(camera_sub))

        # Get total count
        count_q = select(func.count()).select_from(query.subquery())
        total = (await db.execute(count_q)).scalar() or 0

        # Get alert records
        query = query.order_by(Alert.created_at.desc()).limit(limit)
        result = await db.execute(query)
        alerts = result.scalars().all()

        # Severity breakdown
        severity_q = (
            select(Alert.severity, func.count().label("cnt"))
            .where(Alert.org_id == org_id, Alert.created_at >= since)
            .group_by(Alert.severity)
        )
        sev_result = await db.execute(severity_q)
        severity_breakdown = {
            row.severity.value: row.cnt for row in sev_result.all()
        }

        alert_rows = []
        for a in alerts:
            # Load camera name via relationship
            camera_name = "Unknown"
            if a.camera_id:
                cam_result = await db.execute(
                    select(Camera.name).where(Camera.id == a.camera_id)
                )
                cam_name = cam_result.scalar_one_or_none()
                if cam_name:
                    camera_name = cam_name

            alert_rows.append({
                "id": str(a.id),
                "title": a.title,
                "type": a.alert_type.value if a.alert_type else "",
                "severity": a.severity.value if a.severity else "",
                "status": a.status.value if a.status else "",
                "camera": camera_name,
                "created_at": a.created_at.isoformat() if a.created_at else "",
            })

        result_data = {
            "total": total,
            "returned": len(alert_rows),
            "time_range": f"Last {hours_ago} hours",
            "severity_breakdown": severity_breakdown,
            "alerts": alert_rows,
        }

        # Build data cards
        cards: list[dict[str, Any]] = []
        if severity_breakdown:
            cards.append({
                "type": "chart",
                "title": f"Alerts by Severity (Last {hours_ago}h)",
                "data": {
                    "labels": list(severity_breakdown.keys()),
                    "datasets": [{
                        "label": "Alert Count",
                        "data": list(severity_breakdown.values()),
                    }],
                },
            })

        if alert_rows:
            cards.append({
                "type": "alert_list",
                "title": f"Recent Alerts ({len(alert_rows)} of {total})",
                "data": alert_rows[:10],
            })

        return result_data, cards

    async def _query_cameras(
        self,
        db: AsyncSession,
        org_id: uuid.UUID,
        params: dict[str, Any],
    ) -> tuple[dict[str, Any], list[dict[str, Any]]]:
        """Query camera information with optional filters."""
        query = select(Camera).where(Camera.org_id == org_id)

        status_filter = params.get("status", "all")
        if status_filter == "online":
            query = query.where(Camera.is_online.is_(True))
        elif status_filter == "offline":
            query = query.where(Camera.is_online.is_(False))

        if params.get("name"):
            query = query.where(Camera.name.ilike(f"%{params['name']}%"))

        query = query.order_by(Camera.name)
        result = await db.execute(query)
        cameras = result.scalars().all()

        camera_rows = []
        for c in cameras:
            camera_rows.append({
                "id": str(c.id),
                "name": c.name,
                "location": c.location_description or "N/A",
                "status": "online" if c.is_online else "offline",
                "protocol": c.protocol.value if c.protocol else "",
                "resolution": c.resolution or "N/A",
                "fps": c.fps,
                "recording_mode": c.recording_mode.value if c.recording_mode else "",
                "last_seen": c.last_seen_at.isoformat() if c.last_seen_at else "Never",
            })

        online_count = sum(1 for c in camera_rows if c["status"] == "online")
        offline_count = len(camera_rows) - online_count

        result_data = {
            "total": len(camera_rows),
            "online": online_count,
            "offline": offline_count,
            "cameras": camera_rows,
        }

        cards: list[dict[str, Any]] = []
        if camera_rows:
            cards.append({
                "type": "camera_grid",
                "title": f"Cameras ({online_count} online, {offline_count} offline)",
                "data": camera_rows,
            })

        return result_data, cards

    async def _query_analytics(
        self,
        db: AsyncSession,
        org_id: uuid.UUID,
        params: dict[str, Any],
    ) -> tuple[dict[str, Any], list[dict[str, Any]]]:
        """Query analytics data (footfall, occupancy, dwell time)."""
        metric = params.get("metric", "footfall")
        hours_ago = params.get("hours_ago", 24)
        limit = min(params.get("limit", 20), 50)
        since = _utcnow_naive() - timedelta(hours=hours_ago)

        # Resolve camera filter
        camera_ids = None
        if params.get("camera_name"):
            cam_q = select(Camera.id).where(
                Camera.org_id == org_id,
                Camera.name.ilike(f"%{params['camera_name']}%"),
            )
            cam_result = await db.execute(cam_q)
            camera_ids = [row[0] for row in cam_result.all()]

        cards: list[dict[str, Any]] = []

        if metric == "footfall":
            query = (
                select(FootfallRecord)
                .join(Camera, FootfallRecord.camera_id == Camera.id)
                .where(Camera.org_id == org_id, FootfallRecord.timestamp >= since)
            )
            if camera_ids is not None:
                query = query.where(FootfallRecord.camera_id.in_(camera_ids))

            query = query.order_by(FootfallRecord.timestamp.desc()).limit(limit)
            result = await db.execute(query)
            records = result.scalars().all()

            total_entries = sum(r.entries_count for r in records)
            total_exits = sum(r.exits_count for r in records)

            rows = []
            for r in records:
                cam_result = await db.execute(
                    select(Camera.name).where(Camera.id == r.camera_id)
                )
                cam_name = cam_result.scalar_one_or_none() or "Unknown"
                rows.append({
                    "timestamp": r.timestamp.isoformat() if r.timestamp else "",
                    "camera": cam_name,
                    "entries": r.entries_count,
                    "exits": r.exits_count,
                    "occupancy": r.occupancy_estimate,
                })

            result_data = {
                "metric": "footfall",
                "time_range": f"Last {hours_ago} hours",
                "total_entries": total_entries,
                "total_exits": total_exits,
                "records": rows,
            }

            if rows:
                cards.append({
                    "type": "stat",
                    "title": "Footfall Summary",
                    "data": {
                        "label": "Total Entries / Exits",
                        "value": f"{total_entries} / {total_exits}",
                        "trend": "neutral",
                    },
                })
                cards.append({
                    "type": "table",
                    "title": f"Footfall Records (Last {hours_ago}h)",
                    "data": {
                        "headers": ["Timestamp", "Camera", "Entries", "Exits", "Occupancy"],
                        "rows": [
                            [r["timestamp"], r["camera"], r["entries"], r["exits"], r["occupancy"]]
                            for r in rows[:10]
                        ],
                    },
                })

        elif metric == "dwell_time":
            query = (
                select(DwellRecord)
                .join(Camera, DwellRecord.camera_id == Camera.id)
                .where(Camera.org_id == org_id, DwellRecord.enter_time >= since)
            )
            if camera_ids is not None:
                query = query.where(DwellRecord.camera_id.in_(camera_ids))

            query = query.order_by(DwellRecord.enter_time.desc()).limit(limit)
            result = await db.execute(query)
            records = result.scalars().all()

            dwell_values = [r.dwell_seconds for r in records if r.dwell_seconds is not None]
            avg_dwell = round(sum(dwell_values) / len(dwell_values), 1) if dwell_values else 0
            max_dwell = round(max(dwell_values), 1) if dwell_values else 0

            result_data = {
                "metric": "dwell_time",
                "time_range": f"Last {hours_ago} hours",
                "total_records": len(records),
                "avg_dwell_seconds": avg_dwell,
                "max_dwell_seconds": max_dwell,
            }

            cards.append({
                "type": "stat",
                "title": "Dwell Time Summary",
                "data": {
                    "label": "Avg / Max Dwell",
                    "value": f"{avg_dwell}s / {max_dwell}s",
                    "trend": "neutral",
                },
            })

        else:
            # occupancy - derive from footfall
            query = (
                select(FootfallRecord)
                .join(Camera, FootfallRecord.camera_id == Camera.id)
                .where(
                    Camera.org_id == org_id,
                    FootfallRecord.timestamp >= since,
                    FootfallRecord.occupancy_estimate.isnot(None),
                )
            )
            if camera_ids is not None:
                query = query.where(FootfallRecord.camera_id.in_(camera_ids))

            query = query.order_by(FootfallRecord.timestamp.desc()).limit(limit)
            result = await db.execute(query)
            records = result.scalars().all()

            occ_values = [r.occupancy_estimate for r in records if r.occupancy_estimate is not None]
            avg_occ = round(sum(occ_values) / len(occ_values), 1) if occ_values else 0
            max_occ = max(occ_values) if occ_values else 0

            result_data = {
                "metric": "occupancy",
                "time_range": f"Last {hours_ago} hours",
                "total_records": len(records),
                "avg_occupancy": avg_occ,
                "max_occupancy": max_occ,
            }

            cards.append({
                "type": "stat",
                "title": "Occupancy Summary",
                "data": {
                    "label": "Avg / Max Occupancy",
                    "value": f"{avg_occ} / {max_occ}",
                    "trend": "neutral",
                },
            })

        return result_data, cards

    async def _query_faces(
        self,
        db: AsyncSession,
        org_id: uuid.UUID,
        params: dict[str, Any],
    ) -> tuple[dict[str, Any], list[dict[str, Any]]]:
        """Search face enrollment database or recent events."""
        search_type = params.get("search_type", "enrolled")
        limit = min(params.get("limit", 20), 50)
        cards: list[dict[str, Any]] = []

        if search_type == "enrolled":
            query = select(Person).where(Person.org_id == org_id)

            if params.get("person_name"):
                query = query.where(
                    Person.full_name.ilike(f"%{params['person_name']}%")
                )
            if params.get("person_type"):
                try:
                    query = query.where(
                        Person.person_type == PersonType(params["person_type"])
                    )
                except ValueError:
                    pass

            count_q = select(func.count()).select_from(query.subquery())
            total = (await db.execute(count_q)).scalar() or 0

            query = query.order_by(Person.full_name).limit(limit)
            result = await db.execute(query)
            persons = result.scalars().all()

            rows = [{
                "id": str(p.id),
                "name": p.full_name,
                "type": p.person_type.value if p.person_type else "",
                "created_at": p.created_at.isoformat() if p.created_at else "",
            } for p in persons]

            result_data = {
                "search_type": "enrolled",
                "total": total,
                "returned": len(rows),
                "persons": rows,
            }

            if rows:
                cards.append({
                    "type": "table",
                    "title": f"Enrolled Persons ({len(rows)} of {total})",
                    "data": {
                        "headers": ["Name", "Type", "Enrolled"],
                        "rows": [[r["name"], r["type"], r["created_at"]] for r in rows[:10]],
                    },
                })

        else:
            # face events
            hours_ago = params.get("hours_ago", 24)
            since = _utcnow_naive() - timedelta(hours=hours_ago)

            query = (
                select(FaceEvent)
                .join(Camera, FaceEvent.camera_id == Camera.id)
                .where(Camera.org_id == org_id, FaceEvent.timestamp >= since)
            )

            if params.get("person_name"):
                person_sub = select(Person.id).where(
                    Person.org_id == org_id,
                    Person.full_name.ilike(f"%{params['person_name']}%"),
                )
                query = query.where(FaceEvent.person_id.in_(person_sub))

            count_q = select(func.count()).select_from(query.subquery())
            total = (await db.execute(count_q)).scalar() or 0

            query = query.order_by(FaceEvent.timestamp.desc()).limit(limit)
            result = await db.execute(query)
            events = result.scalars().all()

            rows = []
            for e in events:
                person_name = "Unknown"
                if e.person_id:
                    p_result = await db.execute(
                        select(Person.full_name).where(Person.id == e.person_id)
                    )
                    pname = p_result.scalar_one_or_none()
                    if pname:
                        person_name = pname

                cam_result = await db.execute(
                    select(Camera.name).where(Camera.id == e.camera_id)
                )
                cam_name = cam_result.scalar_one_or_none() or "Unknown"

                rows.append({
                    "id": str(e.id),
                    "person": person_name,
                    "camera": cam_name,
                    "confidence": round(e.confidence, 2) if e.confidence else None,
                    "emotion": e.emotion,
                    "timestamp": e.timestamp.isoformat() if e.timestamp else "",
                })

            result_data = {
                "search_type": "events",
                "total": total,
                "returned": len(rows),
                "time_range": f"Last {hours_ago} hours",
                "events": rows,
            }

            if rows:
                cards.append({
                    "type": "table",
                    "title": f"Face Events ({len(rows)} of {total})",
                    "data": {
                        "headers": ["Person", "Camera", "Confidence", "Emotion", "Time"],
                        "rows": [
                            [r["person"], r["camera"], r["confidence"], r["emotion"], r["timestamp"]]
                            for r in rows[:10]
                        ],
                    },
                })

        return result_data, cards

    async def _query_vehicles(
        self,
        db: AsyncSession,
        org_id: uuid.UUID,
        params: dict[str, Any],
    ) -> tuple[dict[str, Any], list[dict[str, Any]]]:
        """Search vehicle database or ANPR events."""
        search_type = params.get("search_type", "registered")
        limit = min(params.get("limit", 20), 50)
        cards: list[dict[str, Any]] = []

        if search_type == "registered":
            query = select(Vehicle).where(Vehicle.org_id == org_id)

            if params.get("plate_number"):
                query = query.where(
                    Vehicle.plate_number.ilike(f"%{params['plate_number']}%")
                )
            if params.get("category"):
                from app.models.vehicle import VehicleCategory
                try:
                    query = query.where(
                        Vehicle.category == VehicleCategory(params["category"])
                    )
                except ValueError:
                    pass

            count_q = select(func.count()).select_from(query.subquery())
            total = (await db.execute(count_q)).scalar() or 0

            query = query.order_by(Vehicle.plate_number).limit(limit)
            result = await db.execute(query)
            vehicles = result.scalars().all()

            rows = [{
                "id": str(v.id),
                "plate": v.plate_number,
                "owner": v.owner_name or "N/A",
                "type": v.vehicle_type or "N/A",
                "color": v.color or "N/A",
                "category": v.category.value if v.category else "",
                "make_model": f"{v.make or ''} {v.model_name or ''}".strip() or "N/A",
            } for v in vehicles]

            result_data = {
                "search_type": "registered",
                "total": total,
                "returned": len(rows),
                "vehicles": rows,
            }

            if rows:
                cards.append({
                    "type": "table",
                    "title": f"Registered Vehicles ({len(rows)} of {total})",
                    "data": {
                        "headers": ["Plate", "Owner", "Type", "Color", "Category"],
                        "rows": [
                            [r["plate"], r["owner"], r["type"], r["color"], r["category"]]
                            for r in rows[:10]
                        ],
                    },
                })

        elif search_type == "events":
            hours_ago = params.get("hours_ago", 24)
            since = _utcnow_naive() - timedelta(hours=hours_ago)

            query = (
                select(VehicleEvent)
                .join(Camera, VehicleEvent.camera_id == Camera.id)
                .where(Camera.org_id == org_id, VehicleEvent.timestamp >= since)
            )

            if params.get("plate_number"):
                query = query.where(
                    VehicleEvent.plate_number.ilike(f"%{params['plate_number']}%")
                )

            count_q = select(func.count()).select_from(query.subquery())
            total = (await db.execute(count_q)).scalar() or 0

            query = query.order_by(VehicleEvent.timestamp.desc()).limit(limit)
            result = await db.execute(query)
            events = result.scalars().all()

            rows = []
            for e in events:
                cam_result = await db.execute(
                    select(Camera.name).where(Camera.id == e.camera_id)
                )
                cam_name = cam_result.scalar_one_or_none() or "Unknown"

                rows.append({
                    "id": str(e.id),
                    "plate": e.plate_number,
                    "camera": cam_name,
                    "confidence": round(e.plate_confidence, 2) if e.plate_confidence else None,
                    "direction": e.direction.value if e.direction else "N/A",
                    "timestamp": e.timestamp.isoformat() if e.timestamp else "",
                })

            result_data = {
                "search_type": "events",
                "total": total,
                "returned": len(rows),
                "time_range": f"Last {hours_ago} hours",
                "events": rows,
            }

            if rows:
                cards.append({
                    "type": "table",
                    "title": f"Vehicle Events ({len(rows)} of {total})",
                    "data": {
                        "headers": ["Plate", "Camera", "Confidence", "Direction", "Time"],
                        "rows": [
                            [r["plate"], r["camera"], r["confidence"], r["direction"], r["timestamp"]]
                            for r in rows[:10]
                        ],
                    },
                })

        else:
            # vehicle logs (entry/exit)
            hours_ago = params.get("hours_ago", 24)
            since = _utcnow_naive() - timedelta(hours=hours_ago)

            query = (
                select(VehicleLog)
                .join(Vehicle, VehicleLog.vehicle_id == Vehicle.id)
                .where(Vehicle.org_id == org_id, VehicleLog.entry_time >= since)
            )

            if params.get("plate_number"):
                vehicle_sub = select(Vehicle.id).where(
                    Vehicle.org_id == org_id,
                    Vehicle.plate_number.ilike(f"%{params['plate_number']}%"),
                )
                query = query.where(VehicleLog.vehicle_id.in_(vehicle_sub))

            count_q = select(func.count()).select_from(query.subquery())
            total = (await db.execute(count_q)).scalar() or 0

            query = query.order_by(VehicleLog.entry_time.desc()).limit(limit)
            result = await db.execute(query)
            logs = result.scalars().all()

            rows = []
            for log in logs:
                v_result = await db.execute(
                    select(Vehicle.plate_number).where(Vehicle.id == log.vehicle_id)
                )
                plate = v_result.scalar_one_or_none() or "Unknown"

                rows.append({
                    "id": str(log.id),
                    "plate": plate,
                    "entry_time": log.entry_time.isoformat() if log.entry_time else "",
                    "exit_time": log.exit_time.isoformat() if log.exit_time else "Still parked",
                    "duration": f"{log.duration_seconds // 60}m" if log.duration_seconds else "N/A",
                })

            result_data = {
                "search_type": "logs",
                "total": total,
                "returned": len(rows),
                "time_range": f"Last {hours_ago} hours",
                "logs": rows,
            }

            if rows:
                cards.append({
                    "type": "table",
                    "title": f"Vehicle Logs ({len(rows)} of {total})",
                    "data": {
                        "headers": ["Plate", "Entry", "Exit", "Duration"],
                        "rows": [
                            [r["plate"], r["entry_time"], r["exit_time"], r["duration"]]
                            for r in rows[:10]
                        ],
                    },
                })

        return result_data, cards

    async def _search_recordings(
        self,
        db: AsyncSession,
        org_id: uuid.UUID,
        params: dict[str, Any],
    ) -> tuple[dict[str, Any], list[dict[str, Any]]]:
        """Search video recordings."""
        hours_ago = params.get("hours_ago", 24)
        limit = min(params.get("limit", 10), 30)
        since = _utcnow_naive() - timedelta(hours=hours_ago)

        query = (
            select(Recording)
            .where(Recording.org_id == org_id, Recording.start_time >= since)
        )

        if params.get("camera_name"):
            camera_sub = select(Camera.id).where(
                Camera.org_id == org_id,
                Camera.name.ilike(f"%{params['camera_name']}%"),
            )
            query = query.where(Recording.camera_id.in_(camera_sub))

        if params.get("recording_type"):
            from app.models.recording import RecordingType
            try:
                query = query.where(
                    Recording.recording_type == RecordingType(params["recording_type"])
                )
            except ValueError:
                pass

        count_q = select(func.count()).select_from(query.subquery())
        total = (await db.execute(count_q)).scalar() or 0

        query = query.order_by(Recording.start_time.desc()).limit(limit)
        result = await db.execute(query)
        recordings = result.scalars().all()

        rows = []
        for r in recordings:
            cam_result = await db.execute(
                select(Camera.name).where(Camera.id == r.camera_id)
            )
            cam_name = cam_result.scalar_one_or_none() or "Unknown"

            duration_str = "N/A"
            if r.duration_seconds:
                mins = int(r.duration_seconds // 60)
                secs = int(r.duration_seconds % 60)
                duration_str = f"{mins}m {secs}s"

            size_str = "N/A"
            if r.file_size_bytes:
                mb = r.file_size_bytes / (1024 * 1024)
                size_str = f"{mb:.1f} MB"

            rows.append({
                "id": str(r.id),
                "camera": cam_name,
                "type": r.recording_type.value if r.recording_type else "",
                "start": r.start_time.isoformat() if r.start_time else "",
                "end": r.end_time.isoformat() if r.end_time else "In progress",
                "duration": duration_str,
                "size": size_str,
            })

        result_data = {
            "total": total,
            "returned": len(rows),
            "time_range": f"Last {hours_ago} hours",
            "recordings": rows,
        }

        cards: list[dict[str, Any]] = []
        if rows:
            cards.append({
                "type": "table",
                "title": f"Recordings ({len(rows)} of {total})",
                "data": {
                    "headers": ["Camera", "Type", "Start", "Duration", "Size"],
                    "rows": [
                        [r["camera"], r["type"], r["start"], r["duration"], r["size"]]
                        for r in rows[:10]
                    ],
                },
            })

        return result_data, cards

    async def _get_system_health(
        self,
        db: AsyncSession,
        org_id: uuid.UUID,
        params: dict[str, Any] | None = None,
    ) -> tuple[dict[str, Any], list[dict[str, Any]]]:
        """Get overall system health status."""
        if params is None:
            params = {}

        # Camera stats
        total_cameras = (await db.execute(
            select(func.count()).select_from(Camera).where(Camera.org_id == org_id)
        )).scalar() or 0

        online_cameras = (await db.execute(
            select(func.count()).select_from(Camera).where(
                Camera.org_id == org_id, Camera.is_online.is_(True)
            )
        )).scalar() or 0

        # Alert stats (last 24h)
        since_24h = _utcnow_naive() - timedelta(hours=24)
        alerts_24h = (await db.execute(
            select(func.count()).select_from(Alert).where(
                Alert.org_id == org_id, Alert.created_at >= since_24h
            )
        )).scalar() or 0

        critical_24h = (await db.execute(
            select(func.count()).select_from(Alert).where(
                Alert.org_id == org_id,
                Alert.created_at >= since_24h,
                Alert.severity == RuleSeverity.CRITICAL,
            )
        )).scalar() or 0

        unresolved = (await db.execute(
            select(func.count()).select_from(Alert).where(
                Alert.org_id == org_id,
                Alert.status.in_([AlertStatus.NEW, AlertStatus.ESCALATED]),
            )
        )).scalar() or 0

        # Rules
        active_rules = (await db.execute(
            select(func.count()).select_from(Rule).where(
                Rule.org_id == org_id, Rule.is_active.is_(True)
            )
        )).scalar() or 0

        total_rules = (await db.execute(
            select(func.count()).select_from(Rule).where(Rule.org_id == org_id)
        )).scalar() or 0

        # Persons and vehicles
        total_persons = (await db.execute(
            select(func.count()).select_from(Person).where(Person.org_id == org_id)
        )).scalar() or 0

        total_vehicles = (await db.execute(
            select(func.count()).select_from(Vehicle).where(Vehicle.org_id == org_id)
        )).scalar() or 0

        # Recordings storage (last 7 days)
        since_7d = _utcnow_naive() - timedelta(days=7)
        storage_result = await db.execute(
            select(func.sum(Recording.file_size_bytes)).where(
                Recording.org_id == org_id,
                Recording.start_time >= since_7d,
            )
        )
        total_storage_bytes = storage_result.scalar() or 0
        storage_gb = round(total_storage_bytes / (1024 ** 3), 2)

        # Users
        total_users = (await db.execute(
            select(func.count()).select_from(User).where(
                User.org_id == org_id, User.is_active.is_(True)
            )
        )).scalar() or 0

        camera_health_pct = round(
            (online_cameras / total_cameras * 100) if total_cameras > 0 else 0, 1
        )
        overall_status = "healthy"
        if camera_health_pct < 50:
            overall_status = "degraded"
        elif camera_health_pct < 80:
            overall_status = "warning"
        if critical_24h > 0 and unresolved > 5:
            overall_status = "attention_needed"

        result_data = {
            "overall_status": overall_status,
            "cameras": {
                "total": total_cameras,
                "online": online_cameras,
                "offline": total_cameras - online_cameras,
                "health_pct": camera_health_pct,
            },
            "alerts_24h": {
                "total": alerts_24h,
                "critical": critical_24h,
                "unresolved": unresolved,
            },
            "rules": {
                "total": total_rules,
                "active": active_rules,
            },
            "face_database": {
                "total_persons": total_persons,
            },
            "vehicle_database": {
                "total_vehicles": total_vehicles,
            },
            "storage": {
                "last_7_days_gb": storage_gb,
            },
            "users": {
                "active": total_users,
            },
        }

        cards: list[dict[str, Any]] = [
            {
                "type": "stat",
                "title": "System Status",
                "data": {
                    "label": "Overall Health",
                    "value": overall_status.replace("_", " ").title(),
                    "trend": "up" if overall_status == "healthy" else "down",
                },
            },
            {
                "type": "table",
                "title": "System Overview",
                "data": {
                    "headers": ["Component", "Status", "Details"],
                    "rows": [
                        ["Cameras", f"{online_cameras}/{total_cameras} online", f"{camera_health_pct}% health"],
                        ["Alerts (24h)", str(alerts_24h), f"{critical_24h} critical, {unresolved} unresolved"],
                        ["Detection Rules", f"{active_rules}/{total_rules} active", ""],
                        ["Face Database", f"{total_persons} persons", ""],
                        ["Vehicle Database", f"{total_vehicles} vehicles", ""],
                        ["Storage (7d)", f"{storage_gb} GB", ""],
                        ["Active Users", str(total_users), ""],
                    ],
                },
            },
        ]

        return result_data, cards

    # ------------------------------------------------------------------
    # Follow-up Suggestions
    # ------------------------------------------------------------------

    @staticmethod
    def _generate_followups(user_message: str, response_text: str) -> list[str]:
        """Generate context-aware follow-up question suggestions.

        Uses simple keyword matching on the conversation to suggest
        relevant next questions. This runs locally without an API call.
        """
        combined = (user_message + " " + response_text).lower()
        suggestions: list[str] = []

        if any(kw in combined for kw in ["alert", "intrusion", "violation", "fire"]):
            suggestions.extend([
                "Show me critical alerts from the last hour",
                "Which cameras have the most alerts today?",
                "What is the false positive rate for alerts?",
            ])

        if any(kw in combined for kw in ["camera", "online", "offline", "stream"]):
            suggestions.extend([
                "Which cameras are currently offline?",
                "Show me the camera health overview",
                "What is the uptime for all cameras this week?",
            ])

        if any(kw in combined for kw in ["footfall", "occupancy", "traffic", "people", "count"]):
            suggestions.extend([
                "What are the peak hours for footfall today?",
                "Show occupancy trends for the last 24 hours",
                "Which zones have the highest foot traffic?",
            ])

        if any(kw in combined for kw in ["face", "person", "recognition", "enrolled"]):
            suggestions.extend([
                "How many persons are enrolled in the database?",
                "Show recent face recognition events",
                "List all blacklisted persons",
            ])

        if any(kw in combined for kw in ["vehicle", "plate", "anpr", "parking", "car"]):
            suggestions.extend([
                "Show recent ANPR detections",
                "Are there any blacklisted vehicles detected today?",
                "List vehicles currently parked",
            ])

        if any(kw in combined for kw in ["health", "system", "status", "overview"]):
            suggestions.extend([
                "Show me today's alert summary",
                "How many cameras are recording?",
                "What is the storage usage this week?",
            ])

        if any(kw in combined for kw in ["recording", "video", "footage"]):
            suggestions.extend([
                "Find recordings from the last hour",
                "Show event-triggered recordings today",
                "How much storage are recordings using?",
            ])

        # Deduplicate and limit
        seen: set[str] = set()
        unique: list[str] = []
        for s in suggestions:
            if s.lower() not in seen:
                seen.add(s.lower())
                unique.append(s)
        return unique[:4]

    # ------------------------------------------------------------------
    # Conversation Management
    # ------------------------------------------------------------------

    async def get_conversations(self, user_id: uuid.UUID) -> list[dict[str, Any]]:
        """List all conversations for a user from Redis.

        Returns:
            List of conversation summary dicts sorted by last activity.
        """
        redis = await self._get_redis()
        index_key = f"{_CONV_LIST_PREFIX}{user_id}"
        raw = await redis.get(index_key)

        if not raw:
            return []

        conversations: list[dict[str, Any]] = json.loads(raw)
        # Sort by updated_at descending
        conversations.sort(key=lambda c: c.get("updated_at", ""), reverse=True)
        return conversations

    async def get_conversation_detail(
        self, user_id: uuid.UUID, conversation_id: str
    ) -> dict[str, Any] | None:
        """Get full conversation detail with all messages.

        Args:
            user_id: The user's ID.
            conversation_id: The conversation ID to retrieve.

        Returns:
            Conversation detail dict or None if not found.
        """
        redis = await self._get_redis()
        conv_key = f"{_CONV_PREFIX}{user_id}:{conversation_id}"
        raw = await redis.get(conv_key)

        if not raw:
            return None

        messages: list[dict[str, Any]] = json.loads(raw)

        # Derive title from first user message
        title = "New Conversation"
        for msg in messages:
            if msg.get("role") == "user" and msg.get("content"):
                title = msg["content"][:80]
                if len(msg["content"]) > 80:
                    title += "..."
                break

        created_at = messages[0].get("timestamp", "") if messages else ""
        updated_at = messages[-1].get("timestamp", "") if messages else ""

        return {
            "id": conversation_id,
            "title": title,
            "messages": messages,
            "message_count": len(messages),
            "created_at": created_at,
            "updated_at": updated_at,
        }

    async def delete_conversation(
        self, user_id: uuid.UUID, conversation_id: str
    ) -> bool:
        """Delete a conversation from Redis.

        Removes both the conversation data and its index entry.

        Args:
            user_id: The user's ID.
            conversation_id: The conversation ID to delete.

        Returns:
            True if deleted, False if not found.
        """
        redis = await self._get_redis()

        # Delete conversation data
        conv_key = f"{_CONV_PREFIX}{user_id}:{conversation_id}"
        deleted = await redis.delete(conv_key)

        # Remove from index
        index_key = f"{_CONV_LIST_PREFIX}{user_id}"
        raw = await redis.get(index_key)
        if raw:
            conversations = json.loads(raw)
            conversations = [c for c in conversations if c.get("id") != conversation_id]
            await redis.set(index_key, json.dumps(conversations), ex=_CONV_TTL)

        return deleted > 0

    async def get_suggested_questions(
        self, db: AsyncSession, org_id: uuid.UUID
    ) -> list[dict[str, str]]:
        """Generate context-aware suggested questions based on current system state.

        Queries live system data to provide relevant starting questions.
        """
        suggestions: list[dict[str, str]] = []

        # Check for offline cameras
        offline_count = (await db.execute(
            select(func.count()).select_from(Camera).where(
                Camera.org_id == org_id, Camera.is_online.is_(False), Camera.is_active.is_(True)
            )
        )).scalar() or 0

        if offline_count > 0:
            suggestions.append({
                "question": f"There are {offline_count} cameras offline. Show me their details.",
                "category": "cameras",
                "icon": "camera-off",
            })

        # Check for unresolved alerts
        unresolved = (await db.execute(
            select(func.count()).select_from(Alert).where(
                Alert.org_id == org_id,
                Alert.status.in_([AlertStatus.NEW, AlertStatus.ESCALATED]),
            )
        )).scalar() or 0

        if unresolved > 0:
            suggestions.append({
                "question": f"Show me the {unresolved} unresolved alerts that need attention.",
                "category": "alerts",
                "icon": "bell-ring",
            })

        # Check for critical alerts today
        # Use naive datetime to match TIMESTAMP WITHOUT TIME ZONE columns
        today_start = _utcnow_naive().replace(
            hour=0, minute=0, second=0, microsecond=0
        )
        critical_today = (await db.execute(
            select(func.count()).select_from(Alert).where(
                Alert.org_id == org_id,
                Alert.created_at >= today_start,
                Alert.severity == RuleSeverity.CRITICAL,
            )
        )).scalar() or 0

        if critical_today > 0:
            suggestions.append({
                "question": f"Summarize the {critical_today} critical alerts from today.",
                "category": "alerts",
                "icon": "alert-triangle",
            })

        # Always include standard suggestions
        suggestions.extend([
            {
                "question": "Give me an overview of the system health.",
                "category": "system",
                "icon": "activity",
            },
            {
                "question": "What are the footfall trends for today?",
                "category": "analytics",
                "icon": "bar-chart-2",
            },
            {
                "question": "Show me the latest face recognition events.",
                "category": "faces",
                "icon": "users",
            },
            {
                "question": "List recent ANPR vehicle detections.",
                "category": "vehicles",
                "icon": "car",
            },
            {
                "question": "Which cameras have the most alerts this week?",
                "category": "alerts",
                "icon": "bell",
            },
        ])

        return suggestions[:8]

    # ------------------------------------------------------------------
    # Internal Helpers
    # ------------------------------------------------------------------

    async def _update_conversation_index(
        self,
        redis: Any,
        user_id: uuid.UUID,
        conversation_id: str,
        first_message: str,
        message_count: int,
    ) -> None:
        """Update the conversation list index in Redis.

        Maintains a compact list of conversation summaries per user
        for the sidebar listing.
        """
        index_key = f"{_CONV_LIST_PREFIX}{user_id}"
        raw = await redis.get(index_key)
        conversations: list[dict[str, Any]] = json.loads(raw) if raw else []

        now_iso = datetime.now(timezone.utc).isoformat()

        # Check if conversation already exists in the index
        existing = None
        for conv in conversations:
            if conv.get("id") == conversation_id:
                existing = conv
                break

        if existing:
            existing["last_message"] = first_message[:100]
            existing["message_count"] = message_count
            existing["updated_at"] = now_iso
        else:
            title = first_message[:80]
            if len(first_message) > 80:
                title += "..."
            conversations.append({
                "id": conversation_id,
                "title": title,
                "last_message": first_message[:100],
                "message_count": message_count,
                "created_at": now_iso,
                "updated_at": now_iso,
            })

        # Limit to 50 most recent conversations
        conversations.sort(key=lambda c: c.get("updated_at", ""), reverse=True)
        conversations = conversations[:50]

        await redis.set(index_key, json.dumps(conversations), ex=_CONV_TTL)
