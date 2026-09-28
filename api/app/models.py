"""SQLAlchemy models.

No ORM relationships on purpose: explicit joins keep the query layer obvious and
avoid lazy-loading surprises. Every table is small and read-mostly.
"""
from __future__ import annotations

from datetime import datetime
from typing import Optional

from sqlalchemy import (
    Boolean,
    CheckConstraint,
    DateTime,
    Float,
    ForeignKey,
    Index,
    Integer,
    JSON,
    String,
    Text,
    UniqueConstraint,
    func,
    text,
)
from sqlalchemy.orm import Mapped, mapped_column

from .db import Base

ROLES = ("admin", "judge", "participant")
SUBMISSION_STATUSES = ("draft", "submitted")


class User(Base):
    __tablename__ = "users"
    # Domain checks live in the database as well as in pydantic (see migration
    # 0004): a row that arrives by any other path is still refused.
    __table_args__ = (
        CheckConstraint("role IN ('admin', 'judge', 'participant')", name="ck_users_role"),
    )

    id: Mapped[int] = mapped_column(Integer, primary_key=True)
    email: Mapped[str] = mapped_column(String(255), unique=True, index=True)
    name: Mapped[str] = mapped_column(String(255), default="")
    role: Mapped[str] = mapped_column(String(20), default="participant", index=True)
    password_hash: Mapped[Optional[str]] = mapped_column(String(255), nullable=True)
    github_id: Mapped[Optional[str]] = mapped_column(String(64), nullable=True, unique=True)
    github_login: Mapped[Optional[str]] = mapped_column(String(255), nullable=True)
    # The identifier this row had in the source system it was imported from
    # (`judge_03`, `user_0104`). Imported data keeps its own keys, so re-running
    # an import is idempotent and a record can always be traced back.
    source_ref: Mapped[Optional[str]] = mapped_column(String(120), nullable=True, index=True)
    created_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), server_default=func.now()
    )


class Team(Base):
    __tablename__ = "teams"
    __table_args__ = (
        # A team name is unique *as created in this application*, where 409 is the
        # answer an organiser expects. An imported dataset is exempt: real events
        # contain repeated team names (the fixture dataset has "StillTrail" three
        # times), and renaming them to fit the schema would be the import lying
        # about its input. See migration 0006.
        Index(
            "uq_teams_name_app",
            "name",
            unique=True,
            sqlite_where=text("source_ref IS NULL"),
            postgresql_where=text("source_ref IS NULL"),
        ),
    )

    id: Mapped[int] = mapped_column(Integer, primary_key=True)
    name: Mapped[str] = mapped_column(String(255), index=True)
    invite_code: Mapped[str] = mapped_column(String(32), unique=True, index=True)
    # The imported dataset's own identifier for this team. Identity is never a
    # name: names repeat, ids do not.
    source_ref: Mapped[Optional[str]] = mapped_column(String(120), nullable=True, index=True)
    created_by: Mapped[Optional[int]] = mapped_column(
        ForeignKey("users.id", ondelete="SET NULL"), nullable=True
    )
    created_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), server_default=func.now()
    )


class TeamMember(Base):
    __tablename__ = "team_members"
    __table_args__ = (UniqueConstraint("user_id", name="uq_team_members_user"),)

    id: Mapped[int] = mapped_column(Integer, primary_key=True)
    team_id: Mapped[int] = mapped_column(ForeignKey("teams.id", ondelete="CASCADE"), index=True)
    user_id: Mapped[int] = mapped_column(ForeignKey("users.id", ondelete="CASCADE"), index=True)
    created_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), server_default=func.now()
    )


class Track(Base):
    """A competition category (AI, Web3, Developer Tools…). Organiser-defined."""

    __tablename__ = "tracks"

    id: Mapped[int] = mapped_column(Integer, primary_key=True)
    name: Mapped[str] = mapped_column(String(120), unique=True, index=True)
    slug: Mapped[str] = mapped_column(String(120), unique=True, index=True)
    description: Mapped[Optional[str]] = mapped_column(Text, nullable=True)
    prize_pool: Mapped[Optional[str]] = mapped_column(String(120), nullable=True)
    display_order: Mapped[int] = mapped_column(Integer, default=0)
    created_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), server_default=func.now()
    )


class Prize(Base):
    """A prize, optionally scoped to one track (a null track_id means overall)."""

    __tablename__ = "prizes"

    id: Mapped[int] = mapped_column(Integer, primary_key=True)
    track_id: Mapped[Optional[int]] = mapped_column(
        ForeignKey("tracks.id", ondelete="SET NULL"), nullable=True, index=True
    )
    rank: Mapped[int] = mapped_column(Integer, default=1)
    title: Mapped[str] = mapped_column(String(255))
    description: Mapped[Optional[str]] = mapped_column(Text, nullable=True)
    created_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), server_default=func.now()
    )


class Submission(Base):
    __tablename__ = "submissions"
    __table_args__ = (
        # One *live* submission per team. A second row is allowed only when it is
        # marked as a duplicate of another, which is how an organiser compares a
        # double submission before deciding. Two writers racing to create the same
        # team's submission still produce exactly one winner. See migration 0006.
        Index(
            "uq_submissions_team_canonical",
            "team_id",
            unique=True,
            sqlite_where=text("duplicate_of_submission_id IS NULL"),
            postgresql_where=text("duplicate_of_submission_id IS NULL"),
        ),
        CheckConstraint("status IN ('draft', 'submitted')", name="ck_submissions_status"),
        CheckConstraint(
            "integrity_pct_in_window IS NULL "
            "OR (integrity_pct_in_window >= 0 AND integrity_pct_in_window <= 100)",
            name="ck_submissions_integrity_pct",
        ),
    )

    id: Mapped[int] = mapped_column(Integer, primary_key=True)
    team_id: Mapped[int] = mapped_column(ForeignKey("teams.id", ondelete="CASCADE"), index=True)
    title: Mapped[str] = mapped_column(String(255))
    repo_url: Mapped[str] = mapped_column(String(500))
    docs_url: Mapped[Optional[str]] = mapped_column(String(500), nullable=True)
    demo_url: Mapped[Optional[str]] = mapped_column(String(500), nullable=True)
    video_url: Mapped[Optional[str]] = mapped_column(String(500), nullable=True)
    summary: Mapped[Optional[str]] = mapped_column(Text, nullable=True)
    # Event organisation: which track this project competes in, and whether the
    # team considers the work finished. A draft is invisible to judging.
    track_id: Mapped[Optional[int]] = mapped_column(
        ForeignKey("tracks.id", ondelete="SET NULL"), nullable=True, index=True
    )
    status: Mapped[str] = mapped_column(String(20), default="submitted", index=True)
    submitted_at: Mapped[Optional[datetime]] = mapped_column(
        DateTime(timezone=True), nullable=True
    )
    # External (often string) identifier from the imported dataset — `proj_017`.
    # Nullable because a submission created in the app has no external source.
    source_ref: Mapped[Optional[str]] = mapped_column(String(120), nullable=True, index=True)
    # Set when this row was detected as a duplicate of another submission (same
    # repository URL or title after normalisation). It is a *marker*, never a
    # deletion: the participant's work stays, and the organiser decides.
    duplicate_of_submission_id: Mapped[Optional[int]] = mapped_column(
        ForeignKey("submissions.id", ondelete="SET NULL"), nullable=True, index=True
    )
    integrity_pct_in_window: Mapped[Optional[float]] = mapped_column(Float, nullable=True)
    integrity_flagged: Mapped[bool] = mapped_column(Boolean, default=False)
    integrity_source: Mapped[Optional[str]] = mapped_column(String(40), nullable=True)
    integrity_details: Mapped[Optional[dict]] = mapped_column(JSON, nullable=True)
    integrity_checked_at: Mapped[Optional[datetime]] = mapped_column(
        DateTime(timezone=True), nullable=True
    )
    created_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), server_default=func.now()
    )
    updated_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), server_default=func.now(), onupdate=func.now()
    )


class Assignment(Base):
    __tablename__ = "assignments"
    __table_args__ = (
        UniqueConstraint("judge_id", "submission_id", name="uq_assignment_pair"),
    )

    id: Mapped[int] = mapped_column(Integer, primary_key=True)
    judge_id: Mapped[int] = mapped_column(ForeignKey("users.id", ondelete="CASCADE"), index=True)
    submission_id: Mapped[int] = mapped_column(
        ForeignKey("submissions.id", ondelete="CASCADE"), index=True
    )
    created_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), server_default=func.now()
    )


class Score(Base):
    """One judge's verdict on one submission.

    Technical and presentation are staged: `technical_score` must exist before
    the presentation fields can be written (enforced in the API layer).
    """

    __tablename__ = "scores"
    __table_args__ = (
        UniqueConstraint("judge_id", "submission_id", name="uq_score_pair"),
        CheckConstraint(
            "technical_score IS NULL OR (technical_score >= 1 AND technical_score <= 10)",
            name="ck_scores_technical_range",
        ),
        CheckConstraint(
            "presentation_score IS NULL OR (presentation_score >= 1 AND presentation_score <= 10)",
            name="ck_scores_presentation_range",
        ),
    )

    id: Mapped[int] = mapped_column(Integer, primary_key=True)
    submission_id: Mapped[int] = mapped_column(
        ForeignKey("submissions.id", ondelete="CASCADE"), index=True
    )
    judge_id: Mapped[int] = mapped_column(ForeignKey("users.id", ondelete="CASCADE"), index=True)
    technical_score: Mapped[Optional[int]] = mapped_column(Integer, nullable=True)
    technical_comment: Mapped[Optional[str]] = mapped_column(Text, nullable=True)
    presentation_score: Mapped[Optional[int]] = mapped_column(Integer, nullable=True)
    presentation_comment: Mapped[Optional[str]] = mapped_column(Text, nullable=True)
    technical_submitted_at: Mapped[Optional[datetime]] = mapped_column(
        DateTime(timezone=True), nullable=True
    )
    presentation_submitted_at: Mapped[Optional[datetime]] = mapped_column(
        DateTime(timezone=True), nullable=True
    )
    # Which rubric produced the technical score, for provenance in the archive.
    rubric_id: Mapped[Optional[int]] = mapped_column(
        ForeignKey("rubrics.id", ondelete="SET NULL"), nullable=True
    )
    updated_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), server_default=func.now(), onupdate=func.now()
    )


class Rubric(Base):
    """Weighted scoring criteria the judges score against.

    Exactly one rubric is active at a time; `criteria` is a JSON list of
    {"key", "label", "weight"}. Weights are normalised by their sum at scoring
    time, so organisers can write either percentages (30/70) or fractions.
    """

    __tablename__ = "rubrics"

    id: Mapped[int] = mapped_column(Integer, primary_key=True)
    name: Mapped[str] = mapped_column(String(120), default="Default technical rubric")
    criteria: Mapped[Optional[list]] = mapped_column(JSON, nullable=True)
    is_active: Mapped[bool] = mapped_column(Boolean, default=True, index=True)
    created_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), server_default=func.now()
    )


class ScoreCriterion(Base):
    """One judge's value for one rubric criterion on one submission.

    The derived `scores.technical_score` is the weight-normalised mean of these
    rows, so the Z-score engine keeps consuming a single integer.
    """

    __tablename__ = "score_criteria"
    __table_args__ = (
        UniqueConstraint("score_id", "key", name="uq_score_criterion_key"),
        CheckConstraint("value >= 1 AND value <= 10", name="ck_score_criteria_value_range"),
    )

    id: Mapped[int] = mapped_column(Integer, primary_key=True)
    score_id: Mapped[int] = mapped_column(
        ForeignKey("scores.id", ondelete="CASCADE"), index=True
    )
    key: Mapped[str] = mapped_column(String(60))
    label: Mapped[Optional[str]] = mapped_column(String(120), nullable=True)
    weight: Mapped[float] = mapped_column(Float, default=0.0)
    value: Mapped[int] = mapped_column(Integer)
    created_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), server_default=func.now()
    )


class AuditLog(Base):
    """Append-only. A Postgres trigger blocks UPDATE/DELETE (see the migration)."""

    __tablename__ = "audit_logs"

    id: Mapped[int] = mapped_column(Integer, primary_key=True)
    # Historical, not a live reference. `actor_id` is the id the actor had when the
    # entry was written, and `actor_email` is the durable attribution. A foreign
    # key with `ON DELETE SET NULL` here would make deleting a user *edit the
    # trail* — and the append-only trigger refuses that write, which left the
    # delete impossible instead. Migration 0005 drops it.
    actor_id: Mapped[Optional[int]] = mapped_column(Integer, nullable=True)
    actor_email: Mapped[Optional[str]] = mapped_column(String(255), nullable=True)
    action: Mapped[str] = mapped_column(String(80), index=True)
    entity: Mapped[Optional[str]] = mapped_column(String(80), nullable=True)
    entity_id: Mapped[Optional[str]] = mapped_column(String(80), nullable=True)
    ip: Mapped[Optional[str]] = mapped_column(String(64), nullable=True)
    details: Mapped[Optional[dict]] = mapped_column(JSON, nullable=True)
    created_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), server_default=func.now(), index=True
    )


class ImportBatch(Base):
    """One import attempt, dry run or applied.

    The diagnostics screen reads the latest row instead of recomputing, so what an
    organiser reviews is exactly what was reported at import time.
    """

    __tablename__ = "import_batches"

    id: Mapped[int] = mapped_column(Integer, primary_key=True)
    source: Mapped[str] = mapped_column(String(255))
    mode: Mapped[str] = mapped_column(String(20), default="dry_run", index=True)
    fixture_version: Mapped[Optional[int]] = mapped_column(Integer, nullable=True)
    summary: Mapped[Optional[dict]] = mapped_column(JSON, nullable=True)
    actor_id: Mapped[Optional[int]] = mapped_column(
        ForeignKey("users.id", ondelete="SET NULL"), nullable=True
    )
    actor_email: Mapped[Optional[str]] = mapped_column(String(255), nullable=True)
    created_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), server_default=func.now(), index=True
    )


class DuplicateReview(Base):
    """An organiser's decision about a suspected duplicate.

    Detection is deterministic and recomputed on demand; only the human decision
    is stored, so a change to the detector cannot silently rewrite history.
    """

    __tablename__ = "duplicate_reviews"
    __table_args__ = (
        UniqueConstraint("submission_id", "duplicate_of_submission_id", name="uq_duplicate_pair"),
    )

    id: Mapped[int] = mapped_column(Integer, primary_key=True)
    submission_id: Mapped[int] = mapped_column(
        ForeignKey("submissions.id", ondelete="CASCADE"), index=True
    )
    duplicate_of_submission_id: Mapped[int] = mapped_column(
        ForeignKey("submissions.id", ondelete="CASCADE"), index=True
    )
    decision: Mapped[str] = mapped_column(String(20), default="duplicate")
    note: Mapped[Optional[str]] = mapped_column(Text, nullable=True)
    actor_id: Mapped[Optional[int]] = mapped_column(
        ForeignKey("users.id", ondelete="SET NULL"), nullable=True
    )
    actor_email: Mapped[Optional[str]] = mapped_column(String(255), nullable=True)
    created_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), server_default=func.now()
    )
    updated_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), server_default=func.now(), onupdate=func.now()
    )


class Voter(Base):
    """An email-gated community voter (T3).

    A voter is deliberately *not* a `User`: the spec asks for voting that is
    "email gated, link based or authenticated", and a stranger who wants to rank
    projects should not have to create an account, take a team slot, or appear in
    the participant table. The identity is an email address plus a link.

    Two anti-stuffing rules live in the schema rather than in a heuristic:
    `email` is unique after normalisation (one address, one ballot), and the token
    is stored as a SHA-256 digest, so a database leak hands an attacker nothing
    that can cast a vote.
    """

    __tablename__ = "voters"

    id: Mapped[int] = mapped_column(Integer, primary_key=True)
    email: Mapped[str] = mapped_column(String(255), unique=True, index=True)
    display_name: Mapped[Optional[str]] = mapped_column(String(255), nullable=True)
    token_hash: Mapped[str] = mapped_column(String(64), unique=True, index=True)
    # Null until the voter follows the link. An unverified voter can hold a token
    # and cannot cast anything, which is what "email gated" has to mean if it is
    # to mean anything.
    verified_at: Mapped[Optional[datetime]] = mapped_column(
        DateTime(timezone=True), nullable=True
    )
    first_ip: Mapped[Optional[str]] = mapped_column(String(64), nullable=True)
    last_ip: Mapped[Optional[str]] = mapped_column(String(64), nullable=True)
    blocked: Mapped[bool] = mapped_column(Boolean, default=False, index=True)
    blocked_reason: Mapped[Optional[str]] = mapped_column(Text, nullable=True)
    created_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), server_default=func.now()
    )
    updated_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), server_default=func.now(), onupdate=func.now()
    )


class Vote(Base):
    """One community vote for one project (T3).

    A cast vote is final. It is not a convention: `uq_vote_voter_submission`
    makes a second vote from the same address on the same project a database
    error, so "one person, one vote" holds under concurrency and under a client
    that ignores the API. An organiser who needs to remove a vote *strikes* it —
    the row stays, marked, with a reason and an audit entry, because a deleted
    vote is a vote nobody can audit.
    """

    __tablename__ = "votes"
    __table_args__ = (
        UniqueConstraint("voter_id", "submission_id", name="uq_vote_voter_submission"),
        CheckConstraint("score >= 1 AND score <= 5", name="ck_votes_score_range"),
        CheckConstraint("status IN ('cast', 'struck')", name="ck_votes_status"),
    )

    id: Mapped[int] = mapped_column(Integer, primary_key=True)
    voter_id: Mapped[int] = mapped_column(ForeignKey("voters.id", ondelete="CASCADE"), index=True)
    submission_id: Mapped[int] = mapped_column(
        ForeignKey("submissions.id", ondelete="CASCADE"), index=True
    )
    score: Mapped[int] = mapped_column(Integer)
    status: Mapped[str] = mapped_column(String(20), default="cast", index=True)
    ip: Mapped[Optional[str]] = mapped_column(String(64), nullable=True)
    user_agent: Mapped[Optional[str]] = mapped_column(String(255), nullable=True)
    cast_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), server_default=func.now(), index=True
    )
    struck_at: Mapped[Optional[datetime]] = mapped_column(DateTime(timezone=True), nullable=True)
    struck_by: Mapped[Optional[str]] = mapped_column(String(255), nullable=True)
    struck_reason: Mapped[Optional[str]] = mapped_column(Text, nullable=True)


class Comment(Base):
    """A comment on a project (T3).

    Hidden rather than deleted, for the same reason a vote is struck rather than
    deleted: the organiser's moderation decision is part of the record. `author_*`
    are denormalised and outlive the account, matching `audit_logs`.
    """

    __tablename__ = "comments"
    __table_args__ = (
        CheckConstraint("status IN ('visible', 'hidden')", name="ck_comments_status"),
    )

    id: Mapped[int] = mapped_column(Integer, primary_key=True)
    submission_id: Mapped[int] = mapped_column(
        ForeignKey("submissions.id", ondelete="CASCADE"), index=True
    )
    # Either a signed-in user or a verified community voter, so both are nullable
    # and the denormalised attribution is the durable one.
    author_id: Mapped[Optional[int]] = mapped_column(
        ForeignKey("users.id", ondelete="SET NULL"), nullable=True, index=True
    )
    voter_id: Mapped[Optional[int]] = mapped_column(
        ForeignKey("voters.id", ondelete="SET NULL"), nullable=True, index=True
    )
    author_name: Mapped[Optional[str]] = mapped_column(String(255), nullable=True)
    author_email: Mapped[Optional[str]] = mapped_column(String(255), nullable=True)
    body: Mapped[str] = mapped_column(Text)
    status: Mapped[str] = mapped_column(String(20), default="visible", index=True)
    ip: Mapped[Optional[str]] = mapped_column(String(64), nullable=True)
    created_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), server_default=func.now(), index=True
    )
    updated_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), server_default=func.now(), onupdate=func.now()
    )
    moderated_by: Mapped[Optional[str]] = mapped_column(String(255), nullable=True)
    moderated_reason: Mapped[Optional[str]] = mapped_column(Text, nullable=True)


class WebhookEndpoint(Base):
    """An organiser-registered destination for event webhooks (T4).

    `events` is a JSON list of subscribed event names; an empty list means every
    event. The secret is stored because delivery must be *signed* — a receiver has
    to be able to tell a webhook from this deployment apart from anyone else who
    knows the URL.
    """

    __tablename__ = "webhook_endpoints"

    id: Mapped[int] = mapped_column(Integer, primary_key=True)
    url: Mapped[str] = mapped_column(String(500))
    description: Mapped[Optional[str]] = mapped_column(String(255), nullable=True)
    secret: Mapped[str] = mapped_column(String(64))
    events: Mapped[Optional[list]] = mapped_column(JSON, nullable=True)
    active: Mapped[bool] = mapped_column(Boolean, default=True, index=True)
    created_by: Mapped[Optional[str]] = mapped_column(String(255), nullable=True)
    failure_count: Mapped[int] = mapped_column(Integer, default=0)
    last_delivered_at: Mapped[Optional[datetime]] = mapped_column(
        DateTime(timezone=True), nullable=True
    )
    last_failed_at: Mapped[Optional[datetime]] = mapped_column(
        DateTime(timezone=True), nullable=True
    )
    created_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), server_default=func.now()
    )
    updated_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), server_default=func.now(), onupdate=func.now()
    )


class WebhookDelivery(Base):
    """One event addressed to one endpoint: an outbox row, not a log line.

    The envelope is written at emit time and never rewritten, so a retry is
    byte-identical to the first attempt — which is what lets a receiver dedupe on
    the delivery id and check the signature without re-deriving anything. A
    delivery that exhausts its retries is marked `dead` rather than deleted, so an
    organiser can see what was lost and replay it.
    """

    __tablename__ = "webhook_deliveries"
    __table_args__ = (
        CheckConstraint(
            "status IN ('pending', 'delivered', 'failed', 'dead')",
            name="ck_webhook_deliveries_status",
        ),
    )

    id: Mapped[int] = mapped_column(Integer, primary_key=True)
    endpoint_id: Mapped[int] = mapped_column(
        ForeignKey("webhook_endpoints.id", ondelete="CASCADE"), index=True
    )
    event: Mapped[str] = mapped_column(String(80), index=True)
    payload: Mapped[Optional[dict]] = mapped_column(JSON, nullable=True)
    status: Mapped[str] = mapped_column(String(20), default="pending", index=True)
    attempts: Mapped[int] = mapped_column(Integer, default=0)
    response_status: Mapped[Optional[int]] = mapped_column(Integer, nullable=True)
    response_body: Mapped[Optional[str]] = mapped_column(Text, nullable=True)
    error: Mapped[Optional[str]] = mapped_column(Text, nullable=True)
    next_attempt_at: Mapped[Optional[datetime]] = mapped_column(
        DateTime(timezone=True), nullable=True
    )
    signature: Mapped[Optional[str]] = mapped_column(String(160), nullable=True)
    created_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), server_default=func.now(), index=True
    )
    delivered_at: Mapped[Optional[datetime]] = mapped_column(
        DateTime(timezone=True), nullable=True
    )


class ParticipationRecord(Base):
    """A signed, publicly verifiable attestation about one person or team (T4).

    The record is deliberately self-contained: the attested `summary`, the event,
    the signer and the algorithm all travel inside the payload that is signed, so a
    verifier needs the record and the published key and nothing else. A record is
    never edited — revocation is a separate, dated fact on the row, because
    "issued and later revoked" is a different statement from "never issued".
    """

    __tablename__ = "participation_records"
    __table_args__ = (
        UniqueConstraint("code", name="uq_participation_records_code"),
        CheckConstraint(
            "subject_kind IN ('judge', 'participant', 'team')",
            name="ck_participation_records_kind",
        ),
    )

    id: Mapped[int] = mapped_column(Integer, primary_key=True)
    # The short public handle: what goes on a certificate, and what a verifier
    # types in. Not a secret, and not a security boundary on its own — the
    # signature is.
    code: Mapped[str] = mapped_column(String(32), index=True)
    subject_kind: Mapped[str] = mapped_column(String(20), index=True)
    subject_ref: Mapped[Optional[str]] = mapped_column(String(120), nullable=True)
    subject_name: Mapped[str] = mapped_column(String(255))
    subject_email: Mapped[Optional[str]] = mapped_column(String(255), nullable=True, index=True)
    event_name: Mapped[str] = mapped_column(String(255))
    role: Mapped[Optional[str]] = mapped_column(String(40), nullable=True)
    # Exactly what was signed. Not a human-facing summary of it, and not a set of
    # columns to be reassembled into it: the signature is over these bytes, so the
    # bytes have to be stored. Rebuilding the payload from columns would make
    # verification depend on how each backend round-trips a datetime, which is a bug
    # that only shows up when a certificate is checked on a different machine.
    payload: Mapped[Optional[dict]] = mapped_column(JSON, nullable=True)
    signature: Mapped[str] = mapped_column(String(160))
    algorithm: Mapped[str] = mapped_column(String(40), default="hmac-sha256")
    issued_by: Mapped[Optional[str]] = mapped_column(String(255), nullable=True)
    issued_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), server_default=func.now()
    )
    revoked_at: Mapped[Optional[datetime]] = mapped_column(
        DateTime(timezone=True), nullable=True
    )
    revoked_reason: Mapped[Optional[str]] = mapped_column(Text, nullable=True)


class InviteToken(Base):
    """One-time judge-onboarding token.

    The organiser generates the token; the invitee follows the link, chooses a
    name and password, and the account is created (or upgraded) with role=judge.

    Only the SHA-256 digest of the raw token is stored: a database leak cannot be
    replayed, and the invitee's browser is the only thing that ever sees the raw
    value — the same guarantee as a password-reset link.
    """

    __tablename__ = "invite_tokens"

    id: Mapped[int] = mapped_column(Integer, primary_key=True)
    # SHA-256 hex digest of the raw token the organiser was given.
    token_digest: Mapped[str] = mapped_column(String(64), unique=True, index=True)
    email: Mapped[str] = mapped_column(String(255), index=True)
    # Who generated it (audit trail, not a FK — we want the email even if the
    # organiser account is later removed).
    invited_by: Mapped[Optional[str]] = mapped_column(String(255), nullable=True)
    # Tokens are single-use and expire.
    expires_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), nullable=False)
    used_at: Mapped[Optional[datetime]] = mapped_column(DateTime(timezone=True), nullable=True)
    created_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), server_default=func.now()
    )


class ThrottleEvent(Base):

    """One rate-limited attempt. See `app/throttle.py` for why it is a table.

    `at_epoch` is an integer rather than a datetime on purpose: SQLite and
    Postgres disagree about the timezone of a stored timestamp, and a rate limit
    that reads differently on two backends is a rate limit that can be walked
    around on one of them.
    """

    __tablename__ = "throttle_events"
    __table_args__ = (
        Index("ix_throttle_bucket_key_epoch", "bucket", "key", "at_epoch"),
    )

    id: Mapped[int] = mapped_column(Integer, primary_key=True)
    bucket: Mapped[str] = mapped_column(String(60), index=True)
    key: Mapped[str] = mapped_column(String(160), index=True)
    at_epoch: Mapped[int] = mapped_column(Integer, index=True)
    created_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), server_default=func.now()
    )
