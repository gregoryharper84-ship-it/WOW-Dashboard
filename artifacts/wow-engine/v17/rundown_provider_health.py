"""Strict TheRundown provider health independent of market-lane feature flags.

Normal market-evidence acquisition is intentionally gated by
``WOW_MARKET_EVIDENCE_ENABLED``. Provider health must not be: engineering needs
to distinguish "feature disabled" from "credential/endpoint broken". This probe
therefore performs two bounded read-only Product V2 requests directly against
the active provider contract and reports feature enablement as a separate axis.

No credential value is returned or logged. Nothing here produces sporting
probability or execution authority.
"""
from __future__ import annotations

import json
import os
from datetime import datetime, timezone
from typing import Any
from urllib.error import HTTPError, URLError
from urllib.request import Request, urlopen

CAN_EXECUTE = False


def _now_iso() -> str:
    return datetime.now(timezone.utc).replace(microsecond=0).isoformat().replace("+00:00", "Z")


def _norm(value: Any) -> str:
    return "".join(ch for ch in str(value or "").lower() if ch.isalnum())


def _api_key(provider: Any) -> tuple[str | None, str | None]:
    for alias in provider.key_envs:
        value = (os.environ.get(alias) or "").strip()
        if value:
            return value, alias
    return None, None


def _base_url(provider: Any) -> str:
    return (os.environ.get(provider.base_url_env) or provider.default_base_url).rstrip("/")


def _path(provider: Any, capability: str) -> str | None:
    override = os.environ.get(f"{provider.endpoint_env_prefix}{capability.upper()}_PATH")
    if override and override.strip():
        return override.strip()
    return provider.endpoints.get(capability)


def _typed_status(
    status: str,
    *,
    provider_code: str | None = None,
    http_status: int | None = None,
    auth_ok: bool | None = None,
    snapshot_present: bool = False,
    blocker: str | None = None,
) -> dict[str, Any]:
    return {
        "market_acquisition_status": status,
        "provider_code": provider_code,
        "http_status": http_status,
        "market_snapshot_present": snapshot_present,
        "auth_ok": auth_ok,
        "blocker": blocker,
        "can_execute": False,
    }


def _request_json(
    provider: Any,
    capability: str,
    *,
    path_values: dict[str, Any] | None = None,
    opener: Any = None,
) -> tuple[dict[str, Any], Any]:
    api_key, _alias = _api_key(provider)
    if not api_key:
        return _typed_status(
            "CREDENTIAL_UNCONFIGURED",
            provider_code="RUNDOWN_CREDENTIAL_UNCONFIGURED",
            auth_ok=False,
            blocker="CREDENTIAL_UNCONFIGURED",
        ), None

    path = _path(provider, capability)
    if not path:
        return _typed_status(
            "MARKET_DATA_UNOBTAINABLE",
            provider_code="RUNDOWN_ENDPOINT_UNCONFIGURED",
            auth_ok=None,
            blocker="RUNDOWN_ENDPOINT_UNCONFIGURED",
        ), None
    for key, value in (path_values or {}).items():
        path = path.replace("{" + key + "}", str(value))
    if "{" in path or "}" in path:
        return _typed_status(
            "MARKET_DATA_UNOBTAINABLE",
            provider_code="RUNDOWN_PATH_PARAMETER_MISSING",
            auth_ok=None,
            blocker="RUNDOWN_PATH_PARAMETER_MISSING",
        ), None

    url = _base_url(provider) + (path if path.startswith("/") else "/" + path)
    headers = {
        "Accept": "application/json",
        "User-Agent": "WOW-V17-Provider-Health/1.0",
        provider.auth_name: api_key,
    }
    request = Request(url, headers=headers)
    try:
        with (opener or urlopen)(request, timeout=5) as response:
            status_code = int(getattr(response, "status", None) or getattr(response, "code", None) or 200)
            body = response.read().decode("utf-8")
    except HTTPError as exc:
        if exc.code in {401, 403}:
            return _typed_status(
                "AUTH_FAILED",
                provider_code=f"RUNDOWN_HTTP_{exc.code}",
                http_status=exc.code,
                auth_ok=False,
                blocker=f"RUNDOWN_HTTP_{exc.code}",
            ), None
        if exc.code == 429:
            return _typed_status(
                "RATE_LIMITED",
                provider_code="RUNDOWN_HTTP_429",
                http_status=429,
                auth_ok=True,
                blocker="RUNDOWN_HTTP_429",
            ), None
        return _typed_status(
            "MARKET_DATA_UNOBTAINABLE",
            provider_code=f"RUNDOWN_HTTP_{exc.code}",
            http_status=exc.code,
            auth_ok=None,
            blocker=f"RUNDOWN_HTTP_{exc.code}",
        ), None
    except (URLError, TimeoutError, OSError) as exc:
        code = f"RUNDOWN_{type(exc).__name__}"
        return _typed_status(
            "MARKET_DATA_UNOBTAINABLE",
            provider_code=code,
            auth_ok=None,
            blocker=code,
        ), None

    if status_code != 200:
        code = f"RUNDOWN_HTTP_{status_code}"
        return _typed_status(
            "MARKET_DATA_UNOBTAINABLE",
            provider_code=code,
            http_status=status_code,
            auth_ok=None,
            blocker=code,
        ), None
    try:
        payload = json.loads(body)
    except (json.JSONDecodeError, UnicodeDecodeError):
        return _typed_status(
            "MARKET_DATA_UNOBTAINABLE",
            provider_code="RUNDOWN_INVALID_JSON",
            http_status=200,
            auth_ok=True,
            blocker="RUNDOWN_INVALID_JSON",
        ), None
    return _typed_status(
        "PASS",
        provider_code="MARKET_EVIDENCE_FETCH_OK",
        http_status=200,
        auth_ok=True,
        snapshot_present=payload not in (None, {}, [], ""),
    ), payload


def _resolve_sport_id(payload: Any, sport_key: str, *, pinned: str | None) -> tuple[str | None, str | None]:
    if pinned:
        return pinned, "MARKET_EVIDENCE_SPORT_ID_PINNED"
    from v17 import market_evidence_sources as sources

    rows = payload.get("sports") if isinstance(payload, dict) else payload
    if not isinstance(rows, list):
        return None, "RUNDOWN_SCHEMA_UNRECOGNISED"
    index: dict[str, str] = {}
    for row in rows:
        if not isinstance(row, dict):
            continue
        sport_id = row.get("sport_id") or row.get("id")
        name = row.get("sport_name") or row.get("name")
        if sport_id is not None and name:
            index[_norm(name)] = str(sport_id)
    for alias in sources._RUNDOWN_SPORT_ALIASES.get(str(sport_key), ()):  # canonical shared aliases
        if alias in index:
            return index[alias], "MARKET_EVIDENCE_SPORT_ID_RESOLVED"
    return None, "MARKET_EVIDENCE_UNSUPPORTED_SPORT"


def probe_rundown_provider_health(
    *,
    sport_key: str = "baseball_mlb",
    date: str,
    opener: Any = None,
) -> dict[str, Any]:
    """Prove Product V2 catalog + event access independently of lane enablement."""
    from v17 import market_evidence_sources as sources
    from v17.sep15_runtime_contract_repairs import install_rundown_v2_auth_repair

    install_rundown_v2_auth_repair()
    provider = sources.PROVIDERS["RUNDOWN"]
    _key, selected_alias = _api_key(provider)
    catalog_status, catalog_payload = _request_json(provider, "sports", opener=opener)

    base = {
        "provider": "RUNDOWN",
        "sport_key": sport_key,
        "date": date,
        "health_contract": "CATALOG_PLUS_EVENTS",
        "auth_contract": "X-TheRundown-Key",
        "credential_configured": selected_alias is not None,
        "credential_source": selected_alias,
        "market_evidence_feature_enabled": bool(sources.ENABLED),
        "feature_enablement_affects_health_probe": False,
        "affects_model_capability": False,
        "prediction_authority": False,
        "checked_at": _now_iso(),
        "can_execute": False,
    }
    if catalog_status["market_acquisition_status"] != "PASS":
        return {
            **base,
            "status": "BLOCKED",
            "catalog_access": catalog_status,
            "event_access": _typed_status(
                "NOT_ATTEMPTED",
                auth_ok=None,
                blocker="CATALOG_ACCESS_REQUIRED_FIRST",
            ),
            "sport_id": None,
            "sport_id_resolution_code": None,
        }

    pin_name = "WOW_RUNDOWN_SPORT_ID_" + str(sport_key).strip().upper().replace("-", "_")
    pinned = (os.environ.get(pin_name) or "").strip() or None
    sport_id, resolution_code = _resolve_sport_id(catalog_payload, sport_key, pinned=pinned)
    if not sport_id:
        return {
            **base,
            "status": "BLOCKED",
            "catalog_access": catalog_status,
            "event_access": _typed_status(
                "NOT_ATTEMPTED",
                auth_ok=True,
                blocker="RUNDOWN_SPORT_ID_UNRESOLVED",
            ),
            "sport_id": None,
            "sport_id_resolution_code": resolution_code,
        }

    event_status, event_payload = _request_json(
        provider,
        "events",
        path_values={"sport_id": sport_id, "date": date},
        opener=opener,
    )
    if event_status["market_acquisition_status"] == "PASS":
        rows = event_payload.get("events") if isinstance(event_payload, dict) else event_payload
        event_status["market_snapshot_present"] = isinstance(rows, list) and bool(rows)

    return {
        **base,
        "status": "PASS" if event_status["market_acquisition_status"] == "PASS" else "BLOCKED",
        "catalog_access": catalog_status,
        "event_access": event_status,
        "sport_id": sport_id,
        "sport_id_resolution_code": resolution_code,
    }


__all__ = ["CAN_EXECUTE", "probe_rundown_provider_health"]
