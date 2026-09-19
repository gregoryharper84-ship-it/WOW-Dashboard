"""Runtime installation for V17 full-board stabilization diagnostics.

These routes are read-only research/health surfaces. They do not score a wager,
change a fitted model, alter calibration, or grant execution authority.
"""
from __future__ import annotations

import os
from datetime import datetime, timezone
from typing import Any

from fastapi import Depends, FastAPI, HTTPException, Query

from v17.full_board_stabilization import (
    capability_matrix,
    classify_market_acquisition,
    compact_event_page,
)

CAN_EXECUTE = False


def _iso_date_yyyymmdd(value: str | None) -> tuple[str, str]:
    if value:
        token = str(value).strip()
        try:
            parsed = datetime.strptime(token, "%Y-%m-%d")
        except ValueError as exc:
            raise ValueError("DATE_MUST_BE_YYYY_MM_DD") from exc
    else:
        parsed = datetime.now(timezone.utc)
    return parsed.strftime("%Y-%m-%d"), parsed.strftime("%Y%m%d")


def _rundown_sport_id_from_env(sport_key: str) -> str | None:
    env_name = "WOW_RUNDOWN_SPORT_ID_" + str(sport_key).strip().upper().replace("-", "_")
    value = os.environ.get(env_name)
    return str(value).strip() if value and str(value).strip() else None


def run_rundown_live_health(
    *,
    sport_key: str = "baseball_mlb",
    date: str | None = None,
    opener: Any = None,
) -> dict[str, Any]:
    """Prove both TheRundown catalog and event access under the V2 auth contract.

    Credential *presence* is not health. The provider is healthy only when an
    authenticated catalog request and an authenticated event request both return.
    An empty event list on an off-day is still valid endpoint/auth proof and is
    represented separately from ``market_snapshot_present``.
    """
    from v17 import market_evidence_sources as sources
    from v17.sep15_runtime_contract_repairs import install_rundown_v2_auth_repair

    install_rundown_v2_auth_repair()
    date_iso, _ = _iso_date_yyyymmdd(date)

    catalog = sources.fetch("RUNDOWN", "sports", opener=opener)
    catalog_status = classify_market_acquisition(
        "RUNDOWN", provider_code=catalog.code, snapshot=catalog.data if catalog.ok else None
    ).as_dict()

    sport_id = _rundown_sport_id_from_env(sport_key)
    resolution_code: str | None = None
    if not sport_id and catalog.ok:
        resolved = sources.rundown_sport_id(sport_key, opener=opener)
        resolution_code = resolved.code
        if resolved.ok and resolved.data not in (None, ""):
            sport_id = str(resolved.data)

    if not catalog.ok:
        return {
            "status": "BLOCKED",
            "provider": "RUNDOWN",
            "sport_key": sport_key,
            "date": date_iso,
            "catalog_access": catalog_status,
            "event_access": {
                "market_acquisition_status": "NOT_ATTEMPTED",
                "provider_code": None,
                "market_snapshot_present": False,
                "auth_ok": None,
                "blocker": "CATALOG_ACCESS_REQUIRED_FIRST",
                "can_execute": False,
            },
            "sport_id_resolution_code": resolution_code,
            "health_contract": "CATALOG_PLUS_EVENTS",
            "affects_model_capability": False,
            "can_execute": False,
        }

    if not sport_id:
        return {
            "status": "BLOCKED",
            "provider": "RUNDOWN",
            "sport_key": sport_key,
            "date": date_iso,
            "catalog_access": catalog_status,
            "event_access": {
                "market_acquisition_status": "NOT_ATTEMPTED",
                "provider_code": None,
                "market_snapshot_present": False,
                "auth_ok": True,
                "blocker": "RUNDOWN_SPORT_ID_UNRESOLVED",
                "can_execute": False,
            },
            "sport_id_resolution_code": resolution_code,
            "health_contract": "CATALOG_PLUS_EVENTS",
            "affects_model_capability": False,
            "can_execute": False,
        }

    events = sources.fetch(
        "RUNDOWN",
        "events",
        path_values={"sport_id": sport_id, "date": date_iso},
        opener=opener,
    )
    event_status = classify_market_acquisition(
        "RUNDOWN", provider_code=events.code, snapshot=events.data if events.ok else None
    ).as_dict()
    # Successful 200 with a structurally empty slate is still authenticated
    # endpoint health. Market presence is a separate fact.
    if events.ok:
        event_status["market_acquisition_status"] = "PASS"
        event_status["auth_ok"] = True
        event_status["blocker"] = None
        payload = events.data
        if isinstance(payload, dict):
            rows = payload.get("events")
            event_status["market_snapshot_present"] = isinstance(rows, list) and bool(rows)
        elif isinstance(payload, list):
            event_status["market_snapshot_present"] = bool(payload)

    overall = "PASS" if catalog.ok and events.ok else "BLOCKED"
    return {
        "status": overall,
        "provider": "RUNDOWN",
        "sport_key": sport_key,
        "date": date_iso,
        "catalog_access": catalog_status,
        "event_access": event_status,
        "sport_id": sport_id,
        "sport_id_resolution_code": resolution_code,
        "health_contract": "CATALOG_PLUS_EVENTS",
        "auth_contract": "X-TheRundown-Key",
        "affects_model_capability": False,
        "prediction_authority": False,
        "can_execute": False,
    }


def _compact_espn_event(raw: dict[str, Any], sport_key: str) -> dict[str, Any]:
    """Return only discovery identity/status fields from one ESPN event."""
    from v17 import scout_secondary_source as secondary

    converted = secondary.espn_event_to_primary_shape(raw, sport_key) or {}
    status = raw.get("status") if isinstance(raw.get("status"), dict) else {}
    status_type = status.get("type") if isinstance(status.get("type"), dict) else {}
    out = {
        "provider_event_id": raw.get("id"),
        "official_event_id": None,  # ESPN id is an alias until canonical resolution.
        "sport": sport_key,
        "league": (secondary.ESPN_SPORT_MAP.get(sport_key) or (None, None, None))[1],
        "scheduled_start_utc": converted.get("commence_time") or raw.get("date"),
        "home_team": converted.get("home_team"),
        "away_team": converted.get("away_team"),
        "event_status": status_type.get("name") or status_type.get("state"),
        "provider": "ESPN_SCOREBOARD_RESEARCH_FALLBACK",
        "prediction_authority": False,
        "exact_line_authority": False,
        "can_execute": False,
    }
    competitions = raw.get("competitions") or []
    competition = competitions[0] if competitions and isinstance(competitions[0], dict) else {}
    probable: dict[str, str] = {}
    for competitor in competition.get("competitors") or []:
        if not isinstance(competitor, dict):
            continue
        probable_athlete = competitor.get("probables") or competitor.get("probable")
        if isinstance(probable_athlete, list) and probable_athlete:
            probable_athlete = probable_athlete[0]
        if isinstance(probable_athlete, dict):
            athlete = probable_athlete.get("athlete") if isinstance(probable_athlete.get("athlete"), dict) else probable_athlete
            name = athlete.get("displayName") or athlete.get("fullName")
            side = competitor.get("homeAway")
            if side and name:
                probable[str(side)] = str(name)
    if probable:
        out["probable_participants"] = probable
    return out


def compact_espn_scoreboard(
    *,
    sport_key: str = "baseball_mlb",
    date: str | None = None,
    page: int = 1,
    page_size: int = 100,
) -> dict[str, Any]:
    """Fetch ESPN server-side and return a bounded identity-only response."""
    from v17 import scout_secondary_source as secondary

    date_iso, date_compact = _iso_date_yyyymmdd(date)
    mapped = secondary.ESPN_SPORT_MAP.get(sport_key)
    if not mapped:
        return {
            "status": "UNSUPPORTED_SPORT",
            "sport_key": sport_key,
            "date": date_iso,
            "events": [],
            "can_execute": False,
        }
    result = secondary._scoreboard(  # intentional server-side reuse of existing cache/transport
        sport_key,
        {"commenceTimeFrom": date_compact, "commenceTimeTo": date_compact},
    )
    if not result.ok:
        return {
            "status": "BLOCKED",
            "sport_key": sport_key,
            "date": date_iso,
            "provider_code": result.code,
            "http_status": result.status,
            "events": [],
            "can_execute": False,
        }
    raw_events = result.data.get("events") if isinstance(result.data, dict) else []
    compact = [_compact_espn_event(raw, sport_key) for raw in (raw_events or []) if isinstance(raw, dict)]
    page_payload = compact_event_page(compact, page=page, page_size=page_size)
    page_payload.update({
        "status": "PASS",
        "sport_key": sport_key,
        "date": date_iso,
        "provider": "ESPN_SCOREBOARD_RESEARCH_FALLBACK",
        "response_contract": "IDENTITY_ONLY_PAGINATED",
        "prediction_authority": False,
        "can_execute": False,
    })
    return page_payload


def install_full_board_runtime_routes(
    app: FastAPI,
    *,
    auth_dependency: Any | None = None,
) -> bool:
    """Install capability, strict market-health, and compact discovery surfaces."""
    if getattr(app.state, "v17_full_board_runtime_routes_installed", False):
        return True

    dependencies = [Depends(auth_dependency)] if callable(auth_dependency) else []

    @app.get(
        "/v17/capabilities",
        operation_id="getV17Capabilities",
        dependencies=dependencies,
    )
    def get_v17_capabilities():
        return capability_matrix()

    @app.get(
        "/v17/market-health/rundown",
        operation_id="getV17RundownMarketHealth",
        dependencies=dependencies,
    )
    def get_v17_rundown_market_health(
        sport_key: str = Query("baseball_mlb"),
        date: str | None = Query(None),
    ):
        try:
            return run_rundown_live_health(sport_key=sport_key, date=date)
        except ValueError as exc:
            raise HTTPException(status_code=422, detail={"code": str(exc), "can_execute": False}) from exc

    @app.get(
        "/v17/discovery/espn-compact",
        operation_id="getV17CompactEspnDiscovery",
        dependencies=dependencies,
    )
    def get_v17_compact_espn_discovery(
        sport_key: str = Query("baseball_mlb"),
        date: str | None = Query(None),
        page: int = Query(1, ge=1),
        page_size: int = Query(100, ge=1, le=250),
    ):
        try:
            return compact_espn_scoreboard(
                sport_key=sport_key,
                date=date,
                page=page,
                page_size=page_size,
            )
        except ValueError as exc:
            raise HTTPException(status_code=422, detail={"code": str(exc), "can_execute": False}) from exc

    app.state.v17_full_board_runtime_routes_installed = True
    return True


__all__ = [
    "CAN_EXECUTE",
    "compact_espn_scoreboard",
    "install_full_board_runtime_routes",
    "run_rundown_live_health",
]
