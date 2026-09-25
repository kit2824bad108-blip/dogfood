"""Runtime configuration, read once from the environment.

Axion is a *single-event* deployment: the event identity and window live here,
not in the database. That keeps the schema small and makes the archive bundle a
faithful snapshot of one deployment.
"""
from __future__ import annotations

import os
from dataclasses import dataclass
from datetime import datetime, timedelta, timezone

DEFAULT_WINDOW = timedelta(hours=72)


def _env(name: str, default: str | None = None) -> str | None:
    value = os.environ.get(name)
    if value is None or value.strip() == "":
        return default
    return value.strip()


def _env_bool(name: str, default: bool = False) -> bool:
    raw = _env(name)
    if raw is None:
        return default
    return raw.lower() in {"1", "true", "yes", "on"}


def _env_dt(name: str, default: datetime | None = None) -> datetime | None:
    raw = _env(name)
    if raw is None:
        return default
    try:
        parsed = datetime.fromisoformat(raw.replace("Z", "+00:00"))
    except ValueError:
        return default
    if parsed.tzinfo is None:
        parsed = parsed.replace(tzinfo=timezone.utc)
    return parsed


@dataclass(frozen=True)
class Settings:
    database_url: str
    secret_key: str
    web_url: str
    cookie_name: str
    cookie_secure: bool
    session_max_age_seconds: int
    event_name: str
    event_start: datetime
    event_end: datetime
    github_client_id: str | None
    github_client_secret: str | None
    github_token: str | None
    mock_github: bool
    api_public_url: str

    @property
    def github_oauth_enabled(self) -> bool:
        return bool(self.github_client_id and self.github_client_secret)

    @classmethod
    def from_env(cls) -> "Settings":
        now = datetime.now(timezone.utc)
        event_end = _env_dt("EVENT_END", now) or now
        event_start = _env_dt("EVENT_START", event_end - DEFAULT_WINDOW) or (
            event_end - DEFAULT_WINDOW
        )
        return cls(
            database_url=_env("DATABASE_URL", "sqlite+pysqlite:///:memory:") or "",
            secret_key=_env("SECRET_KEY", "dev-only-secret-change-me") or "",
            web_url=_env("WEB_URL", "http://localhost:3000") or "",
            cookie_name="axion_session",
            cookie_secure=_env_bool("COOKIE_SECURE", False),
            session_max_age_seconds=int(_env("SESSION_MAX_AGE", str(60 * 60 * 24 * 14)) or 0),
            event_name=_env("EVENT_NAME", "Axion Hackathon") or "",
            event_start=event_start,
            event_end=event_end,
            github_client_id=_env("GITHUB_CLIENT_ID"),
            github_client_secret=_env("GITHUB_CLIENT_SECRET"),
            github_token=_env("GITHUB_TOKEN"),
            mock_github=_env_bool("MOCK_GITHUB", False),
            api_public_url=_env("API_PUBLIC_URL", "http://localhost:8000") or "",
        )


settings = Settings.from_env()
