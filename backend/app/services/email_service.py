"""Email service for transactional emails.

Provides high-level email sending functions used by API endpoints
(password reset, account verification, etc.). Uses SMTP via
aiosmtplib for async delivery.
"""

from __future__ import annotations

import structlog
from email.mime.multipart import MIMEMultipart
from email.mime.text import MIMEText

import aiosmtplib

from app.config import get_settings

logger = structlog.stdlib.get_logger(__name__)


async def _send_email(to_email: str, subject: str, html_body: str) -> None:
    """Send an email via SMTP.

    Args:
        to_email: Recipient email address.
        subject: Email subject line.
        html_body: HTML email body.

    Raises:
        Exception: If SMTP delivery fails.
    """
    settings = get_settings()

    if not settings.SMTP_HOST or not settings.SMTP_USERNAME:
        logger.warning("SMTP not configured; skipping email send", to=to_email)
        return

    message = MIMEMultipart("alternative")
    message["From"] = settings.SMTP_FROM_EMAIL or f"noreply@{settings.APP_NAME.lower()}.local"
    message["To"] = to_email
    message["Subject"] = subject
    message.attach(MIMEText(html_body, "html"))

    try:
        await aiosmtplib.send(
            message,
            hostname=settings.SMTP_HOST,
            port=settings.SMTP_PORT or 587,
            username=settings.SMTP_USERNAME,
            password=settings.SMTP_PASSWORD,
            use_tls=settings.SMTP_SSL,
            start_tls=settings.SMTP_TLS,
        )
        logger.info("Email sent", to=to_email, subject=subject)
    except Exception as exc:
        logger.error("Email send failed", to=to_email, error=str(exc))
        raise


async def send_password_reset_email(
    to_email: str,
    user_name: str,
    reset_token: str,
) -> None:
    """Send a password reset email to the user.

    Args:
        to_email: User's email address.
        user_name: User's display name.
        reset_token: The password reset token.
    """
    settings = get_settings()
    app_name = settings.APP_NAME or "VisionAI"

    html = f"""
    <html>
    <body style="font-family: Arial, sans-serif; max-width: 600px; margin: 0 auto;">
        <h2>{app_name} — Password Reset</h2>
        <p>Hello {user_name},</p>
        <p>We received a request to reset your password. Use the token below
        to complete the reset process:</p>
        <p style="background: #f4f4f4; padding: 12px; font-family: monospace;
        font-size: 16px; border-radius: 4px; text-align: center;">
            {reset_token}
        </p>
        <p>This token expires in 1 hour. If you didn't request this reset,
        you can safely ignore this email.</p>
        <p>— The {app_name} Team</p>
    </body>
    </html>
    """

    await _send_email(
        to_email=to_email,
        subject=f"{app_name} — Password Reset Request",
        html_body=html,
    )


async def send_verification_email(
    to_email: str,
    user_name: str,
    verification_token: str,
) -> None:
    """Send an account verification email.

    Args:
        to_email: User's email address.
        user_name: User's display name.
        verification_token: The email verification token.
    """
    settings = get_settings()
    app_name = settings.APP_NAME or "VisionAI"

    html = f"""
    <html>
    <body style="font-family: Arial, sans-serif; max-width: 600px; margin: 0 auto;">
        <h2>Welcome to {app_name}</h2>
        <p>Hello {user_name},</p>
        <p>Please verify your email address using the token below:</p>
        <p style="background: #f4f4f4; padding: 12px; font-family: monospace;
        font-size: 16px; border-radius: 4px; text-align: center;">
            {verification_token}
        </p>
        <p>— The {app_name} Team</p>
    </body>
    </html>
    """

    await _send_email(
        to_email=to_email,
        subject=f"Verify your {app_name} account",
        html_body=html,
    )
