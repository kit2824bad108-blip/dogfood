"""Compose healthcheck: `python -m app.healthcheck`.

Exits 0 only when `/api/health/ready` answers 200, and prints the readiness
checks either way so `docker compose ps` and the container logs say *why* an
instance is not ready. Kept as a module, not a shell one-liner, so the check is
readable and testable.
"""
from __future__ import annotations

import json
import sys
import urllib.error
import urllib.request

READY_URL = "http://127.0.0.1:8000/api/health/ready"


def main() -> int:
    body: dict = {}
    try:
        with urllib.request.urlopen(READY_URL, timeout=5) as response:
            body = json.loads(response.read())
    except urllib.error.HTTPError as exc:
        try:
            body = json.loads(exc.read())
        except Exception:  # noqa: BLE001 - a non-JSON 503 still means "not ready"
            print(f"not ready: HTTP {exc.code}", file=sys.stderr)
            return 1
    except (urllib.error.URLError, OSError, ValueError) as exc:
        print(f"not ready: {exc}", file=sys.stderr)
        return 1

    checks = json.dumps(body.get("checks") or {}, sort_keys=True)
    ready = bool(body.get("ready"))
    print(f"{'ready' if ready else 'not ready'}: {checks}")
    return 0 if ready else 1


if __name__ == "__main__":
    raise SystemExit(main())
