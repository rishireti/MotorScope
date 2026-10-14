"""Render a real password-reset email to a file so the content can be inspected.

Runs the full forgot-password flow against a fake SMTP transport, then writes the
resulting MIME message out. Nothing is sent anywhere.

Usage:  .venv\\Scripts\\python.exe -m tests.preview_email
"""

from __future__ import annotations

import email
import os
import re
import smtplib
import tempfile
from pathlib import Path

TEMP_DB = Path(tempfile.gettempdir()) / "motorscope_email_preview.db"
OUTPUT_DIR = Path(__file__).resolve().parent.parent / "preview"

os.environ["DATABASE_PATH"] = str(TEMP_DB)
os.environ["SECRET_KEY"] = "preview-key"
os.environ["SMTP_HOST"] = "smtp.example.test"
os.environ["SMTP_PORT"] = "587"
os.environ["SMTP_USER"] = "bot@example.test"
os.environ["SMTP_PASS"] = "not-a-real-password"
os.environ["MAIL_FROM"] = "bot@example.test"
os.environ["MAIL_FROM_NAME"] = "MotorScope"
os.environ["BASE_URL"] = "http://127.0.0.1:8000"
TEMP_DB.unlink(missing_ok=True)

CAPTURED: list[email.message.Message] = []


class FakeSMTP:
    def __init__(self, host, port, timeout=None):
        self.host, self.port = host, port

    def __enter__(self):
        return self

    def __exit__(self, *exc):
        return False

    def ehlo(self):
        pass

    def starttls(self, context=None):
        pass

    def login(self, user, password):
        pass

    def send_message(self, message):
        CAPTURED.append(message)


smtplib.SMTP = FakeSMTP
smtplib.SMTP_SSL = FakeSMTP

from fastapi.testclient import TestClient  # noqa: E402

from app.main import app  # noqa: E402


def main() -> int:
    with TestClient(app, follow_redirects=False) as client:
        def csrf(path: str) -> str:
            match = re.search(
                r'name="csrf_token" value="([^"]+)"', client.get(path).text
            )
            return match.group(1) if match else ""

        client.post(
            "/signup",
            data={
                "csrf_token": csrf("/signup"),
                "display_name": "Alex Mercer",
                "email": "alex@example.com",
                "password": "TorqueMonster2026",
                "confirm_password": "TorqueMonster2026",
            },
        )
        client.post(
            "/forgot-password",
            data={"csrf_token": csrf("/forgot-password"), "email": "alex@example.com"},
        )

    if not CAPTURED:
        print("No email was generated.")
        return 1

    OUTPUT_DIR.mkdir(exist_ok=True)
    reset_message = next(
        (m for m in CAPTURED if m["Subject"].startswith("Reset your")), CAPTURED[-1]
    )

    (OUTPUT_DIR / "reset_email.eml").write_bytes(reset_message.as_bytes())

    html_part = reset_message.get_body(preferencelist=("html",))
    text_part = reset_message.get_body(preferencelist=("plain",))
    (OUTPUT_DIR / "reset_email.html").write_text(
        html_part.get_content() if html_part else "", encoding="utf-8"
    )
    (OUTPUT_DIR / "reset_email.txt").write_text(
        text_part.get_content() if text_part else "", encoding="utf-8"
    )

    print(f"emails generated : {len(CAPTURED)}")
    print(f"subject          : {reset_message['Subject']}")
    print(f"from             : {reset_message['From']}")
    print(f"to               : {reset_message['To']}")
    print(f"content type     : {reset_message.get_content_type()}")
    print(f"wrote            : {OUTPUT_DIR}")

    body = (OUTPUT_DIR / "reset_email.html").read_text(encoding="utf-8")
    link = re.search(r'href="([^"]+)"', body)
    print(f"reset button link: {link.group(1) if link else 'MISSING'}")
    print(f"orange styling   : {'#ff7a1a' in body}")
    print(f"dark background  : {'#0d0f12' in body}")

    TEMP_DB.unlink(missing_ok=True)
    for suffix in ("-wal", "-shm"):
        Path(str(TEMP_DB) + suffix).unlink(missing_ok=True)
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
