"""Public marketing page, protected dashboard, and account page."""

from __future__ import annotations

import logging
import tempfile
from pathlib import Path

from fastapi import APIRouter, Depends, File, Form, Query, Request, UploadFile
from fastapi.responses import HTMLResponse, RedirectResponse

from .. import charts, db, health_export, mobility
from ..config import settings
from ..deps import (
    CurrentUser,
    optional_user,
    require_user,
    validate_csrf,
)
from ..templates_env import templates

logger = logging.getLogger("motorscope.pages")
router = APIRouter()

#: Readings pulled for the dashboard trend chart.
CHART_READING_LIMIT = 120


@router.get("/", response_class=HTMLResponse)
async def landing(request: Request, user: CurrentUser | None = Depends(optional_user)):
    return templates.TemplateResponse(
        request=request,
        name="index.html",
        context={
            "title": "MotorScope",
            "meta_description": (
                "MotorScope helps you understand changes in walking and mobility using "
                "information your iPhone and Apple Watch already collect. Know your "
                "baseline, notice the change."
            ),
            "user": user,
        },
    )


@router.get("/movement", response_class=HTMLResponse)
async def movement_page(request: Request, user: CurrentUser | None = Depends(optional_user)):
    """Long-form education on movement, for visitors who want the background."""
    return templates.TemplateResponse(
        request=request,
        name="movement.html",
        context={
            "title": "Why Movement Matters",
            "meta_description": (
                "Movement changes gradually and affects everyday life. Learn how walking "
                "measurements such as speed, step length, and steadiness can help you "
                "notice change over time."
            ),
            "user": user,
        },
    )


@router.get("/how-it-works", response_class=HTMLResponse)
async def how_it_works_page(request: Request, user: CurrentUser | None = Depends(optional_user)):
    """Product walkthrough from device sensors through to a personal baseline."""
    return templates.TemplateResponse(
        request=request,
        name="how_it_works.html",
        context={
            "title": "How It Works",
            "meta_description": (
                "See how MotorScope turns walking measurements from your iPhone and Apple "
                "Watch into a trend you can compare against your own mobility baseline."
            ),
            "user": user,
        },
    )


@router.get("/resources", response_class=HTMLResponse)
async def resources_page(request: Request, user: CurrentUser | None = Depends(optional_user)):
    """Curated external reading on mobility, balance, and staying active."""
    return templates.TemplateResponse(
        request=request,
        name="resources.html",
        context={
            "title": "Trusted Resources",
            "meta_description": (
                "Curated reading on mobility, balance, and staying active from the CDC, "
                "the National Institute on Aging, NINDS, MedlinePlus, and more."
            ),
            "user": user,
        },
    )


@router.get("/dashboard", response_class=HTMLResponse)
async def dashboard(request: Request, user: CurrentUser = Depends(require_user)):
    row = db.get_user_by_id(user.id)
    readings = db.get_mobility_readings(user.id, limit=CHART_READING_LIMIT)
    latest = readings[0] if readings else None

    score_series = charts.build_series(readings, "score")
    score_chart = charts.line_chart(score_series, y_min=0, y_max=100)

    return templates.TemplateResponse(
        request=request,
        name="dashboard.html",
        context={
            "title": "Dashboard",
            "user": user,
            "app_page": "overview",
            "created_at": row["created_at"] if row else "",
            "last_login_at": row["last_login_at"] if row else "",
            "verified": bool(user.email_verified),
            "smtp_ready": settings.smtp_configured,
            "score_chart": score_chart,
            "score_series": score_series,
            "score_trend": charts.trend_note(score_series) if score_series else "",
            "latest": latest,
            "latest_metric_cards": _latest_metric_cards(latest, readings[1] if len(readings) > 1 else None),
            "reading_count": len(readings),
        },
    )


def _latest_metric_cards(latest, previous=None) -> list[dict]:
    """Turn the newest reading into labelled cards in a stable metric order.

    When a previous reading exists, each card also carries the change against it. The
    change is reported as a direction and a percentage of the measured value only; it is
    deliberately not labelled good or bad, because that judgement is not MotorScope's to
    make.
    """
    if latest is None:
        return []

    cards = []
    for metric in mobility.METRICS:
        raw = latest[metric.key]
        if raw is None:
            continue

        value = float(raw)
        card = {
            "label": metric.label,
            "value": f"{value:g}",
            "unit": metric.unit,
            "watch_only": metric.watch_only,
            "direction": "",
            "change_note": "",
        }

        prior = previous[metric.key] if previous is not None else None
        if prior is not None:
            prior_value = float(prior)
            if prior_value != 0:
                pct = (value - prior_value) / abs(prior_value) * 100
                if abs(pct) < 0.5:
                    card["direction"] = "flat"
                    card["change_note"] = "Level with previous"
                else:
                    card["direction"] = "up" if pct > 0 else "down"
                    card["change_note"] = f"{abs(pct):.1f}% vs previous"

        cards.append(card)
    return cards


@router.get("/mobility", response_class=HTMLResponse)
async def mobility_page(
    request: Request,
    saved: str = Query(default=""),
    user: CurrentUser = Depends(require_user),
):
    return _render_mobility(
        request,
        user,
        values={},
        errors={},
        saved=saved == "1",
    )


@router.post("/mobility", response_class=HTMLResponse)
async def save_mobility(
    request: Request,
    csrf_token: str = Form(default=""),
    walking_speed: str = Form(default=""),
    step_length: str = Form(default=""),
    walking_asymmetry: str = Form(default=""),
    double_support_time: str = Form(default=""),
    cardio_fitness: str = Form(default=""),
    ground_contact_time: str = Form(default=""),
    running_stride_length: str = Form(default=""),
    six_minute_walk: str = Form(default=""),
    stair_speed_up: str = Form(default=""),
    stair_speed_down: str = Form(default=""),
    vertical_oscillation: str = Form(default=""),
    walking_steadiness: str = Form(default=""),
    recorded_on: str = Form(default=""),
    user: CurrentUser = Depends(require_user),
):
    if not validate_csrf(request, csrf_token):
        return _render_mobility(
            request,
            user,
            values={},
            errors={"form": "Your session expired. Please try again."},
            saved=False,
        )

    submitted = {
        "walking_speed": walking_speed,
        "step_length": step_length,
        "walking_asymmetry": walking_asymmetry,
        "double_support_time": double_support_time,
        "cardio_fitness": cardio_fitness,
        "ground_contact_time": ground_contact_time,
        "running_stride_length": running_stride_length,
        "six_minute_walk": six_minute_walk,
        "stair_speed_up": stair_speed_up,
        "stair_speed_down": stair_speed_down,
        "vertical_oscillation": vertical_oscillation,
        "walking_steadiness": walking_steadiness,
    }

    values, errors = mobility.parse_values(submitted)

    date = recorded_on.strip()
    if not date:
        date = db.local_date()

    if not values and not errors:
        errors["form"] = "Enter at least one measurement to save a reading."

    if errors:
        return _render_mobility(request, user, values=values, errors=errors, saved=False)

    score = mobility.score_values(values)
    db.save_mobility_reading(user.id, date, values, score)
    return RedirectResponse("/mobility?saved=1", status_code=303)


@router.post("/mobility/import", response_class=HTMLResponse)
async def import_mobility(
    request: Request,
    csrf_token: str = Form(default=""),
    export_file: UploadFile | None = File(default=None),
    user: CurrentUser = Depends(require_user),
):
    """Read an Apple Health export and pre-fill the form with what it contains.

    The reading is deliberately not saved here. An export holds years of data, so the
    user is shown exactly which metrics were found, on which days, and can adjust
    anything before it enters their history.
    """
    if not validate_csrf(request, csrf_token):
        return _render_mobility(
            request,
            user,
            values={},
            errors={"form": "Your session expired. Please try again."},
            saved=False,
        )

    if export_file is None or not export_file.filename:
        return _render_mobility(
            request,
            user,
            values={},
            errors={"import": "Choose the ZIP file that Apple Health produced."},
            saved=False,
        )

    # Streamed to disk rather than buffered, because real exports run to gigabytes. The
    # temporary file is deleted whatever happens, so the health data does not outlive
    # the request.
    handle = tempfile.NamedTemporaryFile(suffix=".zip", delete=False)
    temp_path = Path(handle.name)
    try:
        size = 0
        too_large = False
        while chunk := await export_file.read(1024 * 1024):
            size += len(chunk)
            if size > health_export.MAX_UPLOAD_BYTES:
                too_large = True
                break
            handle.write(chunk)
        handle.close()

        if too_large:
            message = "That file is too large to be a Health export."
            parsed = None
        else:
            try:
                parsed = health_export.parse_export(temp_path)
                message = ""
            except health_export.ExportError as error:
                parsed = None
                message = str(error)

        if parsed is not None and parsed.summaries:
            return _render_mobility(
                request,
                user,
                values=parsed.values,
                errors={},
                saved=False,
                import_summaries=parsed.summaries,
                scanned=parsed.records_scanned,
            )

        return _render_mobility(
            request,
            user,
            values={},
            errors={"import": message or "Nothing could be read from that file."},
            saved=False,
        )
    finally:
        temp_path.unlink(missing_ok=True)


def _render_mobility(
    request: Request,
    user: CurrentUser,
    *,
    values: dict[str, float],
    errors: dict[str, str],
    saved: bool,
    import_summaries: tuple = (),
    scanned: int = 0,
) -> HTMLResponse:
    latest = db.get_latest_mobility_reading(user.id)
    history = db.get_mobility_readings(user.id)

    return templates.TemplateResponse(
        request=request,
        name="mobility.html",
        context={
            "title": "Mobility",
            "user": user,
            "app_page": "mobility",
            "metrics": mobility.METRICS,
            "metrics_by_key": mobility.METRICS_BY_KEY,
            "values": values,
            "errors": errors,
            "saved": saved,
            "latest": latest,
            "history": history,
            "today": db.local_date(),
            "import_summaries": import_summaries,
            "import_scanned": scanned,
            "import_recorded_on": max(
                (summary.recorded_on for summary in import_summaries), default=""
            ),
        },
    )


@router.get("/account", response_class=HTMLResponse)
async def account(
    request: Request,
    unlinked: str = Query(default=""),
    needs_password: str = Query(default=""),
    user: CurrentUser = Depends(require_user),
):
    row = db.get_user_by_id(user.id)
    if unlinked == "1":
        notice = ("success", "Google sign-in has been removed from this account.")
    elif needs_password == "1":
        notice = (
            "error",
            "Set a password before unlinking Google, otherwise you will be locked out "
            "of this account.",
        )
    else:
        notice = None

    return templates.TemplateResponse(
        request=request,
        name="account.html",
        context={
            "title": "Account",
            "user": user,
            "app_page": "account",
            "created_at": row["created_at"] if row else "",
            "last_login_at": row["last_login_at"] if row else "",
            "google_linked": bool(row and row["google_subject"]),
            "google_linked_at": (row["google_linked_at"] or "")[:19] if row else "",
            "has_password": bool(row and row["password_hash"]),
            "notice": notice,
        },
    )
