"""Request payloads. Responses are returned as plain dicts from the routers."""
from __future__ import annotations

import re
from typing import Optional

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


class ScoreUpsertRequest(BaseModel):
    submission_id: int
    technical_score: Optional[int] = Field(default=None, ge=1, le=10)
    technical_comment: Optional[str] = Field(default=None, max_length=4000)
    presentation_score: Optional[int] = Field(default=None, ge=1, le=10)
    presentation_comment: Optional[str] = Field(default=None, max_length=4000)


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
