"""Meta ``X-Hub-Signature-256`` verification (HMAC-SHA256 of the raw body)."""

from __future__ import annotations

import hashlib
import hmac


def sign_body(app_secret: str, raw_body: bytes) -> str:
    digest = hmac.new(app_secret.encode("utf-8"), raw_body, hashlib.sha256).hexdigest()
    return f"sha256={digest}"


def verify_signature(app_secret: str, raw_body: bytes, header_value: str | None) -> bool:
    if not app_secret or not header_value:
        return False
    if not header_value.startswith("sha256="):
        return False
    expected = sign_body(app_secret, raw_body)
    return hmac.compare_digest(expected, header_value.strip())
