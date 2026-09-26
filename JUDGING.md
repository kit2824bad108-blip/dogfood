# Judging in Axion — assignment, scoring, normalization

How a hackathon is judged end to end: who scores what, against which criteria, and what happens to those
numbers afterwards. Every claim here is enforced in code and provable from the test suite — the
normalization engine is `api/app/zscore.py`, pure functions with no framework or database imports, so every
claim below is checkable in `api/tests/test_zscore.py`.

| Section | Covers |
| ------- | ------ |
| [1](#1-judge-assignment-strategy) | Judge assignment strategy |
| [2](#2-scoring-methodology) | Scoring methodology, blind tiers, weighted rubric |
| [3](#3-the-problem-with-averaging) | Why averaging fails |
| [4](#4-the-core-idea) | The normalization method |
| [5](#5-estimating-each-judges-mean-and-spread) | Estimating each judge's mean and spread |
| [6](#6-worked-example--the-headline-claim) | Worked example: the harsh 6 versus the generous 8 |
| [7](#7-the-normalization-proof--the-seeded-demo) | **Normalization proof** — the seeded demo |
| [8](#8-what-is-deliberately-not-normalized) | What is deliberately not normalized |
| [9](#9-threats-to-validity) | Threats to validity |

---

## 1. Judge assignment strategy

**Every judge scores every submitted project.** No round-robin, no random subsets, no specialisation by
track.

That is a statistical requirement, not a convenience. A Z-score is only meaningful relative to the
distribution it was standardized against, so two judges' z-scores can only be averaged together if their
verdict sets **overlap**. Give Judge A projects 1–5 and Judge B projects 6–10 with no shared project and
both judges are centred on their own private baseline — comparing them is meaningless, no matter how good
the normalization.

At hackathon scale full coverage is cheap: 10 projects × 5 judges = 50 verdicts, each judge reading ten
repositories. Axion therefore makes full coverage the default and keeps it true as the event changes shape:

| Event | Assignment behaviour | Where |
| ----- | -------------------- | ----- |
| A project is submitted | Every judge gets an assignment | `services.assign_judges` |
| A judge is created | They are assigned every already-submitted project | `services.assign_submission_to_new_judge` |
| Self-healing | `POST /api/admin/assignments/backfill` re-derives any missing pair | `routers/admin.py` |

Three properties of the assignment model are deliberate:

- **Assignments are per (judge, project) pair and unique.** The `assignments` table has a unique constraint
  on the pair, so a retry cannot double-assign or inflate a verdict count.
- **Drafts are never assigned.** A project with `status = 'draft'` has not entered the event; judge
  assignment happens on the transition to `submitted`.
- **An assignment is not a verdict.** Being assigned creates the right to score, nothing more. Coverage is
  visible on the organiser console (**Judging progress**) so an unchecked judge is caught before the
  results are announced, not after.

At larger scale, the honest way to keep z-scores comparable is an overlapping block design plus a
connected comparison graph, and coverage warnings when any project has fewer than two judges. Axion does not
implement that; instead the reconciliation endpoint reports anything short of full coverage, and the admin
console surfaces it as outstanding work.

## 2. Scoring methodology

Judging happens in two tiers, and the order between them is enforced by the API rather than by etiquette.

### Tier 1 — technical, and it comes first

A judge sees the repository, the documentation link, the summary and the Commit Integrity signal. They do
**not** see the demo URL or the video URL. Scoring against those artifacts before the code has been read is
what makes demo-driven judging possible, so the presentation tier is withheld until that judge's technical
verdict has been filed:

```
GET /api/judging/submissions/{id}/presentation
  -> 403  "Submit the technical evaluation before the presentation is unlocked"
```

That `403` is issued by the API, not by the interface. Calling the endpoint with `curl`, with a session
cookie, and with the right role still returns `403`. The blur in the browser is cosmetic; the gate is not.
The one exception is an admin, who can audit any submission.

### Tier 1 is scored against a weighted rubric

A single 1–10 technical score asks a judge to collapse four different judgements into one hop. Axion stores
the parts and derives the whole:

- the active rubric is a row in `rubrics`, `criteria` being a JSON list of `{key, label, weight}`;
- the default rubric is **Innovation 30% / Code Quality 70%**;
- judges score each criterion 1–10 and those values are stored individually in `score_criteria`;
- the technical verdict is the **weight-normalized mean** of the criterion values, clamped to 1–10.

Weights are relative — 30/70, 3/7 and 0.3/0.7 are the same rubric — and the organiser can change them at any
time from **Console → Rubric**. Changing them does **not** rewrite history: each verdict records the
`rubric_id` it was filed against, and the audit entry keeps the individual criterion values, so a later
re-weighting can never retroactively change how a project was scored.

Returning a single integer from the rubric is the one non-obvious decision here. The Z-score engine
consumes exactly one number per verdict, and that contract is what makes the normalization proof below
stable; rubrics changed the way judges *arrive* at a number, not the way the number is normalized.

### Tier 2 — presentation, recorded but not ranked

After Tier 1 is filed, the demo and video unlock for that judge and they file a second 1–10 score with its
own timestamp. It is stored, published in the archive, and shown to organisers as context — but it does not
move the ranking. Section 8 explains why.

### Everything is written down

Every verdict, every revision, every rubric change and every export lands in the append-only `audit_logs`
table with an actor, a timestamp and a client IP. A score filed twice logs `score.technical_submitted` then
`score.technical_modified`, with the previous value and the new one. In Postgres a trigger rejects `UPDATE`
and `DELETE` on that table, so the trail cannot be edited after the fact by anyone, including an
administrator with database access.

---

## 3. The problem with averaging

A raw average treats judges as interchangeable instruments. They are not.

Suppose Judge A grades everything between 3 and 5, and Judge B grades everything between 7 and 9. A
project that both judges consider equally good — the best in the pile — receives a 5 from A and a 9 from
B. Averaged with the rest, the project is punished purely for *which judge happened to score it*.

Averaging measures the grader, not the work.

## 4. The core idea

Standardize each verdict against that judge's own distribution, then average the standardized verdicts:

```
z = (raw − judge_mean) / judge_sigma
```

A 6 from a judge who averages 4.5 with a spread of 1.15 lands at **z = +0.92**. An 8 from a judge who
averages 8.0 lands at **z = +0.66**. The first is the stronger endorsement, even though the raw numbers
say the opposite.

The project's Axion score is the mean of its judges' z-scores, mapped onto a readable scale:

```
display = 50 + 10·z      (clamped to [0, 100])
```

`z = 0` means "exactly what this judge expected". Positive means "better than this judge's average".

## 5. Estimating each judge's mean and spread

Two numbers have to be estimated per judge, and they need different treatment. Both are computed over the
verdicts *within this event*.

### Mean — shrunk toward the pool

A judge with two verdicts has a meaningless mean. Each judge's mean is blended toward the global pool of
all verdicts:

```
judge_mean = w·raw_mean + (1 − w)·global_mean        where w = n / (n + PRIOR_STRENGTH)
```

`PRIOR_STRENGTH = 3.0`. With `n = 1` a judge is only 25% their own opinion; with `n = 10` they are 77%
themselves. A single verdict cannot mint an extreme score.

### Spread — shrunk much more weakly, with an explicit gate

The variance uses a deliberately weaker prior, `PRIOR_VARIANCE = 1.0`:

```
judge_sigma = sqrt( w_var·raw_sigma² + (1 − w_var)·global_sigma² )     where w_var = n / (n + 1.0)
```

**Why so weak?** Because a narrow spread is *information*, not noise. A judge who grades every project 4
or 5 is telling you something precise when they hand out a 6. Shrinking their sigma up toward the pool
would discard exactly the signal that makes normalization useful — it would treat a disciplined grader as
an unreliable one. This is the single most consequential decision in the model, and
[section 7](#7-the-normalization-proof--the-seeded-demo) shows the effect on the seeded event.

### Degenerate cases, handled explicitly rather than blended

| Situation | Behaviour | Rationale |
| --------- | --------- | --------- |
| Judge gave **every** project the same score (`raw_sigma < 0.05`) | `z = 0` for every verdict | Zero dispersion carries no ranking information. Dividing by it would be meaningless. |
| Judge has **exactly one** verdict | spread borrowed from the global pool | One observation has no measurable dispersion; the pool's is the honest stand-in. |
| All scores in the event are identical | every `z = 0`, every display `50.0` | There is genuinely nothing to rank. |
| `effective_sigma` under `SIGMA_FLOOR` (0.05) | treated as non-discriminating | Guards the final division. |
| No verdicts at all | `{}` | No projects, no ranking. |

## 6. Worked example — the headline claim

Two judges score five shared projects, then one unshared project each. Judge 1 grades on a 3–6 band;
Judge 2 on a 7–9 band.

Raw verdicts:

| Project | Judge 1 | Judge 2 | Naive average |
| ------- | ------- | ------- | ------------- |
| 1 | 3 | 7 | 5.00 |
| 2 | 4 | 8 | 6.00 |
| 3 | 4 | 8 | 6.00 |
| 4 | 5 | 9 | 7.00 |
| 5 | 4 | 8 | 6.00 |
| **6** | **6** | — | **6.00** |
| **7** | — | **8** | **8.00** |

Estimated judge statistics (both judges have `n = 6`, so both keep 66.7% of their own mean):

| Judge | Raw mean | Raw σ | Effective mean | Effective σ |
| ----- | -------- | ----- | -------------- | ----------- |
| 1 | 4.333 | 0.943 | 4.944 | 1.153 |
| 2 | 8.000 | 0.577 | 7.389 | 0.924 |

Result:

| Rank | Project | Raw avg | Axion | z | Rank ± |
| ---- | ------- | ------- | ----- | - | ------ |
| 1 | **project-6 (the harsh 6)** | 6.00 | **59.16** | **+0.916** | **+5** |
| 2 | project-4 | 7.00 | 58.96 | +0.896 | 0 |
| 3 | **project-7 (the generous 8)** | 8.00 | **56.62** | **+0.662** | **−2** |
| 4 | project-2 | 6.00 | 49.21 | −0.079 | −1 |
| 5 | project-3 | 6.00 | 49.21 | −0.079 | −1 |
| 6 | project-5 | 6.00 | 49.21 | −0.079 | −1 |
| 7 | project-1 | 5.00 | 39.46 | −1.054 | 0 |

The naive average ranks the raw 8 first. Axion ranks the harsh 6 first, and lifts it five places.

## 7. The normalization proof — the seeded demo

The seed script (`api/app/seed.py`) ships a dataset shaped to make the mechanism visible. Five judges:

| Judge | Profile (μ, σ) | Realized mean | Realized σ | Effective σ |
| ----- | -------------- | ------------- | ---------- | ----------- |
| disciplined | 4.5, 0.60 | 4.70 | 1.00 | 1.07 |
| expansive | 7.5, 3.40 | 7.80 | 2.32 | 2.26 |
| balanced-a | 6.5, 1.00 | 6.40 | 0.80 | 0.90 |
| balanced-b | 6.0, 1.10 | 6.30 | 0.64 | 0.78 |
| balanced-c | 7.0, 0.90 | 7.10 | 0.54 | 0.71 |

Two projects are crafted so the two rankings disagree. The three balanced judges give both projects the
same score, so they contribute no difference — the outcome is driven entirely by the two extreme graders:

- **Quiet Craft** — rated 7 by the disciplined grader (at the ceiling of their band), 4 by the expansive one.
- **Flashy Demo** — rated 3 by the disciplined grader, 10 by the expansive one.

| Rank | Project | Raw avg | Axion | z | Rank ± |
| ---- | ------- | ------- | ----- | - | ------ |
| … | | | | | |
| 5 | **Quiet Craft** | 6.00 | **48.79** | −0.121 | **+2** |
| 6 | Realtime Chat Mesh | 6.20 | 48.07 | −0.193 | 0 |
| 7 | **Flashy Demo** | 6.40 | **46.66** | −0.334 | **−2** |
| 8 | Edge Cache Optimizer | 5.60 | 45.07 | −0.493 | +2 |
| 10 | Distributed Ledger UI | 5.80 | 41.36 | −0.864 | −2 |

The naive average prefers the demo by 0.40 points. Axion prefers the craft by 2.13. The two projects swap
places, and other projects move too — this is not a rigged pair sitting alone.

The parameters were not hand-waved: they were selected by searching the space against the real `zscore`
module and keeping the configuration that maximised the normalized gap subject to the naive average
genuinely favouring the demo. `python -m app.seed` prints the table above.

### Reproducing the proof yourself

```bash
cd api && python -m app.seed          # prints the raw-vs-normalized table above
docker compose exec api python -m pytest tests/test_zscore.py -q   # the math, in isolation
make acceptance                      # end-to-end, tier by tier -> acceptance-report.txt
```

The seeded verdicts also carry per-criterion `score_criteria` rows whose values mirror the technical
verdict, so the derived weighted score equals the stored one exactly. That is deliberate: it keeps the
normalization proof above byte-for-byte identical to what the seed prints, while still exercising the
rubric path that a real judge walks through.

## 8. What is deliberately *not* normalized

**Presentation scores are recorded, archived, and excluded from the ranking.** The Axion score is computed
from technical verdicts only.

This is the "best code wins, not the best pitch" claim taken literally: if a compelling demo could move
the ranking, the blind-evaluation gate would be theatre. Presentation verdicts remain in the database and
in the archive bundle, visible to organisers as context — and as a documented tie-breaker when two
projects are genuinely inseparable.

If an event wants a weighted composite, it is a small change: normalize each tier separately with the same
function, then combine with explicit weights (for example `0.7·z_technical + 0.3·z_presentation`) and
publish the weights alongside the results.

## 9. Coverage, provisional scores and balanced assignment

Sections 1–8 describe an event with full coverage. The cases where coverage is *not* full need saying too,
because the convenient answer and the honest one differ.

**A missing verdict is missing, not zero.** If a project has three assigned judges and one never filed, its
normalized score is computed from the two verdicts that exist; the third contributes nothing. The project is
marked `provisional` in the leaderboard while its verdict count is below the minimum (3 by default). A
project with assignments and *no* verdicts does not appear in the ranking at all — it is listed separately
under `unranked`, with the number of judges assigned to it. Nothing anywhere substitutes 0 for a missing
score, because a 0 would drag the average down in a way no judge voted for.

**Coverage is reported next to the score, not instead of it.** The leaderboard payload, both CSV exports
(`reviews_filed`, `reviews_expected`, `confidence`) and the organiser console all carry the count, so it is
visible that the top project was rated by 3 of 3 judges while the second was rated by 2 of 5.

**Balanced assignment.** Full coverage is right at ten projects and wrong at five hundred, where it would
mean 20,000 judgments. `POST /api/admin/assignments/balance` plans a target number of reviews per project
with a balanced load, greedy in this order: projects with the fewest existing assignments first; then, for
each, the eligible judges with the smallest current load; never exceeding `max_projects_per_judge`. It
reports what it *would* do — coverage, reviews per project, load distribution, and the number of connected
components in the judge/project overlap graph — before anything is written, and the default is a dry run.
Two honest notes: the cap applies to assignments the plan *adds*, so an existing load above it is reported
rather than reduced by deleting someone's work; and more than one connected component means the ranking is
really several separate rankings, which the response says rather than hides.

The demo dataset stays full-coverage. Every number in section 7, in README.md and in the committed reports
was computed against it, and a plan that silently rewrote those numbers would make the documentation untrue.

## 10. Threats to validity

Stated plainly, because a scoring system that hides its weaknesses is not defensible:

- **Scores are ordinal-ish, not interval.** Treating 4 and 5 as equally spaced is an assumption. It is the
  standard one, and it is why the output is published as a rank plus a normalized score rather than a
  verdict on absolute quality.
- **Normalization cannot detect a judge who is wrong in the same direction every time.** It corrects
  *scale*, not *taste*.
- **Collusion and score-trading shrink toward invisibility.** If a judge gives their friend's project a 9
  and everything else a 5, that shows up as a large z — but a coordinated block of judges all doing it
  would not be caught by this model alone. The audit trail exists for that, not the math.
- **Small samples remain small samples.** Shrinkage makes a two-verdict judge usable; it does not make
  them informative.
- **Commit dates are client-controlled** and squash merges collapse history, so Commit Integrity is a
  review prompt. It is not part of the score at all, and no automated penalty is applied.
