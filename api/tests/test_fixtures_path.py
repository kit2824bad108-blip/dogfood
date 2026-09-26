"""The fixture dataset can be named explicitly (`FIXTURES_PATH`).

A container has no repository root, so compose mounts `fixtures.json` and points
this variable at the mount. These tests pin the two places that read it: the
loader and the event window.
"""
from __future__ import annotations

import json
from datetime import datetime, timezone

from app import config
from app import fixtures as fixtures_module

MINI = {
    "fixture_version": 1,
    "generator": "tests/fixtures-path",
    "event": {
        "starts_at": "2020-01-01T00:00:00+00:00",
        "ends_at": "2020-01-02T00:00:00+00:00",
    },
    "teams": [{"id": "team_1", "name": "Path Team"}],
    "projects": [
        {
            "id": "proj_1",
            "team_id": "team_1",
            "title": "Path Project",
            "repo_url": "https://github.com/path/project",
        }
    ],
}


def _write(tmp_path, name: str = "mounted-fixtures.json"):
    target = tmp_path / name
    target.write_text(json.dumps(MINI), encoding="utf-8")
    return target


def test_the_loader_reads_the_named_file(tmp_path, monkeypatch):
    target = _write(tmp_path)
    monkeypatch.setenv("FIXTURES_PATH", str(target))

    assert fixtures_module.default_fixture_path() == target
    assert fixtures_module.load_fixture() == MINI


def test_the_event_window_follows_the_named_file(tmp_path, monkeypatch):
    target = _write(tmp_path, "window-fixtures.json")
    monkeypatch.setenv("FIXTURES_PATH", str(target))

    assert config.fixtures_path() == str(target)
    assert config.fixture_window() == (
        datetime(2020, 1, 1, tzinfo=timezone.utc),
        datetime(2020, 1, 2, tzinfo=timezone.utc),
    )


def test_an_explicit_relative_path_is_read_beside_the_named_file(tmp_path, monkeypatch):
    target = _write(tmp_path)
    monkeypatch.setenv("FIXTURES_PATH", str(target))

    # A relative path resolves beside the configured fixture, not beside the
    # repository root, so FIXTURES_PATH relocates a deployment's datasets.
    assert fixtures_module.load_fixture(target.name) == MINI


def test_unset_means_the_repository_root(monkeypatch):
    monkeypatch.delenv("FIXTURES_PATH", raising=False)

    assert fixtures_module.default_fixture_path() == fixtures_module.REPO_ROOT / "fixtures.json"
