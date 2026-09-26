"""Runtime configuration, read once from the environment.

Axion is a *single-event* deployment: the event identity and window live here,
not in the database. That keeps the schema small and makes the archive bundle a
faithful snapshot of one deployment.
"""
from __future__ import annotations

import json
import os
from dataclasses import dataclass
from datetime import datetime, timedelta, timezone

REPO_ROOT = os.path.dirname(os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

try:
    from dotenv import load_dotenv

    load_dotenv()
    load_dotenv(os.path.join(REPO_ROOT, ".env"))
except ImportError:
    pass

DEFAULT_WINDOW = timedelta(hours=72)
# How long before boot a blank-window event is treated as having opened. A small
# lead-in means an event created by simply starting the app is genuinely open:
# the window is now-relative rather than an artefact of when the process started.
DEFAULT_OPEN_LEAD = timedelta(hours=1)


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


def _parse_dt(raw: str | None, default: datetime | None = None) -> datetime | None:
    if raw is None:
        return default
    try:
        parsed = datetime.fromisoformat(str(raw).replace("Z", "+00:00"))
    except ValueError:
        return default
    if parsed.tzinfo is None:
        parsed = parsed.replace(tzinfo=timezone.utc)
    return parsed


def _env_dt(name: str, default: datetime | None = None) -> datetime | None:
    return _parse_dt(_env(name), default)


def fixture_window() -> tuple[datetime | None, datetime | None]:
    """The event window declared by the fixture dataset, if there is one.

    An imported dataset brings its own deadline, and a closed fixture event only
    means anything if the server actually enforces *that* deadline. `EVENT_SOURCE`
    selects it; an explicit EVENT_START/EVENT_END still wins over the file.
    """
    try:
        with open(os.path.join(REPO_ROOT, "fixtures.json"), encoding="utf-8") as handle:
            payload = json.load(handle)
    except (OSError, ValueError):
        return None, None
    event = payload.get("event") or {}
    return (
        _parse_dt(event.get("starts_at")),
        _parse_dt(event.get("closes_for_submissions_at") or event.get("ends_at")),
    )


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
    seed_demo: bool
    local_dev_login_override: bool
    api_public_url: str

    @property
    def github_oauth_enabled(self) -> bool:
        return bool(self.github_client_id and self.github_client_secret)

    @property
    def event_window_closed(self) -> bool:
        return datetime.now(timezone.utc) > self.event_end

    @property
    def event_window_opens_in_future(self) -> bool:
        return datetime.now(timezone.utc) < self.event_start

    @property
    def local_dev_login(self) -> bool:
        """Offline sign-in for demos and air-gapped judging.

        Gated on the two flags that already mean "this is not a production
        deployment": MOCK_GITHUB and SEED_DEMO. A judge who turns off their
        Wi-Fi can still get in, and a real deployment cannot accidentally
        expose a passwordless login. See THREAT-MODEL.md.
        """
        return self.local_dev_login_override or self.mock_github or self.seed_demo

    @classmethod
    def from_env(cls) -> "Settings":
        now = datetime.now(timezone.utc)
        explicit_start = _env_dt("EVENT_START")
        explicit_end = _env_dt("EVENT_END")
        if (_env("EVENT_SOURCE", "env") or "env").lower() in {"fixture", "fixtures"}:
            fixture_start, fixture_end = fixture_window()
            explicit_start = explicit_start or fixture_start
            explicit_end = explicit_end or fixture_end
        if explicit_end is not None:
            # An explicit close time is authoritative: an organiser who sets a
            # past window gets a closed event, which is what the fixture dataset
            # and the deadline-enforcement tests rely on.
            event_end = explicit_end
            event_start = explicit_start or (event_end - DEFAULT_WINDOW)
        else:
            # A blank EVENT_END means "an event happening now", not one that
            # ended the instant the process started. Both .env.example and
            # docker-compose.yml leave it blank, so this branch is what the
            # headline `docker compose up` path runs on: submissions must be
            # accepted, not rejected, on a cold start.
            event_start = explicit_start or (now - DEFAULT_OPEN_LEAD)
            event_end = event_start + DEFAULT_WINDOW
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
            seed_demo=_env_bool("SEED_DEMO", False),
            local_dev_login_override=_env_bool("LOCAL_DEV_LOGIN", False),
            api_public_url=_env("API_PUBLIC_URL", "http://localhost:8000") or "",
        )


settings = Settings.from_env()
