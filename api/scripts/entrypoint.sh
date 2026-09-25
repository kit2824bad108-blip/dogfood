#!/usr/bin/env bash
set -euo pipefail

echo "[axion] applying database migrations..."
alembic upgrade head

if [ "${SEED_DEMO:-false}" = "true" ]; then
  echo "[axion] seeding demo dataset (idempotent)..."
  python -m app.seed || echo "[axion] seed skipped: $?"
fi

echo "[axion] starting API on :8000"
exec uvicorn app.main:app --host 0.0.0.0 --port 8000
