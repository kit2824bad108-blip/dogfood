"""The default event window must be open on a cold start.

Regression guard. `EVENT_END` is blank in `.env.example` and in
`docker-compose.yml`, and the blank default used to resolve to *now* — which
closed the submission window at the instant the process started. On the headline
`docker compose up` path that meant drafting and submitting failed immediately
for anyone who had not written a `.env` by hand.
"""
from __future__ import annotations

from datetime import datetime, timedelta, timezone

from app.config import Settings


def _settings(monkeypatch, **env) -> Settings:
    for key in ("EVENT_START", "EVENT_END"):
        monkeypatch.delenv(key, raising=False)
    for key, value in env.items():
        monkeypatch.setenv(key, value)
    return Settings.from_env()


def test_a_blank_window_is_open_after_boot(monkeypatch):
    settings = _settings(monkeypatch)
    now = datetime.now(timezone.utc)

    assert settings.event_start < now < settings.event_end
    assert not settings.event_window_closed
    assert not settings.event_window_opens_in_future
    # Still a 72-hour event, just anchored to boot instead of ending at it.
    assert settings.event_end - settings.event_start == timedelta(hours=72)


def test_an_explicit_start_is_honoured_and_closes_72h_later(monkeypatch):
    start = datetime.now(timezone.utc) - timedelta(hours=2)
    settings = _settings(monkeypatch, EVENT_START=start.isoformat())

    assert settings.event_start == start
    assert settings.event_end - settings.event_start == timedelta(hours=72)
    assert not settings.event_window_closed


def test_an_explicit_past_close_still_closes_the_event(monkeypatch):
    """The fixture dataset ships a closed event; that has to keep working."""
    end = datetime.now(timezone.utc) - timedelta(hours=3)
    settings = _settings(monkeypatch, EVENT_END=end.isoformat())

    assert settings.event_end == end
    assert settings.event_start == end - timedelta(hours=72)
    assert settings.event_window_closed
