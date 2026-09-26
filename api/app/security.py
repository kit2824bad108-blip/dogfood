"""Password hashing and signed session cookies, stdlib only.

PBKDF2-HMAC-SHA256 for passwords (no native build deps, no passlib/bcrypt version
drift) and an HMAC-SHA256 signed token for the session cookie.
"""
from __future__ import annotations

import base64
import hashlib
import hmac
import json
import secrets
import time
from typing import Any, Optional

PBKDF2_ITERATIONS = 200_000
_ALGO = "pbkdf2_sha256"


def _b64e(raw: bytes) -> str:
    return base64.urlsafe_b64encode(raw).decode().rstrip("=")


def _b64d(value: str) -> bytes:
    padding = "=" * (-len(value) % 4)
    return base64.urlsafe_b64decode(value + padding)


def hash_password(password: str) -> str:
    salt = secrets.token_bytes(16)
    digest = hashlib.pbkdf2_hmac("sha256", password.encode(), salt, PBKDF2_ITERATIONS)
    return f"{_ALGO}${PBKDF2_ITERATIONS}${_b64e(salt)}${_b64e(digest)}"


def verify_password(password: str, encoded: Optional[str]) -> bool:
    if not encoded:
        return False
    try:
        algo, iterations, salt_b64, digest_b64 = encoded.split("$")
        if algo != _ALGO:
            return False
        expected = _b64d(digest_b64)
        candidate = hashlib.pbkdf2_hmac(
            "sha256", password.encode(), _b64d(salt_b64), int(iterations)
        )
    except (ValueError, TypeError):
        return False
    return hmac.compare_digest(candidate, expected)


def sign_payload(payload: dict[str, Any], secret: str) -> str:
    """Sign an arbitrary session payload. Shared by cookies and dev tokens."""
    body = _b64e(json.dumps(payload, separators=(",", ":")).encode())
    signature = hmac.new(secret.encode(), body.encode(), hashlib.sha256).digest()
    return f"{body}.{_b64e(signature)}"


def sign_session(user_id: int, secret: str, max_age_seconds: int) -> str:
    payload = {"uid": user_id, "exp": int(time.time()) + max_age_seconds}
    return sign_payload(payload, secret)


def verify_session(token: Optional[str], secret: str) -> Optional[dict[str, Any]]:
    if not token or "." not in token:
        return None
    body, _, signature = token.partition(".")
    expected = hmac.new(secret.encode(), body.encode(), hashlib.sha256).digest()
    try:
        provided = _b64d(signature)
    except (ValueError, TypeError):
        return None
    if not hmac.compare_digest(provided, expected):
        return None
    try:
        payload = json.loads(_b64d(body))
    except (ValueError, TypeError):
        return None
    if int(payload.get("exp", 0)) < int(time.time()):
        return None
    return payload
