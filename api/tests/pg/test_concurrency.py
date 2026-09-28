"""Concurrency, decided by the database rather than by timing luck.

Two threads are released from a `threading.Barrier` and race for the same row
class. Nothing is asserted about *which* one wins — that is not deterministic, and
pretending otherwise is how a suite becomes flaky. What is asserted is what the
database guarantees: exactly one row exists afterwards, and the loser was told
which invariant refused it, by name.

There are no sleeps: the barrier is the only synchronisation, and every wait after
it is a lock wait inside PostgreSQL. Each worker opens its own session, so the
race is between two real connections, not between two objects in one transaction.

The ordering cannot make these tests pass vacuously. If the writers truly overlap,
the unique index serialises them and one fails; if one finishes first, the other
inserts into a row that already exists and fails the same way. The constraint is
the thing under test, not the schedule.
"""
from __future__ import annotations

from concurrent.futures import ThreadPoolExecutor
from threading import Barrier

import pytest
from sqlalchemy import func, select
from sqlalchemy.exc import IntegrityError

from app.db import SessionLocal
from app.models import (
    Assignment,
    DuplicateReview,
    Score,
    Submission,
    Team,
    TeamMember,
)

pytestmark = pytest.mark.postgres


def race(work) -> list[tuple[str, object]]:
    """Run `work(index)` twice, both released at the same instant."""

    def run(index: int):
        try:
            return ("ok", work(index))
        except Exception as exc:  # noqa: BLE001 - what came back is the assertion
            return ("error", exc)

    barrier = Barrier(2)

    def worker(index: int):
        barrier.wait(timeout=30)
        return run(index)

    with ThreadPoolExecutor(max_workers=2) as pool:
        return list(pool.map(worker, (0, 1)))


def loser(results: list[tuple[str, object]]) -> Exception:
    """The single writer the database refused, with the reason it refused."""
    errors = [value for status, value in results if status == "error"]
    assert len(errors) == 1, f"expected exactly one loser, got {results}"
    return errors[0]


@pytest.fixture()
def team(db, make_user):
    participant = make_user("participant", email="hacker.race@test.dev")
    team = Team(name="Race Team", invite_code="RACETEAM", created_by=participant.id)
    db.add(team)
    db.flush()
    db.add(TeamMember(team_id=team.id, user_id=participant.id))
    db.commit()
    return {"participant": participant, "team": team}


def _submission(db, team_id: int, title: str) -> Submission:
    submission = Submission(
        team_id=team_id, title=title, repo_url=f"https://github.com/race/{title}", status="submitted"
    )
    db.add(submission)
    db.commit()
    return submission


# ── the same row, twice ──────────────────────────────────────────────────────


def test_two_simultaneous_submissions_for_one_team(db, team, constraint_name):
    """Two writers, one team: `uq_submissions_team_canonical` decides.

    The index is partial (`WHERE duplicate_of_submission_id IS NULL`), so this is
    also the assertion that narrowing it did not weaken it: neither racer sets a
    duplicate marker, so exactly one of them wins — decided by the database, not
    by the request order.
    """
    team_id = team["team"].id

    def insert(_index: int):
        with SessionLocal() as session:
            session.add(
                Submission(
                    team_id=team_id,
                    title="Racing Project",
                    repo_url="https://github.com/race/project",
                    status="submitted",
                )
            )
            session.commit()

    refused = loser(race(insert))

    assert isinstance(refused, IntegrityError)
    assert constraint_name(refused) == "uq_submissions_team_canonical"
    assert db.scalar(select(func.count(Submission.id))) == 1


def test_two_simultaneous_verdicts_from_one_judge(db, team, make_user, constraint_name):
    judge = make_user("judge", email="judge.race@test.dev")
    submission = _submission(db, team["team"].id, "racing-verdict")

    def insert(_index: int):
        with SessionLocal() as session:
            session.add(Score(submission_id=submission.id, judge_id=judge.id, technical_score=7))
            session.commit()

    refused = loser(race(insert))

    assert isinstance(refused, IntegrityError)
    assert constraint_name(refused) == "uq_score_pair"
    assert db.scalar(select(func.count(Score.id))) == 1, "one verdict per judge per project"


def test_two_simultaneous_duplicate_decisions(db, team, make_user, constraint_name):
    canonical = _submission(db, team["team"].id, "canonical")
    second = Team(
        name="Race Team Two", invite_code="RACETEAM2", created_by=team["participant"].id
    )
    db.add(second)
    db.flush()
    duplicate = _submission(db, second.id, "duplicate")

    def insert(_index: int):
        with SessionLocal() as session:
            session.add(
                DuplicateReview(
                    submission_id=duplicate.id, duplicate_of_submission_id=canonical.id
                )
            )
            session.commit()

    refused = loser(race(insert))

    assert isinstance(refused, IntegrityError)
    assert constraint_name(refused) == "uq_duplicate_pair"
    assert db.scalar(select(func.count(DuplicateReview.id))) == 1


def test_two_simultaneous_teams_with_the_same_name(db, constraint_name):
    def insert(index: int):
        with SessionLocal() as session:
            session.add(Team(name="Same Name Team", invite_code=f"SAMENAME{index}"))
            session.commit()

    refused = loser(race(insert))

    assert isinstance(refused, IntegrityError)
    assert constraint_name(refused) == "uq_teams_name_app"
    assert db.scalar(select(func.count(Team.id)).where(Team.name == "Same Name Team")) == 1


def test_two_simultaneous_assignment_writes_leave_one_row(db, team, make_user, constraint_name):
    judge = make_user("judge", email="judge.race.assignment@test.dev")
    submission = _submission(db, team["team"].id, "racing-assignment")

    def insert(_index: int):
        with SessionLocal() as session:
            session.add(Assignment(judge_id=judge.id, submission_id=submission.id))
            session.commit()

    refused = loser(race(insert))

    assert isinstance(refused, IntegrityError)
    assert constraint_name(refused) == "uq_assignment_pair"
    assert db.scalar(select(func.count(Assignment.id))) == 1


# ── competing state transitions ──────────────────────────────────────────────


def test_competing_updates_serialise_on_the_row(db, team):
    """Two writers flip the same project. PostgreSQL queues them; both land."""
    submission = _submission(db, team["team"].id, "contested")
    submission_id = submission.id

    def promote(_index: int):
        with SessionLocal() as session:
            row = session.execute(
                select(Submission).where(Submission.id == submission_id).with_for_update()
            ).scalar_one()
            row.status = "submitted"
            session.commit()
            return row.id

    results = race(promote)

    assert [status for status, _ in results] == ["ok", "ok"], "a lock wait is not a failure"
    db.expire_all()
    assert db.get(Submission, submission_id).status == "submitted"
    assert db.scalar(select(func.count(Submission.id))) == 1
