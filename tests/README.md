# `tests/` — how to run the test suite

The suite lives beside the code it tests, at [`../api/tests/`](../api/tests), so that `pytest` and the
Docker build context both find it without a path override. This file is the index a reader arriving from the
brief's root listing needs.

```bash
# 1. The fast suite. In-process, in-memory SQLite, no services to start.
cd api && python -m pytest -q -m "not postgres"

# 2. The PostgreSQL suite: the real migrations, the real append-only trigger,
#    the real constraints. SQLite cannot express what is asserted here.
docker compose -f docker-compose.test.yml up -d --wait
cd api && AXION_TEST_DATABASE_URL=postgresql+psycopg://axion:axion@127.0.0.1:5433/axion_test \
  AXION_REQUIRE_POSTGRES=1 python -m pytest -q tests/pg
docker compose -f docker-compose.test.yml down -v

# 3. The frontend. There is no separate unit-test runner; the type checker and a
#    production build are the assertions, and they are the ones CI runs.
cd web && npm run typecheck && npm run build
```

What each part covers, and what it deliberately does not, is in
[README.md](../README.md#tests-and-the-acceptance-report) and [VERIFICATION.md](../VERIFICATION.md). Three
files are worth knowing about by name:

- `api/tests/test_dogfood_acceptance.py` — the organisers' seven checks, asserted in-process, plus one test
  that starts a real `uvicorn` on a free port, imports the real dataset and runs **their `run.py`** over HTTP.
- `api/tests/test_manifest_contract.py` — cross-checks the committed `.dogfood.toml` against the running
  API's own OpenAPI schema, against `fixtures.json`, and against the tier claims in the README, so a route or
  a claim that drifts fails here rather than in a judge's terminal.
- `api/tests/test_access.py` — the four literal checker credentials, including that they are refused by a
  deployment that has not declared itself a demo.
