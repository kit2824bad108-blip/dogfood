# The Axion scoring model

This document explains what Axion computes, why it is defensible, and where it deliberately stops. The
implementation is `api/app/zscore.py` — pure functions with no framework or database imports, so every
claim below is provable in `api/tests/test_zscore.py`.

---

## 1. The problem with averaging

A raw average treats judges as interchangeable instruments. They are not.

Suppose Judge A grades everything between 3 and 5, and Judge B grades everything between 7 and 9. A
project that both judges consider equally good — the best in the pile — receives a 5 from A and a 9 from
B. Averaged with the rest, the project is punished purely for *which judge happened to score it*.

Averaging measures the grader, not the work.

## 2. The core idea

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

## 3. Estimating each judge's mean and spread

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
an unreliable one. This is the single most consequential decision in the model, and section 6 shows the
effect on real data.

### Degenerate cases, handled explicitly rather than blended

| Situation | Behaviour | Rationale |
| --------- | --------- | --------- |
| Judge gave **every** project the same score (`raw_sigma < 0.05`) | `z = 0` for every verdict | Zero dispersion carries no ranking information. Dividing by it would be meaningless. |
| Judge has **exactly one** verdict | spread borrowed from the global pool | One observation has no measurable dispersion; the pool's is the honest stand-in. |
| All scores in the event are identical | every `z = 0`, every display `50.0` | There is genuinely nothing to rank. |
| `effective_sigma` under `SIGMA_FLOOR` (0.05) | treated as non-discriminating | Guards the final division. |
| No verdicts at all | `{}` | No projects, no ranking. |

## 4. Worked example — the headline claim

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

## 5. Why every judge scores every project

Z-scores are only comparable across judges whose verdicts **overlap**. If Judge A scores projects 1–5 and
Judge B scores projects 6–10 with no shared project, both judges' z-scores are centred on their own private
baselines and comparing them is meaningless.

At hackathon scale this is easy to satisfy honestly: 10 projects × 5 judges = 50 verdicts. Axion therefore
assigns **every judge to every submission** — new judges are backfilled onto all existing submissions and
vice versa. `MATH.md` is the reason that is a design rule rather than a coincidence.

For larger events, the fix is to anchor judges through shared projects and compute a connected comparison
graph; Axion does not attempt that, and the admin console warns when any project has fewer than two
judges.

## 6. The seeded demo, and what it demonstrates

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

## 7. What is deliberately *not* normalized

**Presentation scores are recorded, archived, and excluded from the ranking.** The Axion score is computed
from technical verdicts only.

This is the "best code wins, not the best pitch" claim taken literally: if a compelling demo could move
the ranking, the blind-evaluation gate would be theatre. Presentation verdicts remain in the database and
in the archive bundle, visible to organisers as context — and as a documented tie-breaker when two
projects are genuinely inseparable.

If an event wants a weighted composite, it is a small change: normalize each tier separately with the same
function, then combine with explicit weights (for example `0.7·z_technical + 0.3·z_presentation`) and
publish the weights alongside the results.

## 8. Threats to validity

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
