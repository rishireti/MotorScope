"""End-to-end tests for Google sign-in, with Google's endpoints stubbed out.

No network calls and no real credentials are used. httpx is monkeypatched so the
token endpoint and userinfo endpoint return controlled responses.

Usage:  .venv\\Scripts\\python.exe -m tests.test_google_auth
"""

from __future__ import annotations

import base64
import hashlib
import os
import re
import tempfile
from pathlib import Path
from urllib.parse import parse_qs, urlparse

TEMP_DB = Path(tempfile.gettempdir()) / "motorscope_google_test.db"
os.environ["DATABASE_PATH"] = str(TEMP_DB)
os.environ["SECRET_KEY"] = "google-test-key"
os.environ["GOOGLE_CLIENT_ID"] = "test-client-id.apps.googleusercontent.com"
os.environ["GOOGLE_CLIENT_SECRET"] = "test-client-secret"
os.environ["GOOGLE_REDIRECT_URI"] = "http://127.0.0.1:8000/auth/google/callback"
TEMP_DB.unlink(missing_ok=True)

import httpx  # noqa: E402
from fastapi.testclient import TestClient  # noqa: E402

from app import db, mailer, security  # noqa: E402
from app.main import app  # noqa: E402

failures: list[str] = []

# Every request Google would receive is recorded here for assertions.
CALLS: list[tuple[str, str, dict]] = []
# Verifier from the most recent /auth/google hop, to compare against the token call.
SENT_VERIFIER: list[str] = []
TOKEN_RESPONSE: dict = {"access_token": "fake-access-token", "token_type": "Bearer"}
USERINFO_RESPONSE: dict = {
    "sub": "1234567890",
    "email": "alex@example.com",
    "email_verified": True,
    "name": "Alex Mercer",
    "picture": "https://lh3.googleusercontent.com/a/photo",
}
TOKEN_STATUS = 200
USERINFO_STATUS = 200


class FakeResponse:
    def __init__(self, status_code: int, payload: dict):
        self.status_code = status_code
        self._payload = payload
        self.text = str(payload)

    def json(self) -> dict:
        return self._payload


def fake_post(url: str, data: dict | None = None, timeout: float = 0.0, **kwargs) -> FakeResponse:
    CALLS.append(("POST", url, data or {}))
    return FakeResponse(TOKEN_STATUS, TOKEN_RESPONSE)


def fake_get(url: str, headers: dict | None = None, timeout: float = 0.0, **kwargs) -> FakeResponse:
    CALLS.append(("GET", url, headers or {}))
    return FakeResponse(USERINFO_STATUS, USERINFO_RESPONSE)


httpx.post = fake_post
httpx.get = fake_get
mailer.send_verification_email = lambda *a: True
mailer.send_password_reset_email = lambda *a: True


def check(label: str, condition: bool, extra: str = "") -> None:
    print(f"  [{'PASS' if condition else 'FAIL'}] {label}{(' -> ' + extra) if extra and not condition else ''}")
    if not condition:
        failures.append(label)


def csrf(client: TestClient, path: str) -> str:
    match = re.search(r'name="csrf_token" value="([^"]+)"', client.get(path).text)
    return match.group(1) if match else ""


def sign_in(client: TestClient, **overrides) -> tuple:
    """Walk the browser through Google and return the callback response."""
    profile = {**USERINFO_RESPONSE, **overrides}
    USERINFO_RESPONSE.update(profile)

    start = client.get("/auth/google?next=/dashboard")
    assert start.status_code == 303, start.status_code
    assert "accounts.google.com" in start.headers["location"]
    state = parse_qs(urlparse(start.headers["location"]).query)["state"][0]
    SENT_VERIFIER.clear()
    SENT_VERIFIER.append(security.read_oauth_state(client.cookies.get("motorscope_google"))["verifier"])
    CALLS.clear()
    return client.get(f"/auth/google/callback?code=auth-code-123&state={state}")


def main() -> int:
    global TOKEN_STATUS
    with TestClient(app, follow_redirects=False) as client:
        print("\nAuthorization request")
        response = client.get("/auth/google?next=/dashboard")
        check("redirects to Google", response.status_code == 303)
        location = response.headers["location"]
        query = parse_qs(urlparse(location).query)
        check("host is accounts.google.com", urlparse(location).netloc == "accounts.google.com")
        check("asks for code", query["response_type"] == ["code"])
        check("requests openid/email/profile", set(query["scope"][0].split()) == {"openid", "email", "profile"})
        check("sends client id", query["client_id"] == ["test-client-id.apps.googleusercontent.com"])
        check("sends redirect uri", query["redirect_uri"] == ["http://127.0.0.1:8000/auth/google/callback"])
        check("uses PKCE S256", query["code_challenge_method"] == ["S256"])
        check("has code challenge", len(query["code_challenge"][0]) > 20)
        check("sends a state value", len(query["state"][0]) > 20)
        check("state cookie set", "motorscope_google" in client.cookies)

        # The challenge Google receives must be derived from the verifier we
        # stored for the token exchange, otherwise Google rejects the code.
        stored = security.read_oauth_state(client.cookies.get("motorscope_google"))
        expected = base64.urlsafe_b64encode(
            hashlib.sha256(stored["verifier"].encode("ascii")).digest()
        ).rstrip(b"=").decode("ascii")
        check("challenge matches stored verifier", query["code_challenge"][0] == expected)

        print("\nState validation")
        response = client.get("/auth/google/callback?code=abc&state=tampered-state")
        check("rejects mismatched state", response.status_code == 400)
        check("shows verification error", "could not be verified" in response.text)
        check("no account created", db.count_users() == 0)

        client.get("/auth/google")
        response = client.get("/auth/google/callback?code=abc&state=tampered")
        check("no exchange on bad state", not any(c[0] == "POST" for c in CALLS))

        print("\nDeclined consent")
        response = client.get("/auth/google/callback?error=access_denied")
        check("handles denial", response.status_code == 400)
        check("explains cancellation", "cancelled" in response.text)

        print("\nAccount creation")
        CALLS.clear()
        response = sign_in(client)
        check("redirects after callback", response.status_code == 303)
        check("goes to requested page", response.headers["location"] == "/dashboard")
        check("session cookie issued", client.cookies.get("motorscope_session") is not None)
        check("state cookie cleared", not client.cookies.get("motorscope_google"))
        check("account created", db.count_users() == 1)

        user = db.get_user_by_email("alex@example.com")
        check("email stored", user is not None)
        check("marked verified from Google", user["email_verified"] == 1)
        check("name taken from profile", user["display_name"] == "Alex Mercer")
        check("provider recorded", user["auth_provider"] == "google")
        check("subject stored", user["google_subject"] == "1234567890")
        check("no password set", user["password_hash"] is None)

        print("\nToken exchange request")
        token_call = next((c for c in CALLS if c[0] == "POST"), None)
        check("called Google token endpoint", token_call is not None)
        if token_call:
            check("correct token url", token_call[1] == "https://oauth2.googleapis.com/token")
            body = token_call[2]
            check("sent the code", body.get("code") == "auth-code-123")
            check("sent client secret", body.get("client_secret") == "test-client-secret")
            check("sent PKCE verifier", len(body.get("code_verifier", "")) > 20)
            check("authorization_code grant", body.get("grant_type") == "authorization_code")
            check("verifier sent matches the cookie", body.get("code_verifier") == SENT_VERIFIER[0])
        userinfo_call = next((c for c in CALLS if c[0] == "GET"), None)
        check("called userinfo endpoint", userinfo_call is not None)
        if userinfo_call:
            check("bearer token sent", userinfo_call[2].get("Authorization") == "Bearer fake-access-token")

        print("\nRepeat sign-in reuses the account")
        client.cookies.clear()
        response = sign_in(client)
        check("signed in again", response.status_code == 303)
        check("no duplicate account", db.count_users() == 1)

        print("\nExisting password account gets linked")
        db.create_user("sam@example.com", "Sam Vance", "hash-value", email_verified=True)
        client.cookies.clear()
        response = sign_in(client, sub="google-sub-sam", email="sam@example.com", email_verified=True, name="Sam Vance")
        check("sign-in allowed", response.status_code == 303)
        check("no new account", db.count_users() == 2)
        sam = db.get_user_by_email("sam@example.com")
        check("google linked to it", sam["google_subject"] == "google-sub-sam")
        check("password preserved", sam["password_hash"] == "hash-value")

        print("\nUnverified local account is not hijacked")
        db.create_user("victim@example.com", "Victim", "attacker-chosen-hash")
        client.cookies.clear()
        response = sign_in(client, sub="google-sub-attacker", email="victim@example.com", email_verified=True, name="Mallory")
        check("sign-in refused", response.status_code == 400)
        check("explains the conflict", "unconfirmed account" in response.text)
        victim = db.get_user_by_email("victim@example.com")
        check("no google subject attached", victim["google_subject"] is None)
        check("password untouched", victim["password_hash"] == "attacker-chosen-hash")
        check("still unverified", victim["email_verified"] == 0)

        print("\nUnverified Google email is refused")
        client.cookies.clear()
        response = sign_in(client, sub="google-sub-unverified", email="unverified@example.com", email_verified=False)
        check("sign-in refused", response.status_code == 400)
        check("explains why", "not verified" in response.text)
        check("no account created", db.get_user_by_email("unverified@example.com") is None)

        print("\nMissing email scope")
        client.cookies.clear()
        response = sign_in(client, sub="google-sub-noemail", email="")
        check("sign-in refused", response.status_code == 400)
        check("explains missing permission", "email address" in response.text)

        print("\nGoogle API failure")
        client.cookies.clear()
        TOKEN_STATUS = 400
        response = sign_in(client)
        check("shows failure page", response.status_code == 400)
        check("no crash", "Google rejected" in response.text)
        TOKEN_STATUS = 200

        print("\nNetwork failure")
        def boom(*args, **kwargs):
            raise httpx.ConnectError("no route to host")

        original_post = httpx.post
        httpx.post = boom
        client.cookies.clear()
        response = sign_in(client)
        check("handles offline", response.status_code == 400)
        check("explains connectivity", "Could not reach Google" in response.text)
        httpx.post = original_post

        print("\nPassword sign-in still works for linked accounts")
        client.cookies.clear()
        response = client.get("/login")
        check("login page loads", response.status_code == 200)
        token = csrf(client, "/login")
        response = client.post("/login", data={"csrf_token": token, "email": "sam@example.com", "password": "whatever"})
        check("linked account has no password yet", response.status_code == 401)

        print("\nUI reflects configuration")
        response = client.get("/login")
        check("google button on login", "Continue with Google" in response.text)
        check("button links to start flow", "/auth/google?next=" in response.text)
        response = client.get("/signup")
        check("google button on signup", "Sign up with Google" in response.text)
        response = client.get("/")
        check("landing page has no google button", "Continue with Google" not in response.text)
        response = client.get("/login")
        check("google logo svg rendered", "4285F4" in response.text)

        print("\nDisconnect")
        client.cookies.clear()
        response = sign_in(client, sub="google-sub-sam", email="sam@example.com", email_verified=True)
        check("logged back in", response.status_code == 303)
        account = client.get("/account")
        check("shows google linked", "Google is connected" in account.text)
        token = csrf(client, "/account")
        response = client.post("/auth/google/disconnect", data={"csrf_token": token})
        check("unlink redirects", response.status_code == 303)
        check("redirect flags it", response.headers["location"] == "/account?unlinked=1")
        check("google removed", db.get_user_by_email("sam@example.com")["google_subject"] is None)
        check("password still there", db.get_user_by_email("sam@example.com")["password_hash"] == "hash-value")

        print("\nDisconnect blocked without a password")
        client.cookies.clear()
        response = sign_in(client, sub="1234567890", email="alex@example.com", email_verified=True)
        account = client.get("/account")
        check("warns before unlink", "no password yet" in account.text)
        token = csrf(client, "/account")
        response = client.post("/auth/google/disconnect", data={"csrf_token": token})
        check("refuses to unlink", response.headers["location"] == "/account?needs_password=1")
        check("google still linked", db.get_user_by_email("alex@example.com")["google_subject"] is not None)

        print("\nCSRF on disconnect")
        client.cookies.clear()
        sign_in(client, sub="google-sub-sam", email="sam@example.com", email_verified=True)
        response = client.post("/auth/google/disconnect", data={"csrf_token": "forged"})
        check("forged token rejected", response.status_code == 303)
        check("no unlink happened", db.get_user_by_email("sam@example.com")["google_subject"] is not None)

    TEMP_DB.unlink(missing_ok=True)
    for suffix in ("-wal", "-shm"):
        Path(str(TEMP_DB) + suffix).unlink(missing_ok=True)

    print()
    if failures:
        print(f"{len(failures)} check(s) failed:")
        for name in failures:
            print(f"  - {name}")
        return 1
    print("All Google sign-in checks passed.")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
