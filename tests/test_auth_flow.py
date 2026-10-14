"""End-to-end smoke test of the MotorScope auth flow.

Runs against a throwaway database with the mailer stubbed, so no real email is sent.
Usage:  .venv\\Scripts\\python.exe -m tests.test_auth_flow
"""

from __future__ import annotations

import os
import re
import tempfile
from pathlib import Path

TEMP_DB = Path(tempfile.gettempdir()) / "motorscope_smoke_test.db"
os.environ["DATABASE_PATH"] = str(TEMP_DB)
os.environ["SECRET_KEY"] = "smoke-test-key"
os.environ["PASSWORD_MIN_LENGTH"] = "10"
TEMP_DB.unlink(missing_ok=True)

from fastapi.testclient import TestClient  # noqa: E402

from app import db, mailer  # noqa: E402
from app.main import app  # noqa: E402

CAPTURED: list[tuple[str, str, str]] = []


def _capture_verification(to_address: str, display_name: str, token: str) -> bool:
    CAPTURED.append(("verify", to_address, token))
    return True


def _capture_reset(to_address: str, display_name: str, token: str) -> bool:
    CAPTURED.append(("reset", to_address, token))
    return True


mailer.send_verification_email = _capture_verification
mailer.send_password_reset_email = _capture_reset

PASSWORD = "TorqueMonster2026"
failures: list[str] = []


def check(label: str, condition: bool, extra: str = "") -> None:
    status = "PASS" if condition else "FAIL"
    print(f"  [{status}] {label}{(' -> ' + extra) if extra and not condition else ''}")
    if not condition:
        failures.append(label)


def csrf(client: TestClient, path: str) -> str:
    response = client.get(path)
    match = re.search(r'name="csrf_token" value="([^"]+)"', response.text)
    return match.group(1) if match else ""


def main() -> int:
    with TestClient(app, follow_redirects=False) as client:
        print("\nLanding page")
        home = client.get("/")
        check("200 on /", home.status_code == 200)
        check("MotorScope brand present", "MotorScope" in home.text)
        check("dark theme stylesheet linked", "/static/css/style.css" in home.text)
        check("anonymous user sees the app entry point", 'href="/dashboard">View My Mobility</a>' in home.text)

        print("\nSignup validation")
        token = csrf(client, "/signup")
        response = client.post(
            "/signup",
            data={
                "csrf_token": token,
                "display_name": "A",
                "email": "not-an-email",
                "password": "short",
                "confirm_password": "short",
            },
        )
        check("rejects bad form", response.status_code == 400)
        check("reports name error", "at least 2 characters" in response.text)
        check("reports email error", "valid email" in response.text)
        check("reports password error", "10 characters" in response.text)

        print("\nCSRF protection")
        response = client.post(
            "/signup",
            data={
                "csrf_token": "forged",
                "display_name": "Alex Mercer",
                "email": "alex@example.com",
                "password": PASSWORD,
                "confirm_password": PASSWORD,
            },
        )
        check("forged CSRF token rejected", response.status_code == 400)
        check("no account created", db.get_user_by_email("alex@example.com") is None)

        print("\nAccount creation")
        token = csrf(client, "/signup")
        response = client.post(
            "/signup",
            data={
                "csrf_token": token,
                "display_name": "Alex Mercer",
                "email": "Alex@Example.com",
                "password": PASSWORD,
                "confirm_password": PASSWORD,
            },
        )
        check("signup accepted", response.status_code == 200)
        check("confirmation page shown", "Check your inbox" in response.text)
        check("email normalized", db.get_user_by_email("alex@example.com") is not None)
        check("verification email sent", CAPTURED[-1][0] == "verify")
        check("starts unverified", db.get_user_by_email("alex@example.com")["email_verified"] == 0)

        print("\nOne account per email")
        token = csrf(client, "/signup")
        response = client.post(
            "/signup",
            data={
                "csrf_token": token,
                "display_name": "Impostor",
                "email": "alex@example.com",
                "password": PASSWORD,
                "confirm_password": PASSWORD,
            },
        )
        check("duplicate signup blocked", response.status_code == 409)
        check("duplicate message shown", "already exists" in response.text)
        check("still one user", db.count_users() == 1)

        print("\nLogin before verification")
        token = csrf(client, "/login")
        response = client.post(
            "/login",
            data={
                "csrf_token": token,
                "email": "alex@example.com",
                "password": PASSWORD,
                "next": "/dashboard",
            },
        )
        check("unverified login blocked", response.status_code == 403)
        check("explains why", "Confirm your email" in response.text)
        check("no session cookie issued", settings_session_cookie(client) is None)

        print("\nDashboard is protected")
        response = client.get("/dashboard")
        check("anonymous redirected to login", response.status_code == 303)
        check("redirects to /login", response.headers["location"].startswith("/login?next="))
        response = client.get("/account")
        check("account page protected", response.status_code == 303)

        print("\nEmail verification")
        verify_token = CAPTURED[-1][2]
        response = client.get(f"/verify-email?token={verify_token}")
        check("verification accepted", response.status_code == 200)
        check("confirmed message", "email is confirmed" in response.text)
        check("user marked verified", db.get_user_by_email("alex@example.com")["email_verified"] == 1)
        response = client.get(f"/verify-email?token={verify_token}")
        check("token cannot be reused", response.status_code == 400)
        response = client.get("/verify-email?token=obviously-not-a-real-token")
        check("forged token rejected", response.status_code == 400)

        print("\nSign in")
        token = csrf(client, "/login")
        response = client.post(
            "/login",
            data={
                "csrf_token": token,
                "email": "alex@example.com",
                "password": "wrong-password-1",
                "next": "/dashboard",
            },
        )
        check("wrong password rejected", response.status_code == 401)
        check("generic error", "not recognised" in response.text)

        token = csrf(client, "/login")
        response = client.post(
            "/login",
            data={
                "csrf_token": token,
                "email": "alex@example.com",
                "password": PASSWORD,
                "next": "/dashboard",
            },
        )
        check("login redirects to next", response.status_code == 303)
        check("lands on dashboard", response.headers["location"] == "/dashboard")
        check("session cookie issued", settings_session_cookie(client) is not None)

        response = client.get("/dashboard")
        check("dashboard reachable", response.status_code == 200)
        check("shows account email", "alex@example.com" in response.text)
        check("shows verified badge", "Verified" in response.text)

        print("\nPassword reset")
        token = csrf(client, "/dashboard")
        client.post("/logout", data={"csrf_token": token})
        check("signed out for reset test", settings_session_cookie(client) is None)
        response = client.get("/forgot-password")
        check("forgot page loads", response.status_code == 200)
        token = csrf(client, "/forgot-password")
        response = client.post(
            "/forgot-password",
            data={"csrf_token": token, "email": "alex@example.com"},
        )
        check("reset page shown", response.status_code == 200)
        check("no account enumeration", "Reset link sent" in response.text)
        check("reset email sent", CAPTURED[-1][0] == "reset")
        reset_token = CAPTURED[-1][2]

        token = csrf(client, "/forgot-password")
        sent_before = len(CAPTURED)
        response = client.post(
            "/forgot-password",
            data={"csrf_token": token, "email": "nobody@nowhere.test"},
        )
        check("unknown email still shows same page", "Reset link sent" in response.text)
        check("no email leaked for unknown address", len(CAPTURED) == sent_before)

        response = client.get(f"/reset-password?token={reset_token}")
        check("reset form loads", response.status_code == 200)
        check("form is active", 'name="token" value="%s"' % reset_token in response.text)
        response = client.get("/reset-password?token=bad-token")
        check("bad reset token shows no form", 'name="token"' not in response.text)

        response = client.get("/reset-password")
        check("missing token handled", response.status_code == 200)

        NEW_PASSWORD = "WrenchLover2026"
        token = csrf(client, f"/reset-password?token={reset_token}")
        response = client.post(
            "/reset-password",
            data={
                "csrf_token": token,
                "token": reset_token,
                "password": NEW_PASSWORD,
                "confirm_password": NEW_PASSWORD,
            },
        )
        check("password updated", response.status_code == 200)
        check("confirmation shown", "Password updated" in response.text)

        response = client.get(f"/reset-password?token={reset_token}")
        check("reset token burned", 'name="token"' not in response.text)

        print("\nOld password no longer works")
        client.cookies.clear()
        token = csrf(client, "/login")
        response = client.post(
            "/login",
            data={
                "csrf_token": token,
                "email": "alex@example.com",
                "password": PASSWORD,
                "next": "/dashboard",
            },
        )
        check("old password rejected", response.status_code == 401)

        token = csrf(client, "/login")
        response = client.post(
            "/login",
            data={
                "csrf_token": token,
                "email": "alex@example.com",
                "password": NEW_PASSWORD,
                "next": "/dashboard",
            },
        )
        check("new password accepted", response.status_code == 303)

        print("\nOpen redirect guard")
        client.cookies.clear()
        token = csrf(client, "/login?next=https://evil.test/pwn")
        check(
            "external next scrubbed in form",
            'value="https://evil.test/pwn"' not in client.get("/login?next=https://evil.test/pwn").text,
        )
        response = client.post(
            "/login",
            data={
                "csrf_token": token,
                "email": "alex@example.com",
                "password": NEW_PASSWORD,
                "next": "https://evil.test/pwn",
            },
        )
        check("external next ignored", response.status_code == 303, f"status {response.status_code}")
        check("redirect stays local", response.headers.get("location") == "/dashboard", str(dict(response.headers)))

        print("\nSign out")
        token = csrf(client, "/dashboard")
        response = client.post("/logout", data={"csrf_token": token})
        check("logout redirects", response.status_code == 303)
        check("session cookie cleared", settings_session_cookie(client) is None)
        response = client.get("/dashboard")
        check("dashboard locked again", response.status_code == 303)

        print("\nBrute force lockout")
        for _ in range(5):
            client.cookies.clear()
            token = csrf(client, "/login")
            client.post(
                "/login",
                data={
                    "csrf_token": token,
                    "email": "alex@example.com",
                    "password": "nope-nope-nope",
                },
            )
        client.cookies.clear()
        token = csrf(client, "/login")
        response = client.post(
            "/login",
            data={
                "csrf_token": token,
                "email": "alex@example.com",
                "password": NEW_PASSWORD,
            },
        )
        check("locked out after 5 failures", response.status_code == 429)
        check("lockout message", "Too many failed attempts" in response.text)
        db.clear_login_failures("alex@example.com")

        print("\nSession integrity")
        client.cookies.clear()
        response = client.get("/dashboard", headers={"cookie": "motorscope_session=forged.value"})
        check("forged session rejected", response.status_code == 303)

        print("\nError handling")
        response = client.get("/does-not-exist", headers={"accept": "text/html"})
        check("404 page is styled", response.status_code == 404)
        check("404 message", "does not exist" in response.text)

    TEMP_DB.unlink(missing_ok=True)
    for suffix in ("-wal", "-shm"):
        Path(str(TEMP_DB) + suffix).unlink(missing_ok=True)

    print()
    if failures:
        print(f"{len(failures)} check(s) failed:")
        for name in failures:
            print(f"  - {name}")
        return 1
    print("All checks passed.")
    return 0


def settings_session_cookie(client: TestClient):
    return client.cookies.get("motorscope_session")


if __name__ == "__main__":
    raise SystemExit(main())
