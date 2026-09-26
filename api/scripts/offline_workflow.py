"""Exercise the write path on an offline instance.

Runs inside the offline compose network (see docker-compose.offline.yml):

    docker compose -f docker-compose.yml -f docker-compose.offline.yml run --rm workflow

Against a demo-mode stack (open window, mock commit integrity) it walks the
whole participant-to-organiser path using nothing but the API on the internal
network: register -> team -> submission -> gallery -> judge assignment ->
technical verdict -> leaderboard -> CSV export. Every step prints what it
observed; exit 0 only if all of them held.
"""
from __future__ import annotations

import os
import sys

import httpx

BASE = os.environ.get("AXION_API_URL", "http://api:8000").rstrip("/")
PROJECT_TITLE = "Offline Workflow Project"
REPO_URL = "https://github.com/axion-offline/workflow"
PARTICIPANT = {
    "email": "offline.workflow@axion.test",
    "password": "offline-workflow-1",
    "name": "Offline Workflow",
}


def main() -> int:
    failures: list[str] = []

    def check(label: str, condition: bool, detail: str) -> None:
        print(f"[{'PASS' if condition else 'FAIL'}] {label}: {detail}")
        if not condition:
            failures.append(label)

    submission_id: int | None = None

    with httpx.Client(base_url=BASE, timeout=30.0) as client:
        # The public gallery answers without credentials.
        gallery = client.get("/api/gallery")
        check("gallery", gallery.status_code == 200, f"HTTP {gallery.status_code}")

        # A fresh participant registers with email + password (no GitHub), or
        # signs back in on a re-run.
        registered = client.post("/api/auth/register", json=PARTICIPANT)
        check(
            "register",
            registered.status_code in (200, 409),
            f"HTTP {registered.status_code}",
        )
        if registered.status_code == 409:
            login = client.post(
                "/api/auth/login",
                json={"email": PARTICIPANT["email"], "password": PARTICIPANT["password"]},
            )
            check("login", login.status_code == 200, f"HTTP {login.status_code}")

        # Team and submission. 400 for the team means this run is a re-run and
        # the participant already has one; the submission upsert below is still
        # the same write path.
        team = client.post("/api/teams", json={"name": "Offline Workflow Team"})
        check("team", team.status_code in (200, 400), f"HTTP {team.status_code}")

        submission = client.post(
            "/api/submissions",
            json={"title": PROJECT_TITLE, "repo_url": REPO_URL, "status": "submitted"},
        )
        if submission.status_code == 200:
            submission_id = (submission.json().get("submission") or {}).get("id")
        check(
            "submission",
            submission.status_code == 200 and submission_id is not None,
            f"HTTP {submission.status_code}, id {submission_id}",
        )

        found = client.get("/api/gallery", params={"q": "Offline Workflow"})
        check(
            "gallery_after_submit",
            found.status_code == 200 and PROJECT_TITLE in found.text,
            f"HTTP {found.status_code}, listed: {PROJECT_TITLE in found.text}",
        )

    if submission_id is None:
        print("RESULT: FAILED - no submission id, the judge steps cannot run")
        return 1

    with httpx.Client(base_url=BASE, timeout=30.0) as judge:
        dev = judge.post("/api/auth/dev-login", json={"role": "judge"})
        check("judge_login", dev.status_code == 200, f"HTTP {dev.status_code}")

        assignments = judge.get("/api/judging/assignments")
        rows = (
            assignments.json().get("assignments") or []
            if assignments.status_code == 200
            else []
        )
        assigned = any(row.get("submission_id") == submission_id for row in rows)
        check(
            "judge_assignment",
            assignments.status_code == 200 and assigned,
            f"{len(rows)} assignments, ours present: {assigned}",
        )

        scored = judge.post(
            "/api/judging/scores",
            json={
                "submission_id": submission_id,
                "technical_score": 7,
                "technical_comment": "Offline workflow verdict",
            },
        )
        check("verdict", scored.status_code == 200, f"HTTP {scored.status_code}")

    with httpx.Client(base_url=BASE, timeout=30.0) as admin:
        dev = admin.post("/api/auth/dev-login", json={"role": "admin"})
        check("admin_login", dev.status_code == 200, f"HTTP {dev.status_code}")

        leaderboard = admin.get("/api/admin/leaderboard")
        check("leaderboard", leaderboard.status_code == 200, f"HTTP {leaderboard.status_code}")

        export = admin.get("/api/admin/export/scores.csv")
        body = export.text if export.status_code == 200 else ""
        check(
            "export_csv",
            export.status_code == 200 and PROJECT_TITLE in body,
            f"HTTP {export.status_code}, our verdict in the export: {PROJECT_TITLE in body}",
        )

    if failures:
        print(f"RESULT: FAILED - {', '.join(failures)}")
        return 1
    print("RESULT: OFFLINE WORKFLOW PASSED")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
