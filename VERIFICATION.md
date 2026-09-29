# Verification record

"It works" is a claim, and a claim should be checkable. This file records what was actually executed on the
build machine, in order, with the results as observed — and, just as importantly, what was **not** executed
and why.

Build environment: Windows with Git Bash; Python 3.12.10 (`api/.venv`); Node v24.16.0 / npm 11.13.0.
Phase A3 added Docker Desktop 29.8.0 (Linux engine, WSL2) and a local PostgreSQL 16.10, so the Docker and
PostgreSQL paths recorded below were executed rather than reviewed. `make` is still not installed, so that
one row stays in "not executed".

## Executed

Phases are cumulative and read in order: A1–A2 are the original build, A3 added PostgreSQL, Docker and CI,
A4 reconciled the repository with the organisers' published files, **A5 built the T3 and T4 surfaces**, A6
rebuilt the whole stack from a clean volume and proved the offline network, and **A7 made the event's own
clock an organiser control**. A number that changes between phases (the fast suite's count, the route count,
the check count) is restated in each phase that changed it rather than edited in place, so the record shows
what was true when it was observed.

| # | Command | Observed result |
| - | ------- | --------------- |
| 1 | `cd api && ./.venv/Scripts/python.exe -m pytest -q` | **125 passed**, 1 warning, 242 s. The warning is a Starlette deprecation notice about `anyio.abc.BlockingPortal`, not a failure. 102 ran before Phase A1; the 23 added are the checker-header, role-isolation, validator-CLI and two-clean-import determinism tests |
| 2 | `cd web && npm run typecheck` | Clean (`tsc --noEmit`) |
| 3 | `cd web && npm run build` | Clean: 9 routes, 103 kB shared first-load JS |
| 4 | Fixture-mode boot, then the manifest-driven check → `acceptance-report.txt` | **18 passed, 0 failed, 0 skipped** — the committed report artefact; it predates the Phase A1 check added in row 7 |
| 5 | Demo-mode boot, then the tier suite → `acceptance-report.axion.txt` | **27 passed, 0 failed, 3 skipped** |
| 6 | `api/.venv/Scripts/python.exe api/scripts/build_fixtures.py` | Regenerates `fixtures.json` from a fixed seed (deterministic) |
| 7 | Phase A1: boot through `DOGFOOD_FIXTURE_MODE=true` alone (no `SEED_MODE`, no `EVENT_SOURCE`), then `dogfood_check.py .dogfood.toml` against that instance on :8010 | **19 passed, 0 failed, 0 skipped** — the added check is the participant surface (`GET /api/submissions/me` as the participant header). The committed `acceptance-report.txt` was deliberately not rewritten in Phase A1 |
| 8 | `api/.venv/Scripts/python.exe api/scripts/validate_fixtures.py` | Exit 0, `RESULT: VALID`: 40 projects / 12 judges / 40 teams / 48 participants / 131 reviews / 138 assignments, with the edge cases named (`judge_11`, `judge_12`, 2 duplicate candidates, `proj_018`, 7 assignments without verdicts); SHA-256[:12] `b5c9da177cab` |
| 9 | `cd api && ./.venv/Scripts/python.exe -m pytest tests/test_fixture_determinism.py -q` | **2 passed** — two clean imports (one via the alias, one via explicit variables) produced equivalent databases, row ids and relationships included |

The exact commands behind rows 4 and 5:

```bash
# 4 — the fixture dataset: 40 projects, 12 judges, and an event that has already closed
rm -f /c/tmp/axion-fixtures.db
cd api && EVENT_START= EVENT_END= SEED_MODE=fixtures EVENT_SOURCE=fixtures \
  DATABASE_URL="sqlite+pysqlite:////tmp/axion-fixtures.db" \
  MOCK_GITHUB=true SEED_DEMO=true EVENT_NAME="Axion Fixture Hackathon" \
  ./.venv/Scripts/python.exe -m app.seed
cd api && EVENT_START= EVENT_END= SEED_MODE=fixtures EVENT_SOURCE=fixtures \
  DATABASE_URL="sqlite+pysqlite:////tmp/axion-fixtures.db" \
  MOCK_GITHUB=true SEED_DEMO=true EVENT_NAME="Axion Fixture Hackathon" \
  ./.venv/Scripts/python.exe -m uvicorn app.main:app --port 8000 &
cd .. && AXION_API_URL=http://127.0.0.1:8000 \
  api/.venv/Scripts/python.exe api/scripts/dogfood_check.py .dogfood.toml --out acceptance-report.txt

# 5 — the crafted demo dataset the documentation's numbers come from
rm -f /c/tmp/axion-demo.db
cd api && DATABASE_URL="sqlite+pysqlite:////tmp/axion-demo.db" MOCK_GITHUB=true SEED_DEMO=true \
  EVENT_NAME="Axion Demo Hackathon" ./.venv/Scripts/python.exe -m app.seed
cd api && DATABASE_URL="sqlite+pysqlite:////tmp/axion-demo.db" MOCK_GITHUB=true SEED_DEMO=true \
  EVENT_NAME="Axion Demo Hackathon" ./.venv/Scripts/python.exe -m uvicorn app.main:app --port 8000 &
cd .. && AXION_API_URL=http://127.0.0.1:8000 \
  api/.venv/Scripts/python.exe api/scripts/acceptance.py --out acceptance-report.axion.txt
```

`EVENT_START=` / `EVENT_END=` are passed empty on purpose in row 4: a developer's `.env` may pin a window,
and an explicit value beats the fixture file by design, so clearing them is what lets `EVENT_SOURCE=fixtures`
supply the closed one.

```bash
# 7 — the same dataset through the one-flag alias (Phase A1), on a spare port
rm -f /c/tmp/axion-a1.db
cd api && DOGFOOD_FIXTURE_MODE=true DATABASE_URL="sqlite+pysqlite:////tmp/axion-a1.db" \
  EVENT_START= EVENT_END= EVENT_SOURCE= SEED_MODE= MOCK_GITHUB=true \
  ./.venv/Scripts/python.exe -m app.seed
cd api && DOGFOOD_FIXTURE_MODE=true DATABASE_URL="sqlite+pysqlite:////tmp/axion-a1.db" \
  EVENT_START= EVENT_END= EVENT_SOURCE= SEED_MODE= MOCK_GITHUB=true \
  ./.venv/Scripts/python.exe -m uvicorn app.main:app --port 8010 &
cd .. && AXION_API_URL=http://127.0.0.1:8010 \
  api/.venv/Scripts/python.exe api/scripts/dogfood_check.py .dogfood.toml

# 8 and 9 — the fixture file alone, and the two-clean-import test
api/.venv/Scripts/python.exe api/scripts/validate_fixtures.py
cd api && ./.venv/Scripts/python.exe -m pytest tests/test_fixture_determinism.py -q
```

`EVENT_SOURCE=` and `SEED_MODE=` are passed empty in row 7 for the same reason: it proves the alias alone
selects the dataset and the closed window, rather than a leftover value from this machine's `.env`.

Both reports name the commit they were generated from, and both currently cite `239c53b` — the commit they
were produced *from*, i.e. the parent of the commit that ships them. A report cannot embed the hash of a
commit that does not exist yet. Regenerating with `make acceptance` / `make acceptance-axion` always names
the then-current commit.

## What the checks observed

Not a summary of intent — these lines are from the reports themselves.

**Fixture dataset (`acceptance-report.txt`)**

- `phase closed; window 2026-08-01T00:00:00+00:00 → 2026-08-04T00:00:00+00:00` — the imported deadline is
  enforced, not described
- `stats {'teams': 40, 'submissions': 40, 'judges': 12, 'verdicts': 131, 'assignments': 138}`
- `40 projects, 4 tracks; ['demo_url', 'video_url'] withheld from anonymous callers`
- `403 on a live write attempt while the window is closed: 'The submission window is closed — no further
  edits are accepted'`
- `12 assignments; technical 11/12 filed (91.7%)` — an incomplete batch, visible as such
- `403 before this judge filed a technical verdict: 'Submit the technical evaluation before the presentation
  is unlocked'`
- `39 ranked projects; leader 'Wire Protocol Fuzzer' at 65.34 … 131 technical verdicts` — and 1 project
  unranked, because it has assignments and no verdicts
- `39 data rows, 15 columns` / `131 data rows, 16 columns` / `12 data rows, 9 columns` for the three exports
- `a header alone authenticates as admin@fixtures.axion.dev (admin), with no login round-trip`
- `declared fixtures.json → observed submissions=40, judges=12, teams=40, verdicts=131, assignments=138`

**Demo dataset (`acceptance-report.axion.txt`)**

- T0: anonymous access to all four admin routes → 401; admin session establishes
- T1: 3 tracks, 7 prizes, `phase=open`; gallery search and track filter; invite-code reuse refused; a draft
  stays hidden then promotes
- T2: `{'innovation': 9, 'code_quality': 5} -> stored verdict 6/10`; blind gate `403 → 200`; `52/77 verdicts
  (67.5%)`; three CSV exports
- T3: 22 audit entries, 9 distinct actions, 21 carrying a client IP; no update/patch/delete route exists
- BONUS: the rankings genuinely disagree — `Quiet Craft raw #7 (6.14) → Axion #5 (49.19)` versus
  `Flashy Demo raw #5 (6.4) → Axion #7 (46.63)`. The absolute values differ slightly from README.md because
  the suite's own mutating checks add a judge and a project before it reaches that line; the *direction* is
  what it verifies, and the untouched figures are pinned by
  `api/tests/test_demo_numbers.py::test_the_documented_demo_flip_is_unchanged`.
- The three SKIPs are the deadline check (the window is open, so the closed path cannot be observed here —
  covered by pytest with a frozen window), PDF certificates and pairwise mode (neither is implemented, and
  neither is claimed).

## Not executed, and why

| Thing | Why not | What would settle it |
| ----- | ------- | -------------------- |
| The `make` targets | No `make` on Windows | Any Linux/macOS machine; each target is a two-line wrapper around the commands in the table below |
| Wi-Fi physically switched off | The host's radio cannot be toggled from here | Pull the network cable; the offline Compose stack (Phase A6) enforces the same isolation at the container level |
| Live GitHub OAuth | No client id or secret were provided | Register an OAuth app, set `GITHUB_CLIENT_ID`/`GITHUB_CLIENT_SECRET`, sign in |
| Commit history against the real GitHub API | `MOCK_GITHUB=true` everywhere, so only the deterministic path ran | Set `MOCK_GITHUB=false` with a `GITHUB_TOKEN` and re-check a submission |

## The offline claim, specifically

Every check in this document ran against `127.0.0.1` with `MOCK_GITHUB=true`. No step fetches an image, a
package, a font or a schema from the network, and there is no cloud dependency in the running system: the
database is a file (SQLite) or a Compose service (Postgres), the session is an HMAC-signed cookie, and the
passwordless login and the checker's headers are both issued locally. The one place a network call would
happen is commit-integrity checking against the GitHub API, and that path is explicitly switched to
deterministic synthetic data by `MOCK_GITHUB=true`, which is also the default in `.env.example`.

Phase A6 strengthens this claim beyond "no outbound calls were observed": `docker-compose.offline.yml`
attaches every container to a Docker network declared `internal: true` — a network with no gateway and
no route out. The `probe` service confirmed that DNS resolution of `api.github.com` and a TCP connection
to `1.1.1.1:443` both fail with connection-refused / name-resolution errors, while the API and web
services answer normally on the internal network. The radio is still not physically unplugged, but the
network-level isolation is enforced by the kernel rather than by application-layer promises.

## Defects found while verifying this pass

Recorded because a verification pass that finds nothing is usually a pass that looked at nothing.

1. **A blank `EVENT_END` closed the event at boot.** `EVENT_END` defaulted to *now*, and both `.env.example`
   and `docker-compose.yml` leave it blank — so the headline one-command path started with a deadline that
   had already passed, and a judge who copied nothing could neither draft nor submit. The default is now a
   72-hour window that is open at boot; an explicit value still wins. Pinned by
   `api/tests/test_event_window.py`.
2. **A local `.env` silently enabled the passwordless dev login during tests**, because `app.config` loads
   `.env` and `LOCAL_DEV_LOGIN=true` was set there for local runs. The test bootstrap now pins that variable
   (and `EVENT_SOURCE`) so the suite cannot depend on a developer's local state.
3. **SQLite returned naive datetimes to the API.** `DateTime(timezone=True)` columns come back naive from
   SQLite and aware from Postgres, so on the offline path a browser would have read a deadline as *local*
   time. `api/app/timeutil.py` now decides the meaning once — naive means UTC — and every serializer emits
   through it.
4. **Duplicate detection sorted ids as strings**, so `"31"` sorted before `"7"` and the wrong submission was
   nominated canonical. Ids now sort naturally.
5. **The fixture's first draft put 40 projects into 12 teams.** Axion allows one submission per team, so the
   import hit the unique constraint. The constraint was right; the dataset now has one team per project.
6. **Project rows were being validated for an email address**, which buried the real problems under 40
   spurious "invalid record" entries. Only people are checked for an email now.
7. **An earlier report understated the test count** (`82 tests` hardcoded in a footer). Removed rather than
   updated, so it cannot go stale again.
8. **The audit trail could not survive the deletion it was designed for** (found by the PostgreSQL suite,
   fixed in Phase A3). `audit_logs.actor_id` was declared `FOREIGN KEY … ON DELETE SET NULL`, and
   PostgreSQL applies that action as an `UPDATE` of the audit row — which the append-only trigger refuses.
   The result was that deleting any user who had acted raised `audit_logs is append-only (attempted UPDATE)`
   and the delete failed outright: the trail could be neither anonymised nor left alone. Migration
   `0005_audit_actor_is_historical` drops the foreign key; `actor_id` stays as the id it was at the time and
   `actor_email` remains the durable attribution, so history survives verbatim. SQLite has neither the
   trigger nor the referential action, which is exactly why the fast suite could not have found it.

## Phase A3 — PostgreSQL, Docker and CI (2026-09-27)

Executed on the same machine once Docker Desktop 29.8.0 (Linux engine over WSL2) and PostgreSQL 16.10 were
available. `make` is still absent, so its targets remain the one unexecuted path; each wraps the commands
below.

| # | Command | Observed result |
| - | ------- | --------------- |
| 10 | `python -m pytest -q --ignore=tests/pg` | **144 passed**, 0 failed, 0 skipped, 4 m 02 s — the fast suite, still entirely in-process and in-memory (134 before the manifest-contract tests were added) |
| 11 | `AXION_TEST_DATABASE_URL=postgresql+psycopg://axion:axion@127.0.0.1:5433/axion_test AXION_REQUIRE_POSTGRES=1 python -m pytest -q tests/pg` | **113 passed**, 5 m 57 s. The first run found four failures: two expectation bugs in the new tests and one real defect (item 8 above) |
| 12 | Empty → migrated → fixtures → application, on PostgreSQL: `psql -c "DROP SCHEMA public CASCADE" -c "CREATE SCHEMA public"`, `DATABASE_URL=… alembic upgrade head`, `DATABASE_URL=… python -c "from app import seed; seed.seed_fixtures()"` | Five migrations applied to an empty schema, `alembic_version` at `0005_audit_actor_is_historical`, then 40 projects / 12 judges / 40 teams / 48 participants / 131 reviews / 138 assignments imported, `closed_event: true` |
| 13 | `DOGFOOD_FIXTURE_MODE=true EVENT_START= EVENT_END= docker compose up --build` | Three containers up, `axion-api-1` healthy; `/api/health/ready` → `{"ready":true,"checks":{"database":"ok","migrations":"ok","dataset":"61 users, 40 submissions"}}`; `/api/event` reports the fixture window 2026-08-01 → 2026-08-04 as `closed`; the gallery lists 40 projects; `web` answers 200; inside the container `alembic_version = 0005_audit_actor_is_historical` |
| 14 | `AXION_API_URL=http://127.0.0.1:8000 python api/scripts/dogfood_check.py .dogfood.toml --out acceptance-report.txt` | **19 passed, 0 failed, 0 skipped** — the committed `acceptance-report.txt`, regenerated against the Compose stack; this artefact had previously been produced under uvicorn only |
| 15 | `AXION_API_URL=http://127.0.0.1:8000 python api/scripts/acceptance.py --out acceptance-report.axion.txt` (demo mode) | **27 passed, 0 failed, 3 skipped** — the committed `acceptance-report.axion.txt`, regenerated against the Compose stack; the three skips are PDF certificates, pairwise mode and the closed-deadline check |
| 16 | `git push origin main` → GitHub Actions ([run 36321264917](https://github.com/kit2824bad108-blip/dogfood/actions/runs/36321264917), commit `80997e6`) | **Five jobs, all green on the first run**: backend 121 s, PostgreSQL 75 s, frontend 46 s, Docker images 44 s, acceptance 90 s. The acceptance job's steps — *Start the stack on the fixture dataset*, *Wait for readiness*, the manifest check as a gate, *Tier-by-tier suite* — all completed, and *Container logs on failure* was skipped, which is what a passing run looks like |

The first Docker build failed on `pip install` — `greenlet` came back as "from versions: none" while the
Next.js image was downloading at the same time. Directly fetching the same wheel in the same base image
succeeded, so it was a flaky index response rather than a real dependency problem; the Dockerfile now pins
`--retries 6 --timeout 90` and the build is reproducible.

## Phase A4 — the organisers' four files (2026-09-28)

Phases A1–A3 were written while the organisers' published files were not available to this machine: the
`run.py`, `fixtures.json` and `example.dogfood.toml` named by the brief had never been fetched, so the root
`fixtures.json` was Axion's own generated dataset and `.dogfood.toml` was Axion's own manifest in a shape of
its own. The published spec page was then read, and this phase reconciles the repository with it. Rows 4, 7,
14 and 15 above describe the artefacts as they were *before* that reconciliation: `acceptance-report.txt` was
produced by Axion's own manifest checker reading the root `.dogfood.toml`. From this phase on, the mapping
is the brief's own:

| Artefact | Produced by | Manifest |
| -------- | ----------- | -------- |
| `acceptance-report.txt` | **their `run.py`**, byte-for-byte as published | the root `.dogfood.toml`, now in the published shape |
| `acceptance-report.selfcheck.txt` | `api/scripts/dogfood_check.py` | `api/scripts/selfcheck.toml` (twenty-one checks, ours) |
| `acceptance-report.axion.txt` | `api/scripts/acceptance.py` | none — it walks the tier ladder T0–T4 and the bonus claims |

What was vendored, and where: `run.py` and `fixtures.json` at the repository root (SHA-256
`aa98963841bc…` and `252896bc45d4…`, recorded in README.md), `spec/spec.md` and `spec/example.dogfood.toml`
under `spec/`, and Axion's own dataset moved to `data/axion-fixtures.json` so the two are never confused.
The rest of this section records what was executed against the real files.

| # | Command | Observed result |
| - | ------- | --------------- |
| 17 | `docker compose down -v && docker compose up -d --build` | Docker Desktop 29.8.0 had to be started first (the daemon was stopped, not absent). On a **fresh volume**: migrations `0001`→`0006` applied to an empty database, then the organisers' `fixtures.json` seeded as published — 41 projects, 30 judges, 40 teams, 91 participants, 126 reviews, 199 assignments, 1 duplicate marked, 0 invalid records, `closed_event: true`. All three containers healthy |
| 18 | `curl http://localhost:3000/api/gallery` | `200`; 40 rows and 8 tracks. 40, not 41: the duplicate submission is imported, stored and scored, and excluded from the public gallery |
| 19 | `python3 run.py .dogfood.toml > acceptance-report.txt` — **the organisers' checker, unmodified**, against the Compose stack on `:3000` | **7 checks, 7 PASS, 0 FAIL**: gallery public; a fixture title on page one; the closed event refusing a write; judge A's own scores; judge B refused a peer's scores; a participant refused judge surfaces; the CSV export. Footer: `claimed T1 T2, verified T1 T2` |
| 20 | `python api/scripts/dogfood_check.py api/scripts/selfcheck.toml --out acceptance-report.selfcheck.txt` against the same instance | **21 passed, 0 failed, 0 skipped** — including `403` on `/api/judging/judges/jdg_01/scores` for judge B, the presentation gate before a technical verdict, all three CSV exports (126 verdict rows), and `submissions=41 judges=30 teams=40 verdicts=126 assignments=199` observed against the declared figures |
| 21 | Demo instance on `:8011` (SQLite, `SEED_MODE=demo EVENT_SOURCE=env`, open window), then `AXION_API_URL=http://127.0.0.1:8011 python api/scripts/acceptance.py --out acceptance-report.axion.txt` | **27 passed, 0 failed, 3 skipped** (30 checks). The DOCS check now walks the brief's own root listing: 13 files and 6 directories, and `LICENSE` asserted to be MIT |
| 22 | `cd api && python -m pytest -q --ignore=tests/pg` | **175 passed**, 1 warning, 3 m 14 s. The one warning is the pre-existing Starlette `anyio.abc.BlockingPortal` deprecation notice |
| 23 | `AXION_TEST_DATABASE_URL=postgresql+psycopg://axion:axion@127.0.0.1:5433/axion_test AXION_REQUIRE_POSTGRES=1 python -m pytest -q tests/pg` | **118 passed**, 1 warning, 1 m 21 s — the real migrations, the real append-only trigger and the partial unique indexes under concurrency |
| 24 | `cd web && npm run typecheck && npm run build` | Clean: 9 routes, 103 kB shared first-load JS |
| 25 | `python api/scripts/validate_fixtures.py fixtures.json` and `… data/axion-fixtures.json` | Both `VALID`, exit 0. Theirs: 41 projects / 30 judges / 40 teams / 91 participants / 126 reviews / 199 assignments, window `2026-02-26T18:00:00Z → 2026-03-01T18:00:00Z`, 8 incomplete batches, 1 duplicate (`prj_41 → prj_07`), 3 zero-variance judges (`jdg_07`, `jdg_27`, `jdg_28`), 73 assignments without verdicts. Ours: 40 / 12 / 40 / 48 / 131 / 138, digest `b5c9da177cab` |

One detail row 19 does not show on its face. The probe the checker sends for the closed event carries no
repository URL, so a portal that validated the body first would answer `422` for the missing field — passing
the letter of the check while demonstrating nothing about deadlines. The window is therefore checked before
the body is parsed, and the refusal is `403` with `The submission window is closed — no further edits are
accepted`. That ordering is asserted in `api/tests/test_dogfood_acceptance.py`, which makes the same seven
requests in-process and then runs the organisers' `run.py` verbatim against a real `uvicorn` over HTTP.

### Defects found while verifying this phase

Continuing the numbering from the section above.

9. **There was no `LICENSE` file at all.** The brief lists a non-OSI or absent licence among the automatic
disqualifiers, and this repository had neither a file nor a reference to one. MIT now, checked by the DOCS
row of `acceptance.py` (row 21) so it cannot go missing again.
10. **Three of the four files were not the organisers' files.** `run.py` did not exist anywhere in the
    repository; `.dogfood.toml` had Axion's own section names, so their checker could not have read it; and
    the root `fixtures.json` was Axion's generated demo dataset wearing the organisers' filename. The
    acceptance story in the earlier phases was therefore a story about Axion's own checks, told with the
    brief's vocabulary. The published files are now vendored verbatim with their digests recorded.
11. **The importer could not represent the dataset it advertised.** `uq_teams_name` refused the three teams
    called "StillTrail" (and the repeated "OpenSignal" and "AmberSwitch"), so they merged into one row each
    and their projects were attributed to the wrong team; `uq_submissions_team` refused `prj_41`, so the
    deliberate duplicate silently collapsed and 41 projects became 40. The messy data the brief advertises
    as the interesting part had never actually reached the database. Migration
    `0006_imported_reality_is_partial` makes both rules partial — one **live** submission per team, and team
    names unique **as created in this application** (`source_ref IS NULL`) — so the guarantees an organiser
    depends on still hold for rows this app writes, while a dataset that arrives with repeated names and a
    duplicate submission is imported as written. Both indexes are still enforced under concurrency, which is
    what `api/tests/pg/test_concurrency.py` exercises.
12. **In fixture mode the event on the page was not the event in the file.** The name came from `EVENT_NAME`
    in `.env` ("Axion Demo Hackathon") and the window from an invented `2026-08-01 → 2026-08-04`, so the
    portal announced an event that did not exist and enforced a deadline nobody had declared. In
    `EVENT_SOURCE=fixtures` the dataset's own `name` and `submissions_close` are now authoritative, and an
    explicit `EVENT_START`/`EVENT_END` does **not** override them — a value that could re-open an
    already-closed event would make the acceptance result depend on local configuration rather than on the
    code.
13. **The manifest's `submit` route answered a page, not a refusal.** A `POST` with a JSON body to a closed
    event returned a redirect to the browser's submission page — a `3xx`, which the checker counts as a
    failure, and which told a client nothing. See the note after row 19 for the ordering fix.
14. **`SEED_MODE=demo` seeded nothing.** The documented command in README.md for the crafted demo dataset
    passed `SEED_MODE=demo EVENT_SOURCE=env`, and the container entrypoint only acted on `SEED_DEMO=true`,
    `DOGFOOD_FIXTURE_MODE=true` or `SEED_MODE=fixtures`. An evaluator following the README got an open window
    and an empty event that looked perfectly healthy. The entrypoint now reads `SEED_MODE` for both
    datasets.
15. **The seed is idempotent, so it will not repair a volume.** The first Compose run of this phase reported
    `fixture seed skipped: an admin account already exists` and served a dataset left behind by an earlier
    phase — the gallery returned Axion's demo tracks while the log above it described the organisers' file.
    This is correct behaviour for a re-boot and a trap for an acceptance run, so row 17 starts from
    `down -v`: the report is produced from a fresh volume or it is not produced.
16. **Migration on SQLite is broken at `0001`, and stays broken.** Recorded rather than fixed: the project
    migrates on PostgreSQL (which is what `alembic upgrade head` does in the container and in CI), and the
    fast suite builds its schema with `create_all`. The database we ship is the one that migrates.

## Phase A5 — T3 and T4 built (2026-09-28)

The community surface and the stretch tier were the two blocks of the ladder that were documented as
*not built* rather than unclaimed. This phase builds them and re-runs every artefact, because a tier claim
with no report behind it is the one thing an acceptance report exists to prevent.

| # | Command | Observed result |
| - | ------- | --------------- |
| 26 | `cd api && python -m pytest -q --ignore=tests/pg` | **241 passed**, 1 warning, 8 m 29 s. 205 before this phase (plus 36 new tests: `test_community.py`, `test_webhooks.py`, `test_records.py`, `test_bundle_embed.py`) |
| 27 | `AXION_TEST_DATABASE_URL=… python -m pytest -q tests/pg` | **118 passed**, 1 warning, 2 m 1 s. `tests/pg/test_migrations.py` migrates an empty database `0001`→`0008` and then compares every reflected column, nullability and length against the models — which is how defect 17 below was found |
| 28 | `cd web && npm run typecheck && npm run build` | Clean: **11 routes** (was 9), 103 kB shared first-load JS — `/vote`, `/results` and `/projects/[id]` are new |
| 29 | `docker compose down -v && docker compose up -d --build`, then `python3 run.py .dogfood.toml > acceptance-report.txt` | Migrations `0001`→`0008` on a fresh volume, fixture dataset seeded (41 submissions), then the organisers' checker: **7 PASS**, `claimed T1 T2, verified T1 T2` |
| 30 | `AXION_API_URL=http://localhost:3000 python api/scripts/dogfood_check.py api/scripts/selfcheck.toml --out acceptance-report.selfcheck.txt` | **37 passed, 0 failed, 0 skipped** — up from 21 checks, and the new ones are behavioural: the tally refused with its reason, the ballot refused without a token, the outbox, the export's checksum, the embed's self-containment. Ladder: T1 4/4, T2 9/9, T3 5/5, T4 11/11, `Solid: T1, T2, T3, T4` |
| 31 | `SEED_MODE=demo EVENT_SOURCE=env docker compose up -d --build`, then `AXION_API_URL=http://127.0.0.1:8000 python api/scripts/acceptance.py --out acceptance-report.axion.txt` | **34 passed, 0 failed, 2 skipped** (36 checks, was 30). The two skips are pairwise mode and the *closed*-event deadline path, which cannot be observed on an open event; both state their reason |
| 32 | `docker compose up -d --build` back on the fixture dataset | `{"ready":true,"checks":{"database":"ok","migrations":"ok","dataset":"122 users, 41 submissions"}}` — the shipped default deployment, re-verified after the demo run |

Three defects were found by the new checks rather than by review, which is the argument for having written
them as observations of the deployment instead of as unit tests of the code:

17. **`voters.created_at` was nullable in the migration and not-null in the model.** Migration `0007`
    declared the timestamp without `nullable=False`, and a server default is not a substitute: the default
    applies only when a column is omitted, so an explicit `NULL` would have produced a voter with no creation
    time. `tests/pg/test_migrations.py` reflects the migrated schema and compares it to the models, and it
    caught `voters`, `votes`, `comments`, `throttle_events`, `webhook_endpoints`, `webhook_deliveries` and
    `participation_records` in one run. The migration test's own table and revision lists were updated in the
    same commit — which is the point of pinning them: adding a table without saying so fails there.
18. **The webhook panel's `dispatch` audit entry could not be serialised.** The details stored a Python
    `set` of outcome statuses, and SQLAlchemy's JSON serialiser refuses one. Every dispatch would have failed
    *after* delivering — the worst possible moment, since the deliveries would have gone out and the audit
    entry recording them would not. Found by the first test that actually dispatched with a stubbed receiver.
19. **Purge left orphaned deliveries on SQLite.** `webhook_deliveries.endpoint_id` declares
    `ON DELETE CASCADE`, and Postgres honours it, but SQLite does not enforce foreign keys unless asked to —
    and SQLite is the offline path. Deleting an endpoint removed the row and kept its deliveries, so the same
    operator action left a different database behind depending on the backend. The purge now deletes the
    deliveries explicitly, first, in code rather than relying on the engine.

Two design corrections were made during this phase rather than after it. The comment-duplication window is
compared in Python rather than in SQL, because SQLite returns naive datetimes from
`DateTime(timezone=True)` and Postgres returns aware ones — an `aware >= :naive` comparison reads differently
on the two backends, and the offline demo path is the one that would have broken. And a participation
record's signature is taken over the payload **as stored** rather than over one rebuilt from columns, for the
same reason: a certificate verified on another machine must not depend on a timestamp round-trip.

### What this phase does not claim

Pairwise/Bradley-Terry mode is still unimplemented and still reported as `SKIP` with its reason. Certificates
are self-contained printable HTML rather than PDF, which is stated in the README and visible in the T4 check
that fetches one. And the community tally's independence from the judged ranking is a design property, not a
cryptographic one: the tests assert that a ballot outcome never appears in `axion_score`, not that a
determined group of people with many addresses cannot inflate a number that is published beside it.

## Phase A6 — clean build, 7/7 acceptance, and offline network test (2026-09-28)

A complete teardown-and-rebuild from a clean volume, to confirm the one-command path is reproducible without
any pre-existing state. The acceptance report is regenerated with the organisers' own checker against that
fresh instance, and the offline Compose stack is run to prove the internal network actually blocks egress.

| # | Command | Observed result |
| - | ------- | --------------- |
| 33 | `docker compose down -v` | Three containers stopped, the `axion_axion-db` volume deleted; the `axion_default` network removed. Starting state: nothing |
| 34 | `docker compose up --build` | Both images rebuilt from scratch (API from `python:3.12-slim`, web from `node:20-alpine`). API image cached at the pip layer; web image recompiled Next.js 15.5.26 — 11 routes, 103 kB shared first-load JS. Sequence: `axion-db-1` healthy → `axion-api-1` started (migrations `0001`→`0008` on a fresh Postgres volume, fixture dataset seeded: 41 submissions / 30 judges / 122 users, `closed_event: true`) → `axion-web-1` started |
| 35 | `python3 run.py .dogfood.toml > acceptance-report.txt` — the organisers' checker, unmodified | **7 checks, 7 PASS, 0 FAIL**: gallery public; fixture title on page one; closed event refusing a write (403 `The submission window is closed`); judge A's scores; judge B refused a peer's scores; participant refused judge surfaces; CSV export. Footer: `claimed T1 T2, verified T1 T2` |
| 36 | `docker compose down` (keeping the volume) | Three containers stopped; data volume preserved for the offline run |
| 37 | `docker compose -f docker-compose.yml -f docker-compose.offline.yml up -d` | Same three application containers restarted, now on the `axion_offline` internal network (no gateway). Three extra services started: `probe`, `runner`, `workflow` (all `depends_on: api: condition: service_healthy`) |
| 38 | `docker compose -f docker-compose.yml -f docker-compose.offline.yml run --rm probe` | `egress: api.github.com:443 refused as expected (gaierror)` · `egress: 1.1.1.1:443 refused as expected (OSError)` · `api: http://api:8000/api/health/ready → HTTP 200 (109 bytes)` · `web: http://web:3000/ → HTTP 200 (23099 bytes)` · **RESULT: OFFLINE NETWORK CONFIRMED** |
| 39 | `docker compose -f docker-compose.yml -f docker-compose.offline.yml run --rm runner` | **37 passed, 0 failed, 0 skipped** — full selfcheck.toml inside the air-gapped network. T1 4/4, T2 9/9, T3 5/5, T4 11/11. `Solid: T1, T2, T3, T4`. **RESULT: ALL EXECUTED CHECKS PASSED** |

Exact commands run on this machine:

```
docker compose down -v
docker compose up --build
python3 run.py .dogfood.toml > acceptance-report.txt
docker compose down
docker compose -f docker-compose.yml -f docker-compose.offline.yml up -d
docker compose -f docker-compose.yml -f docker-compose.offline.yml run --rm probe
docker compose -f docker-compose.yml -f docker-compose.offline.yml run --rm runner
docker compose -f docker-compose.yml -f docker-compose.offline.yml down
docker compose up -d
```

The exit code of every `docker compose` command on this machine is reported as 1 by PowerShell because
Docker writes progress messages to stderr and PowerShell treats any stderr output as an error. The actual
containers started, reached healthy, and produced the results above; the real signal is the container
status, not the shell exit code.

## Phase A7 — the organiser's clock (2026-09-29)

The event's dates used to be readable only from the environment, read once at import: extending a deadline
meant editing a variable and restarting, and nothing in the database recorded that it had moved. This phase
makes the clock a **control** — a single row the console can write, with the configuration left underneath
it — and then re-runs every artefact, because a new way to change the event's own rules is exactly the kind
of change that has to be re-observed rather than reasoned about.

| # | Command | Observed result |
| - | ------- | --------------- |
| 40 | `cd api && python -m pytest -q -m "not postgres"` | **295 passed**, 1 warning, 24 m 18 s. 241 passed before this phase, plus 32 new tests in `tests/test_event_settings.py` and 22 added by the working tree since Phase A5. (`pytest tests` with the marker unset reports the same 295 plus **123 deselected**, which is how the PostgreSQL suite stays out of an in-process run.) |
| 41 | `AXION_TEST_DATABASE_URL=… AXION_REQUIRE_POSTGRES=1 python -m pytest -q tests/pg` | **123 passed**, 1 warning. The chain test now pins `0010_event_settings` as the head, and `tests/pg/test_event_settings_postgres.py` proves the singleton at the database: a second `INSERT` is refused by `ck_event_settings_singleton`, and a row with no deadline cannot be stored at all |
| 42 | `cd web && npm run typecheck && npm run build` | Clean: **11 routes**, 103 kB shared first-load JS. `/admin` grew to 16.7 kB — the Event window panel, its warnings, and the change log |
| 43 | `docker compose down -v && SEED_MODE=fixtures EVENT_SOURCE=fixtures docker compose up -d --build` | Migrations `0001`→**`0010`** on a fresh volume (visible in `docker compose logs api`), fixture dataset seeded, readiness `{"ready":true,…,"dataset":"122 users, 41 submissions"}` |
| 44 | `python3 run.py .dogfood.toml > acceptance-report.txt` | **7 PASS**, footer `claimed T1 T2, verified T1 T2` — including *closed event refuses submissions*, which is the check that would have broken first if an empty `event_settings` table had stopped meaning "the configured window" |
| 45 | `python3 api/scripts/dogfood_check.py api/scripts/selfcheck.toml --out acceptance-report.selfcheck.txt` | **40 passed, 0 failed, 0 skipped** (was 37). Ladder: T1 4/4, T2 9/9, **T3 8/8**, T4 11/11, `Solid: T1, T2, T3, T4`. The three new T3 checks observe the clock agreeing with the public payload, a participant being refused it, and the two windows being published side by side |
| 46 | `docker compose down -v && SEED_MODE=demo EVENT_SOURCE=env SEED_DEMO=true EVENT_START=2026-09-25T00:00:00Z EVENT_END=2026-10-03T18:00:00Z docker compose up -d`, then `python3 api/scripts/acceptance.py --out acceptance-report.axion.txt` | **34 passed, 0 failed, 2 skipped** (36 checks), unchanged from Phase A5, and the API-first bonus now reports **80 documented paths** (was 76) — the four clock endpoints are in the generated schema |
| 47 | `docker compose down -v && docker compose up -d` (the shipped default) | `{"ready":true,…,"dataset":"122 users, 41 submissions"}`; `/api/health` reports `"event":"Sample Hack 2026"`, window `2026-02-26 → 2026-03-01T18:00Z`, `closed: true`, `source: "deployment"` |
| 48 | The console, in a browser, against that instance: `/admin` → *Event setup* → **Event window** | Moved the deadline and saved. The panel returned *"Saved: ends_at, note. Recorded in the audit trail."* with the consequences stated as warnings — *"This re-opens the submission window — work may be submitted again."* and *"Submissions will remain open for another 2 days and 23 hours."* — and the badge changed from *from the deployment's configuration* to *set by an organiser · revision 1*. `/api/health` and `/api/event` both immediately reported `source: "organiser"`, `window_revised: true` and the new close (`2026-10-02T06:30:00Z`, which is 12:00 in this browser's own zone — the local-time input is converted through `toISOString()`, so what the organiser types is what the API receives). The header banner above the panel also changed from *Submissions closed* to *Submissions open* **without a page reload**: the header is outside the panel, fetches the window once on mount, and listens for the same announcement, because a console that leaves a stale deadline three inches above the form it just re-opened is contradicting itself |
| 49 | The same panel: **Hand it back to the configuration** | Notice *"The event window is back under the deployment's configuration."*; header and badge both returned to *Submissions closed* without a reload; `/api/health` returned to `closed: true`, `closes_at: 2026-03-01T18:00:00Z`, `source: "deployment"`; the change log showed *Moved ends_at* and *Handed back ends_at* with the actor and the timestamp; `/api/admin/audit` held `event.settings_updated` then `event.settings_reset`. (The first hand-back recorded `changed: []`, so the log read *"Handed back nothing"* — a reset that moved the deadline is a change to the deadline, and the entry now names it; see the third design correction below) |
| 50 | `python3 run.py .dogfood.toml` again, **after** the console had moved the clock and handed it back | **7 PASS** again, `claimed T1 T2, verified T1 T2` — the override is reversible from the console, and the deployment the checker meets afterwards is the one its dataset describes |
| 51 | `docker compose down -v && docker compose up -d --build`, then the whole ladder again on that fresh volume, then `/admin` → *Event setup* in the browser | `event_settings` is **empty** on the fresh volume and `/api/event` reports `source: "deployment"`, `revision: 0`, `phase: "closed"`, `closes_at: 2026-03-01T18:00:00Z` — an untouched deployment still enforces the dataset's deadline, which is the whole reason the row exists instead of a column. `run.py` **7 PASS**; selfcheck **40 passed, 0 failed, 0 skipped** (T1 4/4, T2 9/9, T3 8/8, T4 11/11); `pytest -m "not postgres"` **295 passed**, 123 deselected, 27 m 01 s; `pytest tests/pg` **123 passed** (both on the same sitting, so the two numbers are observed together rather than four hours apart); `npm run typecheck && npm run build` clean, 11 routes. Then, in the console: **Save the window** with the close moved to 31-12-2026 23:30 local — the response said *"Saved: ends_at. Recorded in the audit trail."*, the badge flipped to *set by an organiser · revision 1*, the banner above the panel flipped to *Closes in 93d 05h 42m* **without a reload**, *Record signing key* flipped from *published* to *held until the event closes*, and the warnings named both consequences (*"This re-opens the submission window — work may be submitted again."*, *"Submissions will remain open for another 93 days and 5 hours."*). `/api/event` agreed: `source: "organiser"`, `window_revised: true`, `phase: "open"`, submission close `2026-12-31T18:00:00Z` — exactly 23:30 in the browser's own zone (+05:00), so the local-time input is converted, not reinterpreted — while the voting window stayed closed at `2026-03-04T18:00:00Z`. **Hand it back to the configuration** returned the notice, the banner, the dates and `/api/event` to the deployment's window, left 0 rows in `event_settings`, and left the change log reading *Moved ends_at* then *Handed back ends_at*, each with the actor and the time |

Two findings, both in the test harness rather than the product — and both of the kind that a green suite
would otherwise have hidden:

20. **A test that patched a module-level copy of the settings stopped testing anything.**
    `test_records.py::test_the_verification_key_is_published_only_after_the_event_closes` moved the event
    window by replacing `records.settings`. With the publication rule reading the *effective* clock through
    `app/eventconfig.py`, that patch became a no-op and the test **failed** — the correct outcome, and the
    reason the failure was worth fixing properly rather than by widening the assertion: patching a copy kept
    the test green while the API had stopped honouring the clock, which is precisely the failure it exists to
    catch. The two fixtures in `api/tests/conftest.py` that moved the window the same way were updated in the
    same pass, so `closed_window` and `voting_closed` now move `app.config.settings` — the value the resolver
    actually reads — and clear the resolver's cache. This is the third phase in a row where re-observing the
    deployment found something review had not.
21. **Two test modules with the same basename collided at collection.** `tests/test_event_settings.py` and
    `tests/pg/test_event_settings.py` are both imported as `test_event_settings` under pytest's default
    rootdir import mode, and the first run that collected both in one session stopped with *"import file
    mismatch"* instead of running. The PostgreSQL module is now `tests/pg/test_event_settings_postgres.py`.
    Worth recording because the earlier `pytest tests` run had collected the two files in an order that
    happened to work, so the collision appeared only when the command changed.

Two smaller gaps were closed in the same pass, both found by asking whether a documented setting actually reached the container. `docker-compose.yml` passed neither `VOTING_OPENS_AT`/`VOTING_CLOSES_AT` nor `RECORD_SIGNING_KEY` nor `LOCAL_DEV_LOGIN` nor `AXION_ANNOUNCE_ACCESS` to the API service, so a deployment that set one of them in `.env` (as both `.env.example` and DATA-MODEL.md invite it to) would have been ignored in silence — the community window and the record-signing key were configurable only by rebuilding the image. They are passed through now, and `.env.example` says what the four `EVENT_*`/`VOTING_*` values are: the deployment's **defaults**, overridable at run time from the console without a restart and never overwritten by it.

Three decisions were made during the phase rather than after it. The read path **resolves through the
caller's session** instead of caching the row: an organiser's change has to be visible to the very next
request, and a settings cache with a staleness window is a deadline that is wrong for a few seconds at the
one moment anybody is looking at it. The module-level cache that remains exists only for callers with no
session of their own (startup, an offline script) and is primed at boot and after every write. A **naive
timestamp in a PATCH is read as UTC**, because the alternative is a deadline that depends on the server's
timezone table. And the **reset's audit entry names the fields it moved**, so the change log distinguishes
"handed back" from "nothing happened" instead of reporting the empty set.

A fourth change came from watching the interface rather than the API: the header fetches the window once, on
mount, so moving the clock in the console left the *banner above the panel* still saying **Submissions
closed** until a reload — two different deadlines on one screen. The panel now announces the change
(`axion:window-moved`) and the header listens for it, which was verified by watching the banner flip without
a reload in both directions (step 48 and step 49 above). Nothing about that is visible in an API report, and
it is the kind of thing this project's own documentation would have claimed was fine.

### What this phase does not claim

The clock is per-deployment, not per-event: one row describes the one event this database is running, and
running two events on one instance remains unsupported (that was already true, and the singleton constraint
says so in the schema). A page already open in somebody's browser keeps the deadline it was rendered with
until it reloads — the API is immediate for the next request, and the header refetches when the change is
made *in that browser*, but an open tab elsewhere is not pushed to. Moving the window does **not** re-run
Commit Integrity checks that have already been filed; the response says so before the change is applied, and
the console's *Commit review* tab re-checks a project on demand. And the console warns rather than refuses:
an organiser can still close the event immediately, extend it indefinitely, or publish the community tally
and the record key early — the point is that the consequences are named, attributed and reversible, not that
the buttons are hard to press.
