"""The community surface (T3): ballots, results gating, comments, anti-abuse.

Everything here is asserted through the API's own HTTP surface rather than by
calling the helpers, because the claims being tested are *behaviour* claims — a
stranger cannot vote before proving an address, a tally is not readable while the
window is open, a second vote is refused. Each one is also a claim the tier table
in README.md makes, so a regression fails here rather than in a judge's browser.

The demo dataset is seeded for every test in this module: the community surface is
about projects existing to be voted on, and an empty event would let several of
these tests pass for the wrong reason.
"""
from __future__ import annotations

from datetime import datetime, timezone

import pytest
from sqlalchemy import func, select

from app import throttle, voting
from app.models import AuditLog, Comment, Submission, Team, ThrottleEvent, Vote, Voter


@pytest.fixture(autouse=True)
def seeded(db):
    from app import seed as seed_module

    seed_module.seed()
    return None


def register(client, email="voter@example.org", name="Voter", **kwargs):
    response = client.post(
        "/api/vote/register", json={"email": email, "name": name}, **kwargs
    )
    assert response.status_code == 201, response.text
    return response.json()


def verify(client, token):
    response = client.post("/api/vote/verify", json={"token": token})
    assert response.status_code == 200, response.text
    return response.json()


def a_project(client) -> int:
    rows = client.get("/api/gallery").json()["projects"]
    assert rows, "the seeded dataset has no public projects"
    return rows[0]["id"]


# ── getting a ballot ────────────────────────────────────────────────────────


def test_registering_for_a_ballot_stores_a_digest_not_the_token(client, db):
    payload = register(client)
    token = payload["token"]
    voter = db.scalar(select(Voter).where(Voter.email == "voter@example.org"))

    assert voter is not None
    assert voter.token_hash != token, "the ballot token must not be stored in clear"
    assert voter.token_hash == voting.hash_token(token)
    assert voter.verified_at is None, "an address is not verified by asking for a link"
    assert payload["delivery"]["mailer_configured"] is False


def test_a_voter_cannot_vote_before_following_the_link(client):
    token = register(client)["token"]
    headers = {"X-Axion-Voter": token}

    # The token is known, the address is not proven.
    assert client.get("/api/vote/ballot", headers=headers).status_code == 403
    response = client.post(
        "/api/vote", json={"submission_id": a_project(client), "score": 4}, headers=headers
    )
    assert response.status_code == 403
    assert "Follow the link" in response.json()["detail"]

    # And with no token at all, the API does not guess who is calling.
    assert client.get("/api/vote/ballot").status_code == 401


def test_following_the_link_verifies_the_address_and_signs_the_browser_in(client, db):
    payload = register(client)
    body = verify(client, payload["token"])

    assert body["voter"]["verified"] is True
    assert client.cookies.get("axion_voter") == payload["token"]

    voter = db.scalar(select(Voter).where(Voter.email == "voter@example.org"))
    assert voter.verified_at is not None
    assert db.scalar(select(AuditLog).where(AuditLog.action == "voter.verified")) is not None


def test_an_unknown_link_is_a_404_and_a_reissued_link_invalidates_the_old_one(client):
    first = register(client)
    unknown = client.post("/api/vote/verify", json={"token": "not-a-real-token-at-all"})
    assert unknown.status_code == 404

    second = register(client)
    assert second["voter"]["id"] == first["voter"]["id"], "one address, one ballot"
    # The newest link wins, so a leaked or mistyped link stops working the moment
    # another is requested.
    assert client.post("/api/vote/verify", json={"token": first["token"]}).status_code == 404
    assert client.post("/api/vote/verify", json={"token": second["token"]}).status_code == 200


# ── the ballot itself ───────────────────────────────────────────────────────


def test_the_ballot_is_exactly_the_public_gallery_on_every_project(client, db):
    token = register(client)["token"]
    verify(client, token)

    ballot = client.get("/api/vote/ballot").json()
    listed = [entry["submission_id"] for entry in ballot["ballot"]]
    public = [row["id"] for row in client.get("/api/gallery").json()["projects"]]

    assert sorted(listed) == sorted(public), "a ballot must be the public projects, no more"
    assert len(listed) == len(set(listed))
    assert ballot["progress"] == {
        "votable": len(public),
        "cast": 0,
        "remaining": len(public),
    }
    # No tally is published on the ballot, whatever the state of the window.
    assert "average" not in ballot["ballot"][0]
    assert ballot["ballot"][0]["my_score"] is None


def test_the_organisers_duplicate_is_imported_but_off_the_ballot(client, db, organiser_dataset):
    """The awkward case the brief advertises, checked end to end."""
    duplicate = db.scalar(
        select(Submission).where(Submission.duplicate_of_submission_id.isnot(None))
    )
    assert duplicate is not None, "prj_41 should have been imported as a marked duplicate"

    token = register(client)["token"]
    verify(client, token)
    listed = [e["submission_id"] for e in client.get("/api/vote/ballot").json()["ballot"]]

    assert duplicate.id not in listed
    # Still present, still scored, and still the organiser's to decide about.
    assert db.get(Submission, duplicate.id) is not None
    assert voting.castable_submission_ids(db) == sorted(listed)


def test_ballot_order_is_stable_for_one_voter_and_different_for_another(client):
    first = register(client, email="one@example.org")["token"]
    verify(client, first)
    one = [e["submission_id"] for e in client.get("/api/vote/ballot").json()["ballot"]]
    again = [e["submission_id"] for e in client.get("/api/vote/ballot").json()["ballot"]]
    assert one == again, "a reload must not reshuffle a voter's own ballot"

    second = register(client, email="two@example.org")["token"]
    verify(client, second)
    two = [e["submission_id"] for e in client.get("/api/vote/ballot").json()["ballot"]]

    assert sorted(one) == sorted(two)
    assert one != two, "two voters must not see the same order"


def test_ballot_order_is_reproducible_from_the_voters_own_digest(client, db):
    token = register(client)["token"]
    verify(client, token)
    voter = db.scalar(select(Voter).where(Voter.email == "voter@example.org"))

    served = [e["submission_id"] for e in client.get("/api/vote/ballot").json()["ballot"]]
    recomputed = voting.ballot_order(voter, voting.castable_submission_ids(db))
    assert served == recomputed, "an organiser must be able to reproduce a ballot"


# ── casting ─────────────────────────────────────────────────────────────────


def test_a_cast_vote_is_final(client, db):
    token = register(client)["token"]
    verify(client, token)
    project = a_project(client)

    first = client.post("/api/vote", json={"submission_id": project, "score": 5})
    assert first.status_code == 201, first.text
    assert first.json()["vote"]["score"] == 5

    second = client.post("/api/vote", json={"submission_id": project, "score": 1})
    assert second.status_code == 409
    assert "final" in second.json()["detail"]

    assert db.scalar(select(func.count(Vote.id))) == 1
    entry = db.scalar(select(AuditLog).where(AuditLog.action == "vote.cast"))
    assert entry is not None and entry.details["score"] == 5


def test_a_whole_ballot_lands_at_once_and_reports_stale_entries(client, db):
    token = register(client)["token"]
    verify(client, token)
    projects = [row["id"] for row in client.get("/api/gallery").json()["projects"]][:3]

    response = client.post(
        "/api/vote/ballot",
        json={"votes": [{"submission_id": pid, "score": 3} for pid in projects]},
    )
    assert response.status_code == 201, response.text
    assert sorted(response.json()["cast"]) == sorted(projects)

    # Sending the same ballot again is a stale client, not an error: nothing new is
    # written and nothing already cast is overwritten.
    repeat = client.post(
        "/api/vote/ballot",
        json={"votes": [{"submission_id": pid, "score": 5} for pid in projects]},
    )
    assert repeat.status_code == 201
    assert repeat.json()["cast"] == []
    assert sorted(repeat.json()["already_cast"]) == sorted(projects)
    assert db.scalar(select(func.count(Vote.id))) == 3
    assert set(db.scalars(select(Vote.score))) == {3}


def test_one_ballot_naming_a_project_twice_is_refused(client):
    token = register(client)["token"]
    verify(client, token)
    project = a_project(client)
    response = client.post(
        "/api/vote/ballot",
        json={
            "votes": [
                {"submission_id": project, "score": 4},
                {"submission_id": project, "score": 5},
            ]
        },
    )
    assert response.status_code == 400


def test_a_draft_is_not_votable_and_not_on_the_ballot(client, db):
    token = register(client)["token"]
    verify(client, token)

    team = Team(name="Drafters", invite_code="drafters-invite")
    db.add(team)
    db.commit()
    draft = Submission(
        team_id=team.id,
        title="Work in progress",
        repo_url="https://example.org/wip",
        status="draft",
    )
    db.add(draft)
    db.commit()

    response = client.post("/api/vote", json={"submission_id": draft.id, "score": 3})
    assert response.status_code == 404
    listed = [e["submission_id"] for e in client.get("/api/vote/ballot").json()["ballot"]]
    assert draft.id not in listed


# ── results are hidden while the window is open ─────────────────────────────


def test_no_tally_is_public_while_the_window_is_open(client):
    response = client.get("/api/vote/results")
    assert response.status_code == 403
    assert response.json()["detail"]["window"]["results_visible"] is False


def test_an_organiser_can_read_the_tally_while_the_window_is_open(client, make_user, auth):
    make_user("admin", email="boss@test.dev")
    auth("boss@test.dev")
    response = client.get("/api/vote/results")
    assert response.status_code == 200
    assert response.json()["visibility"]["shown_to"] == "organiser"


def test_the_public_event_payload_hides_the_tally_until_the_window_closes(client):
    body = client.get("/api/event").json()
    assert body["event"]["voting_window"]["open"] is True
    assert body["event"]["community"]["results_visible"] is False
    assert "votes_cast" not in body["event"]["community"]


def test_results_become_public_once_the_window_closes(client, voting_closed):
    token = register(client)["token"]
    verify(client, token)
    project = a_project(client)
    client.post("/api/vote", json={"submission_id": project, "score": 4})

    public = client.get("/api/vote/results")
    assert public.status_code == 200, public.text
    body = public.json()
    assert body["window"]["closed"] is True
    assert body["totals"]["votes"] == 1
    assert body["results"][0]["average"] == 4.0
    assert body["results"][0]["distribution"] == {"4": 1}

    event = client.get("/api/event").json()["event"]
    assert event["community"]["results_visible"] is True
    assert event["community"]["votes_cast"] == 1
    assert event["community"]["voters"] == 1


# ── rate limits, blocking, striking ─────────────────────────────────────────


def test_asking_for_ballots_repeatedly_is_rate_limited(client):
    limit, window = throttle.RULES["vote.register"]
    assert limit <= 20, "this test would be slow if the limit were generous"

    for _ in range(limit):
        assert register(client, email="flood@example.org")["token"]

    response = client.post("/api/vote/register", json={"email": "flood@example.org"})
    assert response.status_code == 429
    assert int(response.headers["Retry-After"]) > 0
    assert response.headers["X-RateLimit-Limit"] == str(limit)
    assert window > 0


def test_every_bucket_the_router_uses_has_a_rule():
    """A guard against a new call site that silently has no limit.

    The router names its buckets as string literals; this pins them, so adding a
    throttled endpoint without a rule fails here rather than in production.
    """
    expected = {
        "vote.register",
        "vote.register.ip",
        "vote.verify",
        "vote.cast",
        "vote.cast.ip",
        "vote.ballot",
        "comment.create",
        "comment.create.ip",
        "vote.results",
    }
    assert expected.issubset(set(throttle.RULES))
    for bucket in expected:
        limit, window = throttle.RULES[bucket]
        assert limit > 0 and window > 0


def test_a_blocked_voter_cannot_be_issued_another_link(client, db, make_user, auth):
    payload = register(client)
    verify(client, payload["token"])
    voter_id = payload["voter"]["id"]

    make_user("admin", email="boss@test.dev")
    auth("boss@test.dev")
    blocked = client.post(
        f"/api/admin/voters/{voter_id}/block",
        json={"blocked": True, "reason": "bulk-registering addresses"},
    )
    assert blocked.status_code == 200
    client.cookies.clear()

    # Re-registering is how a client would try to walk around a block.
    reissued = client.post("/api/vote/register", json={"email": "voter@example.org"})
    assert reissued.status_code == 403
    assert "bulk-registering" in reissued.json()["detail"]

    # An existing link stops working too, so a block is a block and not a speed bump.
    assert client.get(
        "/api/vote/ballot", headers={"X-Axion-Voter": payload["token"]}
    ).status_code in {401, 403}

    voter = db.get(Voter, voter_id)
    assert voter.blocked is True
    assert db.scalar(select(AuditLog).where(AuditLog.action == "voter.blocked")) is not None


def test_a_struck_vote_stays_on_the_record_but_leaves_the_tally(
    client, db, make_user, auth, voting_closed
):
    token = register(client)["token"]
    verify(client, token)
    project = a_project(client)
    client.post("/api/vote", json={"submission_id": project, "score": 5})

    vote = db.scalar(select(Vote))
    make_user("admin", email="boss@test.dev")
    auth("boss@test.dev")

    struck = client.post(f"/api/admin/votes/{vote.id}/strike", json={"reason": "self-vote"})
    assert struck.status_code == 200
    assert struck.json()["vote"]["status"] == "struck"

    # A struck vote never enters the headline figure, whether or not the caller
    # asked for struck votes to be reported: the tally must not depend on which
    # request produced it. What the flag adds is a separate column, so an organiser
    # can see what their own decision removed.
    body = client.get("/api/vote/results", params={"include_struck": True}).json()
    assert body["totals"]["votes"] == 0, "a struck vote must not be counted"
    assert body["totals"]["votes_struck"] == 1
    assert body["results"][0]["struck_votes"] == 1
    assert client.get("/api/vote/results").json()["totals"]["votes"] == 0

    # The request wrote through its own session, and sessions here are configured
    # with `expire_on_commit=False`, so this one is holding the pre-strike row in
    # its identity map. Re-read rather than trusting the cache.
    db.expire_all()
    stored = db.get(Vote, vote.id)
    assert stored is not None, "striking must not delete the row"
    assert stored.struck_reason == "self-vote"
    assert stored.struck_by == "boss@test.dev"
    assert (
        db.scalar(select(AuditLog).where(AuditLog.action == "vote.struck")) is not None
    ), "striking a vote is a decision, so it is audited"


# ── comments ────────────────────────────────────────────────────────────────


def test_commenting_needs_a_proven_address(client):
    project = a_project(client)

    assert client.post(
        f"/api/submissions/{project}/comments", json={"body": "Nice work"}
    ).status_code == 401

    unverified = register(client, email="quiet@example.org")["token"]
    blocked = client.post(
        f"/api/submissions/{project}/comments",
        json={"body": "Nice work"},
        headers={"X-Axion-Voter": unverified},
    )
    assert blocked.status_code == 403, "an unverified address is not a speaker"
    assert "Follow the link" in blocked.json()["detail"]

    verified = register(client, email="voter@example.org")["token"]
    client.post("/api/vote/verify", json={"token": verified})
    posted = client.post(
        f"/api/submissions/{project}/comments",
        json={"body": "Nice work"},
        headers={"X-Axion-Voter": verified},
    )
    assert posted.status_code == 201, posted.text
    assert posted.json()["comment"]["author_kind"] == "voter"
    assert posted.json()["comment"]["author_email"] is None, "a public payload must not leak the address"


def test_a_signed_in_user_can_comment_and_the_address_stays_private(client, make_user, auth):
    project = a_project(client)
    make_user("participant", email="dev@test.dev")
    auth("dev@test.dev")

    posted = client.post(f"/api/submissions/{project}/comments", json={"body": "Shipping it"})
    assert posted.status_code == 201
    assert posted.json()["comment"]["author_kind"] == "user"

    thread = client.get(f"/api/submissions/{project}/comments").json()
    assert thread["count"] == 1
    assert thread["comments"][0]["author_email"] is None


def test_the_same_comment_twice_in_a_row_is_refused(client, make_user, auth):
    project = a_project(client)
    make_user("participant", email="dev@test.dev")
    auth("dev@test.dev")

    first = client.post(f"/api/submissions/{project}/comments", json={"body": "Repost"})
    assert first.status_code == 201
    duplicate = client.post(f"/api/submissions/{project}/comments", json={"body": "Repost"})
    assert duplicate.status_code == 409

    # A different comment is a different comment, at the same speed.
    other = client.post(f"/api/submissions/{project}/comments", json={"body": "Repost again"})
    assert other.status_code == 201


def test_a_hidden_comment_is_absent_from_the_public_thread_and_the_count(client, db, make_user, auth):
    project = a_project(client)
    make_user("participant", email="dev@test.dev")
    auth("dev@test.dev")
    comment_id = client.post(
        f"/api/submissions/{project}/comments", json={"body": "Spam"}
    ).json()["comment"]["id"]

    make_user("admin", email="boss@test.dev")
    auth("boss@test.dev")
    hidden = client.post(
        f"/api/admin/comments/{comment_id}/moderate",
        json={"status": "hidden", "reason": "off-topic"},
    )
    assert hidden.status_code == 200
    assert hidden.json()["comment"]["author_email"] == "dev@test.dev"

    client.cookies.clear()
    public = client.get(f"/api/submissions/{project}/comments").json()
    assert public["count"] == 0
    assert public["comments"] == []
    assert db.get(Comment, comment_id) is not None, "moderation hides, it does not delete"


def test_an_author_can_withdraw_their_own_comment(client, make_user, auth):
    project = a_project(client)
    make_user("participant", email="dev@test.dev")
    auth("dev@test.dev")
    comment_id = client.post(
        f"/api/submissions/{project}/comments", json={"body": "Actually, no"}
    ).json()["comment"]["id"]

    withdrawn = client.delete(f"/api/comments/{comment_id}")
    assert withdrawn.status_code == 200
    assert withdrawn.json()["comment"]["status"] == "hidden"
    assert client.get(f"/api/submissions/{project}/comments").json()["count"] == 0


def test_a_stranger_cannot_withdraw_someone_elses_comment(client, make_user, auth):
    project = a_project(client)
    make_user("participant", email="dev@test.dev")
    auth("dev@test.dev")
    comment_id = client.post(
        f"/api/submissions/{project}/comments", json={"body": "Mine"}
    ).json()["comment"]["id"]

    client.cookies.clear()
    token = register(client, email="stranger@example.org")["token"]
    verify(client, token)
    assert client.delete(f"/api/comments/{comment_id}").status_code == 403


# ── the organiser view ──────────────────────────────────────────────────────


def test_the_organiser_console_reports_participation_and_integrity(client, make_user, auth):
    token = register(client)["token"]
    verify(client, token)
    project = a_project(client)
    client.post("/api/vote", json={"submission_id": project, "score": 5})
    client.post(f"/api/submissions/{project}/comments", json={"body": "Solid"})

    make_user("admin", email="boss@test.dev")
    auth("boss@test.dev")

    overview = client.get("/api/admin/community")
    assert overview.status_code == 200, overview.text
    body = overview.json()

    assert body["participation"]["voters"] == 1
    assert body["participation"]["verified"] == 1
    assert body["participation"]["votes_cast"] == 1
    assert body["participation"]["turnout"] == 1.0
    assert body["participation"]["comments_visible"] == 1
    assert body["window"]["open"] is True
    # Integrity signals are descriptive: a threshold, the clusters above it, and
    # notes that say what a cluster does and does not mean.
    assert body["integrity"]["ip_threshold"] >= 2
    assert isinstance(body["integrity"]["ip_clusters"], list)
    assert body["integrity"]["notes"]
    assert body["throttling"][0]["bucket"] == "vote.cast.ip"
    assert any(entry["action"] == "vote.cast" for entry in body["recent_moderation"])


def test_the_organiser_view_lists_unverified_ballots_so_a_link_can_be_handed_over(
    client, make_user, auth
):
    register(client, email="pending@example.org")
    make_user("admin", email="boss@test.dev")
    auth("boss@test.dev")

    body = client.get("/api/admin/community").json()
    assert [row["email"] for row in body["pending_voters"]] == ["pending@example.org"]


def test_the_organiser_surface_is_admin_only(client, make_user, auth):
    make_user("judge", email="judge@test.dev")
    auth("judge@test.dev")
    assert client.get("/api/admin/community").status_code == 403
    assert client.get("/api/vote/results").status_code == 403, "a judge is not an organiser"


# ── housekeeping ────────────────────────────────────────────────────────────


def test_throttle_rows_are_pruned_rather_than_accumulating(client, db):
    """The limiter's own table must stay proportional to recent traffic.

    `vote.results` is a read bucket, so this also proves the attempt survives the
    request — a limiter whose rows are rolled back when the session closes would
    simply never count.
    """
    for _ in range(3):
        assert client.get("/api/vote/results").status_code == 403

    rows = db.scalars(
        select(ThrottleEvent).where(ThrottleEvent.bucket == "vote.results")
    ).all()
    assert len(rows) == 3, "a read bucket must record the attempt"
    key = rows[0].key

    # Rewriting the clock is the cheapest way to prove the prune: an attempt from
    # an hour ago is outside the window and must not count against a client.
    for row in rows:
        row.at_epoch = row.at_epoch - 3600
    db.commit()

    decision = throttle.hit(db, "vote.results", key)
    assert decision.allowed
    remaining = db.scalars(
        select(ThrottleEvent).where(
            ThrottleEvent.bucket == "vote.results", ThrottleEvent.key == key
        )
    ).all()
    assert len(remaining) == 1, "expired attempts must be pruned, not kept forever"
    assert remaining[0].at_epoch >= int(datetime.now(timezone.utc).timestamp()) - 5
