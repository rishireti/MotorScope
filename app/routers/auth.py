"""Account registration, email verification, login, password reset, Google sign-in."""

from __future__ import annotations

import logging
from datetime import datetime, timezone
from urllib.parse import urlparse

from email_validator import EmailNotValidError, validate_email
from fastapi import APIRouter, Depends, Form, Query, Request
from fastapi.responses import HTMLResponse, RedirectResponse

from .. import db, google_auth, mailer, security
from ..config import settings
from ..deps import (
    CurrentUser,
    clear_session_cookie,
    optional_user,
    set_session_cookie,
    validate_csrf,
)
from ..templates_env import templates

logger = logging.getLogger("motorscope.auth")
router = APIRouter()

GOOGLE_STATE_COOKIE = "motorscope_google"


def _safe_next(candidate: str | None) -> str:
    if not candidate:
        return "/dashboard"
    parsed = urlparse(candidate)
    if parsed.scheme or parsed.netloc or not candidate.startswith("/"):
        return "/dashboard"
    if candidate.startswith("//"):
        return "/dashboard"
    return candidate


def _page(request: Request, name: str, status_code: int = 200, **context) -> HTMLResponse:
    return templates.TemplateResponse(
        request=request,
        name=name,
        context=context,
        status_code=status_code,
    )


def _validate_email(raw: str) -> tuple[str | None, str | None]:
    try:
        result = validate_email(raw.strip(), check_deliverability=False)
    except EmailNotValidError as exc:
        logger.debug("Rejected email %r: %s", raw, exc)
        return None, "Enter a valid email address, for example you@example.com."
    return result.normalized.lower(), None


def _issue_and_send_verification(user) -> bool:
    token = security.generate_token()
    db.store_token(
        user["id"], security.hash_token(token), db.PURPOSE_VERIFY, settings.verify_token_ttl
    )
    return mailer.send_verification_email(user["email"], user["display_name"], token)


# ------------------------------------------------------------------- registration


@router.get("/signup", response_class=HTMLResponse)
async def signup_form(request: Request, user: CurrentUser | None = Depends(optional_user)):
    if user is not None:
        return RedirectResponse("/dashboard", status_code=303)
    return _page(
        request,
        "signup.html",
        title="Create your account",
        user=None,
        form={},
        errors={},
        password_min_length=settings.password_min_length,
    )


@router.post("/signup", response_class=HTMLResponse)
async def signup_submit(
    request: Request,
    display_name: str = Form(""),
    email: str = Form(""),
    password: str = Form(""),
    confirm_password: str = Form(""),
    csrf_token: str = Form(""),
    user: CurrentUser | None = Depends(optional_user),
):
    if user is not None:
        return RedirectResponse("/dashboard", status_code=303)

    form = {
        "display_name": display_name.strip(),
        "email": email.strip(),
    }
    errors: dict[str, str] = {}

    if not validate_csrf(request, csrf_token):
        errors["form"] = "Your session expired before the form was sent. Please try again."

    if len(form["display_name"]) < 2:
        errors["display_name"] = "Enter a name with at least 2 characters."

    normalized_email, email_error = _validate_email(email)
    if email_error or normalized_email is None:
        errors["email"] = email_error or "Enter a valid email address."

    password_errors = _check_password_strength(password, confirm_password)
    errors.update(password_errors)

    if errors:
        return _page(
            request,
            "signup.html",
            status_code=400,
            title="Create your account",
            user=None,
            form=form,
            errors=errors,
            password_min_length=settings.password_min_length,
        )

    existing = db.get_user_by_email(normalized_email)
    if existing is not None:
        return _page(
            request,
            "signup.html",
            status_code=409,
            title="Create your account",
            user=None,
            form=form,
            errors={
                "email": "An account already exists for this address. "
                "Sign in, or reset your password if you have forgotten it."
            },
            password_min_length=settings.password_min_length,
        )

    user_id = db.create_user(normalized_email, form["display_name"], security.hash_password(password))
    if user_id is None:
        return _page(
            request,
            "signup.html",
            status_code=409,
            title="Create your account",
            user=None,
            form=form,
            errors={"email": "An account already exists for this address."},
            password_min_length=settings.password_min_length,
        )

    created = db.get_user_by_id(user_id)
    _issue_and_send_verification(created)

    return _page(
        request,
        "check_email.html",
        title="Confirm your email",
        user=None,
        email=created["email"],
        resend_available=bool(settings.smtp_configured),
    )


def _check_password_strength(password: str, confirm_password: str) -> dict[str, str]:
    errors: dict[str, str] = {}
    if len(password) < settings.password_min_length:
        errors["password"] = (
            f"Use at least {settings.password_min_length} characters."
        )
    elif password.isalpha() or password.isdigit():
        errors["password"] = "Mix in letters and numbers to make this harder to guess."
    if confirm_password and password != confirm_password:
        errors["confirm_password"] = "The two passwords do not match."
    return errors


# -------------------------------------------------------------- email verification


@router.get("/verify-email", response_class=HTMLResponse)
async def verify_email(
    request: Request,
    token: str = Query(default=""),
    user: CurrentUser | None = Depends(optional_user),
):
    if not token:
        return _page(
            request,
            "verify_result.html",
            title="Invalid link",
            user=user,
            ok=False,
            headline="That confirmation link is incomplete",
            detail="Open the link straight from the email we sent you.",
        )

    row = db.consume_token(security.hash_token(token), db.PURPOSE_VERIFY)
    if row is None:
        return _page(
            request,
            "verify_result.html",
            status_code=400,
            title="Link expired",
            user=user,
            ok=False,
            headline="This confirmation link is no longer valid",
            detail="Links can only be used once and expire after 24 hours. "
            "Request a fresh one from the sign-in page.",
        )

    db.mark_email_verified(int(row["user_id"]))
    return _page(
        request,
        "verify_result.html",
        title="Email confirmed",
        user=user,
        ok=True,
        headline="Your email is confirmed",
        detail="Your MotorScope account is active. Sign in to continue.",
    )


@router.post("/resend-verification", response_class=HTMLResponse)
async def resend_verification(
    request: Request,
    email: str = Form(""),
    csrf_token: str = Form(""),
    user: CurrentUser | None = Depends(optional_user),
):
    if not validate_csrf(request, csrf_token):
        return _page(
            request,
            "forgot_password.html",
            status_code=403,
            title="Reset your password",
            user=None,
            form={"email": email},
            errors={"form": "Your session expired. Please try again."},
            message=None,
        )

    normalized_email, _ = _validate_email(email)
    row = db.get_user_by_email(normalized_email) if normalized_email else None
    if row is not None and not row["email_verified"]:
        _issue_and_send_verification(row)

    return _page(
        request,
        "check_email.html",
        title="Confirm your email",
        user=None,
        email=(row["email"] if row is not None else (normalized_email or email)),
        resend_available=bool(settings.smtp_configured),
    )


# ------------------------------------------------------------------------- login


@router.get("/login", response_class=HTMLResponse)
async def login_form(
    request: Request,
    next: str = Query(default=""),
    user: CurrentUser | None = Depends(optional_user),
):
    if user is not None:
        return RedirectResponse(_safe_next(next), status_code=303)
    return _page(
        request,
        "login.html",
        title="Sign in",
        user=None,
        form={"email": ""},
        errors={},
        next_url=_safe_next(next),
    )


@router.post("/login", response_class=HTMLResponse)
async def login_submit(
    request: Request,
    email: str = Form(""),
    password: str = Form(""),
    next: str = Form(""),
    csrf_token: str = Form(""),
    user: CurrentUser | None = Depends(optional_user),
):
    if user is not None:
        return RedirectResponse(_safe_next(next), status_code=303)

    form = {"email": email.strip()}
    next_url = _safe_next(next)

    def render(error: str, status_code: int = 401) -> HTMLResponse:
        return _page(
            request,
            "login.html",
            status_code=status_code,
            title="Sign in",
            user=None,
            form=form,
            errors={"form": error},
            next_url=next_url,
        )

    if not validate_csrf(request, csrf_token):
        return render("Your session expired. Please try again.", status_code=403)

    identifier = db.normalize_email(email)
    if not identifier or not password:
        return render("Enter both your email address and password.")

    failures, locked_until = db.login_lock_state(identifier)
    now = int(datetime.now(timezone.utc).timestamp())
    if locked_until > now:
        wait = max(1, (locked_until - now) // 60)
        return render(
            f"Too many failed attempts. Try again in about {wait} minute"
            f"{'s' if wait != 1 else ''}.",
            status_code=429,
        )

    row = db.get_user_by_email(identifier)
    stored_hash = row["password_hash"] if row is not None else _DUMMY_HASH
    password_ok = security.verify_password(password, stored_hash or "")

    if row is not None and not row["password_hash"]:
        if row["google_subject"]:
            return render(
                "This account signs in with Google. Use the Google button, "
                "or reset your password to add a password.",
                status_code=401,
            )
        return render("No password is set for this account.", status_code=401)

    if row is None or not password_ok:
        db.record_login_failure(identifier)
        if failures + 1 >= settings.max_login_attempts:
            db.apply_lockout(identifier)
            return render(
                "Too many failed attempts. This address is locked for "
                f"{settings.lockout_seconds // 60} minutes.",
                status_code=429,
            )
        return render("That email and password combination is not recognised.")

    if not row["email_verified"]:
        return render(
            "Confirm your email address before signing in. "
            "Use the link we sent you, or request a new one.",
            status_code=403,
        )

    db.clear_login_failures(identifier)
    db.record_login(int(row["id"]))

    response = RedirectResponse(next_url, status_code=303)
    set_session_cookie(response, int(row["id"]), row["email"])
    return response


# A real hash of a random value, so failed lookups cost the same as failed checks.
_DUMMY_HASH = security.hash_password("motorscope::nonexistent-account")


# --------------------------------------------------------------------- password reset


@router.get("/forgot-password", response_class=HTMLResponse)
async def forgot_password_form(
    request: Request, user: CurrentUser | None = Depends(optional_user)
):
    if user is not None:
        return RedirectResponse("/dashboard", status_code=303)
    return _page(
        request,
        "forgot_password.html",
        title="Reset your password",
        user=None,
        form={"email": ""},
        errors={},
        message=None,
    )


@router.post("/forgot-password", response_class=HTMLResponse)
async def forgot_password_submit(
    request: Request,
    email: str = Form(""),
    csrf_token: str = Form(""),
    user: CurrentUser | None = Depends(optional_user),
):
    if user is not None:
        return RedirectResponse("/dashboard", status_code=303)

    if not validate_csrf(request, csrf_token):
        return _page(
            request,
            "forgot_password.html",
            status_code=403,
            title="Reset your password",
            user=None,
            form={"email": email.strip()},
            errors={"form": "Your session expired. Please try again."},
            message=None,
        )

    normalized_email, _ = _validate_email(email)
    row = db.get_user_by_email(normalized_email) if normalized_email else None
    if row is not None:
        token = security.generate_token()
        db.store_token(
            row["id"], security.hash_token(token), db.PURPOSE_RESET, settings.reset_token_ttl
        )
        delivered = mailer.send_password_reset_email(
            row["email"], row["display_name"], token
        )
        if not delivered:
            logger.warning("Password reset email for %s was not delivered", row["email"])

    return _page(
        request,
        "reset_sent.html",
        title="Check your email",
        user=None,
        email=normalized_email or email.strip(),
        smtp_ready=settings.smtp_configured,
    )


@router.get("/reset-password", response_class=HTMLResponse)
async def reset_password_form(
    request: Request,
    token: str = Query(default=""),
    user: CurrentUser | None = Depends(optional_user),
):
    valid = bool(token) and _token_is_usable(token, db.PURPOSE_RESET)
    if user is not None:
        return RedirectResponse("/dashboard", status_code=303)
    return _page(
        request,
        "reset_password.html",
        title="Choose a new password",
        user=None,
        token=token,
        token_valid=valid,
        errors={},
        password_min_length=settings.password_min_length,
    )


def _token_is_usable(token: str, purpose: str) -> bool:
    with db.get_connection() as connection:
        row = connection.execute(
            "SELECT expires_at, used_at FROM auth_tokens WHERE token_hash = ? AND purpose = ?",
            (security.hash_token(token), purpose),
        ).fetchone()
    if row is None or row["used_at"] is not None:
        return False
    return int(row["expires_at"]) >= int(datetime.now(timezone.utc).timestamp())


@router.post("/reset-password", response_class=HTMLResponse)
async def reset_password_submit(
    request: Request,
    token: str = Form(""),
    password: str = Form(""),
    confirm_password: str = Form(""),
    csrf_token: str = Form(""),
    user: CurrentUser | None = Depends(optional_user),
):
    def render(status_code: int, token_valid: bool, errors: dict[str, str]) -> HTMLResponse:
        return _page(
            request,
            "reset_password.html",
            status_code=status_code,
            title="Choose a new password",
            user=None,
            token=token,
            token_valid=token_valid,
            errors=errors,
            password_min_length=settings.password_min_length,
        )

    if not validate_csrf(request, csrf_token):
        return render(403, False, {"form": "Your session expired. Please use the link again."})

    errors = _check_password_strength(password, confirm_password)
    if not token or not _token_is_usable(token, db.PURPOSE_RESET):
        errors["form"] = "This reset link has expired or has already been used. Request a new one."
        return render(400, False, errors)
    if errors:
        return render(400, True, errors)

    row = db.consume_token(security.hash_token(token), db.PURPOSE_RESET)
    if row is None:
        return render(
            400,
            False,
            {"form": "This reset link has expired or has already been used. Request a new one."},
        )

    db.update_password(int(row["user_id"]), security.hash_password(password))

    return _page(
        request,
        "reset_done.html",
        title="Password updated",
        user=None,
        email=_email_for_user(row["user_id"]),
    )


def _email_for_user(user_id: int) -> str:
    row = db.get_user_by_id(int(user_id))
    return row["email"] if row is not None else ""


# ------------------------------------------------------------------------- logout


@router.post("/logout")
async def logout(request: Request, csrf_token: str = Form("")):
    if not validate_csrf(request, csrf_token):
        return RedirectResponse("/dashboard", status_code=303)
    response = RedirectResponse("/login", status_code=303)
    clear_session_cookie(response)
    return response


# ------------------------------------------------------------------ Google sign-in


def _google_error_page(request: Request, headline: str, detail: str = "") -> HTMLResponse:
    return _page(
        request,
        "google_error.html",
        status_code=400,
        title="Google sign-in",
        user=None,
        headline=headline,
        detail=detail,
    )


@router.get("/auth/google")
async def google_sign_in(
    request: Request,
    next: str = Query(default="/dashboard"),
    user: CurrentUser | None = Depends(optional_user),
):
    if user is not None:
        return RedirectResponse(_safe_next(next), status_code=303)

    try:
        state = google_auth.new_state()
        verifier = google_auth.new_pkce_verifier()
        url = google_auth.build_authorization_url(state, verifier)
    except google_auth.GoogleAuthError as exc:
        return _google_error_page(request, exc.message, exc.description)

    response = RedirectResponse(url, status_code=303)
    response.set_cookie(
        GOOGLE_STATE_COOKIE,
        security.issue_oauth_state(state, verifier, _safe_next(next)),
        max_age=600,
        httponly=True,
        samesite="lax",
        secure=settings.base_url.startswith("https://"),
        path="/",
    )
    return response


@router.get("/auth/google/callback")
async def google_callback(
    request: Request,
    code: str = Query(default=""),
    state: str = Query(default=""),
    error: str = Query(default=""),
):
    if error:
        return _google_error_page(request, google_auth.describe_error(error))

    stored = security.read_oauth_state(request.cookies.get(GOOGLE_STATE_COOKIE))
    if not stored or not security.constant_time_equals(str(stored.get("state", "")), state):
        return _google_error_page(
            request,
            "That sign-in attempt could not be verified.",
            "The link may have expired or been tampered with. Start again from the sign-in page.",
        )
    if not code:
        return _google_error_page(request, "Google did not send an authorization code.")

    try:
        token_response = google_auth.exchange_code(code, str(stored.get("verifier", "")))
        profile = google_auth.fetch_profile(str(token_response.get("access_token", "")))
    except google_auth.GoogleAuthError as exc:
        return _google_error_page(request, exc.message, exc.description)

    next_path = _safe_next(stored.get("next"))

    if not profile.email_verified:
        return _google_error_page(
            request,
            "Google has not verified this email address.",
            "MotorScope only creates accounts for addresses Google has confirmed.",
        )

    user_row, created, problem = _resolve_google_user(profile)
    if problem is not None:
        return _google_error_page(request, problem[0], problem[1])

    db.record_login(user_row["id"])
    response = RedirectResponse(next_path, status_code=303)
    set_session_cookie(response, int(user_row["id"]), user_row["email"])
    response.delete_cookie(GOOGLE_STATE_COOKIE, path="/")
    if created:
        logger.info("Created account %s via Google", user_row["email"])
    return response


def _resolve_google_user(
    profile: google_auth.GoogleProfile,
) -> tuple[object, bool, tuple[str, str] | None]:
    """Map a Google profile to a local account.

    Returns (user_row, created, problem). A problem is a user-facing message shown
    instead of signing anyone in.
    """
    existing_subject = db.get_user_by_google_subject(profile.subject)
    if existing_subject is not None:
        return existing_subject, False, None

    existing_email = db.get_user_by_email(profile.email)
    if existing_email is not None:
        if not existing_email["email_verified"]:
            return None, False, (
                "An unconfirmed account already uses this email address.",
                "Confirm that account or reset its password first, then sign in with Google.",
            )
        if not db.link_google_account(
            int(existing_email["id"]), profile.subject, profile.display_name
        ):
            return None, False, (
                "This Google account is already linked elsewhere.",
                "Use the account it is linked to, or contact an administrator.",
            )
        logger.info("Linked Google to existing account %s", existing_email["email"])
        return db.get_user_by_id(int(existing_email["id"])), False, None

    user_id = db.create_user(
        profile.email,
        profile.display_name,
        None,
        email_verified=True,
        auth_provider="google",
        google_subject=profile.subject,
    )
    if user_id is None:
        return None, False, (
            "An account already exists for this address.",
            "Sign in with your password, or reset it if you have forgotten it.",
        )
    return db.get_user_by_id(user_id), True, None


@router.post("/auth/google/disconnect")
async def google_disconnect(request: Request, csrf_token: str = Form("")):
    user = await optional_user(request)
    if user is None or not validate_csrf(request, csrf_token):
        return RedirectResponse("/account", status_code=303)

    if not db.has_password(user.id):
        return RedirectResponse("/account?needs_password=1", status_code=303)

    db.unlink_google_account(user.id)
    return RedirectResponse("/account?unlinked=1", status_code=303)
