"""Protected, bounded control plane for WNBA forward-evidence acquisition.

The Render web host intentionally keeps the autonomous in-process evidence sweep
disabled. GitHub's governed OIDC lifecycle workflow invokes this route in small
rotating batches. The route only persists immutable pregame snapshots for the
already-fitted WNBA component routes.

The primary discovery source remains the official WNBA schedule transports. When
all of those transports are typed unavailable, V17 may recover *identity only*
from its already-governed ESPN WNBA scoreboard source, reconcile each ESPN team
back to a unique official WNBA Stats TeamID from LeagueGameLog, and install that
schedule for the duration of the request. Player rosters, game history and
availability remain on the existing official WNBA evidence adapters. The
identity fallback grants no probability, calibration, certification, promotion,
publication, ranking, price, or execution authority. ``can_execute=false``
always.
"""
from __future__ import annotations

from datetime import date, datetime, timezone
import re
from typing import Any, Callable, Mapping
from urllib.parse import urlsplit
from zoneinfo import ZoneInfo

import httpx
from fastapi import FastAPI
from pydantic import BaseModel, ConfigDict, Field

from basketball_event_hydration_runtime import ESPN_BASE_URLS, _espn_competitors
from github_actions_oidc import scout_route_auth_dependency
from prop_auto_hydration import PropAutoHydrationError
import v17.wnba_official_schedule_web_fallback  # noqa: F401 - preserve install-order contract
from v17 import wnba_official_schedule_web_fallback as schedule_transport
from v17 import wnba_prop_evidence_acquisition as acquisition

CAN_EXECUTE = False
ROUTE_PATH = "/internal/v17/wnba-prop-forward-evidence/acquire"
ESPN_IDENTITY_PROVIDER = schedule_transport.ESPN_IDENTITY_BRIDGE_PROVIDER


class WNBAForwardEvidenceRequest(BaseModel):
    model_config = ConfigDict(extra="forbid")
    requested_date: str
    requested_timezone: str = "America/Chicago"
    candidate_offset: int = Field(default=0, ge=0, le=2000)
    max_candidates: int = Field(default=48, ge=1, le=96)
    official_schedule_provider: str | None = None
    official_schedule: dict[str, Any] | None = None


def _cached_http_get(http_get: Callable[..., Any]) -> Callable[..., Any]:
    cache: dict[tuple[Any, ...], Any] = {}

    def get(url: str, params=None, headers=None, **kwargs: Any) -> Any:
        params_key = tuple(sorted((str(k), str(v)) for k, v in dict(params or {}).items()))
        headers_key = tuple(
            sorted((str(k).lower(), str(v)) for k, v in dict(headers or {}).items())
        )
        key = (str(url), params_key, headers_key)
        if key not in cache:
            cache[key] = http_get(url, params=params, headers=headers, **kwargs)
        return cache[key]

    return get


def _safe_error_summary(raw_errors: list[Any]) -> tuple[list[str], list[str]]:
    """Return type names and typed codes without leaking remote response text."""
    error_kinds: list[str] = []
    error_codes: list[str] = []
    for raw in raw_errors:
        text = str(raw)
        kind = text.split(":", 1)[0].strip()
        if kind and kind not in error_kinds:
            error_kinds.append(kind)
        remainder = text.split(":", 1)[1].strip() if ":" in text else ""
        code = remainder.split(":", 1)[0].strip()
        if re.fullmatch(r"[A-Z][A-Z0-9_]+", code or "") and code not in error_codes:
            error_codes.append(code)
    return error_kinds, error_codes


def _source_diagnostic(exc: Exception) -> dict[str, Any] | None:
    """Return a secret-safe source receipt for typed WNBA acquisition failures."""
    code = str(getattr(exc, "code", "") or "").strip()
    detail = getattr(exc, "detail", None)
    if code != "WNBA_OFFICIAL_SOURCE_UNAVAILABLE" or not isinstance(detail, dict):
        return None

    raw_url = str(detail.get("url") or "").strip()
    if raw_url:
        parsed = urlsplit(raw_url)
        raw_errors = detail.get("errors") if isinstance(detail.get("errors"), list) else []
        error_kinds, _error_codes = _safe_error_summary(raw_errors)
        attempts = detail.get("attempts")
        return {
            "code": code,
            "host": parsed.netloc,
            "path": parsed.path,
            "attempts": int(attempts) if isinstance(attempts, int) else None,
            "error_kinds": error_kinds,
        }

    primary_errors = (
        detail.get("primary_errors") if isinstance(detail.get("primary_errors"), list) else []
    )
    minimal_errors = (
        detail.get("primary_minimal_errors")
        if isinstance(detail.get("primary_minimal_errors"), list)
        else []
    )
    fallback_errors = (
        detail.get("fallback_errors") if isinstance(detail.get("fallback_errors"), list) else []
    )
    if (
        primary_errors
        or minimal_errors
        or fallback_errors
        or detail.get("primary_source")
        or detail.get("fallback_source")
    ):
        attempts = int(getattr(acquisition.wnba, "HTTP_ATTEMPTS", 0) or 0) or None
        primary_kinds, primary_codes = _safe_error_summary(primary_errors)
        minimal_kinds, minimal_codes = _safe_error_summary(minimal_errors)
        fallback_kinds, fallback_codes = _safe_error_summary(fallback_errors)
        return {
            "code": code,
            "sources": [
                {
                    "provider": str(
                        detail.get("primary_source") or "WNBA_CDN_SCHEDULE_CURRENT"
                    ),
                    "host": "cdn.wnba.com",
                    "path": "/static/json/staticData/scheduleLeagueV2.json",
                    "attempts": attempts,
                    "error_kinds": primary_kinds,
                    "error_codes": primary_codes,
                },
                {
                    "provider": "WNBA_CDN_SCHEDULE_CURRENT_MINIMAL",
                    "host": "cdn.wnba.com",
                    "path": "/static/json/staticData/scheduleLeagueV2.json",
                    "attempts": attempts,
                    "error_kinds": minimal_kinds,
                    "error_codes": minimal_codes,
                },
                {
                    "provider": str(
                        detail.get("fallback_source") or "WNBA_OFFICIAL_SCHEDULE_WEB_SSR"
                    ),
                    "host": "www.wnba.com",
                    "path": "/schedule",
                    "attempts": attempts,
                    "error_kinds": fallback_kinds,
                    "error_codes": fallback_codes,
                },
            ],
        }

    return {"code": code, "host": None, "path": None, "attempts": None, "error_kinds": []}


def _result_shell(req: WNBAForwardEvidenceRequest) -> dict[str, Any]:
    return {
        "status": "COMPLETED",
        "sport": "WNBA",
        "requested_date": req.requested_date,
        "requested_timezone": req.requested_timezone,
        "candidate_offset": req.candidate_offset,
        "max_candidates": req.max_candidates,
        "total_candidates": 0,
        "window_candidate_n": 0,
        "attempted": 0,
        "already_captured": 0,
        "hydrated": 0,
        "persisted": 0,
        "held": 0,
        "snapshot_write_failed": 0,
        "next_offset": None,
        "blockers": [],
        "source_diagnostics": [],
        "schedule_source_provider": None,
        "probability_publishable": False,
        "automatic_certification": False,
        "automatic_promotion": False,
        "can_execute": False,
    }


def _existing_snapshot(db: Any, candidate: dict[str, Any], stat_type: str) -> bool:
    result = (
        db.table("wow_prop_evidence_snapshots")
        .select("source_snapshot_id")
        .eq("event_id", str(candidate["event_id"]))
        .eq("sport", "WNBA")
        .eq("player", str(candidate["player"]))
        .eq("stat_type", stat_type)
        .limit(1)
        .execute()
    )
    return bool(result.data or [])


def _flatten(players: list[dict[str, Any]]) -> list[tuple[dict[str, Any], str]]:
    return [
        (candidate, stat_type)
        for candidate in players
        for stat_type in acquisition.CORE_STATS
    ]


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
) -> tuple[dict[str, list[dict[str, str]]], dict[str, list[dict[str, str]]]]]:
    """Resolve current official WNBA TeamIDs without hard-coding franchises."""
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


def _espn_identity_schedule_for_date(
    *,
    requested_date: str,
    requested_timezone: str,
    now: datetime,
    http_get: Callable[..., Any],
) -> dict[str, Any] | None:
    """Build a schedule-shaped identity payload from governed non-market sources."""
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
        event_start = acquisition._aware(raw.get("date"))
        if event_start is None:
            continue
        status = raw.get("status") if isinstance(raw.get("status"), Mapping) else {}
        status_type = (
            status.get("type") if isinstance(status.get("type"), Mapping) else {}
        )
        state = " ".join(
            str(status_type.get(key) or "")
            for key in ("state", "name", "description")
        ).lower()
        completed = status_type.get("completed") is True
        if completed or any(
            token in state for token in ("in progress", "final", "post", "complete")
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
            home_team,
            by_name=by_name,
            by_abbrev=by_abbrev,
        )
        official_away = _resolve_official_team(
            away_team,
            by_name=by_name,
            by_abbrev=by_abbrev,
        )
        games.append(
            {
                "gameId": f"espn-{event_id}",
                "gameDateTimeUTC": event_start.isoformat().replace("+00:00", "Z"),
                "gameDateUTC": event_start.isoformat().replace("+00:00", "Z"),
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
    bridge_requested = (
        req.official_schedule is not None or req.official_schedule_provider is not None
    )
    if bridge_requested:
        result = _result_shell(req)
        if req.official_schedule is None or req.official_schedule_provider is None:
            result["status"] = "DATA_UNOBTAINABLE"
            result["blockers"] = ["WNBA_OIDC_SCHEDULE_BRIDGE_INCOMPLETE"]
            result["source_diagnostics"] = [
                {"code": "WNBA_OIDC_SCHEDULE_BRIDGE_INCOMPLETE"}
            ]
            return result
        try:
            with schedule_transport.official_schedule_override(
                req.official_schedule,
                provider=req.official_schedule_provider,
            ):
                nested = req.model_copy(
                    update={"official_schedule": None, "official_schedule_provider": None}
                )
                bridged = acquire_wnba_forward_evidence_batch(
                    nested,
                    db=db,
                    now=now,
                    http_get=http_get,
                )
                bridged["schedule_source_provider"] = str(req.official_schedule_provider)
                return bridged
        except acquisition.wnba.WNBAPropHydrationError as exc:
            code = str(
                getattr(exc, "code", "") or "WNBA_OIDC_SCHEDULE_BRIDGE_INVALID"
            )
            result["status"] = "DATA_UNOBTAINABLE"
            result["blockers"] = [code]
            result["source_diagnostics"] = [
                {"code": code, "provider": str(req.official_schedule_provider or "")}
            ]
            return result

    now = (now or datetime.now(timezone.utc)).astimezone(timezone.utc)
    cached_get = _cached_http_get(http_get)
    result = _result_shell(req)
    try:
        schedule = acquisition._request_schedule(http_get=cached_get)
        players = acquisition._schedule_players(
            schedule,
            requested_date=req.requested_date,
            requested_timezone=req.requested_timezone,
            now=now,
            http_get=cached_get,
        )
    except Exception as exc:
        code = str(getattr(exc, "code", "") or "")
        if code == "WNBA_OFFICIAL_SOURCE_UNAVAILABLE":
            official_diagnostic = _source_diagnostic(exc)
            try:
                recovered_schedule = _espn_identity_schedule_for_date(
                    requested_date=req.requested_date,
                    requested_timezone=req.requested_timezone,
                    now=now,
                    http_get=cached_get,
                )
            except Exception as fallback_exc:
                fallback_code = str(
                    getattr(fallback_exc, "code", "")
                    or f"WNBA_ESPN_IDENTITY_FALLBACK_FAILED:{type(fallback_exc).__name__}"
                )
                result["status"] = "DATA_UNOBTAINABLE"
                result["blockers"] = [code, fallback_code]
                if official_diagnostic is not None:
                    result["source_diagnostics"].append(official_diagnostic)
                result["source_diagnostics"].append(
                    {"code": fallback_code, "provider": ESPN_IDENTITY_PROVIDER}
                )
                return result

            if recovered_schedule is None:
                result["schedule_source_provider"] = ESPN_IDENTITY_PROVIDER
                if official_diagnostic is not None:
                    result["source_diagnostics"].append(official_diagnostic)
                result["source_diagnostics"].append(
                    {
                        "code": "WNBA_IDENTITY_FALLBACK_VALID_OFF_DAY",
                        "provider": ESPN_IDENTITY_PROVIDER,
                    }
                )
                return result

            nested = req.model_copy(
                update={
                    "official_schedule_provider": ESPN_IDENTITY_PROVIDER,
                    "official_schedule": recovered_schedule,
                }
            )
            recovered = acquire_wnba_forward_evidence_batch(
                nested,
                db=db,
                now=now,
                http_get=cached_get,
            )
            diagnostics = []
            if official_diagnostic is not None:
                diagnostics.append(official_diagnostic)
            diagnostics.append(
                {
                    "code": "WNBA_IDENTITY_FALLBACK_USED",
                    "provider": ESPN_IDENTITY_PROVIDER,
                    "market_features_used": False,
                }
            )
            for diagnostic in recovered.get("source_diagnostics") or []:
                if diagnostic not in diagnostics:
                    diagnostics.append(diagnostic)
            recovered["source_diagnostics"] = diagnostics
            return recovered

        result["status"] = "DATA_UNOBTAINABLE"
        result["blockers"] = [
            getattr(exc, "code", f"WNBA_FORWARD_DISCOVERY_FAILED:{type(exc).__name__}")
        ]
        diagnostic = _source_diagnostic(exc)
        if diagnostic is not None:
            result["source_diagnostics"] = [diagnostic]
        return result

    flattened = _flatten(players)
    result["total_candidates"] = len(flattened)
    start = min(req.candidate_offset, len(flattened))
    end = min(start + req.max_candidates, len(flattened))
    window = flattened[start:end]
    result["window_candidate_n"] = len(window)
    result["next_offset"] = end if end < len(flattened) else None

    for candidate, stat_type in window:
        result["attempted"] += 1
        try:
            if _existing_snapshot(db, candidate, stat_type):
                result["already_captured"] += 1
                continue
        except Exception as exc:
            result["held"] += 1
            result["blockers"].append(
                f"{candidate['player']}:{stat_type}:WNBA_EXISTING_SNAPSHOT_QUERY_FAILED:{type(exc).__name__}"
            )
            continue

        try:
            raw = acquisition.auto_hydrate_prop_evidence(
                sport="WNBA",
                player=str(candidate["player"]),
                stat_type=stat_type,
                event_start_time=str(candidate["event_start_time"]),
                http_get=cached_get,
                now=now,
                source_capture_timestamp=now.isoformat(),
                source_label="V17_WNBA_FORWARD_DISCOVERY",
                opponent=candidate.get("opponent"),
                canonical_event_id=str(candidate["event_id"]),
            )
            evidence = acquisition.RawPropEvidence.model_validate(raw)
            line = acquisition._candidate_line(evidence.game_log)
            row = acquisition.PickRequestRow(
                row_key=(
                    f"wnba-forward:{candidate['official_game_id']}:"
                    f"{candidate['player']}:{stat_type}:{line}"
                ),
                event_id=str(candidate["event_id"]),
                event_start_time=str(candidate["event_start_time"]),
                sport="WNBA",
                player=str(candidate["player"]),
                stat_type=stat_type,
                line=line,
                direction="MORE",
                evidence=evidence,
                source_type=acquisition.SOURCE_TYPE,
                platform=acquisition.PLATFORM,
                opponent=candidate.get("opponent"),
                source_capture_timestamp=now.isoformat(),
            )
            normalized = acquisition._validate_evidence(row, stat_type)
            snapshot_id, _fingerprint, snapshot = acquisition._snapshot_payload(
                row, normalized
            )
            snapshot["source_snapshot_id"] = snapshot_id
            result["hydrated"] += 1
            db.table("wow_prop_evidence_snapshots").upsert(
                snapshot, on_conflict="source_snapshot_id"
            ).execute()
            result["persisted"] += 1
        except (PropAutoHydrationError, acquisition.wnba.WNBAPropHydrationError) as exc:
            result["held"] += 1
            result["blockers"].append(
                f"{candidate['player']}:{stat_type}:{getattr(exc, 'code', type(exc).__name__)}"
            )
            diagnostic = _source_diagnostic(exc)
            if diagnostic is not None and diagnostic not in result["source_diagnostics"]:
                result["source_diagnostics"].append(diagnostic)
        except Exception as exc:
            result["held"] += 1
            result["snapshot_write_failed"] += 1
            result["blockers"].append(
                f"{candidate['player']}:{stat_type}:WNBA_FORWARD_ACQUISITION_ERROR:{type(exc).__name__}"
            )

    result["blockers"] = list(dict.fromkeys(result["blockers"]))
    if result["snapshot_write_failed"] and result["persisted"] == 0:
        result["status"] = "RUN_INVALID_PROP_SNAPSHOT_WRITE_FAILURE"
    elif result["held"]:
        result["status"] = "COMPLETED_WITH_ROW_BLOCKERS"
    return result


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
    "ESPN_IDENTITY_PROVIDER",
    "ROUTE_PATH",
    "WNBAForwardEvidenceRequest",
    "acquire_wnba_forward_evidence_batch",
    "install_wnba_prop_forward_evidence_route",
]
