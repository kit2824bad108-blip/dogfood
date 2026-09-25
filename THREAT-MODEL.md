# Axion threat model

What Axion is defending against, what it is not, and which mechanism sits behind each claim. Written to be
falsifiable: every mitigation below names the code or the schema object that implements it, and the
residual risk is stated next to it rather than omitted.

## Scope and assets

Axion runs one hackathon per deployment. The assets worth attacking:

| Asset | Why it matters |
| ----- | -------------- |
| The ranking | It decides who wins. Manipulating it is the whole game. |
| Verdict integrity | A score that can be edited after the fact makes the ranking unverifiable. |
| The audit trail | It is the evidence base if a result is disputed. |
| Participant identity | Impersonation enables ballot stuffing and submission theft. |
| Judge identity | A hijacked judge account is the most direct path to a rigged result. |

## Trust boundaries

- **Browser → Next.js (:3000).** Untrusted. Everything from a browser is attacker-controlled: form values,
  the session cookie value, and the system clock.
- **Next.js → FastAPI (:8000).** The browser never talks to the API directly; `/api/*` is rewritten, so the
  session cookie is same-origin and there is no CORS hole to open.
- **API → Postgres.** Trusted, but not blindly: the append-only guarantee is enforced by a database trigger
  rather than by the application, so application compromise does not imply history compromise.
- **API → GitHub.** Untrusted input (commit dates, repository metadata) and an availability dependency,
  which is why `MOCK_GITHUB` exists.

The organising principle: **anything a browser can lie about is either verified server-side or excluded from
the decision.** Client clocks, client-side validation and client-side hides are treated as user experience,
never as control.

---

## Threat 1 — Sybil voting

*The attack:* flood the event with accounts, or arrange for the same person to hold multiple identities, and
use those accounts to vote a project to the top.

**Why it does not work here: participants cannot vote at all.**

Axion has no participant-facing vote. Scoring routes are guarded by
`Depends(require_role(*JUDGE_ROLES))` in `api/app/routers/judging.py`, and `JUDGE_ROLES = ("judge", "admin")`.
A participant account calling `POST /api/judging/scores` is rejected by the role guard before any handler
logic runs, no matter how many accounts are created. Creating accounts is cheap; the accounts are useless.

Layered on top of that:

| Mechanism | Implementation | Effect |
| --------- | -------------- | ------ |
| Role isolation | `require_role` in `api/app/deps.py`, applied per route | Only judges and admins can score |
| Judge accounts are issued, not self-served | `POST /api/admin/judges` is admin-only; `POST /api/auth/register` hardcodes `role="participant"` | Mass registration cannot produce a judge |
| One verdict per pair | `uq_score_pair` unique constraint on `scores` | A judge's retry overwrites their verdict; it cannot add a second one |
| Assignment is a prerequisite | `_assignment()` check in the scoring handler | A judge cannot score a project outside their assignments |
| Attribution | `audit_logs.actor_id` + `actor_email` + `ip`, written on every score and every login attempt | Identity is recorded, so a coordinated cluster is *visible* |
| Full-coverage assignment | Every judge scores every project (`services.assign_judges`) | An injected judge is a large, conspicuous change to the calibration table, not a quiet one |

**Residual risk — stated honestly.** Sybil *registration* is not prevented: anyone can create participant
accounts, and a determined organiser could create many judge accounts. What is prevented is those accounts
having any effect on the score without being visible in the audit trail and in the grader calibration table,
where an extra judge standing far from the pool is exactly what `judge_statistics` highlights.

**The dev-login exception, and why it is contained.** `POST /api/auth/dev-login` signs in without a password,
which is by design for air-gapped demos. It is gated on `settings.local_dev_login`, which is true only when
the deployment already declares itself a demo (`MOCK_GITHUB=true` or `SEED_DEMO=true`, or an explicit
`LOCAL_DEV_LOGIN=true`). On a real deployment with a real database these are false and the endpoint returns
`403` — and the flag is documented in `.env.example` and README.md so it cannot be enabled by accident.
A deployment that turns on `SEED_DEMO` for a live event has, in effect, published an admin login; that is the
one configuration mistake this design cannot defend against, so it is called out here and in the README.

## Threat 2 — Judge collusion

*The attack:* judges agree in advance to inflate each other's projects, or a block of judges agrees to bury
one participant.

**Mitigation 1 — make collusion information-poor.** The blind gate (`403` from
`GET /api/judging/submissions/{id}/presentation` until that judge's technical verdict exists) means a
technical score is filed before the judge has seen the demo or the video, so there is nothing to coordinate
around yet. The technical tier is the tier that ranks; the colludable tier is not ranked. Assignments are
also never exposed to participants, so a competitor cannot identify which judge to approach, and the public
gallery deliberately withholds demo and video links for the same reason.

**Mitigation 2 — make collusion statistically loud.** Z-score normalization removes each judge's scale, so a
judge who inflates uniformly gains nothing: their mean moves and their own verdicts are re-centred. What
survives normalization is *relative* deviation, and relative deviation is precisely what
`zscore.judge_statistics` and the console's **Grader calibration** table expose:

- `raw_mean` and `raw_sigma` per judge, next to `discriminative`;
- the spread comparison that makes a narrow-band grader informative and a wide-band grader weak;
- per-verdict `z_score` in the exported `scores.csv`, so outliers are one sort away from being found.

A judge who systematically rates one project far above their own distribution produces a large positive z for
that project. That is the intended behaviour — an outlier is *surfaced*, not smoothed away.

**Mitigation 3 — make collusion unnecessary.** Every judge scores every project, so there is no assignment
to trade and no coverage gap to exploit; the reconciliation endpoint exists to detect a missing pair rather
than to leave one.

**Residual risk.** A coordinated majority all inflating the same project by the same amount within their own
distributions is not detectable by normalization alone — this is stated plainly in JUDGING.md §9. The defence
in that case is procedural and evidentiary: the append-only audit trail, the per-verdict z-scores and the
archive bundle mean a disputed result can be reconstructed and argued about with evidence rather than
recollection. Axion makes collusion auditable; it does not claim to make it impossible.

## Threat 3 — Deadline gaming

*The attack:* edit a submission after the deadline — after seeing other projects, after hearing the
questions, or after the organiser has begun judging.

**The control is the server clock.** `services.submission_window_closed()` compares
`datetime.now(timezone.utc)` against `settings.event_end`, and `POST /api/submissions` calls it before
touching the row. The browser is never asked whether the window is open, and no client-supplied timestamp is
ever trusted. The UI shows a closed-window banner and disables the form, but that is courtesy: `curl` with a
valid session gets the same `403`.

| Attempt | Result |
| ------- | ------ |
| Save a draft after the deadline | `403`, and the attempt is logged |
| Submit a new project after the deadline | `403`, logged |
| Edit a submitted project after the deadline | `403`, logged |
| Revert a submitted project to a draft | `400` — a submitted project cannot be un-submitted at all |
| Backdate `created_at` / `updated_at` | Not client-settable; server defaults and `onupdate=now()` |

Rejections are recorded, not silently dropped: `submission.rejected_after_deadline` lands in `audit_logs`
with the actor, the IP, the attempted status and the deadline that was in force. A late edit attempt is
therefore evidence of intent, which is more useful to an organiser than an anonymous `403`.

**Related: integrity of *when* the work happened.** Commit Integrity compares the repository's commit
history against the event window and flags repositories whose history predates the event
(`api/app/github.py`, surfaced at `/api/admin/flagged`). It is explicitly advisory — commit dates are
client-controlled, and squash merges collapse history — so it produces a review queue and never an automatic
disqualification. `integrity_*` columns are not read by the ranking code at all.

**Residual risk.** The deadline is enforced on writes through the API. Anyone with direct database access can
write whatever they like — and will be doing it *without* an audit row, which is observable as a gap in the
trail rather than as an edited entry. Guarding the database itself is deployment hardening (network policy,
credentials), not application logic.

## Threat 4 — Identity and session attacks

| Vector | Mitigation | Residual |
| ------ | ---------- | -------- |
| Password cracking from a stolen hash | PBKDF2-HMAC-SHA256, 200,000 iterations, per-password 16-byte salt (`api/app/security.py`) | No server-side rate limit on `POST /api/auth/login`; the attempt *is* logged as `auth.login_failed` |
| Session forgery | HMAC-SHA256 signed cookie with an embedded expiry, verified with `hmac.compare_digest` | `SECRET_KEY` defaults to a documented dev value; README and `.env.example` both say to change it, and rotation invalidates sessions |
| Token theft via XSS | Session cookie is `HttpOnly` and `SameSite=Lax`, so script cannot read it and cross-site posts do not carry it | An XSS hole in the app would still allow actions as the user, but not extraction of the cookie |
| Plaintext session over the wire | `COOKIE_SECURE=true` marks the cookie `Secure` for HTTPS deployments | Off by default so `http://localhost` works; must be turned on behind TLS |
| OAuth CSRF / replay | Random `state` in a short-lived `axion_oauth_state` cookie, compared byte-for-byte on callback | None in the implemented path; the live GitHub flow is untested here because no credentials exist in this environment |
| Identity confusion between GitHub and password accounts | `github_id` and `email` are both unique; the callback matches on `github_id` first, then email, and links the two | An email collision between providers merges accounts by design; a verified-email check happens at GitHub |

## Threat 5 — Evidence tampering

The audit trail is the artefact a disputed result is settled with, so it is protected in three places:

1. **The database.** The `axion_audit_logs_immutable` trigger rejects `UPDATE` and `DELETE` on
   `audit_logs`, installed by the `0001_initial` migration. Application compromise does not defeat it.
2. **The API.** There is no update or delete route for audit entries at all — the only route is
   `GET /api/admin/audit`, admin-only, read-only, and capped at 500 rows per call.
3. **The archive.** `POST /api/admin/archive` produces a self-contained JSON + Markdown bundle of results,
   verdicts, per-verdict z-scores and judge calibration, which can be published to a public repository and
   timestamped independently of the database.

**Residual risk.** The trigger is Postgres-specific and is not exercised by the SQLite test suite; the
append-only behaviour of the *application* layer is tested instead (`test_audit_trail_*` in
`api/tests/`), and the trigger is exercised only against Postgres, outside the automated suite. That gap is
real and is listed here rather than hidden.

## Threat 6 — Availability and dependency

| Dependency | Attack or failure | Mitigation |
| ---------- | ----------------- | ---------- |
| GitHub OAuth | Wi-Fi off, GitHub down, or credentials not configured | `MOCK_GITHUB` / `SEED_DEMO` enable the offline **Local Dev Login** (`POST /api/auth/dev-login`) and seeded `.local` accounts; participants can always register with email and password |
| GitHub API | Rate limiting (60 req/h unauthenticated) | `GITHUB_TOKEN` raises it to 5000; the integrity check degrades to an explanatory "unavailable" state instead of failing the submission |
| Postgres | Container not ready on boot | `docker compose` healthcheck plus `depends_on: service_healthy`; the API applies migrations at start |
| Judging integrity under offline conditions | Judge cannot log in at all | The dev-login path is the whole point: no external call is made, so the event can be judged with the network off |

## Out of scope

Named so that nobody assumes coverage that is not there:

- **Denial of service** — no rate limiting, no WAF, no request quotas. This is a self-hosted event tool.
- **Malicious submissions** — Axion stores repository and demo URLs; it never clones, executes or renders
  participant code. Judging a link is a human action with a human risk model.
- **Host and container hardening** — the Compose file is a demo-grade deployment, not a hardened one.
  Rootless containers, secrets management and TLS termination are deployment concerns.
- **Participant privacy law compliance** — the audit trail deliberately records email addresses and client
  IPs. That is what makes it evidence; an organiser handling personal data must document their own basis for
  doing so.
- **Insider threat with database and application access** — an operator who can read `SECRET_KEY`, mint a
  session and edit tables rows directly is outside what any application-level control can detect. The
  remaining defence is the audit trail's *completeness* and the archive, not prevention.

## Reporting

If you find a hole in any of the above, the interesting question is not "is there a bug" but "does it let
someone move the ranking, or does it let someone erase the evidence". Those two are the only outcomes this
document is trying to prevent.
