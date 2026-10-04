"""Protected, bounded control plane for WNBA forward-evidence acquisition.

The Render web host intentionally keeps the autonomous in-process evidence sweep
disabled. GitHub's governed OIDC lifecycle workflow can invoke this route in
small rotating batches instead. The route only persists immutable pregame
snapshots for the already-fitted WNBA component routes.

When the WNBA CDN / public schedule page cannot provide a usable schedule body,
this control plane may recover event identity from league-owned WNBA/NBA
scoreboard transports. Recovery supplies event and team identity only; player
history, roster, availability, fitted probability, calibration and publication
authority remain unchanged. The existing OIDC bridge continues to be accepted
when it carries a validated official schedule payload.

No probability, calibration, certification, promotion, publication, ranking,
price, or execution authority is introduced. ``can_execute=false`` always.
"""
from __future__ import annotations

from copy import deepcopy
from datetime import datetime, timezone
import re
from typing import Any, Callable, Mapping
from urllib.parse import urlsplit

import httpx
from fastapi import FastAPI
from pydantic import BaseModel, ConfigDict, Field

from github_actions_oidc import scout_route_auth_dependency
from prop_auto_hydration import PropAutoHydrationError
import v17.wnba_official_schedule_web_fallback  # noqa: F401 - preserve install-order contract
from v17 import wnba_official_schedule_web_fallback as schedule_transport
from v17 import wnba_prop_evidence_acquisition as acquisition

CAN_EXECUTE = False
ROUTE_PATH = "/internal/v17/wnba-prop-forward-evidence/acquire"
STATS_SCOREBOARD_PROVIDER = "WNBA_STATS_SCOREBOARD_V3_RENDER_RECOVERY"
STATS_SCOREBOARD_URL = f"{acquisition.wnba.WNBA_STATS_BASE}/scoreboardv3"
LIVEDATA_SCOREBOARD_PROVIDER = "WNBA_LIVEDATA_SCOREBOARD_10_RENDER_RECOVERY"
LIVEDATA_SCOREBOARD_URL = (
    "https://cdn.nba.com/static/json/liveData/scoreboard/todaysScoreboard_10.json"
)


class WNBAForwardEvidenceRequest(BaseModel):
    model_config = ConfigDict(extra="forbid")
    requested_date: str
    requested_timezone: str = "America/Chicago"
    candidate_offset: int = Field(default=0, ge=0, le=2000)
    max_candidates: int = Field(default=48, ge=1, le=96)
    official_schedule_provider: str | None = None
    official_schedule: dict[str, Any] | None = None


class _StaticJsonResponse:
    """Minimal response contract used to replay one validated official schedule."""

    status_code = 200

    def __init__(self, payload: Mapping[str, Any]) -> None:
        self._payload = deepcopy(dict(payload))
        self.content = b"{}"
        self.text = "{}"

    def json(self) -> dict[str, Any]:
        return deepcopy(self._payload)


def _cached_http_get(http_get: Callable[..., Any]) -> Callable[..., Any]:
    cache: dict[tuple[Any, ...], Any] = {}

    def get(url: str, params=None, headers=None, **kwargs: Any) -> Any:
        params_key = tuple(sorted((str(k), str(v)) for k, v in dict(params or {}).items()))
        headers_key = tuple(sorted((str(k).lower(), str(v)) for k, v in dict(headers or {}).items()))
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

    primary_errors = detail.get("primary_errors") if isinstance(detail.get("primary_errors"), list) else []
    fallback_errors = detail.get("fallback_errors") if isinstance(detail.get("fallback_errors"), list) else []
    playoffs_errors = detail.get("playoffs_errors") if isinstance(detail.get("playoffs_errors"), list) else []
    if (
        primary_errors
        or fallback_errors
        or playoffs_errors
        or detail.get("primary_source")
        or detail.get("fallback_source")
        or detail.get("playoffs_source")
    ):
        attempts = int(getattr(acquisition.wnba, "HTTP_ATTEMPTS", 0) or 0) or None
        primary_kinds, primary_codes = _safe_error_summary(primary_errors)
        fallback_kinds, fallback_codes = _safe_error_summary(fallback_errors)
        playoffs_kinds, playoffs_codes = _safe_error_summary(playoffs_errors)
        sources = [
            {
                "provider": str(detail.get("primary_source") or "WNBA_CDN_SCHEDULE_CURRENT"),
                "host": "cdn.wnba.com",
                "path": "/static/json/staticData/scheduleLeagueV2.json",
                "attempts": attempts,
                "error_kinds": primary_kinds,
                "error_codes": primary_codes,
            },
            {
                "provider": str(detail.get("fallback_source") or "WNBA_OFFICIAL_SCHEDULE_WEB_SSR"),
                "host": "www.wnba.com",
                "path": "/schedule",
                "attempts": attempts,
                "error_kinds": fallback_kinds,
                "error_codes": fallback_codes,
            },
        ]
        if detail.get("playoffs_source") or playoffs_errors:
            sources.append(
                {
                    "provider": str(
                        detail.get("playoffs_source")
                        or "WNBA_OFFICIAL_PLAYOFF_BRACKET_SSR"
                    ),
                    "host": "www.wnba.com",
                    "path": "/webview/playoffs/2026",
                    "attempts": attempts,
                    "error_kinds": playoffs_kinds,
                    "error_codes": playoffs_codes,
                }
            )
        return {"code": code, "sources": sources}

    return {"code": code, "host": None, "path": None, "attempts": None, "error_kinds": []}


def _scoreboard_team(node: Any) -> dict[str, Any]:
    if not isinstance(node, Mapping):
        raise acquisition.wnba.WNBAPropHydrationError(
            "WNBA_STATS_SCOREBOARD_V3_GAME_IDENTITY_INVALID",
            "WNBA Stats scoreboard team identity was missing",
        )
    team_id = str(node.get("teamId") or "").strip()
    if not team_id:
        raise acquisition.wnba.WNBAPropHydrationError(
            "WNBA_STATS_SCOREBOARD_V3_GAME_IDENTITY_INVALID",
            "WNBA Stats scoreboard teamId was missing",
        )
    return {
        "teamId": team_id,
        "teamTricode": str(node.get("teamTricode") or node.get("teamTriCode") or "").strip().upper(),
        "teamCity": str(node.get("teamCity") or "").strip(),
        "teamName": str(node.get("teamName") or "").strip(),
    }


def _scoreboard_schedule_for_date(
    requested_date: str,
    *,
    http_get: Callable[..., Any],
) -> dict[str, Any]:
    """Normalize league-owned WNBA Stats Scoreboard V3 into the schedule contract."""
    try:
        payload = acquisition.wnba._request(
            STATS_SCOREBOARD_URL,
            http_get=http_get,
            params={"LeagueID": "10", "GameDate": requested_date},
            headers=acquisition.wnba._stats_headers(),
        )
    except Exception as exc:
        raise acquisition.wnba.WNBAPropHydrationError(
            "WNBA_STATS_SCOREBOARD_V3_UNAVAILABLE",
            "WNBA Stats Scoreboard V3 request failed",
            detail={"source": STATS_SCOREBOARD_PROVIDER, "url": STATS_SCOREBOARD_URL},
        ) from exc

    scoreboard = payload.get("scoreboard") if isinstance(payload, Mapping) else None
    raw_games = scoreboard.get("games") if isinstance(scoreboard, Mapping) else None
    if not isinstance(raw_games, list):
        raise acquisition.wnba.WNBAPropHydrationError(
            "WNBA_STATS_SCOREBOARD_V3_INVALID",
            "WNBA Stats Scoreboard V3 response was missing scoreboard.games",
            detail={"source": STATS_SCOREBOARD_PROVIDER, "url": STATS_SCOREBOARD_URL},
        )

    games: list[dict[str, Any]] = []
    for raw in raw_games:
        if not isinstance(raw, Mapping):
            continue
        game_id = str(raw.get("gameId") or raw.get("gameID") or "").strip()
        start = str(
            raw.get("gameTimeUTC")
            or raw.get("gameDateTimeUTC")
            or raw.get("gameDateUTC")
            or ""
        ).strip()
        if not game_id or not start:
            raise acquisition.wnba.WNBAPropHydrationError(
                "WNBA_STATS_SCOREBOARD_V3_GAME_IDENTITY_INVALID",
                "WNBA Stats scoreboard gameId or UTC start time was missing",
                detail={"source": STATS_SCOREBOARD_PROVIDER, "url": STATS_SCOREBOARD_URL},
            )
        try:
            status = int(raw.get("gameStatus") or 0)
        except (TypeError, ValueError) as exc:
            raise acquisition.wnba.WNBAPropHydrationError(
                "WNBA_STATS_SCOREBOARD_V3_GAME_IDENTITY_INVALID",
                "WNBA Stats scoreboard game status was invalid",
                detail={"source": STATS_SCOREBOARD_PROVIDER, "url": STATS_SCOREBOARD_URL},
            ) from exc
        games.append(
            {
                "gameId": game_id,
                "gameDateTimeUTC": start,
                "gameDateUTC": start,
                "gameStatus": status,
                "gameStatusText": str(raw.get("gameStatusText") or "").strip(),
                "homeTeam": _scoreboard_team(raw.get("homeTeam")),
                "awayTeam": _scoreboard_team(raw.get("awayTeam")),
            }
        )

    return {
        "leagueSchedule": {
            "gameDates": ([{"gameDate": requested_date, "games": games}] if games else [])
        },
        "wowScheduleProvenance": {
            "provider": STATS_SCOREBOARD_PROVIDER,
            "url": STATS_SCOREBOARD_URL,
            "game_n": len(games),
            "market_features_used": False,
            "probability_authority": False,
            "can_execute": False,
        },
    }


def _livedata_team(node: Any) -> dict[str, Any]:
    """Copy only official team identity fields; never copy odds or game-market fields."""
    if not isinstance(node, Mapping):
        raise acquisition.wnba.WNBAPropHydrationError(
            "WNBA_LIVEDATA_SCOREBOARD_GAME_IDENTITY_INVALID",
            "WNBA liveData scoreboard team identity was missing",
        )
    team_id = str(node.get("teamId") or "").strip()
    if not team_id:
        raise acquisition.wnba.WNBAPropHydrationError(
            "WNBA_LIVEDATA_SCOREBOARD_GAME_IDENTITY_INVALID",
            "WNBA liveData scoreboard teamId was missing",
        )
    return {
        "teamId": team_id,
        "teamTricode": str(node.get("teamTricode") or node.get("teamTriCode") or "").strip().upper(),
        "teamCity": str(node.get("teamCity") or "").strip(),
        "teamName": str(node.get("teamName") or "").strip(),
    }


def _livedata_schedule_for_date(
    requested_date: str,
    *,
    http_get: Callable[..., Any],
) -> dict[str, Any]:
    """Normalize the official WNBA liveData *today* scoreboard, fail-closed by date."""
    try:
        payload = acquisition.wnba._request(
            LIVEDATA_SCOREBOARD_URL,
            http_get=http_get,
            headers={
                "Accept": "application/json, text/plain, */*",
                "Origin": "https://www.wnba.com",
                "Referer": "https://www.wnba.com/",
                "User-Agent": (
                    "Mozilla/5.0 (Windows NT 10.0; Win64; x64) "
                    "AppleWebKit/537.36 (KHTML, like Gecko) Chrome/120.0.0.0 Safari/537.36"
                ),
            },
        )
    except Exception as exc:
        raise acquisition.wnba.WNBAPropHydrationError(
            "WNBA_LIVEDATA_SCOREBOARD_UNAVAILABLE",
            "WNBA liveData scoreboard request failed",
            detail={"source": LIVEDATA_SCOREBOARD_PROVIDER, "url": LIVEDATA_SCOREBOARD_URL},
        ) from exc

    scoreboard = payload.get("scoreboard") if isinstance(payload, Mapping) else None
    if not isinstance(scoreboard, Mapping):
        raise acquisition.wnba.WNBAPropHydrationError(
            "WNBA_LIVEDATA_SCOREBOARD_INVALID",
            "WNBA liveData response was missing scoreboard",
            detail={"source": LIVEDATA_SCOREBOARD_PROVIDER, "url": LIVEDATA_SCOREBOARD_URL},
        )
    source_date = str(scoreboard.get("gameDate") or "").strip()[:10]
    if source_date != requested_date:
        raise acquisition.wnba.WNBAPropHydrationError(
            "WNBA_LIVEDATA_SCOREBOARD_DATE_MISMATCH",
            "WNBA liveData scoreboard does not represent the requested slate date",
            detail={
                "source": LIVEDATA_SCOREBOARD_PROVIDER,
                "url": LIVEDATA_SCOREBOARD_URL,
                "requested_date": requested_date,
                "source_date": source_date,
            },
        )
    raw_games = scoreboard.get("games")
    if not isinstance(raw_games, list):
        raise acquisition.wnba.WNBAPropHydrationError(
            "WNBA_LIVEDATA_SCOREBOARD_INVALID",
            "WNBA liveData response was missing scoreboard.games",
            detail={"source": LIVEDATA_SCOREBOARD_PROVIDER, "url": LIVEDATA_SCOREBOARD_URL},
        )

    games: list[dict[str, Any]] = []
    for raw in raw_games:
        if not isinstance(raw, Mapping):
            continue
        game_id = str(raw.get("gameId") or raw.get("gameID") or "").strip()
        start = str(
            raw.get("gameTimeUTC")
            or raw.get("gameDateTimeUTC")
            or raw.get("gameDateUTC")
            or ""
        ).strip()
        if not game_id or not start:
            raise acquisition.wnba.WNBAPropHydrationError(
                "WNBA_LIVEDATA_SCOREBOARD_GAME_IDENTITY_INVALID",
                "WNBA liveData gameId or UTC start time was missing",
                detail={"source": LIVEDATA_SCOREBOARD_PROVIDER, "url": LIVEDATA_SCOREBOARD_URL},
            )
        try:
            status = int(raw.get("gameStatus") or 0)
        except (TypeError, ValueError) as exc:
            raise acquisition.wnba.WNBAPropHydrationError(
                "WNBA_LIVEDATA_SCOREBOARD_GAME_IDENTITY_INVALID",
                "WNBA liveData game status was invalid",
                detail={"source": LIVEDATA_SCOREBOARD_PROVIDER, "url": LIVEDATA_SCOREBOARD_URL},
            ) from exc
        # Whitelist only schedule identity. Fields such as pbOdds are deliberately ignored.
        games.append(
            {
                "gameId": game_id,
                "gameDateTimeUTC": start,
                "gameDateUTC": start,
                "gameStatus": status,
                "gameStatusText": str(raw.get("gameStatusText") or "").strip(),
                "homeTeam": _livedata_team(raw.get("homeTeam")),
                "awayTeam": _livedata_team(raw.get("awayTeam")),
            }
        )

    return {
        "leagueSchedule": {
            "gameDates": ([{"gameDate": requested_date, "games": games}] if games else [])
        },
        "wowScheduleProvenance": {
            "provider": LIVEDATA_SCOREBOARD_PROVIDER,
            "url": LIVEDATA_SCOREBOARD_URL,
            "game_n": len(games),
            "market_features_used": False,
            "probability_authority": False,
            "can_execute": False,
        },
    }


def _schedule_replay_get(
    base_get: Callable[..., Any],
    schedule: Mapping[str, Any],
) -> Callable[..., Any]:
    """Replay one validated recovered schedule into nested hydration only."""
    def get(url: str, params=None, headers=None, **kwargs: Any) -> Any:
        if str(url) == acquisition.wnba.WNBA_SCHEDULE_URL:
            return _StaticJsonResponse(schedule)
        return base_get(url, params=params, headers=headers, **kwargs)
    return get


def _apply_recovery_provenance(
    raw: dict[str, Any],
    *,
    provider: str,
    url: str,
) -> dict[str, Any]:
    """Correct provenance after schedule replay so a failed primary source is never implied."""
    captured = str(raw.get("captured_at") or "")
    sources = dict(raw.get("source_timestamps") or {})
    sources.pop(getattr(schedule_transport, "CDN_PROVIDER", "WNBA_CDN_SCHEDULE_CURRENT"), None)
    if captured:
        sources[provider] = captured
    raw["source_timestamps"] = sources
    raw["schedule_source_provider"] = provider
    raw["schedule_source_url"] = url
    role = raw.get("role_status")
    if isinstance(role, dict):
        role["schedule_source_provider"] = provider
        role["schedule_source_url"] = url
        role["source"] = f"{provider} + WNBA Stats roster + official WNBA injury report"
    raw["rate_provenance"] = (
        "Official WNBA LeagueGameLog player rows; current event/team from "
        f"{provider}; roster from CommonTeamRoster; availability from official WNBA injury-report PDF"
    )
    return raw


def _apply_scoreboard_provenance(raw: dict[str, Any]) -> dict[str, Any]:
    """Compatibility wrapper for the Stats Scoreboard V3 recovery provenance."""
    return _apply_recovery_provenance(
        raw,
        provider=STATS_SCOREBOARD_PROVIDER,
        url=STATS_SCOREBOARD_URL,
    )


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


def _recovered_players(
    schedule: Mapping[str, Any],
    *,
    req: WNBAForwardEvidenceRequest,
    now: datetime,
    cached_get: Callable[..., Any],
) -> tuple[list[dict[str, Any]], Callable[..., Any]]:
    evidence_http_get = _schedule_replay_get(cached_get, schedule)
    players = acquisition._schedule_players(
        schedule,
        requested_date=req.requested_date,
        requested_timezone=req.requested_timezone,
        now=now,
        http_get=evidence_http_get,
    )
    return players, evidence_http_get


def acquire_wnba_forward_evidence_batch(
    req: WNBAForwardEvidenceRequest,
    *,
    db: Any,
    now: datetime | None = None,
    http_get: Callable[..., Any] = httpx.get,
) -> dict[str, Any]:
    bridge_requested = req.official_schedule is not None or req.official_schedule_provider is not None
    if bridge_requested:
        result = _result_shell(req)
        if req.official_schedule is None or req.official_schedule_provider is None:
            result["status"] = "DATA_UNOBTAINABLE"
            result["blockers"] = ["WNBA_OIDC_SCHEDULE_BRIDGE_INCOMPLETE"]
            result["source_diagnostics"] = [{"code": "WNBA_OIDC_SCHEDULE_BRIDGE_INCOMPLETE"}]
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
            code = str(getattr(exc, "code", "") or "WNBA_OIDC_SCHEDULE_BRIDGE_INVALID")
            result["status"] = "DATA_UNOBTAINABLE"
            result["blockers"] = [code]
            result["source_diagnostics"] = [
                {"code": code, "provider": str(req.official_schedule_provider or "")}
            ]
            return result

    now = (now or datetime.now(timezone.utc)).astimezone(timezone.utc)
    cached_get = _cached_http_get(http_get)
    evidence_http_get = cached_get
    recovered_provider: str | None = None
    recovered_url: str | None = None
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
        if code != "WNBA_OFFICIAL_SOURCE_UNAVAILABLE":
            result["status"] = "DATA_UNOBTAINABLE"
            result["blockers"] = [
                getattr(exc, "code", f"WNBA_FORWARD_DISCOVERY_FAILED:{type(exc).__name__}")
            ]
            diagnostic = _source_diagnostic(exc)
            if diagnostic is not None:
                result["source_diagnostics"] = [diagnostic]
            return result

        official_diagnostic = _source_diagnostic(exc)
        if official_diagnostic is not None:
            result["source_diagnostics"].append(official_diagnostic)

        stats_recovery_code: str | None = None
        try:
            schedule = _scoreboard_schedule_for_date(req.requested_date, http_get=cached_get)
            players, evidence_http_get = _recovered_players(
                schedule,
                req=req,
                now=now,
                cached_get=cached_get,
            )
            recovered_provider = STATS_SCOREBOARD_PROVIDER
            recovered_url = STATS_SCOREBOARD_URL
            result["schedule_source_provider"] = recovered_provider
            result["source_diagnostics"].append(
                {
                    "code": "WNBA_STATS_SCOREBOARD_V3_RECOVERY_USED",
                    "provider": STATS_SCOREBOARD_PROVIDER,
                    "game_n": sum(
                        len(block.get("games") or [])
                        for block in schedule.get("leagueSchedule", {}).get("gameDates", [])
                        if isinstance(block, Mapping)
                    ),
                }
            )
        except Exception as stats_exc:
            stats_recovery_code = str(
                getattr(stats_exc, "code", "")
                or f"WNBA_STATS_SCOREBOARD_V3_RECOVERY_FAILED:{type(stats_exc).__name__}"
            )
            result["source_diagnostics"].append(
                {"code": stats_recovery_code, "provider": STATS_SCOREBOARD_PROVIDER}
            )
            try:
                schedule = _livedata_schedule_for_date(req.requested_date, http_get=cached_get)
                players, evidence_http_get = _recovered_players(
                    schedule,
                    req=req,
                    now=now,
                    cached_get=cached_get,
                )
                recovered_provider = LIVEDATA_SCOREBOARD_PROVIDER
                recovered_url = LIVEDATA_SCOREBOARD_URL
                result["schedule_source_provider"] = recovered_provider
                result["source_diagnostics"].append(
                    {
                        "code": "WNBA_LIVEDATA_SCOREBOARD_RECOVERY_USED",
                        "provider": LIVEDATA_SCOREBOARD_PROVIDER,
                        "game_n": sum(
                            len(block.get("games") or [])
                            for block in schedule.get("leagueSchedule", {}).get("gameDates", [])
                            if isinstance(block, Mapping)
                        ),
                        "market_features_used": False,
                    }
                )
            except Exception as live_exc:
                live_code = str(
                    getattr(live_exc, "code", "")
                    or f"WNBA_LIVEDATA_SCOREBOARD_RECOVERY_FAILED:{type(live_exc).__name__}"
                )
                result["status"] = "DATA_UNOBTAINABLE"
                # Preserve the pre-existing typed blocker contract while exposing the
                # additional official-source failure in the safe diagnostics receipt.
                result["blockers"] = [code, stats_recovery_code]
                result["source_diagnostics"].append(
                    {"code": live_code, "provider": LIVEDATA_SCOREBOARD_PROVIDER}
                )
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
                http_get=evidence_http_get,
                now=now,
                source_capture_timestamp=now.isoformat(),
                source_label="V17_WNBA_OIDC_FORWARD_DISCOVERY",
                opponent=candidate.get("opponent"),
                canonical_event_id=str(candidate["event_id"]),
            )
            if recovered_provider and recovered_url:
                raw = _apply_recovery_provenance(
                    dict(raw),
                    provider=recovered_provider,
                    url=recovered_url,
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
            snapshot_id, _fingerprint, snapshot = acquisition._snapshot_payload(row, normalized)
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
    "LIVEDATA_SCOREBOARD_PROVIDER",
    "LIVEDATA_SCOREBOARD_URL",
    "ROUTE_PATH",
    "STATS_SCOREBOARD_PROVIDER",
    "STATS_SCOREBOARD_URL",
    "WNBAForwardEvidenceRequest",
    "acquire_wnba_forward_evidence_batch",
    "install_wnba_prop_forward_evidence_route",
]
