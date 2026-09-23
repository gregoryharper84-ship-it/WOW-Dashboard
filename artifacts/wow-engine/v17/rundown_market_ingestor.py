"""Quota-aware TheRundown market-data ingestor for WOW V17.

This service boundary owns reference-catalog refresh and append-only market quote
collection. It is intentionally outside every fitted sporting-probability path.
The collector never creates model probabilities, never alters probability rank,
and never executes a wager.

Acquisition policy:
- discover sports / markets / affiliates from provider catalogs;
- bootstrap with the narrowest filtered REST snapshot;
- stay in SNAPSHOT mode when plan delay is missing, invalid, or positive;
- mark DELTA as the *next eligible mode* only when the bootstrap response
  explicitly reports X-Data-Delay-Seconds: 0 and supplies a valid positive
  meta.delta_last_id cursor;
- WebSocket is not auto-enabled here; it remains an explicit entitlement/runtime
  deployment decision with REST reconciliation.
"""
from __future__ import annotations

import json
import time
from dataclasses import dataclass
from datetime import datetime, timezone
from typing import Any, Callable
from urllib.error import HTTPError, URLError
from urllib.parse import urlencode
from urllib.request import Request, urlopen

from v17 import market_evidence_sources as sources
from v17 import rundown_market_ledger as ledger

CAN_EXECUTE = False
PROVIDER = "RUNDOWN"
RETIRED_AFFILIATE_IDS = {"27"}


@dataclass(frozen=True)
class TransportResult:
    ok: bool
    data: Any = None
    status: int | None = None
    code: str | None = None
    endpoint: str | None = None
    data_delay_seconds: int | None = None
    datapoints: int | None = None
    observed_at: str | None = None
    can_execute: bool = False


def _now_iso() -> str:
    return datetime.now(timezone.utc).replace(microsecond=0).isoformat().replace("+00:00", "Z")


def _positive_int(value: Any) -> int | None:
    if isinstance(value, bool):
        return None
    try:
        parsed = int(str(value).strip())
    except (TypeError, ValueError):
        return None
    return parsed if parsed > 0 else None


def _nonnegative_int(value: Any) -> int | None:
    if isinstance(value, bool):
        return None
    try:
        parsed = int(str(value).strip())
    except (TypeError, ValueError):
        return None
    return parsed if parsed >= 0 else None


def _header(headers: Any, name: str) -> Any:
    if headers is None:
        return None
    getter = getattr(headers, "get", None)
    if callable(getter):
        return getter(name) or getter(name.lower())
    return None


def _request_json(
    path: str,
    *,
    params: dict[str, Any] | None = None,
    opener: Callable[..., Any] | None = None,
) -> TransportResult:
    """Fetch one V2 resource while preserving only non-secret entitlement headers."""
    provider = sources.PROVIDERS[PROVIDER]
    api_key = sources._api_key(provider)
    if not api_key:
        return TransportResult(False, code="MARKET_EVIDENCE_CREDENTIAL_UNCONFIGURED", observed_at=_now_iso())

    base = sources._base_url(provider)
    query = {key: value for key, value in (params or {}).items() if value is not None}
    endpoint = base + (path if path.startswith("/") else "/" + path)
    full_url = endpoint + ("?" + urlencode(query) if query else "")
    request = Request(
        full_url,
        headers={
            "Accept": "application/json",
            "User-Agent": sources.USER_AGENT,
            "X-TheRundown-Key": api_key,
        },
    )
    try:
        with (opener or urlopen)(request, timeout=sources.TIMEOUT_SECONDS) as response:
            body = response.read().decode("utf-8")
            status = getattr(response, "status", None) or getattr(response, "code", None)
            headers = getattr(response, "headers", None)
            delay = _nonnegative_int(_header(headers, "X-Data-Delay-Seconds"))
            datapoints = _nonnegative_int(_header(headers, "X-Datapoints"))
    except HTTPError as exc:
        return TransportResult(
            False,
            status=exc.code,
            code=f"RUNDOWN_HTTP_{exc.code}",
            endpoint=endpoint,
            observed_at=_now_iso(),
        )
    except (URLError, TimeoutError, OSError) as exc:
        return TransportResult(
            False,
            code=f"RUNDOWN_{type(exc).__name__.upper()}",
            endpoint=endpoint,
            observed_at=_now_iso(),
        )

    try:
        payload = json.loads(body)
    except ValueError:
        return TransportResult(
            False,
            status=status,
            code="RUNDOWN_INVALID_JSON",
            endpoint=endpoint,
            observed_at=_now_iso(),
        )
    return TransportResult(
        True,
        data=payload,
        status=status,
        code="RUNDOWN_FETCH_OK",
        endpoint=endpoint,
        data_delay_seconds=delay,
        datapoints=datapoints,
        observed_at=_now_iso(),
    )


def _catalog_items(payload: Any, catalog_type: str) -> list[dict[str, Any]]:
    if isinstance(payload, list):
        return [row for row in payload if isinstance(row, dict)]
    if not isinstance(payload, dict):
        return []
    key = {"SPORT": "sports", "MARKET": "markets", "AFFILIATE": "affiliates"}[catalog_type]
    value = payload.get(key)
    return [row for row in value if isinstance(row, dict)] if isinstance(value, list) else []


def _sport_canonical_key(name: Any) -> str | None:
    token = sources._norm(name)
    for sport_key, aliases in sources._RUNDOWN_SPORT_ALIASES.items():
        if token in aliases:
            return str(sport_key)
    return None


def _catalog_row(raw: dict[str, Any], catalog_type: str, *, refreshed_at: str) -> dict[str, Any] | None:
    if catalog_type == "SPORT":
        provider_id = raw.get("sport_id") or raw.get("id")
        name = raw.get("sport_name") or raw.get("name")
        canonical_key = _sport_canonical_key(name)
    elif catalog_type == "MARKET":
        provider_id = raw.get("market_id") or raw.get("id")
        name = raw.get("market_name") or raw.get("name")
        canonical_key = sources.canonical_market_key(name)
    else:
        provider_id = raw.get("affiliate_id") or raw.get("id")
        name = raw.get("affiliate_name") or raw.get("name")
        canonical_key = None
    if provider_id is None:
        return None
    provider_id = str(provider_id)
    active_raw = raw.get("active") if "active" in raw else raw.get("is_active")
    active = None if active_raw is None else bool(active_raw)
    if catalog_type == "AFFILIATE" and provider_id in RETIRED_AFFILIATE_IDS:
        active = False
    return {
        "catalog_type": catalog_type,
        "provider_id": provider_id,
        "canonical_key": canonical_key,
        "display_name": str(name) if name is not None else None,
        "active": active,
        "payload": raw,
        "refreshed_at": refreshed_at,
    }


def refresh_catalogs(
    client: Any,
    *,
    opener: Callable[..., Any] | None = None,
    pause_seconds: float = 1.0,
    sleep_fn: Callable[[float], None] = time.sleep,
) -> dict[str, Any]:
    """Refresh free provider reference catalogs with bounded request cadence."""
    specs = (
        ("SPORT", "/api/v2/sports"),
        ("MARKET", "/api/v2/markets"),
        ("AFFILIATE", "/api/v2/affiliates"),
    )
    refreshed_at = _now_iso()
    totals: dict[str, int] = {}
    failures: dict[str, str] = {}
    for index, (catalog_type, path) in enumerate(specs):
        response = _request_json(path, opener=opener)
        if not response.ok:
            failures[catalog_type] = response.code or "RUNDOWN_CATALOG_FETCH_FAILED"
        else:
            rows = [
                row
                for item in _catalog_items(response.data, catalog_type)
                if (row := _catalog_row(item, catalog_type, refreshed_at=refreshed_at)) is not None
            ]
            totals[catalog_type] = ledger.persist_catalog_rows(client, rows)
        if index < len(specs) - 1 and pause_seconds > 0:
            sleep_fn(pause_seconds)
    return {
        "status": "COMPLETE" if not failures else "PARTIAL",
        "rows_written": totals,
        "failures": failures,
        "catalog_refreshed_at": refreshed_at,
        "prediction_authority": False,
        "can_execute": False,
    }


def _events(payload: Any) -> list[dict[str, Any]]:
    if isinstance(payload, dict) and isinstance(payload.get("events"), list):
        return [row for row in payload["events"] if isinstance(row, dict)]
    return []


def _delta_cursor(payload: Any) -> int | None:
    if not isinstance(payload, dict) or not isinstance(payload.get("meta"), dict):
        return None
    return _positive_int(payload["meta"].get("delta_last_id"))


def _feed_key(sport_key: str, slate_date: str, market_ids: tuple[str, ...], affiliate_ids: tuple[str, ...]) -> str:
    return ":".join([PROVIDER, sport_key, slate_date, ",".join(market_ids), ",".join(affiliate_ids)])


def collect_snapshot(
    client: Any,
    *,
    sport_key: str,
    slate_date: str,
    market_ids: tuple[str, ...] | list[str],
    affiliate_ids: tuple[str, ...] | list[str],
    opener: Callable[..., Any] | None = None,
    allow_delta: bool = True,
    include_all_periods: bool = True,
) -> dict[str, Any]:
    """Collect one narrow snapshot and persist raw quote history + recovery state."""
    markets = tuple(str(value) for value in market_ids)
    affiliates = tuple(str(value) for value in affiliate_ids if str(value) not in RETIRED_AFFILIATE_IDS)
    if not markets or not affiliates:
        return {
            "status": "BLOCKED",
            "reason_code": "RUNDOWN_SNAPSHOT_FILTERS_REQUIRED",
            "prediction_authority": False,
            "can_execute": False,
        }

    resolved = sources.rundown_sport_id(sport_key, opener=opener)
    if not resolved.ok or resolved.data is None:
        return {
            "status": "BLOCKED",
            "reason_code": resolved.code or "MARKET_EVIDENCE_UNSUPPORTED_SPORT",
            "prediction_authority": False,
            "can_execute": False,
        }
    sport_id = str(resolved.data)
    params = {
        "market_ids": ",".join(markets),
        "affiliate_ids": ",".join(affiliates),
        "main_line": "true",
        "hide_closed": "true",
        "include": "all_periods" if include_all_periods else None,
        "offset": str(sources.rundown_date_offset_minutes()),
    }
    response = _request_json(
        f"/api/v2/sports/{sport_id}/events/{slate_date}",
        params=params,
        opener=opener,
    )
    feed_key = _feed_key(str(sport_key), str(slate_date), markets, affiliates)
    if not response.ok:
        ledger.persist_sync_state(
            client,
            {
                "feed_key": feed_key,
                "sport_key": sport_key,
                "slate_date": slate_date,
                "market_ids": list(markets),
                "affiliate_ids": list(affiliates),
                "acquisition_mode": "SNAPSHOT",
                "last_failure_at": _now_iso(),
                "last_error_code": response.code,
                "last_rows_written": 0,
                "metadata": {"endpoint": response.endpoint, "status": response.status},
            },
        )
        return {
            "status": "FAILED",
            "reason_code": response.code,
            "http_status": response.status,
            "prediction_authority": False,
            "can_execute": False,
        }

    fetched_at = response.observed_at or _now_iso()
    observations: list[dict[str, Any]] = []
    for event in _events(response.data):
        observations.extend(
            ledger.price_observations_from_rundown_event(
                event,
                sport_key=sport_key,
                snapshot_kind="CURRENT",
                fetched_at=fetched_at,
                is_live=False,
            )
        )
    rows_written = ledger.persist_observations(client, observations)
    cursor = _delta_cursor(response.data)
    delta_eligible = bool(allow_delta and response.data_delay_seconds == 0 and cursor is not None)
    next_mode = "DELTA" if delta_eligible else "SNAPSHOT"
    ledger.persist_sync_state(
        client,
        {
            "feed_key": feed_key,
            "sport_key": sport_key,
            "slate_date": slate_date,
            "market_ids": list(markets),
            "affiliate_ids": list(affiliates),
            "acquisition_mode": next_mode,
            "data_delay_seconds": response.data_delay_seconds,
            "delta_cursor": cursor if delta_eligible else None,
            "last_success_at": fetched_at,
            "last_error_code": None,
            "last_rows_written": rows_written,
            "metadata": {
                "endpoint": response.endpoint,
                "datapoints": response.datapoints,
                "events": len(_events(response.data)),
                "delta_eligible": delta_eligible,
                "delta_gate": "EXPLICIT_ZERO_DELAY_AND_POSITIVE_CURSOR",
            },
        },
    )
    return {
        "status": "COMPLETE",
        "sport_id": sport_id,
        "events": len(_events(response.data)),
        "rows_written": rows_written,
        "datapoints": response.datapoints,
        "data_delay_seconds": response.data_delay_seconds,
        "delta_cursor": cursor if delta_eligible else None,
        "next_acquisition_mode": next_mode,
        "delta_eligible": delta_eligible,
        "prediction_authority": False,
        "can_execute": False,
    }


__all__ = [
    "CAN_EXECUTE",
    "PROVIDER",
    "RETIRED_AFFILIATE_IDS",
    "TransportResult",
    "collect_snapshot",
    "refresh_catalogs",
]
