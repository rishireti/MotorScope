"""SMTP delivery of MotorScope's transactional emails."""

from __future__ import annotations

import logging
import smtplib
import ssl
from email.message import EmailMessage

from .config import settings

logger = logging.getLogger("motorscope.mailer")

_BRAND = "#ff7a1a"
_DARK = "#0d0f12"


def _wrap(title: str, intro: str, cta_label: str, cta_url: str, footer: str) -> str:
    return f"""<!DOCTYPE html>
<html lang="en">
<head><meta charset="utf-8"><meta name="viewport" content="width=device-width,initial-scale=1"></head>
<body style="margin:0;padding:0;background:{_DARK};">
  <table role="presentation" width="100%" cellpadding="0" cellspacing="0"
         style="background:{_DARK};padding:32px 12px;">
    <tr><td align="center">
      <table role="presentation" width="100%" cellpadding="0" cellspacing="0"
             style="max-width:560px;background:#14171c;border:1px solid #262b33;
                    border-radius:14px;overflow:hidden;">
        <tr><td style="padding:28px 32px 8px 32px;">
          <span style="font-family:'Segoe UI',Arial,sans-serif;font-size:20px;
                       font-weight:700;letter-spacing:2px;color:{_BRAND};
                       text-transform:uppercase;">MotorScope</span>
        </td></tr>
        <tr><td style="padding:8px 32px 0 32px;
                       font-family:'Segoe UI',Arial,sans-serif;font-size:22px;
                       font-weight:700;color:#f5f7fa;">{title}</td></tr>
        <tr><td style="padding:12px 32px 0 32px;
                       font-family:'Segoe UI',Arial,sans-serif;font-size:15px;
                       line-height:22px;color:#9aa4b2;">{intro}</td></tr>
        <tr><td align="center" style="padding:28px 32px 8px 32px;">
          <a href="{cta_url}"
             style="display:inline-block;background:{_BRAND};color:#111317;
                    font-family:'Segoe UI',Arial,sans-serif;font-size:15px;
                    font-weight:700;text-decoration:none;padding:14px 28px;
                    border-radius:8px;">{cta_label}</a>
        </td></tr>
        <tr><td style="padding:20px 32px 0 32px;
                       font-family:'Segoe UI',Arial,sans-serif;font-size:13px;
                       line-height:20px;color:#6b7480;word-break:break-all;">
          If the button does not work, paste this link into your browser:<br>
          <span style="color:{_BRAND};">{cta_url}</span>
        </td></tr>
        <tr><td style="padding:24px 32px 28px 32px;
                       font-family:'Segoe UI',Arial,sans-serif;font-size:12px;
                       line-height:18px;color:#5a626d;border-top:1px solid #262b33;
                       margin-top:24px;">{footer}</td></tr>
      </table>
    </td></tr>
  </table>
</body>
</html>"""


def _send(to_address: str, subject: str, html_body: str, text_body: str) -> bool:
    if not settings.smtp_configured:
        logger.error(
            "SMTP is not configured. Set SMTP_HOST, SMTP_USER and SMTP_PASS in .env "
            "to deliver mail. Link for %s: %s",
            to_address,
            text_body,
        )
        return False

    message = EmailMessage()
    message["Subject"] = subject
    message["From"] = f"{settings.mail_from_name} <{settings.mail_from}>"
    message["To"] = to_address
    message.set_content(text_body)
    message.add_alternative(html_body, subtype="html")

    try:
        if settings.smtp_use_ssl:
            context = ssl.create_default_context()
            with smtplib.SMTP_SSL(
                settings.smtp_host, settings.smtp_port, context=context, timeout=20
            ) as client:
                client.login(settings.smtp_user, settings.smtp_password)
                client.send_message(message)
        else:
            with smtplib.SMTP(
                settings.smtp_host, settings.smtp_port, timeout=20
            ) as client:
                client.ehlo()
                if settings.smtp_use_starttls:
                    client.starttls(context=ssl.create_default_context())
                    client.ehlo()
                client.login(settings.smtp_user, settings.smtp_password)
                client.send_message(message)
    except smtplib.SMTPException as exc:
        logger.error("SMTP delivery to %s failed: %s", to_address, exc)
        return False
    except OSError as exc:
        logger.error("Network error while mailing %s: %s", to_address, exc)
        return False

    logger.info("Sent '%s' to %s", subject, to_address)
    return True


def send_verification_email(to_address: str, display_name: str, token: str) -> bool:
    url = f"{settings.base_url}/verify-email?token={token}"
    greeting = display_name or "there"
    subject = "Confirm your MotorScope email address"
    html = _wrap(
        "Confirm your email",
        f"Hi {greeting}, welcome to MotorScope. Confirm this address to activate "
        "your account, then you can start charting your mobility score from the "
        "movement data your watch and phone already collect. This confirmation link "
        "expires in 24 hours.",
        "Verify email address",
        url,
        "You received this because someone signed up for MotorScope with this address. "
        "If that was not you, ignore this email and nothing will happen.",
    )
    text = (
        f"Hi {greeting},\n\nConfirm your MotorScope email address by opening this link:\n"
        f"{url}\n\nThe link expires in 24 hours."
    )
    return _send(to_address, subject, html, text)


def send_password_reset_email(to_address: str, display_name: str, token: str) -> bool:
    url = f"{settings.base_url}/reset-password?token={token}"
    greeting = display_name or "there"
    subject = "Reset your MotorScope password"
    html = _wrap(
        "Reset your password",
        f"Hi {greeting}, we received a request to reset the password for your "
        "MotorScope account. Choose a new one using the button below. "
        "This link expires in 30 minutes and can only be used once.",
        "Reset password",
        url,
        "If you did not request a reset, ignore this email. Your current password "
        "stays active and nothing has changed.",
    )
    text = (
        f"Hi {greeting},\n\nReset your MotorScope password here:\n{url}\n\n"
        "The link expires in 30 minutes. If you did not request this, ignore this email."
    )
    return _send(to_address, subject, html, text)
