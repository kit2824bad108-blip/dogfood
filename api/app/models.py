"""SQLAlchemy models.

No ORM relationships on purpose: explicit joins keep the query layer obvious and
avoid lazy-loading surprises. Every table is small and read-mostly.
"""
from __future__ import annotations

from datetime import datetime
from typing import Optional

from sqlalchemy import (
    Boolean,
    DateTime,
    Float,
    ForeignKey,
    Integer,
    JSON,
    String,
    Text,
    UniqueConstraint,
    func,
)
from sqlalchemy.orm import Mapped, mapped_column

from .db import Base

ROLES = ("admin", "judge", "participant")
SUBMISSION_STATUSES = ("draft", "submitted")


class User(Base):
    __tablename__ = "users"

    id: Mapped[int] = mapped_column(Integer, primary_key=True)
    email: Mapped[str] = mapped_column(String(255), unique=True, index=True)
    name: Mapped[str] = mapped_column(String(255), default="")
    role: Mapped[str] = mapped_column(String(20), default="participant", index=True)
    password_hash: Mapped[Optional[str]] = mapped_column(String(255), nullable=True)
    github_id: Mapped[Optional[str]] = mapped_column(String(64), nullable=True, unique=True)
    github_login: Mapped[Optional[str]] = mapped_column(String(255), nullable=True)
    created_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), server_default=func.now()
    )


class Team(Base):
    __tablename__ = "teams"

    id: Mapped[int] = mapped_column(Integer, primary_key=True)
    name: Mapped[str] = mapped_column(String(255), unique=True, index=True)
    invite_code: Mapped[str] = mapped_column(String(32), unique=True, index=True)
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
    __table_args__ = (UniqueConstraint("team_id", name="uq_submissions_team"),)

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
    # Commit Integrity (advisory signal, never an automatic disqualification)
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
    actor_id: Mapped[Optional[int]] = mapped_column(
        ForeignKey("users.id", ondelete="SET NULL"), nullable=True
    )
    actor_email: Mapped[Optional[str]] = mapped_column(String(255), nullable=True)
    action: Mapped[str] = mapped_column(String(80), index=True)
    entity: Mapped[Optional[str]] = mapped_column(String(80), nullable=True)
    entity_id: Mapped[Optional[str]] = mapped_column(String(80), nullable=True)
    ip: Mapped[Optional[str]] = mapped_column(String(64), nullable=True)
    details: Mapped[Optional[dict]] = mapped_column(JSON, nullable=True)
    created_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), server_default=func.now(), index=True
    )
