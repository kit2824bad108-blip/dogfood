"""The event deadline, tested against PostgreSQL at its exact boundary.

The documented semantics are the ones the code implements: a write is refused
when `now > settings.event_end`, so the instant `event_end` itself is still
inside the window and one microsecond later is outside it. The clock is the
server's — `datetime.now(timezone.utc)` in `app.services` — so the clock is what
these tests freeze. Nothing here consults a client-supplied timestamp, and
`test_a_client_timestamp_cannot_reopen_the_window` proves the server ignores one
even when it is offered.

The window used is the one the committed fixture dataset declares
(2026-02-26 → 2026-03-01T18:00Z, closed), which is the window a fixture-mode
deployment enforces. `RUNNING_TESTS` therefore tests the deadline that ships.
"""
from __future__ import annotations

from datetime import datetime, timedelta, timezone

import pytest
from sqlalchemy import select

from app import config
from app.models import AuditLog, Score, Submission, Team, TeamMember
from app.services import assign_judges

pytestmark = pytest.mark.postgres

FIXTURE_CLOSE = datetime(2026, 3, 1, 18, 0, tzinfo=timezone.utc)
CLOSED_DETAIL = "The submission window is closed"

WINDOW_READERS = (
    "app.routers.submissions",
    "app.routers.event",
    "app.routers.auth",
    "app.services",
)


class _FrozenClock(datetime):
    """A `datetime` whose `now()` is a fixed instant, for exact boundaries."""

    fixed: datetime

    @classmethod
    def now(cls, tz=None):  # noqa: D102 - mirrors datetime.now
        if tz is None:
            return cls.fixed.replace(tzinfo=None)
        return cls.fixed.astimezone(tz)


@pytest.fixture()
def clock(monkeypatch):
    """Freeze the server clock, and read the window from a chosen `event_end`."""

    def _freeze(now: datetime, closes_at: datetime | None = None):
        frozen = type("Frozen", (_FrozenClock,), {"fixed": now})
        for module in ("app.services",):
            monkeypatch.setattr(f"{module}.datetime", frozen, raising=False)
        window = config.settings
        from dataclasses import replace

        window = replace(
            window,
            event_start=FIXTURE_CLOSE - timedelta(days=3),
            event_end=closes_at or FIXTURE_CLOSE,
        )
        for module in WINDOW_READERS:
            monkeypatch.setattr(f"{module}.settings", window, raising=False)
        monkeypatch.setattr(config, "settings", window)
        return window

    return _freeze


@pytest.fixture()
def project(db, make_user):
    """A participant with a team of their own, ready to submit something."""
    participant = make_user("participant", email="hacker.deadline@test.dev")
    team = Team(name="Deadline Team", invite_code="DEADLIN1", created_by=participant.id)
    db.add(team)
    db.flush()
    db.add(TeamMember(team_id=team.id, user_id=participant.id))
    db.commit()
    return {"participant": participant, "team": team}


def _payload(**overrides) -> dict:
    body = {
        "title": "Deadline Candidate",
        "repo_url": "https://github.com/deadline/candidate",
        "status": "submitted",
    }
    body.update(overrides)
    return body


def _post(client, body: dict):
    return client.post("/api/submissions", json=body)


# ── before the deadline ──────────────────────────────────────────────────────


def test_before_the_deadline_a_submission_is_created(client, auth, clock, project):
    clock(FIXTURE_CLOSE - timedelta(hours=1))
    auth(project["participant"].email)

    response = _post(client, _payload())

    assert response.status_code == 200, response.text
    assert response.json()["submission"]["status"] == "submitted"


def test_before_the_deadline_a_draft_can_be_edited_and_then_submitted(
    client, auth, clock, project
):
    clock(FIXTURE_CLOSE - timedelta(hours=2))
    auth(project["participant"].email)

    draft = _post(client, _payload(status="draft"))
    assert draft.status_code == 200, draft.text
    assert draft.json()["submission"]["status"] == "draft"

    edited = _post(client, _payload(title="Deadline Candidate v2", status="draft"))
    assert edited.status_code == 200
    assert edited.json()["submission"]["title"] == "Deadline Candidate v2"

    final = _post(client, _payload(title="Deadline Candidate v2"))
    assert final.status_code == 200, final.text
    assert final.json()["submission"]["submitted_at"] is not None


# ── at the deadline ──────────────────────────────────────────────────────────


def test_exactly_at_the_deadline_the_window_is_still_open(client, auth, clock, db, project):
    """`now > event_end` is the rule, so `now == event_end` is still allowed."""
    clock(FIXTURE_CLOSE)
    auth(project["participant"].email)

    response = _post(client, _payload())

    assert response.status_code == 200, response.text
    assert db.scalar(select(Submission.id)) is not None


def test_one_microsecond_past_the_deadline_is_closed(client, auth, clock, db, project):
    clock(FIXTURE_CLOSE + timedelta(microseconds=1))
    auth(project["participant"].email)

    response = _post(client, _payload())

    assert response.status_code == 403
    assert CLOSED_DETAIL in response.json()["detail"]
    assert db.scalar(select(Submission.id)) is None, "nothing is written"


# ── after the deadline ───────────────────────────────────────────────────────


def test_after_the_deadline_a_submission_is_refused_and_audited(
    client, auth, clock, db, project
):
    clock(FIXTURE_CLOSE + timedelta(hours=1))
    auth(project["participant"].email)

    response = _post(client, _payload())

    assert response.status_code == 403
    entry = db.scalars(
        select(AuditLog).where(AuditLog.action == "submission.rejected_after_deadline")
    ).first()
    assert entry is not None, "a refused write is worth a record"
    assert entry.actor_email == project["participant"].email
    assert entry.details["attempted_status"] == "submitted"
    assert entry.details["deadline"].startswith("2026-03-01")
    # The refusal happens before the body is validated, so the trail says so
    # rather than implying a request that got as far as the schema.
    assert entry.details["refused_before_body_validation"] is True


def test_a_client_timestamp_cannot_reopen_the_window(client, auth, clock, db, project):
    """The deadline is the server's: a header claiming otherwise changes nothing."""
    clock(FIXTURE_CLOSE + timedelta(hours=1))
    auth(project["participant"].email)

    response = client.post(
        "/api/submissions",
        json=_payload(),
        headers={
            "X-Client-Time": FIXTURE_CLOSE.isoformat(),
            "If-Unmodified-Since": FIXTURE_CLOSE.isoformat(),
            "Date": FIXTURE_CLOSE.isoformat(),
        },
    )

    assert response.status_code == 403
    assert db.scalar(select(Submission.id)) is None


def test_an_edit_after_the_deadline_leaves_the_submission_untouched(
    client, auth, clock, db, project
):
    auth(project["participant"].email)
    clock(FIXTURE_CLOSE - timedelta(hours=1))
    original = _post(client, _payload(title="Locked Title"))
    assert original.status_code == 200, original.text
    submission_id = original.json()["submission"]["id"]

    clock(FIXTURE_CLOSE + timedelta(hours=1))
    refused = _post(client, _payload(title="Rewritten After The Bell"))
    assert refused.status_code == 403

    db.expire_all()
    stored = db.get(Submission, submission_id)
    assert stored.title == "Locked Title", "the refused edit is not a partial write"


def test_judging_continues_after_the_submission_deadline(client, auth, clock, db, project, make_user):
    """The deadline closes submissions, not judging: verdicts may still be filed.

    The project has to be in before the bell; the verdict that judges it may
    arrive after it, which is how a hackathon actually runs (submissions close
    Friday, judging happens over the weekend).
    """
    clock(FIXTURE_CLOSE - timedelta(hours=1))
    auth(project["participant"].email)
    created = _post(client, _payload())
    assert created.status_code == 200, created.text
    submission_id = created.json()["submission"]["id"]

    judge = make_user("judge", email="judge.deadline@test.dev")
    assign_judges(db, submission_id)
    db.commit()

    clock(FIXTURE_CLOSE + timedelta(hours=2))
    auth(judge.email)
    verdict = client.post(
        "/api/judging/scores", json={"submission_id": submission_id, "technical_score": 8}
    )

    assert verdict.status_code == 200, verdict.text
    score = db.scalars(select(Score).where(Score.submission_id == submission_id)).first()
    assert score is not None and score.technical_score == 8
    # The judging handler timestamps with the real clock, so this asserts the
    # verdict landed after the frozen deadline rather than at a frozen instant.
    assert score.technical_submitted_at > FIXTURE_CLOSE
