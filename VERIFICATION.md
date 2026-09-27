# Verification record

"It works" is a claim, and a claim should be checkable. This file records what was actually executed on the
build machine, in order, with the results as observed — and, just as importantly, what was **not** executed
and why.

Build environment: Windows with Git Bash; Python 3.12.10 (`api/.venv`); Node v24.16.0 / npm 11.13.0.
Phase A3 added Docker Desktop 29.8.0 (Linux engine, WSL2) and a local PostgreSQL 16.10, so the Docker and
PostgreSQL paths recorded below were executed rather than reviewed. `make` is still not installed, so that
one row stays in "not executed".

## Executed

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
| Wi-Fi physically switched off | The host's radio cannot be toggled from here | Pull the network cable — see "The offline claim, specifically" below for what would still hold |
| Live GitHub OAuth | No client id or secret were provided | Register an OAuth app, set `GITHUB_CLIENT_ID`/`GITHUB_CLIENT_SECRET`, sign in |
| Commit history against the real GitHub API | `MOCK_GITHUB=true` everywhere, so only the deterministic path ran | Set `MOCK_GITHUB=false` with a `GITHUB_TOKEN` and re-check a submission |
| Wi-Fi physically switched off | The host's radio cannot be toggled from here | Pull the network cable; every check above is loopback-only, and no build or run step contacts a third party |

## The offline claim, specifically

Every check in this document ran against `127.0.0.1` with `MOCK_GITHUB=true`. No step fetches an image, a
package, a font or a schema from the network, and there is no cloud dependency in the running system: the
database is a file (SQLite) or a Compose service (Postgres), the session is an HMAC-signed cookie, and the
passwordless login and the checker's headers are both issued locally. The one place a network call would
happen is commit-integrity checking against the GitHub API, and that path is explicitly switched to
deterministic synthetic data by `MOCK_GITHUB=true`, which is also the default in `.env.example`.

Where this falls short of the strongest possible claim: nobody physically disconnected the machine. The
Compose path has since been executed (Phase A3 below), including the two-container front door, so the
one-command boot is observed rather than reviewed; the radio itself is the only untested part of the claim.

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
