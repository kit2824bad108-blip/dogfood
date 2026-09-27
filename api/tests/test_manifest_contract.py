"""The self-check manifest and the API cannot drift apart, in either direction.

`.dogfood.toml` is a contract: it names the routes, roles and status codes that
`api/scripts/dogfood_check.py` then observes against a running instance. The
checker enforces the contract, but nothing enforced the *manifest* — a route
renamed in the API and not in the manifest would surface as a confusing checker
failure ("404 from a route the contract promises") rather than as drift.

This closes the loop from the other side. It imports the application, reads the
committed manifest, and asserts that every route it names is one the app serves,
that the declared dataset figures are the ones `fixtures.json` actually holds,
that the declared roles are the four deterministic identities the checker is
issued, and that no token was ever pasted into the file. It needs no server, no
database and no network.
"""
from __future__ import annotations

import json
import re
import tomllib
from pathlib import Path

import pytest

from app.devtokens import ROLE_ORDER
from app.main import app

REPO_ROOT = Path(__file__).resolve().parents[2]
MANIFEST_PATH = REPO_ROOT / ".dogfood.toml"
FIXTURES_PATH = REPO_ROOT / "fixtures.json"


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


def manifest_path(check: dict) -> str:
    """The route a check names, without its query string."""
    return check["path"].split("?", 1)[0]


# The framework serves these itself, so they are routes the app answers but not
# paths in the application's own OpenAPI document. They are checked by request.
FRAMEWORK_ROUTES = {"/api/docs", "/api/openapi.json"}


def test_every_manifest_check_names_a_route_the_api_serves(manifest, schema):
    missing: list[str] = []
    for check in manifest["checks"]:
        path = manifest_path(check)
        if path in FRAMEWORK_ROUTES:
            continue
        operations = schema["paths"].get(path)
        if operations is None:
            missing.append(f"{check['id']}: the API serves no {path}")
        elif check["method"].lower() not in operations:
            missing.append(f"{check['id']}: the API serves no {check['method']} {path}")
    assert missing == [], "the manifest promises routes the API does not serve"


def test_the_manifest_itself_is_described_by_a_route(manifest, schema, client):
    """The checker's own health/docs/openapi entry points answer too."""
    assert manifest["service"]["health"] in schema["paths"]
    for key in ("docs", "openapi"):
        response = client.get(manifest["service"][key])
        assert response.status_code == 200, (key, response.status_code)
        if key == "openapi":
            body = response.json()
            assert body["openapi"].startswith("3."), body["openapi"]


def test_the_minimum_path_expectation_still_holds(manifest, schema):
    assert len(schema["paths"]) >= manifest["expectations"]["min_openapi_paths"]


def test_the_declared_dataset_is_the_committed_fixture_file(manifest, fixtures_payload):
    dataset = manifest["dataset"]
    assert dataset["fixtures"] == FIXTURES_PATH.name
    assert FIXTURES_PATH.exists(), "the manifest names a dataset that is not in the repository"
    assert dataset["expected_submissions"] == len(fixtures_payload["projects"])
    assert dataset["expected_submissions"] == len(fixtures_payload["teams"])
    assert dataset["expected_judges"] == len(fixtures_payload["judges"])


def test_the_declared_event_is_actually_closed(manifest, fixtures_payload):
    from datetime import datetime, timezone

    event = fixtures_payload["event"]
    closes_at = event["closes_for_submissions_at"]
    assert manifest["dataset"]["expect_closed_event"] is True
    assert datetime.fromisoformat(closes_at) < datetime.now(timezone.utc), (
        "the manifest declares a closed event; the dataset must not be open"
    )


def test_the_search_expectation_holds_for_the_committed_dataset(manifest, fixtures_payload):
    term = manifest["expectations"]["search_term"].lower()
    minimum = manifest["expectations"]["search_expected_min"]
    matches = [
        project["id"]
        for project in fixtures_payload["projects"]
        if term in json.dumps(project).lower()
    ]
    assert len(matches) >= minimum, (
        f"the manifest expects at least {minimum} projects matching {term!r}, "
        f"the dataset has {len(matches)}"
    )


def test_the_declared_roles_are_the_ones_the_checker_is_issued(manifest, schema):
    auth = manifest["auth"]
    assert set(auth["roles"]) == set(ROLE_ORDER)
    assert auth["mode"] == "headers"
    assert auth["session_header"] == "Authorization"
    assert auth["bearer_prefix"] == "Bearer "
    assert auth["token_source"] in schema["paths"], "the token source must be a real route"


def test_the_manifest_embeds_no_token(manifest_text, manifest):
    """Credentials are fetched at run time; the file only names where from.

    A committed token would be a secret in a public repository and would pin the
    report to one machine's `SECRET_KEY`, which is exactly what
    `auth.token_source` exists to avoid.
    """
    # A token is a long, dotted, base64-ish string. `bearer_prefix = "Bearer "`
    # is the prefix itself, and matching that would make this test useless.
    assert re.search(r"Bearer\s+[A-Za-z0-9._~+/=-]{20,}", manifest_text) is None, (
        "a bearer token is committed"
    )
    assert re.search(r"(?i)\b(secret|password|token)\s*=\s*['\"]?[A-Za-z0-9._-]{8,}", manifest_text) is None
    assert manifest["auth"]["token_source"].startswith("/api/dev/"), (
        "the credentials endpoint is a development endpoint by design"
    )


def test_the_claimed_tiers_are_the_ones_the_readme_claims(manifest):
    """Two artefacts that make the same claim must keep making it together."""
    readme = (REPO_ROOT / "README.md").read_text(encoding="utf-8")
    claimed = sorted(
        f"T{number}"
        for number in re.findall(r"\*\*T(\d)\b[^|]*\*\*\s*\|\s*\*\*Claimed\*\*", readme)
    )
    # `[[claims]]`: the manifest keeps the claim list open, so read the first entry.
    claims = manifest["claims"][0]
    assert claimed, "no tier is marked claimed in README.md"
    assert claims["tiers"] == claimed
    for tier in claims["not_claimed"]:
        assert tier not in claimed, f"{tier} is claimed in the manifest but not in the README"


def test_the_manifest_checks_both_sides_of_the_role_boundary(manifest):
    """A contract that only asserts the happy path proves very little.

    The manifest is expected to hold refusals too: an organizer-only route asked
    for by a participant (403) and by nobody (401). This is what stops the claim
    "authorization is enforced" from resting on frontend behaviour.
    """
    refused = {
        (check["role"], check["path"].split("?")[0])
        for check in manifest["checks"]
        if check.get("expect") in {401, 403} or check.get("expect_class") == "4xx"
    }
    roles = {role for role, _path in refused}
    assert {"participant", "public"} <= roles, refused
    assert any(path.startswith("/api/admin/") for _role, path in refused), refused
    assert any(path.startswith("/api/submissions") for _role, path in refused), refused
