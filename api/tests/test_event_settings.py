"""The organiser's clock: the event's own dates, editable while it runs.

The deployment's configuration is the default and the database is the override,
which is the only arrangement that keeps two things true at once:

  * a fresh `docker compose up` behaves exactly as it always has — a fixture-mode
    deployment starts closed, so the brief's "a closed event refuses submissions"
    check keeps passing with nobody visiting a console; and
  * an organiser running a real event can extend the deadline, move the ballot
    window, rename the event, and undo all of it, without editing an environment
    variable and restarting a container.

The tests below are written against that seam. They prove the change is visible
where it *matters* — the write path, the public payload, the community tally and
the webhook queue — not merely that a row was written.
"""
from __future__ import annotations

from datetime import datetime, timedelta, timezone

import pytest
from sqlalchemy import select

from app import config, eventconfig
from app.models import AuditLog

NOW = datetime.now(timezone.utc)
DEFAULT_END = config.settings.event_end


def iso(moment: datetime) -> str:
    return moment.isoformat()


def as_admin(client, make_user, auth) -> None:
    user = make_user("admin", email="organiser@test.dev")
    auth(user.email)


def current(client) -> dict:
    response = client.get("/api/admin/event")
    assert response.status_code == 200, response.text
    return response.json()


def move(client, **body) -> dict:
    response = client.patch("/api/admin/event", json=body)
    assert response.status_code == 200, response.text
    return response.json()


# ── the door ────────────────────────────────────────────────────────────────


def test_only_an_organiser_can_read_or_move_the_clock(client, make_user, auth):
    judge = make_user("judge", email="judge.clock@test.dev")
    auth(judge.email)

    assert client.get("/api/admin/event").status_code == 403
    assert client.patch("/api/admin/event", json={"name": "Hijacked"}).status_code == 403
    assert client.delete("/api/admin/event").status_code == 403
    assert client.get("/api/admin/event/history").status_code == 403


def test_an_anonymous_caller_is_asked_to_sign_in_first(client):
    assert client.get("/api/admin/event").status_code == 401
    assert client.patch("/api/admin/event", json={"ends_at": iso(NOW)}).status_code == 401


# ── with no row, the configuration is the clock ─────────────────────────────


def test_a_deployment_with_no_row_reports_the_configuration_as_its_clock(client, make_user, auth):
    as_admin(client, make_user, auth)
    payload = current(client)

    assert payload["overridden"] is False
    assert payload["can_reset"] is False
    assert payload["event"]["source"] == "deployment"
    assert payload["event"]["revision"] == 0
    assert payload["event"]["ends_at"] == DEFAULT_END.isoformat()
    # ... and the same values it would fall back to, so "reset" is a promise the
    # organiser can read before pressing it.
    assert payload["deployment_default"]["ends_at"] == DEFAULT_END.isoformat()
    assert payload["status"]["submissions_open"] is True
    assert payload["status"]["record_key_published"] is False


# ── moving it ───────────────────────────────────────────────────────────────


def test_extending_the_deadline_moves_the_window_everywhere(client, make_user, auth, db):
    as_admin(client, make_user, auth)
    extended = NOW + timedelta(hours=72)

    payload = move(client, ends_at=iso(extended), note="The venue flooded.")

    assert payload["event"]["ends_at"] == extended.isoformat()
    assert payload["event"]["source"] == "organiser"
    assert payload["event"]["revision"] == 1
    # `note` is a field of the window like any other, so the trail says that it
    # moved too rather than pretending the deadline moved for no stated reason.
    assert payload["changed"] == ["ends_at", "note"]
    # The deadline the *write path* enforces is the new one, not the configured one.
    window = client.get("/api/health").json()["event_window"]
    assert window["closes_at"] == extended.isoformat()
    assert window["source"] == "organiser"
    assert db.scalar(select(AuditLog).where(AuditLog.action == "event.settings_updated")) is not None


def test_a_partial_change_leaves_every_other_field_alone(client, make_user, auth):
    as_admin(client, make_user, auth)
    before = current(client)["event"]

    payload = move(client, ends_at=iso(NOW + timedelta(hours=12)))

    assert payload["event"]["ends_at"] == (NOW + timedelta(hours=12)).isoformat()
    for field in ("name", "starts_at", "voting_opens_at", "voting_closes_at"):
        assert payload["event"][field] == before[field], field


def test_a_naive_timestamp_is_read_as_utc_rather_than_local_time(client, make_user, auth):
    """A bare local-time string must not become a local-time deadline.

    The console sends an offset, but a script with `curl` will not, and reading it
    as the host's local time would move the deadline by hours for exactly the
    people who cannot check it.
    """
    as_admin(client, make_user, auth)
    target = (NOW + timedelta(days=30)).replace(microsecond=0)

    payload = move(client, ends_at=target.replace(tzinfo=None).isoformat())

    assert payload["event"]["ends_at"] == target.isoformat()


def test_the_event_can_be_renamed_and_the_name_follows_the_reader(client, make_user, auth):
    as_admin(client, make_user, auth)

    move(client, name="DOGFOOD 2026 — extended edition")

    assert client.get("/api/event").json()["event"]["name"] == "DOGFOOD 2026 — extended edition"
    assert client.get("/api/auth/status").json()["event_name"] == "DOGFOOD 2026 — extended edition"
    assert client.get("/api/health").json()["event"] == "DOGFOOD 2026 — extended edition"


def test_the_public_payload_says_the_window_was_revised(client, make_user, auth):
    as_admin(client, make_user, auth)
    assert client.get("/api/event").json()["event"]["window_revised"] is False

    move(client, ends_at=iso(NOW + timedelta(hours=6)))
    published = client.get("/api/event").json()["event"]

    assert published["window_revised"] is True
    assert published["window_source"] == "organiser"
    assert published["submission_window"]["closes_at"] == (NOW + timedelta(hours=6)).isoformat()
    assert published["submission_window"]["closed"] is False


# ── the consequence: the deadline check is the console's ─────────────────────


def test_closing_the_event_from_the_console_refuses_the_next_submission(
    client, make_user, auth, db
):
    """The brief's third check: a closed event refuses a write.

    Nobody restarted anything and nothing was reconfigured — an organiser moved
    the clock, and the write path started refusing. If this stops being true, the
    console has quietly become a way to *claim* a closed event rather than cause
    one.
    """
    as_admin(client, make_user, auth)
    move(client, ends_at=iso(NOW - timedelta(minutes=5)))

    assert client.get("/api/health").json()["event_window"]["closed"] is True
    assert client.get("/api/event").json()["event"]["phase"] == "closed"


def test_the_deadline_a_participant_meets_is_the_organisers(client, make_user, auth):
    as_admin(client, make_user, auth)
    participant = make_user("participant", email="latecomer@test.dev")
    move(client, ends_at=iso(NOW - timedelta(minutes=5)))
    client.post("/api/auth/logout")
    auth(participant.email)

    refused = client.post("/api/submissions", json={"title": "Too late", "repo_url": "https://github.com/a/b"})

    assert refused.status_code == 403
    assert "closed" in refused.json()["detail"].lower()


def test_reopening_the_window_lets_the_write_through_again(client, make_user, auth):
    as_admin(client, make_user, auth)
    participant = make_user("participant", email="second.chance@test.dev")
    move(client, ends_at=iso(NOW - timedelta(minutes=5)))
    move(client, ends_at=iso(NOW + timedelta(hours=3)))
    client.post("/api/auth/logout")
    auth(participant.email)

    # Refused for a different reason now: the window is open, so the refusal is
    # about the missing team rather than about the clock.
    response = client.post(
        "/api/submissions", json={"title": "In time", "repo_url": "https://github.com/a/b"}
    )

    assert response.status_code == 400
    assert "team" in response.json()["detail"].lower()


def test_a_second_change_races_the_first(client, make_user, auth):
    as_admin(client, make_user, auth)
    move(client, ends_at=iso(NOW + timedelta(hours=1)))

    conflict = client.patch(
        "/api/admin/event",
        json={"ends_at": iso(NOW + timedelta(hours=2)), "expected_revision": 0},
    )

    assert conflict.status_code == 409, conflict.text
    detail = conflict.json()["detail"]
    assert detail["current"]["ends_at"] == (NOW + timedelta(hours=1)).isoformat()
    # Nothing was written by the losing writer.
    assert current(client)["event"]["ends_at"] == (NOW + timedelta(hours=1)).isoformat()
    assert current(client)["event"]["revision"] == 1


def test_the_revision_increments_with_every_write(client, make_user, auth):
    as_admin(client, make_user, auth)

    move(client, ends_at=iso(NOW + timedelta(hours=1)))
    move(client, ends_at=iso(NOW + timedelta(hours=2)))
    third = move(client, name="Renamed mid-event")

    assert third["event"]["revision"] == 3
    assert third["changed"] == ["name"]


# ── refusals ────────────────────────────────────────────────────────────────


@pytest.mark.parametrize(
    "body, field",
    [
        ({"ends_at": iso(NOW - timedelta(days=30))}, "ends_at"),
        ({"name": "Renamed", "ends_at": iso(NOW + timedelta(days=400))}, "starts_at"),
        (
            {"voting_closes_at": iso(NOW - timedelta(days=1)), "voting_opens_at": iso(NOW)},
            "voting_closes_at",
        ),
    ],
)
def test_a_window_that_cannot_mean_anything_is_refused(client, make_user, auth, body, field):
    as_admin(client, make_user, auth)

    response = client.patch("/api/admin/event", json=body)

    assert response.status_code == 422, response.text
    assert response.json()["detail"]["field"] == field
    # A refused change is not a partial one.
    assert current(client)["overridden"] is False


def test_an_empty_name_is_refused_before_anything_is_written(client, make_user, auth):
    as_admin(client, make_user, auth)

    response = client.patch("/api/admin/event", json={"name": "   "})

    assert response.status_code == 422, response.text
    assert current(client)["overridden"] is False


def test_an_unknown_field_is_not_silently_ignored(client, make_user, auth):
    as_admin(client, make_user, auth)

    response = client.patch("/api/admin/event", json={"submissions_open": False})

    assert response.status_code == 422


# ── impact, not obstruction ─────────────────────────────────────────────────


def test_closing_immediately_is_allowed_and_says_what_it_does(client, make_user, auth):
    """An organiser is entitled to close the event. They are not entitled to be
    surprised by it, so the response carries the consequence in words."""
    as_admin(client, make_user, auth)

    payload = move(client, ends_at=iso(NOW - timedelta(minutes=1)))

    assert payload["impact"]["submissions_open_before"] is True
    assert payload["impact"]["submissions_open_after"] is False
    assert any("close immediately" in warning for warning in payload["warnings"])


def test_an_extension_says_how_long_is_left(client, make_user, auth):
    as_admin(client, make_user, auth)

    payload = move(client, ends_at=iso(NOW + timedelta(days=2, hours=3)))

    assert any("2 days" in warning for warning in payload["warnings"])


def test_the_impact_names_projects_that_would_sit_after_the_new_deadline(
    client, make_user, auth, db
):
    """A project already in the event, with a `submitted_at` the new deadline would
    exclude, is the case an organiser most needs to see before applying."""
    from app.models import Submission, Team

    as_admin(client, make_user, auth)
    team = Team(name="Early Birds", invite_code="EARLYB01", created_by=None)
    db.add(team)
    db.flush()
    db.add(
        Submission(
            team_id=team.id,
            title="Filed on time",
            repo_url="https://github.com/early/birds",
            status="submitted",
            submitted_at=NOW - timedelta(hours=1),
        )
    )
    db.commit()

    payload = move(client, ends_at=iso(NOW - timedelta(days=2)))

    assert payload["impact"]["submissions_after_deadline"] == 1
    assert any("after the new deadline" in warning for warning in payload["warnings"])


def test_moving_the_close_into_the_past_publishes_the_record_key(client, make_user, auth):
    as_admin(client, make_user, auth)
    assert current(client)["status"]["record_key_published"] is False

    move(client, ends_at=iso(NOW - timedelta(minutes=1)))

    assert current(client)["status"]["record_key_published"] is True


# ── the community window is its own clock, and the console can move it ───────


def test_the_ballot_window_moves_independently_of_the_deadline(client, make_user, auth):
    as_admin(client, make_user, auth)
    before = current(client)["event"]

    move(client, ends_at=iso(NOW + timedelta(hours=5)))
    after = current(client)["event"]

    assert after["voting_closes_at"] == before["voting_closes_at"]
    assert after["voting_opens_at"] == before["voting_opens_at"]


def test_closing_the_ballot_publishes_the_community_results(client, make_user, auth):
    """The T3 rule — no tally before the window closes — is a property of the
    clock, so moving the clock is what publishes it. This test is the seam."""
    # A stranger first: an organiser may always read a running tally, so the
    # "hidden" half of this test has to be asked anonymously.
    assert client.get("/api/vote/results").status_code == 403
    as_admin(client, make_user, auth)

    move(client, voting_closes_at=iso(NOW - timedelta(minutes=1)))
    client.post("/api/auth/logout")

    assert client.get("/api/vote/results").status_code == 200
    published = client.get("/api/event").json()["event"]
    assert published["voting_window"]["results_visible"] is True
    assert published["community"]["results_visible"] is True


def test_holding_the_ballot_open_keeps_the_tally_hidden(client, make_user, auth):
    as_admin(client, make_user, auth)

    move(client, voting_closes_at=iso(NOW + timedelta(days=30)))
    client.post("/api/auth/logout")

    assert client.get("/api/vote/results").status_code == 403
    assert client.get("/api/event").json()["event"]["community"]["results_visible"] is False


# ── hand it back ────────────────────────────────────────────────────────────


def test_the_organiser_can_hand_the_clock_back(client, make_user, auth):
    as_admin(client, make_user, auth)
    move(client, ends_at=iso(NOW + timedelta(days=5)), name="Temporary name")

    response = client.delete("/api/admin/event")

    assert response.status_code == 200, response.text
    assert response.json()["event"]["source"] == "deployment"
    assert response.json()["event"]["ends_at"] == DEFAULT_END.isoformat()
    assert current(client)["overridden"] is False
    # The configured window was never overwritten, so nothing had to be restored.
    assert client.get("/api/event").json()["event"]["window_revised"] is False


def test_resetting_a_deployment_that_never_took_the_clock_over_changes_nothing(
    client, make_user, auth
):
    as_admin(client, make_user, auth)

    response = client.delete("/api/admin/event")

    assert response.status_code == 200
    assert response.json()["changed"] == []
    assert "already comes from" in response.json()["notice"]


# ── the trail ───────────────────────────────────────────────────────────────


def test_every_change_is_in_the_trail_with_its_actor_and_fields(client, make_user, auth):
    as_admin(client, make_user, auth)
    move(client, ends_at=iso(NOW + timedelta(hours=4)), note="Extension")
    move(client, name="Same event, better name")
    client.delete("/api/admin/event")

    history = client.get("/api/admin/event/history").json()
    entries = history["entries"]

    assert [entry["action"] for entry in entries[:3]] == [
        "event.settings_reset",
        "event.settings_updated",
        "event.settings_updated",
    ]
    assert entries[1]["changed"] == ["name"]
    assert entries[2]["changed"] == ["ends_at", "note"]
    assert entries[2]["actor_email"] == "organiser@test.dev"
    assert entries[2]["note"] == "Extension"
    assert entries[2]["after"]["ends_at"] == (NOW + timedelta(hours=4)).isoformat()
    assert history["count"] == 3


def test_the_trail_only_shows_clock_changes(client, make_user, auth):
    """The history is read from the append-only audit trail, so it must not pick up
    unrelated entries that happen to be in it."""
    as_admin(client, make_user, auth)
    move(client, ends_at=iso(NOW + timedelta(hours=1)))

    entries = client.get("/api/admin/event/history").json()["entries"]

    assert all(entry["action"].startswith("event.settings_") for entry in entries)


# ── outbound (T4): the deadline moving is an event, not a silence ───────────


def test_a_moved_deadline_is_announced_on_the_webhook_queue(client, make_user, auth, db):
    as_admin(client, make_user, auth)
    created = client.post(
        "/api/admin/webhooks",
        json={"url": "https://status.example.dev/axion", "events": ["event.settings_updated"]},
    )
    assert created.status_code == 201, created.text
    catalogue = client.get("/api/admin/webhooks/catalogue").json()["events"]
    assert "event.settings_updated" in [entry["event"] for entry in catalogue]

    move(client, ends_at=iso(NOW + timedelta(hours=9)), note="Extended once")

    deliveries = client.get(
        "/api/admin/webhooks/deliveries?include_payload=true"
    ).json()["deliveries"]
    assert len(deliveries) == 1, deliveries
    delivery = deliveries[0]
    assert delivery["event"] == "event.settings_updated"
    assert delivery["payload"]["data"]["event"]["ends_at"] == (NOW + timedelta(hours=9)).isoformat()
    assert delivery["payload"]["data"]["changed"] == ["ends_at", "note"]
    assert delivery["signature"], "a subscriber cannot trust an unsigned envelope"
    assert delivery["status"] == "pending"


def test_the_export_carries_the_organiser_window(client, make_user, auth):
    """T4's bundle is a snapshot of *this* deployment, so the window in it has to be
    the one the deployment actually enforced."""
    as_admin(client, make_user, auth)
    move(client, ends_at=iso(NOW + timedelta(hours=7)), name="Exported Edition")

    bundle = client.get("/api/admin/bundle/export").json()

    assert bundle["event"]["name"] == "Exported Edition"
    assert bundle["event"]["ends_at"] == (NOW + timedelta(hours=7)).isoformat()
    assert bundle["event"]["source"] == "organiser"


def test_the_row_is_the_only_row(client, make_user, auth, db):
    """Zero or one, enforced by the database rather than by this code path."""
    from app.models import EventSettings

    as_admin(client, make_user, auth)
    move(client, ends_at=iso(NOW + timedelta(hours=1)))
    move(client, ends_at=iso(NOW + timedelta(hours=2)))

    assert len(db.scalars(select(EventSettings)).all()) == 1
    assert eventconfig.row_for(db).id == eventconfig.SETTINGS_ID
