#!/usr/bin/env bash
set -euo pipefail

echo "[axion] applying database migrations..."
alembic upgrade head

# Which dataset to seed on boot.
#
# `SEED_MODE` names the dataset, so it selects one. The older `SEED_DEMO=true`
# switch still works, and the acceptance brief's one-flag
# `DOGFOOD_FIXTURE_MODE=true` alias still selects the fixture dataset on its own,
# so an evaluator who exports only that flag gets a seeded instance rather than
# an empty event that silently looks fine.
#
# Reading `SEED_MODE=demo` here matters: the documented way to run the crafted
# demo dataset is `SEED_MODE=demo EVENT_SOURCE=env docker compose up`, and while
# this script ignored that value the API came up with an open window and *no*
# data — a clean-looking event that nothing had been loaded into.
fixture_mode="false"
demo_mode="false"
case "${SEED_MODE:-}" in
  fixture | fixtures) fixture_mode="true" ;;
  demo) demo_mode="true" ;;
esac
case "${DOGFOOD_FIXTURE_MODE:-}" in
  1 | true | yes | on) fixture_mode="true" ;;
esac
case "${SEED_DEMO:-false}" in
  1 | true | yes | on) demo_mode="true" ;;
esac

if [ "$fixture_mode" = "true" ]; then
  # Fatal on purpose: booting an open, empty event while claiming to run the
  # fixture dataset is worse than not booting at all.
  echo "[axion] seeding fixture dataset (idempotent)..."
  python -m app.seed
elif [ "$demo_mode" = "true" ]; then
  echo "[axion] seeding demo dataset (idempotent)..."
  python -m app.seed || echo "[axion] seed skipped: $?"
fi

echo "[axion] starting API on :8000"
exec uvicorn app.main:app --host 0.0.0.0 --port 8000
