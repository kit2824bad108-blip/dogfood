#!/usr/bin/env python3
"""Manifest-driven acceptance check for Axion.

    python api/scripts/dogfood_check.py .dogfood.toml --out acceptance-report.txt

Every route, role, status expectation, dataset size and even the search probe
comes from the manifest — this script contains no route list of its own — so the
check and the contract cannot drift apart. The manifest is read with the standard
library (`tomllib`), and credentials are fetched from the endpoint the manifest
names, never embedded.

**This is Axion's own check.** No organiser-provided `.dogfood.toml`, `run.py` or
`fixtures.json` was present in this repository or on the machine Axion was built
on. The report this produces says so on its face, rather than letting the
artefact imply a run that never happened.

Nothing here mutates the instance except the closed-event submission probe, and
even that is skipped (not attempted) when the event is open.

Exit code is 0 only if nothing failed. SKIP states the reason it could not be
observed.
"""
from __future__ import annotations

import argparse
import json
import os
import platform
import subprocess
import sys
import time
import tomllib
from dataclasses import dataclass, field
from datetime import datetime, timezone
from pathlib import Path
from typing import Any, Optional
from urllib.parse import quote

try:
    import httpx
except ImportError:  # pragma: no cover - the API venv always has httpx
    print("httpx is required: pip install httpx", file=sys.stderr)
    raise SystemExit(2)

# The report is committed to the repository, so it is written as UTF-8 even on a
# console that defaults to a legacy code page.
if hasattr(sys.stdout, "reconfigure"):
    sys.stdout.reconfigure(encoding="utf-8", errors="replace")

PASS, FAIL, SKIP = "PASS", "FAIL", "SKIP"
REPO_ROOT = Path(__file__).resolve().parents[2]
TIMEOUT = 30.0

GROUP_ORDER = ("T0", "T1", "T2", "AUTH", "DATASET")
GROUP_TITLES = {
    "T0": "T0 — the runtime answers",
    "T1": "T1 — public surface and the deadline",
    "T2": "T2 — judging, exports and the blind gate",
    "AUTH": "AUTH — authenticated boundaries",
    "DATASET": "DATASET — the instance runs the declared dataset",
}


class CheckFailure(Exception):
    """A declared expectation was not met."""


@dataclass
class Observation:
    group: str
    name: str
    status: str
    detail: str
    ms: int


@dataclass
class Checker:
    client: httpx.Client
    manifest: dict[str, Any]
    manifest_path: Path
    # The URL actually used, which AXION_API_URL can override. Reported verbatim
    # so a report can never claim a target it did not talk to.
    base_url: str = ""
    observations: list[Observation] = field(default_factory=list)
    headers: dict[str, dict[str, str]] = field(default_factory=dict)
    probes: dict[tuple[str, bool], tuple[Optional[int], str]] = field(default_factory=dict)
    event_payload: Optional[dict[str, Any]] = None

    # ── plumbing ─────────────────────────────────────────────────────────────

    @property
    def manifest_name(self) -> str:
        try:
            return str(self.manifest_path.relative_to(REPO_ROOT))
        except ValueError:
            return str(self.manifest_path)

    def load_headers(self) -> None:
        """Fetch the checker credentials the manifest points at."""
        source = self.manifest["auth"]["token_source"]
        response = self.client.get(source)
        if response.status_code != 200:
            raise CheckFailure(
                f"{source} returned {response.status_code}; the checker needs development "
                "credentials (set MOCK_GITHUB=true, SEED_DEMO=true or LOCAL_DEV_LOGIN=true)"
            )
        payload = response.json()
        header_name = self.manifest["auth"].get("session_header", "Authorization")
        available = payload.get("roles") or {}
        for role_key, role_name in self.manifest["auth"].get("roles", {}).items():
            record = available.get(role_name)
            if record is None:
                continue
            self.headers[role_key] = {header_name: record["Authorization"]}

    def run(self) -> None:
        try:
            self.load_headers()
        except CheckFailure as exc:
            # Record it and keep going: the authenticated checks below will fail
            # with 401/403, which is the honest observation of a misconfigured run.
            self.observations.append(
                Observation("AUTH", "Checker credentials", FAIL, str(exc), 0)
            )
        for check in self.manifest.get("checks", []):
            started = time.perf_counter()
            try:
                status, detail = self.verify(check)
            except CheckFailure as exc:
                status, detail = FAIL, str(exc)
            except Exception as exc:  # noqa: BLE001 - the report must survive anything
                status, detail = FAIL, f"{type(exc).__name__}: {exc}"
            self.observations.append(
                Observation(
                    check.get("group", "T0"),
                    check.get("name") or check["id"],
                    status,
                    detail,
                    int((time.perf_counter() - started) * 1000),
                )
            )

    # ── one check at a time ──────────────────────────────────────────────────

    def verify(self, check: dict[str, Any]) -> tuple[str, str]:
        path, note = self.resolve_path(check)
        if path is None:
            return SKIP, note

        preflight = self.precheck(check)
        if preflight is not None:
            return preflight

        response = self.send(check, path)
        expected = self.expect(check, response)
        if expected[0] == FAIL:
            return expected
        return self.semantic(check, response) or expected

    def resolve_path(self, check: dict[str, Any]) -> tuple[Optional[str], str]:
        path = check["path"]
        if "{search_term}" in path:
            term = self.manifest["expectations"]["search_term"]
            path = path.replace("{search_term}", quote(str(term)))
        if "{submission_id}" in path:
            submission_id, note = self.probe_submission(
                check.get("role", "public"), require_unscored=check["id"] == "presentation_gate"
            )
            if submission_id is None:
                return None, note
            path = path.replace("{submission_id}", str(submission_id))
        return path, ""

    def probe_submission(self, role: str, *, require_unscored: bool = False) -> tuple[Optional[int], str]:
        """Pick the submission this role should be pointed at, once per run."""
        key = (role, require_unscored)
        if key in self.probes:
            return self.probes[key]

        response = self.client.get("/api/judging/assignments", headers=self.headers.get(role, {}))
        result: tuple[Optional[int], str] = (None, f"the {role} header could not read assignments")
        if response.status_code == 200:
            assignments = response.json().get("assignments") or []
            ground = [a for a in assignments if not a.get("technical_submitted")]
            pool = ground if require_unscored else assignments
            if pool:
                result = (pool[0]["submission_id"], "")
            elif require_unscored:
                result = (
                    None,
                    "every project this judge is assigned already carries their technical "
                    "verdict, so the blind gate cannot be observed without writing one",
                )
            else:
                result = (None, f"the {role} header has no assignments to probe")
        self.probes[key] = result
        return result

    def send(self, check: dict[str, Any], path: str) -> httpx.Response:
        method = str(check.get("method", "GET")).upper()
        headers = self.headers.get(check.get("role", "public"), {})
        body = check.get("body")
        return self.client.request(
            method, path, headers=headers, json=body if method != "GET" else None
        )

    def expect(self, check: dict[str, Any], response: httpx.Response) -> tuple[str, str]:
        """The declared status and content-type contract."""
        summary = f"{response.status_code} from {check['path']}"
        codes = check.get("expect")
        if codes is not None:
            allowed = codes if isinstance(codes, list) else [codes]
            if response.status_code not in allowed:
                return FAIL, f"expected {allowed}, observed {response.status_code}: {self.snippet(response)}"
        klass = check.get("expect_class")
        if klass:
            wanted = {"2xx": 200 <= response.status_code < 300, "4xx": 400 <= response.status_code < 500}
            if not wanted.get(str(klass), False):
                return FAIL, (
                    f"expected a {klass} response, observed {response.status_code}: "
                    f"{self.snippet(response)}"
                )
        content_type = check.get("expect_content_type")
        if content_type and content_type not in response.headers.get("content-type", ""):
            return FAIL, (
                f"expected content-type {content_type}, observed "
                f"{response.headers.get('content-type')!r}"
            )
        return PASS, summary

    def semantic(self, check: dict[str, Any], response: httpx.Response) -> Optional[tuple[str, str]]:
        """Per-check assertions, keyed by the manifest's check id."""
        handler = getattr(self, f"_check_{check['id']}", None)
        return handler(response) if handler else None

    def precheck(self, check: dict[str, Any]) -> Optional[tuple[str, str]]:
        if check["id"] == "submit_closed" and not self.event_is_closed():
            phase = (self.event() or {}).get("event", {}).get("phase")
            return SKIP, (
                f"the instance reports phase {phase!r}, so the closed-event probe was not "
                "attempted: sending it would mutate an open event. Boot the fixture dataset "
                "(SEED_MODE=fixtures) to observe this check."
            )
        return None

    # ── read-through helpers ─────────────────────────────────────────────────

    def event(self) -> Optional[dict[str, Any]]:
        if self.event_payload is None:
            response = self.client.get("/api/event")
            self.event_payload = response.json() if response.status_code == 200 else {}
        return self.event_payload

    def event_is_closed(self) -> bool:
        payload = self.event() or {}
        return bool((payload.get("event") or {}).get("submission_window", {}).get("closed"))

    @staticmethod
    def snippet(response: httpx.Response) -> str:
        try:
            payload = response.json()
            if isinstance(payload, dict) and "detail" in payload:
                return repr(payload["detail"])
            return json.dumps(payload)[:160]
        except Exception:  # noqa: BLE001 - a non-JSON error body is still worth reporting
            return response.text[:160].replace("\n", " ")

    # ── per-check detail ─────────────────────────────────────────────────────

    def _check_health(self, response: httpx.Response) -> tuple[str, str]:
        body = response.json()
        if body.get("status") != "ok":
            raise CheckFailure(f"health reported status {body.get('status')!r}")
        window = body.get("event_window") or {}
        phase = "closed" if window.get("closed") else "open"
        return PASS, (
            f"status ok; event {body.get('event')!r}; window {phase} "
            f"({window.get('opens_at')} → {window.get('closes_at')}); "
            f"dev login {body.get('local_dev_login')}; "
            f"commit integrity from {body.get('commit_integrity_source')}"
        )

    def _check_openapi(self, response: httpx.Response) -> tuple[str, str]:
        body = response.json()
        paths = body.get("paths") or {}
        minimum = int(self.manifest["expectations"]["min_openapi_paths"])
        if len(paths) < minimum:
            raise CheckFailure(f"{len(paths)} paths in the schema, expected at least {minimum}")
        return PASS, (
            f"{len(paths)} documented paths, OpenAPI {body.get('openapi')}, "
            f"title {body.get('info', {}).get('title')!r}"
        )

    def _check_docs(self, response: httpx.Response) -> tuple[str, str]:
        content_type = response.headers.get("content-type", "")
        if "text/html" not in content_type:
            raise CheckFailure(f"expected HTML from the docs route, got {content_type!r}")
        return PASS, (
            f"Swagger UI served at {self.manifest['service']['docs']} "
            f"({len(response.text)} bytes of HTML)"
        )

    def _check_event(self, response: httpx.Response) -> tuple[str, str]:
        body = response.json()
        event = body.get("event") or {}
        tracks = body.get("tracks") or []
        criteria = (body.get("rubric") or {}).get("criteria") or []
        if not event.get("phase"):
            raise CheckFailure("no event phase in the payload")
        if not tracks:
            raise CheckFailure("no tracks configured, so track filtering cannot work")
        weights = ", ".join(
            f"{entry.get('label')} {entry.get('percent')}%" for entry in criteria
        )
        return PASS, (
            f"phase {event['phase']}; window {event.get('starts_at')} → {event.get('ends_at')}; "
            f"{len(tracks)} tracks; rubric {weights}; stats {body.get('stats')}"
        )

    def _check_gallery(self, response: httpx.Response) -> tuple[str, str]:
        body = response.json()
        projects = body.get("projects") or []
        withheld = self.manifest["expectations"]["gallery_withholds"]
        leaked = sorted(
            {field for project in projects for field in withheld if field in project}
        )
        if leaked:
            raise CheckFailure(f"the public gallery exposes {leaked}")
        if not projects:
            raise CheckFailure("the gallery returned no projects")
        return PASS, (
            f"{len(projects)} projects, {len(body.get('tracks') or [])} tracks; "
            f"{withheld} withheld from anonymous callers"
        )

    def _check_gallery_search(self, response: httpx.Response) -> tuple[str, str]:
        body = response.json()
        term = self.manifest["expectations"]["search_term"]
        minimum = int(self.manifest["expectations"]["search_expected_min"])
        count = body.get("count") or 0
        if count < minimum:
            raise CheckFailure(f"q={term!r} matched {count} projects, expected at least {minimum}")
        titles = ", ".join(project["title"] for project in (body.get("projects") or [])[:3])
        return PASS, f"q={term!r} matched {count} projects (≥{minimum} declared): {titles}"

    def _check_dataset(self, response: httpx.Response) -> tuple[str, str]:
        stats = response.json().get("stats") or {}
        declared = self.manifest["dataset"]
        detail = (
            f"declared {declared.get('fixtures')} → observed "
            f"submissions={stats.get('submissions')}, judges={stats.get('judges')}, "
            f"teams={stats.get('teams')}, verdicts={stats.get('verdicts')}, "
            f"assignments={stats.get('assignments')}"
        )
        wanted = {
            "submissions": int(declared["expected_submissions"]),
            "judges": int(declared["expected_judges"]),
        }
        mismatches = [
            f"{key}: observed {stats.get(key)}, declared {value}"
            for key, value in wanted.items()
            if stats.get(key) != value
        ]
        if mismatches:
            raise CheckFailure("dataset mismatch — " + "; ".join(mismatches))
        return PASS, detail

    def _check_submit_closed(self, response: httpx.Response) -> tuple[str, str]:
        return PASS, (
            f"{response.status_code} on a live write attempt while the window is closed: "
            f"{self.snippet(response)}"
        )

    def _check_judge_assignments(self, response: httpx.Response) -> tuple[str, str]:
        body = response.json()
        assignments = body.get("assignments") or []
        progress = body.get("progress") or {}
        rubric = body.get("rubric") or {}
        if not assignments:
            raise CheckFailure("the judge has no assignments")
        return PASS, (
            f"{len(assignments)} assignments; technical {progress.get('technical_done')}/"
            f"{progress.get('total')} filed ({progress.get('percent_technical')}%); "
            f"rubric {rubric.get('name')!r} with {len(rubric.get('criteria') or [])} weighted criteria"
        )

    def _check_judge_technical(self, response: httpx.Response) -> tuple[str, str]:
        body = response.json()
        submission = body.get("submission") or {}
        raw = json.dumps(body)
        leaked = [field for field in ("demo_url", "video_url") if f'"{field}"' in raw]
        if leaked:
            raise CheckFailure(
                f"the technical view exposes {leaked} before the judge filed a technical verdict"
            )
        unlocked = submission.get("presentation_unlocked")
        scored = bool((submission.get("score") or {}).get("technical_score"))
        if bool(unlocked) != scored:
            raise CheckFailure(
                f"presentation_unlocked={unlocked} while a technical score is "
                f"{'present' if scored else 'absent'}"
            )
        return PASS, (
            f"{submission.get('title')!r}: repo and docs visible, demo/video links absent "
            f"from the payload, presentation_unlocked={unlocked}"
        )

    def _check_presentation_gate(self, response: httpx.Response) -> tuple[str, str]:
        return PASS, (
            f"{response.status_code} before this judge filed a technical verdict: "
            f"{self.snippet(response)} — enforced by the API, not by the browser"
        )

    def _check_leaderboard(self, response: httpx.Response) -> tuple[str, str]:
        body = response.json()
        rows = body.get("leaderboard") or []
        if not rows:
            raise CheckFailure("the leaderboard is empty")
        ranks = [row.get("axion_rank") for row in rows]
        if ranks != list(range(1, len(rows) + 1)):
            raise CheckFailure(f"ranks are not sequential: {ranks[:8]}…")
        leader = rows[0]
        return PASS, (
            f"{len(rows)} ranked projects; leader {leader.get('title')!r} at "
            f"{leader.get('axion_score')} (raw average {leader.get('raw_average')}, "
            f"movement {leader.get('rank_movement')}); {body.get('verdict_count')} technical "
            f"verdicts; {len(body.get('coverage_warnings') or {})} coverage warnings"
        )

    def _csv_summary(self, response: httpx.Response) -> str:
        content_type = response.headers.get("content-type", "")
        if "text/csv" not in content_type:
            raise CheckFailure(f"expected text/csv, got {content_type!r}")
        lines = [line for line in response.text.splitlines() if line.strip()]
        if len(lines) < 2:
            raise CheckFailure(
                f"the export has {len(lines)} non-empty lines; expected a header and at least one row"
            )
        columns = lines[0].split(",")
        return (
            f"{len(lines) - 1} data rows, {len(columns)} columns: "
            f"{', '.join(columns[:6])}…"
        )

    def _check_export_leaderboard(self, response: httpx.Response) -> tuple[str, str]:
        return PASS, self._csv_summary(response)

    def _check_export_scores(self, response: httpx.Response) -> tuple[str, str]:
        return PASS, self._csv_summary(response)

    def _check_export_progress(self, response: httpx.Response) -> tuple[str, str]:
        return PASS, self._csv_summary(response)

    def _check_session_identity(self, response: httpx.Response) -> tuple[str, str]:
        body = response.json()
        user = body.get("user") if isinstance(body.get("user"), dict) else body
        if user.get("role") != "admin":
            raise CheckFailure(f"the header authenticated as role {user.get('role')!r}")
        return PASS, (
            f"a header alone authenticates as {user.get('email')} "
            f"({user.get('role')}), with no login round-trip"
        )

    def _check_participant_forbidden(self, response: httpx.Response) -> tuple[str, str]:
        return PASS, f"{response.status_code} for a participant on an organizer-only route"

    def _check_anonymous_forbidden(self, response: httpx.Response) -> tuple[str, str]:
        return PASS, f"{response.status_code} with no credentials at all"


def git_sha() -> str:
    try:
        completed = subprocess.run(
            ["git", "rev-parse", "--short", "HEAD"],
            cwd=REPO_ROOT,
            capture_output=True,
            text=True,
            timeout=10,
        )
        return completed.stdout.strip() or "unknown"
    except Exception:  # noqa: BLE001 - a report without a SHA still beats no report
        return "unknown"


def render(checker: Checker) -> str:
    manifest = checker.manifest
    service = manifest.get("service", {})
    dataset = manifest.get("dataset", {})
    auth = manifest.get("auth", {})
    counts = {PASS: 0, FAIL: 0, SKIP: 0}
    for observation in checker.observations:
        counts[observation.status] += 1

    lines: list[str] = [
        "AXION SELF-CHECK REPORT (manifest-driven)",
        "=" * 78,
        "",
        "This report was produced by Axion's own manifest-driven checker",
        f"(api/scripts/dogfood_check.py) reading {checker.manifest_name}.",
        "",
        manifest.get("checker", {}).get("disclosure", ""),
        "",
        "No organiser-provided `.dogfood.toml`, `run.py` or `fixtures.json` was present in",
        "this repository or on the machine Axion was built on, so this is not a run of an",
        "organiser's acceptance suite. Every line below is an observation of a running",
        "instance, made through the routes the manifest declares.",
        "",
        f"Generated     : {datetime.now(timezone.utc).isoformat()}",
        f"Target        : {checker.base_url or service.get('base_url')}",
        f"Manifest      : {checker.manifest_name} "
        f"({manifest.get('checker', {}).get('name')} v{manifest.get('checker', {}).get('manifest_version')})",
        f"Dataset       : {dataset.get('fixtures')} — declared "
        f"{dataset.get('expected_submissions')} submissions, {dataset.get('expected_judges')} judges",
        f"Auth          : {auth.get('mode')} mode, credentials from {auth.get('token_source')}",
        f"Git commit    : {git_sha()}",
        f"Python        : {platform.python_version()} on {platform.system()}",
        f"Checks        : {len(checker.observations)} total — "
        f"{counts[PASS]} passed, {counts[FAIL]} failed, {counts[SKIP]} skipped",
        "",
        "The only write this check ever attempts is the closed-event submission probe,",
        "and it is skipped rather than attempted when the event is open, so a read-only",
        "run cannot mutate the instance it is measuring.",
        "",
    ]

    for group in GROUP_ORDER:
        group_results = [o for o in checker.observations if o.group == group]
        if not group_results:
            continue
        lines += ["-" * 78, GROUP_TITLES[group], "-" * 78, ""]
        for observation in group_results:
            lines.append(f"[{observation.status}] {observation.name}  ({observation.ms} ms)")
            lines.append(f"       {observation.detail}")
        lines.append("")

    lines += ["=" * 78, "SUMMARY", "=" * 78, ""]
    width = max((len(o.name) for o in checker.observations), default=0) + 2
    for group in GROUP_ORDER:
        for observation in checker.observations:
            if observation.group != group:
                continue
            lines.append(
                f"{group:<8} {observation.status:<5} {observation.name:<{width}} {observation.detail}"
            )
    lines += [
        "",
        f"{counts[PASS]} passed, {counts[FAIL]} failed, {counts[SKIP]} skipped "
        f"({len(checker.observations)} checks)",
        "",
        "SKIP is not PASS: a skipped check states why it could not be observed. The",
        "automated pytest suite covers the same paths in-process (api/tests).",
        "",
        "RESULT: " + ("FAILED" if counts[FAIL] else "ALL EXECUTED CHECKS PASSED"),
        "",
    ]
    return "\n".join(lines)


def load_manifest(path: Path) -> dict[str, Any]:
    with path.open("rb") as handle:
        return tomllib.load(handle)


def main(argv: Optional[list[str]] = None) -> int:
    parser = argparse.ArgumentParser(
        description="Manifest-driven acceptance check (see .dogfood.toml)."
    )
    parser.add_argument(
        "manifest",
        nargs="?",
        default=".dogfood.toml",
        help="path to the manifest (default: .dogfood.toml, relative to the repository root)",
    )
    parser.add_argument(
        "--out",
        help="also write the report to this file as UTF-8 (it is printed to stdout regardless)",
    )
    args = parser.parse_args(argv)

    manifest_path = Path(args.manifest)
    if not manifest_path.is_absolute() and not manifest_path.exists():
        manifest_path = REPO_ROOT / manifest_path
    if not manifest_path.exists():
        print(f"manifest not found: {manifest_path}", file=sys.stderr)
        return 2
    manifest = load_manifest(manifest_path)

    base_url = (os.environ.get("AXION_API_URL") or manifest["service"]["base_url"]).rstrip("/")
    with httpx.Client(base_url=base_url, timeout=TIMEOUT, follow_redirects=False) as client:
        checker = Checker(
            client=client, manifest=manifest, manifest_path=manifest_path, base_url=base_url
        )
        try:
            client.get(manifest["service"]["health"])
        except httpx.HTTPError as exc:
            print(f"cannot reach {base_url}: {exc}", file=sys.stderr)
            print("start the stack first: docker compose up --build", file=sys.stderr)
            return 2
        checker.run()
        report = render(checker)

    print(report)

    if args.out:
        out_path = Path(args.out)
        if not out_path.is_absolute:
            out_path = REPO_ROOT / out_path
        out_path.write_text(report + "\n", encoding="utf-8")
        print(f"[axion] report written to {out_path}", file=sys.stderr)

    failures = sum(1 for observation in checker.observations if observation.status == FAIL)
    return 1 if failures else 0


if __name__ == "__main__":
    raise SystemExit(main())
