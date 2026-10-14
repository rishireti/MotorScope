"""Application configuration loaded from environment / .env file."""

from __future__ import annotations

import os
import secrets
from dataclasses import dataclass
from pathlib import Path

from dotenv import load_dotenv

BASE_DIR = Path(__file__).resolve().parent.parent
DATA_DIR = BASE_DIR / "data"
TEMPLATES_DIR = BASE_DIR / "templates"
STATIC_DIR = BASE_DIR / "static"

load_dotenv(BASE_DIR / ".env")


def _env_bool(name: str, default: bool = False) -> bool:
    raw = os.getenv(name)
    if raw is None:
        return default
    return raw.strip().lower() in {"1", "true", "yes", "on"}


def _env_int(name: str, default: int) -> int:
    raw = _env_str(name)
    if raw is None:
        return default
    try:
        return int(raw)
    except ValueError:
        return default


def _env_str(name: str, default: str | None = None) -> str | None:
    """Read an env var, treating a blank value the same as an unset one.

    .env files list every option with an empty value as documentation, so a blank
    line must fall back to the default rather than override it.
    """
    raw = os.getenv(name)
    if raw is None or not raw.strip():
        return default
    return raw.strip()


def _load_or_create_secret() -> str:
    """Persist a random signing key so sessions survive a restart."""
    env_secret = os.getenv("SECRET_KEY")
    if env_secret:
        return env_secret

    DATA_DIR.mkdir(parents=True, exist_ok=True)
    key_file = DATA_DIR / "secret.key"
    if key_file.exists():
        return key_file.read_text(encoding="utf-8").strip()

    key = secrets.token_urlsafe(48)
    key_file.write_text(key, encoding="utf-8")
    try:
        key_file.chmod(0o600)
    except OSError:  # pragma: no cover - Windows ACLs
        pass
    return key


@dataclass(frozen=True)
class Settings:
    secret_key: str
    database_path: Path
    base_url: str

    smtp_host: str
    smtp_port: int
    smtp_user: str
    smtp_password: str
    smtp_use_ssl: bool
    smtp_use_starttls: bool
    mail_from: str
    mail_from_name: str

    session_cookie: str
    session_max_age: int
    verify_token_ttl: int
    reset_token_ttl: int
    max_login_attempts: int
    lockout_seconds: int
    password_min_length: int

    google_client_id: str
    google_client_secret: str
    google_redirect_uri: str

    @property
    def smtp_configured(self) -> bool:
        return bool(self.smtp_host and self.smtp_user and self.smtp_password)

    @property
    def google_enabled(self) -> bool:
        return bool(self.google_client_id and self.google_client_secret)


def load_settings() -> Settings:
    smtp_user = _env_str("SMTP_USER", "") or ""
    # BASE_URL drives email links and the secure-cookie flag. On Render we can
    # infer it from the service's public URL so a fresh deploy is correct before
    # any environment variables are filled in.
    base_url = (
        _env_str("BASE_URL")
        or _env_str("RENDER_EXTERNAL_URL")
        or "http://127.0.0.1:8000"
    ).rstrip("/")
    port = _env_int("SMTP_PORT", 587)
    return Settings(
        secret_key=_load_or_create_secret(),
        database_path=Path(
            _env_str("DATABASE_PATH", str(DATA_DIR / "motorscope.db"))
        ).resolve(),
        base_url=base_url,
        smtp_host=_env_str("SMTP_HOST", "") or "",
        smtp_port=port,
        smtp_user=smtp_user,
        smtp_password=_env_str("SMTP_PASS", "") or "",
        smtp_use_ssl=_env_bool("SMTP_SSL", port == 465),
        smtp_use_starttls=_env_bool("SMTP_STARTTLS", port != 465),
        mail_from=_env_str("MAIL_FROM", smtp_user) or "",
        mail_from_name=_env_str("MAIL_FROM_NAME", "MotorScope") or "MotorScope",
        session_cookie=_env_str("SESSION_COOKIE", "motorscope_session")
        or "motorscope_session",
        session_max_age=_env_int("SESSION_MAX_AGE_MINUTES", 60 * 12) * 60,
        verify_token_ttl=_env_int("VERIFY_TOKEN_TTL_MINUTES", 60 * 24) * 60,
        reset_token_ttl=_env_int("RESET_TOKEN_TTL_MINUTES", 30) * 60,
        max_login_attempts=_env_int("MAX_LOGIN_ATTEMPTS", 5),
        lockout_seconds=_env_int("LOGIN_LOCKOUT_SECONDS", 300),
        password_min_length=_env_int("PASSWORD_MIN_LENGTH", 10),
        google_client_id=_env_str("GOOGLE_CLIENT_ID", "") or "",
        google_client_secret=_env_str("GOOGLE_CLIENT_SECRET", "") or "",
        google_redirect_uri=_env_str("GOOGLE_REDIRECT_URI", f"{base_url}/auth/google/callback")
        or "",
    )


settings = load_settings()
