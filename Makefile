.PHONY: up down reset logs seed venv test test-api test-web acceptance acceptance-selfcheck acceptance-axion build clean

# The one command that matters. Seeds the organisers' fixtures.json, takes the
# event window from it (already closed), and prints the checker's test logins.
up:
	docker compose up --build

down:
	docker compose down

# Nuke the volume too — useful before demoing a clean cold start.
reset:
	docker compose down -v

logs:
	docker compose logs -f --tail=100

# Re-run the seed script inside the running API container.
seed:
	docker compose exec api python -m app.seed

# Local (non-Docker) Python environment for the test suite.
venv:
	cd api && python -m venv .venv && \
	  ( [ -x .venv/bin/python ] && .venv/bin/python -m pip install -q -r requirements.txt \
	    || .venv/Scripts/python.exe -m pip install -q -r requirements.txt )

test: test-api test-web

test-api:
	@cd api && \
	  if [ -x .venv/bin/python ]; then .venv/bin/python -m pytest -q; \
	  elif [ -f .venv/Scripts/python.exe ]; then .venv/Scripts/python.exe -m pytest -q; \
	  else python -m pytest -q; fi

test-web:
	cd web && npm run typecheck

# The organisers' acceptance report: their run.py, their fixtures.json, and the
# manifest they read at the root of this repository. Standard library only, any
# Python 3. This is the artefact the brief asks for, and it is committed as
# printed -- failures included -- rather than edited by hand.
#
#   make up            # portal on http://localhost:3000, seeded, already closed
#   make acceptance
acceptance:
	python3 run.py .dogfood.toml > acceptance-report.txt
	@echo "acceptance-report.txt written by the organisers' run.py"

# Axion's own deeper check: nineteen questions against the same instance, from
# api/scripts/selfcheck.toml. Deliberately a separate file and a separate report,
# so nothing Axion asserts about itself can be confused with what was verified.
acceptance-selfcheck:
	@PY=python; \
	if [ -x api/.venv/bin/python ]; then PY=api/.venv/bin/python; \
	elif [ -f api/.venv/Scripts/python.exe ]; then PY=api/.venv/Scripts/python.exe; fi; \
	$$PY api/scripts/dogfood_check.py api/scripts/selfcheck.toml --out acceptance-report.selfcheck.txt

# The tier-by-tier suite (T0-T4 + bonuses), run against the demo dataset: it
# exercises the write path, which needs an open window.
#
#   SEED_MODE=demo make up && make acceptance-axion
acceptance-axion:
	@PY=python; \
	if [ -x api/.venv/bin/python ]; then PY=api/.venv/bin/python; \
	elif [ -f api/.venv/Scripts/python.exe ]; then PY=api/.venv/Scripts/python.exe; fi; \
	$$PY api/scripts/acceptance.py --out acceptance-report.axion.txt

build:
	docker compose build

clean:
	rm -rf web/.next web/node_modules web/tsconfig.tsbuildinfo
