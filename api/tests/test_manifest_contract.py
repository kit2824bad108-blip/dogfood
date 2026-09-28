"""The committed `.dogfood.toml` and the running API cannot drift apart.

`.dogfood.toml` at the repository root is the organisers' file: their `run.py`
reads it, and it is the only description they have of where things are in this
portal. Nothing in the API enforces it — a route renamed in the API and not in the
manifest would surface to them as a confusing 404 ("a route the contract
promises") rather than as the drift it is.

This closes the loop from the other side. It needs no server for most of it: it
parses the committed manifest, imports the application, and asserts that every
route the manifest names is one the app serves, that the four printed headers
resolve to the four roles the manifest implies, that the dataset figures are the
ones `fixtures.json` actually holds, and that the peer-scores url names a judge
the peer header is *not*. The dataset-backed checks import the organisers' own
fixture file, so the translation is exercised rather than assumed.
"""
from __future__ import annotations

import json
import re
import tomllib
from datetime import datetime, timezone
from pathlib import Path

import pytest

from app import access, config, devtokens
from app.main import app

REPO_ROOT = Path(__file__).resolve().parents[2]
API_DIR = Path(__file__).resolve().parents[1]
MANIFEST_PATH = REPO_ROOT / ".dogfood.toml"
FIXTURES_PATH = REPO_ROOT / "fixtures.json"
COMPOSE_PATH = REPO_ROOT / "docker-compose.yml"

TIERS = ("T1", "T2", "T3", "T4")
ROUTE_KEYS = ("gallery", "submit", "judge_scores", "peer_scores", "csv_export")
AUTH_KEYS = ("organizer", "judge_a", "judge_b", "participant")
ROLE_FOR_KEY = {
    "organizer": "admin",
    "judge_a": "judge",
    "judge_b": "judge",
    "participant": "participant",
}


@pytest.fixture(scope="module")
def manifest_text() -> str:
    return MANIFEST_PATH.read_text(encoding="utf-8")


@pytest.fixture(scope="module")
def manifest(manifest_text: str) -> dict:
    return tomllib.loads(manifest_text)


@pytest.fixture(scope="module")
def schema() -> dict:
    return app.openapi()


@pytest.fixture(scope="module")
def fixtures_payload() -> dict:
    return json.loads(FIXTURES_PATH.read_text(encoding="utf-8"))


def _openapi_paths(schema: dict) -> set[str]:
    return set(schema.get("paths") or {})


def _matches_template(path: str, templates: set[str]) -> bool:
    """Does `path` match an OpenAPI path template, segment by segment?

    `/api/judging/judges/jdg_01/scores` matches `/api/judging/judges/{judge_ref}/scores`.
    Exact path matching would only prove that the manifest names something the
    schema does not, which is the opposite of useful.
    """
    wanted = [segment for segment in path.split("/") if segment]
    for template in templates:
        parts = [segment for segment in template.split("/") if segment]
        if len(parts) != len(wanted):
            continue
        if all(
            part.startswith("{") and part.endswith("}") or part == segment
            for part, segment in zip(parts, wanted)
        ):
            return True
    return False


# ── the shape the organisers ask for ─────────────────────────────────────────


def test_the_manifest_declares_the_shape_the_organisers_ask_for(manifest):
    assert manifest["portal"]["base_url"].startswith(("http://", "https://"))

    assert isinstance(manifest["tiers"]["claimed"], list)
    assert set(manifest["tiers"]["claimed"]) <= set(TIERS)
    # The brief asks for a one-line pitch next to the claim. An empty string is
    # a claim with nothing behind it.
    assert manifest["tiers"]["pitch"].strip()

    for key in ROUTE_KEYS:
        assert manifest["routes"][key].startswith("/"), key
    for key in AUTH_KEYS:
        assert manifest["auth"][key].strip(), key


def test_the_claimed_tiers_are_the_ones_the_readme_claims(manifest):
    """An overclaim is the one thing the brief says actually costs points.

    Three files have to agree, and they are three different audiences: the
    organisers' manifest (`claimed` is what *their* checker can verify), our own
    manifest (`claimed` is what our deeper checker verifies), and the README table a
    judge reads before running anything.

    The rule this pins is the honest one rather than the flattering one. The
    organisers' checker has seven checks and all seven are T1/T2, so claiming T3 or
    T4 in `.dogfood.toml` would make `run.py` print "claimed but not verified". Those
    tiers are therefore claimed in `selfcheck.toml`, where checks for them exist —
    and the README table must mark exactly the union of the two manifests, so a tier
    is never advertised in prose without a manifest behind it.
    """
    import tomllib

    readme = (REPO_ROOT / "README.md").read_text(encoding="utf-8")
    selfcheck = tomllib.loads((API_DIR / "scripts" / "selfcheck.toml").read_text(encoding="utf-8"))

    organisers = manifest["tiers"]["claimed"]
    ours = (selfcheck.get("claims") or [{}])[-1].get("tiers") or []

    # Their checker verifies T1/T2 and nothing else, so claiming more there would be
    # a claim their own report immediately marks as unverified.
    assert organisers == ["T1", "T2"], organisers
    assert set(organisers) <= set(ours), (
        "a tier claimed to the organisers must also be verified by our own manifest"
    )

    advertised = set(organisers) | set(ours)
    for tier in sorted(advertised):
        assert tier in readme, f"README never mentions the claimed tier {tier}"

        # "Claimed"/"Not claimed" per tier, so a tier that is verified here cannot be
        # described in the README as unbuilt.
        row = next(
            (line for line in readme.splitlines() if line.startswith(f"| **{tier}")),
            None,
        )
        assert row is not None, f"README has no tier row for {tier}"
        assert "Claimed" in row, f"{tier} is verified by a manifest but not claimed in the README: {row}"
        assert "Not claimed" not in row, f"{tier} is both claimed and not claimed: {row}"


def test_every_manifest_route_is_a_route_the_api_serves(manifest, schema):
    templates = _openapi_paths(schema)

    for key in ROUTE_KEYS:
        path = manifest["routes"][key].split("?", 1)[0]
        assert _matches_template(path, templates), (
            f"routes.{key} names {path}, which the API does not serve"
        )


def test_the_judge_route_is_the_one_the_organisers_probe_for_isolation(manifest):
    """`peer_scores` must be a backend route, not a page.

    The brief is explicit that this check has to live in the API, because the API
    is where curl arrives. A manifest that pointed at a page would verify nothing,
    so the drift check would be worse than useless: it would look like coverage.
    """
    peer = manifest["routes"]["peer_scores"]
    assert peer.startswith("/api/"), peer
    assert peer != manifest["routes"]["judge_scores"]


def test_the_base_url_is_the_portal_compose_publishes(manifest):
    """The report cites a portal someone else will try to open.

    `[portal]` is where a visitor lands, so it has to be the web service's
    published port rather than whatever happened to be on this machine when the
    report was generated.
    """
    compose = COMPOSE_PATH.read_text(encoding="utf-8")
    match = re.search(r"\n  web:.*?\n    ports:\n(.*?)\n", compose, re.DOTALL)
    assert match, "docker-compose.yml has no web service with published ports"
    ports = re.findall(r'"(\d+):(\d+)"', match.group(1))
    assert ports, "the web service publishes no port"
    published = {host for host, _container in ports}

    base_port = manifest["portal"]["base_url"].rsplit(":", 1)[-1].strip("/")
    assert base_port in published, (
        f"the manifest points at port {base_port}, which compose does not publish "
        f"(it publishes {sorted(published)})"
    )


# ── the credentials ──────────────────────────────────────────────────────────


def test_every_auth_header_is_one_of_the_gated_literal_credentials(manifest):
    """No ad-hoc secret may be pasted into the manifest.

    Every header must be a credential `app.access` issues and gates, so that
    "these headers stop working in a real deployment" is a claim one place can be
    checked, rather than four strings nobody re-reads.
    """
    for key in AUTH_KEYS:
        assert manifest["auth"][key] == access.header_for(key), key
        token = manifest["auth"][key].split("=", 1)[1]
        assert token in access.LITERAL_TOKENS.values()


def test_the_four_headers_are_the_four_roles_the_checker_is_issued(manifest):
    assert set(access.LITERAL_TOKENS) == set(AUTH_KEYS) == set(devtokens.ROLE_ORDER)
    for key in AUTH_KEYS:
        assert manifest["auth"][key].startswith(f"Cookie: {access.LITERAL_COOKIE}=")


def test_each_header_authenticates_as_the_role_the_manifest_implies(
    client, organiser_dataset, checker_headers
):
    """The headers are checked against the running app, not against a table.

    A header that authenticates as the wrong person would let a checker "verify"
    isolation that does not exist, which is worse than no header at all.
    """
    for key in AUTH_KEYS:
        response = client.get("/api/auth/me", headers=checker_headers[key])
        assert response.status_code == 200, (key, response.text)
        assert response.json()["user"]["role"] == ROLE_FOR_KEY[key], key


def test_the_peer_url_names_the_judge_the_peer_header_is_not(
    db, client, organiser_dataset, checker_headers, manifest
):
    """The url is judge A's record, and judge B is a different person.

    This is the one check the brief singles out as costing the most points, and
    it is satisfied only if the two judge headers are genuinely two judges.
    """
    actors = devtokens.select_actors(db)
    judge_a, judge_b = actors["judge_a"], actors["judge_b"]

    assert judge_a.id != judge_b.id
    # The url names judge A by the dataset's own identifier, which is what makes
    # it readable in a committed manifest.
    assert judge_a.source_ref in manifest["routes"]["peer_scores"]
    assert judge_b.source_ref not in manifest["routes"]["peer_scores"]

    # And both of those headers really are those two judges.
    me_a = client.get("/api/auth/me", headers=checker_headers["judge_a"]).json()["user"]
    me_b = client.get("/api/auth/me", headers=checker_headers["judge_b"]).json()["user"]
    assert me_a["email"] == judge_a.email
    assert me_b["email"] == judge_b.email


# ── the dataset ──────────────────────────────────────────────────────────────


def test_the_declared_dataset_is_the_committed_fixture_file(manifest, fixtures_payload):
    declared = manifest["dataset"]

    assert declared["source"] == FIXTURES_PATH.name
    assert FIXTURES_PATH.exists(), "the manifest names a dataset that is not in the repository"
    assert declared["projects"] == len(fixtures_payload["projects"]) == 41
    assert declared["judges"] == len(fixtures_payload["judges"]) == 30
    assert declared["teams"] == len(fixtures_payload["teams"]) == 40
    assert declared["tracks"] == len(fixtures_payload["tracks"]) == 8
    assert declared["scores"] == len(fixtures_payload["scores"]) == 126


def test_the_declared_event_is_actually_closed(manifest, fixtures_payload):
    """The closed-event check passes only because this date is in the past.

    The checker does not move a clock and does not ask why a write was refused:
    it posts once and expects 4xx. A dataset whose deadline is in the future would
    pass every other check and fail that one, so the date is asserted here.
    """
    close = datetime.fromisoformat(
        fixtures_payload["event"]["submissions_close"].replace("Z", "+00:00")
    )
    assert close.tzinfo is not None
    assert close < datetime.now(timezone.utc), "the fixture event is not closed any more"

    declared = datetime.fromisoformat(
        manifest["dataset"]["submissions_close"].replace("Z", "+00:00")
    )
    assert declared == close
    assert fixtures_payload["event"]["name"] == manifest["dataset"]["event"]


def test_the_fixture_window_is_the_one_the_server_would_enforce(monkeypatch):
    """What the manifest declares and what the app enforces are the same instant."""
    monkeypatch.setenv("EVENT_SOURCE", "fixtures")
    monkeypatch.setenv("EVENT_START", "")
    monkeypatch.setenv("EVENT_END", "")

    _, close = config.fixture_window()

    assert close == datetime(2026, 3, 1, 18, 0, tzinfo=timezone.utc)
    assert config.fixture_event_name() == "Sample Hack 2026"
