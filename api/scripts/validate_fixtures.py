"""Validate a fixture file: ids, references, statuses, timestamps, scores.

    python api/scripts/validate_fixtures.py [path-to-fixture.json]
    python api/scripts/validate_fixtures.py fixtures.json --json
    python api/scripts/validate_fixtures.py fixtures.json --out validation.txt

Deterministic by construction: no database, no network, and no clock in the
report. The fixture's *declared* window is printed; whether that window has
passed depends on the current time and is enforced by the server, so this tool
does not pretend to decide it.

Exit codes:
    0  the fixture is structurally valid
    1  it contains invalid records (each one is listed)
    2  it could not be read or parsed at all

The rules are the ones the importer itself enforces before it writes anything
(`app/fixtures.py`: `validate()` and `diagnose()`), so a fixture that passes
here is one the importer accepts whole rather than refuses. Invalid records are
never silently discarded: the import stops, and this tool names every one.
"""
from __future__ import annotations

import argparse
import hashlib
import json
import sys
from pathlib import Path
from typing import Any, Optional

REPO_ROOT = Path(__file__).resolve().parents[2]
# Import the same module the importer uses, so the two can never disagree.
sys.path.insert(0, str(REPO_ROOT / "api"))

from app import fixtures as fixtures_module  # noqa: E402


def content_digest(path: Path) -> str:
    return hashlib.sha256(path.read_bytes()).hexdigest()[:12]


def _edge_case_lines(diagnostics: dict[str, Any]) -> list[str]:
    lines: list[str] = []
    windows = diagnostics["event_window"]
    lines.append(
        f"Declared window: {windows.get('starts_at')} -> {windows.get('ends_at')} "
        "(the server enforces it against the clock; this tool does not guess)"
    )
    if diagnostics["zero_variance_judges"]:
        lines.append(
            "Zero-variance judges: " + ", ".join(diagnostics["zero_variance_judges"])
        )
    if diagnostics["single_verdict_judges"]:
        lines.append(
            "Single-verdict judges: " + ", ".join(diagnostics["single_verdict_judges"])
        )
    for candidate in diagnostics["duplicate_candidates"]:
        lines.append(
            f"Duplicate candidate: {candidate['submission']} -> {candidate['duplicate_of']} "
            f"({candidate['reason']})"
        )
    if diagnostics["projects_without_reviews"]:
        lines.append(
            "Projects with no verdicts: "
            + ", ".join(diagnostics["projects_without_reviews"])
        )
    if diagnostics["incomplete_batches"]:
        batches = ", ".join(
            f"{row['project']} {row['reviews']}/{row['expected']}"
            for row in diagnostics["incomplete_batches"]
        )
        lines.append(f"Incomplete review batches: {batches}")
    lines.append(
        f"Assignments without verdicts: {diagnostics['assignments_without_scores']}"
    )
    lines.append(
        "Nulls preserved: "
        f"{diagnostics['null_technical_comments']} technical comments, "
        f"{diagnostics['null_summaries']} summaries"
    )
    lines.append(f"String ids preserved: {diagnostics['string_ids_preserved']}")
    return lines


def render_text(path: Path, digest: str, diagnostics: dict[str, Any]) -> str:
    records = diagnostics["records"]
    problems = diagnostics["invalid"]
    lines = [
        "AXION FIXTURE VALIDATION",
        "=" * 78,
        "",
        "These are the rules the importer enforces before it writes anything: an",
        "invalid fixture is refused whole rather than half-imported, and nothing is",
        "silently discarded. This run reads the file only - no database, no network,",
        "and no clock in the report.",
        "",
        f"File        : {path}",
        f"SHA-256[:12]: {digest}",
        f"Fixture     : version {diagnostics.get('fixture_version')} "
        f"(source: {diagnostics.get('source')})",
        f"Records     : {records['projects']} projects, {records['judges']} judges, "
        f"{records['teams']} teams, {records['participants']} participants, "
        f"{records['reviews']} reviews, {records['assignments']} assignments",
        "",
        "Diagnostics:",
    ]
    for line in diagnostics["headline"]:
        lines.append(f"  - {line}")
    for line in _edge_case_lines(diagnostics):
        lines.append(f"  - {line}")
    lines += ["", "Structural problems:"]
    if problems:
        for problem in problems:
            lines.append(
                f"  [FAIL] {problem['kind']:<18} {problem['ref']:<24} {problem['detail']}"
            )
    else:
        lines.append("  none - every record is structurally valid")
    lines += ["", "RESULT: " + ("INVALID" if problems else "VALID"), ""]
    return "\n".join(lines)


def render_json(path: Path, digest: str, diagnostics: dict[str, Any]) -> str:
    windows = diagnostics["event_window"]
    payload = {
        "file": str(path),
        "sha256_12": digest,
        "fixture_version": diagnostics.get("fixture_version"),
        "source": diagnostics.get("source"),
        "records": diagnostics["records"],
        "declared_window": {
            "starts_at": windows.get("starts_at"),
            "ends_at": windows.get("ends_at"),
        },
        "headline": diagnostics["headline"],
        "problems": diagnostics["invalid"],
        "zero_variance_judges": diagnostics["zero_variance_judges"],
        "single_verdict_judges": diagnostics["single_verdict_judges"],
        "duplicate_candidates": diagnostics["duplicate_candidates"],
        "projects_without_reviews": diagnostics["projects_without_reviews"],
        "assignments_without_scores": diagnostics["assignments_without_scores"],
        "valid": not diagnostics["invalid"],
    }
    return json.dumps(payload, indent=2, sort_keys=True)


def main(argv: Optional[list[str]] = None) -> int:
    parser = argparse.ArgumentParser(
        description="Validate an Axion fixture file (no database required)."
    )
    parser.add_argument(
        "path",
        nargs="?",
        default="fixtures.json",
        help="path to the fixture (default: fixtures.json at the repository root)",
    )
    parser.add_argument(
        "--json", action="store_true", help="print the report as JSON instead of text"
    )
    parser.add_argument(
        "--out",
        help="also write the report to this file as UTF-8 (it is printed regardless)",
    )
    args = parser.parse_args(argv)

    path = Path(args.path)
    if not path.is_absolute() and not path.exists():
        path = REPO_ROOT / path
    if not path.exists():
        print(f"fixture not found: {path}", file=sys.stderr)
        return 2
    try:
        payload = fixtures_module.load_fixture(path)
    except ValueError as exc:
        print(f"cannot read fixture: {exc}", file=sys.stderr)
        return 2

    diagnostics = fixtures_module.diagnose(payload)
    digest = content_digest(path)
    report = (
        render_json(path, digest, diagnostics)
        if args.json
        else render_text(path, digest, diagnostics)
    )

    print(report)
    if args.out:
        out_path = Path(args.out)
        if not out_path.is_absolute():
            out_path = REPO_ROOT / out_path
        out_path.write_text(report + "\n", encoding="utf-8")
        print(f"[axion] report written to {out_path}", file=sys.stderr)

    return 1 if diagnostics["invalid"] else 0


if __name__ == "__main__":
    raise SystemExit(main())
