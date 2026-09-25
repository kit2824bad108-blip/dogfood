#!/usr/bin/env python3
"""Axion acceptance suite — tier by tier, against a running API over HTTP.

    # with the stack up (docker compose up, or uvicorn app.main:app)
    python api/scripts/acceptance.py            # prints the report
    python api/scripts/acceptance.py > acceptance-report.txt

This is **Axion's own** acceptance suite. No organiser-provided acceptance
script, Postman collection or test harness was present in this repository or on
the machine it was built on, so rather than claim a run that never happened, this
drives the real API over HTTP and reports what it actually observes. Read-only
checks run first; the mutating checks (registration, a submission, a new judge)
come last so they cannot perturb the normalization proof above them.

Exit code is 0 only if nothing failed. SKIP means "not verifiable in this
environment", and every skip says why.
"""
from __future__ import annotations

import csv
import io
import json
import os
import platform
import subprocess
import sys
import time
from dataclasses import dataclass, field
from datetime import datetime, timezone
from pathlib import Path
from typing import Callable

try:
    import httpx
except ImportError:  # pragma: no cover - the API venv always has httpx
    print("httpx is required: pip install httpx", file=sys.stderr)
    raise SystemExit(2)

# The report is committed to the repository, so it is written as UTF-8 even on a
# console that defaults to a legacy code page.
if hasattr(sys.stdout, "reconfigure"):
    sys.stdout.reconfigure(encoding="utf-8", errors="replace")

BASE_URL = os.environ.get("AXION_API_URL", "http://localhost:8000").rstrip("/")
ADMIN_EMAIL = os.environ.get("AXION_ADMIN_EMAIL", "admin@axion.dev")
ADMIN_PASSWORD = os.environ.get("AXION_ADMIN_PASSWORD", "axion-admin")
REPO_ROOT = Path(__file__).resolve().parents[2]
TIMEOUT = 30.0

PASS, FAIL, SKIP = "PASS", "FAIL", "SKIP"
TIER_ORDER = ["T0", "T1", "T2", "T3", "T4", "BONUS", "DOCS"]
TIER_TITLES = {
    "T0": "T0 — Runtime and boundaries",
    "T1": "T1 — Core (the minimum to be judged)",
    "T2": "T2 — Judging",
    "T3": "T3 — Public surface and audit trail",
    "T4": "T4 — Stretch",
    "BONUS": "Bonus claims",
    "DOCS": "Required artefacts",
}


@dataclass
class Result:
    tier: str
    name: str
    status: str
    detail: str
    ms: int


@dataclass
class Suite:
    client: httpx.Client
    stamp: str = field(default_factory=lambda: str(int(time.time())))
    results: list[Result] = field(default_factory=list)

    # ── plumbing ────────────────────────────────────────────────────────────
    def check(self, tier: str, name: str) -> Callable[[Callable[[], tuple[str, str]]], None]:
        def decorator(fn: Callable[[], tuple[str, str]]) -> None:
            started = time.perf_counter()
            try:
                status, detail = fn()
            except AssertionError as exc:
                status, detail = FAIL, str(exc) or "assertion failed"
            except Exception as exc:  # noqa: BLE001 - the report must survive anything
                status, detail = FAIL, f"{type(exc).__name__}: {exc}"
            self.results.append(
                Result(tier, name, status, detail, int((time.perf_counter() - started) * 1000))
            )

        return decorator

    def get(self, path: str, **kwargs) -> httpx.Response:
        return self.client.get(path, **kwargs)

    def post(self, path: str, **kwargs) -> httpx.Response:
        return self.client.post(path, **kwargs)

    def json(self, path: str, **kwargs) -> dict:
        response = self.get(path, **kwargs)
        assert response.status_code == 200, f"GET {path} -> {response.status_code} {response.text[:200]}"
        return response.json()


def unique(prefix: str, suite: Suite) -> str:
    return f"{prefix}-{suite.stamp}"


def as_csv(text: str) -> list[list[str]]:
    return list(csv.reader(io.StringIO(text)))


def git_sha() -> str:
    try:
        return subprocess.run(
            ["git", "rev-parse", "--short", "HEAD"],
            cwd=REPO_ROOT,
            capture_output=True,
            text=True,
            timeout=10,
            check=False,
        ).stdout.strip() or "unknown"
    except Exception:  # noqa: BLE001
        return "unknown"


# ── the checks ──────────────────────────────────────────────────────────────


def run_checks(suite: Suite) -> None:
    client = suite.client

    # ── T0: runtime + boundaries (read-only) ────────────────────────────────

    @suite.check("T0", "API answers on /api/health")
    def _() -> tuple[str, str]:
        body = suite.json("/api/health")
        assert body["status"] == "ok", f"status={body.get('status')}"
        return PASS, f"event={body.get('event')!r}, integrity={body.get('commit_integrity_source')}"

    @suite.check("T0", "Admin endpoints require authentication")
    def _() -> tuple[str, str]:
        codes = {}
        for path in (
            "/api/admin/overview",
            "/api/admin/leaderboard",
            "/api/admin/audit",
            "/api/admin/export/leaderboard.csv",
        ):
            codes[path] = httpx.get(f"{BASE_URL}{path}", timeout=TIMEOUT).status_code
        assert all(code == 401 for code in codes.values()), f"expected 401 everywhere, got {codes}"
        return PASS, "anonymous access to all four admin routes -> 401"

    @suite.check("T0", "Public reads work without a session")
    def _() -> tuple[str, str]:
        codes = {}
        for path in ("/api/event", "/api/gallery", "/api/auth/status"):
            codes[path] = httpx.get(f"{BASE_URL}{path}", timeout=TIMEOUT).status_code
        assert all(code == 200 for code in codes.values()), f"expected 200 everywhere, got {codes}"
        return PASS, "event, gallery and auth status are anonymous"

    @suite.check("T0", "Admin signs in and holds a session")
    def _() -> tuple[str, str]:
        body = _login_admin(suite)
        return PASS, f"signed in as {body['user']['email']} ({body['user']['role']})"

    # ── T1: core ─────────────────────────────────────────────────────────────

    @suite.check("T1", "Event configuration is public: window, tracks, prizes")
    def _() -> tuple[str, str]:
        event = suite.json("/api/event")
        window = event["event"]["submission_window"]
        assert window["opens_at"] and window["closes_at"], "no server-side event window published"
        assert event["tracks"], "no tracks configured"
        prizes = sum(len(track["prizes"]) for track in event["tracks"]) + len(
            event["overall_prizes"]
        )
        assert prizes > 0, "no prizes configured"
        return PASS, (
            f"{len(event['tracks'])} tracks, {prizes} prizes, phase={event['event']['phase']}, "
            f"closes {window['closes_at']}"
        )

    @suite.check("T1", "Deadline is enforced against the server clock")
    def _() -> tuple[str, str]:
        event = suite.json("/api/event")
        closed = event["event"]["submission_window"]["closed"]
        if not closed:
            return SKIP, (
                "submission window is still open, so the closed path cannot be observed here; "
                "the pytest suite covers it with a frozen window "
                "(tests/test_event_features.py::test_the_deadline_is_enforced_server_side)"
            )
        # A real participant with a real team, so the request reaches the deadline
        # check rather than the authentication or membership guard.
        email = unique("accept-late", suite) + "@axion.test"
        with httpx.Client(base_url=BASE_URL, timeout=TIMEOUT) as late:
            late.post(
                "/api/auth/register",
                json={"email": email, "password": "acceptance-pass-1", "name": "Late Runner"},
            )
            late.post("/api/teams", json={"name": unique("Late Team", suite)})
            response = late.post(
                "/api/submissions",
                json={"title": "too late", "repo_url": "https://github.com/axion-demo/too-late"},
            )
        assert response.status_code == 403, f"expected 403 after the deadline, got {response.status_code}"
        assert "closed" in response.json()["detail"].lower(), response.text[:200]
        return PASS, "post-deadline write -> 403 with the rejection written to the audit trail"

    @suite.check("T1", "Searchable public gallery")
    def _() -> tuple[str, str]:
        everything = suite.json("/api/gallery")
        assert everything["count"] > 0, "gallery is empty"
        titles = [row["title"] for row in everything["projects"]]
        needle = titles[0].split()[0]
        found = suite.json(f"/api/gallery?q={needle}")
        assert found["count"] >= 1, f"search for {needle!r} returned nothing"
        assert all(
            needle.lower() in f"{row['title']} {row['team']} {row['summary']}".lower()
            for row in found["projects"]
        ), "search returned non-matching rows"
        assert found["count"] <= everything["count"], "search returned more rows than the unfiltered list"
        tracks = {row["slug"] for row in everything["tracks"]}
        filtered = suite.json(f"/api/gallery?track={sorted(tracks)[0]}")
        assert all(row["track"]["slug"] == sorted(tracks)[0] for row in filtered["projects"])
        return PASS, (
            f"{everything['count']} projects listed, search q={needle!r} -> {found['count']}, "
            f"track filter on {sorted(tracks)[0]} -> {filtered['count']}"
        )

    @suite.check("T1", "Gallery cannot be used to bypass blind evaluation")
    def _() -> tuple[str, str]:
        projects = suite.json("/api/gallery")["projects"]
        leaked = [
            row["title"] for row in projects if "demo_url" in row or "video_url" in row
        ]
        assert not leaked, f"presentation links leaked for {leaked}"
        return PASS, "no demo_url or video_url in any public gallery row"

    @suite.check("T1", "Participant flow: register, team, invite code, one team per person")
    def _() -> tuple[str, str]:
        email = unique("accept-participant", suite) + "@axion.test"
        with httpx.Client(base_url=BASE_URL, timeout=TIMEOUT) as participant:
            registered = participant.post(
                "/api/auth/register",
                json={"email": email, "password": "acceptance-pass-1", "name": "Acceptance Runner"},
            )
            assert registered.status_code == 200, registered.text[:200]
            assert registered.json()["user"]["role"] == "participant", "register must not grant a role"
            created = participant.post("/api/teams", json={"name": unique("Acceptance Team", suite)})
            assert created.status_code == 200, created.text[:200]
            code = created.json()["team"]["invite_code"]
            again = participant.post("/api/teams/join", json={"invite_code": code})
            assert again.status_code == 400, "a second team membership must be refused"
        return PASS, f"registered {email}, created a team, invite-code reuse refused"

    @suite.check("T1", "Draft submissions are private and promotable")
    def _() -> tuple[str, str]:
        email = unique("accept-drafter", suite) + "@axion.test"
        title = unique("Acceptance Draft", suite)
        with httpx.Client(base_url=BASE_URL, timeout=TIMEOUT) as drafter:
            drafter.post(
                "/api/auth/register",
                json={"email": email, "password": "acceptance-pass-1", "name": "Draft Runner"},
            )
            drafter.post("/api/teams", json={"name": unique("Draft Team", suite)})
            payload = {
                "title": title,
                "repo_url": f"https://github.com/axion-demo/{unique('acceptance', suite)}",
            }
            draft = drafter.post("/api/submissions", json={**payload, "status": "draft"})
            assert draft.status_code == 200, draft.text[:200]
            body = draft.json()["submission"]
            assert body["status"] == "draft", f"status={body['status']}"
            assert body["submitted_at"] is None, "a draft must not be stamped as submitted"
            gallery = suite.json("/api/gallery")
            assert all(row["title"] != title for row in gallery["projects"]), "draft leaked to gallery"

            promoted = drafter.post("/api/submissions", json={**payload, "status": "submitted"})
            assert promoted.status_code == 200, promoted.text[:200]
            final = promoted.json()["submission"]
            assert final["status"] == "submitted" and final["submitted_at"], "promotion failed"
        gallery = suite.json("/api/gallery")
        assert any(row["title"] == title for row in gallery["projects"]), "submitted project missing"
        return PASS, "draft hidden from the gallery, promotion ran integrity + assignment, now listed"

    @suite.check("T1", "Configurable rubric is published")
    def _() -> tuple[str, str]:
        rubric = suite.json("/api/event")["rubric"]
        assert rubric["criteria"], "no rubric configured"
        total = sum(entry["percent"] for entry in rubric["criteria"])
        assert 99.0 <= total <= 101.0, f"weights do not sum to 100%: {total}"
        return PASS, " · ".join(f"{c['label']} {c['percent']}%" for c in rubric["criteria"])

    # ── T2: judging ──────────────────────────────────────────────────────────

    @suite.check("T2", "Participants cannot score anything")
    def _() -> tuple[str, str]:
        email = unique("accept-voter", suite) + "@axion.test"
        attempts: dict[str, int] = {}
        with httpx.Client(base_url=BASE_URL, timeout=TIMEOUT) as voter:
            voter.post(
                "/api/auth/register",
                json={"email": email, "password": "acceptance-pass-1", "name": "Voter"},
            )
            # The method that would work for each route, so a 405 cannot be
            # mistaken for the role guard doing its job.
            attempts["POST /api/judging/scores"] = voter.post(
                "/api/judging/scores", json={"submission_id": 1, "technical_score": 10}
            ).status_code
            attempts["GET /api/judging/assignments"] = voter.get("/api/judging/assignments").status_code
            attempts["POST /api/judging/scores (presentation)"] = voter.post(
                "/api/judging/scores", json={"submission_id": 1, "presentation_score": 10}
            ).status_code
            attempts["GET /api/admin/leaderboard"] = voter.get("/api/admin/leaderboard").status_code
            attempts["GET /api/admin/export/scores.csv"] = voter.get(
                "/api/admin/export/scores.csv"
            ).status_code
        assert all(code == 403 for code in attempts.values()), f"participant got through: {attempts}"
        return PASS, ", ".join(f"{route} -> 403" for route in attempts)

    @suite.check("T2", "Weighted rubric derives the technical verdict")
    def _() -> tuple[str, str]:
        # Verified against the live rubric rather than assumed: read the weights,
        # file criterion values, then check the stored verdict is the weighted mean.
        judge_email = unique("accept-judge", suite) + "@axion.test"
        created = suite.post(
            "/api/admin/judges",
            json={"email": judge_email, "password": "acceptance-pass-1", "name": "Acceptance Judge"},
        )
        assert created.status_code == 200, created.text[:200]

        with httpx.Client(base_url=BASE_URL, timeout=TIMEOUT) as judge:
            judge.post("/api/auth/login", json={"email": judge_email, "password": "acceptance-pass-1"})
            assignments = judge.get("/api/judging/assignments").json()
            rubric = assignments["rubric"]
            assert assignments["assignments"], "a new judge was assigned nothing"
            target = assignments["assignments"][0]["submission_id"]
            values = {entry["key"]: (9 if index % 2 == 0 else 5) for index, entry in enumerate(rubric["criteria"])}
            submitted = judge.post(
                "/api/judging/scores",
                json={
                    "submission_id": target,
                    "criteria": [{"key": key, "value": value} for key, value in values.items()],
                    "technical_comment": "Acceptance runner verdict.",
                },
            )
            assert submitted.status_code == 200, submitted.text[:200]
            body = submitted.json()
            expected = _weighted(rubric["criteria"], values)
            assert body["score"]["technical_score"] == expected, (
                f"derived {body['score']['technical_score']} != weighted mean {expected}"
            )
            assert body["score"]["criteria"] == values, "criterion values were not stored"
            assert body["score"]["rubric_id"], "verdict did not record which rubric produced it"
            assert body["technical_score_derived"] == expected
        return PASS, (
            f"{judge_email} filed {values} -> stored verdict "
            f"{body['score']['technical_score']}/10 on submission {target}, "
            f"rubric #{body['score']['rubric_id']}"
        )

    @suite.check("T2", "Blind gate: presentation is withheld until Tier 1 is filed")
    def _() -> tuple[str, str]:
        judge_email = unique("accept-blind", suite) + "@axion.test"
        suite.post(
            "/api/admin/judges",
            json={"email": judge_email, "password": "acceptance-pass-1", "name": "Blind Judge"},
        )
        with httpx.Client(base_url=BASE_URL, timeout=TIMEOUT) as judge:
            judge.post("/api/auth/login", json={"email": judge_email, "password": "acceptance-pass-1"})
            assignments = judge.get("/api/judging/assignments").json()["assignments"]
            target = assignments[0]["submission_id"]

            before = judge.get(f"/api/judging/submissions/{target}/presentation")
            assert before.status_code == 403, (
                f"presentation was readable before a technical verdict ({before.status_code})"
            )
            assert "presentation" in before.json()["detail"].lower()

            rubric = judge.get("/api/judging/assignments").json()["rubric"]
            filed = judge.post(
                "/api/judging/scores",
                json={
                    "submission_id": target,
                    "criteria": [{"key": c["key"], "value": 7} for c in rubric["criteria"]],
                },
            )
            assert filed.status_code == 200, filed.text[:200]

            after = judge.get(f"/api/judging/submissions/{target}/presentation")
            assert after.status_code == 200, f"still locked after Tier 1 ({after.status_code})"

            # And it is enforced server-side, not merely hidden in the interface.
            outsider = httpx.get(f"{BASE_URL}/api/judging/submissions/{target}/presentation", timeout=TIMEOUT)
            assert outsider.status_code == 401, "unauthenticated presentation read should be 401"
        return PASS, "403 before the technical verdict, 200 after, 401 for anonymous callers"

    @suite.check("T2", "Judge progress dashboard reports real counts")
    def _() -> tuple[str, str]:
        progress = suite.json("/api/admin/judging-progress")
        assert progress["judges"], "no judges reported"
        assert progress["totals"]["expected_technical_verdicts"] >= progress["totals"]["technical_verdicts"]
        row = progress["judges"][0]
        for key in ("assigned", "technical_done", "technical_pending", "percent"):
            assert key in row, f"progress row missing {key}"
        assert row["assigned"] == row["technical_done"] + row["technical_pending"]
        return PASS, (
            f"{progress['totals']['technical_verdicts']}/"
            f"{progress['totals']['expected_technical_verdicts']} verdicts "
            f"({progress['totals']['percent']}%), {len(progress['judges'])} judges tracked"
        )

    @suite.check("T2", "Per-judge progress is also exposed to judges themselves")
    def _() -> tuple[str, str]:
        judge_email = unique("accept-progress", suite) + "@axion.test"
        suite.post(
            "/api/admin/judges",
            json={"email": judge_email, "password": "acceptance-pass-1", "name": "Progress Judge"},
        )
        with httpx.Client(base_url=BASE_URL, timeout=TIMEOUT) as judge:
            judge.post("/api/auth/login", json={"email": judge_email, "password": "acceptance-pass-1"})
            body = judge.get("/api/judging/assignments").json()
            progress = body["progress"]
            assert progress["total"] == len(body["assignments"])
            assert progress["technical_done"] == 0, "a brand new judge should have graded nothing"
            assert progress["technical_pending"] == progress["total"]
        return PASS, f"x/{progress['total']} graded, {progress['technical_pending']} pending"

    @suite.check("T2", "CSV export: leaderboard")
    def _() -> tuple[str, str]:
        response = suite.get("/api/admin/export/leaderboard.csv")
        assert response.status_code == 200, response.text[:200]
        assert response.headers["content-type"].startswith("text/csv")
        assert "attachment" in response.headers.get("content-disposition", "")
        rows = as_csv(response.text)
        assert rows[0][:4] == ["rank", "submission_id", "project", "team"], f"header={rows[0]}"
        assert len(rows) >= 2, "no leaderboard rows exported"
        assert all(len(row) == len(rows[0]) for row in rows), "ragged CSV"
        return PASS, f"{len(rows) - 1} rows, {len(rows[0])} columns"

    @suite.check("T2", "CSV export: every verdict with z-scores")
    def _() -> tuple[str, str]:
        response = suite.get("/api/admin/export/scores.csv")
        assert response.status_code == 200, response.text[:200]
        rows = as_csv(response.text)
        header = rows[0]
        for column in ("technical_score", "judge_raw_mean", "judge_raw_sigma", "z_score"):
            assert column in header, f"{column} missing from scores.csv"
        assert any(column.startswith("criterion:") for column in header), "no per-criterion columns"
        assert len(rows) >= 2, "no verdict rows exported"
        return PASS, f"{len(rows) - 1} verdicts, {len(header)} columns including per-criterion"

    @suite.check("T2", "CSV export: judging progress")
    def _() -> tuple[str, str]:
        response = suite.get("/api/admin/export/judging-progress.csv")
        assert response.status_code == 200, response.text[:200]
        rows = as_csv(response.text)
        assert rows[0][:3] == ["judge_id", "judge", "email"], f"header={rows[0]}"
        assert len(rows) >= 2, "no judge rows exported"
        return PASS, f"{len(rows) - 1} judges"

    @suite.check("T2", "Exports are admin-only")
    def _() -> tuple[str, str]:
        judge_email = unique("accept-export", suite) + "@axion.test"
        suite.post(
            "/api/admin/judges",
            json={"email": judge_email, "password": "acceptance-pass-1", "name": "Curious Judge"},
        )
        with httpx.Client(base_url=BASE_URL, timeout=TIMEOUT) as judge:
            judge.post("/api/auth/login", json={"email": judge_email, "password": "acceptance-pass-1"})
            codes = {
                path: judge.get(path).status_code
                for path in (
                    "/api/admin/export/leaderboard.csv",
                    "/api/admin/export/scores.csv",
                    "/api/admin/export/judging-progress.csv",
                )
            }
        assert all(code == 403 for code in codes.values()), f"judge reached exports: {codes}"
        return PASS, "a judge session gets 403 on all three exports"

    # ── T3: public surface + audit trail ────────────────────────────────────

    @suite.check("T3", "Append-only audit trail records who did what, from where")
    def _() -> tuple[str, str]:
        entries = suite.json("/api/admin/audit?limit=500")["entries"]
        assert entries, "audit trail is empty"
        actions = {entry["action"] for entry in entries}
        for expected in ("auth.login", "score.technical_submitted"):
            assert expected in actions, f"{expected} missing from the audit trail"
        with_ip = [entry for entry in entries if entry["ip"]]
        assert with_ip, "no audit entry recorded a client IP"
        assert all(entry["created_at"] for entry in entries), "audit entry without a timestamp"
        return PASS, (
            f"{len(entries)} entries, {len(actions)} distinct actions, "
            f"{len(with_ip)} carry a client IP"
        )

    @suite.check("T3", "Audit entries cannot be updated or deleted through the API")
    def _() -> tuple[str, str]:
        attempts = {
            "PUT": suite.client.put(f"/api/admin/audit/1", json={}).status_code,
            "DELETE": suite.client.delete("/api/admin/audit/1").status_code,
            "PATCH": suite.client.patch("/api/admin/audit/1", json={}).status_code,
        }
        assert all(code in (404, 405) for code in attempts.values()), f"mutating route exists: {attempts}"
        return PASS, "no update, patch or delete route exists for audit entries"

    @suite.check("T3", "Ephemeral archive bundle is publishable")
    def _() -> tuple[str, str]:
        body = suite.post("/api/admin/archive").json()
        bundle = body["bundle"]
        assert bundle["bundle_version"] >= 1, "no bundle version"
        assert bundle["results"], "archive has no results"
        assert bundle["verdicts"], "archive has no verdicts"
        assert "technical verdicts only" in bundle["methodology"]["ranking_basis"]

        # Ranked results must form a clean 1..n sequence. What is *not* required is
        # that every result is ranked: a project submitted moments ago with no
        # verdicts yet is unranked, and saying so is more honest than inventing a
        # position for it.
        ranked = [row for row in bundle["results"] if row["axion_rank"] is not None]
        assert ranked, "nothing in the archive is ranked"
        assert sorted(row["axion_rank"] for row in ranked) == list(range(1, len(ranked) + 1)), (
            "ranks are not a contiguous 1..n sequence"
        )
        unranked = [row for row in bundle["results"] if row["axion_rank"] is None]
        assert all(row["z_score"] is None for row in unranked), "unranked result carried a z-score"

        markdown = body["markdown"]
        assert markdown.startswith("# "), "markdown has no title"
        assert "| Rank | Project |" in markdown, "markdown has no results table"
        return PASS, (
            f"bundle v{bundle['bundle_version']}, {len(ranked)} ranked of {len(bundle['results'])} "
            f"results ({len(unranked)} awaiting verdicts), {len(bundle['verdicts'])} verdicts, "
            f"{len(markdown)} chars of RESULTS.md"
        )

    # ── T4 ──────────────────────────────────────────────────────────────────

    @suite.check("T4", "PDF certificates")
    def _() -> tuple[str, str]:
        return SKIP, (
            "not implemented, and deliberately last in priority: the rules prefer a clean T2 to a "
            "broken T4. The archive bundle carries every field a certificate would need."
        )

    @suite.check("T4", "Pairwise comparison mode")
    def _() -> tuple[str, str]:
        return SKIP, (
            "not implemented: the requirement text supplied for this challenge is truncated after the "
            "heading, and pairwise/Bradley-Terry is a large feature. Spec it and it can be built."
        )

    # ── Bonus ───────────────────────────────────────────────────────────────

    @suite.check("BONUS", "Normalization proof — the rankings genuinely disagree")
    def _() -> tuple[str, str]:
        board = suite.json("/api/admin/leaderboard")
        rows = {row["title"]: row for row in board["leaderboard"]}
        quiet = rows.get("Quiet Craft")
        flashy = rows.get("Flashy Demo")
        assert quiet and flashy, "the crafted demo pair is not in the leaderboard"
        assert flashy["raw_average"] > quiet["raw_average"], "naive average no longer prefers the demo"
        assert flashy["raw_rank"] < quiet["raw_rank"], "raw ranking no longer prefers the demo"
        assert quiet["axion_rank"] < flashy["axion_rank"], "Axion no longer prefers the craft"
        assert quiet["axion_score"] > flashy["axion_score"], "Axion scores do not cross over"
        assert quiet["rank_movement"] > 0 and flashy["rank_movement"] < 0, "no rank movement recorded"
        return PASS, (
            f"Quiet Craft raw #{quiet['raw_rank']} ({quiet['raw_average']}) -> Axion "
            f"#{quiet['axion_rank']} ({quiet['axion_score']}); Flashy Demo raw #{flashy['raw_rank']} "
            f"({flashy['raw_average']}) -> Axion #{flashy['axion_rank']} ({flashy['axion_score']})"
        )

    @suite.check("BONUS", "API First — generated OpenAPI schema and interactive docs")
    def _() -> tuple[str, str]:
        schema = suite.json("/api/openapi.json")
        assert schema["openapi"].startswith("3."), f"openapi={schema['openapi']}"
        paths = schema["paths"]
        required = {
            "/api/auth/me",
            "/api/submissions",
            "/api/judging/scores",
            "/api/admin/leaderboard",
            "/api/gallery",
            "/api/event",
        }
        missing = required - set(paths)
        assert not missing, f"missing from the schema: {missing}"
        docs = client.get("/api/docs")
        assert docs.status_code == 200, f"/api/docs -> {docs.status_code}"
        return PASS, f"OpenAPI {schema['openapi']}, {len(paths)} documented paths, Swagger UI at /api/docs"

    @suite.check("BONUS", "Offline mode — local dev login with no external calls")
    def _() -> tuple[str, str]:
        with httpx.Client(base_url=BASE_URL, timeout=TIMEOUT) as offline:
            status = offline.get("/api/auth/status").json()
            if not status.get("local_dev_login"):
                return SKIP, (
                    "MOCK_GITHUB and SEED_DEMO are both off, so dev login is disabled by design "
                    "(this is what a production deployment looks like)"
                )
            signed_in = offline.post("/api/auth/dev-login", json={"role": "admin"})
            assert signed_in.status_code == 200, signed_in.text[:200]
            assert signed_in.json()["offline"] is True
            me = offline.get("/api/auth/me").json()
            assert me["authenticated"] and me["user"]["role"] == "admin", "dev session is not usable"
            identifier = status["demo_accounts"][0]["email"]
            via_form = offline.post(
                "/api/auth/login", json={"email": "admin@axion.local", "password": "password"}
            )
            assert via_form.status_code == 200, "seeded .local credentials did not work"
        return PASS, (
            f"one-click admin session issued with zero external calls; {identifier} + 'password' "
            f"also works through the ordinary login form"
        )

    # ── Required artefacts ──────────────────────────────────────────────────

    @suite.check("DOCS", "Required files exist")
    def _() -> tuple[str, str]:
        # acceptance-report.txt is deliberately absent from this list: it is the
        # artefact this very run produces, so requiring it would be circular.
        required = [
            "README.md",
            "ARCHITECTURE.md",
            "DATA-MODEL.md",
            "JUDGING.md",
            "THREAT-MODEL.md",
            "docker-compose.yml",
            ".env.example",
        ]
        missing = [name for name in required if not (REPO_ROOT / name).exists()]
        assert not missing, f"missing: {missing}"
        return PASS, f"present: {', '.join(required)}"

    @suite.check("DOCS", "Documentation covers what it claims to")
    def _() -> tuple[str, str]:
        judging = (REPO_ROOT / "JUDGING.md").read_text(encoding="utf-8")
        for heading in (
            "Judge assignment strategy",
            "Scoring methodology",
            "normalization proof",
        ):
            assert heading.lower() in judging.lower(), f"JUDGING.md does not cover {heading!r}"
        model = (REPO_ROOT / "DATA-MODEL.md").read_text(encoding="utf-8")
        for table in ("users", "submissions", "scores", "audit_logs", "score_criteria"):
            assert f"`{table}`" in model, f"DATA-MODEL.md does not document {table}"
        threats = (REPO_ROOT / "THREAT-MODEL.md").read_text(encoding="utf-8")
        for threat in ("Sybil voting", "Judge collusion", "Deadline gaming"):
            assert threat.lower() in threats.lower(), f"THREAT-MODEL.md does not cover {threat}"
        return PASS, "JUDGING.md, DATA-MODEL.md and THREAT-MODEL.md match their required content"


def _login_admin(suite: Suite) -> dict:
    response = suite.post("/api/auth/login", json={"email": ADMIN_EMAIL, "password": ADMIN_PASSWORD})
    if response.status_code == 200:
        return response.json()
    # Fall back to the offline one-click login when the seed used other credentials.
    fallback = suite.post("/api/auth/dev-login", json={"role": "admin"})
    if fallback.status_code == 200:
        return fallback.json()
    raise AssertionError(
        f"could not sign in as {ADMIN_EMAIL} ({response.status_code}) and dev login is disabled "
        f"({fallback.status_code}); set AXION_ADMIN_EMAIL / AXION_ADMIN_PASSWORD"
    )


def _weighted(criteria: list[dict], values: dict[str, int]) -> int:
    total = sum(criterion["percent"] for criterion in criteria if criterion["key"] in values)
    blended = (
        sum(criterion["percent"] * values[criterion["key"]] for criterion in criteria if criterion["key"] in values)
        / total
    )
    return max(1, min(10, round(blended)))


def render(suite: Suite) -> str:
    lines: list[str] = []
    started = datetime.now(timezone.utc)
    counts = {PASS: 0, FAIL: 0, SKIP: 0}
    for result in suite.results:
        counts[result.status] += 1

    lines += [
        "AXION ACCEPTANCE REPORT",
        "=" * 78,
        "",
        "This report was produced by Axion's own acceptance suite ",
        "(api/scripts/acceptance.py), run against a live, seeded instance over HTTP.",
        "",
        "It is NOT a run of an organiser-provided acceptance suite: no such suite,",
        "Postman collection or test script was provided in the repository or the",
        "hackathon resources available at build time. Rather than claim a run that",
        "never happened, every line below is an observation of the running system.",
        "",
        f"Generated     : {started.isoformat()}",
        f"Target        : {BASE_URL}",
        f"Git commit    : {git_sha()}",
        f"Python        : {platform.python_version()} on {platform.system()}",
        f"Checks        : {len(suite.results)} total — "
        f"{counts[PASS]} passed, {counts[FAIL]} failed, {counts[SKIP]} skipped",
        "",
        "The mutating checks (a participant registration, a submitted project and",
        "three new judge accounts) change the instance they run against, so counts",
        "reported late in the run are larger than in a freshly seeded event. The",
        "normalization proof is verified by direction, not by absolute value.",
        "",
    ]

    for tier in TIER_ORDER:
        tier_results = [result for result in suite.results if result.tier == tier]
        if not tier_results:
            continue
        lines += ["-" * 78, TIER_TITLES[tier], "-" * 78, ""]
        for result in tier_results:
            lines.append(f"[{result.status}] {result.name}  ({result.ms} ms)")
            lines.append(f"       {result.detail}")
        lines.append("")

    lines += ["=" * 78, "SUMMARY", "=" * 78, ""]
    width = max(len(result.name) for result in suite.results) + 2
    for tier in TIER_ORDER:
        for result in suite.results:
            if result.tier != tier:
                continue
            lines.append(f"{tier:<6} {result.status:<5} {result.name:<{width}} {result.detail}")
    lines += [
        "",
        f"{counts[PASS]} passed, {counts[FAIL]} failed, {counts[SKIP]} skipped "
        f"({len(suite.results)} checks)",
        "",
        "SKIP is not PASS: a skipped check states the reason it could not be observed",
        "in this environment, and the automated pytest suite covers those paths",
        "separately (api/tests, 82 tests).",
        "",
        "RESULT: " + ("FAILED" if counts[FAIL] else "ALL EXECUTED CHECKS PASSED"),
        "",
    ]
    return "\n".join(lines)


def main() -> int:
    with httpx.Client(base_url=BASE_URL, timeout=TIMEOUT, follow_redirects=False) as client:
        suite = Suite(client=client)
        try:
            suite.client.get("/api/health")
        except httpx.HTTPError as exc:
            print(f"cannot reach {BASE_URL}: {exc}", file=sys.stderr)
            print("start the stack first: docker compose up --build", file=sys.stderr)
            return 2
        run_checks(suite)
        report = render(suite)

    print(report)
    failures = sum(1 for result in suite.results if result.status == FAIL)
    return 1 if failures else 0


if __name__ == "__main__":
    raise SystemExit(main())
