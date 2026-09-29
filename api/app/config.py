"""Runtime configuration, read once from the environment.

Axion is a *single-event* deployment, and the values here are that event's
**defaults**: `event_start` / `event_end` / `voting_start` / `voting_end` are what
this deployment enforces until an organiser takes the clock over from the console.

The *effective* clock is `app/eventconfig.py`, which overlays the organiser's row
from `event_settings` on top of these values, and nothing in the application reads
the window from this module directly — the properties at the bottom of this file
describe the configured window, not the enforced one, and exist so that the
environment's own semantics stay testable in isolation.
"""
from __future__ import annotations

import json
import os
from dataclasses import dataclass
from datetime import datetime, timedelta, timezone

from . import fixture_dialects

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
# How long the community keeps voting after the submission deadline, when the
# voting window is derived rather than configured. The community window is its own
# clock precisely so that an event can keep the two separate; the default keeps it
# open for three days past the deadline, which is the shortest span that still
# lets people who are not in the room take part.
VOTING_TAIL = timedelta(hours=72)


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


def fixtures_path() -> str:
    """Where the fixture dataset lives.

    `FIXTURES_PATH` names it explicitly, which is what a container needs: the API
    image does not contain the repository root, so compose mounts the file and
    points this variable at the mount. Unset, the repository root is the right
    answer for a checkout.
    """
    return _env("FIXTURES_PATH") or os.path.join(REPO_ROOT, "fixtures.json")


def _fixture_payload() -> dict:
    """The configured fixture file, or an empty mapping if it cannot be read."""
    try:
        with open(fixtures_path(), encoding="utf-8") as handle:
            payload = json.load(handle)
    except (OSError, ValueError):
        return {}
    return payload if isinstance(payload, dict) else {}


def fixture_window() -> tuple[datetime | None, datetime | None]:
    """The event window declared by the fixture dataset, if there is one.

    An imported dataset brings its own deadline, and a closed fixture event only
    means anything if the server actually enforces *that* deadline. Both fixture
    dialects are read — the organisers' file names only `submissions_close`, from
    which the start is derived backwards.
    """
    return fixture_dialects.window_from_fixture(_fixture_payload())


def fixture_event_name() -> str | None:
    """The event name the dataset declares.

    In fixture mode this replaces EVENT_NAME, because the seeded data and the
    name in the header have to describe the same event. A portal that runs
    "Sample Hack 2026" whose deadline is the fixture's, while announcing itself as
    something else, is a portal nobody can trust the screen of.
    """
    return fixture_dialects.event_name_from_fixture(_fixture_payload())


@dataclass(frozen=True)
class Settings:
    database_url: str
    secret_key: str
    # The key that signs participation records. A deployment can rotate the session
    # secret without invalidating every certificate it has ever issued, which is the
    # point of keeping them separate — and the two are the same value by default so
    # that nothing has to be configured for a demo.
    record_signing_key: str
    web_url: str
    cookie_name: str
    cookie_secure: bool
    session_max_age_seconds: int
    event_name: str
    event_start: datetime
    event_end: datetime
    # The community voting window (T3). Deliberately separate from the submission
    # window: the submission deadline is about when work stops, this is about when
    # the crowd stops counting, and an event is entitled to place them differently.
    # Default is "open with the event, close three days after it".
    voting_start: datetime
    voting_end: datetime
    github_client_id: str | None
    github_client_secret: str | None
    github_token: str | None
    mock_github: bool
    seed_demo: bool
    # True when DOGFOOD_FIXTURE_MODE selects the fixture dataset. Kept as a
    # field, not just an env lookup, so the gate it implies is visible in the
    # settings object a test can inspect.
    dogfood_fixture_mode: bool
    # True when SEED_MODE selects the fixture dataset. A deployment that seeds an
    # imported dataset is an explicitly non-production posture for the same reason
    # SEED_DEMO is: it is running published sample data, not an event. It also
    # decides whether the checker's literal headers are accepted, so relying on
    # MOCK_GITHUB alone to imply it would make the acceptance run depend on an
    # unrelated flag.
    fixture_dataset: bool
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
    def records_verifiable_publicly(self) -> bool:
        """Whether the *verification key* may be published yet (T4).

        Participation records are signed with HMAC, so publishing the key lets
        anyone verify — and also lets anyone forge. That trade is resolved by time
        rather than by pretending it does not exist: the key is published only once
        the event window has closed. Before that, records are verifiable by the
        organiser who holds the key; after it, by anyone, and a forgery created
        afterwards changes nothing that has already been issued and cited.
        """
        return self.event_window_closed

    @property
    def voting_window_open(self) -> bool:
        now = datetime.now(timezone.utc)
        return self.voting_start <= now <= self.voting_end

    @property
    def voting_results_visible(self) -> bool:
        """Community results are published only once the voting window has closed.

        Before it opens there is nothing to publish; while it is open, publishing a
        running tally would let the first votes decide the rest. This is a property
        of the deployment's clock rather than a flag, so there is no way to switch
        it on by accident.
        """
        return datetime.now(timezone.utc) > self.voting_end

    @property
    def local_dev_login(self) -> bool:
        """Offline sign-in for demos and air-gapped judging.

        Gated on the flags that already mean "this is not a production
        deployment": MOCK_GITHUB, SEED_DEMO, and the DOGFOOD_FIXTURE_MODE alias,
        which selects the fixture dataset and its already-closed window — an
        explicit "run the acceptance/demo data" posture, not a production
        switch. A judge who turns off their Wi-Fi can still get in, and a real
        deployment cannot accidentally expose a passwordless login. See
        THREAT-MODEL.md.
        """
        return (
            self.local_dev_login_override
            or self.mock_github
            or self.seed_demo
            or self.dogfood_fixture_mode
            or self.fixture_dataset
        )

    @classmethod
    def from_env(cls) -> "Settings":
        now = datetime.now(timezone.utc)
        explicit_start = _env_dt("EVENT_START")
        explicit_end = _env_dt("EVENT_END")
        # The one-flag alias the acceptance brief names: it selects the fixture
        # dataset and, through the branch below, that dataset's own window. An
        # explicit EVENT_SOURCE still wins, so the alias cannot quietly retarget
        # an existing deployment.
        fixture_mode = _env_bool("DOGFOOD_FIXTURE_MODE", False)
        event_source = (_env("EVENT_SOURCE") or ("fixtures" if fixture_mode else "env")).lower()
        fixture_name = None
        if event_source in {"fixture", "fixtures"}:
            fixture_start, fixture_end = fixture_window()
            fixture_name = fixture_event_name()
            # In fixture mode the dataset's window is authoritative, and an
            # explicit EVENT_START/EVENT_END does *not* override it. Seeding an
            # event that has already closed and then letting a value from the
            # environment re-open it would make the acceptance check pass or fail
            # on local configuration rather than on the code. A deployment that
            # wants a different window sets EVENT_SOURCE=env.
            explicit_start, explicit_end = fixture_start, fixture_end
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
        secret = _env("SECRET_KEY", "dev-only-secret-change-me") or ""
        return cls(
            database_url=_env("DATABASE_URL", "sqlite+pysqlite:///:memory:") or "",
            secret_key=secret,
            record_signing_key=_env("RECORD_SIGNING_KEY") or secret,
            web_url=_env("WEB_URL", "http://localhost:3000") or "",
            cookie_name="axion_session",
            cookie_secure=_env_bool("COOKIE_SECURE", False),
            session_max_age_seconds=int(_env("SESSION_MAX_AGE", str(60 * 60 * 24 * 14)) or 0),
            event_name=fixture_name or _env("EVENT_NAME", "Axion Hackathon") or "",
            event_start=event_start,
            event_end=event_end,
            # The community window is derived from the event unless it is named
            # explicitly, and it is only ever *derived* — a value from the
            # environment cannot re-open a closed event, for the same reason the
            # fixture window cannot be overridden (see the EVENT_SOURCE note above).
            voting_start=_env_dt("VOTING_OPENS_AT", event_start) or event_start,
            voting_end=_env_dt("VOTING_CLOSES_AT", event_end + VOTING_TAIL)
            or (event_end + VOTING_TAIL),
            github_client_id=_env("GITHUB_CLIENT_ID"),
            github_client_secret=_env("GITHUB_CLIENT_SECRET"),
            github_token=_env("GITHUB_TOKEN"),
            mock_github=_env_bool("MOCK_GITHUB", False),
            seed_demo=_env_bool("SEED_DEMO", False),
            dogfood_fixture_mode=fixture_mode,
            fixture_dataset=(_env("SEED_MODE", "") or "").lower() in {"fixture", "fixtures"},
            local_dev_login_override=_env_bool("LOCAL_DEV_LOGIN", False),
            api_public_url=_env("API_PUBLIC_URL", "http://localhost:8000") or "",
        )


settings = Settings.from_env()
