"""Axion API entrypoint."""
from __future__ import annotations

from contextlib import asynccontextmanager

from fastapi import FastAPI, Response
from fastapi.middleware.cors import CORSMiddleware

from . import devtokens
from .config import settings
from .readiness import readiness_report
from .routers import admin, admin_import, auth, devtools, event, judging, submissions, teams


@asynccontextmanager
async def lifespan(_app: FastAPI):
    # A checker attaches headers instead of performing a login round-trip, so the
    # credentials it needs are printed once at boot. Dev deployments only, and
    # silent on an empty database.
    devtokens.announce()
    yield


app = FastAPI(
    title="Axion API",
    description="The fundamental engine for trustless hackathon execution.",
    version="1.0.0",
    # Served under /api/* so the generated docs and the OpenAPI schema are
    # reachable on the same origin as the app, through the Next.js proxy. That
    # means the "API First" claim is one click from the settings menu, with no
    # hard-coded API host in the frontend.
    docs_url="/api/docs",
    redoc_url="/api/redoc",
    openapi_url="/api/openapi.json",
    lifespan=lifespan,
)

# The browser normally reaches the API through the Next.js rewrite proxy, which
# makes every request same-origin. This CORS policy covers direct :8000 access
# during development.
_origins = {settings.web_url, "http://localhost:3000", "http://127.0.0.1:3000"}
app.add_middleware(
    CORSMiddleware,
    allow_origins=sorted(o for o in _origins if o),
    allow_credentials=True,
    allow_methods=["*"],
    allow_headers=["*"],
)

app.include_router(auth.router)
app.include_router(event.router)
app.include_router(teams.router)
app.include_router(submissions.router)
app.include_router(judging.router)
app.include_router(admin.router)
app.include_router(admin_import.router)
app.include_router(devtools.router)


@app.get("/api/health/live", tags=["meta"])
def health_live() -> dict:
    """Liveness: the process is up. Deliberately touches nothing else."""
    return {"status": "alive"}


@app.get("/api/health/ready", tags=["meta"])
def health_ready(response: Response) -> dict:
    """Readiness: database, migrations and dataset, each named when it fails.

    The Compose healthcheck calls this and the web service waits for it, so a
    half-migrated or unseeded instance is never handed traffic.
    """
    report, status_code = readiness_report()
    response.status_code = status_code
    return report


@app.get("/api/health", tags=["meta"])
def health() -> dict:
    return {
        "status": "ok",
        "event": settings.event_name,
        "github_oauth_enabled": settings.github_oauth_enabled,
        "local_dev_login": settings.local_dev_login,
        "commit_integrity_source": "mock" if settings.mock_github else "github",
        # The resolved window is published because a blank EVENT_END now means an
        # open, now-relative event. A judge can see the deadline without reading
        # the process environment.
        "event_window": {
            "opens_at": settings.event_start.isoformat(),
            "closes_at": settings.event_end.isoformat(),
            "closed": settings.event_window_closed,
        },
    }
