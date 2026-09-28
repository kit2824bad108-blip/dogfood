# `spec/` — the organisers' published files, as published

DOGFOOD 2026 publishes the whole interface up front, including the checker's source, so that nobody has to
guess what will be verified. This directory keeps those published files next to the code they constrain, so
a reader can diff the requirement against the implementation without leaving the repository.

| File | Where it really lives | Why it is here |
| --- | --- | --- |
| [`spec.md`](./spec.md) | <https://dogfoodhack.com/spec/spec.md> | The full brief: the tier ladder, the scoring weights, the repo layout, the rules and the disqualifiers. |
| [`example.dogfood.toml`](./example.dogfood.toml) | <https://dogfoodhack.com/spec/example.dogfood.toml> | The organisers' worked example of the manifest, copied verbatim. It is the shape reference for [`/.dogfood.toml`](../.dogfood.toml). |

Two of the four files are not duplicated into this directory, because the root of the repository *is* the
place the brief asks for them:

| File | Where it lives here | Why not in `spec/` |
| --- | --- | --- |
| `run.py` | [`/run.py`](../run.py) | The brief says the checker is run from the repo root (`python3 run.py .dogfood.toml`). It is vendored unmodified, and its SHA-256 is recorded in [`/README.md`](../README.md) so a reader can confirm this repository did not edit the thing that judges it. |
| `fixtures.json` | [`/fixtures.json`](../fixtures.json) | Same reason: it is the dataset the portal is seeded with, and `docker compose up` mounts it from the root. Vendored unmodified, SHA-256 recorded alongside. |

Also published, and deliberately not vendored — both are reachable at their canonical URLs and neither
constrains the code:

- <https://dogfoodhack.com/spec/context.txt> — the same brief flattened into one plain-text message for
  pasting into an assistant. Its content is `spec.md` plus the timeline, tier ladder and scoring sections,
  so keeping a second copy here would only create a second thing to go stale.
- The rendered spec page itself, <https://dogfoodhack.com/spec>, whose Appendices reproduce `run.py` and
  `fixtures.json` inline.

If the organisers publish a revision after the code freeze, replace the file here and in the root together;
the fingerprints in `README.md` are what make that difference visible.
