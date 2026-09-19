"""Bounded provider-health probes for fallback market-data infrastructure.

This module proves credentialed provider access before a provider is treated as a
healthy fallback. It never exposes credential values and never creates sporting
probabilities.
"""
from __future__ import annotations

import json
import os
from typing import Any
from urllib.error import HTTPError, URLError
from urllib.parse import urlencode
from urllib.request import Request, urlopen

CAN_EXECUTE = False
ODDS_API_BASE = os.environ.get("WOW_ODDS_API_BASE_URL", "https://api.the-odds-api.com/v4").rstrip("/")
ODDS_API_KEY_LADDER = (
    "ODDS_API_KEY_100K",
    "ODDS_API_PAID_KEY",
    "ODDS_API_FREE_KEY",
    "ODDS_API_KEY",
)


def resolve_odds_api_credential() -> tuple[str | None, str | None]:
    for name in ODDS_API_KEY_LADDER:
        value = (os.environ.get(name) or "").strip()
        if value:
            return value, name
    return None, None


def _status(
    code: str,
    *,
    key_source: str | None,
    http_status: int | None = None,
    provider_detail: str | None = None,
) -> dict[str, Any]:
    healthy = code == "PASS"
    return {
        "provider": "THE_ODDS_API",
        "status": code,
        "provider_detail": provider_detail,
        "http_status": http_status,
        "credential_configured": key_source is not None,
        "credential_source": key_source,
        "fallback_eligible": healthy,
        "market_snapshot_present": False,
        "affects_model_capability": False,
        "prediction_authority": False,
        "can_execute": False,
        "secret_values_exposed": False,
    }


def probe_odds_api_health(*, opener: Any = None) -> dict[str, Any]:
    """GET the quota-free sports catalog to prove auth/reachability.

    A configured-but-deactivated key returns ``AUTH_FAILED`` and is explicitly
    ineligible for fallback routing. No request is attempted when no credential is
    configured. Transport/schema detail remains subordinate to the canonical
    V17 provider taxonomy rather than minting one-off terminal codes.
    """
    api_key, key_source = resolve_odds_api_credential()
    if not api_key:
        return _status("CREDENTIAL_UNCONFIGURED", key_source=None)

    query = urlencode({"apiKey": api_key})
    request = Request(
        f"{ODDS_API_BASE}/sports?{query}",
        headers={"Accept": "application/json", "User-Agent": "WOW-V17-Provider-Health/1.0"},
    )
    open_call = opener or urlopen
    try:
        with open_call(request, timeout=5) as response:
            body = response.read()
            status = getattr(response, "status", None) or getattr(response, "code", None) or 200
            if int(status) != 200:
                return _status(
                    "PROVIDER_REQUEST_FAILED",
                    key_source=key_source,
                    http_status=int(status),
                    provider_detail=f"HTTP_{int(status)}",
                )
            try:
                payload = json.loads(body.decode("utf-8"))
            except (json.JSONDecodeError, UnicodeDecodeError):
                return _status(
                    "PROVIDER_SCHEMA_FAILURE",
                    key_source=key_source,
                    http_status=200,
                    provider_detail="NON_JSON_RESPONSE",
                )
            result = _status("PASS", key_source=key_source, http_status=200)
            result["catalog_rows_present"] = isinstance(payload, list) and bool(payload)
            return result
    except HTTPError as exc:
        if exc.code in {401, 403}:
            return _status("AUTH_FAILED", key_source=key_source, http_status=exc.code)
        if exc.code == 429:
            return _status("RATE_LIMITED", key_source=key_source, http_status=429)
        return _status(
            "PROVIDER_REQUEST_FAILED",
            key_source=key_source,
            http_status=exc.code,
            provider_detail=f"HTTP_{exc.code}",
        )
    except (URLError, TimeoutError, OSError) as exc:
        return _status(
            "PROVIDER_REQUEST_FAILED",
            key_source=key_source,
            provider_detail=type(exc).__name__,
        )


def fallback_provider_allowed(health: dict[str, Any]) -> bool:
    """One gate used by callers before treating a provider as a fallback."""
    return bool(
        health.get("provider") == "THE_ODDS_API"
        and health.get("status") == "PASS"
        and health.get("fallback_eligible") is True
    )


__all__ = [
    "CAN_EXECUTE",
    "ODDS_API_KEY_LADDER",
    "fallback_provider_allowed",
    "probe_odds_api_health",
    "resolve_odds_api_credential",
]
