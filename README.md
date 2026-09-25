# Axion

**The fundamental engine for trustless hackathon execution.**

Hackathon judging is broken. It relies on opaque averaging, emotional bias from flashy demos, and
logistical friction. Axion is an open-source, self-hostable hackathon lifecycle platform that replaces
subjective averaging with mathematically defensible Z-score normalization and enforces blind technical
evaluation.

> Axion doesn't just collect submissions; it audits the judging process, so the best code wins — not the
> best pitch.

---

## Contents

| Document | What it covers |
| -------- | -------------- |
| [ARCHITECTURE.md](./ARCHITECTURE.md) | Topology, request flow, build-time rewrite caveat, testing strategy |
| [DATA-MODEL.md](./DATA-MODEL.md) | Postgres schema, tables, constraints, ERD |
| [JUDGING.md](./JUDGING.md) | Judge assignment, scoring methodology, normalization method and the proof |
| [THREAT-MODEL.md](./THREAT-MODEL.md) | Sybil voting, judge collusion, deadline gaming, and what is out of scope |
| [acceptance-report.txt](./acceptance-report.txt) | Tier-by-tier acceptance run against the live API |

## Run it (one command)

```bash
cp .env.example .env      # optional: every value has a working default
docker compose up --build
```

Then open **http://localhost:3000**. On first boot the API container applies migrations and, because
`SEED_DEMO=true`, loads a demo event: 50 participants, 10 teams, 10 projects, 5 judges and 50 verdicts
(50 technical + 50 presentation scores), plus 3 tracks with prizes and the default 30/70 rubric.

The seed prints a comparison to the container logs — the naive average and the Axion leaderboard disagree
on purpose:

| Rank | Project | Raw avg | Axion | z | Rank ± |
| ---- | ------- | ------- | ----- | - | ------ |
| 7 | Flashy Demo | 6.40 | 46.66 | −0.334 | −2 |
| 5 | Quiet Craft | 6.00 | 48.79 | −0.121 | **+2** |

The flashy demo wins on the naive average. The quiet craft wins under Axion, because it was rated at the
ceiling of a grader who works a narrow band — the more informative signal. Full reasoning and the worked
example: [JUDGING.md](./JUDGING.md).

## Judging with no network at all

The rules of a hackathon are simple: if it cannot run on a laptop with the Wi-Fi off, it cannot be adopted.
GitHub OAuth cannot be the only door, so Axion ships an offline one.

When `MOCK_GITHUB=true` or `SEED_DEMO=true` — the two flags that already mean "this is a demo" — the sign-in
page grows a **Local Dev Login** panel with one-click buttons, and these seeded accounts work through the
ordinary password form:

| Account | Password | Role |
| ------- | -------- | ---- |
| `admin@axion.local` | `password` | Organiser |
| `hacker@axion.local` | `password` | Participant, already on a team |
| "Login as Judge" | — | Signs in as `disciplined@axion.dev`, who has verdicts to inspect |

Zero external calls are made. The endpoint behind it (`POST /api/auth/dev-login`) returns `403` unless the
deployment is already in demo mode, so it cannot become a silent backdoor in a live event. That risk, and
how it is contained, is written up in [THREAT-MODEL.md](./THREAT-MODEL.md).

## Light and dark themes

The interface ships two complete themes and a moon/sun toggle in the header, plus a **settings menu in the
bottom-left corner** with the theme choice (light / dark / system), the event window, the active environment
flags and a link to the API reference. The choice persists in `localStorage`, follows
`prefers-color-scheme` when set to system, and is applied by a blocking script before first paint so there is
no flash of the wrong theme.

Every colour is a token in `web/app/globals.css`: light is a near-white, mint-tinted paper with teal as the
only saturated accent; dark is near-black ink with the same teal primary and a purple→pink gradient on the
highlighted headline word.

## Demo accounts

| Role | Email | Password |
| ---- | ----- | -------- |
| Organiser | `admin@axion.dev` | `axion-admin` |
| Organiser (offline) | `admin@axion.local` | `password` |
| Judge (narrow band) | `disciplined@axion.dev` | `axion-judge` |
| Judge (wide range) | `expansive@axion.dev` | `axion-judge` |
| Judge (balanced) | `balanced-a@axion.dev` | `axion-judge` |
| Participants | `hacker01@axion.dev` … `hacker50@axion.dev` | `axion-hacker` |

The seeded judges have already filed verdicts for every project, so nothing is locked for them. To watch
the blind boundary work, sign in as the organiser, create a judge in *Console → Event setup*, then sign in
as that judge: every demo and video link is withheld until the technical verdict is submitted.

## The three pillars

**1. Trustless intake.** GitHub OAuth for team formation, invite codes for teammates, one submission per
team. A project can be saved as a **draft** while it is still being built — no judge assignment, no gallery
listing, no integrity check — and promoted to **submitted** when it is ready. On submit, the **Commit
Integrity** check samples the repository's commit history and reports the share authored inside the event
window. A repository whose history predates the event is flagged for an organiser to review — never
automatically disqualified. See `api/app/github.py`.

**2. The Axion Core.** Three mechanisms, all enforced in the API rather than the interface:

- **Blind evaluation tiers.** Judges see the repository and documentation first. The demo and video links
  are withheld until that judge's technical verdict exists. Calling the endpoint directly returns `403`,
  and the assignment list never includes the links — even the public gallery withholds them, so there is no
  second door. The blur in the UI is cosmetic.
- **A weighted rubric.** Criteria and weights live in the database (default *Innovation 30% / Code Quality
  70%*), judges score each criterion, and the technical verdict is the weight-normalized mean. Re-weighting
  the rubric never rewrites a verdict already filed.
- **Z-score normalization.** Each judge is standardized against their own grading distribution before
  verdicts are averaged, so a strict grader's 6 can outrank a generous grader's 8. Full write-up in
  [JUDGING.md](./JUDGING.md).

Every score submission, alteration, role change, rubric change, CSV export and archive action is written to
an append-only `audit_logs` table with a timestamp and client IP. In Postgres a trigger rejects `UPDATE` and
`DELETE` on that table.

**3. The ephemeral archive.** The organiser clicks *Generate archive* and gets a self-contained JSON
bundle plus a `RESULTS.md` results page ready for GitHub Pages. Drafts are counted and never ranked. Nothing
is deleted; the database can be spun down afterwards.

## Running an event

Everything an organiser needs is in **/admin**:

| Tab | What it does |
| --- | ------------ |
| Leaderboard | Axion vs naive ranking, grader calibration, **CSV export** of the leaderboard and of every verdict (raw score, that judge's mean/σ, the z-score and each criterion) |
| Judging progress | Per-judge `x/10 graded`, pending count, last activity, and a **CSV export** |
| Commit review | The Commit Integrity review queue, with a re-check action |
| Tracks & prizes | Create tracks (slug, description, prize pool) and prizes, per track or overall |
| Rubric | Edit the criteria and their weights; the preview shows the resulting percentages |
| Audit trail | The append-only record, newest first |
| Event setup | Counts, judge creation, assignment backfill |
| Archive | The publishable results bundle |

The public **/gallery** page lists every submitted project with search and a track filter. It deliberately
omits demo and video links: blind evaluation would be theatre if an unauthenticated page published the
presentation tier.

## We claim the API First bonus

The API is the product; the interface is a client of it. FastAPI generates an OpenAPI 3 schema from the
route definitions, served on the **same origin as the app** so there is nothing extra to run:

- **Swagger UI: http://localhost:3000/api/docs** (also `…/api/docs` on the API port)
- **Schema: http://localhost:3000/api/openapi.json**
- ReDoc: `http://localhost:3000/api/redoc`

The schema documents every route, request model and response, including the judging, rubric, gallery and
CSV-export endpoints. The acceptance report verifies that the generated document still contains the
expected paths, so the claim cannot rot silently.

## Repository layout

```
api/                      FastAPI service — owns the database, all auth and all math
  app/zscore.py           the normalization engine (pure functions, no framework imports)
  app/github.py           commit integrity
  app/services.py         assignment, rubric weighting, event window
  app/routers/            auth, event, teams, submissions, judging, admin
  scripts/acceptance.py   tier-by-tier acceptance runner
  alembic/                migrations (0001 initial, 0002 tracks/prizes/rubrics/drafts)
  tests/                  pytest suite, including the math proofs
web/                      Next.js App Router frontend (Tailwind + shadcn-style components)
docker-compose.yml        db + api + web
```

The browser only ever talks to `/api/*` on port 3000; Next.js rewrites those calls to the API container,
which keeps the session cookie same-origin and hides the API port.

## Tests and the acceptance report

```bash
make test                                     # pytest + typecheck (82 tests)
make acceptance                               # live tier-by-tier run -> acceptance-report.txt
cd api && python -m pytest -q                 # or directly
docker compose exec api python -m pytest -q    # no local Python setup needed
cd web && npm run typecheck && npm run build
```

The pytest suite covers the normalization math (including the harsh-6-versus-generous-8 claim,
zero-variance judges and single-verdict judges), the blind-evaluation boundary, commit-integrity parsing,
the draft/deadline rules, the rubric weighting, the gallery and the CSV exports.

`acceptance-report.txt` in the repository root is the output of running the acceptance suite against a live,
seeded instance. It is **Axion's own** suite: no organiser-provided acceptance script, Postman collection or
test harness was available in this repository or the hackathon resources at build time, and the report says
so in its header rather than implying otherwise.

## Configuration

All variables are documented in `.env.example`. The ones that matter most:

| Variable | Purpose |
| -------- | ------- |
| `SECRET_KEY` | Signs session cookies. **Change before exposing the app.** |
| `EVENT_START` / `EVENT_END` | The commit-integrity window **and** the submission deadline, enforced server-side. Defaults to the 72 hours ending now. |
| `GITHUB_CLIENT_ID` / `GITHUB_CLIENT_SECRET` | Enables "Continue with GitHub". Register the callback `{WEB_URL}/api/auth/github/callback`. Without them participants register with email. |
| `GITHUB_TOKEN` | Raises the GitHub API limit from 60 to 5000 requests/hour. |
| `MOCK_GITHUB` | Computes commit integrity from deterministic synthetic data. Keep `true` for offline demos. |
| `SEED_DEMO` | Loads the demo dataset on boot. Set `false` for a clean event. |
| `LOCAL_DEV_LOGIN` | Force the passwordless offline login on or off. Implied on by either flag above; never enable it for a live event. |

## Honest limitations

- **Commit dates are client-controlled.** A team can rewrite them, and squash merges collapse history.
  Commit Integrity is a review prompt, not proof.
- **Commits are not lines of code.** The signal is *when work happened*, not how much.
- **Z-scores assume overlapping coverage.** They are comparable because every judge scores every project
  at this scale; see [JUDGING.md](./JUDGING.md) for the caveat at larger events.
- **Collusion inside a shared distribution is not detectable by the math.** Normalization exposes outlier
  grading, not a coordinated majority; the audit trail is the evidence layer for that.
- **Participants are not emailed.** Judges are created by the organiser, who hands out credentials.
- **`docker compose up` and `make` were not executed while building this**, because the build environment
  has neither a Docker CLI nor `make`. Both are validated structurally (Compose parsed and checked for
  service wiring, healthcheck and env completeness) and the acceptance report was produced against the API
  running directly under uvicorn with the Next.js frontend in production mode.
