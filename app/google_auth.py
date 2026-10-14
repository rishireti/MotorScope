"""Google OAuth 2.0 / OpenID Connect sign-in.

Uses the authorization code flow with PKCE. A confidential client secret is sent on
the token exchange, and the `state` value is checked by the route before anything
else happens.
"""

from __future__ import annotations

import base64
import hashlib
import logging
import secrets
from dataclasses import dataclass
from urllib.parse import urlencode

import httpx

from .config import settings

logger = logging.getLogger("motorscope.google")

AUTHORIZE_URL = "https://accounts.google.com/o/oauth2/v2/auth"
TOKEN_URL = "https://oauth2.googleapis.com/token"
USERINFO_URL = "https://openidconnect.googleapis.com/v1/userinfo"

SCOPES = ("openid", "email", "profile")

# Google returns these when the user declines consent or the app is misconfigured.
ERROR_MESSAGES = {
    "access_denied": "Google sign-in was cancelled.",
    "consent_denied": "Google sign-in was cancelled.",
    "invalid_scope": "Google rejected the requested permissions. Check SCOPES.",
    "unauthorized_client": "Google rejected this client. Check the client ID and secret.",
    "invalid_request": "Google rejected the sign-in request.",
    "server_error": "Google is having trouble right now. Try again shortly.",
    "temporarily_unavailable": "Google is having trouble right now. Try again shortly.",
}


class GoogleAuthError(Exception):
    """A failure that should be shown to the user as a page message."""

    def __init__(self, message: str, *, description: str = ""):
        super().__init__(message)
        self.message = message
        self.description = description


@dataclass(frozen=True)
class GoogleProfile:
    subject: str
    email: str
    display_name: str
    email_verified: bool


def new_state() -> str:
    return secrets.token_urlsafe(32)


def new_pkce_verifier() -> str:
    """Return a fresh PKCE code_verifier.

    The verifier must be kept and reused verbatim at token exchange, so it is
    returned separately from the challenge built by pkce_challenge().
    """
    return secrets.token_urlsafe(64)[:128]


def pkce_challenge(verifier: str) -> str:
    """Return the S256 code_challenge for the given code_verifier."""
    digest = hashlib.sha256(verifier.encode("ascii")).digest()
    return base64.urlsafe_b64encode(digest).rstrip(b"=").decode("ascii")


def build_authorization_url(state: str, code_verifier: str) -> str:
    if not settings.google_enabled:
        raise GoogleAuthError(
            "Google sign-in is not configured.",
            description="Add GOOGLE_CLIENT_ID and GOOGLE_CLIENT_SECRET to .env, then restart.",
        )

    params = {
        "client_id": settings.google_client_id,
        "redirect_uri": settings.google_redirect_uri,
        "response_type": "code",
        "scope": " ".join(SCOPES),
        "state": state,
        "code_challenge": pkce_challenge(code_verifier),
        "code_challenge_method": "S256",
        "access_type": "online",
        "prompt": "select_account",
    }
    return f"{AUTHORIZE_URL}?{urlencode(params)}"


def exchange_code(code: str, code_verifier: str) -> dict:
    payload = {
        "code": code,
        "client_id": settings.google_client_id,
        "client_secret": settings.google_client_secret,
        "redirect_uri": settings.google_redirect_uri,
        "grant_type": "authorization_code",
        "code_verifier": code_verifier,
    }
    try:
        response = httpx.post(TOKEN_URL, data=payload, timeout=20.0)
    except httpx.HTTPError as exc:
        raise GoogleAuthError(
            "Could not reach Google to finish sign-in.", description=str(exc)
        ) from exc

    if response.status_code != 200:
        logger.error("Google token exchange failed: %s %s", response.status_code, response.text)
        detail = ""
        try:
            detail = response.json().get("error_description", "")
        except ValueError:
            pass
        raise GoogleAuthError(
            "Google rejected the sign-in attempt.", description=detail
        )

    return response.json()


def fetch_profile(access_token: str) -> GoogleProfile:
    try:
        response = httpx.get(
            USERINFO_URL,
            headers={"Authorization": f"Bearer {access_token}"},
            timeout=20.0,
        )
    except httpx.HTTPError as exc:
        raise GoogleAuthError(
            "Could not read your Google profile.", description=str(exc)
        ) from exc

    if response.status_code != 200:
        logger.error("Google userinfo failed: %s %s", response.status_code, response.text)
        raise GoogleAuthError("Google did not return your profile details.")

    try:
        data = response.json()
    except ValueError as exc:
        raise GoogleAuthError("Google returned an unreadable profile.") from exc

    subject = str(data.get("sub") or "").strip()
    email = str(data.get("email") or "").strip().lower()
    if not subject or not email:
        raise GoogleAuthError(
            "Google did not share the email address MotorScope needs.",
            description="The openid and email permissions are required.",
        )

    verified = data.get("email_verified")
    if isinstance(verified, str):
        verified = verified.lower() in {"true", "1", "yes"}
    verified = bool(verified)

    return GoogleProfile(
        subject=subject,
        email=email,
        display_name=str(data.get("name") or email.split("@")[0]).strip(),
        email_verified=verified,
    )


def describe_error(error: str) -> str:
    return ERROR_MESSAGES.get(error, f"Google sign-in failed ({error}).")
