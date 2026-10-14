"""Jinja2 environment.

`csrf_token` is exposed as a global rather than a per-render context value so no
template can accidentally render a form without it.
"""

from __future__ import annotations

from contextvars import ContextVar
from datetime import datetime

from fastapi.templating import Jinja2Templates

from .config import STATIC_DIR, TEMPLATES_DIR, settings

_csrf: ContextVar[str] = ContextVar("motorscope_csrf", default="")


def _asset_version() -> str:
    """Fingerprint the stylesheet so browsers refetch it after an edit.

    Derived from the file's mtime rather than a hand-maintained counter, so it
    changes exactly when the CSS does.
    """
    css = STATIC_DIR / "css" / "style.css"
    try:
        return str(int(css.stat().st_mtime))
    except OSError:
        return "0"


templates = Jinja2Templates(directory=str(TEMPLATES_DIR))
templates.env.globals["app_name"] = "MotorScope"
templates.env.globals["smtp_configured"] = settings.smtp_configured
templates.env.globals["google_enabled"] = settings.google_enabled
templates.env.globals["csrf_token"] = lambda: _csrf.get()
templates.env.globals["asset_version"] = _asset_version
templates.env.globals["base_url"] = settings.base_url


def _greeting() -> str:
    """Time-of-day greeting for the dashboard.

    Rendered server-side so the dashboard is correct on first paint and readable to a
    screen reader without waiting for client-side JavaScript.
    """
    hour = datetime.now().hour
    if hour < 12:
        return "morning"
    if hour < 18:
        return "afternoon"
    return "evening"


templates.env.globals["greeting"] = _greeting


def set_csrf(value: str) -> None:
    _csrf.set(value)


def current_csrf() -> str:
    return _csrf.get()
