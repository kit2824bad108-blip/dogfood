"""Request payloads. Responses are returned as plain dicts from the routers."""
from __future__ import annotations

import re
from typing import Literal, Optional

from pydantic import BaseModel, Field, field_validator

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
