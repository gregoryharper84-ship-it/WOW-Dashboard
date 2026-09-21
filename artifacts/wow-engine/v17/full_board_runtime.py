"""Runtime installation for V17 full-board stabilization diagnostics.

These routes are read-only research/health surfaces. They do not score a wager,
change a fitted model, alter calibration, or grant execution authority.
"""
from __future__ import annotations

import os
from concurrent.futures import ThreadPoolExecutor, TimeoutError as FutureTimeoutError
from datetime import datetime, timezone
from typing import Any, Callable

from fastapi import Depends, FastAPI, HTTPException, Query

from v17.full_board_stabilization import capability_matrix, compact_event_page
from v17.rundown_provider_health import probe_rundown_provider_health

CAN_EXECUTE = False
DIAGNOSTIC_DEADLINE_SECONDS = float(
    os.environ.get("WOW_V17_DIAGNOSTIC_DEADLINE_SECONDS", "4.0")
)
_DIAGNOSTIC_EXECUTOR = ThreadPoolExecutor(
    max_workers=4,
    thread_name_prefix="wow-v17-diagnostic",
)


class DiagnosticDeadlineExceeded(TimeoutError):
    """Raised when a read-only diagnostic exceeds the host-safe deadline."""


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


def _bounded_diagnostic_call(
    fn: Callable[..., dict[str, Any]],
    /,
    *args: Any,
    **kwargs: Any,
) -> dict[str, Any]:
    """Run a diagnostic behind a hard host-facing deadline.

    Provider transports keep their own lower-level timeout/error semantics, but
    those calls can still be delayed by DNS, connection establishment, proxy
    behavior, or dependency stalls. The Action surface must return a typed
    diagnostic result before the client abandons the request.
    """
    deadline = max(0.05, float(DIAGNOSTIC_DEADLINE_SECONDS))
    future = _DIAGNOSTIC_EXECUTOR.submit(fn, *args, **kwargs)
    try:
        result = future.result(timeout=deadline)
    except FutureTimeoutError as exc:
        future.cancel()
        raise DiagnosticDeadlineExceeded("DIAGNOSTIC_DEADLINE_EXCEEDED") from exc
    if not isinstance(result, dict):
        raise TypeError("DIAGNOSTIC_RESULT_MUST_BE_OBJECT")
    return result


def _rundown_route_blocked(
    *,
    sport_key: str,
    date_iso: str,
    provider_code: str,
) -> dict[str, Any]:
    access = {
        "market_acquisition_status": "MARKET_DATA_UNOBTAINABLE",
        "provider_code": provider_code,
        "http_status": None,
        "market_snapshot_present": False,
        "auth_ok": None,
        "blocker": provider_code,
        "can_execute": False,
    }
    return {
        "provider": "RUNDOWN",
        "sport_key": sport_key,
        "date": date_iso,
        "status": "BLOCKED",
        "health_contract": "CATALOG_PLUS_EVENTS",
        "credential_configured": None,
        "market_snapshot_present": False,
        "catalog_access": access,
        "event_access": {
            "market_acquisition_status": "NOT_ATTEMPTED",
            "provider_code": None,
            "http_status": None,
            "market_snapshot_present": False,
            "auth_ok": None,
            "blocker": "DIAGNOSTIC_ROUTE_DID_NOT_COMPLETE",
            "can_execute": False,
        },
        "affects_model_capability": False,
        "prediction_authority": False,
        "can_execute": False,
    }


def _espn_route_blocked(
    *,
    sport_key: str,
    date_iso: str,
    provider_code: str,
) -> dict[str, Any]:
    return {
        "status": "BLOCKED",
        "sport_key": sport_key,
        "date": date_iso,
        "provider": "ESPN_SCOREBOARD_RESEARCH_FALLBACK",
        "provider_code": provider_code,
        "http_status": None,
        "response_contract": "IDENTITY_ONLY_PAGINATED",
        "prediction_authority": False,
        "exact_line_authority": False,
        "events": [],
        "can_execute": False,
    }


def run_rundown_live_health(
    *,
    sport_key: str = "baseball_mlb",
    date: str | None = None,
    opener: Any = None,
) -> dict[str, Any]:
    """Prove TheRundown catalog + events independently of feature enablement."""
    date_iso, _ = _iso_date_yyyymmdd(date)
    return probe_rundown_provider_health(
        sport_key=sport_key,
        date=date_iso,
        opener=opener,
    )


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
            athlete = (
                probable_athlete.get("athlete")
                if isinstance(probable_athlete.get("athlete"), dict)
                else probable_athlete
            )
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
    compact = [
        _compact_espn_event(raw, sport_key)
        for raw in (raw_events or [])
        if isinstance(raw, dict)
    ]
    page_payload = compact_event_page(compact, page=page, page_size=page_size)
    page_payload.update(
        {
            "status": "PASS",
            "sport_key": sport_key,
            "date": date_iso,
            "provider": "ESPN_SCOREBOARD_RESEARCH_FALLBACK",
            "response_contract": "IDENTITY_ONLY_PAGINATED",
            "prediction_authority": False,
            "can_execute": False,
        }
    )
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
            date_iso, _ = _iso_date_yyyymmdd(date)
        except ValueError as exc:
            raise HTTPException(
                status_code=422,
                detail={"code": str(exc), "can_execute": False},
            ) from exc

        try:
            return _bounded_diagnostic_call(
                run_rundown_live_health,
                sport_key=sport_key,
                date=date_iso,
            )
        except DiagnosticDeadlineExceeded:
            return _rundown_route_blocked(
                sport_key=sport_key,
                date_iso=date_iso,
                provider_code="RUNDOWN_DIAGNOSTIC_DEADLINE_EXCEEDED",
            )
        except Exception as exc:  # noqa: BLE001 - typed diagnostic boundary
            return _rundown_route_blocked(
                sport_key=sport_key,
                date_iso=date_iso,
                provider_code=f"RUNDOWN_DIAGNOSTIC_EXCEPTION_{type(exc).__name__}",
            )

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
            date_iso, _ = _iso_date_yyyymmdd(date)
        except ValueError as exc:
            raise HTTPException(
                status_code=422,
                detail={"code": str(exc), "can_execute": False},
            ) from exc

        try:
            return _bounded_diagnostic_call(
                compact_espn_scoreboard,
                sport_key=sport_key,
                date=date_iso,
                page=page,
                page_size=page_size,
            )
        except DiagnosticDeadlineExceeded:
            return _espn_route_blocked(
                sport_key=sport_key,
                date_iso=date_iso,
                provider_code="ESPN_DIAGNOSTIC_DEADLINE_EXCEEDED",
            )
        except Exception as exc:  # noqa: BLE001 - typed diagnostic boundary
            return _espn_route_blocked(
                sport_key=sport_key,
                date_iso=date_iso,
                provider_code=f"ESPN_DIAGNOSTIC_EXCEPTION_{type(exc).__name__}",
            )

    app.state.v17_full_board_runtime_routes_installed = True
    return True


__all__ = [
    "CAN_EXECUTE",
    "DIAGNOSTIC_DEADLINE_SECONDS",
    "DiagnosticDeadlineExceeded",
    "compact_espn_scoreboard",
    "install_full_board_runtime_routes",
    "run_rundown_live_health",
]
