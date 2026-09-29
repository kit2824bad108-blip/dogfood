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

Twenty-two tables, no ORM relationships (explicit joins, so there is no lazy-loading surprise). The full column
list, ERD and constraint rationale live in [DATA-MODEL.md](./DATA-MODEL.md).

| Table | Purpose | Notable constraints |
| ----- | ------- | ------------------- |
| `users` | Every human, discriminated by `role` ∈ {`admin`, `judge`, `participant`} | unique `email`, unique `github_id`; `password_hash` nullable so OAuth-only users exist without one |
| `teams` | A competing team | unique `name`, unique `invite_code` |
| `team_members` | Membership | **unique `user_id`** — one team per person |
| `tracks` | A competition category (AI, Web3, DevTools…) | unique `name`, unique `slug` |
| `prizes` | A prize, per track or overall (`track_id` null) | `rank` orders them |
| `submissions` | One per team, with track, draft/submitted state and Commit Integrity fields | **unique `team_id`**, indexed `status` |
| `assignments` | Judge × submission pairs | unique `(judge_id, submission_id)` |
| `rubrics` | Weighted criteria as JSON, one active row | only one `is_active` at a time |
| `scores` | A judge's staged verdict on one submission | unique `(judge_id, submission_id)`, `rubric_id` for provenance |
| `score_criteria` | Per-criterion values behind a verdict | unique `(score_id, key)` |
| `audit_logs` | Append-only event trail | `action` and `created_at` indexed |
| `import_batches` | One row per applied fixture import, with its counts and its source | `created_at` indexed; an import is always recorded, including a dry run |
| `duplicate_reviews` | An organiser's decision about a suspected duplicate pair | unique `(submission_id, candidate_id)` |
| `voters` | A community voter: an address and a **SHA-256 digest** of its ballot token | unique `email`, unique `token_hash` |
| `votes` | One immutable ballot, strikable but never editable | **unique `(voter_id, submission_id)`**, `score` 1–5, `status` ∈ {cast, struck} |
| `comments` | A project comment, hidden rather than deleted | `status` ∈ {visible, hidden}, `created_at` indexed |
| `throttle_events` | Rate-limit attempts, in the database so they hold across workers | index on `(bucket, key, at_epoch)` |
| `webhook_endpoints` | A registered receiver and its signing secret | `active` indexed; `events` empty means everything |
| `webhook_deliveries` | The signed outbox: one row per event per endpoint | `status` ∈ {pending, delivered, failed, dead} |
| `participation_records` | A signed attestation, stored **as signed** | unique `code`; revocation is a column, never an edit |
| `invite_tokens` | One-time judge onboarding links, stored as SHA-256 digests | unique `token_digest`; single-use, and expiring |
| `event_settings` | The organiser's clock: the event's own dates, editable while it runs | **at most one row** (`CHECK (id = 1)`), plus a `revision` for optimistic concurrency |

### The staged score row

`technical_score` and `presentation_score` live on the **same row** with separate timestamps
(`technical_submitted_at`, `presentation_submitted_at`). The row is created on the first write and updated
on later ones. "Presentation unlocked" is derived, not stored: it means `technical_score IS NOT NULL` for
that judge and submission. Deriving it means the lock cannot drift out of sync with the data.

### The weighted rubric collapses to one integer

Judges score each rubric criterion (`score_criteria`), and `services.weighted_technical_score` turns those
values into the single integer stored in `scores.technical_score`. Both are persisted: the parts so an
organiser can ask how a project did on code quality specifically, and the whole because the Z-score engine
consumes one number per verdict. Re-weighting the rubric inserts a new active row and never rewrites a
verdict already filed — `scores.rubric_id` records which weights produced which score.

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
| Auth | `GET /api/auth/me`, `GET /api/auth/status`, `POST /api/auth/{register,login,logout}`, `POST /api/auth/dev-login` *(offline mode)*, `GET /api/auth/github/{login,callback}` |
| Public | `GET /api/event`, `GET /api/gallery?q=&track=` |
| Teams | `POST /api/teams`, `POST /api/teams/join`, `GET /api/teams/me`, `GET /api/teams` *(admin)* |
| Submissions | `POST /api/submissions` *(draft or submitted)*, `GET /api/submissions/me`, `GET /api/submissions` *(admin)*, `POST /api/submissions/{id}/recheck` *(admin)* |
| Judging | `GET /api/judging/assignments`, `GET /api/judging/submissions/{id}`, `GET /api/judging/submissions/{id}/presentation`, `POST /api/judging/scores` *(accepts per-criterion values)* |
| Admin | `GET /api/admin/{overview,leaderboard,flagged,audit,judging-progress,tracks,rubric}`, `POST /api/admin/{judges,tracks,prizes,rubric,assignments/backfill}`, `POST /api/admin/archive[/markdown]` |
| Export | `GET /api/admin/export/{leaderboard,scores,judging-progress}.csv` — every export writes an `export.csv` audit entry |
| Community (T3) | `POST /api/vote/register`, `GET|POST /api/vote/verify`, `GET /api/vote/ballot`, `POST /api/vote[/ballot]`, `GET /api/vote/results`, `GET|POST /api/submissions/{id}/comments`, `DELETE /api/comments/{id}`, and the organiser tools under `/api/admin/{community,votes/{id}/strike,comments/{id}/moderate,voters/{id}/block}` |
| Event clock | `GET|PATCH|DELETE /api/admin/event`, `GET /api/admin/event/history` — the organiser moving the deadline, doing it knowingly, and doing it on the record |
| Webhooks (T4) | `GET|POST /api/admin/webhooks`, `PATCH|DELETE /api/admin/webhooks/{id}`, `POST /api/admin/webhooks/{id}/test`, `POST /api/admin/webhooks/dispatch`, `GET /api/admin/webhooks/deliveries[/{id}]`, `POST …/deliveries/{id}/redeliver` |
| Records (T4) | `POST /api/admin/records/issue`, `GET /api/admin/records`, `POST /api/admin/records/{code}/revoke`, and the public `GET /api/records[/{code}][/certificate]` |
| Bundle (T4) | `GET /api/admin/bundle/export`, `POST /api/admin/bundle/{validate,import}` — the import is a **dry run** unless asked otherwise |
| Embed (T4) | `GET /api/embed/gallery`, `GET /api/embed/gallery/snippet` — public, read-only, self-contained |
| Meta | `GET /api/health[/live,/ready]`, `GET /api/docs`, `GET /api/openapi.json`, `GET /api/redoc` |

Responses are plain dicts assembled in the routers; only request payloads are validated with Pydantic.
Responses are read-only projections with no lazy loading, and the extra layer buys nothing here.

### The public surface is deliberate

`/api/event` and `/api/gallery` are the only unauthenticated reads with substance. The gallery returns
submitted projects — title, team, summary, repository and docs — and **never** returns `demo_url` or
`video_url`. Publishing the presentation tier anonymously would make the blind gate pointless, since a
judge could read it from a second door. See THREAT-MODEL.md.

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

## The organiser's clock

The event's own dates — when submissions open and close, when the community ballot runs — used to live only
in configuration, read once at import. That made the deadline a fact about the *process*: extending it meant
editing an environment variable and restarting a container, and nothing in the database recorded that it had
moved. `app/eventconfig.py` is the answer to that, and it is one small module with one job.

- **Two inputs, one resolver.** The deployment's configuration (`EVENT_*` / `VOTING_*`, or the imported
dataset's window under `EVENT_SOURCE=fixtures`) is the default; a single row in `event_settings` is the
organiser's override. Every reader — `services.event_window()`, `voting.voting_window()`, the record key's
publication rule, the archive, the bundle export, `/api/event`, `/api/health` — resolves through
`eventconfig`, so a deployment cannot have a submission form and a certificate that disagree about the event.
- **Empty means "configured", which is what keeps a cold boot honest.** A fresh `docker compose up` has no
  row, so a fixture-mode deployment still starts *closed* and the acceptance brief's "a closed event refuses
  submissions" check passes with nobody visiting a console. Moving the deadline is a deliberate act, not the
  default state of the software.
- **A change is partial, previewed and attributed.** PATCH moves the fields it names and leaves the rest
  alone, because the row is a complete statement of the window rather than a set of overrides to re-derive.
  The response describes the consequence in sentences — submissions open before and after, whether the record
  key and the community tally become public, how many projects already sit after the new deadline — and every
  write lands in the append-only audit trail and on the webhook queue (`event.settings_updated`).
- **Zero rows or one, enforced by the database.** `CHECK (id = 1)` makes two deadlines unrepresentable,
  including for a script or a `psql` session. `revision` makes two organisers editing at once a **409**
  instead of the last save silently winning, and `DELETE /api/admin/event` hands the clock back to
  configuration: the configured window was never overwritten, so the reset cannot fail to find it.
- **The read path is the session's.** `active(db)` resolves through the session the caller already has, so a
  change is visible to the next request with no cache to reason about; the module-level fallback exists only
  for callers with no session at all (startup, an offline script), and is primed at boot and after every
  write.

## The community surface (T3)

`app/voting.py` owns the rules and `app/routers/community.py` owns the doors, for the same reason `zscore.py`
is separate from the judging router: the rules are asked about from several places and must not be able to
differ between them.

- **A voter is not a user.** Voting is email-gated, so `Voter` holds an address and a token digest, and the
  ballot cookie is the proof. This is why the community router authenticates differently from the rest of the
  API, and why it is its own module rather than four endpoints bolted onto judging.
- **Ballot order is a pure function of (voter, project).** `voting.ballot_order` sorts by
  `hmac-sha256(secret, token_digest + ':' + submission_id)`. Stable across reloads, different per voter, and
  reproducible by an organiser — three properties that a `random.shuffle()` per request cannot have, and the
  ballot payload names the method so the property is checkable from outside.
- **The window is its own clock.** The community window is derived from the event window with a tail
  (default: three days past the submission deadline), so an event can stop taking work and keep taking votes;
  `voting_window()` is the single place that decides the phase, and every public endpoint asks it rather than
  reading a flag. Both windows are resolved by `app/eventconfig.py` — see *The organiser's clock* below — so
  moving a date in the console moves it for the ballot too, and the tally becomes public exactly when the
  organiser says the crowd has stopped counting.
- **The tally is cast-votes-only.** `voting.aggregate` never mixes struck votes into the headline figures;
  `include_struck=True` *adds* a separate `struck_votes` number instead of changing the average, because a
  figure that changes depending on which request produced it is not a tally.
- **Rate limits live in the database.** `throttle_events` plus `app/throttle.py` counts attempts per bucket so
  the limiter keeps applying when a deployment runs more than one worker. An in-process counter would
  silently stop working at exactly the moment it starts to matter.

## Outbound and reproducible (T4)

Three engines, each with one job.

- **`app/webhooks.py` — the outbox.** `emit()` inserts one delivery row per subscribed endpoint *inside the
  caller's transaction*, so a receiver that is down cannot fail a participant's write. The envelope is built
  once and stored, which is what makes a retry byte-identical and lets a receiver dedupe on
  `X-Axion-Delivery`; `sign()` and `verify_signature()` are the two halves of one algorithm, kept together
  so the documentation cannot drift from the code. `dispatch()` is the only thing that sends anything — there
  is no worker, by design — and it takes an injectable sender so the retry policy is testable without a
  socket.
- **`app/records.py` — signed records and certificates.** The signature is over `record.payload` **as
  stored**, not over a payload reassembled from columns: rebuilding it would make verification depend on how
  SQLite and Postgres each round-trip a datetime, which is a defect that only appears when a stranger checks
  a certificate on another machine. `key_publication()` decides from the clock whether the symmetric key may
  be published, and says why either way.
- **`app/bundle.py` — the whole event, in and out.** Export is ordered by primary key throughout so two
  exports of an unchanged event are byte-identical and the checksum is worth publishing. Import matches every
  row on the key it is *identified* by, so it is idempotent, and it refuses a bundle whole rather than
  applying half of one.

`app/routers/embed.py` is deliberately tiny and read-only: a sponsor's page embeds a gallery, and an embed
that could write anything would be a second front door into an event.

## Frontend

Next.js 15 App Router, React 19, Tailwind with hand-written primitives in `web/components/ui` — no component
library, because six primitives is less code than one dependency.

- **Theming.** Every colour is a token in `web/app/globals.css`, with a light set on `:root` and a dark set
  on `.dark`. `theme-provider.tsx` keeps `localStorage` and `prefers-color-scheme` in sync; a blocking inline
  script in `layout.tsx` applies the class before first paint, so there is no flash. `theme-toggle.tsx`
  renders both icons and lets CSS pick one, which means the right icon is correct before hydration.
- **Settings menu.** `settings-menu.tsx` is a fixed bottom-left control (theme, event window, environment
  flags, API docs, sign out). It is reachable from every route, including the public ones.
- **Live server state.** Pages fetch from `/api/*` with `cache: "no-store"`; nothing is duplicated server
  side, so the API stays the only source of truth. `RequireAuth` handles the redirect-and-role dance in one
  place.

## Dataset import, and the acceptance checker

Two pieces exist because a real event is messier than a curated demo, and because a claim about a running
system should be mechanically checkable.

**Import.** `api/app/fixtures.py` is split so the interesting part is testable without a database:

- `validate()` reports structural problems (unknown teams, duplicate ids, unparseable timestamps) and an
  import with **any** invalid record is refused whole — half an event is worse than none;
- `diagnose()` is pure: counts, incomplete batches, zero-variance judges, single-verdict judges, duplicates,
  nulls, string ids, UTC normalisation, and whether the imported window is closed;
- `apply_fixture()` writes it, idempotent on `source_ref`, and records an `import_batches` row either way.

An import never reopens a deadline. `EVENT_SOURCE=fixtures` makes the window come from the dataset, which is
the only way a *closed* fixture event can be demonstrated rather than described, and `/api/health` publishes
the window that was actually resolved.

**Timestamps.** SQLite (the offline path) returns naive datetimes even from `DateTime(timezone=True)`
columns, while Postgres returns aware ones. `api/app/timeutil.py` is the single place that decides what a
stored datetime means — a naive one is UTC — and every serializer emits through `iso()`, so a browser can
never read a deadline as local time by accident.

**Two dialects, one loader.** The organisers' `fixtures.json` and Axion's own generated dataset share a job
and almost no field names: theirs gives a project `team`/`track` (string ids), a review a `criteria` map, a
team a list of member *addresses* and a judge a list of *tracks*; ours gives a project `team_id`/`track_id`, a
review a verdict, teams a participant list and judges explicit assignments. `api/app/fixture_dialects.py`
translates theirs into the canonical shape the importer already reads, so `validate`, `diagnose`,
`duplicate_candidates` and `apply_fixture` stay the single implementation of "what a dataset is" and the
translation is one file a reviewer can read. Two consequences are deliberate: a participant's display name
is derived from their address because the file carries no names, and a verdict is the app's own
weight-normalized mean of the file's criteria rather than a second opinion invented by the importer.

**The checker.** There are two, and only one of them is ours to interpret.

`.dogfood.toml` at the root is the **organisers'** manifest, read by **their** `run.py` (committed verbatim):
base url, tier claim, four literal header strings and five routes. The headers are literal because their
checker never logs in and contains no code that could fetch a credential. `api/app/access.py` issues and
gates them: four fixed cookies, accepted only while the deployment has declared itself a demo, so a live
event answers 401 to all four. `api/tests/test_manifest_contract.py` checks the other direction — that every
route the manifest names is one the API serves, that the four headers resolve to the roles the manifest
implies, that `peer_scores` names judge A and *not* judge B, and that the numbers in `[dataset]` are the ones
`fixtures.json` holds. A rename in one place and not the other fails in CI.

`api/scripts/selfcheck.toml` is **Axion's own** manifest, read by `api/scripts/dogfood_check.py` with the
standard library: forty checks rather than their seven, including the blind gate, all three CSV
exports, the Z-score leaderboard, both sides of four role boundaries, the community ballot and its hidden
tally, the organiser's clock agreeing with the public one, the webhook outbox, signed records, the bundle
and the embed. It fetches signed bearer tokens from
`GET /api/dev/checker-headers` so it stays valid on any machine, and the one write it attempts is *skipped*
rather than sent when the event is open, so a read-only run cannot mutate what it measures. It also prints
**claimed versus observed** for each tier and applies the same ladder rule their `run.py` does — a tier counts
only if every check of its own passed *and* every tier below it did — so a claim in the report can never be
more generous than the evidence under it.

They are separate files and separate reports on purpose. Forty assertions of ours folded into the artefact
the organisers read would blur the only line that matters in an acceptance report: who ran it.
`api/scripts/acceptance.py` remains the third, tier-by-tier tool (T0–T4 plus bonus claims), run against the
**demo** dataset because half of it asks what only an open event can answer.

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
- `test_end_to_end.py` — the seeded event, driven through the API: the crafted rank flip, grader
  calibration, the archive bundle and role boundaries.
- `test_event_features.py` — offline dev login, drafts and promotion, the server-side deadline (with a
  frozen window), the public gallery's withholding rule, tracks and prizes, weighted rubrics, judge
  progress and the CSV exports.

The event window in `tests/conftest.py` is computed relative to *now*: a hard-coded date would silently
start exercising the closed-window path instead of the open one.

`api/scripts/acceptance.py` is the live counterpart — it drives a running instance over HTTP tier by tier
and writes `acceptance-report.axion.txt` at the repository root. (`acceptance-report.txt` is a different
artefact: the organisers' own `run.py` reading `.dogfood.toml`, which nothing in this repository writes.)

Two suites exist because they answer different questions. `dogfood_check.py` is a manifest walk: every
assertion is declared in TOML and the script contains no route list of its own, so the check and the contract
cannot drift. `acceptance.py` is a *narrative* walk: it registers a real participant, files a real verdict,
casts a real vote, posts a real comment, issues and revokes a real record, and dispatches a real outbound
delivery to this deployment's own route — because those are paths only observable end to end.

SQLite is used only for speed; the models avoid Postgres-specific column types so the same code paths run
against both. The append-only trigger is exercised by the migration, which only runs on Postgres.
