"""Request payloads. Responses are returned as plain dicts from the routers."""
from __future__ import annotations

import re
from datetime import datetime, timezone
from typing import Literal, Optional

from pydantic import BaseModel, ConfigDict, Field, field_validator

EMAIL_RE = re.compile(r"^[^@\s]+@[^@\s]+\.[^@\s]+$")


class RegisterRequest(BaseModel):
    email: str = Field(min_length=3, max_length=255)
    password: str = Field(min_length=8, max_length=200)
    name: str = Field(default="", max_length=255)

    @field_validator("email")
    @classmethod
    def valid_email(cls, value: str) -> str:
        value = value.strip().lower()
        if not EMAIL_RE.match(value):
            raise ValueError("Enter a valid email address")
        return value


class LoginRequest(BaseModel):
    email: str = Field(min_length=3, max_length=255)
    password: str = Field(min_length=1, max_length=200)

    @field_validator("email")
    @classmethod
    def normalize_email(cls, value: str) -> str:
        return value.strip().lower()


class TeamCreateRequest(BaseModel):
    name: str = Field(min_length=2, max_length=80)


class TeamJoinRequest(BaseModel):
    invite_code: str = Field(min_length=4, max_length=32)


class SubmissionUpsertRequest(BaseModel):
    title: str = Field(min_length=2, max_length=255)
    repo_url: str = Field(min_length=8, max_length=500)
    docs_url: Optional[str] = Field(default=None, max_length=500)
    demo_url: Optional[str] = Field(default=None, max_length=500)
    video_url: Optional[str] = Field(default=None, max_length=500)
    summary: Optional[str] = Field(default=None, max_length=2000)
    track_id: Optional[int] = Field(default=None, ge=1)
    # "draft" work in progress is invisible to judging; "submitted" enters the
    # event (integrity check + judge assignment).
    status: Literal["draft", "submitted"] = "submitted"


class CriterionScore(BaseModel):
    key: str = Field(min_length=1, max_length=60)
    value: int = Field(ge=1, le=10)


class ScoreUpsertRequest(BaseModel):
    submission_id: int
    technical_score: Optional[int] = Field(default=None, ge=1, le=10)
    technical_comment: Optional[str] = Field(default=None, max_length=4000)
    presentation_score: Optional[int] = Field(default=None, ge=1, le=10)
    presentation_comment: Optional[str] = Field(default=None, max_length=4000)
    # Per-criterion values for the active rubric. When present they decide the
    # technical score (weight-normalised mean) instead of a bare integer.
    criteria: Optional[list[CriterionScore]] = Field(default=None, max_length=20)


class TrackCreateRequest(BaseModel):
    name: str = Field(min_length=2, max_length=120)
    slug: Optional[str] = Field(default=None, max_length=120)
    description: Optional[str] = Field(default=None, max_length=2000)
    prize_pool: Optional[str] = Field(default=None, max_length=120)
    display_order: int = Field(default=0, ge=0, le=1000)


class PrizeCreateRequest(BaseModel):
    title: str = Field(min_length=2, max_length=255)
    track_id: Optional[int] = Field(default=None, ge=1)
    rank: int = Field(default=1, ge=1, le=100)
    description: Optional[str] = Field(default=None, max_length=2000)


class RubricCriterionInput(BaseModel):
    key: str = Field(min_length=1, max_length=60)
    label: str = Field(min_length=1, max_length=120)
    weight: float = Field(gt=0, le=1000)


class RubricUpdateRequest(BaseModel):
    name: str = Field(default="Default technical rubric", min_length=2, max_length=120)
    criteria: list[RubricCriterionInput] = Field(min_length=1, max_length=20)


class DevLoginRequest(BaseModel):
    role: Literal["admin", "judge", "participant"] = "admin"
    # Optional escape hatch: sign in as one specific seeded account.
    email: Optional[str] = Field(default=None, max_length=255)


class ImportFixtureRequest(BaseModel):
    """Import a fixture file. A dry run is the default, deliberately."""

    path: Optional[str] = Field(default=None, max_length=500)
    dry_run: bool = True


class DuplicateDecisionRequest(BaseModel):
    submission_id: int = Field(ge=1)
    duplicate_of_submission_id: int = Field(ge=1)
    decision: Literal["duplicate", "distinct"] = "duplicate"
    note: Optional[str] = Field(default=None, max_length=1000)


class BalanceAssignmentRequest(BaseModel):
    """Balanced assignment: N reviews per project instead of every judge on everything."""

    reviews_per_project: int = Field(default=3, ge=1, le=50)
    max_projects_per_judge: Optional[int] = Field(default=None, ge=1, le=2000)
    dry_run: bool = True


class VoterRegisterRequest(BaseModel):
    """Ask for a ballot. The address is normalised, then unique per event."""

    email: str = Field(min_length=3, max_length=255)
    name: str = Field(default="", max_length=255)

    @field_validator("email")
    @classmethod
    def valid_email(cls, value: str) -> str:
        value = value.strip().lower()
        if not EMAIL_RE.match(value):
            raise ValueError("Enter a valid email address")
        return value


class VoterVerifyRequest(BaseModel):
    token: str = Field(min_length=8, max_length=200)


class VoteCastRequest(BaseModel):
    """One community vote. The scale is 1-5, the same scale the fixture uses."""

    submission_id: int = Field(ge=1)
    score: int = Field(ge=1, le=5)


class BallotCastRequest(BaseModel):
    """A whole ballot at once, so a voter's choices land in one transaction."""

    votes: list[VoteCastRequest] = Field(min_length=1, max_length=500)


class CommentCreateRequest(BaseModel):
    body: str = Field(min_length=1, max_length=2000)


class CommentModerateRequest(BaseModel):
    status: Literal["visible", "hidden"] = "hidden"
    reason: Optional[str] = Field(default=None, max_length=1000)


class VoteStrikeRequest(BaseModel):
    reason: str = Field(min_length=3, max_length=1000)


class VoterBlockRequest(BaseModel):
    """Block or reinstate a ballot. Blocking never removes votes already cast."""

    blocked: bool = True
    reason: Optional[str] = Field(default=None, max_length=1000)


class WebhookEndpointCreateRequest(BaseModel):
    """Register a webhook receiver. `events` empty means every event."""

    url: str = Field(min_length=8, max_length=500)
    description: Optional[str] = Field(default=None, max_length=255)
    events: Optional[list[str]] = Field(default=None, max_length=40)
    active: bool = True


class WebhookEndpointUpdateRequest(BaseModel):
    description: Optional[str] = Field(default=None, max_length=255)
    events: Optional[list[str]] = Field(default=None, max_length=40)
    active: Optional[bool] = None
    rotate_secret: bool = False


class RecordIssueRequest(BaseModel):
    """Issue participation records. Judges and teams are both covered by default."""

    judges: bool = True
    teams: bool = True
    winners: int = Field(default=0, ge=0, le=10)


class RecordRevokeRequest(BaseModel):
    reason: str = Field(min_length=3, max_length=1000)


class BundleImportRequest(BaseModel):
    """A whole-event bundle. `mode=apply` writes; the default is a dry run."""

    bundle: dict
    mode: Literal["dry_run", "apply"] = "dry_run"


class JudgeCreateRequest(BaseModel):
    email: str = Field(min_length=3, max_length=255)
    password: str = Field(min_length=8, max_length=200)
    name: str = Field(default="", max_length=255)

    @field_validator("email")
    @classmethod
    def valid_email(cls, value: str) -> str:
        value = value.strip().lower()
        if not EMAIL_RE.match(value):
            raise ValueError("Enter a valid email address")
        return value


class JudgeInviteRequest(BaseModel):
    """Organiser request to generate a judge invite link.

    The returned token is single-use and expires in `expires_hours` hours
    (default 72). The invitee follows the link, picks a name and password, and
    their account is created (or an existing participant account is upgraded)
    with role=judge.
    """

    email: str = Field(min_length=3, max_length=255)
    expires_hours: int = Field(default=72, ge=1, le=720)

    @field_validator("email")
    @classmethod
    def valid_email(cls, value: str) -> str:
        value = value.strip().lower()
        if not EMAIL_RE.match(value):
            raise ValueError("Enter a valid email address")
        return value


class EventSettingsUpdateRequest(BaseModel):
    """A move of the organiser's clock. Every field is optional: a PATCH says what
    changed, not what the window should become, so `{"ends_at": ...}` extends a
    deadline without touching the ballot window.

    Timestamps are accepted as ISO-8601. A naive value is read as UTC rather than
    as the server's local time — a deadline that depends on the host's timezone
    table is exactly the class of bug this project keeps writing tests against —
    and every stored value is converted to UTC.
    """

    # An unknown key is a 422 rather than something silently dropped: a console
    # that sent `{"submissions_open": false}` and got a 200 would be a control
    # that appears to work and does nothing, which is worse than an error.
    model_config = ConfigDict(extra="forbid")

    name: Optional[str] = Field(default=None, min_length=1, max_length=160)
    starts_at: Optional[datetime] = None
    ends_at: Optional[datetime] = None
    voting_opens_at: Optional[datetime] = None
    voting_closes_at: Optional[datetime] = None
    note: Optional[str] = Field(default=None, max_length=500)
    # The revision the console read. A mismatch is a 409 rather than a silent
    # overwrite of an edit somebody else made in the meantime.
    expected_revision: Optional[int] = Field(default=None, ge=0)

    @field_validator("starts_at", "ends_at", "voting_opens_at", "voting_closes_at")
    @classmethod
    def utc(cls, value: Optional[datetime]) -> Optional[datetime]:
        if value is None:
            return None
        if value.tzinfo is None:
            return value.replace(tzinfo=timezone.utc)
        return value.astimezone(timezone.utc)

    @field_validator("name")
    @classmethod
    def not_blank(cls, value: Optional[str]) -> Optional[str]:
        if value is None:
            return None
        trimmed = value.strip()
        if not trimmed:
            raise ValueError("The event needs a name")
        return trimmed


class AcceptInviteRequest(BaseModel):
    """Invitee request to claim a judge invite link.

    The raw token from the URL's `?token=` query parameter, plus the name and
    password the new judge wants to use.
    """

    token: str = Field(min_length=16, max_length=200)
    name: str = Field(min_length=1, max_length=255)
    password: str = Field(min_length=8, max_length=200)
