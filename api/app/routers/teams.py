"""Team formation. One team per participant, joined by invite code or GitHub OAuth."""
from __future__ import annotations

import secrets
import string

from fastapi import APIRouter, Depends, HTTPException, Request
from sqlalchemy import select
from sqlalchemy.orm import Session

from .. import audit
from ..db import get_db
from ..deps import client_ip, current_user, require_role
from ..models import Submission, Team, TeamMember, User
from ..schemas import TeamCreateRequest, TeamJoinRequest

router = APIRouter(prefix="/api/teams", tags=["teams"])

INVITE_ALPHABET = string.ascii_uppercase + string.digits


def _invite_code() -> str:
    return "".join(secrets.choice(INVITE_ALPHABET) for _ in range(8))


def membership(db: Session, user_id: int) -> TeamMember | None:
    return db.scalar(select(TeamMember).where(TeamMember.user_id == user_id))


def serialize_team(db: Session, team: Team) -> dict:
    member_rows = db.execute(
        select(User.id, User.name, User.email)
        .join(TeamMember, TeamMember.user_id == User.id)
        .where(TeamMember.team_id == team.id)
    ).all()
    submission = db.scalar(select(Submission).where(Submission.team_id == team.id))
    return {
        "id": team.id,
        "name": team.name,
        "invite_code": team.invite_code,
        "members": [{"id": i, "name": n, "email": e} for i, n, e in member_rows],
        "submission": (
            {
                "id": submission.id,
                "title": submission.title,
                "repo_url": submission.repo_url,
                "integrity_flagged": submission.integrity_flagged,
                "integrity_pct_in_window": submission.integrity_pct_in_window,
            }
            if submission
            else None
        ),
    }


@router.post("")
def create_team(
    payload: TeamCreateRequest,
    request: Request,
    db: Session = Depends(get_db),
    user: User = Depends(current_user),
) -> dict:
    if membership(db, user.id) is not None:
        raise HTTPException(status_code=400, detail="You already belong to a team")
    if db.scalar(select(Team).where(Team.name == payload.name)) is not None:
        raise HTTPException(status_code=409, detail="That team name is taken")

    code = _invite_code()
    while db.scalar(select(Team).where(Team.invite_code == code)) is not None:
        code = _invite_code()

    team = Team(name=payload.name, invite_code=code, created_by=user.id)
    db.add(team)
    db.flush()
    db.add(TeamMember(team_id=team.id, user_id=user.id))
    audit.record(
        db, "team.created", actor=user, entity="team", entity_id=team.id, ip=client_ip(request)
    )
    db.commit()
    return {"team": serialize_team(db, team)}


@router.post("/join")
def join_team(
    payload: TeamJoinRequest,
    request: Request,
    db: Session = Depends(get_db),
    user: User = Depends(current_user),
) -> dict:
    if membership(db, user.id) is not None:
        raise HTTPException(status_code=400, detail="You already belong to a team")

    code = payload.invite_code.strip().upper()
    team = db.scalar(select(Team).where(Team.invite_code == code))
    if team is None:
        raise HTTPException(status_code=404, detail="No team matches that invite code")

    db.add(TeamMember(team_id=team.id, user_id=user.id))
    audit.record(
        db, "team.joined", actor=user, entity="team", entity_id=team.id, ip=client_ip(request)
    )
    db.commit()
    return {"team": serialize_team(db, team)}


@router.get("/me")
def my_team(db: Session = Depends(get_db), user: User = Depends(current_user)) -> dict:
    member = membership(db, user.id)
    if member is None:
        return {"team": None}
    team = db.get(Team, member.team_id)
    return {"team": serialize_team(db, team) if team else None}


@router.get("")
def list_teams(
    db: Session = Depends(get_db),
    user: User = Depends(require_role("admin")),
) -> dict:
    teams = db.scalars(select(Team).order_by(Team.id)).all()
    return {"teams": [serialize_team(db, team) for team in teams]}
