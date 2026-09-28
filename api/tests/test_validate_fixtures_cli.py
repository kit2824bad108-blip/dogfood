"""The fixture validator CLI: exit codes, determinism, no clock in the report.

The validator wraps the same rules the importer enforces, so what matters at
this layer is the contract around it: a valid fixture exits 0 with a report that
is byte-identical between runs, an invalid one exits 1 and names every problem,
and an unreadable one exits 2.

It is run against both datasets on purpose. The organisers' `fixtures.json` at
the repository root is the one `docker compose up` seeds, and Axion's own
`data/axion-fixtures.json` is the one the published numbers came from; a
translation that quietly mangled either is a bug in the import path rather than
in a dataset.
"""
from __future__ import annotations

import json
import subprocess
import sys
from pathlib import Path

REPO_ROOT = Path(__file__).resolve().parents[2]
SCRIPT = REPO_ROOT / "api" / "scripts" / "validate_fixtures.py"
ORGANISER_FIXTURE = "fixtures.json"
AXION_FIXTURE = "data/axion-fixtures.json"


def _run(*args: str) -> subprocess.CompletedProcess:
    return subprocess.run(
        [sys.executable, str(SCRIPT), *args],
        cwd=REPO_ROOT,
        capture_output=True,
        text=True,
        timeout=120,
    )


def test_the_committed_fixture_is_valid_and_the_report_is_deterministic():
    first = _run()
    second = _run()
    assert first.returncode == 0, first.stderr
    assert "RESULT: VALID" in first.stdout
    # The organisers' published dataset, as published: 41 projects including the
    # duplicate submission, 30 judges, 40 teams, 8 tracks.
    assert "41 projects" in first.stdout
    assert "30 judges" in first.stdout
    assert "1 duplicate candidate detected" in first.stdout
    assert first.stdout == second.stdout


def test_axions_own_dataset_still_validates_too():
    """The other dialect is not collateral damage of supporting this one."""
    completed = _run(AXION_FIXTURE)
    assert completed.returncode == 0, completed.stderr
    assert "40 projects" in completed.stdout
    assert "12 judges" in completed.stdout
    assert "2 duplicate candidates detected" in completed.stdout


def test_an_invalid_fixture_is_named_record_by_record(tmp_path):
    payload = json.loads((REPO_ROOT / AXION_FIXTURE).read_text(encoding="utf-8"))
    payload["projects"] = [
        *payload["projects"],
        {**payload["projects"][0], "team_id": "team_nowhere"},
    ]
    broken = tmp_path / "broken.json"
    broken.write_text(json.dumps(payload), encoding="utf-8")

    completed = _run(str(broken))
    assert completed.returncode == 1
    assert "RESULT: INVALID" in completed.stdout
    assert "unknown_team" in completed.stdout
    # The invalid record is not silently dropped: the whole import is refused,
    # and the CLI says which record and why.
    assert "team_nowhere" in completed.stdout


def test_a_missing_file_is_an_exit_2(tmp_path):
    completed = _run(str(tmp_path / "absent.json"))
    assert completed.returncode == 2
    assert "fixture not found" in completed.stderr


def test_the_json_report_carries_the_same_facts():
    completed = _run(ORGANISER_FIXTURE, "--json")
    assert completed.returncode == 0, completed.stderr
    body = json.loads(completed.stdout)
    assert body["valid"] is True
    assert body["problems"] == []
    assert body["records"] == {
        "projects": 41,
        "judges": 30,
        "teams": 40,
        # Derived from `teams[].members`: the organisers' file carries addresses
        # for people, never names, so the accounts are created from the addresses.
        "participants": 91,
        "reviews": 126,
        # Derived from `judges[].tracks`: every judge covers every project in a
        # track they declared. 126 of those 199 assignments carry a verdict,
        # which is the "two review batches nobody finished" case, at scale.
        "assignments": 199,
    }
    # Detected from the data, not read from a note in the file: three judges gave
    # every project they scored the same mark, and two filed a single verdict.
    assert body["zero_variance_judges"] == ["jdg_07", "jdg_27", "jdg_28"]
    assert body["single_verdict_judges"] == ["jdg_01", "jdg_23"]


def test_the_axion_dataset_json_report_carries_the_same_facts():
    completed = _run(AXION_FIXTURE, "--json")
    assert completed.returncode == 0, completed.stderr
    body = json.loads(completed.stdout)
    assert body["records"]["projects"] == 40
    assert body["zero_variance_judges"] == ["judge_11"]
    assert body["single_verdict_judges"] == ["judge_12"]
