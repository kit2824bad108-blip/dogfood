"""The event clock on a real PostgreSQL server, migrated rather than created.

The claim this file exists to check is a database claim: *there is one clock*. The
models and the console both behave as if the table holds at most one row, but a
guarantee that only exists in the code that writes it is a guarantee that lasts
exactly as long as everybody uses that code. `ck_event_settings_singleton` is the
constraint that makes a second row impossible — including for a script, a
migration, or a hand-written `INSERT` in a psql session at 03:00.

The other checks are about the schema a deployment actually runs: the window
columns are `NOT NULL` (a half-filled row would make "who owns this field" a
question every reader has to answer), and the revision is where the optimistic
concurrency check reads from.
"""
from __future__ import annotations

from datetime import datetime, timedelta, timezone

import pytest

from app import eventconfig
from app.models import EventSettings

pytestmark = pytest.mark.postgres

NOW = datetime.now(timezone.utc)
INSERT = (
    "INSERT INTO event_settings "
    "(id, name, starts_at, ends_at, voting_opens_at, voting_closes_at, revision) "
    "VALUES (:id, :name, :starts, :ends, :voting_opens, :voting_closes, 1)"
)
ROW = {
    "name": "DOGFOOD 2026",
    "starts": NOW - timedelta(days=1),
    "ends": NOW + timedelta(days=2),
    "voting_opens": NOW - timedelta(days=1),
    "voting_closes": NOW + timedelta(days=4),
}


def test_the_database_permits_exactly_one_clock(raw, expect_violation):
    raw(INSERT, {**ROW, "id": 1})

    with expect_violation(
        INSERT, {**ROW, "id": 2}, constraint="ck_event_settings_singleton"
    ):
        pass


def test_the_window_columns_cannot_be_absent(pg_migrated, raw):
    """A row with no deadline is not a window, and must not be storable."""
    from sqlalchemy.exc import IntegrityError

    with pytest.raises(IntegrityError):
        raw(
            "INSERT INTO event_settings (id, name, starts_at, ends_at, voting_opens_at) "
            "VALUES (1, 'Nameless', now(), now(), now())"
        )


def test_the_resolver_reads_the_row_the_database_holds(db, raw):
    raw(INSERT, {**ROW, "id": eventconfig.SETTINGS_ID})

    clock = eventconfig.active(db)

    assert clock.source == eventconfig.ORGANISER
    assert clock.name == "DOGFOOD 2026"
    assert clock.revision == 1
    assert clock.ends_at.replace(microsecond=0) == ROW["ends"].replace(microsecond=0)


def test_deleting_the_row_hands_the_clock_back(db, raw):
    default = eventconfig.deployment_default()
    raw(INSERT, {**ROW, "id": eventconfig.SETTINGS_ID})
    assert eventconfig.active(db).ends_at != default.ends_at

    eventconfig.clear(db)
    db.commit()

    assert eventconfig.row_for(db) is None
    assert eventconfig.active(db).source == eventconfig.DEPLOYMENT
    assert eventconfig.active(db).ends_at == default.ends_at


def test_a_write_carries_the_actor_and_a_rising_revision(db, make_user):
    organiser = make_user("admin", email="clock@test.dev")

    _, _, first = eventconfig.apply(
        db, ends_at=NOW + timedelta(hours=5), actor=organiser
    )
    db.commit()
    _, _, second = eventconfig.apply(db, name="Renamed", actor=organiser)
    db.commit()

    assert first.revision == 1
    assert second.revision == 2
    assert second.updated_by == "clock@test.dev"
    assert second.updated_at is not None, "the database's own timestamp, read back"
    stored = db.get(EventSettings, eventconfig.SETTINGS_ID)
    assert stored.updated_by_id == organiser.id
    assert stored.name == "Renamed"
