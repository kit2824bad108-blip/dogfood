"""Axion API entrypoint."""
from __future__ import annotations

from fastapi import FastAPI
from fastapi.middleware.cors import CORSMiddleware

from .config import settings
from .routers import admin, auth, event, judging, submissions, teams

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


@app.get("/api/health", tags=["meta"])
def health() -> dict:
    return {
        "status": "ok",
        "event": settings.event_name,
        "github_oauth_enabled": settings.github_oauth_enabled,
        "local_dev_login": settings.local_dev_login,
        "commit_integrity_source": "mock" if settings.mock_github else "github",
    }
