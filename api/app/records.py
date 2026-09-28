"""Signed participation records and the certificates drawn from them (T4).

A participation record is a claim about a person or a team — *this judge filed
fourteen verdicts in this event*, *this team won third place* — that can be checked
by someone who does not trust the deployment it came from. Three decisions make
that true rather than aspirational:

**The signature is over the stored bytes.** `payload` is written once and signed as
stored, so verification is a comparison and not a reconstruction. Rebuilding the
payload from columns would make it depend on how SQLite and Postgres each round-trip
a timestamp — a defect that would only appear when a stranger checked a certificate
on a different machine from the one that issued it.

**Everyone whose record is worth something gets one, and the numbers are the app's
own.** A judge's record carries the verdict count, the coverage they were assigned,
their own mean and their *effective* standard deviation from `zscore` — the same
figures the leaderboard is computed from. A team's record carries its project and its
placement from that same leaderboard, duplicates excluded, because a record that
ranked a different set of verdicts than the results page would be worthless.

**The verification key is published once the event closes.** Records are signed with
HMAC, which is symmetric: whoever can verify can also forge. Publishing the key
before the event ends would let anyone mint a "this team won" record while results
still mattered. Publishing it at the close is enough, because after the close a
forgery cannot change a record that has already been issued and cited — and the
signature, the algorithm and the key fingerprint all travel inside the signed payload
so a verifier needs nothing else. `key_publication()` states this on the wire rather
than leaving it to documentation.

Nothing here is stored twice: a certificate is a rendering of a record, not a second
artefact that can drift from it.
"""
from __future__ import annotations

import hashlib
import hmac
import html
import json
import secrets
from datetime import datetime, timezone
from typing import Optional

from sqlalchemy import func, select
from sqlalchemy.orm import Session

from . import services
from .config import settings
from .models import Assignment, ParticipationRecord, Score, Submission, Team, TeamMember, Track, User
from .timeutil import iso

ALGORITHM = "hmac-sha256"

# No I, O, 0 or 1: a code gets read aloud on a call and typed from a screenshot.
CODE_ALPHABET = "ABCDEFGHJKLMNPQRSTUVWXYZ23456789"


def now_utc() -> datetime:
    return datetime.now(timezone.utc)


def make_code() -> str:
    """A short public handle, in the shape `AXN-4F7Q-2M8Z`."""
    groups = ["".join(secrets.choice(CODE_ALPHABET) for _ in range(4)) for _ in range(2)]
    return "AXN-" + "-".join(groups)


def signing_key() -> str:
    return settings.record_signing_key


def key_fingerprint() -> str:
    """A short, safe-to-publish identifier for the key in use."""
    return hashlib.sha256(signing_key().encode("utf-8")).hexdigest()[:16]


def key_publication() -> dict:
    """Whether the verification key may be published, and why."""
    published = settings.records_verifiable_publicly
    return {
        "algorithm": ALGORITHM,
        "fingerprint": key_fingerprint(),
        "published": published,
        "key": signing_key() if published else None,
        "reason": (
            "The event window is closed, so the key is published and anyone can verify "
            "a record without asking this deployment."
            if published
            else "The event is still running. Publishing a symmetric key early would let "
            "anyone mint a record while the results still matter; records remain "
            "verifiable by the organiser until the window closes."
        ),
        "scheme": "sha256=<hex> over canonical-json(payload) with the key above",
    }


# ── signing ─────────────────────────────────────────────────────────────────


def canonical_bytes(payload: dict) -> bytes:
    """One serialisation, so signing and verification cannot disagree."""
    return json.dumps(payload, sort_keys=True, separators=(",", ":"), default=str).encode("utf-8")


def sign(payload: dict, key: Optional[str] = None) -> str:
    mac = hmac.new((key or signing_key()).encode("utf-8"), canonical_bytes(payload), hashlib.sha256)
    return f"{ALGORITHM}={mac.hexdigest()}"


def verify(record: ParticipationRecord, key: Optional[str] = None) -> dict:
    """Check a stored record. Returns the detail a verifier wants to see."""
    expected = sign(dict(record.payload or {}), key)
    ok = hmac.compare_digest(expected, record.signature or "")
    return {
        "verified": ok,
        "expected": expected,
        "presented": record.signature,
        "algorithm": record.algorithm or ALGORITHM,
        "key_fingerprint": key_fingerprint(),
        "reason": None if ok else "the signature does not match the stored payload",
    }


# ── issuing ─────────────────────────────────────────────────────────────────


def issue(
    db: Session,
    *,
    subject_kind: str,
    subject_name: str,
    subject_ref: Optional[str] = None,
    subject_email: Optional[str] = None,
    role: Optional[str] = None,
    summary: Optional[dict] = None,
    actor: Optional[User] = None,
    event_name: Optional[str] = None,
) -> ParticipationRecord:
    """Create one record. The payload is built, signed, and stored as signed."""
    issued_at = now_utc().replace(microsecond=0)
    payload = {
        "code": None,  # filled below, then re-signed: the code is part of the claim
        "subject": {
            "kind": subject_kind,
            "ref": subject_ref,
            "name": subject_name,
            "email": subject_email,
            "role": role,
        },
        "event": {"name": event_name or settings.event_name, "deployment": settings.web_url},
        "summary": summary or {},
        "issued_at": iso(issued_at),
        "issued_by": actor.email if actor else "seed",
        "algorithm": ALGORITHM,
        "key_fingerprint": key_fingerprint(),
        "verify_url": None,
    }

    record = ParticipationRecord(
        code=make_code(),
        subject_kind=subject_kind,
        subject_ref=subject_ref,
        subject_name=subject_name,
        subject_email=subject_email,
        event_name=payload["event"]["name"],
        role=role,
        algorithm=ALGORITHM,
        issued_by=payload["issued_by"],
        issued_at=issued_at,
    )
    # The code and the verification URL are inside the signed content: a verifier
    # who is told "check AXN-4F7Q-2M8Z" must be able to see that the record they
    # fetched is the record that code names.
    payload["code"] = record.code
    payload["verify_url"] = f"{settings.web_url}/api/records/{record.code}"
    record.payload = payload
    record.signature = sign(payload)
    db.add(record)
    db.flush()
    return record


def _judge_evidence(db: Session):
    """Judges with at least one filed verdict, and what that amounts to."""
    from . import zscore

    records = services.score_records(db)
    stats = zscore.judge_statistics(records)
    filed = dict(
        db.execute(
            services.canonical_only(
                select(Score.judge_id, func.count(Score.id))
                .join(Submission, Submission.id == Score.submission_id)
                .where(Score.technical_score.isnot(None))
                .group_by(Score.judge_id)
            )
        ).all()
    )
    assigned = dict(
        db.execute(
            select(Assignment.judge_id, func.count(Assignment.id)).group_by(Assignment.judge_id)
        ).all()
    )
    judges = db.scalars(select(User).where(User.role == "judge").order_by(User.id)).all()
    for judge in judges:
        count = filed.get(judge.id, 0)
        if not count:
            continue
        stat = stats.get(judge.id)
        yield judge, {
            "verdicts_filed": count,
            "projects_assigned": assigned.get(judge.id, 0),
            "mean_technical_score": round(stat.raw_mean, 3) if stat else None,
            "effective_mean": round(stat.effective_mean, 3) if stat else None,
            "effective_sigma": round(stat.effective_sigma, 3) if stat else None,
            "discriminative": stat.discriminative if stat else None,
            "normalization": "z-score against this judge's own distribution, shrunk toward the pool",
        }


def _team_evidence(db: Session):
    """Every submitted, canonical project with the team behind it."""
    rows = db.execute(
        services.canonical_only(
            select(Submission, Team, Track)
            .join(Team, Team.id == Submission.team_id)
            .outerjoin(Track, Track.id == Submission.track_id)
            .where(Submission.status == "submitted")
        ).order_by(Submission.id)
    ).all()
    for submission, team, track in rows:
        members = list(
            db.scalars(
                select(User.email)
                .join(TeamMember, TeamMember.user_id == User.id)
                .where(TeamMember.team_id == team.id)
                .order_by(User.email)
            ).all()
        )
        yield team, submission, {
            "project": submission.title,
            "track": track.name if track else None,
            "repo_url": submission.repo_url,
            "members": members,
            "submitted_at": iso(submission.submitted_at),
            "commit_integrity_pct_in_window": submission.integrity_pct_in_window,
        }


def ranked_results(db: Session, limit: int = 3) -> list[dict]:
    """The published ranking, from the same engine the results page uses."""
    from . import zscore

    normalized = zscore.leaderboard(services.score_records(db))
    titles = dict(db.execute(select(Submission.id, Submission.title)).all())
    teams = dict(
        db.execute(
            select(Submission.id, Team.name).join(Team, Team.id == Submission.team_id)
        ).all()
    )
    return [
        {
            "rank": row.rank,
            "submission_id": row.submission_id,
            "title": titles.get(row.submission_id),
            "team": teams.get(row.submission_id),
            "axion_score": row.display,
            "z": round(row.z, 4),
        }
        for row in normalized[:limit]
    ]


def issue_for_event(
    db: Session,
    actor: Optional[User] = None,
    *,
    judges: bool = True,
    teams: bool = True,
    winners: int = 0,
) -> dict:
    """Issue the event's records. Idempotent: an existing record is left alone.

    "Left alone" is the whole point. A record is a signed statement about a moment;
    re-issuing one because a verdict changed afterwards would silently invalidate
    every copy of it already in someone's hands. An organiser who needs a corrected
    record revokes the old one and issues a new one, and both facts stay visible.
    """
    existing = {
        (row.subject_kind, row.subject_ref or row.subject_name)
        for row in db.scalars(
            select(ParticipationRecord).where(ParticipationRecord.revoked_at.is_(None))
        ).all()
    }

    issued: list[dict] = []
    skipped: list[dict] = []

    def _once(**kwargs) -> None:
        key = (kwargs["subject_kind"], kwargs.get("subject_ref") or kwargs["subject_name"])
        if key in existing:
            skipped.append({"subject": kwargs["subject_name"], "kind": kwargs["subject_kind"]})
            return
        record = issue(db, actor=actor, **kwargs)
        existing.add(key)
        issued.append(
            {
                "code": record.code,
                "subject_kind": record.subject_kind,
                "subject_name": record.subject_name,
            }
        )

    if judges:
        for judge, summary in _judge_evidence(db):
            _once(
                subject_kind="judge",
                subject_name=judge.name or judge.email,
                subject_ref=judge.source_ref,
                subject_email=judge.email,
                role="judge",
                summary=summary,
            )

    if teams:
        for team, submission, summary in _team_evidence(db):
            _once(
                subject_kind="team",
                subject_name=team.name,
                subject_ref=team.source_ref,
                subject_email=(summary["members"] or [None])[0],
                role="participant",
                summary=summary,
            )

    if winners:
        for entry in ranked_results(db, limit=winners):
            _once(
                subject_kind="team",
                subject_name=entry["team"] or entry["title"],
                subject_ref=f"winner:{entry['rank']}",
                role="winner",
                summary={
                    "placement": entry["rank"],
                    "project": entry["title"],
                    "axion_score": entry["axion_score"],
                    "z": entry["z"],
                    "basis": "technical verdicts only, z-scores averaged, duplicates excluded",
                },
            )

    db.flush()
    return {
        "issued": issued,
        "skipped": skipped,
        "totals": {"issued": len(issued), "already_issued": len(skipped)},
        "note": (
            "Records are never re-issued: a signed statement about a moment would be "
            "invalidated everywhere it was already cited. Revoke and issue a new one."
        ),
        "key": key_publication(),
    }


def revoke(db: Session, record: ParticipationRecord, reason: str, actor: Optional[User] = None) -> ParticipationRecord:
    record.revoked_at = now_utc()
    record.revoked_reason = reason
    db.flush()
    return record


# ── reading ─────────────────────────────────────────────────────────────────


def find(db: Session, code: str) -> Optional[ParticipationRecord]:
    return db.scalar(select(ParticipationRecord).where(ParticipationRecord.code == (code or "").strip().upper()))


def serialize(record: ParticipationRecord, *, include_payload: bool = True) -> dict:
    row = {
        "code": record.code,
        "subject_kind": record.subject_kind,
        "subject_name": record.subject_name,
        "subject_ref": record.subject_ref,
        "subject_email": record.subject_email,
        "role": record.role,
        "event_name": record.event_name,
        "issued_at": iso(record.issued_at),
        "issued_by": record.issued_by,
        "algorithm": record.algorithm,
        "signature": record.signature,
        "revoked": record.revoked_at is not None,
        "revoked_at": iso(record.revoked_at),
        "revoked_reason": record.revoked_reason,
        "verify_url": f"{settings.web_url}/api/records/{record.code}",
        "certificate_url": f"{settings.web_url}/api/records/{record.code}/certificate",
    }
    if include_payload:
        row["payload"] = record.payload
    return row


def verification_report(record: ParticipationRecord, *, public: bool = True) -> dict:
    """The answer a verifier gets: the record, the check, and the key material."""
    check = verify(record)
    return {
        "record": serialize(record),
        "verification": {
            **check,
            # A public verifier is told which key was used and how to get it; the key
            # itself is only readable once the event has closed (see key_publication).
            "key_fingerprint": key_fingerprint(),
            "key_available": settings.records_verifiable_publicly,
        },
        "key": key_publication() if public else None,
        "revocation": {
            "revoked": record.revoked_at is not None,
            "reason": record.revoked_reason,
            "note": "Revocation is a dated fact beside the signature; the record itself is never altered.",
        },
    }


def certificate_html(record: ParticipationRecord) -> str:
    """A self-contained, printable certificate.

    No external asset, font or stylesheet is referenced — the whole thing has to
    render on a laptop with the network off, which is the same rule the rest of the
    deployment follows. The verification block carries the code, the algorithm, the
    key fingerprint and the signature prefix, so a printed copy can be checked by
    hand against the public endpoint.
    """
    payload = dict(record.payload or {})
    summary = payload.get("summary") or {}
    subject = payload.get("subject") or {}
    check = verify(record)

    def rows(items: list[tuple[str, object]]) -> str:
        out = []
        for label, value in items:
            if value in (None, "", [], {}):
                continue
            out.append(
                f'<div class="row"><dt>{html.escape(label)}</dt>'
                f"<dd>{html.escape(str(value))}</dd></div>"
            )
        return "\n".join(out)

    facts = rows(
        [
            ("Project", summary.get("project")),
            ("Track", summary.get("track")),
            ("Placement", f"#{summary['placement']}" if summary.get("placement") else None),
            ("Axion score", summary.get("axion_score")),
            ("Verdicts filed", summary.get("verdicts_filed")),
            ("Projects assigned", summary.get("projects_assigned")),
            ("Mean verdict", summary.get("mean_technical_score")),
            ("Effective σ", summary.get("effective_sigma")),
            ("Team members", ", ".join(summary.get("members") or []) or None),
            ("Submitted", summary.get("submitted_at")),
            ("Basis", summary.get("basis") or summary.get("normalization")),
        ]
    )

    return f"""<!DOCTYPE html>
<html lang="en">
<head>
<meta charset="utf-8">
<title>{html.escape(record.subject_name)} — {html.escape(record.event_name)}</title>
<style>
  :root {{ color-scheme: light; }}
  body {{ margin: 0; padding: 48px 24px; background: #f6f8f7; color: #10201c;
         font: 15px/1.6 ui-sans-serif, system-ui, -apple-system, "Segoe UI", sans-serif; }}
  .sheet {{ max-width: 820px; margin: 0 auto; background: #ffffff; border: 1px solid #cfe3dd;
            border-top: 6px solid #0f766e; padding: 48px 56px; }}
  .kicker {{ font: 600 11px/1 ui-monospace, monospace; letter-spacing: .22em;
             text-transform: uppercase; color: #0f766e; }}
  h1 {{ margin: 14px 0 4px; font-size: 30px; line-height: 1.15; }}
  h2 {{ margin: 0 0 28px; font-size: 16px; font-weight: 500; color: #4b5f59; }}
  dl {{ margin: 0 0 28px; display: grid; grid-template-columns: 1fr; gap: 0; }}
  .row {{ display: flex; gap: 16px; padding: 9px 0; border-bottom: 1px solid #eef4f2; }}
  .row dt {{ flex: 0 0 200px; margin: 0; color: #4b5f59; }}
  .row dd {{ margin: 0; font-weight: 600; }}
  .verify {{ margin-top: 32px; padding: 18px 20px; background: #f2f8f6; border: 1px solid #d8e8e3; }}
  .verify p {{ margin: 0 0 10px; font: 12.5px/1.7 ui-monospace, monospace; }}
  .verify p:last-child {{ margin-bottom: 0; }}
  .ok {{ color: #0f766e; font-weight: 700; }}
  .bad {{ color: #b42318; font-weight: 700; }}
  footer {{ margin-top: 28px; font-size: 12.5px; color: #4b5f59; }}
  @media print {{ body {{ background: #fff; padding: 0; }} .sheet {{ border: 0; }} }}
</style>
</head>
<body>
  <div class="sheet">
    <div class="kicker">Certificate of participation</div>
    <h1>{html.escape(record.subject_name)}</h1>
    <h2>{html.escape(record.event_name)} · {html.escape(record.role or record.subject_kind)}</h2>
    <dl>
      {facts}
    </dl>
    <div class="verify">
      <p>Record <strong>{html.escape(record.code)}</strong> · issued {html.escape(iso(record.issued_at) or "")}</p>
      <p>Signature <span class="{'ok' if check['verified'] else 'bad'}">
        {'valid' if check['verified'] else 'INVALID'}</span>
        · {html.escape(record.algorithm)} · key {html.escape(check['key_fingerprint'])}</p>
      <p>{html.escape((record.signature or '')[:42])}…</p>
      <p>Verify at {html.escape(f"{settings.web_url}/api/records/{record.code}")}</p>
      {f'<p class="bad">Revoked: {html.escape(record.revoked_reason or "")}</p>' if record.revoked_at else ''}
    </div>
    <footer>
      Signed by {html.escape(record.issued_by or 'the organiser')}.
      Participation is attested from this deployment's own records: technical verdicts,
      coverage and the normalized ranking, with duplicate submissions excluded.
    </footer>
  </div>
</body>
</html>
"""


__all__ = [
    "ALGORITHM",
    "certificate_html",
    "canonical_bytes",
    "find",
    "issue",
    "issue_for_event",
    "key_fingerprint",
    "key_publication",
    "make_code",
    "ranked_results",
    "revoke",
    "serialize",
    "sign",
    "signing_key",
    "verification_report",
    "verify",
]
