"""Commit Integrity — an advisory signal, never an automatic disqualification.

Axion fetches a submission's commit history and reports what fraction of it was
authored inside the event window. A repo whose history predates the event is
flagged for an admin to review.

Known limitations (deliberate, documented in MATH.md):
  * commit *author* dates are client-controlled and can be rewritten;
  * squash merges and force pushes collapse history;
  * commits are not lines of code.
So the output is `flagged_for_review`, not `cheating`.
"""
from __future__ import annotations

import hashlib
import re
from datetime import datetime, timezone
from typing import Optional
from urllib.parse import urlparse

import httpx

from .config import settings

GITHUB_API = "https://api.github.com"
MAX_PAGES = 3
PER_PAGE = 100
FLAG_BELOW_PCT = 50.0

# scp-style remotes: git@github.com:owner/repo.git
_GIT_URL_RE = re.compile(
    r"^(?:git@|ssh://git@|git://)github\.com[:/](?P<owner>[\w.-]+)/(?P<repo>[\w.-]+?)(?:\.git)?/?$",
    re.IGNORECASE,
)
# https://github.com/owner/repo, with or without the scheme
_HTTP_URL_RE = re.compile(
    r"^(?:https?://)?(?:www\.)?github\.com/(?P<owner>[\w.-]+)/(?P<repo>[\w.-]+?)(?:\.git)?/?$",
    re.IGNORECASE,
)


def parse_repo(repo_url: str) -> Optional[tuple[str, str]]:
    """Accepts https URLs, scp-style git remotes and bare `owner/repo`."""
    if not repo_url:
        return None
    candidate = repo_url.strip()

    for pattern in (_GIT_URL_RE, _HTTP_URL_RE):
        match = pattern.match(candidate)
        if match:
            return match.group("owner"), match.group("repo")

    # Bare "owner/repo" shorthand. Guarded so hostnames and remotes that failed
    # the patterns above are rejected rather than mangled into an owner name.
    if "@" not in candidate and ":" not in candidate and candidate.count("/") == 1:
        owner, repo = (part.strip() for part in candidate.split("/"))
        repo = repo.removesuffix(".git")
        if owner and repo:
            return owner, repo
    return None


def _parse_dt(value: Optional[str]) -> Optional[datetime]:
    if not value:
        return None
    try:
        parsed = datetime.fromisoformat(value.replace("Z", "+00:00"))
    except ValueError:
        return None
    if parsed.tzinfo is None:
        parsed = parsed.replace(tzinfo=timezone.utc)
    return parsed


def _result(**kwargs) -> dict:
    base = {
        "source": "unavailable",
        "repo": None,
        "commits_scanned": 0,
        "commits_in_window": 0,
        "pct_in_window": None,
        "repo_created_at": None,
        "flagged": False,
        "reason": "",
    }
    base.update(kwargs)
    return base


def _mock_result(owner: str, repo: str) -> dict:
    """Deterministic synthetic history so demos work with no network or token.

    Repos named with 'legacy'/'prebuilt'/'pre-existing' deliberately land below
    the flag threshold so the review queue can be demonstrated on stage.
    """
    digest = int(hashlib.sha256(f"{owner}/{repo}".lower().encode()).hexdigest(), 16)
    spread = (digest % 1000) / 1000.0
    name = repo.lower()
    if any(key in name for key in ("legacy", "prebuilt", "pre-existing", "template")):
        pct = round(16.0 + spread * 14.0, 2)
        reason = "Mock history: repository appears to predate the event window."
    else:
        pct = round(58.0 + spread * 40.0, 2)
        reason = "Mock history: majority of commits fall inside the event window."
    scanned = 40 + digest % 160
    in_window = int(round(scanned * pct / 100.0))
    return _result(
        source="mock",
        repo=f"{owner}/{repo}",
        commits_scanned=scanned,
        commits_in_window=in_window,
        pct_in_window=pct,
        repo_created_at=None,
        flagged=pct < FLAG_BELOW_PCT,
        reason=reason,
    )


def check_commit_integrity(
    repo_url: str,
    *,
    event_start: datetime,
    event_end: datetime,
    token: Optional[str] = None,
    mock: Optional[bool] = None,
    client: Optional[httpx.Client] = None,
    max_pages: int = MAX_PAGES,
) -> dict:
    parsed = parse_repo(repo_url)
    if not parsed:
        return _result(reason="Not a recognizable GitHub repository URL.")
    owner, repo = parsed

    use_mock = settings.mock_github if mock is None else mock
    if use_mock:
        return _mock_result(owner, repo)

    token = token or settings.github_token
    headers = {"Accept": "application/vnd.github+json", "User-Agent": "axion-integrity"}
    if token:
        headers["Authorization"] = f"Bearer {token}"

    owns_client = client is None
    client = client or httpx.Client(timeout=10.0)
    try:
        repo_created_at = None
        try:
            repo_resp = client.get(f"{GITHUB_API}/repos/{owner}/{repo}", headers=headers)
            if repo_resp.status_code == 200:
                repo_created_at = repo_resp.json().get("created_at")
        except httpx.HTTPError:
            pass

        commits: list[dict] = []
        for page in range(1, max_pages + 1):
            response = client.get(
                f"{GITHUB_API}/repos/{owner}/{repo}/commits",
                params={"per_page": PER_PAGE, "page": page},
                headers=headers,
            )
            if response.status_code == 404:
                return _result(
                    repo=f"{owner}/{repo}",
                    reason="Repository not found or private (grant the Axion token access).",
                )
            if response.status_code in (403, 429):
                return _result(
                    repo=f"{owner}/{repo}",
                    reason="GitHub API rate limit reached — add GITHUB_TOKEN and retry.",
                )
            response.raise_for_status()
            batch = response.json()
            if not isinstance(batch, list) or not batch:
                break
            commits.extend(batch)
            if len(batch) < PER_PAGE:
                break
    except httpx.HTTPError as exc:  # pragma: no cover - network path
        return _result(repo=f"{owner}/{repo}", reason=f"GitHub API error: {exc}")
    finally:
        if owns_client:
            client.close()

    if not commits:
        return _result(
            repo=f"{owner}/{repo}",
            repo_created_at=repo_created_at,
            reason="No commit history returned (empty repository?).",
        )

    in_window = 0
    first_commit: Optional[datetime] = None
    for commit in commits:
        authored = _parse_dt((commit.get("commit") or {}).get("author", {}).get("date"))
        if authored is None:
            continue
        if first_commit is None or authored < first_commit:
            first_commit = authored
        if event_start <= authored <= event_end:
            in_window += 1

    pct = round(100.0 * in_window / len(commits), 2)
    flagged = pct < FLAG_BELOW_PCT
    reason = (
        f"{in_window}/{len(commits)} sampled commits fall inside the event window "
        f"({pct}%)."
    )
    if repo_created_at and _parse_dt(repo_created_at) and _parse_dt(repo_created_at) < event_start:
        reason += " Repository was created before the event started."
    if first_commit and first_commit < event_start:
        reason += " Earliest sampled commit predates the event."

    return _result(
        source="github",
        repo=f"{owner}/{repo}",
        commits_scanned=len(commits),
        commits_in_window=in_window,
        pct_in_window=pct,
        repo_created_at=repo_created_at,
        flagged=flagged,
        reason=reason,
    )
