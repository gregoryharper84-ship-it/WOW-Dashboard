"""Request signing for the internal Engineering Auditor persistence bridge.

The scorer and worker already receive the same private Render Redis connection
credential. We derive a context-separated HMAC key from it instead of copying a
Supabase admin credential or provisioning a second long-lived secret.
"""
from __future__ import annotations

import hashlib
import hmac
import json
import secrets
import time
from typing import Any, Mapping


_CONTEXT = b"WOW_ENGINEERING_AUDIT_BRIDGE_V1"
_MAX_CLOCK_SKEW_SECONDS = 90


def canonical_payload(payload: Mapping[str, Any]) -> bytes:
    return json.dumps(
        payload,
        sort_keys=True,
        separators=(",", ":"),
        ensure_ascii=False,
    ).encode("utf-8")


def _derived_key(redis_url: str) -> bytes:
    value = str(redis_url or "").strip()
    if not value:
        raise ValueError("ENGINEERING_AUDIT_REDIS_CREDENTIAL_MISSING")
    return hmac.new(value.encode("utf-8"), _CONTEXT, hashlib.sha256).digest()


def signed_headers(
    payload: Mapping[str, Any],
    redis_url: str,
    *,
    timestamp: int | None = None,
    nonce: str | None = None,
) -> dict[str, str]:
    ts = int(time.time()) if timestamp is None else int(timestamp)
    nonce_value = nonce or secrets.token_hex(16)
    if len(nonce_value) != 32 or any(ch not in "0123456789abcdef" for ch in nonce_value.lower()):
        raise ValueError("ENGINEERING_AUDIT_NONCE_INVALID")
    message = f"{ts}.{nonce_value}.".encode("ascii") + canonical_payload(payload)
    signature = hmac.new(_derived_key(redis_url), message, hashlib.sha256).hexdigest()
    return {
        "X-WOW-Engineering-Timestamp": str(ts),
        "X-WOW-Engineering-Nonce": nonce_value,
        "X-WOW-Engineering-Signature": signature,
    }


def verify_signed_request(
    payload: Mapping[str, Any],
    redis_url: str,
    *,
    timestamp: str | None,
    nonce: str | None,
    signature: str | None,
    now: int | None = None,
) -> str:
    try:
        ts = int(str(timestamp or ""))
    except ValueError as exc:
        raise ValueError("ENGINEERING_AUDIT_SIGNATURE_TIMESTAMP_INVALID") from exc
    current = int(time.time()) if now is None else int(now)
    if abs(current - ts) > _MAX_CLOCK_SKEW_SECONDS:
        raise ValueError("ENGINEERING_AUDIT_SIGNATURE_EXPIRED")

    nonce_value = str(nonce or "").lower()
    if len(nonce_value) != 32 or any(ch not in "0123456789abcdef" for ch in nonce_value):
        raise ValueError("ENGINEERING_AUDIT_SIGNATURE_NONCE_INVALID")
    provided = str(signature or "").lower()
    if len(provided) != 64 or any(ch not in "0123456789abcdef" for ch in provided):
        raise ValueError("ENGINEERING_AUDIT_SIGNATURE_INVALID")

    message = f"{ts}.{nonce_value}.".encode("ascii") + canonical_payload(payload)
    expected = hmac.new(_derived_key(redis_url), message, hashlib.sha256).hexdigest()
    if not hmac.compare_digest(provided, expected):
        raise ValueError("ENGINEERING_AUDIT_SIGNATURE_INVALID")
    return nonce_value
