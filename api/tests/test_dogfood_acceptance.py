"""The organisers' seven acceptance checks, asserted without Docker.

`run.py` is the program that produces the committed `acceptance-report.txt`, and
these tests exist so a regression fails in the fast suite rather than in the
handful of minutes a year someone remembers to run the checker. Every check below
is written as the brief states it, against the organisers' own fixture dataset,
with the same literal headers the committed manifest carries.

The last test is the real thing: it starts a real uvicorn on a free port, imports
the real dataset into a real SQLite file, and runs the organisers' `run.py` at the
repository root over HTTP. That is the only test here that proves the manifest,
the routes, the credentials and the dataset agree with each other — the rest prove
each of them individually.
"""
from __future__ import annotations

import json
import os
import socket
import subprocess
import sys
import time
import urllib.error
import urllib.request
from pathlib import Path

import pytest

REPO_ROOT = Path(__file__).resolve().parents[2]
API_ROOT = REPO_ROOT / "api"
RUNNER = REPO_ROOT / "run.py"
FIXTURES = REPO_ROOT / "fixtures.json"
MANIFEST = REPO_ROOT / ".dogfood.toml"

# The check labels exactly as `run.py` prints them, so a PASS line in a report can
# be traced to an assertion here by name and not by position.
GALLERY_CHECK = "T1  gallery is public"
FIXTURE_TITLE_CHECK = "T1  project from fixtures shown"
CLOSED_CHECK = "T1  closed event refuses submissions"
OWN_SCORES_CHECK = "T2  judge sees own scores"
PEER_SCORES_CHECK = "T2  judge cannot see peer scores"
PARTICIPANT_CHECK = "T2  participant blocked"
CSV_CHECK = "T2  csv export works"


@pytest.fixture(scope="module")
def fixture_titles() -> list[str]:
    """The first three titles the checker looks for, read from the committed file."""
    payload = json.loads(FIXTURES.read_text(encoding="utf-8"))
    return [project["title"] for project in payload["projects"][:3]]


# ── T1 ───────────────────────────────────────────────────────────────────────


def test_t1_gallery_is_public(client):
    """A stranger can browse the gallery: no auth header, expect 200."""
    response = client.get("/api/gallery")
    assert response.status_code == 200, response.text


def test_t1_gallery_shows_a_first_page_fixture_title(client, organiser_dataset, fixture_titles):
    """Page one must contain one of the first three fixture titles.

    The checker does not paginate and does not search: it reads the body of the one
    request it makes. A gallery that put the first fixture project on page two
    would fail this however good the rest of it is.
    """
    body = client.get("/api/gallery").text.lower()

    assert fixture_titles == ["Glass Signal", "Small Meadow", "Deep Compass"]
    assert any(title.lower() in body for title in fixture_titles), (
        "none of the first three fixture titles appears in the gallery response"
    )


def test_t1_closed_event_refuses_submissions(
    client, organiser_dataset, checker_headers, closed_window
):
    """A closed event refuses the probe, and the refusal is the deadline's.

    The brief accepts any 4xx. This asserts the stronger thing: that the write was
    refused *because the event is closed*, by the API, before the body was even
    validated — not incidentally, because the probe omitted a field.

    A deployed portal takes its window from the fixture dataset itself
    (EVENT_SOURCE=fixtures), so it is closed without being asked to be. This suite
    deliberately runs on its own clock instead, which is why the window is moved
    here; the end-to-end test below exercises the real posture.
    """
    response = client.post(
        "/api/submissions",
        headers=checker_headers["participant"],
        json={"title": "dogfood-late-submission-probe", "summary": "probe"},
    )

    assert 400 <= response.status_code < 500, response.text
    assert response.status_code == 403
    assert "closed" in response.json()["detail"].lower()


def test_t1_an_anonymous_write_is_never_reached_by_the_deadline(
    client, organiser_dataset, closed_window
):
    """Authentication precedes the clock: with no credentials it is a 401, not a 403.

    The order matters for a portal that is closed for months: an anonymous probe
    should learn that it is unauthenticated, not that the event has ended, and the
    refusal must not depend on who is asking last.
    """
    response = client.post("/api/submissions", json={"title": "probe", "summary": "probe"})
    assert response.status_code == 401


# ── T2 ───────────────────────────────────────────────────────────────────────


def test_t2_judge_sees_own_scores(client, organiser_dataset, checker_headers):
    """A judge can read their own scores: 200."""
    response = client.get("/api/judging/scores", headers=checker_headers["judge_a"])
    assert response.status_code == 200, response.text
    assert response.json()["viewer"]["own_record"] is True


def test_t2_judge_cannot_see_peer_scores(client, organiser_dataset, checker_headers, db):
    """The one that matters: judge_b asking for judge_a's record is refused.

    Enforced in the API, not by hiding a link, so the same assertion holds whether
    it is made by a browser or by a test client. It must also not be a blanket
    refusal — judge_a reading the same url answers 200, which is what makes this
    isolation rather than a broken endpoint.
    """
    from app import devtokens

    judge_a = devtokens.select_actors(db)["judge_a"]
    url = f"/api/judging/judges/{judge_a.source_ref}/scores"

    refused = client.get(url, headers=checker_headers["judge_b"])
    assert refused.status_code in (401, 403), refused.text
    assert refused.status_code == 403

    own = client.get(url, headers=checker_headers["judge_a"])
    assert own.status_code == 200, own.text
    assert own.json()["judge"]["source_ref"] == judge_a.source_ref


def test_t2_judge_cannot_see_peer_scores_anonymously(client, organiser_dataset, db):
    from app import devtokens

    judge_a = devtokens.select_actors(db)["judge_a"]
    response = client.get(f"/api/judging/judges/{judge_a.source_ref}/scores")
    assert response.status_code == 401


def test_t2_participant_blocked(client, organiser_dataset, checker_headers):
    """A participant is not a judge: 401 or 403."""
    response = client.get("/api/judging/scores", headers=checker_headers["participant"])
    assert response.status_code in (401, 403), response.text
    assert response.status_code == 403


def test_t2_csv_export_works(client, organiser_dataset, checker_headers):
    """An organizer can export CSV: 200, and a comma in the first line."""
    response = client.get(
        "/api/admin/export/leaderboard.csv", headers=checker_headers["organizer"]
    )

    assert response.status_code == 200, response.text
    first_line = response.text.splitlines()[0] if response.text.splitlines() else ""
    assert "," in first_line, first_line


# ── the same claims against an independent probe ─────────────────────────────


def test_the_peer_scores_route_hides_the_numbers_not_just_the_link(
    client, organiser_dataset, checker_headers, db
):
    """A 403 body must not contain the peer's scores either.

    A refusal that still returns the data in the error payload is not a refusal.
    """
    from app import devtokens
    from app.models import Score

    judge_a = devtokens.select_actors(db)["judge_a"]
    filed = db.query(Score).filter(Score.judge_id == judge_a.id).all()
    assert filed, "judge_a has no scores to leak, so this test would pass vacuously"

    response = client.get(
        f"/api/judging/judges/{judge_a.source_ref}/scores",
        headers=checker_headers["judge_b"],
    )

    assert response.status_code == 403
    for score in filed:
        assert str(score.technical_score) not in response.text or score.technical_score is None


# ── the dataset survives the import ──────────────────────────────────────────


def test_the_deliberate_duplicate_is_imported_rather_than_collapsed(
    db, client, organiser_dataset
):
    """41 projects in, 41 submission rows, one of them marked as a duplicate.

    Two bugs used to make this impossible: a UNIQUE(team_id) the fixture data
    violates, and an importer that resolved a submission by team. Between them
    they turned 41 projects into 40 and dropped the case the brief advertises.
    """
    from app.models import Submission

    rows = db.query(Submission).all()
    marked = [row for row in rows if row.duplicate_of_submission_id is not None]

    assert len(rows) == 41
    assert len(marked) == 1

    duplicate = marked[0]
    original = db.get(Submission, duplicate.duplicate_of_submission_id)
    assert duplicate.source_ref == "prj_41"
    assert original.source_ref == "prj_07"
    assert duplicate.team_id == original.team_id
    assert duplicate.repo_url == original.repo_url

    # The public gallery shows the project once; the pair is for the organiser.
    assert client.get("/api/gallery").json()["count"] == 40


def test_the_repeated_team_names_are_not_merged(db, organiser_dataset):
    """Three teams are called "StillTrail" and they are three teams.

    Keying a team on its name merged them, which silently moved two teams'
    projects onto a third team's submission.
    """
    from app.models import Team

    still_trail = db.query(Team).filter(Team.name == "StillTrail").all()

    assert len(still_trail) == 3
    assert {team.source_ref for team in still_trail} == {"tm_03", "tm_30", "tm_40"}
    assert db.query(Team).count() == 40


def test_the_fixture_member_addresses_become_participant_accounts(db, organiser_dataset):
    """The file gives addresses, not people. The portal needs accounts either way."""
    from app.models import Team, TeamMember, User

    participant = db.query(User).filter(User.email == "priya1@example.org").one()
    assert participant.role == "participant"
    membership = db.query(TeamMember).filter(TeamMember.user_id == participant.id).one()
    assert db.get(Team, membership.team_id).source_ref == "tm_01"

    # And the address is readable as a name rather than rendered as a bare string.
    assert participant.name


def test_the_judges_are_assigned_within_the_tracks_they_declared(db, organiser_dataset):
    """`judges[].tracks` is coverage, and the importer has to honour it.

    The fixture file says which tracks each judge covers and never which projects
    they were given, so an assignment is only defensible if it lands inside that
    declaration. The file's own missing verdicts then show up as thin coverage
    rather than being manufactured: 126 of these 199 assignments carry a score.
    """
    from app.models import Assignment, Submission, User

    declared = json.loads(FIXTURES.read_text(encoding="utf-8"))
    track_of_project = {row["id"]: row["track"] for row in declared["projects"]}
    tracks_of_judge = {
        row["id"]: set(row.get("tracks") or []) for row in declared["judges"]
    }

    ref_of_judge = {judge.id: judge.source_ref for judge in db.query(User).all()}
    ref_of_submission = {
        row.id: row.source_ref for row in db.query(Submission).all()
    }

    assignments = db.query(Assignment).all()
    assert len(assignments) == 199
    for assignment in assignments:
        judge_ref = ref_of_judge[assignment.judge_id]
        project_ref = ref_of_submission[assignment.submission_id]
        assert track_of_project[project_ref] in tracks_of_judge[judge_ref], (
            f"{judge_ref} is assigned {project_ref}, in a track they do not cover"
        )


# ── the real checker, over HTTP, end to end ──────────────────────────────────


def _free_port() -> int:
    with socket.socket() as sock:
        sock.bind(("127.0.0.1", 0))
        return int(sock.getsockname()[1])


def _wait_for_ready(url: str, process: subprocess.Popen, timeout: float = 90.0) -> None:
    deadline = time.time() + timeout
    while time.time() < deadline:
        if process.poll() is not None:
            raise AssertionError(f"the API exited early with {process.returncode}")
        try:
            with urllib.request.urlopen(url, timeout=5) as response:
                if response.status == 200:
                    return
        except (urllib.error.URLError, OSError):
            time.sleep(0.5)
    raise AssertionError(f"the API never became ready at {url}")


def test_the_organisers_runner_passes_all_seven_checks(tmp_path):
    """`python3 run.py .dogfood.toml` against a real, freshly seeded portal.

    A throwaway database and a rewritten copy of the committed manifest, so the
    run cannot touch the repository or depend on anything already running. What it
    proves is the whole contract at once: the manifest's routes, the four printed
    headers, the imported dataset, the enforced deadline and the CSV export.
    """
    port = _free_port()
    database = tmp_path / "acceptance.db"
    env = {
        **os.environ,
        "DATABASE_URL": f"sqlite+pysqlite:///{database.as_posix()}",
        "SECRET_KEY": "acceptance-test-secret",
        "SEED_MODE": "fixtures",
        "SEED_DEMO": "false",
        "EVENT_SOURCE": "fixtures",
        "EVENT_START": "",
        "EVENT_END": "",
        "MOCK_GITHUB": "true",
        "LOCAL_DEV_LOGIN": "false",
        "COOKIE_SECURE": "false",
        # Left on deliberately: the seed banner is part of what a judge sees when
        # the stack comes up, and this asserts it names the same credentials the
        # committed manifest carries.
        "AXION_ANNOUNCE_ACCESS": "true",
        "FIXTURES_PATH": str(FIXTURES),
        "DOGFOOD_FIXTURE_MODE": "",
    }
    env.pop("PYTEST_CURRENT_TEST", None)

    seeded = subprocess.run(
        [sys.executable, "-m", "app.seed"],
        cwd=API_ROOT,
        env=env,
        capture_output=True,
        text=True,
        timeout=300,
    )
    assert seeded.returncode == 0, seeded.stderr
    assert "[axion] seeded fixtures" in seeded.stdout

    # The seed prints the credentials the manifest carries. If these two ever
    # disagree the report would pass on a developer's machine and fail on a
    # judge's, which is the failure this assertion exists to prevent.
    for role, header in (
        ("organizer", "Cookie: session=axion-organizer-1"),
        ("judge_a", "Cookie: session=axion-judge-a-1"),
        ("judge_b", "Cookie: session=axion-judge-b-1"),
        ("participant", "Cookie: session=axion-participant-1"),
    ):
        assert header in seeded.stdout, role

    manifest = MANIFEST.read_text(encoding="utf-8").replace(
        'base_url = "http://localhost:3000"',
        f'base_url = "http://127.0.0.1:{port}"',
    )
    assert f"127.0.0.1:{port}" in manifest
    manifest_path = tmp_path / ".dogfood.toml"
    manifest_path.write_text(manifest, encoding="utf-8")

    server = subprocess.Popen(
        [
            sys.executable,
            "-m",
            "uvicorn",
            "app.main:app",
            "--host",
            "127.0.0.1",
            "--port",
            str(port),
            "--log-level",
            "warning",
        ],
        cwd=API_ROOT,
        env=env,
        stdout=subprocess.PIPE,
        stderr=subprocess.STDOUT,
        text=True,
    )
    try:
        # Liveness, not readiness: the database here is built by SQLAlchemy's
        # `create_all` rather than by the migrations, so `/api/health/ready`
        # correctly reports an unmigrated schema and would never answer 200. The
        # acceptance checks do not read that endpoint; the Compose stack, which
        # does migrate, is where readiness is exercised.
        _wait_for_ready(f"http://127.0.0.1:{port}/api/health", server)

        completed = subprocess.run(
            [
                sys.executable,
                str(RUNNER),
                str(manifest_path),
                "--fixtures",
                str(FIXTURES),
            ],
            cwd=REPO_ROOT,
            capture_output=True,
            text=True,
            timeout=180,
        )
    finally:
        server.terminate()
        try:
            server.wait(timeout=15)
        except subprocess.TimeoutExpired:  # pragma: no cover - a hung server is not the test
            server.kill()

    report = completed.stdout
    assert completed.returncode == 0, completed.stderr
    if "FAIL" in report:
        raise AssertionError(f"the organisers' checker reported a failure:\n{report}")

    for label in (
        GALLERY_CHECK,
        FIXTURE_TITLE_CHECK,
        CLOSED_CHECK,
        OWN_SCORES_CHECK,
        PEER_SCORES_CHECK,
        PARTICIPANT_CHECK,
        CSV_CHECK,
    ):
        assert f"{label} " in report, report

    assert report.count("PASS") == 7, report
    assert "claimed T1 T2, verified T1 T2" in report, report
    assert "claimed but not verified" not in report, report
