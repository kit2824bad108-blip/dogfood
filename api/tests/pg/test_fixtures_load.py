"""empty database → migrations → fixtures → application, on PostgreSQL.

The chain the brief names, executed against the repository's committed
`fixtures.json` rather than a toy dataset: 40 projects, 12 judges, 131 reviews,
and an event whose window has already closed. Two things can only be observed
here — that the schema the import writes into is the one the migrations built,
and that the fixture's *own* deadline (2026-08-04) is the one the server would
enforce. The enforcement itself is in `test_deadline.py`.
"""
from __future__ import annotations

from datetime import datetime, timezone

import pytest
from sqlalchemy import func, select

from app import seed
from app.config import fixture_window
from app.models import ImportBatch, Score, ScoreCriterion, Submission, Team, User

pytestmark = pytest.mark.postgres

EXPECTED_RECORDS = {
    "projects": 40,
    "judges": 12,
    "teams": 40,
    "participants": 48,
    "reviews": 131,
    "assignments": 138,
}


@pytest.fixture()
def imported(db):
    """The repository's own dataset, imported through the shipped seeder."""
    result = seed.seed_fixtures()
    assert result["skipped"] is False, result
    return result


def test_the_repository_fixture_imports_onto_the_migrated_schema(imported, db):
    assert imported["records"] == EXPECTED_RECORDS
    assert imported["applied"]["projects_created"] == EXPECTED_RECORDS["projects"]
    assert imported["applied"]["scores_created"] == EXPECTED_RECORDS["reviews"]
    assert imported["closed_event"] is True, "the fixture event is closed by design"

    assert db.scalar(select(func.count(Submission.id))) == EXPECTED_RECORDS["projects"]
    assert db.scalar(select(func.count(Team.id))) == EXPECTED_RECORDS["teams"]
    assert db.scalar(select(func.count(Score.id))) == EXPECTED_RECORDS["reviews"]
    assert db.scalar(select(func.count(User.id))) >= (
        EXPECTED_RECORDS["participants"] + EXPECTED_RECORDS["judges"]
    )


def test_every_imported_verdict_keeps_the_criteria_that_produced_it(imported, db):
    """A verdict is not just a number: the inputs are stored beside it."""
    score_ids = set(db.scalars(select(Score.id)).all())
    with_criteria = set(db.scalars(select(ScoreCriterion.score_id).distinct()).all())
    assert with_criteria == score_ids
    assert db.scalar(select(func.count(ScoreCriterion.id))) >= EXPECTED_RECORDS["reviews"]


def test_the_import_records_its_provenance(imported, db):
    """What was imported, from where, and in which mode — stored, not recomputed."""
    batch = db.scalars(select(ImportBatch).order_by(ImportBatch.id.desc())).first()
    assert batch is not None, "an applied import must leave a provenance row"
    assert batch.mode == "apply"
    assert batch.source == "api/scripts/build_fixtures.py"
    assert batch.summary["records"] == EXPECTED_RECORDS


def test_a_second_import_refuses_to_run_twice(imported, db):
    """Seeding is guarded: a populated instance is never half-overwritten."""
    second = seed.seed_fixtures()
    assert second == {"skipped": True, "reason": "an admin account already exists"}
    assert db.scalar(select(func.count(Submission.id))) == EXPECTED_RECORDS["projects"]


def test_the_fixture_declares_a_window_that_has_already_closed():
    """The deadline the dataset ships with, read from the file itself."""
    starts_at, closes_at = fixture_window()
    assert starts_at == datetime(2026, 8, 1, tzinfo=timezone.utc)
    assert closes_at == datetime(2026, 8, 4, tzinfo=timezone.utc)
    assert closes_at < datetime.now(timezone.utc)
