"""Prove the network is actually off, and Axion still answers.

Run inside the offline compose network (see docker-compose.offline.yml):

    docker compose -f docker-compose.yml -f docker-compose.offline.yml run --rm probe

Exit 0 only when all three hold:

  * a connection to the public internet fails - DNS and a bare IP, so a missing
    resolver cannot be mistaken for a blocked route;
  * the API answers /api/health/ready on the internal network;
  * the web frontend serves a page on the internal network.

This is a test of the network the containers are actually attached to, not of an
environment variable.
"""
from __future__ import annotations

import os
import socket
import sys
import urllib.request

API_READY = os.environ.get("AXION_API_URL", "http://api:8000").rstrip("/") + "/api/health/ready"
WEB_ROOT = os.environ.get("AXION_WEB_URL", "http://web:3000") + "/"
EGRESS = (("api.github.com", 443), ("1.1.1.1", 443))
CONNECT_TIMEOUT = 5.0


def _egress_blocked() -> tuple[bool, list[str]]:
    blocked = True
    details: list[str] = []
    for host, port in EGRESS:
        try:
            with socket.create_connection((host, port), timeout=CONNECT_TIMEOUT):
                details.append(f"{host}:{port} CONNECTED - egress is open")
                blocked = False
        except OSError as exc:
            details.append(f"{host}:{port} refused as expected ({type(exc).__name__})")
    return blocked, details


def _fetch(url: str) -> tuple[bool, str]:
    try:
        with urllib.request.urlopen(url, timeout=10) as response:
            body = response.read()
    except Exception as exc:  # noqa: BLE001 - the probe reports, it does not raise
        return False, f"{url} -> {type(exc).__name__}: {exc}"
    return True, f"{url} -> HTTP {response.status} ({len(body)} bytes)"


def main() -> int:
    blocked, details = _egress_blocked()
    api_ok, api_detail = _fetch(API_READY)
    web_ok, web_detail = _fetch(WEB_ROOT)

    print("offline probe")
    for line in details:
        print(f"  egress: {line}")
    print(f"  api   : {api_detail}")
    print(f"  web   : {web_detail}")

    ok = blocked and api_ok and web_ok
    print("RESULT: " + ("OFFLINE NETWORK CONFIRMED" if ok else "FAILED"))
    return 0 if ok else 1


if __name__ == "__main__":
    raise SystemExit(main())
