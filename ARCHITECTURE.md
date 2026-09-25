# Axion architecture

## Topology

```
browser ──▶ :3000  Next.js (App Router, Tailwind, shadcn-style components)
                    │  rewrites /api/*  ──▶  :8000  FastAPI (SQLAlchemy, Alembic)
                    └────────────────────────▶        │
                                                      ▼
                                              :5432  PostgreSQL 16
```

Three services in one `docker compose up`:

| Service | Image | Role |
| ------- | ----- | ---- |
| `db` | `postgres:16-alpine` | State, with a named volume and a `pg_isready` healthcheck |
| `api` | `python:3.12-slim` | Owns the database, all auth and all math. Entrypoint runs `alembic upgrade head`, optionally seeds, then starts uvicorn. |
| `web` | `node:20-alpine` | Presentation only. `next build` then `next start`. |

**The browser never talks to port 8000.** Every call goes to `/api/*` on port 3000 and is rewritten by
Next to the API service. That keeps the session cookie same-origin (no CORS or `SameSite` negotiation in
the happy path) and hides the API from the client. The API still ships a permissive-for-localhost CORS
policy so `:8000` can be poked directly during development.

One consequence worth knowing: **Next.js resolves rewrite destinations at build time** and serialises them
into `.next/routes-manifest.json`, so `API_INTERNAL_URL` cannot be changed at runtime. The web image
therefore receives it as a build argument (`--build-arg API_INTERNAL_URL=http://api:8000`, wired up in
`docker-compose.yml`), while local `npm run dev` / `npm start` falls back to `http://localhost:8000`.

The GitHub OAuth callback follows the same reasoning: it is registered and issued as
`{WEB_URL}/api/auth/github/callback` rather than the API origin, so GitHub returns the browser to the origin
the app is served from and the session cookie is set there.

The web container depends only on `api` starting; the API's entrypoint handles migration ordering against
a healthy database.

## Why the database is owned by one service

Postgres is reached only through SQLAlchemy. The Next.js app performs no database access at all — it is a
view over the API. One schema, one migration history, one place where invariants are enforced.

## Schema

Seven tables, no ORM relationships (explicit joins, so there is no lazy-loading surprise).

| Table | Purpose | Notable constraints |
| ----- | ------- | ------------------- |
| `users` | Every human, discriminated by `role` ∈ {`admin`, `judge`, `participant`} | unique `email`, unique `github_id`; `password_hash` nullable so OAuth-only users exist without one |
| `teams` | A competing team | unique `name`, unique `invite_code` |
| `team_members` | Membership | **unique `user_id`** — one team per person |
| `submissions` | One per team, with Commit Integrity fields | **unique `team_id`** |
| `assignments` | Judge × submission pairs | unique `(judge_id, submission_id)` |
| `scores` | A judge's staged verdict on one submission | unique `(judge_id, submission_id)` |
| `audit_logs` | Append-only event trail | `action` and `created_at` indexed |

### The staged score row

`technical_score` and `presentation_score` live on the **same row** with separate timestamps
(`technical_submitted_at`, `presentation_submitted_at`). The row is created on the first write and updated
on later ones. "Presentation unlocked" is derived, not stored: it means `technical_score IS NOT NULL` for
that judge and submission. Deriving it means the lock cannot drift out of sync with the data.

### Scoring is computed, never stored

Raw verdicts are the only scores persisted. Z-scores are recomputed on every leaderboard request from
`zscore.py`. A verdict added later therefore never leaves stale normalized values behind — the property
that makes incremental scoring safe to reason about.

### Append-only audit

`audit_logs` is written through `app/audit.py`, which records the action, actor, entity, client IP (from
`X-Forwarded-For`, falling back to the socket address) and a JSON detail blob.

The migration installs a Postgres trigger:

```sql
CREATE TRIGGER axion_audit_logs_immutable
BEFORE UPDATE OR DELETE ON audit_logs
FOR EACH ROW EXECUTE FUNCTION axion_audit_logs_immutable();
```

which raises on any attempt to rewrite history. The application layer has no update or delete path for
this table either, so the constraint is defence in depth rather than the only guard.

## Authentication and authorization

One identity model, two front doors:

| Path | Who | Mechanism |
| ---- | --- | --------- |
| GitHub OAuth | Participants | `GET /api/auth/github/{login,callback}`; state is a signed 10-minute cookie, then the GitHub profile is fetched and the user upserted by `github_id` or email |
| Email + password | Judges, organisers, and participants who prefer it | PBKDF2-HMAC-SHA256, 200,000 iterations, per-password salt |
| Invite code | Teammates | `POST /api/teams/join` |

Both converge on a single **HMAC-SHA256 signed session cookie** (`axion_session`, `HttpOnly`, `SameSite=Lax`,
`Secure` when `COOKIE_SECURE=true`). The payload carries the user id and an expiry; there is no server-side
session table to keep in sync.

`security.py` uses only the standard library — no `passlib`, no `bcrypt` build step, no version drift
between the password hasher and the Python image.

Authorization is declarative: `Depends(require_role("admin", "judge"))`. Roles are checked against the
database row on every request, so a role change takes effect immediately without reissuing sessions.

## The judging flow, and where the blind boundary lives

```
judge opens assignment
        │
        ▼
GET /api/judging/submissions/{id}          → repo_url, docs_url, summary, integrity flag
        │                                     demo_url and video_url are ABSENT
        ▼
POST /api/judging/scores {technical_score} → row written, audit entry recorded
        │
        ▼
GET  /api/judging/submissions/{id}/presentation   → 200, demo/video returned
```

The boundary is enforced in three places, all server-side:

1. `GET .../presentation` returns **403** while `technical_score IS NULL` for that judge.
2. `POST /api/judging/scores` refuses a `presentation_score` unless a technical score already exists
   (or is being written in the same request).
3. `GET /api/judging/assignments` never selects the presentation columns at all.

The blur and the locked placeholder in the UI are decoration on top of that. A judge who opens devtools
and calls the endpoint directly gets the same 403 as the greyed-out card.

Admins are exempt from the gate so they can audit the process, and every write — including a *change* to an
existing verdict (`score.technical_modified`, with previous and new values) — lands in the audit trail.

## API surface

| Area | Endpoints |
| ---- | --------- |
| Auth | `GET /api/auth/me`, `GET /api/auth/status`, `POST /api/auth/{register,login,logout}`, `GET /api/auth/github/{login,callback}` |
| Teams | `POST /api/teams`, `POST /api/teams/join`, `GET /api/teams/me`, `GET /api/teams` *(admin)* |
| Submissions | `POST /api/submissions`, `GET /api/submissions/me`, `GET /api/submissions` *(admin)*, `POST /api/submissions/{id}/recheck` *(admin)* |
| Judging | `GET /api/judging/assignments`, `GET /api/judging/submissions/{id}`, `GET /api/judging/submissions/{id}/presentation`, `POST /api/judging/scores` |
| Admin | `GET /api/admin/{overview,leaderboard,flagged,audit}`, `POST /api/admin/judges`, `POST /api/admin/assignments/backfill`, `POST /api/admin/archive[/markdown]` |
| Meta | `GET /api/health` |

Responses are plain dicts assembled in the routers; only request payloads are validated with Pydantic.
Responses are read-only projections with no lazy loading, and the extra layer buys nothing here.

## Commit Integrity

`app/github.py` parses `owner/repo` from a repository URL, then walks up to three pages of commit history
(`per_page=100`) plus the repository's `created_at`, counting commits authored inside the configured event
window.

- Below **50%** in-window commits → `flagged_for_review`.
- Private repositories, rate limits and malformed URLs return a structured `source: "unavailable"` result
  with a reason instead of raising; a broken GitHub call must never block a team's submission.
- `MOCK_GITHUB=true` produces a deterministic synthetic history derived from the repository name, so demos
  and offline judging need no network. Names containing `legacy`, `prebuilt`, `pre-existing` or `template`
  deliberately land below the threshold so the review queue can be demonstrated.

## Archive

`POST /api/admin/archive` builds a self-contained bundle: version, timestamp, event window, methodology
(including the explicit statement that ranking is technical-only), totals, ranked results with commit
integrity, and per-verdict z-scores with the judge statistics used to produce them. `POST
/api/admin/archive/markdown` returns the same content as a `RESULTS.md` page suitable for GitHub Pages.

Archiving mutates nothing. Downloading it is safe mid-event, and the database stays up until an operator
chooses to spin it down.

## Testing strategy

`api/tests/` runs against in-memory SQLite with `MOCK_GITHUB=true`, so the suite needs no Postgres and no
network:

- `test_zscore.py` — the math, in isolation: the harsh-6/generous-8 claim, zero-variance judges,
  single-verdict shrinkage, ties, clamping, empty input.
- `test_blind.py` — the blind boundary called directly, bypassing the UI: 403s, no link leakage in the
  assignment payload, audit entries for writes and modifications.
- `test_github.py` — URL parsing, mock determinism, real-mode accounting with a stubbed HTTP client,
  private-repo and rate-limit paths.
- `test_auth.py` — registration, login, sessions, role guards, team lifecycle, submission validation.

SQLite is used only for speed; the models avoid Postgres-specific column types so the same code paths run
against both. The append-only trigger is exercised by the migration, which only runs on Postgres.
