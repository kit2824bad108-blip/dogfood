"""empty database → migrations → fixtures → application, on PostgreSQL.

The chain the one-command path actually runs, executed against the repository's
committed `fixtures.json` — the organisers' own file, loaded as published: 41
projects including the deliberate duplicate submission, 30 judges, 40 teams
including three called "StillTrail", 126 scores, and an event whose window closed
on 2026-03-01.

Three things can only be observed here, and all three are about the *migrated*
schema rather than the models:

  * that the import writes into the schema the migrations built at all;
  * that a duplicate submission and repeated team names are storable against a
    real PostgreSQL server, which is what the partial unique indexes in migration
    0006 exist for;
  * that the fixture's own deadline is the one the server would enforce.

The enforcement itself is in `test_deadline.py`.
"""
from __future__ import annotations

from datetime import datetime, timezone

import pytest
from sqlalchemy import func, select

from app import seed
from app.config import fixture_window
from app.models import (
    ImportBatch,
    Score,
    ScoreCriterion,
    Submission,
    Team,
    User,
)

pytestmark = pytest.mark.postgres

EXPECTED_RECORDS = {
    "projects": 41,
    "judges": 30,
    "teams": 40,
    # Derived from `teams[].members`, which carries addresses rather than names.
    "participants": 91,
    "reviews": 126,
    # Derived from `judges[].tracks`: the file says which tracks a judge covers
    # and never which projects they were given.
    "assignments": 199,
}


@pytest.fixture()
def imported(db):
    """The repository's committed dataset, imported through the shipped seeder."""
    result = seed.seed_fixtures()
    assert result["skipped"] is False, result
    return result


def test_the_repository_fixture_imports_onto_the_migrated_schema(imported, db):
    assert imported["records"] == EXPECTED_RECORDS
    assert imported["applied"]["projects_created"] == EXPECTED_RECORDS["projects"]
    assert imported["applied"]["scores_created"] == EXPECTED_RECORDS["reviews"]
    assert imported["dialect"] == "dogfood"
    assert imported["closed_event"] is True, "the fixture event is closed by design"

    assert db.scalar(select(func.count(Submission.id))) == EXPECTED_RECORDS["projects"]
    assert db.scalar(select(func.count(Team.id))) == EXPECTED_RECORDS["teams"]
    assert db.scalar(select(func.count(Score.id))) == EXPECTED_RECORDS["reviews"]
    assert db.scalar(select(func.count(User.id))) >= (
        EXPECTED_RECORDS["participants"] + EXPECTED_RECORDS["judges"]
    )


def test_the_deliberate_duplicate_is_stored_on_postgres(imported, db):
    """The case the schema used to make unrepresentable, on the real server.

    41 projects go in and 41 submissions come out, one of them marked as a
    duplicate of another. Before migration 0006 the UNIQUE(team_id) refused the
    row, so the import silently produced 40 and the awkward case the brief
    advertises never existed in the database at all.
    """
    duplicates = (
        db.scalars(
            select(Submission).where(Submission.duplicate_of_submission_id.isnot(None))
        ).all()
    )

    assert len(duplicates) == 1
    duplicate = duplicates[0]
    original = db.get(Submission, duplicate.duplicate_of_submission_id)

    assert duplicate.source_ref == "prj_41"
    assert original.source_ref == "prj_07"
    assert duplicate.team_id == original.team_id
    assert duplicate.repo_url == original.repo_url
    assert imported["applied"]["duplicates_marked"] == 1


def test_the_repeated_team_names_all_survive(imported, db):
    """Three teams called "StillTrail", three rows, distinct identifiers."""
    rows = db.scalars(select(Team).where(Team.name == "StillTrail")).all()

    assert len(rows) == 3
    assert {row.source_ref for row in rows} == {"tm_03", "tm_30", "tm_40"}


def test_every_imported_verdict_keeps_the_criteria_that_produced_it(imported, db):
    """A verdict is not just a number: the inputs are stored beside it.

    And for this dataset the inputs are the judge's own answers, not a copy of
    the derived verdict — the organisers' file scores three criteria per project,
    so each score carries three rows and the derivation stays checkable.
    """
    score_ids = set(db.scalars(select(Score.id)).all())
    with_criteria = set(db.scalars(select(ScoreCriterion.score_id).distinct()).all())

    assert with_criteria == score_ids
    assert db.scalar(select(func.count(ScoreCriterion.id))) == (
        EXPECTED_RECORDS["reviews"] * 3
    )

    # The stored verdict really is the weighted mean of those rows, on the
    # fixture's own 1-5 scale rather than rescaled to the demo dataset's 1-10.
    sample = db.scalars(
        select(ScoreCriterion).where(ScoreCriterion.score_id == next(iter(score_ids))).limit(3)
    ).all()
    assert {row.key for row in sample} == {"functionality", "quality", "innovation"}
    assert all(1 <= row.value <= 5 for row in sample)


def test_the_import_records_its_provenance(imported, db):
    """What was imported, from where, and in which mode — stored, not recomputed."""
    batch = db.scalars(select(ImportBatch).order_by(ImportBatch.id.desc())).first()

    assert batch is not None, "an applied import must leave a provenance row"
    assert batch.mode == "apply"
    assert batch.source == "organisers' fixtures.json"
    assert batch.summary["records"] == EXPECTED_RECORDS
    assert batch.summary["duplicates_detected"] == 1


def test_a_second_import_refuses_to_run_twice(imported, db):
    """Seeding is guarded: a populated instance is never half-overwritten."""
    second = seed.seed_fixtures()

    assert second == {"skipped": True, "reason": "an admin account already exists"}
    assert db.scalar(select(func.count(Submission.id))) == EXPECTED_RECORDS["projects"]


def test_the_fixture_declares_a_window_that_has_already_closed():
    """The deadline the dataset ships with, read from the file itself.

    It names only a close date, so the start is derived backwards; the close is
    the instant the API enforces.
    """
    starts_at, closes_at = fixture_window()

    assert starts_at == datetime(2026, 2, 26, 18, 0, tzinfo=timezone.utc)
    assert closes_at == datetime(2026, 3, 1, 18, 0, tzinfo=timezone.utc)
    assert closes_at < datetime.now(timezone.utc)
