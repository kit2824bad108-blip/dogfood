"""Seed a demo event so the normalization math is visible in seconds.

Run with `python -m app.seed` (or SEED_DEMO=true on boot). Idempotent: if the
admin account already exists nothing is written.

The dataset is shaped, not random noise:
  * 5 judges with deliberately different means and spreads: one disciplined
    grader working a narrow low band, one expansive grader ranging widely, and
    three balanced judges;
  * every judge scores every project, so per-judge z-scores are comparable;
  * two projects are crafted so a naive average and the Axion Z-score
    leaderboard visibly disagree. "Quiet Craft" is rated at the disciplined
    grader's ceiling, while "Flashy Demo" collects a 10 from the expansive
    grader. The naive average prefers the demo; Axion prefers the craft, because
    the disciplined grader's deviation is the more informative signal.

The crafted pair was tuned against the real `zscore` module, not by hand-waving:
see `_demo_preview` (printed on seed) for the resulting flip.
"""
from __future__ import annotations

import random
from datetime import datetime, timedelta, timezone

from sqlalchemy import select

from .audit import record
from .config import settings
from .db import Base, SessionLocal, engine
from .github import check_commit_integrity
from .models import (
    Assignment,
    Prize,
    Rubric,
    Score,
    ScoreCriterion,
    Submission,
    Team,
    TeamMember,
    Track,
    User,
)
from .security import hash_password

RNG_SEED = 42

ADMIN_EMAIL = "admin@axion.dev"
ADMIN_PASSWORD = "axion-admin"

# Offline demo accounts. `admin@axion.local` / `password` is the documented
# pair for a judging session with the Wi-Fi off; these accounts only exist
# alongside SEED_DEMO=true.
#
# There is deliberately no `judge@axion.local`: the judge role resolves to the
# first seeded calibrated judge, and adding a sixth account with the judge role
# would put a permanent zero-verdict row in the calibration table — and, worse,
# silently join every new assignment. One-click "Login as Judge" therefore signs
# in as Dana Disciplined, who has a full set of filed verdicts to look at.
DEV_ACCOUNTS = [
    ("admin@axion.local", "Axion Admin", "admin"),
    ("hacker@axion.local", "Axion Hacker", "participant"),
]
DEV_PASSWORD = "password"

# Tracks and prizes are organiser configuration, not code.
TRACKS = [
    ("AI & Machine Learning", "ai-ml", "Models, agents and applied inference.", "$10,000", 0),
    ("Web3 & Trust", "web3", "On-chain, cryptographic and verifiability tooling.", "$10,000", 1),
    ("Developer Tools", "devtools", "Everything that makes other engineers faster.", "$5,000", 2),
]

TRACK_PRIZES = [
    ("ai-ml", 1, "Best AI project", "Highest Axion score in the track."),
    ("ai-ml", 2, "Runner-up, AI", "Second place in the track."),
    ("web3", 1, "Best Web3 project", "Highest Axion score in the track."),
    ("web3", 2, "Runner-up, Web3", "Second place in the track."),
    ("devtools", 1, "Best developer tool", "Highest Axion score in the track."),
]

OVERALL_PRIZES = [
    (1, "Grand Prize", "Best Axion-normalized score across every track."),
    (2, "Runner-up", "Second place overall."),
]

DEFAULT_RUBRIC_NAME = "Default technical rubric"
DEFAULT_CRITERIA = [
    {"key": "innovation", "label": "Innovation", "weight": 30.0},
    {"key": "code_quality", "label": "Code Quality", "weight": 70.0},
]
# Why the seeded per-criterion values simply mirror the technical verdict: the
# derived score is the weight-normalised mean of the criteria, so mirroring them
# keeps every seeded score — and therefore the crafted Quiet Craft vs Flashy Demo
# flip that this seed exists to demonstrate — byte-for-byte identical to the
# numbers in README.md and JUDGING.md.
CRITERIA_MIRROR_VERDICT = True

JUDGE_PROFILES = [
    # email local part, display name, mean, sigma
    # A disciplined grader who works a narrow band, and an expansive one who
    # ranges widely. Z-score normalization weights the narrow band more heavily,
    # which is the statistically correct reading of a 6 from someone who never
    # gives 6s.
    ("disciplined", "Dana Disciplined", 4.5, 0.60),
    ("expansive", "Enzo Expansive", 7.5, 3.40),
    ("balanced-a", "Ava Balanced", 6.5, 1.00),
    ("balanced-b", "Ben Balanced", 6.0, 1.10),
    ("balanced-c", "Cleo Balanced", 7.0, 0.90),
]
JUDGE_PASSWORD = "axion-judge"

TEAM_NAMES = [
    "Null Pointer Exception",
    "Compile & Conquer",
    "Rubber Duck Squad",
    "Stack Overflow Cartel",
    "Merge Conflict",
    "Off By One",
    "Semantic Drift",
    "Race Condition",
    "Undefined Behavior",
    "Big O Energy",
]

PROJECT_TITLES = [
    "Quiet Craft",           # crafted: naive-average loser, Axion winner
    "Flashy Demo",           # crafted: naive-average winner, Axion loser
    "Distributed Ledger UI",
    "Realtime Chat Mesh",
    "Edge Cache Optimizer",
    "Model Router",
    "Federated Notes",
    "Query Planner",
    "CRDT Whiteboard",
    "Zero-Knowledge Vault",
]

# Crafted rows: [disciplined, expansive, balanced-a, balanced-b, balanced-c].
# The balanced judges give both projects the same score, so they contribute no
# difference — the flip is driven purely by the two extreme graders.
CRAFTED_SCORES = {
    "Quiet Craft": [7, 4, 6, 6, 7],
    "Flashy Demo": [3, 10, 6, 6, 7],
}


def _clip(score: float) -> int:
    return max(1, min(10, int(round(score))))


def _person_names(rng: random.Random, count: int) -> list[str]:
    first = ["Ada", "Linus", "Grace", "Alan", "Barbara", "Dennis", "Ken", "Margaret", "Guido", "Anita",
             "Yuki", "Omar", "Priya", "Diego", "Lena", "Tariq", "Noor", "Hana", "Sven", "Mei"]
    last = ["Lovelace", "Torvalds", "Hopper", "Turing", "Liskov", "Ritchie", "Thompson", "Hamilton",
            "Rossum", "Borg", "Tanaka", "Haddad", "Nair", "Alvarez", "Novak", "Rahman", "Kwon",
            "Sato", "Andersson", "Chen"]
    return [f"{rng.choice(first)} {rng.choice(last)}" for _ in range(count)]


def seed(reset: bool = False) -> dict:
    """Create the demo dataset. Returns a small summary dict."""
    Base.metadata.create_all(bind=engine)
    rng = random.Random(RNG_SEED)

    with SessionLocal() as db:
        if db.scalar(select(User).where(User.email == ADMIN_EMAIL)) is not None and not reset:
            return {"skipped": True, "reason": "demo dataset already present"}

        admin = User(
            email=ADMIN_EMAIL,
            name="Axion Admin",
            role="admin",
            password_hash=hash_password(ADMIN_PASSWORD),
        )
        db.add(admin)

        # Offline accounts: same roles, `.local` addresses, shared password.
        for email, name, role in DEV_ACCOUNTS:
            db.add(User(email=email, name=name, role=role, password_hash=hash_password(DEV_PASSWORD)))

        # Tracks, prizes and the active rubric are organiser configuration.
        tracks: list[Track] = []
        for name, slug, description, prize_pool, order in TRACKS:
            track = Track(
                name=name,
                slug=slug,
                description=description,
                prize_pool=prize_pool,
                display_order=order,
            )
            db.add(track)
            tracks.append(track)
        db.flush()

        track_by_slug = {track.slug: track for track in tracks}
        for slug, rank, title, description in TRACK_PRIZES:
            db.add(
                Prize(
                    track_id=track_by_slug[slug].id,
                    rank=rank,
                    title=title,
                    description=description,
                )
            )
        for rank, title, description in OVERALL_PRIZES:
            db.add(Prize(track_id=None, rank=rank, title=title, description=description))

        rubric = Rubric(
            name=DEFAULT_RUBRIC_NAME, criteria=list(DEFAULT_CRITERIA), is_active=True
        )
        db.add(rubric)
        db.flush()

        judges: list[User] = []
        for local, name, _mu, _sigma in JUDGE_PROFILES:
            judge = User(
                email=f"{local}@axion.dev",
                name=name,
                role="judge",
                password_hash=hash_password(JUDGE_PASSWORD),
            )
            db.add(judge)
            judges.append(judge)
        db.flush()

        # 50 participants spread across 10 teams of 5.
        participants: list[User] = []
        names = _person_names(rng, 50)
        for index, name in enumerate(names):
            participant = User(
                email=f"hacker{index + 1:02d}@axion.dev",
                name=name,
                role="participant",
                password_hash=hash_password("axion-hacker"),
            )
            db.add(participant)
            participants.append(participant)
        db.flush()

        teams: list[Team] = []
        for index, team_name in enumerate(TEAM_NAMES):
            captain = participants[index * 5]
            team = Team(
                name=team_name,
                invite_code=f"{team_name[:3].upper()}{index:02d}X{rng.randint(10, 99)}",
                created_by=captain.id,
            )
            db.add(team)
            teams.append(team)
        db.flush()

        for index, team in enumerate(teams):
            for participant in participants[index * 5 : index * 5 + 5]:
                db.add(TeamMember(team_id=team.id, user_id=participant.id))

        # Put the offline demo hacker on a team so the participant flow is
        # walkable with no network at all.
        dev_hacker = db.scalar(select(User).where(User.email == "hacker@axion.local"))
        if dev_hacker is not None:
            db.add(TeamMember(team_id=teams[0].id, user_id=dev_hacker.id))

        submissions: list[Submission] = []
        for index, (team, title) in enumerate(zip(teams, PROJECT_TITLES)):
            slug = title.lower().replace(" ", "-").replace("&", "and")
            submission = Submission(
                team_id=team.id,
                title=title,
                repo_url=f"https://github.com/axion-demo/{slug}",
                docs_url=f"https://github.com/axion-demo/{slug}#readme",
                demo_url=f"https://{slug}.axion-demo.dev",
                video_url=f"https://youtu.be/axion-demo-{index + 1}",
                summary=f"{title} — a demo submission for the Axion seed dataset.",
                track_id=tracks[index % len(tracks)].id,
                status="submitted",
                submitted_at=datetime.now(timezone.utc) - timedelta(hours=6),
            )
            db.add(submission)
            submissions.append(submission)
        db.flush()

        # Commit integrity: mocked deterministic history, with one clear flag.
        for index, submission in enumerate(submissions):
            report = check_commit_integrity(
                submission.repo_url,
                event_start=settings.event_start,
                event_end=settings.event_end,
                mock=True,
            )
            submission.integrity_pct_in_window = report.get("pct_in_window")
            submission.integrity_flagged = bool(report.get("flagged"))
            submission.integrity_source = report.get("source")
            submission.integrity_details = report
            submission.integrity_checked_at = datetime.now(timezone.utc)

        # A visibly pre-existing repository so the admin review queue is not empty.
        preexisting = submissions[7]
        preexisting.repo_url = "https://github.com/axion-demo/legacy-monolith"
        preexisting.integrity_pct_in_window = 19.4
        preexisting.integrity_flagged = True
        preexisting.integrity_source = "mock"
        preexisting.integrity_details = {
            "source": "mock",
            "commits_scanned": 128,
            "commits_in_window": 25,
            "pct_in_window": 19.4,
            "flagged": True,
            "reason": "Mock history: repository appears to predate the event window.",
        }

        for submission in submissions:
            for judge in judges:
                db.add(Assignment(judge_id=judge.id, submission_id=submission.id))
        db.flush()

        # Latent project quality drives all the non-crafted projects. Keyed by
        # position, not by database id, so the dataset is identical whatever the
        # ids happen to be.
        quality = {
            index: (index / (len(submissions) - 1)) * 2 - 1
            for index in range(len(submissions))
        }

        # Verdicts draw from their own seeded stream, so the generated scores are
        # reproducible regardless of how many names were drawn above. The crafted
        # pair was tuned against exactly this stream and the real zscore module.
        score_rng = random.Random(RNG_SEED)

        # Technical verdicts first: exactly one latent draw per (project, judge)
        # in a fixed order.
        technical_scores: dict[tuple[int, int], int] = {}
        for project_index, submission in enumerate(submissions):
            crafted = CRAFTED_SCORES.get(submission.title)
            for judge_index in range(len(judges)):
                mu, sigma = JUDGE_PROFILES[judge_index][2], JUDGE_PROFILES[judge_index][3]
                latent = quality[project_index] * sigma * 1.15 + score_rng.gauss(0, sigma * 0.55)
                score = _clip(mu + latent)
                if crafted is not None:
                    score = crafted[judge_index]
                technical_scores[(project_index, judge_index)] = score

        verdicts = 0
        for project_index, submission in enumerate(submissions):
            for judge_index, judge in enumerate(judges):
                technical = technical_scores[(project_index, judge_index)]
                presentation = _clip(technical + score_rng.gauss(0.4, 0.9))
                score = Score(
                    submission_id=submission.id,
                    judge_id=judge.id,
                    technical_score=technical,
                    technical_comment="Seeded technical verdict.",
                    presentation_score=presentation,
                    presentation_comment="Seeded presentation verdict.",
                    technical_submitted_at=datetime.now(timezone.utc) - timedelta(hours=5),
                    presentation_submitted_at=datetime.now(timezone.utc) - timedelta(hours=4),
                    rubric_id=rubric.id,
                )
                db.add(score)
                db.flush()
                for entry in DEFAULT_CRITERIA:
                    db.add(
                        ScoreCriterion(
                            score_id=score.id,
                            key=entry["key"],
                            label=entry["label"],
                            weight=float(entry["weight"]),
                            value=technical if CRITERIA_MIRROR_VERDICT else _clip(technical),
                        )
                    )
                verdicts += 1

        record(
            db,
            "seed.run",
            actor=admin,
            entity="event",
            details={"teams": len(teams), "judges": len(judges), "verdicts": verdicts},
        )
        db.commit()

        return {
            "skipped": False,
            "admin": ADMIN_EMAIL,
            "judges": [j.email for j in judges],
            "teams": len(teams),
            "participants": len(participants),
            "submissions": len(submissions),
            "verdicts": verdicts,
            "tracks": [track.slug for track in tracks],
            "rubric": DEFAULT_RUBRIC_NAME,
            "dev_accounts": [email for email, _name, _role in DEV_ACCOUNTS],
        }


def _demo_preview() -> None:
    """Print the raw-vs-normalized comparison so the effect is visible in the logs."""
    from . import zscore

    with SessionLocal() as db:
        rows = db.execute(
            select(Score.judge_id, Score.submission_id, Score.technical_score).where(
                Score.technical_score.isnot(None)
            )
        ).all()
        titles = dict(db.execute(select(Submission.id, Submission.title)).all())
        records = [
            zscore.ScoreRecord(judge_id=j, submission_id=s, score=float(t)) for j, s, t in rows
        ]
        print("\n[axion] raw average vs Axion Z-score normalization")
        raw = {r.submission_id: r for r in zscore.raw_leaderboard(records)}
        norm = zscore.leaderboard(records)
        print(f"{'#':>2}  {'project':<24} {'raw avg':>8}  {'axion':>7}  {'z':>7}  {'move':>5}")
        for row in norm[:10]:
            raw_rank = raw[row.submission_id].rank
            move = raw_rank - row.rank
            print(
                f"{row.rank:>2}  {titles.get(row.submission_id,''):<24} "
                f"{raw[row.submission_id].display:>8.2f}  {row.display:>7.2f}  "
                f"{row.z:>+7.3f}  {move:>+5d}"
            )
        print()


if __name__ == "__main__":
    summary = seed()
    if summary.get("skipped"):
        print(f"[axion] seed skipped: {summary['reason']}")
    else:
        print(f"[axion] seeded {summary}")
    _demo_preview()
