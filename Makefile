.PHONY: up down reset logs seed venv test test-api test-web build clean

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

build:
	docker compose build

clean:
	rm -rf web/.next web/node_modules web/tsconfig.tsbuildinfo
