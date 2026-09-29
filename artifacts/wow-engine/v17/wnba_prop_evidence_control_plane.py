"""WNBA forward-evidence control plane with governed identity recovery.

This module intentionally wraps the last-known-good WNBA control plane rather
than duplicating its persistence and hydration code. The base module remains the
production implementation for ordinary acquisition and request-scoped schedule
bridges. This wrapper adds one Class-B recovery path: after the official WNBA
schedule transports terminate with ``WNBA_OFFICIAL_SOURCE_UNAVAILABLE``, V17 may
use the already-governed ESPN WNBA scoreboard for event/date/team identity only,
reconcile those teams to official WNBA Stats TeamIDs, and then execute the
unchanged base hydration under a request-scoped schedule override.

No market data, fitted probability, calibration, certification, publication,
promotion, ranking, or execution authority is introduced. ``can_execute`` is
always false.
"""
from __future__ import annotations

from datetime import date, datetime, timezone
from typing import Any, Callable, Mapping
from zoneinfo import ZoneInfo

import httpx
from fastapi import FastAPI

from basketball_event_hydration_runtime import ESPN_BASE_URLS, _espn_competitors
from github_actions_oidc import scout_route_auth_dependency
import v17.wnba_official_schedule_web_fallback  # noqa: F401 - preserve install-order contract
from v17 import wnba_prop_evidence_acquisition as acquisition
from v17 import wnba_prop_evidence_control_plane_base as _base

CAN_EXECUTE = False
ROUTE_PATH = _base.ROUTE_PATH
WNBAForwardEvidenceRequest = _base.WNBAForwardEvidenceRequest
schedule_transport = _base.schedule_transport
ESPN_IDENTITY_PROVIDER = schedule_transport.ESPN_IDENTITY_BRIDGE_PROVIDER

# Preserve the historical public/private test seam while keeping the stable base
# implementation as the runtime default. Existing tests and narrow runtime hooks
# patch ``control._existing_snapshot``; the base delegates through this proxy so
# those patches still apply without per-request mutation of module globals.
_ORIGINAL_BASE_EXISTING_SNAPSHOT = _base._existing_snapshot


def _existing_snapshot(*args: Any, **kwargs: Any) -> bool:
    return _ORIGINAL_BASE_EXISTING_SNAPSHOT(*args, **kwargs)


def _existing_snapshot_proxy(*args: Any, **kwargs: Any) -> bool:
    return globals()["_existing_snapshot"](*args, **kwargs)


_base._existing_snapshot = _existing_snapshot_proxy


def __getattr__(name: str) -> Any:
    """Preserve compatibility for existing private/base helpers and tests."""
    return getattr(_base, name)


def _typed_source_error(code: str, message: str, **detail: Any) -> Exception:
    return acquisition.wnba.WNBAPropHydrationError(code, message, detail=detail)


def _response_json(
    response: Any,
    *,
    unavailable_code: str,
    invalid_code: str,
    source: str,
) -> dict[str, Any]:
    status = int(getattr(response, "status_code", 0) or 0)
    if status != 200:
        raise _typed_source_error(
            unavailable_code,
            "WNBA identity source returned a non-success status",
            source=source,
            status=status,
        )
    try:
        payload = response.json()
    except Exception as exc:
        raise _typed_source_error(
            invalid_code,
            "WNBA identity source did not return JSON",
            source=source,
        ) from exc
    if not isinstance(payload, Mapping):
        raise _typed_source_error(
            invalid_code,
            "WNBA identity source JSON was not an object",
            source=source,
        )
    return dict(payload)


def _official_team_registry(
    season: int,
    *,
    http_get: Callable[..., Any],
) -> tuple[dict[str, list[dict[str, str]]], dict[str, list[dict[str, str]]]]:
    """Resolve current official WNBA TeamIDs without hard-coded franchises."""
    url = f"{acquisition.wnba.WNBA_STATS_BASE}/leaguegamelog"
    payload = acquisition.wnba._request(
        url,
        params={
            "LeagueID": "10",
            "PlayerOrTeam": "T",
            "Season": str(season),
            "SeasonType": "Regular Season",
            "Counter": "0",
            "DateFrom": "",
            "DateTo": "",
            "Direction": "ASC",
            "Sorter": "DATE",
        },
        headers=acquisition.wnba._stats_headers(),
        http_get=http_get,
    )
    rows = acquisition.wnba._result_rows(payload, "LeagueGameLog")
    by_name: dict[str, list[dict[str, str]]] = {}
    by_abbrev: dict[str, list[dict[str, str]]] = {}
    seen: set[tuple[str, str, str]] = set()
    for row in rows:
        team_id = str(row.get("TEAM_ID") or "").strip()
        abbrev = str(row.get("TEAM_ABBREVIATION") or "").strip().upper()
        name = " ".join(str(row.get("TEAM_NAME") or "").split())
        if not team_id or not abbrev or not name:
            continue
        key = (team_id, abbrev, name)
        if key in seen:
            continue
        seen.add(key)
        normalized = {"teamId": team_id, "teamTricode": abbrev, "teamName": name}
        by_name.setdefault(acquisition.wnba._name_key(name), []).append(normalized)
        by_abbrev.setdefault(abbrev, []).append(normalized)
    if not seen:
        raise _typed_source_error(
            "WNBA_OFFICIAL_TEAM_REGISTRY_EMPTY",
            "official WNBA LeagueGameLog returned no usable team identities",
            source=url,
            season=season,
        )
    return by_name, by_abbrev


def _resolve_official_team(
    espn_team: Mapping[str, Any],
    *,
    by_name: dict[str, list[dict[str, str]]],
    by_abbrev: dict[str, list[dict[str, str]]],
) -> dict[str, str]:
    names = [
        str(espn_team.get("displayName") or ""),
        str(espn_team.get("shortDisplayName") or ""),
        " ".join(
            [str(espn_team.get("location") or ""), str(espn_team.get("name") or "")]
        ).strip(),
    ]
    for name in names:
        key = acquisition.wnba._name_key(name)
        matches = by_name.get(key, []) if key else []
        unique = {row["teamId"]: row for row in matches}
        if len(unique) == 1:
            return dict(next(iter(unique.values())))

    abbrev = str(espn_team.get("abbreviation") or "").strip().upper()
    matches = by_abbrev.get(abbrev, []) if abbrev else []
    unique = {row["teamId"]: row for row in matches}
    if len(unique) == 1:
        return dict(next(iter(unique.values())))

    raise _typed_source_error(
        "WNBA_ESPN_TEAM_IDENTITY_UNRESOLVED",
        "ESPN WNBA team could not be reconciled to exactly one official WNBA TeamID",
        espn_team_id=str(espn_team.get("id") or ""),
        espn_abbreviation=abbrev,
    )


def _aware(value: Any) -> datetime | None:
    raw = str(value or "").strip()
    if not raw:
        return None
    try:
        parsed = datetime.fromisoformat(raw.replace("Z", "+00:00"))
    except ValueError:
        return None
    if parsed.tzinfo is None:
        parsed = parsed.replace(tzinfo=timezone.utc)
    return parsed.astimezone(timezone.utc)


def _espn_identity_schedule_for_date(
    *,
    requested_date: str,
    requested_timezone: str,
    now: datetime,
    http_get: Callable[..., Any],
) -> dict[str, Any] | None:
    """Build schedule-shaped identity from governed non-market sources only."""
    try:
        requested_day = date.fromisoformat(requested_date)
        zone = ZoneInfo(requested_timezone)
    except Exception as exc:
        raise _typed_source_error(
            "WNBA_ESPN_IDENTITY_REQUEST_INVALID",
            "requested WNBA fallback date/timezone was invalid",
        ) from exc

    source = ESPN_BASE_URLS["WNBA"]
    try:
        response = http_get(
            source,
            params={"dates": requested_day.strftime("%Y%m%d"), "limit": 100},
            headers={
                "Accept": "application/json",
                "User-Agent": "WOW-V17-WNBA-Prop-Identity/1.0",
            },
            timeout=20,
            follow_redirects=True,
        )
    except Exception as exc:
        raise _typed_source_error(
            "WNBA_ESPN_IDENTITY_SOURCE_UNAVAILABLE",
            "ESPN WNBA identity request failed",
            source=source,
        ) from exc

    body = _response_json(
        response,
        unavailable_code="WNBA_ESPN_IDENTITY_SOURCE_UNAVAILABLE",
        invalid_code="WNBA_ESPN_IDENTITY_SOURCE_INVALID",
        source=source,
    )
    raw_events = body.get("events")
    if not isinstance(raw_events, list):
        raise _typed_source_error(
            "WNBA_ESPN_IDENTITY_SOURCE_INVALID",
            "ESPN WNBA identity response had no event list",
            source=source,
        )

    events: list[tuple[dict[str, Any], datetime]] = []
    for raw in raw_events:
        if not isinstance(raw, dict):
            continue
        event_start = _aware(raw.get("date"))
        if event_start is None:
            continue
        status = raw.get("status") if isinstance(raw.get("status"), Mapping) else {}
        status_type = status.get("type") if isinstance(status.get("type"), Mapping) else {}
        state = " ".join(
            str(status_type.get(key) or "")
            for key in ("state", "name", "description")
        ).lower()
        if status_type.get("completed") is True or any(
            token in state for token in ("in progress", "final", "complete")
        ):
            continue
        if event_start <= now or event_start.astimezone(zone).date() != requested_day:
            continue
        home, away = _espn_competitors(raw)
        if not isinstance(home, dict) or not isinstance(away, dict):
            raise _typed_source_error(
                "WNBA_ESPN_EVENT_IDENTITY_INCOMPLETE",
                "ESPN WNBA event was missing home/away competitors",
                event_id=str(raw.get("id") or ""),
            )
        events.append((raw, event_start))

    # A valid empty scoreboard is a legitimate off-day, not an acquisition failure.
    if not events:
        return None

    by_name, by_abbrev = _official_team_registry(
        requested_day.year,
        http_get=http_get,
    )
    games: list[dict[str, Any]] = []
    for raw, event_start in events:
        event_id = str(raw.get("id") or "").strip()
        home_competitor, away_competitor = _espn_competitors(raw)
        if not event_id or not isinstance(home_competitor, dict) or not isinstance(
            away_competitor, dict
        ):
            raise _typed_source_error(
                "WNBA_ESPN_EVENT_IDENTITY_INCOMPLETE",
                "ESPN WNBA event identity was incomplete",
            )
        home_team = (
            home_competitor.get("team")
            if isinstance(home_competitor.get("team"), Mapping)
            else {}
        )
        away_team = (
            away_competitor.get("team")
            if isinstance(away_competitor.get("team"), Mapping)
            else {}
        )
        official_home = _resolve_official_team(
            home_team, by_name=by_name, by_abbrev=by_abbrev
        )
        official_away = _resolve_official_team(
            away_team, by_name=by_name, by_abbrev=by_abbrev
        )
        start_iso = event_start.isoformat().replace("+00:00", "Z")
        games.append(
            {
                "gameId": f"espn-{event_id}",
                "gameDateTimeUTC": start_iso,
                "gameDateUTC": start_iso,
                "gameStatus": 1,
                "gameStatusText": "Scheduled",
                "homeTeam": official_home,
                "awayTeam": official_away,
            }
        )

    games.sort(key=lambda game: (str(game["gameDateTimeUTC"]), str(game["gameId"])))
    return {
        "leagueSchedule": {"gameDates": [{"games": games}]},
        "wowScheduleProvenance": {
            "provider": ESPN_IDENTITY_PROVIDER,
            "identity_source": source,
            "team_id_source": f"{acquisition.wnba.WNBA_STATS_BASE}/leaguegamelog",
            "game_n": len(games),
            "market_features_used": False,
            "probability_authority": False,
            "can_execute": False,
        },
    }


def acquire_wnba_forward_evidence_batch(
    req: WNBAForwardEvidenceRequest,
    *,
    db: Any,
    now: datetime | None = None,
    http_get: Callable[..., Any] = httpx.get,
) -> dict[str, Any]:
    """Run the stable base path first; recover identity only on its typed source failure."""
    result = _base.acquire_wnba_forward_evidence_batch(
        req,
        db=db,
        now=now,
        http_get=http_get,
    )

    # A caller-supplied governed schedule already passed through the base bridge.
    if req.official_schedule is not None or req.official_schedule_provider is not None:
        if req.official_schedule_provider:
            result["schedule_source_provider"] = str(req.official_schedule_provider)
        return result

    blockers = [str(value) for value in (result.get("blockers") or [])]
    if result.get("status") != "DATA_UNOBTAINABLE" or "WNBA_OFFICIAL_SOURCE_UNAVAILABLE" not in blockers:
        return result

    observed_now = (now or datetime.now(timezone.utc)).astimezone(timezone.utc)
    cached_get = _base._cached_http_get(http_get)
    official_diagnostics = list(result.get("source_diagnostics") or [])
    try:
        recovered_schedule = _espn_identity_schedule_for_date(
            requested_date=req.requested_date,
            requested_timezone=req.requested_timezone,
            now=observed_now,
            http_get=cached_get,
        )
    except Exception as exc:
        fallback_code = str(
            getattr(exc, "code", "")
            or f"WNBA_ESPN_IDENTITY_FALLBACK_FAILED:{type(exc).__name__}"
        )
        # The original typed official-source failure remains the terminal owner.
        # Recovery failures are diagnostic context, not a replacement blocker.
        result["blockers"] = blockers
        result["source_diagnostics"] = official_diagnostics + [
            {"code": fallback_code, "provider": ESPN_IDENTITY_PROVIDER}
        ]
        return result

    if recovered_schedule is None:
        off_day = _base._result_shell(req)
        off_day["schedule_source_provider"] = ESPN_IDENTITY_PROVIDER
        off_day["source_diagnostics"] = official_diagnostics + [
            {
                "code": "WNBA_IDENTITY_FALLBACK_VALID_OFF_DAY",
                "provider": ESPN_IDENTITY_PROVIDER,
                "market_features_used": False,
            }
        ]
        return off_day

    bridged_req = req.model_copy(
        update={
            "official_schedule_provider": ESPN_IDENTITY_PROVIDER,
            "official_schedule": recovered_schedule,
        }
    )
    recovered = _base.acquire_wnba_forward_evidence_batch(
        bridged_req,
        db=db,
        now=observed_now,
        http_get=cached_get,
    )
    recovered["schedule_source_provider"] = ESPN_IDENTITY_PROVIDER
    diagnostics = official_diagnostics + [
        {
            "code": "WNBA_IDENTITY_FALLBACK_USED",
            "provider": ESPN_IDENTITY_PROVIDER,
            "market_features_used": False,
        }
    ]
    for diagnostic in recovered.get("source_diagnostics") or []:
        if diagnostic not in diagnostics:
            diagnostics.append(diagnostic)
    recovered["source_diagnostics"] = diagnostics
    return recovered


def install_wnba_prop_forward_evidence_route(
    app: FastAPI,
    *,
    auth_dependency: Any,
    db_client_fn: Any,
) -> None:
    if any(getattr(route, "path", None) == ROUTE_PATH for route in app.router.routes):
        return

    @app.post(
        ROUTE_PATH,
        dependencies=[scout_route_auth_dependency(auth_dependency)],
        operation_id="acquireWowV17WnbaPropForwardEvidence",
    )
    def acquire(req: WNBAForwardEvidenceRequest) -> dict[str, Any]:
        return acquire_wnba_forward_evidence_batch(req, db=db_client_fn())


__all__ = [
    "CAN_EXECUTE",
    "ESPN_BASE_URLS",
    "ESPN_IDENTITY_PROVIDER",
    "ROUTE_PATH",
    "WNBAForwardEvidenceRequest",
    "acquire_wnba_forward_evidence_batch",
    "install_wnba_prop_forward_evidence_route",
]
