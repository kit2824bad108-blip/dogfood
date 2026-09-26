.PHONY: up down reset logs seed venv test test-api test-web acceptance acceptance-axion build clean

# The one command that matters.
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

# The manifest-driven self-check, read from .dogfood.toml. This writes the
# report the challenge asks for, and the report itself states on its face that
# it is Axion's own check rather than an organiser-provided suite.
acceptance:
	@PY=python; \
	if [ -x api/.venv/bin/python ]; then PY=api/.venv/bin/python; \
	elif [ -f api/.venv/Scripts/python.exe ]; then PY=api/.venv/Scripts/python.exe; fi; \
	$$PY api/scripts/dogfood_check.py .dogfood.toml --out acceptance-report.txt

# The tier-by-tier suite (T0–T4 + bonuses). Kept separately so the two artefacts
# never overwrite each other.
acceptance-axion:
	@PY=python; \
	if [ -x api/.venv/bin/python ]; then PY=api/.venv/bin/python; \
	elif [ -f api/.venv/Scripts/python.exe ]; then PY=api/.venv/Scripts/python.exe; fi; \
	$$PY api/scripts/acceptance.py --out acceptance-report.axion.txt

build:
	docker compose build

clean:
	rm -rf web/.next web/node_modules web/tsconfig.tsbuildinfo
