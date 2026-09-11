"""Third-party integration service for Slack, Teams, PagerDuty, and Jira.

Provides integration classes for each supported platform and an
IntegrationManager that routes events to all active integrations
for an organization. All HTTP calls are async via httpx.
"""

from __future__ import annotations

import json
import uuid
from datetime import datetime, timezone
from typing import Any, Optional

import httpx
import structlog
from sqlalchemy import and_, func, select
from sqlalchemy.ext.asyncio import AsyncSession

from app.exceptions import NotFoundError, ValidationError
from app.models.integration import IntegrationConfig, IntegrationType

logger = structlog.stdlib.get_logger(__name__)


# ── Slack Integration ────────────────────────────────────────────────────


class SlackIntegration:
    """Send messages and alerts to Slack channels via incoming webhooks."""

    def __init__(self, config: dict[str, Any]) -> None:
        self.webhook_url: str = config.get("webhook_url", "")
        self.channel: str = config.get("channel", "#general")
        self.username: str = config.get("username", "VisionAI Bot")
        self.icon_emoji: str = config.get("icon_emoji", ":shield:")

    async def send_message(
        self,
        channel: str | None = None,
        text: str = "",
        blocks: list[dict[str, Any]] | None = None,
    ) -> dict[str, Any]:
        """Send a message to a Slack channel.

        Args:
            channel: Target channel (overrides default).
            text: Fallback text for notifications.
            blocks: Slack Block Kit blocks for rich formatting.

        Returns:
            Dict with success status and optional error.
        """
        payload: dict[str, Any] = {
            "channel": channel or self.channel,
            "username": self.username,
            "icon_emoji": self.icon_emoji,
            "text": text,
        }
        if blocks:
            payload["blocks"] = blocks

        try:
            async with httpx.AsyncClient(timeout=15.0) as client:
                response = await client.post(
                    self.webhook_url,
                    json=payload,
                )
            success = 200 <= response.status_code < 300
            logger.info(
                "Slack message sent",
                channel=channel or self.channel,
                status_code=response.status_code,
                success=success,
            )
            return {
                "success": success,
                "status_code": response.status_code,
                "error": None if success else response.text[:500],
            }
        except Exception as exc:
            logger.error("Slack message failed", error=str(exc))
            return {"success": False, "status_code": None, "error": str(exc)}

    async def send_alert(self, alert: dict[str, Any]) -> dict[str, Any]:
        """Send a formatted alert notification to Slack.

        Args:
            alert: Alert data dictionary.

        Returns:
            Delivery result dict.
        """
        blocks = self.format_alert_blocks(alert)
        text = f"[{alert.get('severity', 'ALERT').upper()}] {alert.get('title', 'VisionAI Alert')}"
        return await self.send_message(text=text, blocks=blocks)

    @staticmethod
    def format_alert_blocks(alert: dict[str, Any]) -> list[dict[str, Any]]:
        """Format an alert as Slack Block Kit blocks.

        Args:
            alert: Alert data dictionary.

        Returns:
            List of Slack Block Kit block dicts.
        """
        severity = alert.get("severity", "unknown").upper()
        severity_emoji = {
            "CRITICAL": ":rotating_light:",
            "HIGH": ":warning:",
            "MEDIUM": ":large_orange_diamond:",
            "LOW": ":information_source:",
        }.get(severity, ":bell:")

        title = alert.get("title", "VisionAI Alert")
        alert_type = alert.get("alert_type", alert.get("event_type", "unknown"))
        camera = alert.get("camera_name", alert.get("camera_id", "N/A"))
        created_at = alert.get("created_at", datetime.now(timezone.utc).isoformat())

        blocks = [
            {
                "type": "header",
                "text": {
                    "type": "plain_text",
                    "text": f"{severity_emoji} {title}",
                    "emoji": True,
                },
            },
            {
                "type": "section",
                "fields": [
                    {"type": "mrkdwn", "text": f"*Severity:*\n{severity}"},
                    {"type": "mrkdwn", "text": f"*Type:*\n{alert_type}"},
                    {"type": "mrkdwn", "text": f"*Camera:*\n{camera}"},
                    {"type": "mrkdwn", "text": f"*Time:*\n{created_at}"},
                ],
            },
        ]

        description = alert.get("description")
        if description:
            blocks.append(
                {
                    "type": "section",
                    "text": {
                        "type": "mrkdwn",
                        "text": f"*Details:*\n{description[:500]}",
                    },
                }
            )

        blocks.append({"type": "divider"})
        blocks.append(
            {
                "type": "context",
                "elements": [
                    {
                        "type": "mrkdwn",
                        "text": f"Alert ID: `{alert.get('alert_id', alert.get('id', 'N/A'))}` | Sent by VisionAI",
                    }
                ],
            }
        )

        return blocks

    async def test_connection(self) -> dict[str, Any]:
        """Send a test message to verify the Slack webhook is working.

        Returns:
            Dict with success flag and optional error.
        """
        return await self.send_message(
            text="VisionAI integration test - Slack connection verified successfully.",
        )


# ── Microsoft Teams Integration ──────────────────────────────────────────


class TeamsIntegration:
    """Send messages and alerts to Microsoft Teams via incoming webhooks."""

    def __init__(self, config: dict[str, Any]) -> None:
        self.webhook_url: str = config.get("webhook_url", "")

    async def send_adaptive_card(
        self,
        webhook_url: str | None = None,
        card: dict[str, Any] | None = None,
    ) -> dict[str, Any]:
        """Send an Adaptive Card to a Teams channel.

        Args:
            webhook_url: Override webhook URL.
            card: Adaptive Card payload.

        Returns:
            Delivery result dict.
        """
        url = webhook_url or self.webhook_url
        payload = {
            "type": "message",
            "attachments": [
                {
                    "contentType": "application/vnd.microsoft.card.adaptive",
                    "contentUrl": None,
                    "content": card or {},
                }
            ],
        }

        try:
            async with httpx.AsyncClient(timeout=15.0) as client:
                response = await client.post(url, json=payload)
            success = 200 <= response.status_code < 300
            logger.info(
                "Teams adaptive card sent",
                status_code=response.status_code,
                success=success,
            )
            return {
                "success": success,
                "status_code": response.status_code,
                "error": None if success else response.text[:500],
            }
        except Exception as exc:
            logger.error("Teams message failed", error=str(exc))
            return {"success": False, "status_code": None, "error": str(exc)}

    async def send_alert(self, alert: dict[str, Any]) -> dict[str, Any]:
        """Send a formatted alert notification to Teams.

        Args:
            alert: Alert data dictionary.

        Returns:
            Delivery result dict.
        """
        card = self.format_alert_card(alert)
        return await self.send_adaptive_card(card=card)

    @staticmethod
    def format_alert_card(alert: dict[str, Any]) -> dict[str, Any]:
        """Format an alert as a Teams Adaptive Card.

        Args:
            alert: Alert data dictionary.

        Returns:
            Adaptive Card JSON structure.
        """
        severity = alert.get("severity", "unknown").upper()
        severity_colors = {
            "CRITICAL": "attention",
            "HIGH": "warning",
            "MEDIUM": "accent",
            "LOW": "good",
        }
        color = severity_colors.get(severity, "default")

        title = alert.get("title", "VisionAI Alert")
        alert_type = alert.get("alert_type", alert.get("event_type", "unknown"))
        camera = alert.get("camera_name", alert.get("camera_id", "N/A"))
        created_at = alert.get("created_at", datetime.now(timezone.utc).isoformat())
        description = alert.get("description", "")

        return {
            "$schema": "http://adaptivecards.io/schemas/adaptive-card.json",
            "type": "AdaptiveCard",
            "version": "1.4",
            "body": [
                {
                    "type": "TextBlock",
                    "text": title,
                    "size": "Large",
                    "weight": "Bolder",
                    "color": color,
                },
                {
                    "type": "FactSet",
                    "facts": [
                        {"title": "Severity", "value": severity},
                        {"title": "Type", "value": alert_type},
                        {"title": "Camera", "value": camera},
                        {"title": "Time", "value": created_at},
                    ],
                },
                {
                    "type": "TextBlock",
                    "text": description[:500] if description else "No additional details.",
                    "wrap": True,
                    "isSubtle": True,
                },
                {
                    "type": "TextBlock",
                    "text": f"Alert ID: {alert.get('alert_id', alert.get('id', 'N/A'))}",
                    "isSubtle": True,
                    "size": "Small",
                },
            ],
        }

    async def test_connection(self) -> dict[str, Any]:
        """Send a test Adaptive Card to verify the Teams webhook.

        Returns:
            Dict with success flag and optional error.
        """
        card = {
            "$schema": "http://adaptivecards.io/schemas/adaptive-card.json",
            "type": "AdaptiveCard",
            "version": "1.4",
            "body": [
                {
                    "type": "TextBlock",
                    "text": "VisionAI Integration Test",
                    "size": "Large",
                    "weight": "Bolder",
                },
                {
                    "type": "TextBlock",
                    "text": "Teams connection verified successfully.",
                    "wrap": True,
                },
            ],
        }
        return await self.send_adaptive_card(card=card)


# ── PagerDuty Integration ────────────────────────────────────────────────


class PagerDutyIntegration:
    """Create and resolve incidents in PagerDuty via the Events API v2."""

    EVENTS_URL = "https://events.pagerduty.com/v2/enqueue"

    def __init__(self, config: dict[str, Any]) -> None:
        self.routing_key: str = config.get("routing_key", "")
        self.service_id: str = config.get("service_id", "")

    async def create_incident(self, alert: dict[str, Any]) -> dict[str, Any]:
        """Create a PagerDuty incident from an alert.

        Args:
            alert: Alert data dictionary.

        Returns:
            Dict with success flag, dedup_key, and optional error.
        """
        severity = alert.get("severity", "warning").lower()
        pd_severity = {
            "critical": "critical",
            "high": "error",
            "medium": "warning",
            "low": "info",
        }.get(severity, "warning")

        dedup_key = f"visionai-{alert.get('alert_id', alert.get('id', uuid.uuid4()))}"

        payload = {
            "routing_key": self.routing_key,
            "event_action": "trigger",
            "dedup_key": dedup_key,
            "payload": {
                "summary": f"[VisionAI] {alert.get('title', 'Alert')}",
                "source": f"visionai-camera-{alert.get('camera_name', alert.get('camera_id', 'unknown'))}",
                "severity": pd_severity,
                "timestamp": alert.get("created_at", datetime.now(timezone.utc).isoformat()),
                "component": alert.get("alert_type", alert.get("event_type", "unknown")),
                "group": alert.get("zone_name", "default"),
                "class": alert.get("alert_type", alert.get("event_type", "alert")),
                "custom_details": {
                    "alert_id": str(alert.get("alert_id", alert.get("id", ""))),
                    "camera_name": alert.get("camera_name", ""),
                    "description": alert.get("description", ""),
                },
            },
        }

        try:
            async with httpx.AsyncClient(timeout=15.0) as client:
                response = await client.post(self.EVENTS_URL, json=payload)
            success = 200 <= response.status_code < 300
            result = response.json() if success else {}
            logger.info(
                "PagerDuty incident created",
                dedup_key=dedup_key,
                status_code=response.status_code,
                success=success,
            )
            return {
                "success": success,
                "dedup_key": dedup_key,
                "status_code": response.status_code,
                "message": result.get("message"),
                "error": None if success else response.text[:500],
            }
        except Exception as exc:
            logger.error("PagerDuty incident creation failed", error=str(exc))
            return {"success": False, "dedup_key": dedup_key, "error": str(exc)}

    async def resolve_incident(self, alert_id: str) -> dict[str, Any]:
        """Resolve a PagerDuty incident by alert ID.

        Args:
            alert_id: The VisionAI alert ID used as the dedup key basis.

        Returns:
            Dict with success flag and optional error.
        """
        dedup_key = f"visionai-{alert_id}"

        payload = {
            "routing_key": self.routing_key,
            "event_action": "resolve",
            "dedup_key": dedup_key,
        }

        try:
            async with httpx.AsyncClient(timeout=15.0) as client:
                response = await client.post(self.EVENTS_URL, json=payload)
            success = 200 <= response.status_code < 300
            logger.info(
                "PagerDuty incident resolved",
                dedup_key=dedup_key,
                status_code=response.status_code,
                success=success,
            )
            return {
                "success": success,
                "dedup_key": dedup_key,
                "status_code": response.status_code,
                "error": None if success else response.text[:500],
            }
        except Exception as exc:
            logger.error("PagerDuty incident resolve failed", error=str(exc))
            return {"success": False, "dedup_key": dedup_key, "error": str(exc)}

    async def test_connection(self) -> dict[str, Any]:
        """Test the PagerDuty integration by sending a trigger and immediately resolving.

        Returns:
            Dict with success flag and optional error.
        """
        test_alert = {
            "id": f"test-{uuid.uuid4()}",
            "title": "VisionAI Integration Test",
            "severity": "info",
            "alert_type": "test",
            "camera_name": "Test Camera",
            "description": "This is a test incident from VisionAI. It will be auto-resolved.",
            "created_at": datetime.now(timezone.utc).isoformat(),
        }
        create_result = await self.create_incident(test_alert)

        if create_result.get("success"):
            await self.resolve_incident(str(test_alert["id"]))

        return create_result


# ── Jira Integration ─────────────────────────────────────────────────────


class JiraIntegration:
    """Create and update Jira issues from VisionAI alerts."""

    def __init__(self, config: dict[str, Any]) -> None:
        self.base_url: str = config.get("base_url", "").rstrip("/")
        self.email: str = config.get("email", "")
        self.api_token: str = config.get("api_token", "")
        self.project_key: str = config.get("project_key", "")
        self.issue_type: str = config.get("issue_type", "Bug")

    def _get_auth(self) -> tuple[str, str]:
        """Return HTTP basic auth credentials."""
        return (self.email, self.api_token)

    async def create_issue(
        self,
        alert: dict[str, Any],
        project_key: str | None = None,
    ) -> dict[str, Any]:
        """Create a Jira issue from an alert.

        Args:
            alert: Alert data dictionary.
            project_key: Override project key.

        Returns:
            Dict with success flag, issue key, and optional error.
        """
        key = project_key or self.project_key
        severity = alert.get("severity", "medium").upper()
        priority_map = {
            "CRITICAL": "Highest",
            "HIGH": "High",
            "MEDIUM": "Medium",
            "LOW": "Low",
        }
        priority = priority_map.get(severity, "Medium")

        title = alert.get("title", "VisionAI Alert")
        description_parts = [
            f"h3. {title}",
            "",
            f"*Severity:* {severity}",
            f"*Type:* {alert.get('alert_type', alert.get('event_type', 'unknown'))}",
            f"*Camera:* {alert.get('camera_name', alert.get('camera_id', 'N/A'))}",
            f"*Time:* {alert.get('created_at', '')}",
            "",
        ]
        if alert.get("description"):
            description_parts.extend(["*Details:*", alert["description"]])
        description_parts.extend(
            [
                "",
                f"_Alert ID: {alert.get('alert_id', alert.get('id', 'N/A'))}_",
                "_Created by VisionAI Surveillance Platform_",
            ]
        )

        payload = {
            "fields": {
                "project": {"key": key},
                "summary": f"[VisionAI] {title}",
                "description": "\n".join(description_parts),
                "issuetype": {"name": self.issue_type},
                "priority": {"name": priority},
                "labels": ["visionai", "auto-generated", severity.lower()],
            }
        }

        url = f"{self.base_url}/rest/api/2/issue"

        try:
            async with httpx.AsyncClient(timeout=15.0) as client:
                response = await client.post(
                    url,
                    json=payload,
                    auth=self._get_auth(),
                    headers={"Content-Type": "application/json"},
                )

            if 200 <= response.status_code < 300:
                data = response.json()
                issue_key = data.get("key", "")
                logger.info(
                    "Jira issue created",
                    issue_key=issue_key,
                    project=key,
                )
                return {
                    "success": True,
                    "issue_key": issue_key,
                    "issue_id": data.get("id"),
                    "self_url": data.get("self"),
                    "error": None,
                }
            else:
                error_text = response.text[:500]
                logger.warning(
                    "Jira issue creation failed",
                    status_code=response.status_code,
                    error=error_text,
                )
                return {
                    "success": False,
                    "issue_key": None,
                    "status_code": response.status_code,
                    "error": error_text,
                }
        except Exception as exc:
            logger.error("Jira API call failed", error=str(exc))
            return {"success": False, "issue_key": None, "error": str(exc)}

    async def update_issue(
        self,
        issue_key: str,
        data: dict[str, Any],
    ) -> dict[str, Any]:
        """Update a Jira issue.

        Args:
            issue_key: Jira issue key (e.g., PROJ-123).
            data: Fields to update.

        Returns:
            Dict with success flag and optional error.
        """
        url = f"{self.base_url}/rest/api/2/issue/{issue_key}"

        payload = {"fields": data}

        try:
            async with httpx.AsyncClient(timeout=15.0) as client:
                response = await client.put(
                    url,
                    json=payload,
                    auth=self._get_auth(),
                    headers={"Content-Type": "application/json"},
                )
            success = 200 <= response.status_code < 300
            logger.info(
                "Jira issue updated",
                issue_key=issue_key,
                status_code=response.status_code,
                success=success,
            )
            return {
                "success": success,
                "issue_key": issue_key,
                "error": None if success else response.text[:500],
            }
        except Exception as exc:
            logger.error("Jira issue update failed", error=str(exc))
            return {"success": False, "issue_key": issue_key, "error": str(exc)}

    async def test_connection(self) -> dict[str, Any]:
        """Test the Jira integration by checking API connectivity.

        Returns:
            Dict with success flag and optional error.
        """
        url = f"{self.base_url}/rest/api/2/myself"

        try:
            async with httpx.AsyncClient(timeout=15.0) as client:
                response = await client.get(
                    url,
                    auth=self._get_auth(),
                    headers={"Content-Type": "application/json"},
                )

            if 200 <= response.status_code < 300:
                user_data = response.json()
                logger.info(
                    "Jira connection test succeeded",
                    user=user_data.get("displayName", ""),
                )
                return {
                    "success": True,
                    "user": user_data.get("displayName"),
                    "email": user_data.get("emailAddress"),
                    "error": None,
                }
            else:
                return {
                    "success": False,
                    "status_code": response.status_code,
                    "error": response.text[:500],
                }
        except Exception as exc:
            logger.error("Jira connection test failed", error=str(exc))
            return {"success": False, "error": str(exc)}


# ── Integration Factory ──────────────────────────────────────────────────


def _get_integration_instance(
    integration_type: str,
    config: dict[str, Any],
) -> SlackIntegration | TeamsIntegration | PagerDutyIntegration | JiraIntegration | None:
    """Instantiate the appropriate integration class.

    Args:
        integration_type: The integration type string.
        config: Integration-specific configuration.

    Returns:
        An integration instance or None for unsupported types.
    """
    if integration_type == IntegrationType.SLACK.value:
        return SlackIntegration(config)
    elif integration_type == IntegrationType.TEAMS.value:
        return TeamsIntegration(config)
    elif integration_type == IntegrationType.PAGERDUTY.value:
        return PagerDutyIntegration(config)
    elif integration_type == IntegrationType.JIRA.value:
        return JiraIntegration(config)
    else:
        return None


# ── Integration Config CRUD ──────────────────────────────────────────────


async def create_integration(
    db: AsyncSession,
    org_id: uuid.UUID,
    data: dict[str, Any],
    created_by: uuid.UUID | None = None,
) -> IntegrationConfig:
    """Create a new integration configuration.

    Args:
        db: Async database session.
        org_id: Owning organization ID.
        data: Integration config from the request body.
        created_by: Creator user ID.

    Returns:
        The newly created IntegrationConfig instance.
    """
    integration = IntegrationConfig(
        id=uuid.uuid4(),
        org_id=org_id,
        integration_type=IntegrationType(data["integration_type"]),
        name=data["name"],
        config=data.get("config", {}),
        is_active=True,
        created_by=created_by,
    )
    db.add(integration)
    await db.flush()

    logger.info(
        "Integration config created",
        integration_id=str(integration.id),
        type=integration.integration_type.value,
        name=integration.name,
        org_id=str(org_id),
    )
    return integration


async def update_integration(
    db: AsyncSession,
    integration_id: uuid.UUID,
    data: dict[str, Any],
    org_id: uuid.UUID | None = None,
) -> IntegrationConfig:
    """Update an existing integration configuration.

    Args:
        db: Async database session.
        integration_id: ID of the integration to update.
        data: Fields to update.
        org_id: Optional organization filter.

    Returns:
        The updated IntegrationConfig instance.

    Raises:
        NotFoundError: If the integration does not exist.
    """
    integration = await _get_integration(db, integration_id, org_id)

    if "name" in data and data["name"] is not None:
        integration.name = data["name"]
    if "config" in data and data["config"] is not None:
        integration.config = data["config"]
    if "is_active" in data and data["is_active"] is not None:
        integration.is_active = data["is_active"]

    await db.flush()

    logger.info("Integration config updated", integration_id=str(integration_id))
    return integration


async def delete_integration(
    db: AsyncSession,
    integration_id: uuid.UUID,
    org_id: uuid.UUID | None = None,
) -> IntegrationConfig:
    """Soft-delete an integration configuration.

    Args:
        db: Async database session.
        integration_id: ID of the integration to delete.
        org_id: Optional organization filter.

    Returns:
        The soft-deleted IntegrationConfig instance.

    Raises:
        NotFoundError: If the integration does not exist.
    """
    integration = await _get_integration(db, integration_id, org_id)
    integration.is_deleted = True
    integration.is_active = False
    await db.flush()

    logger.info("Integration config soft-deleted", integration_id=str(integration_id))
    return integration


async def list_integrations(
    db: AsyncSession,
    org_id: uuid.UUID,
    page: int = 1,
    page_size: int = 20,
) -> tuple[list[IntegrationConfig], int]:
    """List active (non-deleted) integrations for an organization.

    Args:
        db: Async database session.
        org_id: Organization to list integrations for.
        page: Page number.
        page_size: Items per page.

    Returns:
        Tuple of (integration list, total count).
    """
    base_filter = and_(
        IntegrationConfig.org_id == org_id,
        IntegrationConfig.is_deleted.is_(False),
    )

    count_q = select(func.count()).select_from(IntegrationConfig).where(base_filter)
    total = (await db.execute(count_q)).scalar() or 0

    query = (
        select(IntegrationConfig)
        .where(base_filter)
        .order_by(IntegrationConfig.created_at.desc())
        .offset((page - 1) * page_size)
        .limit(page_size)
    )
    result = await db.execute(query)
    integrations = list(result.scalars().all())

    return integrations, total


async def get_integration(
    db: AsyncSession,
    integration_id: uuid.UUID,
    org_id: uuid.UUID | None = None,
) -> IntegrationConfig:
    """Retrieve a single integration by ID.

    Args:
        db: Async database session.
        integration_id: Integration ID.
        org_id: Optional organization filter.

    Returns:
        The IntegrationConfig instance.

    Raises:
        NotFoundError: If the integration does not exist.
    """
    return await _get_integration(db, integration_id, org_id)


async def test_integration(
    db: AsyncSession,
    integration_id: uuid.UUID,
    org_id: uuid.UUID | None = None,
) -> dict[str, Any]:
    """Test an integration connection.

    Args:
        db: Async database session.
        integration_id: Integration to test.
        org_id: Optional organization filter.

    Returns:
        Dict with success flag and optional error.
    """
    integration = await _get_integration(db, integration_id, org_id)

    instance = _get_integration_instance(
        integration.integration_type.value,
        integration.config,
    )

    if instance is None:
        return {
            "success": False,
            "error": f"Unsupported integration type: {integration.integration_type.value}",
        }

    try:
        result = await instance.test_connection()
        logger.info(
            "Integration test completed",
            integration_id=str(integration_id),
            type=integration.integration_type.value,
            success=result.get("success"),
        )
        return result
    except Exception as exc:
        logger.error(
            "Integration test failed",
            integration_id=str(integration_id),
            error=str(exc),
        )
        return {"success": False, "error": str(exc)}


# ── Integration Manager (Event Routing) ──────────────────────────────────


class IntegrationManager:
    """Routes events to all active integrations for an organization.

    The manager loads all active integrations from the database and
    dispatches the event payload to each one based on the integration
    type. Supports Slack, Teams, PagerDuty, and Jira.
    """

    def __init__(self, db: AsyncSession) -> None:
        self.db = db

    async def route_event(
        self,
        org_id: uuid.UUID,
        event_type: str,
        payload: dict[str, Any],
    ) -> dict[str, Any]:
        """Dispatch an event to all active integrations for the organization.

        Args:
            org_id: Organization to dispatch for.
            event_type: The event type string.
            payload: The event payload.

        Returns:
            Dict with per-integration results.
        """
        query = select(IntegrationConfig).where(
            and_(
                IntegrationConfig.org_id == org_id,
                IntegrationConfig.is_active.is_(True),
                IntegrationConfig.is_deleted.is_(False),
            )
        )
        result = await self.db.execute(query)
        integrations = list(result.scalars().all())

        results: dict[str, Any] = {}

        for integration in integrations:
            integration_key = f"{integration.integration_type.value}:{integration.id}"

            try:
                instance = _get_integration_instance(
                    integration.integration_type.value,
                    integration.config,
                )
                if instance is None:
                    results[integration_key] = {
                        "success": False,
                        "error": "Unsupported integration type",
                    }
                    continue

                # Route based on event type and integration capability
                if event_type.startswith("alert."):
                    if isinstance(instance, (SlackIntegration, TeamsIntegration)):
                        result_data = await instance.send_alert(payload)
                    elif isinstance(instance, PagerDutyIntegration):
                        if event_type == "alert.resolved":
                            alert_id = payload.get("alert_id", payload.get("id", ""))
                            result_data = await instance.resolve_incident(str(alert_id))
                        else:
                            result_data = await instance.create_incident(payload)
                    elif isinstance(instance, JiraIntegration):
                        if event_type == "alert.created":
                            result_data = await instance.create_issue(payload)
                        else:
                            result_data = {"success": True, "skipped": True}
                    else:
                        result_data = {"success": True, "skipped": True}
                elif isinstance(instance, (SlackIntegration, TeamsIntegration)):
                    # For non-alert events, send a simple message to Slack/Teams
                    if isinstance(instance, SlackIntegration):
                        text = f"[{event_type}] {json.dumps(payload, default=str)[:500]}"
                        result_data = await instance.send_message(text=text)
                    else:
                        card = {
                            "$schema": "http://adaptivecards.io/schemas/adaptive-card.json",
                            "type": "AdaptiveCard",
                            "version": "1.4",
                            "body": [
                                {
                                    "type": "TextBlock",
                                    "text": f"Event: {event_type}",
                                    "size": "Medium",
                                    "weight": "Bolder",
                                },
                                {
                                    "type": "TextBlock",
                                    "text": json.dumps(payload, default=str, indent=2)[:1000],
                                    "wrap": True,
                                    "fontType": "Monospace",
                                    "size": "Small",
                                },
                            ],
                        }
                        result_data = await instance.send_adaptive_card(card=card)
                else:
                    result_data = {"success": True, "skipped": True}

                results[integration_key] = result_data

            except Exception as exc:
                logger.error(
                    "Integration event routing failed",
                    integration_id=str(integration.id),
                    type=integration.integration_type.value,
                    event_type=event_type,
                    error=str(exc),
                )
                results[integration_key] = {
                    "success": False,
                    "error": str(exc),
                }

        success_count = sum(
            1
            for r in results.values()
            if r.get("success") and not r.get("skipped")
        )

        logger.info(
            "Event routed to integrations",
            org_id=str(org_id),
            event_type=event_type,
            total_integrations=len(integrations),
            successful=success_count,
        )

        return {
            "integrations_notified": len(results),
            "successful": success_count,
            "results": results,
        }


# ── Helpers ──────────────────────────────────────────────────────────────


async def _get_integration(
    db: AsyncSession,
    integration_id: uuid.UUID,
    org_id: uuid.UUID | None = None,
) -> IntegrationConfig:
    """Retrieve an integration by ID with optional org filter.

    Excludes soft-deleted integrations.

    Raises:
        NotFoundError: If the integration does not exist.
    """
    conditions = [
        IntegrationConfig.id == integration_id,
        IntegrationConfig.is_deleted.is_(False),
    ]
    if org_id:
        conditions.append(IntegrationConfig.org_id == org_id)

    result = await db.execute(
        select(IntegrationConfig).where(and_(*conditions))
    )
    integration = result.scalar_one_or_none()

    if integration is None:
        raise NotFoundError(resource="IntegrationConfig", identifier=integration_id)

    return integration
