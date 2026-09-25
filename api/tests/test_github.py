"""Commit Integrity parsing and accounting."""
from __future__ import annotations

from datetime import datetime, timezone

import httpx
import pytest

from app import github

START = datetime(2026, 9, 20, tzinfo=timezone.utc)
END = datetime(2026, 9, 23, tzinfo=timezone.utc)


class FakeResponse:
    def __init__(self, status_code: int, payload):
        self.status_code = status_code
        self._payload = payload

    def json(self):
        return self._payload

    def raise_for_status(self):
        if self.status_code >= 400:
            raise RuntimeError(f"unexpected status {self.status_code}")


class FakeClient:
    """Routes the two endpoints the checker uses."""

    def __init__(self, commits, repo_payload=None, status=200):
        self.commits = commits
        self.repo_payload = repo_payload or {"created_at": "2026-09-21T00:00:00Z"}
        self.status = status
        self.calls = 0

    def get(self, url, params=None, headers=None):
        self.calls += 1
        if url.endswith("/commits"):
            return FakeResponse(self.status, self.commits)
        return FakeResponse(200, self.repo_payload)

    def close(self):
        pass


def commit(iso: str) -> dict:
    return {"commit": {"author": {"date": iso}}}


@pytest.mark.parametrize(
    "url,expected",
    [
        ("https://github.com/axion-demo/thing", ("axion-demo", "thing")),
        ("http://github.com/axion-demo/thing.git", ("axion-demo", "thing")),
        ("git@github.com:axion-demo/thing.git", ("axion-demo", "thing")),
        ("axion-demo/thing", ("axion-demo", "thing")),
    ],
)
def test_parse_repo_accepts_common_forms(url, expected):
    assert github.parse_repo(url) == expected


def test_parse_repo_rejects_nonsense():
    assert github.parse_repo("not a repo") is None
    assert github.parse_repo("") is None


def test_mock_mode_is_deterministic():
    first = github.check_commit_integrity(
        "https://github.com/axion-demo/distributed-ledger", event_start=START, event_end=END, mock=True
    )
    second = github.check_commit_integrity(
        "https://github.com/axion-demo/distributed-ledger", event_start=START, event_end=END, mock=True
    )
    assert first["pct_in_window"] == second["pct_in_window"]
    assert first["source"] == "mock"


def test_mock_mode_flags_preexisting_names():
    report = github.check_commit_integrity(
        "https://github.com/axion-demo/legacy-monolith", event_start=START, event_end=END, mock=True
    )
    assert report["flagged"] is True
    assert report["pct_in_window"] < github.FLAG_BELOW_PCT


def test_real_mode_counts_commits_inside_the_window():
    commits = [
        commit("2026-09-21T10:00:00Z"),
        commit("2026-09-22T10:00:00Z"),
        commit("2026-09-10T10:00:00Z"),
        commit("2026-08-01T10:00:00Z"),
    ]
    client = FakeClient(commits)
    report = github.check_commit_integrity(
        "https://github.com/axion-demo/thing",
        event_start=START,
        event_end=END,
        mock=False,
        client=client,
    )
    assert report["source"] == "github"
    assert report["commits_scanned"] == 4
    assert report["commits_in_window"] == 2
    assert report["pct_in_window"] == 50.0
    assert report["flagged"] is False


def test_real_mode_flags_a_pre_existing_history():
    commits = [commit("2026-01-01T10:00:00Z") for _ in range(6)] + [commit("2026-09-21T10:00:00Z")]
    report = github.check_commit_integrity(
        "https://github.com/axion-demo/thing",
        event_start=START,
        event_end=END,
        mock=False,
        client=FakeClient(commits),
    )
    assert report["flagged"] is True
    assert "predates the event" in report["reason"]


def test_private_repository_is_reported_not_crashed():
    report = github.check_commit_integrity(
        "https://github.com/axion-demo/secret",
        event_start=START,
        event_end=END,
        mock=False,
        client=FakeClient([], status=404),
    )
    assert report["source"] == "unavailable"
    assert "not found or private" in report["reason"]


def test_rate_limit_is_reported():
    report = github.check_commit_integrity(
        "https://github.com/axion-demo/thing",
        event_start=START,
        event_end=END,
        mock=False,
        client=FakeClient([], status=403),
    )
    assert "rate limit" in report["reason"].lower()


def test_bad_url_is_rejected_without_network():
    report = github.check_commit_integrity(
        "not-a-url", event_start=START, event_end=END, mock=False
    )
    assert report["source"] == "unavailable"
    assert report["pct_in_window"] is None
