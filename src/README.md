# `src/` — where the code is

The brief's root listing names `src/` and `tests/`, and calls them "yours, any shape". This repository's
shape is two conventional trees at the root rather than one `src/` directory, because the backend and the
frontend are separate images, separate dependency sets and separate test runners:

| Directory | What lives there | Runtime |
| --------- | ---------------- | ------- |
| [`../api/`](../api) | The FastAPI service: models, migrations, routers, the judging math, the fixture importer, the CLI scripts and the pytest suite | `python` 3.12, SQLAlchemy 2, Alembic |
| [`../web/`](../web) | The Next.js App Router frontend (React 19, Tailwind) | Node 20+ |

They are not two projects. The browser only talks to `http://localhost:3000/api/*`, which Next.js rewrites
to the API service, so the session cookie stays same-origin and the API port stays hidden. Neither half is
useful without the other, and `docker compose up` starts both.

Everything in both trees was written inside the event window; nothing arrives from a scaffold generator or a
previous project. [README.md](../README.md#repository-layout) has the file-by-file map and
[ARCHITECTURE.md](../ARCHITECTURE.md) explains the topology and why it is this shape.

Nothing imports this file, and nothing depends on this directory existing — it exists so a reader following
the brief's own listing finds the code from the path they expected.
