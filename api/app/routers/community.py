"""The community surface: email-gated voting and project comments (T3).

Everything in T3 that a *visitor* touches lives here, plus the organiser
endpoints that police it. Four things are worth knowing before reading the
handlers:

**A voter is not a user.** T3 asks for voting that is "email gated, link based or
authenticated", so the door is an address plus a link rather than an account. A
stranger can rank projects without taking a team slot or appearing in the
participants table.

**No tally leaves this module while the window is open.** Not in the ballot, not
in a vote response, not in the gallery. A running total is what turns a community
vote into a bandwagon, so the only endpoint that can report one is
`/api/vote/results`, and it answers 403 until the window closes. An organiser can
read them throughout — which is what makes "hidden" checkable rather than a claim.

**Every refusal is the API's, not the interface's.** A blocked voter, an
unverified address, a duplicate vote, a rate-limited client: each is answered
here with a real status code (`curl` sees exactly what the browser sees).

**Line endings of trust.** `X-Axion-Voter` carries the ballot token for
programmatic clients; the same token arrives as the `axion_voter` cookie for a
browser that followed the link once. Both are SHA-256-digested before they touch
the database.
"""
from __future__ import annotations

from datetime import datetime, timedelta, timezone

from fastapi import APIRouter, Depends, HTTPException, Query, Request, Response, status
from sqlalchemy import func, select
from sqlalchemy.orm import Session

from .. import audit, throttle, voting, webhooks
from ..config import settings
from ..db import get_db
from ..deps import client_ip, optional_user, require_role
from ..models import AuditLog, Comment, Submission, Team, Track, User, Vote, Voter
from ..schemas import (
    BallotCastRequest,
    CommentCreateRequest,
    CommentModerateRequest,
    VoteCastRequest,
    VoteStrikeRequest,
    VoterBlockRequest,
    VoterRegisterRequest,
    VoterVerifyRequest,
)
from ..timeutil import as_utc

router = APIRouter(tags=["community"])

VOTER_COOKIE = "axion_voter"
VOTER_HEADER = "x-axion-voter"

# A comment that repeats another comment is a duplicate, not an emphasis. The
# window is deliberately short: saying the same thing again an hour later in a
# different conversation is ordinary, saying it twice in ten minutes is a flood.
DUPLICATE_COMMENT_WINDOW = timedelta(minutes=10)

COMMENT_LIST_LIMIT = 200


# ── helpers ─────────────────────────────────────────────────────────────────


def _enforce(decision: throttle.Decision, what: str) -> None:
    """Turn a throttle refusal into the status code a client expects."""
    if decision.allowed:
        return
    raise HTTPException(
        status_code=status.HTTP_429_TOO_MANY_REQUESTS,
        detail=f"Too many {what} from here. Try again in {decision.retry_after} seconds.",
        headers=decision.headers(),
    )


def _limited_read(db: Session, bucket: str, key: str | None, what: str) -> None:
    """Rate-limit a *read*.

    The attempt is committed here, and not at the end of the handler, because
    these handlers write nothing else and `get_db` does not commit: a limiter
    whose rows are rolled back when the session closes is a limiter that never
    counts a single request. A write endpoint needs none of this — its own commit
    carries the attempt with it.
    """
    _enforce(throttle.hit(db, bucket, key), what)
    db.commit()


def _voter_token(request: Request) -> str | None:
    header = request.headers.get(VOTER_HEADER)
    if header:
        return header.strip()
    return request.cookies.get(VOTER_COOKIE)


def _voter_or_401(request: Request, db: Session) -> Voter:
    voter = voting.voter_by_token(db, _voter_token(request))
    if voter is None:
        raise HTTPException(
            status_code=status.HTTP_401_UNAUTHORIZED,
            detail="Ask for a ballot first (POST /api/vote/register)",
        )
    if voter.blocked:
        raise HTTPException(
            status_code=status.HTTP_403_FORBIDDEN,
            detail=voter.blocked_reason or "This ballot has been blocked by the organiser",
        )
    return voter


def _verified_voter_or_403(request: Request, db: Session) -> Voter:
    voter = _voter_or_401(request, db)
    if voter.verified_at is None:
        raise HTTPException(
            status_code=status.HTTP_403_FORBIDDEN,
            detail="Follow the link we issued for this address before voting",
        )
    return voter


def _public_submission(db: Session, submission_id: int) -> Submission:
    """A submission a stranger is allowed to see, or 404.

    Drafts have not entered the event and a marked duplicate is shown once, in the
    organiser's review queue rather than twice in public. Both answer 404 rather
    than 403: there is nothing here for an anonymous caller to be told about.
    """
    submission = db.get(Submission, submission_id)
    if submission is None or submission.status != "submitted":
        raise HTTPException(status_code=404, detail="Project not found")
    if submission.duplicate_of_submission_id is not None:
        raise HTTPException(status_code=404, detail="Project not found")
    return submission


def _set_voter_cookie(response: Response, token: str) -> None:
    response.set_cookie(
        VOTER_COOKIE,
        token,
        max_age=60 * 60 * 24 * 30,
        httponly=True,
        samesite="lax",
        # Same rule as the session cookie: a shared secret over plain HTTP on a
        # LAN is the deployment's choice to make, not this module's.
        secure=settings.cookie_secure,
        path="/",
    )


def _delivery_note() -> dict:
    """How the ballot link reaches the voter, stated rather than implied.

    Axion has no mail server and must not acquire one: the rules require it to run
    with the network off. So the link is returned to the caller and written to the
    organiser console, and this field says so on the wire. A deployment that wants
    real delivery puts a mailer in front of this endpoint; nothing else changes.
    """
    return {
        "channel": "returned",
        "mailer_configured": False,
        "note": (
            "No SMTP dependency: the link is returned here and listed in the "
            "organiser console. Wire a mailer to POST /api/vote/register to send it."
        ),
    }


def _cast(
    db: Session,
    voter: Voter,
    *,
    submission_id: int,
    score: int,
    ip: str | None,
    user_agent: str | None,
) -> tuple[str, Vote | None]:
    """Cast one immutable vote. Returns (outcome, vote).

    Immutability is the anti-abuse rule, not a preference: the unique constraint
    makes a second vote an error, so "changing your mind" is not a way to vote
    twice, and an organiser removes a vote by striking it — which keeps the row
    and writes an audit entry.
    """
    submission = _public_submission(db, submission_id)
    existing = db.scalar(
        select(Vote).where(
            Vote.voter_id == voter.id, Vote.submission_id == submission.id
        )
    )
    if existing is not None:
        return "already_cast", existing

    vote = Vote(
        voter_id=voter.id,
        submission_id=submission.id,
        score=score,
        status="cast",
        ip=ip,
        user_agent=(user_agent or "")[:255] or None,
    )
    db.add(vote)
    db.flush()
    audit.record(
        db,
        "vote.cast",
        entity="vote",
        entity_id=vote.id,
        ip=ip,
        details={
            "voter_id": voter.id,
            "submission_id": submission.id,
            "score": score,
            "email": voter.email,
        },
    )
    # A subscriber hears *that* a vote happened, never the address behind it: a
    # webhook payload is a place data goes to live on, and the voter's address is
    # the one thing a public event has promised not to spread. The tally itself is
    # also absent, for the same reason the results page is closed until the window
    # ends.
    webhooks.emit(
        db,
        webhooks.EVENT_VOTE_CAST,
        {
            "submission_id": submission.id,
            "submission_title": submission.title,
            "voter_ref": f"voter_{vote.voter_id}",
            "window_closes_at": voting.voting_window(db)["closes_at"],
        },
    )
    return "cast", vote


# ── the voter's own journey ─────────────────────────────────────────────────


@router.post("/api/vote/register", status_code=status.HTTP_201_CREATED)
def register_voter(
    payload: VoterRegisterRequest,
    request: Request,
    response: Response,
    db: Session = Depends(get_db),
) -> dict:
    """Ask for a ballot. Idempotent per address: asking again rotates the link."""
    ip = client_ip(request)
    email = voting.normalize_email(payload.email)
    _enforce(
        throttle.hit_all(db, [("vote.register", email), ("vote.register.ip", ip)]),
        "ballot requests",
    )

    voter = db.scalar(select(Voter).where(Voter.email == email))
    token, token_hash = voting.mint_token()
    created = voter is None
    if voter is None:
        voter = Voter(
            email=email,
            display_name=(payload.name or "").strip() or None,
            token_hash=token_hash,
            first_ip=ip,
            last_ip=ip,
        )
        db.add(voter)
        db.flush()
    else:
        if voter.blocked:
            raise HTTPException(
                status_code=status.HTTP_403_FORBIDDEN,
                detail=voter.blocked_reason or "This ballot has been blocked by the organiser",
            )
        # The newest link wins, so a leaked or mistyped link stops working the
        # moment the voter asks for another one. Verification survives a reissue:
        # the address has already been proven.
        voter.token_hash = token_hash
        voter.last_ip = ip
        if (payload.name or "").strip():
            voter.display_name = payload.name.strip()

    audit.record(
        db,
        "voter.registered" if created else "voter.link_reissued",
        entity="voter",
        entity_id=voter.id,
        ip=ip,
        details={"email": email},
    )
    db.commit()

    verify_path = f"/api/vote/verify?token={token}"
    return {
        "voter": voting.serialize_voter(voter),
        "token": token,
        "verify_url": verify_path,
        "ballot_url": "/vote",
        "window": voting.voting_window(db),
        "delivery": _delivery_note(),
    }


@router.get("/api/vote/verify")
def verify_voter_link(
    request: Request,
    response: Response,
    token: str = Query(min_length=8, max_length=200),
    db: Session = Depends(get_db),
) -> dict:
    """The link from the ballot request. Verifies the address and signs the browser in.

    Read-only by design: a link that arrives in a chat client must not cast
    anything. Following it verifies the address and sets the ballot cookie; the
    votes themselves still have to be submitted.
    """
    ip = client_ip(request)
    _enforce(throttle.hit(db, "vote.verify", ip), "verification attempts")

    voter = voting.voter_by_token(db, token)
    if voter is None:
        raise HTTPException(status_code=404, detail="This ballot link is not valid")
    if voter.blocked:
        raise HTTPException(
            status_code=status.HTTP_403_FORBIDDEN,
            detail=voter.blocked_reason or "This ballot has been blocked by the organiser",
        )
    if voter.verified_at is None:
        voter.verified_at = datetime.now(timezone.utc)
        audit.record(
            db,
            "voter.verified",
            entity="voter",
            entity_id=voter.id,
            ip=ip,
            details={"email": voter.email},
        )
    db.commit()
    _set_voter_cookie(response, token)
    return {
        "voter": voting.serialize_voter(voter),
        "window": voting.voting_window(db),
    }


@router.post("/api/vote/verify")
def verify_voter(
    payload: VoterVerifyRequest,
    request: Request,
    response: Response,
    db: Session = Depends(get_db),
) -> dict:
    """The same link, for a client that would rather post a token than click it."""
    return verify_voter_link(request, response, token=payload.token, db=db)


@router.get("/api/vote/me")
def my_ballot_state(
    request: Request,
    db: Session = Depends(get_db),
) -> dict:
    """The caller's own voting state. No tally of anyone else's votes."""
    voter = _voter_or_401(request, db)
    votes = voting.voter_votes(db, voter)
    return {
        "voter": voting.serialize_voter(voter),
        "window": voting.voting_window(db),
        "voted": sorted(votes),
        "votes": [voting.serialize_vote(vote) for vote in sorted(votes.values(), key=lambda v: v.submission_id)],
    }


@router.get("/api/vote/ballot")
def get_ballot(
    request: Request,
    db: Session = Depends(get_db),
) -> dict:
    """The voter's ballot: every votable project, in that voter's own order."""
    voter = _verified_voter_or_403(request, db)
    _limited_read(db, "vote.ballot", f"voter:{voter.id}", "ballot reads")

    ordered_ids = voting.ballot_order(voter, voting.castable_submission_ids(db))
    rows = {}
    if ordered_ids:
        rows = {
            submission.id: (submission, team, track)
            for submission, team, track in db.execute(
                select(Submission, Team, Track)
                .join(Team, Team.id == Submission.team_id)
                .outerjoin(Track, Track.id == Submission.track_id)
                .where(Submission.id.in_(ordered_ids))
            ).all()
        }
    mine = voting.voter_votes(db, voter)

    ballot = []
    for position, submission_id in enumerate(ordered_ids, start=1):
        found = rows.get(submission_id)
        if found is None:
            continue
        submission, team, track = found
        vote = mine.get(submission_id)
        ballot.append(
            {
                "position": position,
                "submission_id": submission.id,
                "title": submission.title,
                "team": team.name,
                "summary": submission.summary,
                "repo_url": submission.repo_url,
                "docs_url": submission.docs_url,
                "track": {"slug": track.slug, "name": track.name} if track else None,
                "my_score": vote.score if vote else None,
                "my_status": vote.status if vote else None,
            }
        )

    cast = [entry for entry in ballot if entry["my_score"] is not None]
    return {
        "voter": voting.serialize_voter(voter),
        "window": voting.voting_window(db),
        "ballot": ballot,
        "progress": {
            "votable": len(ballot),
            "cast": len(cast),
            "remaining": len(ballot) - len(cast),
        },
        # Published so the ordering is auditable rather than mysterious: an
        # organiser can reproduce any voter's ballot with the same inputs.
        "ordering": {
            "method": "hmac-sha256(secret_key, voter_token_digest + ':' + submission_id)",
            "properties": [
                "stable for this voter across reloads",
                "different for every voter",
                "reproducible by an organiser investigating a complaint",
            ],
        },
    }


@router.post("/api/vote", status_code=status.HTTP_201_CREATED)
def cast_vote(
    payload: VoteCastRequest,
    request: Request,
    db: Session = Depends(get_db),
) -> dict:
    """Cast one vote. Final: a second vote on the same project is a 409."""
    ip = client_ip(request)
    voter = _verified_voter_or_403(request, db)
    _enforce(
        throttle.hit_all(
            db, [("vote.cast", f"voter:{voter.id}"), ("vote.cast.ip", ip)]
        ),
        "votes",
    )
    outcome, vote = _cast(
        db,
        voter,
        submission_id=payload.submission_id,
        score=payload.score,
        ip=ip,
        user_agent=request.headers.get("user-agent"),
    )
    db.commit()
    if outcome == "already_cast":
        raise HTTPException(
            status_code=status.HTTP_409_CONFLICT,
            detail=(
                "You have already voted on this project. A vote is final: an "
                "organiser can strike it, but it cannot be overwritten."
            ),
        )
    return {
        "vote": voting.serialize_vote(vote) if vote else None,
        "window": voting.voting_window(db),
        "note": "Your vote is recorded. No tally is published until the window closes.",
    }


@router.post("/api/vote/ballot", status_code=status.HTTP_201_CREATED)
def cast_ballot(
    payload: BallotCastRequest,
    request: Request,
    db: Session = Depends(get_db),
) -> dict:
    """Submit a whole ballot in one transaction.

    Validation runs before anything is written, so a ballot containing a project
    that has been withdrawn is refused whole rather than silently half-counted.
    Projects the voter has already voted on are reported and skipped — that is not
    an error, it is a client sending its stale copy of the ballot.
    """
    ip = client_ip(request)
    voter = _verified_voter_or_403(request, db)
    _enforce(
        throttle.hit_all(
            db, [("vote.cast", f"voter:{voter.id}"), ("vote.cast.ip", ip)]
        ),
        "votes",
    )

    seen: set[int] = set()
    for entry in payload.votes:
        if entry.submission_id in seen:
            raise HTTPException(
                status_code=400,
                detail=f"Ballot names submission {entry.submission_id} twice",
            )
        seen.add(entry.submission_id)
        _public_submission(db, entry.submission_id)

    cast, already, votes = [], [], []
    for entry in payload.votes:
        outcome, vote = _cast(
            db,
            voter,
            submission_id=entry.submission_id,
            score=entry.score,
            ip=ip,
            user_agent=request.headers.get("user-agent"),
        )
        if outcome == "already_cast":
            already.append(entry.submission_id)
            continue
        cast.append(entry.submission_id)
        if vote is not None:
            votes.append(voting.serialize_vote(vote))

    db.commit()
    return {
        "cast": cast,
        "already_cast": already,
        "votes": votes,
        "window": voting.voting_window(db),
        "note": "Your ballot is recorded. No tally is published until the window closes.",
    }


@router.get("/api/vote/results")
def vote_results(
    request: Request,
    db: Session = Depends(get_db),
    user: User | None = Depends(optional_user),
    include_struck: bool = Query(default=False),
) -> dict:
    """Community results. 403 until the window closes, unless you are an organiser."""
    _limited_read(db, "vote.results", client_ip(request), "requests")
    organiser = user is not None and user.role == "admin"
    if not voting.results_visible(db) and not organiser:
        raise HTTPException(
            status_code=status.HTTP_403_FORBIDDEN,
            detail={
                "detail": "Community results are hidden until the voting window closes",
                "window": voting.voting_window(db),
            },
        )

    payload = voting.aggregate(db, include_struck=include_struck and organiser)
    payload["visibility"] = {
        "results_visible": voting.results_visible(db),
        "shown_to": "organiser" if organiser and not voting.results_visible(db) else "everyone",
        "reason": (
            "The window is open, so no tally is published: a running total would "
            "let the first votes decide the rest."
            if not voting.results_visible(db)
            else "The window has closed."
        ),
    }
    return payload


# ── comments ────────────────────────────────────────────────────────────────


@router.get("/api/submissions/{submission_id}/comments")
def list_comments(
    submission_id: int,
    db: Session = Depends(get_db),
    user: User | None = Depends(optional_user),
) -> dict:
    """Public comment thread on a public project."""
    submission = _public_submission(db, submission_id)
    organiser = user is not None and user.role == "admin"

    statement = (
        select(Comment)
        .where(Comment.submission_id == submission.id)
        .order_by(Comment.created_at.desc(), Comment.id.desc())
        .limit(COMMENT_LIST_LIMIT)
    )
    if not organiser:
        # A hidden comment is not merely unlisted: it is absent from the public
        # payload and from the count, so a visitor cannot infer that one exists.
        statement = statement.where(Comment.status == "visible")
    rows = db.scalars(statement).all()

    visible_count = db.scalar(
        select(func.count(Comment.id)).where(
            Comment.submission_id == submission.id, Comment.status == "visible"
        )
    ) or 0

    return {
        "submission_id": submission.id,
        "title": submission.title,
        "count": visible_count,
        "includes_hidden": organiser,
        "comments": [voting.serialize_comment(row, viewer_is_organiser=organiser) for row in rows],
    }


@router.post("/api/submissions/{submission_id}/comments", status_code=status.HTTP_201_CREATED)
def create_comment(
    submission_id: int,
    payload: CommentCreateRequest,
    request: Request,
    db: Session = Depends(get_db),
    user: User | None = Depends(optional_user),
) -> dict:
    """Comment on a project, as a signed-in user or as a verified voter."""
    submission = _public_submission(db, submission_id)
    ip = client_ip(request)

    voter: Voter | None = None
    if user is None:
        # No session: the T3 door is the ballot link, and an unverified address is
        # not a speaker.
        voter = _verified_voter_or_403(request, db)
        identity_key = f"voter:{voter.id}"
        author_name = voter.display_name or voter.email.split("@")[0]
        author_email = voter.email
    else:
        identity_key = f"user:{user.id}"
        author_name = user.name or user.email.split("@")[0]
        author_email = user.email

    _enforce(
        throttle.hit_all(
            db,
            [("comment.create", identity_key), ("comment.create.ip", ip)],
        ),
        "comments",
    )

    body = payload.body.strip()
    if not body:
        raise HTTPException(status_code=400, detail="A comment needs a body")

    # Duplicate detection: the same words from the same identity, twice in ten
    # minutes, is a flood rather than a conversation.
    #
    # The window is compared in Python rather than in SQL on purpose. SQLite
    # returns naive datetimes from `DateTime(timezone=True)` columns and Postgres
    # returns aware ones, so a `WHERE created_at >= :aware_now` reads differently
    # on the two backends — and the offline/demo path is SQLite. `as_utc` is the
    # single place that decides what a stored timestamp means, so it decides here
    # too. One row is fetched, not a window of rows: only the most recent comment
    # from this identity on this project can be a duplicate of it.
    previous = db.scalar(
        select(Comment)
        .where(
            Comment.submission_id == submission.id,
            Comment.author_id == (user.id if user else None),
            Comment.voter_id == (voter.id if voter else None),
        )
        .order_by(Comment.id.desc())
        .limit(1)
    )
    if previous is not None and previous.body == body:
        posted_at = as_utc(previous.created_at)
        if posted_at is not None and posted_at >= datetime.now(timezone.utc) - DUPLICATE_COMMENT_WINDOW:
            raise HTTPException(
                status_code=status.HTTP_409_CONFLICT,
                detail="You already posted that comment on this project",
            )

    comment = Comment(
        submission_id=submission.id,
        author_id=user.id if user else None,
        voter_id=voter.id if voter else None,
        author_name=author_name,
        author_email=author_email,
        body=body,
        status="visible",
        ip=ip,
    )
    db.add(comment)
    db.flush()
    audit.record(
        db,
        "comment.created",
        actor=user,
        entity="comment",
        entity_id=comment.id,
        ip=ip,
        details={
            "submission_id": submission.id,
            "author_email": author_email,
            "author_kind": "user" if user else "voter",
            "length": len(body),
        },
    )
    # The body travels, and the address does not. A subscriber that wants to
    # display comments is a subscriber that needs the words; who wrote them is the
    # moderator's business, and the moderator reads the console.
    webhooks.emit(
        db,
        webhooks.EVENT_COMMENT_CREATED,
        {
            "comment_id": comment.id,
            "submission_id": submission.id,
            "submission_title": submission.title,
            "author_name": author_name,
            "author_kind": "user" if user else "voter",
            "body": body,
            "status": comment.status,
        },
    )
    db.commit()
    return {
        "comment": voting.serialize_comment(comment),
        "count": db.scalar(
            select(func.count(Comment.id)).where(
                Comment.submission_id == submission.id, Comment.status == "visible"
            )
        ) or 0,
    }


@router.delete("/api/comments/{comment_id}")
def withdraw_comment(
    comment_id: int,
    request: Request,
    reason: str | None = Query(default=None, max_length=500),
    db: Session = Depends(get_db),
    user: User | None = Depends(optional_user),
) -> dict:
    """Withdraw your own comment, or hide anyone's as an organiser.

    Withdrawn, not deleted: the row keeps its author, its body and the reason, so
    a moderator's view of the conversation does not change underneath them.
    """
    comment = db.get(Comment, comment_id)
    if comment is None:
        raise HTTPException(status_code=404, detail="Comment not found")

    voter = None
    if user is None:
        voter = voting.voter_by_token(db, _voter_token(request))

    is_author = (user is not None and comment.author_id == user.id) or (
        voter is not None and comment.voter_id == voter.id
    )
    is_organiser = user is not None and user.role == "admin"
    if not (is_author or is_organiser):
        raise HTTPException(
            status_code=status.HTTP_403_FORBIDDEN,
            detail="You can withdraw your own comment, or an organiser can hide any comment",
        )

    comment.status = "hidden"
    comment.moderated_by = (
        user.email if user else (voter.email if voter else None)
    )
    comment.moderated_reason = reason or (
        "withdrawn by the author" if is_author else "hidden by an organiser"
    )
    audit.record(
        db,
        "comment.withdrawn" if is_author else "comment.moderated",
        actor=user,
        entity="comment",
        entity_id=comment.id,
        ip=client_ip(request),
        details={"status": "hidden", "reason": comment.moderated_reason},
    )
    db.commit()
    return {"comment": voting.serialize_comment(comment, viewer_is_organiser=True)}


# ── organiser tooling ───────────────────────────────────────────────────────


@router.get("/api/admin/community")
def community_overview(
    db: Session = Depends(get_db),
    user: User = Depends(require_role("admin")),
) -> dict:
    """Participation, integrity signals and the throttle's own state.

    Opens with the tally because an organiser is the one person allowed to see it
    while the window is open; everything else is the evidence behind it.
    """
    unverified = db.scalars(
        select(Voter)
        .where(Voter.verified_at.is_(None))
        .order_by(Voter.created_at.desc())
        .limit(50)
    ).all()
    return {
        "window": voting.voting_window(db),
        "participation": voting.participation(db),
        "results": voting.aggregate(db, include_struck=True)["results"],
        "integrity": voting.integrity_signals(db),
        "throttling": [
            throttle.recent_hits(db, "vote.cast.ip", window_seconds=900, limit=20),
            throttle.recent_hits(db, "vote.register.ip", window_seconds=3600, limit=20),
            throttle.recent_hits(db, "comment.create.ip", window_seconds=900, limit=20),
        ],
        "pending_voters": [
            {
                "id": voter.id,
                "email": voter.email,
                "display_name": voter.display_name,
                "created_at": voter.created_at.isoformat() if voter.created_at else None,
                "first_ip": voter.first_ip,
            }
            for voter in unverified
        ],
        "recent_moderation": [
            {
                "id": row.id,
                "action": row.action,
                "entity": row.entity,
                "entity_id": row.entity_id,
                "actor_email": row.actor_email,
                "created_at": row.created_at.isoformat() if row.created_at else None,
                "details": row.details,
            }
            for row in db.scalars(
                select(AuditLog)
                .where(
                    AuditLog.action.in_(
                        [
                            "vote.cast",
                            "vote.struck",
                            "comment.created",
                            "comment.moderated",
                            "comment.withdrawn",
                            "voter.blocked",
                            "voter.unblocked",
                        ]
                    )
                )
                .order_by(AuditLog.id.desc())
                .limit(30)
            ).all()
        ],
    }


@router.post("/api/admin/votes/{vote_id}/strike")
def strike_vote(
    vote_id: int,
    payload: VoteStrikeRequest,
    request: Request,
    db: Session = Depends(get_db),
    user: User = Depends(require_role("admin")),
) -> dict:
    """Invalidate a vote without deleting it, with a reason that stays on the record."""
    vote = db.get(Vote, vote_id)
    if vote is None:
        raise HTTPException(status_code=404, detail="Vote not found")
    if vote.status == "struck":
        raise HTTPException(status_code=409, detail="That vote is already struck")

    vote.status = "struck"
    vote.struck_at = datetime.now(timezone.utc)
    vote.struck_by = user.email
    vote.struck_reason = payload.reason
    audit.record(
        db,
        "vote.struck",
        actor=user,
        entity="vote",
        entity_id=vote.id,
        ip=client_ip(request),
        details={
            "submission_id": vote.submission_id,
            "voter_id": vote.voter_id,
            "score": vote.score,
            "reason": payload.reason,
        },
    )
    db.commit()
    return {"vote": voting.serialize_vote(vote)}


@router.post("/api/admin/comments/{comment_id}/moderate")
def moderate_comment(
    comment_id: int,
    payload: CommentModerateRequest,
    request: Request,
    db: Session = Depends(get_db),
    user: User = Depends(require_role("admin")),
) -> dict:
    comment = db.get(Comment, comment_id)
    if comment is None:
        raise HTTPException(status_code=404, detail="Comment not found")

    comment.status = payload.status
    comment.moderated_by = user.email
    comment.moderated_reason = payload.reason
    audit.record(
        db,
        "comment.moderated",
        actor=user,
        entity="comment",
        entity_id=comment.id,
        ip=client_ip(request),
        details={
            "status": payload.status,
            "reason": payload.reason,
            "author_email": comment.author_email,
        },
    )
    db.commit()
    return {"comment": voting.serialize_comment(comment, viewer_is_organiser=True)}


@router.post("/api/admin/voters/{voter_id}/block")
def block_voter(
    voter_id: int,
    payload: VoterBlockRequest,
    request: Request,
    db: Session = Depends(get_db),
    user: User = Depends(require_role("admin")),
) -> dict:
    """Block or unblock a ballot. Blocked voters keep their votes but cast no more."""
    voter = db.get(Voter, voter_id)
    if voter is None:
        raise HTTPException(status_code=404, detail="Voter not found")

    voter.blocked = payload.blocked
    voter.blocked_reason = payload.reason if payload.blocked else None
    audit.record(
        db,
        "voter.blocked" if payload.blocked else "voter.unblocked",
        actor=user,
        entity="voter",
        entity_id=voter.id,
        ip=client_ip(request),
        details={"email": voter.email, "reason": voter.blocked_reason},
    )
    db.commit()
    return {"voter": voting.serialize_voter(voter)}
