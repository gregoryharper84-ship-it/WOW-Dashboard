"""Read-only Google Gemini Batch/cache inspection, disabled by default.

Can read only known exact resource names; never POST, DELETE, list all
resources, write content, or reveal credentials. A real durable journal and
Conductor owner must bind the expected job identity before calling.
"""
from __future__ import annotations

import json
import os
from datetime import datetime
from urllib.error import HTTPError, URLError
from urllib.request import Request, urlopen

from v17.gemini_async_contracts import (
    BATCH_NAME_RE, CACHE_NAME_RE, GeminiContractError,
    reconcile_inline_batch, reconcile_cache_resource,
)

BASE = "https://generativelanguage.googleapis.com/v1beta/"
CAN_EXECUTE = False
MAX_RESPONSE = 262_144


def _read(name: str, kind: str, *, env=None, opener=None) -> dict:
    e = os.environ if env is None else env
    if e.get("WOW_GEMINI_ENABLED", "").lower() != "true":
        raise GeminiContractError("GEMINI_DISABLED")
    if e.get("WOW_GEMINI_PAID_API_PROJECT_VERIFIED", "").lower() != "true":
        raise GeminiContractError("GEMINI_BILLING_UNVERIFIED")
    if e.get("WOW_GEMINI_READONLY_INSPECTION_ENABLED", "").lower() != "true":
        raise GeminiContractError("GEMINI_INSPECTION_DISABLED")
    pattern = BATCH_NAME_RE if kind == "BATCH" else CACHE_NAME_RE
    if not isinstance(name, str) or not pattern.fullmatch(name):
        raise GeminiContractError("GEMINI_RESOURCE_NAME_INVALID")
    if not e.get("GEMINI_API_KEY"):
        raise GeminiContractError("GEMINI_CONFIG_MISSING")
    request = Request(BASE + name, headers={"x-goog-api-key": e["GEMINI_API_KEY"],
                                            "Accept": "application/json"}, method="GET")
    caller = urlopen if opener is None else opener
    try:
        with caller(request, timeout=10) as response:
            data = response.read(MAX_RESPONSE + 1)
    except HTTPError as exc:
        if exc.code == 429:
            raise GeminiContractError("GEMINI_RATE_LIMITED") from None
        raise GeminiContractError("GEMINI_TRANSPORT_FAILED") from None
    except (URLError, TimeoutError, OSError):
        raise GeminiContractError("GEMINI_TRANSPORT_FAILED") from None
    if len(data) > MAX_RESPONSE:
        raise GeminiContractError("GEMINI_INSPECTION_RESPONSE_INVALID")
    try:
        payload = json.loads(data)
    except (ValueError, TypeError):
        raise GeminiContractError("GEMINI_INSPECTION_RESPONSE_INVALID") from None
    if not isinstance(payload, dict) or payload.get("name") != name:
        raise GeminiContractError("GEMINI_RESOURCE_IDENTITY_MISMATCH")
    return payload


def inspect_batch(
    *, batch_name: str, expected_keys: list[str], env=None, opener=None
) -> dict:
    payload = _read(batch_name, "BATCH", env=env, opener=opener)
    return reconcile_inline_batch(payload, expected_batch_name=batch_name,
                                   expected_keys=expected_keys)


def inspect_cache(
    *, cache_name: str, expected_model: str, evaluated_at: datetime,
    env=None, opener=None
) -> dict:
    payload = _read(cache_name, "CACHE", env=env, opener=opener)
    return reconcile_cache_resource(payload, expected_name=cache_name,
                                    expected_model=expected_model,
                                    evaluated_at=evaluated_at)
