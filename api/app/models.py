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
    updated_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), server_default=func.now(), onupdate=func.now()
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
