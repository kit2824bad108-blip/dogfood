# Axion

**The fundamental engine for trustless hackathon execution.**

Hackathon judging is broken. It relies on opaque averaging, emotional bias from flashy demos, and
logistical friction. Axion is an open-source, self-hostable hackathon lifecycle platform that replaces
subjective averaging with mathematically defensible Z-score normalization and enforces blind technical
evaluation.

> Axion doesn't just collect submissions; it audits the judging process, so the best code wins — not the
> best pitch.

---

## Run it (one command)

```bash
cp .env.example .env      # optional: every value has a working default
docker compose up --build
```

Then open **http://localhost:3000**. On first boot the API container applies migrations and, because
`SEED_DEMO=true`, loads a demo event: 50 participants, 10 teams, 10 submissions, 5 judges and 50 verdicts
(50 technical + 50 presentation scores).

The seed prints a comparison to the container logs — the naive average and the Axion leaderboard disagree
on purpose:

| Rank | Project | Raw avg | Axion | z | Rank ± |
| ---- | ------- | ------- | ----- | - | ------ |
| 7 | Flashy Demo | 6.40 | 46.66 | −0.334 | −2 |
| 5 | Quiet Craft | 6.00 | 48.79 | −0.121 | **+2** |

The flashy demo wins on the naive average. The quiet craft wins under Axion, because it was rated at the
ceiling of a grader who works a narrow band — the more informative signal.

## Demo accounts

| Role | Email | Password |
| ---- | ----- | -------- |
| Organiser | `admin@axion.dev` | `axion-admin` |
| Judge (narrow band) | `disciplined@axion.dev` | `axion-judge` |
| Judge (wide range) | `expansive@axion.dev` | `axion-judge` |
| Judge (balanced) | `balanced-a@axion.dev` | `axion-judge` |
| Participants | `hacker01@axion.dev` … `hacker50@axion.dev` | `axion-hacker` |

The seeded judges have already filed verdicts for every project, so nothing is locked for them. To watch
the blind boundary work, sign in as the organiser, create a judge in *Console → Event setup*, then sign in
as that judge: every demo and video link is withheld until the technical verdict is submitted.

## The three pillars

**1. Trustless intake.** GitHub OAuth for team formation, invite codes for teammates, one submission per
team. On submit, the **Commit Integrity** check samples the repository's commit history and reports the
share authored inside the event window. A repository whose history predates the event is flagged for an
organiser to review — never automatically disqualified. See `api/app/github.py`.

**2. The Axion Core.** Two mechanisms, both enforced in the API rather than the interface:

- **Blind evaluation tiers.** Judges see the repository and documentation first. The demo and video links
  are withheld until that judge's technical verdict exists. Calling the endpoint directly returns `403`,
  and the assignment list never includes the links. The blur in the UI is cosmetic.
- **Z-score normalization.** Each judge is standardized against their own grading distribution before
  verdicts are averaged, so a strict grader's 6 can outrank a generous grader's 8. Full write-up in
  [MATH.md](./MATH.md).

Every score submission, alteration, role change and archive action is written to an append-only
`audit_logs` table with a timestamp and client IP. In Postgres a trigger rejects `UPDATE` and `DELETE` on
that table.

**3. The ephemeral archive.** The organiser clicks *Generate archive* and gets a self-contained JSON
bundle plus a `RESULTS.md` results page ready for GitHub Pages. Nothing is deleted; the database can be
spun down afterwards.

## Repository layout

```
api/                 FastAPI service — owns the database, all auth and all math
  app/zscore.py      the normalization engine (pure functions, no framework imports)
  app/github.py      commit integrity
  app/routers/       auth, teams, submissions, judging, admin
  alembic/           migrations
  tests/             pytest suite, including the math proofs
web/                 Next.js App Router frontend (Tailwind + shadcn-style components)
docker-compose.yml   db + api + web
```

The browser only ever talks to `/api/*` on port 3000; Next.js rewrites those calls to the API container,
which keeps the session cookie same-origin and hides the API port.

## Tests

```bash
make test                                  # or: cd api && python -m pytest -q
docker compose exec api python -m pytest -q   # no local Python setup needed
cd web && npm run typecheck
```

The suite covers the normalization math (including the harsh-6-versus-generous-8 claim, zero-variance
judges and single-verdict judges), the blind-evaluation boundary, commit-integrity parsing, and the auth
and role model.

## Configuration

All variables are documented in `.env.example`. The ones that matter most:

| Variable | Purpose |
| -------- | ------- |
| `SECRET_KEY` | Signs session cookies. **Change before exposing the app.** |
| `EVENT_START` / `EVENT_END` | The commit-integrity window. Defaults to the 72 hours ending now. |
| `GITHUB_CLIENT_ID` / `GITHUB_CLIENT_SECRET` | Enables "Continue with GitHub". Register the callback `{WEB_URL}/api/auth/github/callback`. Without them participants register with email. |
| `GITHUB_TOKEN` | Raises the GitHub API limit from 60 to 5000 requests/hour. |
| `MOCK_GITHUB` | Computes commit integrity from deterministic synthetic data. Keep `true` for offline demos. |
| `SEED_DEMO` | Loads the demo dataset on boot. Set `false` for a clean event. |

## Honest limitations

- **Commit dates are client-controlled.** A team can rewrite them, and squash merges collapse history.
  Commit Integrity is a review prompt, not proof.
- **Commits are not lines of code.** The signal is *when work happened*, not how much.
- **Z-scores assume overlapping coverage.** They are comparable because every judge scores every project
  at this scale; see MATH.md for the caveat at larger events.
- **Participants are not emailed.** Judges are created by the organiser, who hands out credentials.
