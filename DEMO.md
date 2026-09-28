# The five-minute demo

The brief asks for one recording showing **one full event lifecycle: create, submit, judge, publish.** This
is the shot list for it. Every beat below is something the portal already does — nothing here needs code to
be written first, only a camera and a timer.

The recording itself is not committed to the repository (it is several hundred megabytes); link it wherever
you like, as the brief allows. This file exists so the link has something to be checked against.

## Before you press record

Two terminals and one browser window. Everything below is copy-pasteable.

```bash
# Terminal 1 — the stack, on the *demo* dataset, i.e. a live 72-hour window.
# The acceptance dataset has already closed (2026-03-01T18:00:00Z), which is
# correct for the report and useless for a lifecycle demo: nothing can be
# submitted into a closed event.
SEED_MODE=demo EVENT_SOURCE=env SEED_DEMO=false docker compose up --build

# Terminal 2 — a second participant, for the invite-link beat. Optional;
# a second browser profile reaches the same instance.
open http://localhost:3000
```

The seed prints the four logins at boot (`docker compose logs api | head -20`). They are also in
[README.md](./README.md#authentication-for-the-checker), and the passwords are the same single-use
passwordless path the checker's headers use:

| Role | Sign-in | Who it is |
| ---- | ------- | --------- |
| Organiser | `organiser@sample-hack-2026.dogfood` | The account that runs the event |
| Participant | `priya1@example.org` | A team member, from the dataset |
| Judge | created in shot 6 | — |
| Community voter | any address, in shot 10 | No account: the ballot link is the identity |

Before recording: close every other tab, set the browser to 100% zoom, and hide the bookmarks bar. Size the
window to 1600×900 so the tables do not wrap.

## The shots

| # | Time | On screen | What you do | What it proves |
| - | ---- | --------- | ----------- | -------------- |
| 1 | 0:00–0:20 | `/` | State the premise in one sentence: a self-hostable portal where the deadline is enforced by the API, judges cannot see each other's scores, and the ranking is normalized. | There is a product, not a mockup. |
| 2 | 0:20–1:00 | `/admin` → *Tracks & prizes*, *Rubric* | Create a track and a prize; edit the rubric weights and watch the percentage preview update. | **create** — the event is configurable by the organiser, and the weighting is data, not a constant. |
| 3 | 1:00–1:40 | `/team` → `/submit` | Sign in as the participant, create a team, copy the invite code, paste it into a second tab as a second account. Then save a project as a **draft**. | **submit** — onboarding, team formation by invite, and a draft that is genuinely private (no gallery listing, no assignment). |
| 4 | 1:40–2:10 | `/submit` | Promote the draft to submitted. Show the Commit Integrity reading: the share of sampled commits authored inside the window, and what happens when it is low. | **submit** — intake is real, and provenance is reported rather than assumed. |
| 5 | 2:10–2:40 | `/gallery` | Search and filter by track. Point out that demo and video links are absent from the public payload, not merely hidden in the markup. | The public surface is searchable *and* does not leak the presentation tier. |
| 6 | 2:40–3:40 | `/admin` → *Event setup* | Create a judge, backfill assignments, then sign in as that judge and open `/judge`. Show the assignment list, then open one project: repository and docs visible, **presentation locked**. `curl` the project endpoint and show the `403`. | **judge** — the blind tier boundary is enforced by the API. This is the claim the brief calls the one that matters most. |
| 7 | 3:40–4:20 | `/judge/score/[id]` | Score the technical rubric, submit, watch presentation unlock, then score that too. | **judge** — a weighted rubric, in the order the portal dictates. |
| 8 | 4:20–4:50 | `/admin` → *Leaderboard* | Toggle **Naive average** ↔ **Axion normalized**. Point at the rank ± column: a project moves, and [JUDGING.md](./JUDGING.md) says why. Export the leaderboard CSV and open it. | **publish** — the normalization is visible, not folklore, and the CSV export is one click. |
| 9 | 4:50–5:00 | `/admin` → *Archive* | Generate the archive and show the `RESULTS.md` preview. | **publish** — the results are a portable artefact. Nothing is deleted. |

## If you have ninety seconds left, show the community surface

The five-minute lifecycle above is what the brief asks for. T3 and T4 are the parts a viewer cannot see in a
judging walkthrough, so they get a short second act — and each beat is a *refusal* or a *proof*, because that
is where the interesting decisions are.

| # | Time | On screen | What you do | What it proves |
| - | ---- | --------- | ----------- | -------------- |
| 10 | +0:00–0:30 | `/vote` | Enter an address, follow the returned link (no mail server, and the page says so), then cast one score. Press a second score on the same project and show the `409`. | **T3** — an address plus a link is the gate, and a vote is final rather than editable. |
| 11 | +0:30–0:50 | `/results` | Show the refusal: while the window is open there is no tally, only the reason. | **T3** — results hidden until the close, and the refusal explains itself. |
| 12 | +0:50–1:10 | `/projects/[id]` | Post a comment as the verified voter; post the same words again and show the `409`; then post from a private window and show that an unidentified commenter is refused. | **T3** — the thread is public, the identity behind it is not, and flooding is bounded. |
| 13 | +1:10–1:30 | `/admin` → *Webhooks*, *Records*, *Export & import* | Register a receiver, press **Test**, then **Dispatch now** and show the delivery go green with its HTTP status. Issue records, open one certificate, revoke it and show the signature unchanged. Download the bundle, show the checksum, then show the dry-run import reporting updates and no creations. | **T4** — signed outbound delivery, signed records anyone can verify, and an event that can leave the deployment it was run on. |

Shot 13 is the one to leave the room with: the last three panels are what an organiser needs *after* the
event, and none of them requires a cloud account, a worker process or a second service.

## The two things worth saying out loud

**The deadline is enforced by the API, not by the clock in the interface.** Shot 4 is where it is worth
naming: the demo dataset's window is the one the brief describes as live, and the *acceptance* dataset is
seeded with a `submissions_close` already in the past — which is exactly why the organisers' checker's
"closed event refuses submissions" check passes. Both postures are the same code path; only the data
differs. Show it if you have the seconds:

```bash
# Against the acceptance posture (SEED_MODE=fixtures), the probe is refused by the
# deadline before the body is parsed — a 403 about the window, not a 422 about a
# missing field.
curl -i -X POST http://localhost:3000/api/submissions \
  -H 'Content-Type: application/json' \
  -H 'Cookie: session=axion-participant-1' \
  -d '{"title":"dogfood-late-submission-probe","summary":"probe"}'
```

**The math is documented, and the documentation was written before the video.** Shot 8's number is not
"scores look better"; it is a method with a proof, a worked example and a stated limit
([JUDGING.md](./JUDGING.md) §7 and §11). If a judge asks one question about this project, this is the one to
answer well.

## Recording and publishing

```bash
# 1080p, 30fps, no commentary required (the brief asks for the lifecycle, not a voiceover)
ffmpeg -f gdigrab -framerate 30 -video_size 1600x900 -i desktop -c:v libx264 -preset veryfast \
       -pix_fmt yuv420p demo.mp4
```

Link `demo.mp4` from [README.md](./README.md). Nothing in the repository depends on it: the acceptance
report, the test suite and the documentation stand on their own, which is the point — the video shows the
product, and the report shows the evidence.
