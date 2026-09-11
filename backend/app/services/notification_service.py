"""Notification service for multi-channel alert delivery.

Supports WhatsApp (via Twilio/Meta API), Telegram (Bot API), Email
(SMTP/aiosmtplib), SMS (Twilio), and generic Webhook (POST with HMAC
signature). Provides channel CRUD and per-channel connectivity testing.
"""

from __future__ import annotations

import hashlib
import hmac
import json
import uuid
from datetime import datetime, timezone
from email.mime.multipart import MIMEMultipart
from email.mime.text import MIMEText
from typing import Any, Optional

import structlog

from app.config import get_settings
from app.exceptions import NotFoundError, ValidationError

logger = structlog.stdlib.get_logger(__name__)


# ── Channel Storage (Redis-backed) ──────────────────────────────────────


async def _get_redis():
    """Get the Redis client."""
    from app.dependencies import get_redis
    return await get_redis()


def _channel_key(org_id: uuid.UUID) -> str:
    """Return the Redis hash key for an org's notification channels."""
    return f"notification_channels:{org_id}"


async def get_channels(org_id: uuid.UUID) -> list[dict[str, Any]]:
    """Retrieve all notification channels for an organization.

    Args:
        org_id: Organization to list channels for.

    Returns:
        List of channel configuration dicts.
    """
    try:
        redis = await _get_redis()
        key = _channel_key(org_id)
        raw = await redis.hgetall(key)
        channels = []
        for channel_id, data in raw.items():
            channel = json.loads(data)
            channel["id"] = channel_id
            channels.append(channel)
        return channels
    except Exception as exc:
        logger.error("Failed to fetch channels", error=str(exc))
        return []


async def create_channel(
    org_id: uuid.UUID,
    channel_type: str,
    name: str,
    config: dict[str, Any],
) -> dict[str, Any]:
    """Create a new notification channel.

    Args:
        org_id: Organization owning the channel.
        channel_type: One of 'email', 'sms', 'whatsapp', 'telegram', 'webhook'.
        name: Human-readable channel name.
        config: Channel-specific configuration (recipients, URLs, tokens, etc.).

    Returns:
        The created channel configuration dict.

    Raises:
        ValidationError: If the channel type is not supported.
    """
    valid_types = ("email", "sms", "whatsapp", "telegram", "webhook")
    if channel_type not in valid_types:
        raise ValidationError(
            message=f"Invalid channel type '{channel_type}'. Must be one of: {', '.join(valid_types)}",
            code="INVALID_CHANNEL_TYPE",
        )

    channel_id = str(uuid.uuid4())
    channel = {
        "id": channel_id,
        "type": channel_type,
        "name": name,
        "config": config,
        "is_active": True,
        "created_at": datetime.now(timezone.utc).isoformat(),
    }

    try:
        redis = await _get_redis()
        key = _channel_key(org_id)
        await redis.hset(key, channel_id, json.dumps(channel))
    except Exception as exc:
        logger.error("Failed to create channel", error=str(exc))
        raise

    logger.info("Notification channel created", channel_id=channel_id, type=channel_type, name=name)
    return channel


async def update_channel(
    org_id: uuid.UUID,
    channel_id: str,
    name: Optional[str] = None,
    config: Optional[dict[str, Any]] = None,
    is_active: Optional[bool] = None,
) -> dict[str, Any]:
    """Update an existing notification channel.

    Args:
        org_id: Organization owning the channel.
        channel_id: Channel to update.
        name: Optional updated name.
        config: Optional updated configuration.
        is_active: Optional active status.

    Returns:
        The updated channel configuration dict.

    Raises:
        NotFoundError: If the channel does not exist.
    """
    redis = await _get_redis()
    key = _channel_key(org_id)
    raw = await redis.hget(key, channel_id)

    if raw is None:
        raise NotFoundError(resource="NotificationChannel", identifier=channel_id)

    channel = json.loads(raw)

    if name is not None:
        channel["name"] = name
    if config is not None:
        channel["config"] = config
    if is_active is not None:
        channel["is_active"] = is_active

    channel["updated_at"] = datetime.now(timezone.utc).isoformat()
    await redis.hset(key, channel_id, json.dumps(channel))

    logger.info("Notification channel updated", channel_id=channel_id)
    return channel


async def delete_channel(
    org_id: uuid.UUID,
    channel_id: str,
) -> bool:
    """Delete a notification channel.

    Args:
        org_id: Organization owning the channel.
        channel_id: Channel to delete.

    Returns:
        True if the channel was deleted.

    Raises:
        NotFoundError: If the channel does not exist.
    """
    redis = await _get_redis()
    key = _channel_key(org_id)

    exists = await redis.hexists(key, channel_id)
    if not exists:
        raise NotFoundError(resource="NotificationChannel", identifier=channel_id)

    await redis.hdel(key, channel_id)
    logger.info("Notification channel deleted", channel_id=channel_id)
    return True


async def test_channel(
    org_id: uuid.UUID,
    channel_id: str,
) -> dict[str, Any]:
    """Test a notification channel by sending a test message.

    Args:
        org_id: Organization owning the channel.
        channel_id: Channel to test.

    Returns:
        Dict with success status and optional error message.
    """
    redis = await _get_redis()
    key = _channel_key(org_id)
    raw = await redis.hget(key, channel_id)

    if raw is None:
        raise NotFoundError(resource="NotificationChannel", identifier=channel_id)

    channel = json.loads(raw)
    channel_type = channel["type"]
    config = channel.get("config", {})

    test_message = "This is a test notification from VisionAI."
    test_subject = "VisionAI Test Notification"

    try:
        if channel_type == "email":
            recipients = config.get("recipients", [])
            if not recipients:
                return {"success": False, "error": "No recipients configured"}
            await send_email(
                to=recipients,
                subject=test_subject,
                body=test_message,
            )

        elif channel_type == "sms":
            phone_numbers = config.get("phone_numbers", [])
            if not phone_numbers:
                return {"success": False, "error": "No phone numbers configured"}
            await send_sms(
                to=phone_numbers[0],
                message=test_message,
            )

        elif channel_type == "whatsapp":
            phone_number = config.get("phone_number")
            if not phone_number:
                return {"success": False, "error": "No phone number configured"}
            await send_whatsapp(
                to=phone_number,
                message=test_message,
            )

        elif channel_type == "telegram":
            chat_id = config.get("chat_id")
            if not chat_id:
                return {"success": False, "error": "No chat ID configured"}
            await send_telegram(
                chat_id=chat_id,
                message=test_message,
                bot_token=config.get("bot_token"),
            )

        elif channel_type == "webhook":
            url = config.get("url")
            if not url:
                return {"success": False, "error": "No webhook URL configured"}
            await send_webhook(
                url=url,
                payload={"event": "test", "message": test_message},
                secret=config.get("secret"),
                headers=config.get("headers"),
            )

        else:
            return {"success": False, "error": f"Unknown channel type: {channel_type}"}

        logger.info("Channel test succeeded", channel_id=channel_id, type=channel_type)
        return {"success": True, "error": None}

    except Exception as exc:
        logger.error("Channel test failed", channel_id=channel_id, error=str(exc))
        return {"success": False, "error": str(exc)}


# ── Email (SMTP / aiosmtplib) ───────────────────────────────────────────


async def send_email(
    to: list[str],
    subject: str,
    body: str,
    html_body: Optional[str] = None,
    from_email: Optional[str] = None,
    from_name: Optional[str] = None,
) -> bool:
    """Send an email via SMTP using aiosmtplib.

    Args:
        to: List of recipient email addresses.
        subject: Email subject line.
        body: Plain text body.
        html_body: Optional HTML body.
        from_email: Optional sender address (defaults to settings).
        from_name: Optional sender name (defaults to settings).

    Returns:
        True if the email was sent successfully.

    Raises:
        Exception: If sending fails.
    """
    settings = get_settings()

    if not settings.smtp_configured:
        logger.warning("SMTP not configured, skipping email send")
        raise RuntimeError("SMTP is not configured")

    sender = from_email or settings.SMTP_FROM_EMAIL
    sender_name = from_name or settings.SMTP_FROM_NAME

    msg = MIMEMultipart("alternative")
    msg["Subject"] = subject
    msg["From"] = f"{sender_name} <{sender}>"
    msg["To"] = ", ".join(to)

    msg.attach(MIMEText(body, "plain", "utf-8"))

    if html_body:
        msg.attach(MIMEText(html_body, "html", "utf-8"))

    try:
        import aiosmtplib

        kwargs: dict[str, Any] = {
            "hostname": settings.SMTP_HOST,
            "port": settings.SMTP_PORT,
            "username": settings.SMTP_USERNAME,
            "password": settings.SMTP_PASSWORD,
        }

        if settings.SMTP_SSL:
            kwargs["use_tls"] = True
        elif settings.SMTP_TLS:
            kwargs["start_tls"] = True

        await aiosmtplib.send(msg, **kwargs)

        logger.info("Email sent", to=to, subject=subject)
        return True

    except ImportError:
        logger.warning("aiosmtplib not installed, attempting synchronous send")
        import smtplib

        with smtplib.SMTP(settings.SMTP_HOST, settings.SMTP_PORT) as server:
            if settings.SMTP_TLS:
                server.starttls()
            if settings.SMTP_USERNAME and settings.SMTP_PASSWORD:
                server.login(settings.SMTP_USERNAME, settings.SMTP_PASSWORD)
            server.sendmail(sender, to, msg.as_string())

        logger.info("Email sent (sync fallback)", to=to, subject=subject)
        return True


# ── SMS (Twilio) ─────────────────────────────────────────────────────────


async def send_sms(
    to: str,
    message: str,
) -> bool:
    """Send an SMS via Twilio.

    Args:
        to: Recipient phone number in E.164 format.
        message: SMS body text.

    Returns:
        True if the SMS was sent successfully.

    Raises:
        RuntimeError: If Twilio is not configured.
    """
    settings = get_settings()

    if not settings.twilio_configured:
        raise RuntimeError("Twilio SMS is not configured")

    import httpx

    url = f"https://api.twilio.com/2010-04-01/Accounts/{settings.TWILIO_ACCOUNT_SID}/Messages.json"

    async with httpx.AsyncClient() as client:
        response = await client.post(
            url,
            auth=(settings.TWILIO_ACCOUNT_SID, settings.TWILIO_AUTH_TOKEN),
            data={
                "To": to,
                "From": settings.TWILIO_FROM_NUMBER,
                "Body": message,
            },
            timeout=30.0,
        )
        response.raise_for_status()

    logger.info("SMS sent", to=to)
    return True


# ── WhatsApp (Twilio / Meta API) ────────────────────────────────────────


async def send_whatsapp(
    to: str,
    message: str,
) -> bool:
    """Send a WhatsApp message via the Twilio WhatsApp API.

    The sender number must be a Twilio WhatsApp-enabled number.

    Args:
        to: Recipient phone number in E.164 format.
        message: Message text.

    Returns:
        True if the message was sent successfully.

    Raises:
        RuntimeError: If Twilio is not configured.
    """
    settings = get_settings()

    if not settings.twilio_configured:
        raise RuntimeError("Twilio/WhatsApp is not configured")

    import httpx

    url = f"https://api.twilio.com/2010-04-01/Accounts/{settings.TWILIO_ACCOUNT_SID}/Messages.json"

    whatsapp_to = f"whatsapp:{to}" if not to.startswith("whatsapp:") else to
    whatsapp_from = f"whatsapp:{settings.TWILIO_FROM_NUMBER}"

    async with httpx.AsyncClient() as client:
        response = await client.post(
            url,
            auth=(settings.TWILIO_ACCOUNT_SID, settings.TWILIO_AUTH_TOKEN),
            data={
                "To": whatsapp_to,
                "From": whatsapp_from,
                "Body": message,
            },
            timeout=30.0,
        )
        response.raise_for_status()

    logger.info("WhatsApp message sent", to=to)
    return True


# ── Telegram (Bot API) ──────────────────────────────────────────────────


async def send_telegram(
    chat_id: str,
    message: str,
    bot_token: Optional[str] = None,
    parse_mode: str = "HTML",
) -> bool:
    """Send a Telegram message via the Bot API.

    Args:
        chat_id: Target chat ID (user, group, or channel).
        message: Message text (supports HTML by default).
        bot_token: Optional bot token override (defaults to settings).
        parse_mode: Parse mode for the message ('HTML' or 'Markdown').

    Returns:
        True if the message was sent successfully.

    Raises:
        RuntimeError: If Telegram bot is not configured.
    """
    settings = get_settings()
    token = bot_token or settings.TELEGRAM_BOT_TOKEN

    if not token:
        raise RuntimeError("Telegram bot token is not configured")

    import httpx

    url = f"https://api.telegram.org/bot{token}/sendMessage"

    async with httpx.AsyncClient() as client:
        response = await client.post(
            url,
            json={
                "chat_id": chat_id,
                "text": message,
                "parse_mode": parse_mode,
            },
            timeout=30.0,
        )
        response.raise_for_status()
        result = response.json()

        if not result.get("ok"):
            error_desc = result.get("description", "Unknown error")
            raise RuntimeError(f"Telegram API error: {error_desc}")

    logger.info("Telegram message sent", chat_id=chat_id)
    return True


# ── Webhook (POST with HMAC) ────────────────────────────────────────────


async def send_webhook(
    url: str,
    payload: dict[str, Any],
    secret: Optional[str] = None,
    headers: Optional[dict[str, str]] = None,
) -> bool:
    """Send a webhook POST request with optional HMAC-SHA256 signature.

    The signature is computed over the JSON-encoded payload body and
    included in the ``X-VisionAI-Signature`` header.

    Args:
        url: Webhook endpoint URL.
        payload: JSON-serializable payload dict.
        secret: Optional HMAC secret for signing the payload.
        headers: Optional additional HTTP headers.

    Returns:
        True if the webhook was delivered (2xx response).

    Raises:
        Exception: If delivery fails.
    """
    import httpx

    body = json.dumps(payload, separators=(",", ":"), sort_keys=True)

    request_headers = {
        "Content-Type": "application/json",
        "User-Agent": "VisionAI-Webhook/1.0",
    }

    if secret:
        signature = hmac.new(
            secret.encode("utf-8"),
            body.encode("utf-8"),
            hashlib.sha256,
        ).hexdigest()
        request_headers["X-VisionAI-Signature"] = f"sha256={signature}"

    if headers:
        request_headers.update(headers)

    async with httpx.AsyncClient() as client:
        response = await client.post(
            url,
            content=body,
            headers=request_headers,
            timeout=30.0,
        )
        response.raise_for_status()

    logger.info(
        "Webhook delivered",
        url=url,
        status_code=response.status_code,
    )
    return True


# ── Unified Dispatcher ──────────────────────────────────────────────────


async def send_notification(
    org_id: uuid.UUID,
    channel_id: str,
    subject: str,
    message: str,
    html_message: Optional[str] = None,
    payload: Optional[dict[str, Any]] = None,
) -> dict[str, Any]:
    """Send a notification through a specific channel.

    Loads the channel configuration and dispatches to the appropriate
    send method.

    Args:
        org_id: Organization owning the channel.
        channel_id: Target notification channel.
        subject: Notification subject (used for email).
        message: Plain text message body.
        html_message: Optional HTML message (used for email).
        payload: Optional structured payload (used for webhooks).

    Returns:
        Dict with success status and optional error.
    """
    redis = await _get_redis()
    key = _channel_key(org_id)
    raw = await redis.hget(key, channel_id)

    if raw is None:
        return {"success": False, "error": f"Channel {channel_id} not found"}

    channel = json.loads(raw)

    if not channel.get("is_active", True):
        return {"success": False, "error": "Channel is disabled"}

    channel_type = channel["type"]
    config = channel.get("config", {})

    try:
        if channel_type == "email":
            await send_email(
                to=config.get("recipients", []),
                subject=subject,
                body=message,
                html_body=html_message,
            )

        elif channel_type == "sms":
            for phone in config.get("phone_numbers", []):
                await send_sms(to=phone, message=f"{subject}: {message}")

        elif channel_type == "whatsapp":
            phone = config.get("phone_number")
            if phone:
                await send_whatsapp(to=phone, message=f"*{subject}*\n{message}")

        elif channel_type == "telegram":
            chat_id = config.get("chat_id")
            if chat_id:
                await send_telegram(
                    chat_id=chat_id,
                    message=f"<b>{subject}</b>\n{message}",
                    bot_token=config.get("bot_token"),
                )

        elif channel_type == "webhook":
            url = config.get("url")
            if url:
                webhook_payload = payload or {
                    "subject": subject,
                    "message": message,
                    "timestamp": datetime.now(timezone.utc).isoformat(),
                }
                await send_webhook(
                    url=url,
                    payload=webhook_payload,
                    secret=config.get("secret"),
                    headers=config.get("headers"),
                )

        else:
            return {"success": False, "error": f"Unknown channel type: {channel_type}"}

        return {"success": True, "error": None}

    except Exception as exc:
        logger.error(
            "Notification send failed",
            channel_type=channel_type,
            channel_id=channel_id,
            error=str(exc),
        )
        return {"success": False, "error": str(exc)}


async def send_alert_to_all_channels(
    org_id: uuid.UUID,
    alert_data: dict[str, Any],
) -> dict[str, Any]:
    """Broadcast an alert notification to all active channels.

    Args:
        org_id: Organization to broadcast to.
        alert_data: Alert information dict.

    Returns:
        Dict with per-channel results.
    """
    channels = await get_channels(org_id)
    results: dict[str, Any] = {}

    subject = f"[{alert_data.get('severity', 'ALERT').upper()}] {alert_data.get('title', 'VisionAI Alert')}"
    message = (
        f"Alert: {alert_data.get('title', 'Unknown')}\n"
        f"Type: {alert_data.get('alert_type', 'unknown')}\n"
        f"Severity: {alert_data.get('severity', 'unknown')}\n"
        f"Camera: {alert_data.get('camera_id', 'unknown')}\n"
        f"Time: {alert_data.get('created_at', '')}\n"
    )

    html_message = f"""
    <div style="font-family: Arial, sans-serif; padding: 10px;">
        <h2 style="color: #e74c3c;">{subject}</h2>
        <table>
            <tr><td><strong>Type:</strong></td><td>{alert_data.get('alert_type', '')}</td></tr>
            <tr><td><strong>Severity:</strong></td><td>{alert_data.get('severity', '')}</td></tr>
            <tr><td><strong>Camera:</strong></td><td>{alert_data.get('camera_id', '')}</td></tr>
            <tr><td><strong>Time:</strong></td><td>{alert_data.get('created_at', '')}</td></tr>
        </table>
        <p>{alert_data.get('description', '')}</p>
    </div>
    """

    for channel in channels:
        if not channel.get("is_active", True):
            continue

        channel_id = channel["id"]
        result = await send_notification(
            org_id=org_id,
            channel_id=channel_id,
            subject=subject,
            message=message,
            html_message=html_message,
            payload=alert_data,
        )
        results[channel_id] = result

    success_count = sum(1 for r in results.values() if r.get("success"))
    logger.info(
        "Alert broadcast completed",
        org_id=str(org_id),
        total_channels=len(results),
        successful=success_count,
    )

    return {
        "channels_notified": len(results),
        "successful": success_count,
        "results": results,
    }
