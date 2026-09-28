# Axion

**The fundamental engine for trustless hackathon execution.**

[![CI](https://github.com/kit2824bad108-blip/dogfood/actions/workflows/ci.yml/badge.svg)](https://github.com/kit2824bad108-blip/dogfood/actions/workflows/ci.yml)

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
| [VERIFICATION.md](./VERIFICATION.md) | What was actually executed here, with commands and results, and what could not be |
| [DEMO.md](./DEMO.md) | The five-minute demo video: shot list, and what each shot is meant to prove |
| [.dogfood.toml](./.dogfood.toml) | The manifest the organisers' checker reads: base url, tier claim, the four headers, five routes |
| [run.py](./run.py) | The organisers' acceptance checker, verbatim as published |
| [fixtures.json](./fixtures.json) | The organisers' dataset, verbatim as published: 41 projects, 30 judges, 8 tracks |
| [data/axion-fixtures.json](./data/axion-fixtures.json) | Axion's own generated dataset, which the demo numbers below were computed against |
| [acceptance-report.txt](./acceptance-report.txt) | Their checker, their dataset, run against this portal and committed as printed |
| [acceptance-report.selfcheck.txt](./acceptance-report.selfcheck.txt) | Axion's own deeper self-check: twenty-one questions rather than their seven |
| [acceptance-report.axion.txt](./acceptance-report.axion.txt) | The tier-by-tier suite (T0–T4 plus bonus claims), on the demo dataset |

## The four files the brief names

The brief asks every team to agree on four files. All four are at the root of this repository, unmodified
where they come from the organisers:

| File | Whose | What it is |
| ---- | ----- | ---------- |
| `.dogfood.toml` | ours | Where things are in *this* portal, and what we claim |
| `acceptance-report.txt` | their `run.py` | The receipt: seven checks, tier by tier, committed as printed |
| `fixtures.json` | theirs | The dataset `docker compose up` seeds, loaded as published |
| `run.py` | theirs | The checker itself, byte-for-byte as published |

Reproducing the report is two commands, and the second one is theirs:

```bash
docker compose up --build          # portal on http://localhost:3000, seeded, already closed
python3 run.py .dogfood.toml > acceptance-report.txt
```

## Run it (one command)

```bash
cp .env.example .env      # optional: every value has a working default
docker compose up --build
```

Then open **http://localhost:3000**. On first boot the API container applies migrations and loads the
organisers' `fixtures.json`: 41 projects (including the duplicate submission), 30 judges, 40 teams, 8
tracks, 126 scores — and an event called **Sample Hack 2026** whose submissions closed on
`2026-03-01T18:00:00Z`, in the past, which is the point: the deadline the portal enforces is the deadline
the dataset declares.

That is the acceptance posture, so it is the default. The seed prints the credentials the checker uses:

```
seeded. test logins:
  organizer    Cookie: session=axion-organizer-1   (organiser@sample-hack-2026.dogfood)
  judge_a      Cookie: session=axion-judge-a-1     (tomas.varga@example.org)
  judge_b      Cookie: session=axion-judge-b-1     (wei.lindqvist@example.org)
  participant  Cookie: session=axion-participant-1 (priya1@example.org)
```

### The demo dataset instead

`SEED_MODE=demo` loads Axion's own crafted dataset — 50 participants, 10 teams, 10 projects, 5 judges and
50 verdicts (50 technical + 50 presentation scores), plus 3 tracks with prizes and the default 30/70
rubric — with an **open** window, which is what the write path needs:

```bash
SEED_MODE=demo EVENT_SOURCE=env docker compose up --build
```

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

With `SEED_MODE=demo` (the crafted dataset above):

| Role | Email | Password |
| ---- | ----- | -------- |
| Organiser | `admin@axion.dev` | `axion-admin` |
| Organiser (offline) | `admin@axion.local` | `password` |
| Judge (narrow band) | `disciplined@axion.dev` | `axion-judge` |
| Judge (wide range) | `expansive@axion.dev` | `axion-judge` |
| Judge (balanced) | `balanced-a@axion.dev` | `axion-judge` |
| Participants | `hacker01@axion.dev` … `hacker50@axion.dev` | `axion-hacker` |

With the default fixture dataset the accounts come from the organisers' file instead, so they have the
addresses that file uses and **no password**: the four literal session headers above are how the checker
signs in, and the import creates an organiser account (`organiser@sample-hack-2026.dogfood`) because the
file declares none. Participant display names are derived from their addresses, because the file carries
team members as email addresses and never as names — inventing names would be inventing data.

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
| Import & duplicates | Dry-run/apply a fixture, the import diagnostics, the duplicate review queue, coverage totals and balanced assignment |
| Tracks & prizes | Create tracks (slug, description, prize pool) and prizes, per track or overall |
| Rubric | Edit the criteria and their weights; the preview shows the resulting percentages |
| Audit trail | The append-only record, newest first |
| Event setup | Counts, judge creation, assignment backfill |
| Archive | The publishable results bundle |

The public **/gallery** page lists every submitted project with search and a track filter. It deliberately
omits demo and video links: blind evaluation would be theatre if an unauthenticated page published the
presentation tier.

## What Axion claims, and what it does not

A clean T2 is worth more than a broken T4, so the claim list is short and every line is backed by a test, a
route or a file in this repository.

| Tier | Status | Evidence |
| ---- | ------ | -------- |
| **T1 — core** | **Claimed** | Event window, tracks and prizes, drafts editable before the deadline, server-side deadline enforcement, a searchable public gallery, registration and teams with invite codes |
| **T2 — judging** | **Claimed** | Weighted rubrics, blind technical-then-presentation ordering, Z-score normalization with a worked proof, per-judge progress, the normalized leaderboard, CSV exports of the leaderboard, every verdict and judging progress, coverage/provisional marking, and a duplicate review queue |
| **T3 — community** | **Claimed** | Email-gated community ballots (`/vote`), a per-voter ballot order that is stable and reproducible, votes that are final and strikable rather than editable, project comment threads with duplicate refusal and moderation, and a tally that is refused with its reason while the window is open and published when it closes |
| **T4 — stretch** | **Claimed** | A signed webhook outbox with retries and dead-letter replay, signed participation records anyone can verify plus printable certificates, one whole-event bundle that exports and re-imports, and a self-contained embeddable gallery |

Bonus items claimed, each with something behind it: the **normalization proof** ([JUDGING.md](./JUDGING.md)),
**API first** (`/api/docs` and `/api/openapi.json`, with a run-time check that the expected paths still
exist), the **threat model** ([THREAT-MODEL.md](./THREAT-MODEL.md)) and the **one-command rule** (one
`docker compose up` brings up the database, migrations, the seeded event and the portal, with no external
service — the webhook receiver in the acceptance suite is the deployment's own route, precisely so the
outbound path is observable with the network off).

Two things are deliberately *not* claimed. There is no pairwise/Bradley-Terry mode: the requirement text
this repository was given is truncated after that heading, and guessing at a scoring model that then feeds
the normalized leaderboard would have risked the part of the ladder that is actually worth something. PDF
certificates are not produced either — the certificate is self-contained HTML that prints from the browser,
which is the same artefact without a PDF dependency in an air-gapped deployment. Both are reported as `SKIP`
with their reason in [acceptance-report.axion.txt](./acceptance-report.axion.txt) rather than quietly
missing.

### What the organisers' checker actually verified

Their `run.py`, their `fixtures.json`, this portal. The committed [acceptance-report.txt](./acceptance-report.txt)
is their output, unedited, and it ends:

```
T1  gallery is public ................. PASS
T1  project from fixtures shown ....... PASS
T1  closed event refuses submissions .. PASS
T2  judge sees own scores ............. PASS
T2  judge cannot see peer scores ...... PASS
T2  participant blocked ............... PASS
T2  csv export works .................. PASS

claimed T1 T2, verified T1 T2
```

Two details worth naming, because both are the substance rather than the ceremony. `judge cannot see peer
scores` is enforced in the API, so it answers `403` to `curl` as well as to a browser — the same
requirement the brief calls "the one that matters most". And `closed event refuses submissions` is refused
*by the deadline*, before the request body is even validated: the checker's probe sends no repository URL,
and a portal that answered `422` for the missing field would pass the letter of the check while telling a
judge nothing about deadline enforcement.

Their seven checks are also asserted in the fast test suite, so a regression fails in CI in seconds rather
than in a manual run: `api/tests/test_dogfood_acceptance.py` makes the same seven requests, and its last
test starts a real uvicorn on a free port, imports the real dataset and runs their real `run.py` over HTTP.

`claimed T1 T2` in that file is not shyness. Their checker has seven checks and all seven are T1/T2, so
claiming T3 or T4 there would print `claimed T1 T2 T3 T4, verified T1 T2` — a note saying the claim was
not verified. T3 and T4 are claimed where the evidence exists, in our own manifests:
[acceptance-report.selfcheck.txt](./acceptance-report.selfcheck.txt) (37 checks, T1–T4 all verified) and
[acceptance-report.axion.txt](./acceptance-report.axion.txt) (36 checks, 34 pass, 2 skip with reasons). The
self-check prints the ladder rule it applies, which is the same one their `run.py` uses: a tier counts only
if every check of its own passed *and* every tier below it did.

```
T1   claimed      verified                                4/4 checks passed
T2   claimed      verified                                9/9 checks passed
T3   claimed      verified                                5/5 checks passed
T4   claimed      verified                               11/11 checks passed

Solid: T1, T2, T3, T4
```

## The community surface (T3)

A judge's verdict and a stranger's vote are different instruments, so they are different tables, different
endpoints and different rules — and they are never averaged into one number.

**A ballot is an address plus a link, never an account.** `POST /api/vote/register` takes an address,
stores a **SHA-256 digest** of the token it returns, and the link to `GET /api/vote/verify?token=…` is what
proves the address and sets the ballot cookie. Axion has no mail server and must run with the network off,
so the link is returned to the caller and listed in the organiser console, and the endpoint says
`mailer_configured: false` on the wire rather than pretending otherwise. A leaked or mistyped link stops
working the moment a new one is requested.

**The ballot order is determined, not shuffled.** Each voter's list is sorted by
`hmac-sha256(secret, token_digest + ':' + submission_id)`, which gives three properties at once: the order is
stable across reloads (a ballot that reshuffles is unusable), it differs per voter (a fixed alphabetical
list hands the top of it an advantage that has nothing to do with quality), and it is reproducible by an
organiser investigating a complaint. The ballot payload states the method so this is checkable rather than
claimed.

**A vote is final; a moderator strikes it.** `uq_vote_voter_submission` makes a second vote a `409`, so
"changing your mind" is not a way to vote twice, and an organiser removes a vote by striking it — which
keeps the row, records who struck it and why, and writes an audit entry. The headline tally counts **cast
votes only, always**: a figure that changed depending on which request produced it would not be a tally.
Struck votes are reported beside it (`struck_votes`) rather than mixed into it.

**The tally is hidden until the window closes.** While the window is open, `/api/vote/results` answers `403`
*with its reason* to anyone who is not an organiser — and the community window is its own clock, so an event
can stop accepting work and still be taking votes. An organiser can always read the running figure, which is
what makes "hidden" a check rather than a slogan: the acceptance reports assert the refusal from an
anonymous caller and the publication after the close.

**Comments belong to an identity too.** Signed-in users comment as themselves; everyone else needs a
verified ballot address, and an unidentified caller is refused. The same words from the same identity twice
in ten minutes are refused as a flood, moderation hides rather than deletes, an address is visible to the
organiser (the moderator) and never in the public payload, and every write is rate-limited through a
`throttle_events` table — in the database rather than in process memory, because a counter that lives in one
worker silently stops applying the moment a deployment runs two.

The interface is at **/vote** (the ballot), **/projects/{id}** (a project and its thread) and **/results**
(the tally, or the reason there is not one yet).

## Outbound and reproducible (T4)

Four features that exist so an event can leave, be audited and be embedded — all of them with nothing to
run and nothing external to call.

**A signed webhook outbox.** Emitting an event inserts a delivery row in the *same transaction* as the thing
that happened, so a receiver that is down can never turn a participant's successful submission into a 500.
The envelope is built once and never rebuilt, which is what lets a retry carry the same `X-Axion-Delivery`
id and the same bytes: a receiver can dedupe on the id and verify the signature without re-deriving the
body. Signatures are `sha256=<hex>` over `'<timestamp>.<body>'` with the endpoint's own secret, the
timestamp is inside the signed material so a captured delivery cannot be replayed forever, and
`verify_signature()` ships in `app/webhooks.py` as the receiver's half so the scheme has exactly one
implementation. Five attempts over ~70 minutes, then `dead` — a state, not a deletion — with replay from
the console. `POST /api/admin/webhooks/dispatch` is the only thing that sends anything; there is no worker,
on purpose, and the console shows the pending count rather than hiding it.

**Signed participation records anyone can verify.** A record is a claim about a person or a team —
*this judge filed fourteen verdicts*, *this team placed third* — signed with HMAC over the payload **as
stored**, so verification is a comparison rather than a reconstruction and does not depend on how SQLite and
Postgres each round-trip a timestamp. Judges' records carry the verdict count, the assigned coverage, the
mean and the **effective σ** from the same `zscore` engine the leaderboard uses; teams' records carry their
placement from that same ranking, duplicates excluded. Issuing is idempotent — a signed statement about a
moment cannot be silently replaced — and revocation is a **dated fact beside the signature**, so a verifier
sees both what was claimed and what the organiser later decided. The key is published once the event closes
and not before: HMAC is symmetric, so whoever can verify can also forge, and publishing it while results
still matter would let anyone mint "this team won". `/api/records/{code}` verifies,`/certificate` prints,
`/api/records` reports counts and the key's state without naming anyone.

**One bundle, in and out.** `GET /api/admin/bundle/export` returns the whole event as one JSON document with
a `sha256` of its tables in a response header; two exports of an unchanged event are byte-identical, which
is what makes the checksum worth quoting. Identities travel and **credentials never do** — no password hash,
no session — because moving an event must not move the ability to sign in as anyone in it. The import is
idempotent (every row is matched on the key it is identified by), **dry by default**, and refuses a bundle
whole if it names a team it does not contain: half an event is worse than none.

**A gallery that embeds anywhere.** `GET /api/embed/gallery` is a complete, self-contained HTML document
for an `<iframe>` — no script tag, no font, no third-party asset — and `/gallery/snippet` generates the
exact markup from the origin that served the request, so a sponsor's page cannot be pointed at the wrong
host. Read-only in every direction: an embed is never an entry point into the event.

All four are exercised end to end by the acceptance suites over HTTP, including a real outbound delivery
whose receiver is this deployment's own route — chosen so the whole path is observable with the network off.

## Import a messy dataset

A curated ten-project demo proves nothing about a real event, so the organisers' `fixtures.json` is
deliberately awkward — and it is what ships here:

```
41 projects imported
30 judges imported
8 incomplete review batches detected
1 duplicate candidate detected
3 zero-variance judges detected
0 invalid records
```

One loader reads both datasets. Their file and Axion's own disagree about almost every field name, so
`api/app/fixture_dialects.py` translates theirs into the canonical shape the importer already reads
(`scores[].criteria` into weighted verdicts plus the criteria that produced them, `teams[].members` into
participant accounts, `judges[].tracks` into assignment, `event.submissions_close` into the enforced
window). One code path validates, diagnoses, detects duplicates and writes; the translation is one file a
reviewer can read.

```bash
# Diagnose either dataset. Pure function, no database, writes nothing.
cd api
python -c "from app import fixtures; print('\n'.join(fixtures.diagnose(fixtures.load_fixture())['headline']))"
python -c "from app import fixtures; print('\n'.join(fixtures.diagnose(fixtures.load_fixture('data/axion-fixtures.json'))['headline']))"

# Boot the API on theirs. The event has already closed, and that deadline is enforced.
SEED_MODE=fixtures EVENT_SOURCE=fixtures uvicorn app.main:app --port 8000
```

`POST /api/admin/import` and the **Import & duplicates** tab run the same code, dry-run by default.

What it handles, and how. An **incomplete batch** is an assignment with no verdict: a missing score stays
missing, the project is marked *provisional* and it is not ranked as if it had scored zero. A
**zero-variance judge** (every verdict identical) is detected from the verdicts themselves and contributes
`z = 0` for all of them; a **single-verdict judge** borrows the pool's dispersion rather than minting an
arbitrary z. **Duplicates** are found by comparing normalised repository URLs and title fingerprints, so
casing, a trailing slash and a `.git` suffix are not three different projects — while "Mesh Scheduler" is
not confused with "Mesh Relay". Detection is recomputed on every request and only an organiser's decision is
stored; a confirmed duplicate is flagged, never deleted. **String ids** (`proj_017`) are preserved in
`source_ref`, so importing twice updates instead of duplicating, and **timestamps** are normalised to UTC on
the way in and serialised with an explicit offset on the way out.

### Two constraints the real data disproved

Importing their dataset honestly was impossible, and the reason was this schema rather than the file. Two
things the fixture data does on purpose were unrepresentable:

- **A duplicate submission.** `prj_41` is a second submission from `tm_07`, with `prj_07`'s repository URL,
filed three minutes before the deadline. `UNIQUE(team_id)` refused the row, so the import silently turned
41 projects into 40 and the awkward case the brief advertises never reached the database at all.
- **Repeated team names.** "StillTrail" appears three times (`tm_03`, `tm_30`, `tm_40`), "OpenSignal" twice
and "AmberSwitch" twice. `UNIQUE(name)` refused them, so those teams merged into one row and their projects
were attributed to the wrong team.

Neither constraint was wrong; each was too broad. Migration `0006` narrows both into **partial** unique
indexes: one *live* submission per team (`WHERE duplicate_of_submission_id IS NULL`) and team names unique
as *created in this application* (`WHERE source_ref IS NULL`). Both still hold for everything this API
writes, including under concurrency — two writers racing to create the same team's submission still produce
exactly one winner — while a dataset that already contains repeats is imported as published instead of
being renamed to fit. `submissions.duplicate_of_submission_id` records the detected pair so the organiser's
decision has a durable subject; the gallery and the leaderboard show the project once, and the duplicates
screen shows the pair with its scores. Nothing is ever deleted.

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
.dogfood.toml             where things are, and what we claim (read by their run.py)
run.py                    the organisers' acceptance checker, as published
fixtures.json             the organisers' dataset, as published — what compose seeds
acceptance-report.txt     their report, committed as printed
acceptance-report.selfcheck.txt   Axion's own deeper check, thirty-seven questions
acceptance-report.axion.txt       the tier-by-tier suite, T0–T4 and the bonuses
data/axion-fixtures.json  Axion's own generated dataset (the demo numbers)
src/                      pointer: the code is api/ and web/ (see src/README.md)
tests/                    pointer: the suite is api/tests (see tests/README.md)
spec/                     the organisers' spec.md and example .dogfood.toml
DEMO.md                   the five-minute video: shot list, and what each shot proves
api/                      FastAPI service — owns the database, all auth and all math
  app/zscore.py           the normalization engine (pure functions, no framework imports)
  app/github.py           commit integrity
  app/services.py         assignment, rubric weighting, event window
  app/fixture_dialects.py translates both fixture dialects into one canonical shape
  app/access.py           the four literal checker credentials, and the gate on them
  app/voting.py           community ballots, comment moderation rules, the hidden tally
  app/throttle.py         rate limiting that is honest across workers
  app/webhooks.py         the signed outbox: envelope, signature, retry policy
  app/records.py          signed participation records and printable certificates
  app/bundle.py           whole-event export and an idempotent, dry-by-default import
  app/routers/            auth, event, teams, submissions, judging, community, admin,
                          webhooks, records, bundle, embed, devtools
  scripts/selfcheck.toml  Axion's own deeper manifest (thirty-seven checks, not theirs)
  scripts/dogfood_check.py  the checker that reads it
  scripts/acceptance.py   tier-by-tier acceptance runner
  alembic/                migrations (0001 initial … 0008 webhooks and records)
  tests/                  pytest suite, including the math proofs and the seven checks
web/                      Next.js App Router frontend (Tailwind + shadcn-style components)
docker-compose.yml        db + api + web
```

The two files that come from the organisers and judge this repository are vendored byte-for-byte, so this
project cannot have edited the thing that scores it without the edit being visible:

| File | SHA-256 |
| ---- | ------- |
| `run.py` | `aa98963841bc8e18e8e5d76f0499697c093dd3c0055f9d73a459f592f4dcf09d` |
| `fixtures.json` | `252896bc45d49fca69ad413be40c6bfde9d9b9f9dd8db702b3ff74eaaa181121` |

`fixtures.json` has one further copy, at `data/organiser-fixtures.json`, with the same digest: it is the
byte-identical second location the fixture search order looks in when the checker is run from inside `api/`.
`data/axion-fixtures.json` is a different file entirely — Axion's own generated dataset, digest
`b5c9da177cab3dc1e74489c8d190ccde26a5ce9a61cbb20dff92e0b2b1f9367e`, and the one every demo number in these
documents was computed against.

The browser only ever talks to `/api/*` on port 3000; Next.js rewrites those calls to the API container,
which keeps the session cookie same-origin and hides the API port.

## Tests and the acceptance report

The commands below are the ones that were actually run for [VERIFICATION.md](./VERIFICATION.md);
the `make` targets in [Makefile](./Makefile) are the same commands.

```bash
# The fast suite: in-process, in-memory SQLite, no services to start. Includes
# the organisers' seven checks (in-process) and one real end-to-end run of their
# run.py against a real uvicorn over HTTP.
cd api && python -m pytest -q -m "not postgres"

# The PostgreSQL suite. A real server, the real migrations, the real trigger:
# constraint, migration, judging, authorization, deadline, audit and concurrency
# tests that SQLite cannot express.
docker compose -f docker-compose.test.yml up -d --wait
cd api && AXION_TEST_DATABASE_URL=postgresql+psycopg://axion:axion@127.0.0.1:5433/axion_test \
  AXION_REQUIRE_POSTGRES=1 python -m pytest -q tests/pg
docker compose -f docker-compose.test.yml down -v

# The frontend has no separate test runner: typecheck and a production build are
# the check, and both run in CI.
cd web && npm ci && npm run typecheck && npm run build

# The one-command path, then the report the brief asks for: their checker, their
# manifest, their dataset, run against this portal.
docker compose up --build
python3 run.py .dogfood.toml > acceptance-report.txt

# Axion's own deeper self-check, against the same instance: twenty-one questions
# rather than seven. Separate manifest, separate report, so nothing we assert
# about ourselves can be mistaken for what was verified.
python api/scripts/dogfood_check.py api/scripts/selfcheck.toml --out acceptance-report.selfcheck.txt

# The tier-by-tier suite expects the demo dataset (it asserts the crafted
# rankings and the seeded .local accounts, and it writes).
SEED_MODE=demo EVENT_SOURCE=env python api/scripts/acceptance.py --out acceptance-report.axion.txt

# Both datasets: structure, references, timestamps, and two clean imports.
python api/scripts/validate_fixtures.py fixtures.json
python api/scripts/validate_fixtures.py data/axion-fixtures.json
cd api && python -m pytest tests/test_fixture_determinism.py -q

# `python -m pytest -q` with no marker expression runs everything; `tests/pg`
# skips itself (with the reason) when AXION_TEST_DATABASE_URL is unset, so a
# missing server never looks like a passing database suite.
```

The pytest suite covers the normalization math (including the harsh-6-versus-generous-8 claim,
zero-variance judges and single-verdict judges), the blind-evaluation boundary, commit-integrity parsing,
the draft/deadline rules, the rubric weighting, the gallery and the CSV exports.

The suite also covers the fixture importer's edge cases, duplicate and coverage handling, the balanced
assignment planner and a regression guard on the demo numbers quoted above — plus the role-by-role
authorization matrix, the checker's header credentials, the fixture validator CLI and a two-clean-import
determinism test.

There are **three report artefacts**, deliberately separate, and none overwrites another:

| File | Produced by | What it is |
| ---- | ----------- | ---------- |
| `acceptance-report.txt` | **the organisers' `run.py`** reading [.dogfood.toml](./.dogfood.toml) | The receipt the brief asks for: seven checks, tier by tier, committed exactly as printed. This is the only one of the three anyone else ran. |
| `acceptance-report.selfcheck.txt` | `api/scripts/dogfood_check.py` reading [api/scripts/selfcheck.toml](./api/scripts/selfcheck.toml) | Axion's own deeper check, 37 checks against the fixture instance: the blind gate, all three CSV exports, the Z-score leaderboard, both sides of four role boundaries, the community ballot and its hidden tally, the webhook outbox, signed records, the bundle and the embed. It prints `claimed vs observed` for every tier and applies the same "a tier counts only if the tiers below it passed" rule their `run.py` does. |
| `acceptance-report.axion.txt` | `api/scripts/acceptance.py` | The tier-by-tier suite (36 checks, T0–T4 plus the bonus claims) against the **demo** dataset, because half of it asks what only an *open* event can answer. It prints a SKIP with its reason wherever something cannot be observed, and a FAIL the run is expected to be clean of. |

## Continuous integration

[.github/workflows/ci.yml](./.github/workflows/ci.yml) runs five jobs on every push and pull request. None
of them needs a secret, a cloud account, a paid service or an external API: GitHub-hosted runners, a
PostgreSQL service container, and the repository's own `fixtures.json` with `MOCK_GITHUB=true`.

| Job | What it proves |
| --- | -------------- |
| `backend` | the fast suite (`pytest -m "not postgres"`) and `validate_fixtures.py` |
| `postgres` | `alembic upgrade head` against an **empty** database, then the PostgreSQL suite — constraints, migrations, judging, authorization, deadlines, the append-only audit trigger, concurrency |
| `frontend` | `npm ci`, `npm run typecheck`, `npm run build` |
| `docker` | both images build from a clean checkout |
| `acceptance` | the stack boots on the fixture dataset, `/api/health/ready` answers, then **the organisers' own `run.py`** reads the committed `.dogfood.toml` as a **gate** (the job fails on any failed check), and the deeper self-check runs against the same instance |

The command block above is the local reproduction of those jobs — same commands, same order.

The acceptance job is the organisers' program, not ours: their `run.py`, their `fixtures.json` and the
`.dogfood.toml` we committed. `api/tests/test_manifest_contract.py` closes the loop from the other side by
asserting that every route the manifest names is one the API serves, that the four headers resolve to the
roles the manifest implies, that `peer_scores` names judge A and not judge B, and that the dataset figures
in the manifest are the ones `fixtures.json` actually holds. A route renamed in one place and not the other
fails in CI rather than in a judge's terminal.

## Configuration

All variables are documented in `.env.example`. The ones that matter most:

| Variable | Purpose |
| -------- | ------- |
| `SECRET_KEY` | Signs session cookies. **Change before exposing the app.** |
| `EVENT_START` / `EVENT_END` | The commit-integrity window **and** the submission deadline, enforced server-side. In `EVENT_SOURCE=env` leave both blank and the window is a 72-hour event that is **open now**. An explicit value wins, so a closed event stays closed. |
| `EVENT_SOURCE` | `fixtures` (the compose default) or `env`. In `fixtures` the event **name and window come from the dataset**, and `EVENT_START` / `EVENT_END` are ignored: letting an environment value re-open an already-closed event would make the acceptance result depend on local configuration rather than on the code. |
| `SEED_MODE` | `fixtures` (the compose default — the organisers' file, already closed) or `demo` (the crafted dataset every demo number in these docs was computed against). |
| `GITHUB_CLIENT_ID` / `GITHUB_CLIENT_SECRET` | Enables "Continue with GitHub". Register the callback `{WEB_URL}/api/auth/github/callback`. Without them participants register with email. |
| `GITHUB_TOKEN` | Raises the GitHub API limit from 60 to 5000 requests/hour. |
| `MOCK_GITHUB` | Computes commit integrity from deterministic synthetic data. Keep `true` for offline demos. |
| `SEED_DEMO` | Loads the demo dataset on boot. Set `false` for a clean event. |
| `LOCAL_DEV_LOGIN` | Force the passwordless offline login on or off. Implied on by either flag above; never enable it for a live event. |
| `DOGFOOD_FIXTURE_MODE` | One flag for the acceptance dataset: implies `SEED_MODE=fixtures` and `EVENT_SOURCE=fixtures`, and enables the same offline/checker gate as `SEED_DEMO`. Explicit `SEED_MODE` / `EVENT_SOURCE` / `LOCAL_DEV_LOGIN` values still win. |

### Authentication for the checker

The organisers' checker **never logs in**, and there is no code in `run.py` that fetches a credential — it
attaches the header string written in `.dogfood.toml` and nothing else. So the manifest carries four
literal cookies, and the seed prints them:

```
organizer    Cookie: session=axion-organizer-1
judge_a      Cookie: session=axion-judge-a-1
judge_b      Cookie: session=axion-judge-b-1
participant  Cookie: session=axion-participant-1
```

Fixed strings rather than random ones, for the reason the brief gives: the header has to be writable into a
committed manifest, readable by a human, and identical on every machine — a value that rotated between a
run and the commit would make the report it cites unverifiable. What keeps that honest is the gate in
`api/app/access.py`: they are accepted only while the deployment has declared itself a demo (`SEED_MODE=fixtures`,
`SEED_DEMO`, `MOCK_GITHUB` or `LOCAL_DEV_LOGIN`). A live event answers **401** to all four, and
`api/tests/test_access.py` asserts exactly that — including that an invented literal of the same shape is
not a session. `GET /api/dev/checker-headers` still serves the same four roles as signed
`Authorization: Bearer` tokens, which is the form Axion's own deeper self-check uses. The residual risk, and
why it is acceptable here and not in a live event, is in [THREAT-MODEL.md](./THREAT-MODEL.md).

Two deterministic checks back the dataset itself up:

```bash
python api/scripts/validate_fixtures.py fixtures.json                 # exit 0 valid / 1 invalid / 2 unreadable
python api/scripts/validate_fixtures.py data/axion-fixtures.json
cd api && python -m pytest tests/test_fixture_determinism.py -q        # two clean imports → equivalent data
```

## Honest limitations

- **Commit dates are client-controlled.** A team can rewrite them, and squash merges collapse history.
  Commit Integrity is a review prompt, not proof.
- **Commits are not lines of code.** The signal is *when work happened*, not how much.
- **Z-scores assume overlapping coverage.** They are comparable because every judge scores every project
  at this scale; see [JUDGING.md](./JUDGING.md) for the caveat at larger events.
- **Collusion inside a shared distribution is not detectable by the math.** Normalization exposes outlier
  grading, not a coordinated majority; the audit trail is the evidence layer for that.
- **Participants are not emailed.** Judges are created by the organiser, who hands out credentials.
- **Full coverage is the default model.** Every judge on every project is right at ten projects and wrong at
  five hundred, so balanced assignment exists as an explicit organiser action. It reports the number of
  connected components in the judge/project overlap graph: more than one means the ranking is really several
  rankings, and it says so.
- **An imported dataset is not repaired.** A fixture with invalid records is refused whole rather than
  partially imported, because half an event is worse than none.
- **Their checker inspects behaviour, not quality.** Seven requests prove the gallery is public, the closed
  event refuses a write, isolation holds and the CSV exports; they prove nothing about the math, the schema
  or the interface. `api/scripts/selfcheck.toml` and the pytest suite are where the rest is asserted, and
  neither of those is evidence anyone else ran.
- **No pairwise/Bradley-Terry mode, and no PDF certificates.** The requirement text supplied for pairwise is
  truncated after its heading, so it is unimplemented rather than guessed at; certificates are self-contained
  printable HTML rather than PDF, which keeps the artefact working with the network off and no PDF engine in
  the image. Both are reported as `SKIP` with their reason, never as a pass.
- **T4's outbox has no background worker, by design.** Deliveries are queued in the same transaction as the
  thing that happened and flushed by `POST /api/admin/webhooks/dispatch`, which the console exposes as
  *Dispatch now*. A deployment that never calls it accumulates visible pending deliveries. That is the
  one-command rule applied to outbound calls: nothing to run, nothing to configure, nothing external.
- **The demo dataset and the acceptance dataset are different files.** Every number in the demo section was
  computed against `data/axion-fixtures.json`; the report was produced against `fixtures.json`. They are not
  interchangeable, and confusing them is the easiest way to misread either one.
- **No five-minute demo video is committed yet.** [DEMO.md](./DEMO.md) is the shot list for it, with what
  each shot is meant to prove, so the recording is a matter of following it rather than inventing it.
- **`make` was not executed while building this**, because `make` is not installed on the build machine; the
  equivalent commands in the section above were run directly instead. The full stack has not been brought up
  with Docker here either — the acceptance report in this repository was produced by running their `run.py`
  against a real API server on a real database, which is the same seven requests a `docker compose up`
  portal would receive. [VERIFICATION.md](./VERIFICATION.md) records the commands and their output.
