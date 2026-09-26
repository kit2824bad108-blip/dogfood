#!/usr/bin/env bash
set -euo pipefail

echo "[axion] applying database migrations..."
alembic upgrade head

# Which dataset to seed on boot. SEED_DEMO is the original switch; fixture mode
# selects the acceptance dataset through either of its own names, so an
# evaluator who exports only DOGFOOD_FIXTURE_MODE=true still gets a seeded
# instance instead of an empty event that silently looks fine.
fixture_mode="false"
case "${SEED_MODE:-}" in
  fixture | fixtures) fixture_mode="true" ;;
esac
case "${DOGFOOD_FIXTURE_MODE:-}" in
  1 | true | yes | on) fixture_mode="true" ;;
esac

if [ "$fixture_mode" = "true" ]; then
  # Fatal on purpose: booting an open, empty event while claiming to run the
  # fixture dataset is worse than not booting at all.
  echo "[axion] seeding fixture dataset (idempotent)..."
  python -m app.seed
elif [ "${SEED_DEMO:-false}" = "true" ]; then
  echo "[axion] seeding demo dataset (idempotent)..."
  python -m app.seed || echo "[axion] seed skipped: $?"
fi

echo "[axion] starting API on :8000"
exec uvicorn app.main:app --host 0.0.0.0 --port 8000
