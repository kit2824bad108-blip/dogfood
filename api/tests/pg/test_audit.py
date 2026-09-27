"""The audit trail, on PostgreSQL, where the append-only guarantee lives.

SQLite has no trigger, so on the fast suite every append-only claim is really a
claim about the API. Here the guarantee is the database's: the plpgsql trigger
from `0001_initial` refuses `UPDATE` and `DELETE`, and these tests exercise it
through both the ORM the application uses and raw SQL.

One boundary is stated rather than hidden: PostgreSQL fires row-level triggers
for `UPDATE` and `DELETE`, not for `TRUNCATE`. The test harness relies on that to
reset between tests, and `test_truncate_is_the_one_documented_gap` records it, so
nobody reads the guarantee as "nothing can remove a row, ever" — it is "nothing
can rewrite or remove a row through a row operation", which is what a compromised
API key, a careless session or a rogue migration would do.
"""
from __future__ import annotations

import pytest
from sqlalchemy import select
from sqlalchemy.exc import ProgrammingError

from app.models import AuditLog, Score, Submission, Team, TeamMember

pytestmark = pytest.mark.postgres


@pytest.fixture()
def scenario(db, make_user):
    admin = make_user("admin", email="organizer.audit@test.dev")
    judge = make_user("judge", email="judge.audit@test.dev")
    participant = make_user("participant", email="hacker.audit@test.dev")

    team = Team(name="Audit Team", invite_code="AUDIT001", created_by=participant.id)
    db.add(team)
    db.flush()
    db.add(TeamMember(team_id=team.id, user_id=participant.id))

    submission = Submission(
        team_id=team.id,
        title="Audited Project",
        repo_url="https://github.com/audit/project",
        status="submitted",
    )
    db.add(submission)
    db.flush()

    from app.services import assign_judges

    assign_judges(db, submission.id)
    db.commit()

    return {"admin": admin, "judge": judge, "participant": participant, "submission": submission}


def _actions(db) -> list[str]:
    return [entry.action for entry in db.scalars(select(AuditLog).order_by(AuditLog.id)).all()]


def _entries(db, action: str) -> list[AuditLog]:
    return list(db.scalars(select(AuditLog).where(AuditLog.action == action)).all())


# ── writes that must be recorded ──────────────────────────────────────────────


def test_a_filed_verdict_creates_an_audit_entry(client, auth, db, scenario):
    auth(scenario["judge"].email)

    response = client.post(
        "/api/judging/scores",
        json={"submission_id": scenario["submission"].id, "technical_score": 7},
    )
    assert response.status_code == 200, response.text

    entry = _entries(db, "score.technical_submitted")[0]
    assert entry.actor_email == scenario["judge"].email
    assert entry.actor_id == scenario["judge"].id
    assert entry.entity == "score"
    assert entry.details["value"] == 7
    assert entry.details["previous"] is None


def test_changing_a_verdict_creates_a_second_entry(client, auth, db, scenario):
    auth(scenario["judge"].email)
    payload = {"submission_id": scenario["submission"].id, "technical_score": 7}
    assert client.post("/api/judging/scores", json=payload).status_code == 200
    assert client.post(
        "/api/judging/scores", json={**payload, "technical_score": 9}
    ).status_code == 200

    modified = _entries(db, "score.technical_modified")
    assert len(modified) == 1
    assert modified[0].details == {**modified[0].details, "previous": 7, "value": 9}
    # One row, two entries: the verdict is a state, the trail is the history.
    assert _entries(db, "score.technical_submitted")[0].details["value"] == 7
    assert db.scalar(select(Score.technical_score)) == 9


def test_an_export_creates_an_audit_entry(client, auth, db, scenario):
    auth(scenario["judge"].email)
    assert (
        client.post(
            "/api/judging/scores",
            json={"submission_id": scenario["submission"].id, "technical_score": 8},
        ).status_code
        == 200
    )
    auth(scenario["admin"].email)

    response = client.get("/api/admin/export/leaderboard.csv")

    assert response.status_code == 200
    assert response.headers["content-type"].startswith("text/csv")
    entry = _entries(db, "export.csv")[0]
    assert entry.actor_email == scenario["admin"].email
    assert entry.entity == "export" and entry.entity_id == "leaderboard.csv"
    assert entry.details["rows"] >= 1


def test_the_trail_stays_queryable_through_the_api(client, auth, db, scenario):
    auth(scenario["admin"].email)
    client.post(
        "/api/judging/scores",
        json={"submission_id": scenario["submission"].id, "technical_score": 6},
    )

    body = client.get("/api/admin/audit").json()["entries"]

    assert any(entry["action"] == "score.technical_submitted" for entry in body)
    assert body == sorted(body, key=lambda entry: entry["id"], reverse=True)
    assert {"actor", "entity", "ip", "details", "created_at"} <= set(body[0])


# ── the guarantee itself ──────────────────────────────────────────────────────


def test_the_trigger_refuses_an_update_of_an_audit_entry(raw, query, db, scenario):
    entry = AuditLog(action="audit.probe", actor_id=scenario["judge"].id)
    db.add(entry)
    db.commit()
    entry_id = entry.id

    with pytest.raises(ProgrammingError) as info:
        raw("UPDATE audit_logs SET action = 'audit.rewritten' WHERE id = :id", {"id": entry_id})

    assert "append-only" in str(info.value), "refused by the trigger, not by a check"
    assert query("SELECT action FROM audit_logs WHERE id = :id", {"id": entry_id}) == [
        ("audit.probe",)
    ]


def test_the_trigger_refuses_a_delete_of_an_audit_entry(raw, query, db, scenario):
    entry = AuditLog(action="audit.probe.delete", actor_id=scenario["judge"].id)
    db.add(entry)
    db.commit()
    entry_id = entry.id

    with pytest.raises(ProgrammingError) as info:
        raw("DELETE FROM audit_logs WHERE id = :id", {"id": entry_id})

    assert "append-only" in str(info.value)
    assert query("SELECT count(*) FROM audit_logs WHERE id = :id", {"id": entry_id}) == [(1,)]


def test_every_attempted_rewrite_leaves_the_trail_complete(raw, query, db, scenario):
    """A refused write rolls back cleanly: no partial row, no lost history."""
    db.add(AuditLog(action="audit.keep"))
    db.commit()
    before = query("SELECT count(*) FROM audit_logs")[0][0]

    for statement in (
        "UPDATE audit_logs SET actor_email = 'someone@else'",
        "DELETE FROM audit_logs",
        "UPDATE audit_logs SET details = '{\"tampered\": true}'::json",
    ):
        with pytest.raises(ProgrammingError):
            raw(statement)

    assert query("SELECT count(*) FROM audit_logs")[0][0] == before
    assert query("SELECT action FROM audit_logs WHERE action = 'audit.keep'") == [("audit.keep",)]


def test_no_api_route_rewrites_the_trail(client):
    """Nothing in the served contract offers an UPDATE or DELETE of a log entry."""
    schema = client.get("/api/openapi.json").json()
    offenders = [
        f"{method.upper()} {path}"
        for path, operations in schema["paths"].items()
        if "audit" in path
        for method in operations
        if method in {"put", "patch", "delete"}
    ]
    assert offenders == []


def test_truncate_is_the_one_documented_gap(raw, query, scenario):
    """Row triggers do not fire for TRUNCATE — the boundary of the guarantee.

    Stated here so the claim stays exact: the trigger protects against the row
    operations a compromised key, a `psql` session or a rogue migration would use.
    `TRUNCATE` needs table ownership and is not reachable from the API, and the
    test harness uses it to reset between tests.
    """
    raw("INSERT INTO audit_logs (action) VALUES ('audit.truncate.probe')")
    assert query("SELECT count(*) FROM audit_logs WHERE action = 'audit.truncate.probe'") == [(1,)]

    raw("TRUNCATE TABLE audit_logs RESTART IDENTITY")

    assert query("SELECT count(*) FROM audit_logs") == [(0,)]
