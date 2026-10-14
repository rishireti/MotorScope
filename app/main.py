"""FastAPI application factory for MotorScope."""

from __future__ import annotations

import logging
from contextlib import asynccontextmanager

from fastapi import FastAPI, Request
from fastapi.exceptions import RequestValidationError
from fastapi.responses import JSONResponse, RedirectResponse
from fastapi.staticfiles import StaticFiles
from starlette.exceptions import HTTPException as StarletteHTTPException

from . import db, security
from .config import STATIC_DIR, settings
from .deps import CSRF_COOKIE, login_redirect_url
from .routers import auth, pages
from .templates_env import set_csrf, templates

logging.basicConfig(
    level=logging.INFO,
    format="%(asctime)s  %(levelname)-7s %(name)s  %(message)s",
    datefmt="%H:%M:%S",
)


@asynccontextmanager
async def lifespan(app: FastAPI):
    db.init_db()
    removed = db.purge_expired_tokens()
    if removed:
        logging.getLogger("motorscope").info("Pruned %s expired token(s)", removed)
    if not settings.smtp_configured:
        logging.getLogger("motorscope").warning(
            "SMTP is not configured. Emails will not be delivered. "
            "Copy .env.example to .env and fill in your SMTP details."
        )
    yield


app = FastAPI(title="MotorScope", lifespan=lifespan, docs_url=None, redoc_url=None)
app.mount("/static", StaticFiles(directory=str(STATIC_DIR)), name="static")
app.include_router(auth.router)
app.include_router(pages.router)


@app.middleware("http")
async def issue_csrf_cookie(request: Request, call_next):
    """Guarantee every visitor holds a CSRF cookie so forms can be protected.

    Also marks rendered pages as no-store, so a reload during development always
    shows the current markup instead of a cached copy.
    """
    token = request.cookies.get(CSRF_COOKIE) or security.generate_token()
    set_csrf(token)
    response = await call_next(request)
    if "Cache-Control" not in response.headers:
        # Static assets are mounted by StaticFiles and keep its own headers.
        response.headers["Cache-Control"] = (
            "public, max-age=31536000, immutable"
            if request.url.path.startswith("/static/")
            else "no-store"
        )
    if token != request.cookies.get(CSRF_COOKIE) and response.status_code < 500:
        response.set_cookie(
            CSRF_COOKIE,
            token,
            max_age=settings.session_max_age,
            httponly=True,
            samesite="lax",
            secure=settings.base_url.startswith("https://"),
            path="/",
        )
    return response


def _wants_html(request: Request) -> bool:
    """MotorScope is a server-rendered site, so HTML wins unless JSON is asked for."""
    accept = request.headers.get("accept", "")
    if "application/json" in accept and "text/html" not in accept:
        return False
    return True


@app.exception_handler(StarletteHTTPException)
async def http_exception_handler(request: Request, exc: StarletteHTTPException):
    if exc.status_code == 401 and _wants_html(request):
        return RedirectResponse(login_redirect_url(request), status_code=303)
    if exc.status_code == 404:
        return templates.TemplateResponse(
            request=request,
            name="error.html",
            context={
                "title": "Page not found",
                "user": None,
                "message": "That page does not exist.",
            },
            status_code=404,
        )
    if _wants_html(request):
        return templates.TemplateResponse(
            request=request,
            name="error.html",
            context={
                "title": "Something went wrong",
                "user": None,
                "message": str(exc.detail),
            },
            status_code=exc.status_code,
        )
    return JSONResponse({"detail": exc.detail}, status_code=exc.status_code)


@app.exception_handler(RequestValidationError)
async def validation_exception_handler(request: Request, exc: RequestValidationError):
    if _wants_html(request):
        return templates.TemplateResponse(
            request=request,
            name="error.html",
            context={
                "title": "Bad request",
                "user": None,
                "message": "That form could not be submitted. Please reload and try again.",
            },
            status_code=400,
        )
    return JSONResponse({"detail": exc.errors()}, status_code=422)


@app.get("/health", include_in_schema=False)
async def health():
    return {"status": "ok", "smtp_configured": settings.smtp_configured}

@app.get("/favicon.ico", include_in_schema=False)
async def favicon():
    return RedirectResponse("/static/favicon.svg", status_code=307)
