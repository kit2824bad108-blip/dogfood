#!/usr/bin/env python3
"""Generate the fixture dataset (`fixtures.json`) deterministically.

The dataset is deliberately messy, because that is the point: it carries the
cases a real event produces and a curated demo does not.

  * **Incomplete review batches** — one project has three assignments and no
    verdict at all, and projects carry 1, 2, 4 and 5 verdicts while the declared
    target is 3.
  * **A zero-variance judge** (every verdict is an 8) and a **single-verdict
    judge**, the two degenerate cases the Z-score model guards against.
  * **Two duplicate submissions**, declared explicitly in the file with
    `duplicate_of`, so the importer's own detector can be checked against ground
    truth instead of being taken on trust. A third project is a near-miss (same
    word in the title, different repository) and must *not* be flagged.
  * **Null comments** and **null project summaries**, so a renderer that assumes
    a string breaks loudly here rather than in front of a judge.
  * **A closed event window**, so deadline enforcement is exercised rather than
    described.
  * **String identifiers** throughout (`proj_007`, not `7`), so the importer has
    to preserve a source-ID mapping instead of assuming integer keys, and
    **ISO-8601 UTC timestamps** with explicit offsets.
  * **Cross-cluster judge overlap**, so calibration is possible in the presence
    of incomplete coverage.

Forty projects need forty teams: Axion allows a team exactly one submission, and
the import of the first draft of this file failed its unique constraint until the
dataset respected that.

Nothing here is an organiser-provided fixture set: no such file was available
when Axion was built. This is Axion's own, and `fixtures.json` says so.

Run:

    python api/scripts/build_fixtures.py
"""
from __future__ import annotations

import json
import random
from datetime import datetime, timedelta, timezone
from pathlib import Path

REPO_ROOT = Path(__file__).resolve().parents[2]
OUTPUT = REPO_ROOT / "fixtures.json"

RNG_SEED = 20260801
TARGET_REVIEWS_PER_PROJECT = 3
MIN_COVERAGE = 3  # what an organiser would consider "enough verdicts"

EVENT = {
    "name": "Axion Fixture Hackathon",
    "starts_at": "2026-08-01T00:00:00+00:00",
    "ends_at": "2026-08-04T00:00:00+00:00",
    "closes_for_submissions_at": "2026-08-04T00:00:00+00:00",
}

RUBRIC = {
    "name": "Fixture technical rubric",
    "criteria": [
        {"key": "innovation", "label": "Innovation", "weight": 30.0},
        {"key": "code_quality", "label": "Code Quality", "weight": 70.0},
    ],
}

TRACKS = [
    {
        "id": "track_ai",
        "name": "AI & Machine Learning",
        "slug": "ai-ml",
        "prize_pool": "$10,000",
        "prizes": [
            {"id": "prize_ai_1", "rank": 1, "title": "Best AI project"},
            {"id": "prize_ai_2", "rank": 2, "title": "Runner-up, AI"},
        ],
    },
    {
        "id": "track_web3",
        "name": "Web3 & Trust",
        "slug": "web3",
        "prize_pool": "$10,000",
        "prizes": [
            {"id": "prize_web3_1", "rank": 1, "title": "Best Web3 project"},
            {"id": "prize_web3_2", "rank": 2, "title": "Runner-up, Web3"},
        ],
    },
    {
        "id": "track_devtools",
        "name": "Developer Tools",
        "slug": "devtools",
        "prize_pool": "$5,000",
        "prizes": [{"id": "prize_devtools_1", "rank": 1, "title": "Best developer tool"}],
    },
    {
        "id": "track_climate",
        "name": "Climate & Energy",
        "slug": "climate",
        "prize_pool": "$5,000",
        "prizes": [{"id": "prize_climate_1", "rank": 1, "title": "Best climate project"}],
    },
]

# name, mean, sigma, note. judge_11 and judge_12 are the degenerate cases.
JUDGES = [
    ("judge_01", "Dana Disciplined", 4.5, 0.60, "narrow band, low"),
    ("judge_02", "Enzo Expansive", 7.5, 3.40, "wide band"),
    ("judge_03", "Ava Balanced", 6.5, 1.00, "balanced"),
    ("judge_04", "Ben Balanced", 6.0, 1.10, "balanced"),
    ("judge_05", "Cleo Balanced", 7.0, 0.90, "balanced"),
    ("judge_06", "Devon Steady", 5.5, 1.30, "balanced"),
    ("judge_07", "Farah Steady", 6.2, 1.20, "balanced"),
    ("judge_08", "Gus Broad", 5.8, 2.00, "wide band"),
    ("judge_09", "Hana Focused", 6.8, 0.80, "narrow band, high"),
    ("judge_10", "Ivan Moderate", 5.2, 1.40, "balanced"),
    ("judge_11", "Jo Flat", 8.0, 0.00, "ZERO VARIANCE: every verdict is 8"),
    ("judge_12", "Kim Once", 6.0, 1.00, "SINGLE VERDICT: exactly one review"),
]

# One team per project: Axion allows a team exactly one submission, because a team
# entering two projects would double-dip on the same judges and the same prize.
# The fixture therefore needs 40 teams for its 40 projects, not 12 with a pile of
# projects each — the first version of this file tried that and the import hit the
# unique constraint, which is the constraint doing its job.
TEAM_NAMES = [
    "Null Pointer Exception",
    "Compile & Conquer",
    "Rubber Duck Squad",
    "Merge Conflict",
    "Nebula Labs",
    "Off By One",
    "Semantic Drift",
    "Race Condition",
    "Orbit Works",
    "Undefined Behavior",
    "Big O Energy",
    "Stack Overflow Cartel",
]
_TEAM_ADJECTIVES = [
    "Atomic", "Lazy", "Greedy", "Immutable", "Idempotent", "Recursive", "Silent",
    "Elastic", "Sparse", "Dense", "Polymorphic", "Reentrant", "Concurrent", "Stateless",
    "Zero-Copy",
]
_TEAM_NOUNS = [
    "Lattice", "Kernel", "Quasar", "Runtime", "Scheduler", "Pipeline", "Buffer", "Heap",
    "Gardener", "Lighthouse", "Monsoon", "Cascade", "Tessellate",
]
TEAMS: list[tuple[str, str]] = [
    (f"team_{index + 1:02d}", name) for index, name in enumerate(TEAM_NAMES)
]
for _index, _ in enumerate(range(40 - len(TEAMS))):
    _adjective = _TEAM_ADJECTIVES[_index % len(_TEAM_ADJECTIVES)]
    _noun = _TEAM_NOUNS[(_index // len(_TEAM_ADJECTIVES)) % len(_TEAM_NOUNS)]
    TEAMS.append((f"team_{len(TEAMS) + 1:02d}", f"{_adjective} {_noun}"))

TITLES = [
    "Quiet Craft",
    "Flashy Demo",
    "Model Router",
    "Federated Notes",
    "Query Planner",
    "CRDT Whiteboard",
    "Mesh Relay - resilient chat",
    "Edge Cache Optimizer",
    "Federated Scheduler",
    "Realtime Auction",
    "Distributed Ledger UI",
    "Delta Compressor",
    "Streaming Notebooks",
    "Zero-Knowledge Vault",
    "Consensus Playground",
    "Semantic Search Cache",
    "Nimbus Mesh",
    "Incremental Compiler",
    "Policy Engine",
    "Trace Collector",
    "Vector Index Lab",
    "Modal Editor",
    "Serverless Queue",
    "Deterministic Replay",
    "Mesh Scheduler",
    "Null-Safe Migrator",
    "Thermal Budget Planner",
    "Algae Bloom Watcher",
    "Grid Load Forecaster",
    "Carbon Ledger",
    "Mesh Subnet Simulator",
    "Signal Router",
    "Anomaly Triage",
    "Gradient Playground",
    "Latency Budgeter",
    "Wire Protocol Fuzzer",
    "Contract Diff Tool",
    "Reproducible Builds",
    "Compliance Mapper",
    "Archive Verifier",
]

# Two declared duplicate pairs, plus a near-miss ("Mesh Scheduler") that must NOT
# be flagged: it shares a word with "Mesh Relay" and nothing else.
#
# Both the duplicate and its canonical project are rewritten here, so the pair is
# a real pair — the same repository under a different name — rather than two
# projects that merely have similar titles in a comment.
DUPLICATE_PAIRS = [
    {
        "duplicate": "proj_021",
        "canonical": "proj_017",
        "duplicate_title": "Nimbus Mesh (final v2)",
        "duplicate_repo": "https://github.com/Nebula-Labs/Nimbus-Mesh.git",
        "canonical_title": "Nimbus Mesh",
        "canonical_repo": "https://github.com/nebula-labs/nimbus-mesh",
        "reason": "same repository with different casing and a .git suffix",
    },
    {
        "duplicate": "proj_031",
        "canonical": "proj_007",
        "duplicate_title": "Mesh Relay",
        "duplicate_repo": "https://github.com/Orbit-Works/Mesh-Relay/",
        "canonical_title": "Mesh Relay - resilient chat",
        "canonical_repo": "https://github.com/orbit-works/mesh-relay",
        "reason": "same repository with a trailing slash and different casing",
    },
]

# One in four is null: enough to break a renderer that assumes a string, not so
# many that the gallery looks broken.
SUMMARIES = [
    "{title}: a fixture submission with a full description of the work.",
    "{title}: built during the fixture event window.",
    None,
    "{title}: an accurate description of the project and its trade-offs.",
]

# The two review clusters. judge_11 (zero variance) and judge_12 (single verdict)
# are deliberately NOT in either pool: they are attached to specific projects
# below, so their degenerate shapes stay exactly as documented.
CLUSTER_A = ["judge_01", "judge_02", "judge_03", "judge_04", "judge_05", "judge_06"]
CLUSTER_B = ["judge_07", "judge_08", "judge_09", "judge_10"]


def _names(rng: random.Random, count: int) -> list[str]:
    first = [
        "Ada", "Linus", "Grace", "Alan", "Barbara", "Dennis", "Ken", "Margaret", "Guido",
        "Anita", "Yuki", "Omar", "Priya", "Diego", "Lena", "Tariq", "Noor", "Hana",
        "Sven", "Mei", "Ines", "Pavel", "Rina", "Marcus", "Sofia", "Hugo", "Leila",
        "Nadia", "Tomas", "Wei", "Zara", "Idris", "Kaito", "Marta", "Nils", "Ola",
        "Ravi", "Sara", "Theo", "Uma",
    ]
    last = [
        "Lovelace", "Torvalds", "Hopper", "Turing", "Liskov", "Ritchie", "Thompson",
        "Hamilton", "Rossum", "Borg", "Tanaka", "Haddad", "Nair", "Alvarez", "Novak",
        "Rahman", "Kwon", "Sato", "Andersson", "Chen", "Duarte", "Eriksen", "Fischer",
        "Gallo", "Haruna", "Ibarra", "Jensen", "Kovac", "Lindgren", "Moreau",
        "Nakamura", "Okonkwo", "Petrov", "Quintero", "Rinaldi", "Silva", "Takeda",
        "Ueda", "Vargas", "Weber",
    ]
    return [f"{rng.choice(first)} {rng.choice(last)}" for _ in range(count)]


def _judge_pool(index: int) -> list[str]:
    """Which judges review project `index` (1-based), before the edge cases."""
    pool = CLUSTER_A if index <= 20 else CLUSTER_B
    offset = (index * 2) % len(pool)
    chosen = [pool[(offset + step) % len(pool)] for step in range(TARGET_REVIEWS_PER_PROJECT)]
    # The overlap zone is reviewed across clusters, so the calibration graph is
    # connected even though neither cluster alone sees every project.
    if 17 <= index <= 20:
        chosen.append(["judge_09", "judge_10"][index % 2])
    elif 21 <= index <= 24:
        chosen.append(["judge_03", "judge_04"][index % 2])
    return chosen


def build() -> dict:
    rng = random.Random(RNG_SEED)
    profile_by_id = {judge[0]: judge for judge in JUDGES}

    # ── people ──────────────────────────────────────────────────────────────
    # 48 people over 40 teams: every team has a member, and the first eight have
    # two, so multi-member team handling is exercised as well.
    names = _names(rng, 48)
    participants = [
        {
            "id": f"user_{index + 101:04d}",
            "email": f"hacker{index + 1:02d}@fixtures.axion.dev",
            "name": name,
            "role": "participant",
            "team_id": TEAMS[index % len(TEAMS)][0],
        }
        for index, name in enumerate(names)
    ]

    judges = [
        {
            "id": judge_id,
            "email": f"{judge_id}@fixtures.axion.dev",
            "name": name,
            "role": "judge",
            "profile": note,
            "mean": mean,
            "sigma": sigma,
        }
        for judge_id, name, mean, sigma, note in JUDGES
    ]

    # ── projects ────────────────────────────────────────────────────────────
    duplicate_of = {pair["duplicate"]: pair["canonical"] for pair in DUPLICATE_PAIRS}
    duplicate_repo = {
        pair["duplicate"]: pair["duplicate_repo"] for pair in DUPLICATE_PAIRS
    }
    duplicate_repo.update(
        {pair["canonical"]: pair["canonical_repo"] for pair in DUPLICATE_PAIRS}
    )
    declared_titles = {
        pair["duplicate"]: pair["duplicate_title"] for pair in DUPLICATE_PAIRS
    }
    declared_titles.update(
        {pair["canonical"]: pair["canonical_title"] for pair in DUPLICATE_PAIRS}
    )
    duplicate_kind = {pair["duplicate"]: pair["reason"] for pair in DUPLICATE_PAIRS}

    projects = []
    for index, title in enumerate(TITLES):
        project_id = f"proj_{index + 1:03d}"
        title = declared_titles.get(project_id, title)
        # One project per team, one team per project.
        team_id = TEAMS[index][0]
        slug = (
            title.lower()
            .replace(" - ", "-")
            .replace(" ", "-")
            .replace(",", "")
            .replace("&", "and")
            .replace("(", "")
            .replace(")", "")
        )
        repo_url = f"https://github.com/fixture-labs/{slug}"
        if project_id in duplicate_repo:
            repo_url = duplicate_repo[project_id]
        summary_template = SUMMARIES[index % len(SUMMARIES)]
        project = {
            "id": project_id,
            "team_id": team_id,
            "title": title,
            "repo_url": repo_url,
            "docs_url": f"{repo_url.rstrip('/')}#readme" if index % 4 else None,
            "demo_url": f"https://{slug}.fixtures.axion.dev" if index % 5 else None,
            "video_url": f"https://youtu.be/fixture-{index + 1:03d}" if index % 3 else None,
            "summary": summary_template.format(title=title) if summary_template else None,
            "track_id": TRACKS[index % len(TRACKS)]["id"],
            "status": "submitted",
            "submitted_at": (
                datetime(2026, 8, 1, tzinfo=timezone.utc)
                + timedelta(hours=6 * index + 3)
            ).isoformat(),
            "commit_integrity": {
                "pct_in_window": round(100 - (index * 7.3) % 100, 1),
                "flagged": index in {13, 34},
            },
            "assigned_judges": [],
            "declared_duplicate_of": duplicate_of.get(project_id),
            "duplicate_note": duplicate_kind.get(project_id),
        }
        if project["declared_duplicate_of"]:
            project["declared_duplicate_reason"] = duplicate_kind[project_id]
        projects.append(project)

    # ── assignments and reviews ─────────────────────────────────────────────
    reviews = []
    for index, project in enumerate(projects, start=1):
        assigned = _judge_pool(index)
        expected_reviews = len(assigned)

        if index == 5:  # one judge, one verdict: the thinnest possible coverage
            assigned = assigned[:1]
        elif index == 11:  # heavily reviewed, for contrast
            assigned = assigned + [judge[0] for judge in JUDGES[4:6]]
        elif index == 18:  # assigned, never reviewed: the incomplete batch
            expected_reviews = 0
        elif index == 27:
            assigned = assigned[:2]
            expected_reviews = 1
        elif index == 33:
            expected_reviews = 2
        elif index == 40:
            expected_reviews = 2

        # The zero-variance judge deliberately covers a spread of projects.
        if index <= 6 or 21 <= index <= 26:
            assigned = assigned + ["judge_11"]
            if index not in {5}:
                expected_reviews = min(expected_reviews + 1, len(assigned))

        # The single-verdict judge reviews exactly one project.
        if index == 1:
            assigned = assigned + ["judge_12"]
            expected_reviews = len(assigned)

        project["assigned_judges"] = sorted(set(assigned))
        project["declared_review_target"] = TARGET_REVIEWS_PER_PROJECT

        for position, judge_id in enumerate(project["assigned_judges"]):
            if position >= expected_reviews:
                continue  # an assignment with no verdict: a missing score, not a zero
            _, _, mean, sigma, _ = profile_by_id[judge_id]
            latent = ((index / len(TITLES)) * 2 - 1) * sigma * 1.1
            draw = rng.gauss(0, sigma * 0.6) if sigma else 0.0
            technical = max(1, min(10, int(round(mean + latent + draw))))
            if judge_id == "judge_11":
                technical = 8  # zero variance, by definition
            presentation = max(1, min(10, int(round(technical + rng.gauss(0.4, 0.9)))))
            review = {
                "judge_id": judge_id,
                "project_id": project["id"],
                "technical": technical,
                "technical_comment": (
                    "Fixture verdict." if (len(reviews) % 5) != 4 else None
                ),
                "presentation": presentation if (len(reviews) % 7) != 6 else None,
                "presentation_comment": (
                    "Fixture presentation note." if (len(reviews) % 3) != 0 else None
                ),
                "submitted_at": (
                    datetime(2026, 8, 2, tzinfo=timezone.utc)
                    + timedelta(minutes=17 * len(reviews))
                ).isoformat(),
            }
            reviews.append(review)

    coverage: dict[str, int] = {}
    for review in reviews:
        coverage[review["project_id"]] = coverage.get(review["project_id"], 0) + 1

    return {
        "fixture_version": 1,
        "note": (
            "Axion's own fixture dataset, generated by api/scripts/build_fixtures.py. "
            "It is not an organiser-provided fixture set: none was available. It is "
            "deliberately messy so that import, coverage and duplicate handling are "
            "exercised against realistic problems."
        ),
        "generator": "api/scripts/build_fixtures.py",
        "rng_seed": RNG_SEED,
        "target_reviews_per_project": TARGET_REVIEWS_PER_PROJECT,
        "minimum_coverage": MIN_COVERAGE,
        "accounts": {
            "admin": {"email": "admin@fixtures.axion.dev", "password": "fixture-admin"},
            "judge": {"email": "judge_01@fixtures.axion.dev", "password": "fixture-judge"},
            "participant": {"email": "hacker01@fixtures.axion.dev", "password": "fixture-hacker"},
        },
        "event": EVENT,
        "rubric": RUBRIC,
        "tracks": TRACKS,
        "judges": judges,
        "participants": participants,
        "teams": [{"id": team_id, "name": name} for team_id, name in TEAMS],
        "projects": projects,
        "reviews": reviews,
        "expected": {
            "projects": len(projects),
            "judges": len(judges),
            "teams": len(TEAMS),
            "participants": len(participants),
            "reviews": len(reviews),
            "assignments": sum(len(project["assigned_judges"]) for project in projects),
            "projects_below_minimum_coverage": sum(
                1 for project in projects if coverage.get(project["id"], 0) < MIN_COVERAGE
            ),
            "projects_without_reviews": sum(
                1 for project in projects if coverage.get(project["id"], 0) == 0
            ),
            "declared_duplicate_pairs": len(DUPLICATE_PAIRS),
            "zero_variance_judges": [judge["id"] for judge in judges if judge["sigma"] == 0],
            "single_verdict_judges": [
                judge["id"]
                for judge in judges
                if sum(1 for review in reviews if review["judge_id"] == judge["id"]) == 1
            ],
            "null_technical_comments": sum(
                1 for review in reviews if review["technical_comment"] is None
            ),
            "null_summaries": sum(1 for project in projects if project["summary"] is None),
            "assignments_without_scores": sum(
                len(project["assigned_judges"]) for project in projects
            )
            - len(reviews),
            "closed_event": True,
        },
    }


def main() -> int:
    fixture = build()
    OUTPUT.write_text(json.dumps(fixture, indent=2) + "\n", encoding="utf-8")
    expected = fixture["expected"]
    print(f"[axion] wrote {OUTPUT}")
    print(
        "[axion] "
        + ", ".join(f"{key}={value}" for key, value in expected.items() if not isinstance(value, list))
    )
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
