"""Shared FastAPI dependencies: session identity and CSRF protection."""

from __future__ import annotations

from dataclasses import dataclass
from urllib.parse import quote

from fastapi import HTTPException, Request

from . import db, security
from .config import settings

CSRF_COOKIE = "motorscope_csrf"


@dataclass(frozen=True)
class CurrentUser:
    id: int
    email: str
    display_name: str
    email_verified: bool


def _user_from_session(request: Request) -> CurrentUser | None:
    data = security.read_session(request.cookies.get(settings.session_cookie))
    if not data:
        return None

    row = db.get_user_by_id(int(data.get("uid", 0)))
    if row is None:
        return None
    if db.normalize_email(row["email"]) != db.normalize_email(data.get("email", "")):
        return None
    return CurrentUser(
        id=int(row["id"]),
        email=row["email"],
        display_name=row["display_name"],
        email_verified=bool(row["email_verified"]),
    )


async def optional_user(request: Request) -> CurrentUser | None:
    return _user_from_session(request)


async def require_user(request: Request) -> CurrentUser:
    user = _user_from_session(request)
    if user is None:
        raise HTTPException(
            status_code=401,
            detail="Sign in to continue",
            headers={"location": login_redirect_url(request)},
        )
    return user


def login_redirect_url(request: Request) -> str:
    target = request.url.path
    if request.url.query:
        target = f"{target}?{request.url.query}"
    return f"/login?next={quote(target, safe='')}"


def csrf_token(request: Request) -> str:
    """Token for the hidden form field: always mirrors the CSRF cookie."""
    return request.cookies.get(CSRF_COOKIE, "")


def validate_csrf(request: Request, submitted: str) -> bool:
    """Double-submit cookie check: the form value must match the cookie."""
    expected = request.cookies.get(CSRF_COOKIE, "")
    if not expected or not submitted:
        return False
    return security.constant_time_equals(expected, submitted)


def set_session_cookie(response, user_id: int, email: str) -> None:
    response.set_cookie(
        settings.session_cookie,
        security.issue_session(user_id, email),
        max_age=settings.session_max_age,
        httponly=True,
        samesite="lax",
        secure=settings.base_url.startswith("https://"),
        path="/",
    )


def clear_session_cookie(response) -> None:
    response.delete_cookie(settings.session_cookie, path="/")
