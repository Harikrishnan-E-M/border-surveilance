"""Audit logging service for tracking administrative actions.

Stores audit events in Redis (with TTL-based retention) and provides
query capabilities for the admin audit trail endpoint.
"""

from __future__ import annotations

import json
import math
import uuid
from datetime import datetime, timezone
from typing import Any, Optional

import structlog

logger = structlog.stdlib.get_logger(__name__)

AUDIT_KEY_PREFIX = "audit:"
AUDIT_LIST_KEY = "audit:log"
AUDIT_RETENTION_SECONDS = 90 * 24 * 3600  # 90 days


async def _get_redis():
    """Get the Redis client."""
    from app.dependencies import get_redis
    return await get_redis()


async def log_action(
    org_id: str,
    user_id: str,
    action: str,
    resource_type: str,
    resource_id: Optional[str] = None,
    details: Optional[dict[str, Any]] = None,
    ip_address: Optional[str] = None,
) -> str:
    """Record an audit event.

    Args:
        org_id: Organization UUID string.
        user_id: User UUID string who performed the action.
        action: Action name (e.g. 'user.create', 'camera.delete').
        resource_type: Type of resource affected (e.g. 'user', 'camera').
        resource_id: Optional ID of the affected resource.
        details: Optional dict with extra context.
        ip_address: Optional IP address of the request.

    Returns:
        The audit event ID.
    """
    event_id = str(uuid.uuid4())
    event = {
        "id": event_id,
        "org_id": org_id,
        "user_id": user_id,
        "action": action,
        "resource_type": resource_type,
        "resource_id": resource_id,
        "details": details or {},
        "ip_address": ip_address,
        "timestamp": datetime.now(timezone.utc).isoformat(),
    }

    try:
        redis = await _get_redis()
        serialized = json.dumps(event)

        # Store individual event with TTL
        event_key = f"{AUDIT_KEY_PREFIX}{org_id}:{event_id}"
        await redis.set(event_key, serialized, ex=AUDIT_RETENTION_SECONDS)

        # Append to org-scoped audit list
        list_key = f"{AUDIT_LIST_KEY}:{org_id}"
        await redis.lpush(list_key, serialized)
        # Keep max 10,000 entries in the list
        await redis.ltrim(list_key, 0, 9999)

        logger.info(
            "Audit event logged",
            event_id=event_id,
            action=action,
            resource_type=resource_type,
        )
    except Exception as exc:
        logger.warning("Failed to write audit event to Redis", error=str(exc))

    return event_id


async def get_audit_logs(
    org_id: str,
    action: Optional[str] = None,
    user_id: Optional[str] = None,
    start_date: Optional[datetime] = None,
    end_date: Optional[datetime] = None,
    page: int = 1,
    page_size: int = 20,
) -> dict[str, Any]:
    """Query audit logs for an organization.

    Args:
        org_id: Organization UUID string.
        action: Optional filter by action name.
        user_id: Optional filter by user ID.
        start_date: Optional start of time range.
        end_date: Optional end of time range.
        page: Page number (1-indexed).
        page_size: Number of results per page.

    Returns:
        Dict with 'logs' list and 'meta' pagination info.
    """
    try:
        redis = await _get_redis()
        list_key = f"{AUDIT_LIST_KEY}:{org_id}"

        # Fetch all entries (up to 10k) and filter in memory
        raw_entries = await redis.lrange(list_key, 0, -1)

        events = []
        for raw in raw_entries:
            try:
                event = json.loads(raw)
            except (json.JSONDecodeError, TypeError):
                continue

            # Apply filters
            if action and event.get("action") != action:
                continue
            if user_id and event.get("user_id") != user_id:
                continue
            if start_date:
                event_ts = datetime.fromisoformat(event["timestamp"])
                if event_ts < start_date:
                    continue
            if end_date:
                event_ts = datetime.fromisoformat(event["timestamp"])
                if event_ts > end_date:
                    continue

            events.append(event)

        # Paginate
        total = len(events)
        total_pages = max(1, math.ceil(total / page_size))
        start_idx = (page - 1) * page_size
        page_events = events[start_idx : start_idx + page_size]

        return {
            "logs": page_events,
            "meta": {
                "page": page,
                "page_size": page_size,
                "total": total,
                "total_pages": total_pages,
            },
        }
    except Exception as exc:
        logger.error("Failed to query audit logs", error=str(exc))
        return {
            "logs": [],
            "meta": {
                "page": page,
                "page_size": page_size,
                "total": 0,
                "total_pages": 0,
            },
        }
