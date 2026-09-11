"""
VisionAI Alert Dispatch Celery Tasks.

Handles multi-channel alert delivery including WhatsApp, Telegram,
email (SMTP/SendGrid), SMS (Twilio), and webhooks. The main dispatcher
fans out to channel-specific tasks for parallel delivery.
"""

from __future__ import annotations

import asyncio
import smtplib
import uuid
from datetime import datetime, timezone
from email.mime.multipart import MIMEMultipart
from email.mime.text import MIMEText
from typing import Any

import httpx
import structlog
from sqlalchemy import select, update

from app.config import get_settings
from app.workers.celery_app import celery_app

logger = structlog.stdlib.get_logger(__name__)
settings = get_settings()


def _run_async(coro):
    """Run an async coroutine from a synchronous Celery task context."""
    loop = asyncio.new_event_loop()
    try:
        return loop.run_until_complete(coro)
    finally:
        loop.close()


async def _load_alert(alert_id: str) -> dict[str, Any] | None:
    """Load full alert record with related entities from the database."""
    from app.database import get_db_context
    from app.models.alert import Alert

    async with get_db_context() as session:
        result = await session.execute(
            select(Alert).where(Alert.id == uuid.UUID(alert_id))
        )
        alert = result.scalar_one_or_none()
        if alert is None:
            return None

        return {
            "id": str(alert.id),
            "org_id": str(alert.org_id),
            "camera_id": str(alert.camera_id),
            "zone_id": str(alert.zone_id) if alert.zone_id else None,
            "rule_id": str(alert.rule_id),
            "alert_type": alert.alert_type.value,
            "severity": alert.severity.value,
            "title": alert.title,
            "description": alert.description or "",
            "snapshot_path": alert.snapshot_path,
            "video_clip_path": alert.video_clip_path,
            "metadata_json": alert.metadata_json or {},
            "status": alert.status.value,
            "created_at": alert.created_at.isoformat(),
        }


async def _load_alert_channels(alert_id: str) -> dict[str, Any]:
    """Load notification channel configuration from the alert's rule."""
    from app.database import get_db_context
    from app.models.alert import Alert
    from app.models.rule import Rule

    async with get_db_context() as session:
        result = await session.execute(
            select(Rule.alert_channels).join(Alert, Alert.rule_id == Rule.id).where(
                Alert.id == uuid.UUID(alert_id)
            )
        )
        row = result.scalar_one_or_none()
        return row if row and isinstance(row, dict) else {}


def _format_alert_message(alert: dict[str, Any]) -> str:
    """Format a human-readable alert message from alert data."""
    severity_emoji = {
        "critical": "[CRITICAL]",
        "high": "[HIGH]",
        "medium": "[MEDIUM]",
        "low": "[LOW]",
        "info": "[INFO]",
    }
    severity_label = severity_emoji.get(alert["severity"], "[ALERT]")

    lines = [
        f"{severity_label} {alert['title']}",
        "",
        f"Type: {alert['alert_type'].replace('_', ' ').title()}",
        f"Severity: {alert['severity'].upper()}",
        f"Time: {alert['created_at']}",
    ]

    if alert.get("description"):
        lines.append(f"Details: {alert['description']}")

    lines.append("")
    lines.append("-- VisionAI Alert System")

    return "\n".join(lines)


def _format_alert_html(alert: dict[str, Any]) -> str:
    """Format an HTML email body from alert data."""
    severity_colors = {
        "critical": "#DC2626",
        "high": "#EA580C",
        "medium": "#CA8A04",
        "low": "#2563EB",
        "info": "#6B7280",
    }
    color = severity_colors.get(alert["severity"], "#6B7280")

    return f"""
    <html>
    <body style="font-family: Arial, sans-serif; max-width: 600px; margin: 0 auto;">
        <div style="background-color: {color}; color: white; padding: 16px; border-radius: 8px 8px 0 0;">
            <h2 style="margin: 0;">{alert['title']}</h2>
        </div>
        <div style="border: 1px solid #E5E7EB; border-top: none; padding: 16px; border-radius: 0 0 8px 8px;">
            <table style="width: 100%; border-collapse: collapse;">
                <tr>
                    <td style="padding: 8px 0; font-weight: bold; width: 120px;">Type:</td>
                    <td style="padding: 8px 0;">{alert['alert_type'].replace('_', ' ').title()}</td>
                </tr>
                <tr>
                    <td style="padding: 8px 0; font-weight: bold;">Severity:</td>
                    <td style="padding: 8px 0;">
                        <span style="background-color: {color}; color: white; padding: 2px 8px; border-radius: 4px;">
                            {alert['severity'].upper()}
                        </span>
                    </td>
                </tr>
                <tr>
                    <td style="padding: 8px 0; font-weight: bold;">Time:</td>
                    <td style="padding: 8px 0;">{alert['created_at']}</td>
                </tr>
                {'<tr><td style="padding: 8px 0; font-weight: bold;">Details:</td><td style="padding: 8px 0;">' + alert["description"] + '</td></tr>' if alert.get("description") else ''}
            </table>
        </div>
        <p style="color: #6B7280; font-size: 12px; text-align: center; margin-top: 16px;">
            This is an automated alert from VisionAI.
        </p>
    </body>
    </html>
    """


@celery_app.task(
    name="app.workers.alert_tasks.dispatch_alert",
    bind=True,
    max_retries=3,
    default_retry_delay=10,
    queue="alerts",
)
def dispatch_alert(self, alert_id: str) -> dict[str, Any]:
    """Main alert dispatcher that fans out to channel-specific delivery tasks.

    Loads the alert from the database, determines which notification channels
    are configured for the associated rule, and dispatches a subtask for each
    enabled channel.

    Args:
        alert_id: UUID of the alert to dispatch.

    Returns:
        dict summarizing which channels were dispatched.
    """
    log = logger.bind(alert_id=alert_id, task_id=self.request.id)
    log.info("Dispatching alert to configured channels")

    try:
        # Load alert data
        alert = _run_async(_load_alert(alert_id))
        if alert is None:
            log.error("Alert not found in database")
            return {"alert_id": alert_id, "status": "error", "message": "Alert not found"}

        # Load notification channel configuration from the rule
        channels = _run_async(_load_alert_channels(alert_id))
        dispatched = []

        # WhatsApp
        if channels.get("whatsapp", {}).get("enabled"):
            send_whatsapp_alert.apply_async(
                args=[alert_id, channels["whatsapp"]],
                queue="alerts",
            )
            dispatched.append("whatsapp")

        # Telegram
        if channels.get("telegram", {}).get("enabled"):
            send_telegram_alert.apply_async(
                args=[alert_id, channels["telegram"]],
                queue="alerts",
            )
            dispatched.append("telegram")

        # Email
        if channels.get("email", {}).get("enabled"):
            send_email_alert.apply_async(
                args=[alert_id, channels["email"]],
                queue="alerts",
            )
            dispatched.append("email")

        # SMS
        if channels.get("sms", {}).get("enabled"):
            send_sms_alert.apply_async(
                args=[alert_id, channels["sms"]],
                queue="alerts",
            )
            dispatched.append("sms")

        # Webhook
        if channels.get("webhook", {}).get("enabled"):
            webhook_url = channels["webhook"].get("url", "")
            if webhook_url:
                send_webhook_alert.apply_async(
                    args=[alert_id, webhook_url],
                    queue="alerts",
                )
                dispatched.append("webhook")

        log.info("Alert dispatched to channels", channels=dispatched)

        return {
            "alert_id": alert_id,
            "status": "dispatched",
            "channels": dispatched,
            "channel_count": len(dispatched),
        }

    except Exception as exc:
        log.error("Alert dispatch failed", error=str(exc))
        raise self.retry(exc=exc)


@celery_app.task(
    name="app.workers.alert_tasks.send_whatsapp_alert",
    bind=True,
    max_retries=5,
    default_retry_delay=15,
    queue="alerts",
)
def send_whatsapp_alert(self, alert_id: str, config: dict) -> dict[str, Any]:
    """Send alert notification via WhatsApp using the Meta Cloud API or Twilio.

    Args:
        alert_id: UUID of the alert.
        config: WhatsApp channel configuration containing phone numbers and API credentials.

    Returns:
        dict with delivery status.
    """
    log = logger.bind(alert_id=alert_id, channel="whatsapp", task_id=self.request.id)
    log.info("Sending WhatsApp alert")

    try:
        alert = _run_async(_load_alert(alert_id))
        if alert is None:
            return {"alert_id": alert_id, "status": "error", "message": "Alert not found"}

        message_text = _format_alert_message(alert)
        recipients = config.get("phone_numbers", [])
        api_provider = config.get("provider", "twilio")
        results = []

        if api_provider == "meta":
            # Meta Cloud API (WhatsApp Business Platform)
            phone_number_id = config.get("phone_number_id", "")
            access_token = config.get("access_token", "")
            api_url = f"https://graph.facebook.com/v18.0/{phone_number_id}/messages"

            with httpx.Client(timeout=30) as client:
                for phone in recipients:
                    payload = {
                        "messaging_product": "whatsapp",
                        "to": phone,
                        "type": "text",
                        "text": {"body": message_text},
                    }
                    response = client.post(
                        api_url,
                        json=payload,
                        headers={"Authorization": f"Bearer {access_token}"},
                    )
                    results.append({
                        "phone": phone,
                        "status_code": response.status_code,
                        "success": 200 <= response.status_code < 300,
                    })
                    log.info("WhatsApp Meta API response", phone=phone, status=response.status_code)

        else:
            # Twilio WhatsApp API
            account_sid = config.get("account_sid") or settings.TWILIO_ACCOUNT_SID
            auth_token = config.get("auth_token") or settings.TWILIO_AUTH_TOKEN
            from_number = config.get("from_number") or settings.TWILIO_FROM_NUMBER

            with httpx.Client(timeout=30) as client:
                for phone in recipients:
                    response = client.post(
                        f"https://api.twilio.com/2010-04-01/Accounts/{account_sid}/Messages.json",
                        data={
                            "From": f"whatsapp:{from_number}",
                            "To": f"whatsapp:{phone}",
                            "Body": message_text,
                        },
                        auth=(account_sid, auth_token),
                    )
                    results.append({
                        "phone": phone,
                        "status_code": response.status_code,
                        "success": 200 <= response.status_code < 300,
                    })
                    log.info("WhatsApp Twilio response", phone=phone, status=response.status_code)

        success_count = sum(1 for r in results if r["success"])
        log.info("WhatsApp alerts sent", total=len(results), success=success_count)

        return {
            "alert_id": alert_id,
            "channel": "whatsapp",
            "results": results,
            "success_count": success_count,
            "total_count": len(results),
        }

    except Exception as exc:
        log.error("WhatsApp alert failed", error=str(exc))
        raise self.retry(exc=exc)


@celery_app.task(
    name="app.workers.alert_tasks.send_telegram_alert",
    bind=True,
    max_retries=5,
    default_retry_delay=10,
    queue="alerts",
)
def send_telegram_alert(self, alert_id: str, config: dict) -> dict[str, Any]:
    """Send alert notification via Telegram Bot API.

    Args:
        alert_id: UUID of the alert.
        config: Telegram channel configuration containing bot token and chat IDs.

    Returns:
        dict with delivery status.
    """
    log = logger.bind(alert_id=alert_id, channel="telegram", task_id=self.request.id)
    log.info("Sending Telegram alert")

    try:
        alert = _run_async(_load_alert(alert_id))
        if alert is None:
            return {"alert_id": alert_id, "status": "error", "message": "Alert not found"}

        message_text = _format_alert_message(alert)
        bot_token = config.get("bot_token") or settings.TELEGRAM_BOT_TOKEN
        chat_ids = config.get("chat_ids", [])

        if not chat_ids and settings.TELEGRAM_DEFAULT_CHAT_ID:
            chat_ids = [settings.TELEGRAM_DEFAULT_CHAT_ID]

        if not bot_token:
            log.error("Telegram bot token not configured")
            return {"alert_id": alert_id, "status": "error", "message": "Bot token not configured"}

        api_url = f"https://api.telegram.org/bot{bot_token}/sendMessage"
        results = []

        with httpx.Client(timeout=30) as client:
            for chat_id in chat_ids:
                payload = {
                    "chat_id": chat_id,
                    "text": message_text,
                    "parse_mode": "HTML",
                    "disable_web_page_preview": True,
                }
                response = client.post(api_url, json=payload)
                response_data = response.json()
                success = response_data.get("ok", False)
                results.append({
                    "chat_id": chat_id,
                    "status_code": response.status_code,
                    "success": success,
                })
                log.info("Telegram API response", chat_id=chat_id, success=success)

                # If the alert has a snapshot, send it as a photo
                if alert.get("snapshot_path") and success:
                    snapshot_url = alert["snapshot_path"]
                    if snapshot_url.startswith(("http://", "https://")):
                        photo_payload = {
                            "chat_id": chat_id,
                            "photo": snapshot_url,
                            "caption": alert["title"][:200],
                        }
                        client.post(
                            f"https://api.telegram.org/bot{bot_token}/sendPhoto",
                            json=photo_payload,
                        )

        success_count = sum(1 for r in results if r["success"])
        log.info("Telegram alerts sent", total=len(results), success=success_count)

        return {
            "alert_id": alert_id,
            "channel": "telegram",
            "results": results,
            "success_count": success_count,
            "total_count": len(results),
        }

    except Exception as exc:
        log.error("Telegram alert failed", error=str(exc))
        raise self.retry(exc=exc)


@celery_app.task(
    name="app.workers.alert_tasks.send_email_alert",
    bind=True,
    max_retries=5,
    default_retry_delay=30,
    queue="alerts",
)
def send_email_alert(self, alert_id: str, config: dict) -> dict[str, Any]:
    """Send alert notification via SMTP email.

    Supports direct SMTP or SendGrid API depending on configuration.

    Args:
        alert_id: UUID of the alert.
        config: Email channel configuration containing recipients and optional
                SendGrid API key.

    Returns:
        dict with delivery status.
    """
    log = logger.bind(alert_id=alert_id, channel="email", task_id=self.request.id)
    log.info("Sending email alert")

    try:
        alert = _run_async(_load_alert(alert_id))
        if alert is None:
            return {"alert_id": alert_id, "status": "error", "message": "Alert not found"}

        recipients = config.get("recipients", [])
        if not recipients:
            log.warning("No email recipients configured")
            return {"alert_id": alert_id, "status": "skipped", "message": "No recipients"}

        subject = f"[VisionAI Alert] {alert['severity'].upper()}: {alert['title']}"
        text_body = _format_alert_message(alert)
        html_body = _format_alert_html(alert)

        # Check if SendGrid is configured
        sendgrid_api_key = config.get("sendgrid_api_key", "")

        if sendgrid_api_key:
            # SendGrid API
            with httpx.Client(timeout=30) as client:
                payload = {
                    "personalizations": [
                        {"to": [{"email": r} for r in recipients]}
                    ],
                    "from": {
                        "email": config.get("from_email", settings.SMTP_FROM_EMAIL),
                        "name": config.get("from_name", settings.SMTP_FROM_NAME),
                    },
                    "subject": subject,
                    "content": [
                        {"type": "text/plain", "value": text_body},
                        {"type": "text/html", "value": html_body},
                    ],
                }
                response = client.post(
                    "https://api.sendgrid.com/v3/mail/send",
                    json=payload,
                    headers={
                        "Authorization": f"Bearer {sendgrid_api_key}",
                        "Content-Type": "application/json",
                    },
                )
                success = 200 <= response.status_code < 300
                log.info("SendGrid response", status_code=response.status_code, success=success)

                return {
                    "alert_id": alert_id,
                    "channel": "email",
                    "provider": "sendgrid",
                    "recipients": recipients,
                    "status_code": response.status_code,
                    "success": success,
                }

        else:
            # Direct SMTP
            smtp_host = config.get("smtp_host", settings.SMTP_HOST)
            smtp_port = config.get("smtp_port", settings.SMTP_PORT)
            smtp_username = config.get("smtp_username", settings.SMTP_USERNAME)
            smtp_password = config.get("smtp_password", settings.SMTP_PASSWORD)
            from_email = config.get("from_email", settings.SMTP_FROM_EMAIL)
            from_name = config.get("from_name", settings.SMTP_FROM_NAME)
            use_tls = config.get("use_tls", settings.SMTP_TLS)
            use_ssl = config.get("use_ssl", settings.SMTP_SSL)

            msg = MIMEMultipart("alternative")
            msg["Subject"] = subject
            msg["From"] = f"{from_name} <{from_email}>"
            msg["To"] = ", ".join(recipients)

            msg.attach(MIMEText(text_body, "plain"))
            msg.attach(MIMEText(html_body, "html"))

            if use_ssl:
                server = smtplib.SMTP_SSL(smtp_host, smtp_port, timeout=30)
            else:
                server = smtplib.SMTP(smtp_host, smtp_port, timeout=30)

            try:
                if use_tls and not use_ssl:
                    server.starttls()
                if smtp_username and smtp_password:
                    server.login(smtp_username, smtp_password)
                server.sendmail(from_email, recipients, msg.as_string())
                log.info("Email sent via SMTP", recipients=recipients)
            finally:
                server.quit()

            return {
                "alert_id": alert_id,
                "channel": "email",
                "provider": "smtp",
                "recipients": recipients,
                "success": True,
            }

    except Exception as exc:
        log.error("Email alert failed", error=str(exc))
        raise self.retry(exc=exc)


@celery_app.task(
    name="app.workers.alert_tasks.send_sms_alert",
    bind=True,
    max_retries=5,
    default_retry_delay=15,
    queue="alerts",
)
def send_sms_alert(self, alert_id: str, config: dict) -> dict[str, Any]:
    """Send alert notification via Twilio SMS.

    Args:
        alert_id: UUID of the alert.
        config: SMS channel configuration containing phone numbers.

    Returns:
        dict with delivery status.
    """
    log = logger.bind(alert_id=alert_id, channel="sms", task_id=self.request.id)
    log.info("Sending SMS alert")

    try:
        alert = _run_async(_load_alert(alert_id))
        if alert is None:
            return {"alert_id": alert_id, "status": "error", "message": "Alert not found"}

        # SMS messages have character limits; create a concise version
        message_text = (
            f"[VisionAI {alert['severity'].upper()}] {alert['title']}"
        )
        if len(message_text) > 160:
            message_text = message_text[:157] + "..."

        account_sid = config.get("account_sid") or settings.TWILIO_ACCOUNT_SID
        auth_token = config.get("auth_token") or settings.TWILIO_AUTH_TOKEN
        from_number = config.get("from_number") or settings.TWILIO_FROM_NUMBER
        phone_numbers = config.get("phone_numbers", [])

        if not account_sid or not auth_token:
            log.error("Twilio credentials not configured")
            return {"alert_id": alert_id, "status": "error", "message": "Twilio not configured"}

        results = []

        with httpx.Client(timeout=30) as client:
            for phone in phone_numbers:
                response = client.post(
                    f"https://api.twilio.com/2010-04-01/Accounts/{account_sid}/Messages.json",
                    data={
                        "From": from_number,
                        "To": phone,
                        "Body": message_text,
                    },
                    auth=(account_sid, auth_token),
                )
                success = 200 <= response.status_code < 300
                results.append({
                    "phone": phone,
                    "status_code": response.status_code,
                    "success": success,
                })
                log.info("Twilio SMS response", phone=phone, status=response.status_code)

        success_count = sum(1 for r in results if r["success"])
        log.info("SMS alerts sent", total=len(results), success=success_count)

        return {
            "alert_id": alert_id,
            "channel": "sms",
            "results": results,
            "success_count": success_count,
            "total_count": len(results),
        }

    except Exception as exc:
        log.error("SMS alert failed", error=str(exc))
        raise self.retry(exc=exc)


@celery_app.task(
    name="app.workers.alert_tasks.send_webhook_alert",
    bind=True,
    max_retries=5,
    default_retry_delay=10,
    queue="alerts",
)
def send_webhook_alert(self, alert_id: str, webhook_url: str) -> dict[str, Any]:
    """Send alert notification via HTTP POST to a webhook URL.

    The webhook payload contains the full alert object as JSON. A signature
    header is included for verification.

    Args:
        alert_id: UUID of the alert.
        webhook_url: The destination URL to POST the alert payload.

    Returns:
        dict with delivery status.
    """
    log = logger.bind(alert_id=alert_id, channel="webhook", webhook_url=webhook_url, task_id=self.request.id)
    log.info("Sending webhook alert")

    try:
        alert = _run_async(_load_alert(alert_id))
        if alert is None:
            return {"alert_id": alert_id, "status": "error", "message": "Alert not found"}

        import hashlib
        import hmac
        import json

        payload = {
            "event": "alert.created",
            "timestamp": datetime.now(timezone.utc).isoformat(),
            "alert": alert,
        }

        payload_bytes = json.dumps(payload, sort_keys=True).encode("utf-8")

        # Generate HMAC signature using the application secret key
        signature = hmac.new(
            settings.SECRET_KEY.encode("utf-8"),
            payload_bytes,
            hashlib.sha256,
        ).hexdigest()

        headers = {
            "Content-Type": "application/json",
            "User-Agent": "VisionAI-Webhook/1.0",
            "X-VisionAI-Signature": f"sha256={signature}",
            "X-VisionAI-Event": "alert.created",
            "X-VisionAI-Alert-ID": alert_id,
        }

        with httpx.Client(timeout=30) as client:
            response = client.post(
                webhook_url,
                json=payload,
                headers=headers,
            )

        success = 200 <= response.status_code < 300
        log.info(
            "Webhook response",
            status_code=response.status_code,
            success=success,
        )

        return {
            "alert_id": alert_id,
            "channel": "webhook",
            "webhook_url": webhook_url,
            "status_code": response.status_code,
            "success": success,
            "response_body": response.text[:500] if not success else "",
        }

    except Exception as exc:
        log.error("Webhook alert failed", error=str(exc))
        raise self.retry(exc=exc)
