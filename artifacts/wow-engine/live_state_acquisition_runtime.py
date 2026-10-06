"""Server-owned raw live-state acquisition for governed in-play scoring.

This module acquires and persists immutable point-in-time game state only. It does
not calculate sporting probability, market probability, calibration, ranking, or
wager execution. Raw snapshots are intentionally not model-ready; a certified
sport-specific live feature materializer must create the model-bound snapshot
consumed by live_probability_runtime.
"""
from __future__ import annotations

import hashlib
import json
import re
import time
import unicodedata
import uuid
from datetime import datetime, timezone
from typing import Any, Callable, Mapping, Optional

import httpx
from fastapi import HTTPException
from pydantic import BaseModel, Field
from v17.team_event_capability_manifest import EXPECTED_TEAM_EVENT_SPORTS, normalize_team_event_sport

CAN_EXECUTE = False
RAW_FEATURE_FAMILY = "LIVE_RAW_STATE_ACQUISITION_V1"
RAW_FEATURE_ARTIFACT_VERSION = "1"
HTTP_TIMEOUT_SECONDS = 8.0
HTTP_ATTEMPTS = 2

MLB_FEED = "https://statsapi.mlb.com/api/v1.1/game/{event_id}/feed/live"
ESPN_TEAM_SUMMARY_PATHS = {
    "NFL": "https://site.api.espn.com/apis/site/v2/sports/football/nfl/summary",
    "NCAAF": "https://site.api.espn.com/apis/site/v2/sports/football/college-football/summary",
    "NBA": "https://site.api.espn.com/apis/site/v2/sports/basketball/nba/summary",
    "WNBA": "https://site.api.espn.com/apis/site/v2/sports/basketball/wnba/summary",
    "NCAAB": "https://site.api.espn.com/apis/site/v2/sports/basketball/mens-college-basketball/summary",
    "NHL": "https://site.api.espn.com/apis/site/v2/sports/hockey/nhl/summary",
}
LIVE_STATE_CAPTURE_SPORTS = frozenset({"MLB", *ESPN_TEAM_SUMMARY_PATHS})


class LiveStateCaptureRequest(BaseModel):
    sport: str = Field(min_length=2, max_length=32)
    league: str = Field(min_length=2, max_length=64)
    official_event_id: str = Field(min_length=1, max_length=128)
    home_team: str = Field(min_length=1, max_length=160)
    away_team: str = Field(min_length=1, max_length=160)


class LiveStateCaptureError(RuntimeError):
    def __init__(
        self,
        code: str,
        message: str,
        *,
        http_status: int = 422,
        detail: Optional[dict[str, Any]] = None,
    ):
        super().__init__(message)
        self.code = code
        self.http_status = http_status
        self.detail = detail or {}


def _team_key(value: Any) -> str:
    text = unicodedata.normalize("NFKD", str(value or ""))
    text = "".join(ch for ch in text if not unicodedata.combining(ch))
    text = re.sub(r"[^A-Za-z0-9]+", " ", text).strip().casefold()
    return " ".join(text.split())


def _now_utc(now: Optional[datetime]) -> datetime:
    value = now or datetime.now(timezone.utc)
    if value.utcoffset() is None:
        value = value.replace(tzinfo=timezone.utc)
    return value.astimezone(timezone.utc)


def _request_json(
    url: str,
    *,
    http_get: Callable[..., Any],
    params: Optional[dict[str, Any]] = None,
) -> dict[str, Any]:
    errors: list[str] = []
    for attempt in range(1, HTTP_ATTEMPTS + 1):
        try:
            response = http_get(
                url,
                params=params or {},
                headers={"User-Agent": "WOW-Live-State/1.0", "Accept": "application/json"},
                timeout=HTTP_TIMEOUT_SECONDS,
                follow_redirects=True,
            )
            status = int(getattr(response, "status_code", 200))
            if status >= 400:
                raise RuntimeError(f"HTTP_{status}")
            payload = response.json()
            if not isinstance(payload, Mapping):
                raise TypeError("JSON_NOT_OBJECT")
            return dict(payload)
        except Exception as exc:  # noqa: BLE001
            errors.append(f"{type(exc).__name__}:{exc}")
            if attempt < HTTP_ATTEMPTS:
                time.sleep(0.05)
    raise LiveStateCaptureError(
        "LIVE_STATE_PROVIDER_UNAVAILABLE",
        "official live-state provider could not be retrieved",
        http_status=503,
        detail={"source_uri": url, "errors": errors[-4:]},
    )


def _require_identity(
    req: LiveStateCaptureRequest,
    *,
    provider_home: str,
    provider_away: str,
) -> None:
    if _team_key(provider_home) != _team_key(req.home_team) or _team_key(provider_away) != _team_key(req.away_team):
        raise LiveStateCaptureError(
            "LIVE_EVENT_IDENTITY_CONFLICT",
            "provider event teams did not match the requested home/away identity",
            detail={
                "requested_home_team": req.home_team,
                "requested_away_team": req.away_team,
                "provider_home_team": provider_home,
                "provider_away_team": provider_away,
            },
        )


def _require_event_id(req: LiveStateCaptureRequest, provider_event_id: Any, *, provider: str) -> None:
    provider_id = str(provider_event_id or "").strip()
    requested_id = str(req.official_event_id).strip()
    if not provider_id:
        raise LiveStateCaptureError(
            "LIVE_STATE_PROVIDER_PAYLOAD_INVALID",
            f"{provider} response lacked an official event identifier",
            http_status=502,
        )
    if provider_id != requested_id:
        raise LiveStateCaptureError(
            "LIVE_EVENT_IDENTITY_CONFLICT",
            "provider event identifier did not match the requested official event",
            detail={
                "requested_official_event_id": requested_id,
                "provider_official_event_id": provider_id,
                "provider": provider,
            },
        )


def _mlb_state(req: LiveStateCaptureRequest, payload: Mapping[str, Any]) -> tuple[dict[str, Any], str, str]:
    _require_event_id(req, payload.get("gamePk"), provider="MLB_STATS_API_OFFICIAL_GAME_FEED")
    game_data = payload.get("gameData") if isinstance(payload.get("gameData"), Mapping) else {}
    status = game_data.get("status") if isinstance(game_data.get("status"), Mapping) else {}
    abstract = str(status.get("abstractGameState") or "")
    detailed = str(status.get("detailedState") or "")
    if abstract.casefold() != "live" or any(token in detailed.casefold() for token in ("delay", "suspend", "postpon")):
        raise LiveStateCaptureError(
            "LIVE_EVENT_NOT_IN_PROGRESS",
            "official MLB feed does not show active in-progress gameplay",
            http_status=409,
            detail={"abstract_state": abstract, "detailed_state": detailed},
        )

    teams = game_data.get("teams") if isinstance(game_data.get("teams"), Mapping) else {}
    home = teams.get("home") if isinstance(teams.get("home"), Mapping) else {}
    away = teams.get("away") if isinstance(teams.get("away"), Mapping) else {}
    provider_home = str(home.get("name") or "")
    provider_away = str(away.get("name") or "")
    _require_identity(req, provider_home=provider_home, provider_away=provider_away)

    live_data = payload.get("liveData") if isinstance(payload.get("liveData"), Mapping) else {}
    linescore = live_data.get("linescore") if isinstance(live_data.get("linescore"), Mapping) else {}
    line_teams = linescore.get("teams") if isinstance(linescore.get("teams"), Mapping) else {}
    home_line = line_teams.get("home") if isinstance(line_teams.get("home"), Mapping) else {}
    away_line = line_teams.get("away") if isinstance(line_teams.get("away"), Mapping) else {}
    offense = linescore.get("offense") if isinstance(linescore.get("offense"), Mapping) else {}
    defense = linescore.get("defense") if isinstance(linescore.get("defense"), Mapping) else {}
    current_play = (live_data.get("plays") or {}).get("currentPlay") if isinstance(live_data.get("plays"), Mapping) else None

    try:
        inning = int(linescore.get("currentInning"))
        outs = int(linescore.get("outs"))
        home_score = int(home_line.get("runs") or 0)
        away_score = int(away_line.get("runs") or 0)
    except (TypeError, ValueError) as exc:
        raise LiveStateCaptureError(
            "LIVE_STATE_PROVIDER_PAYLOAD_INVALID",
            "official MLB live state lacked numeric inning/outs/score",
            http_status=502,
        ) from exc

    half_raw = str(linescore.get("inningHalf") or "").upper()
    if half_raw.startswith("TOP"):
        half = "TOP"
    elif half_raw.startswith("BOT"):
        half = "BOTTOM"
    else:
        raise LiveStateCaptureError(
            "LIVE_STATE_PROVIDER_PAYLOAD_INVALID",
            "official MLB live state lacked a valid inning half",
            http_status=502,
            detail={"inning_half": half_raw},
        )

    normalized = {
        "state_schema_version": "MLB_LIVE_RAW_STATE_V1",
        "official_event_id": req.official_event_id,
        "home_team": provider_home,
        "away_team": provider_away,
        "home_team_id": home.get("id"),
        "away_team_id": away.get("id"),
        "home_score": home_score,
        "away_score": away_score,
        "inning": inning,
        "half": half,
        "outs": outs,
        "base_occupancy": {
            "first": bool(offense.get("first")),
            "second": bool(offense.get("second")),
            "third": bool(offense.get("third")),
        },
        "offense": {
            "team_id": (offense.get("team") or {}).get("id") if isinstance(offense.get("team"), Mapping) else None,
            "batter_id": (offense.get("batter") or {}).get("id") if isinstance(offense.get("batter"), Mapping) else None,
        },
        "defense": {
            "team_id": (defense.get("team") or {}).get("id") if isinstance(defense.get("team"), Mapping) else None,
            "pitcher_id": (defense.get("pitcher") or {}).get("id") if isinstance(defense.get("pitcher"), Mapping) else None,
        },
        "official_status": {"abstract": abstract, "detailed": detailed},
        "current_play": current_play if isinstance(current_play, Mapping) else None,
    }
    return normalized, provider_home, provider_away


def _espn_team_state(req: LiveStateCaptureRequest, payload: Mapping[str, Any], *, sport: str) -> tuple[dict[str, Any], str, str]:
    header = payload.get("header") if isinstance(payload.get("header"), Mapping) else {}
    competitions = header.get("competitions")
    competition = competitions[0] if isinstance(competitions, list) and competitions and isinstance(competitions[0], Mapping) else {}
    _require_event_id(req, competition.get("id"), provider=f"ESPN_{sport}_OFFICIAL_EVENT_SUMMARY")
    status = competition.get("status") if isinstance(competition.get("status"), Mapping) else {}
    status_type = status.get("type") if isinstance(status.get("type"), Mapping) else {}
    state = str(status_type.get("state") or "").casefold()
    completed = bool(status_type.get("completed"))
    if state != "in" or completed:
        raise LiveStateCaptureError(
            "LIVE_EVENT_NOT_IN_PROGRESS",
            f"official ESPN {sport} summary does not show active in-progress gameplay",
            http_status=409,
            detail={"provider_state": state, "completed": completed},
        )

    competitors = competition.get("competitors")
    if not isinstance(competitors, list):
        raise LiveStateCaptureError(
            "LIVE_STATE_PROVIDER_PAYLOAD_INVALID",
            f"ESPN {sport} summary lacked competitors",
            http_status=502,
        )
    home_row: Mapping[str, Any] = {}
    away_row: Mapping[str, Any] = {}
    for competitor in competitors:
        if not isinstance(competitor, Mapping):
            continue
        if str(competitor.get("homeAway") or "").casefold() == "home":
            home_row = competitor
        elif str(competitor.get("homeAway") or "").casefold() == "away":
            away_row = competitor

    def _team_name(row: Mapping[str, Any]) -> str:
        team = row.get("team") if isinstance(row.get("team"), Mapping) else {}
        return str(team.get("displayName") or team.get("name") or "")

    provider_home = _team_name(home_row)
    provider_away = _team_name(away_row)
    _require_identity(req, provider_home=provider_home, provider_away=provider_away)

    try:
        home_score = int(float(home_row.get("score") or 0))
        away_score = int(float(away_row.get("score") or 0))
        period = int(status.get("period") or 0)
    except (TypeError, ValueError) as exc:
        raise LiveStateCaptureError(
            "LIVE_STATE_PROVIDER_PAYLOAD_INVALID",
            f"ESPN {sport} summary lacked numeric score/period",
            http_status=502,
        ) from exc

    situation = competition.get("situation") if isinstance(competition.get("situation"), Mapping) else {}
    drives = payload.get("drives") if isinstance(payload.get("drives"), Mapping) else {}
    current_drive = drives.get("current") if isinstance(drives.get("current"), Mapping) else {}
    normalized = {
        "state_schema_version": f"{sport}_LIVE_RAW_STATE_V1",
        "official_event_id": req.official_event_id,
        "home_team": provider_home,
        "away_team": provider_away,
        "home_team_id": (home_row.get("team") or {}).get("id") if isinstance(home_row.get("team"), Mapping) else None,
        "away_team_id": (away_row.get("team") or {}).get("id") if isinstance(away_row.get("team"), Mapping) else None,
        "home_score": home_score,
        "away_score": away_score,
        "period": period,
        "display_clock": str(status.get("displayClock") or ""),
        "situation": dict(situation),
        "current_drive": dict(current_drive),
        "official_status": {
            "state": state,
            "name": status_type.get("name"),
            "detail": status_type.get("detail"),
        },
    }
    return normalized, provider_home, provider_away


def capture_live_event_state(
    req: LiveStateCaptureRequest,
    db: Any,
    *,
    http_get: Callable[..., Any] = httpx.get,
    now: Optional[datetime] = None,
) -> dict[str, Any]:
    sport = normalize_team_event_sport(req.sport)
    captured = _now_utc(now)
    if sport == "MLB":
        uri = MLB_FEED.format(event_id=req.official_event_id)
        payload = _request_json(uri, http_get=http_get)
        normalized, provider_home, provider_away = _mlb_state(req, payload)
        provider = "MLB_STATS_API_OFFICIAL_GAME_FEED"
    elif sport in ESPN_TEAM_SUMMARY_PATHS:
        uri = ESPN_TEAM_SUMMARY_PATHS[sport]
        payload = _request_json(uri, params={"event": req.official_event_id}, http_get=http_get)
        normalized, provider_home, provider_away = _espn_team_state(req, payload, sport=sport)
        provider = f"ESPN_{sport}_OFFICIAL_EVENT_SUMMARY"
    elif sport in EXPECTED_TEAM_EVENT_SPORTS:
        raise LiveStateCaptureError(
            f"LIVE_STATE_CAPTURE_PROVIDER_NOT_WIRED:{sport}",
            f"server-owned live-state capture provider is not yet wired for {sport}",
            http_status=409,
        )
    else:
        raise LiveStateCaptureError(
            "LIVE_STATE_CAPTURE_UNKNOWN_SPORT",
            f"{sport or 'UNKNOWN'} is not in the V17 team/event sport manifest",
            http_status=422,
        )

    state_json = json.loads(json.dumps(normalized, sort_keys=True, separators=(",", ":"), default=str))
    state_hash = hashlib.sha256(
        json.dumps(state_json, sort_keys=True, separators=(",", ":")).encode("utf-8")
    ).hexdigest()
    schema = str(state_json["state_schema_version"])
    extractor_checksum = hashlib.sha256(f"{RAW_FEATURE_FAMILY}:{schema}".encode("utf-8")).hexdigest()
    snapshot_id = str(uuid.uuid4())
    row = {
        "source_snapshot_id": snapshot_id,
        "official_event_id": req.official_event_id,
        "sport": sport,
        "league": req.league,
        "event_status": "IN_PROGRESS",
        "home_team": provider_home,
        "away_team": provider_away,
        "snapshot_timestamp": captured.isoformat(),
        "latest_material_update_at": captured.isoformat(),
        "source_provider": provider,
        "source_uri": uri,
        "source_retrieved_at": captured.isoformat(),
        "state_schema_version": schema,
        "state_hash": state_hash,
        "state_json": state_json,
        "feature_model_family": RAW_FEATURE_FAMILY,
        "feature_model_artifact_version": RAW_FEATURE_ARTIFACT_VERSION,
        "feature_schema_version": schema,
        "feature_artifact_checksum": extractor_checksum,
        "can_execute": False,
    }
    try:
        db.table("wow_live_state_snapshots").insert(row).execute()
    except Exception as exc:  # noqa: BLE001
        raise LiveStateCaptureError(
            "LIVE_STATE_SNAPSHOT_PERSISTENCE_FAILED",
            "server-owned live-state snapshot could not be persisted",
            http_status=503,
            detail={"error_type": type(exc).__name__},
        ) from exc

    return {
        "ok": True,
        "status": "CAPTURED_RAW_LIVE_STATE",
        "sport": sport,
        "league": req.league,
        "official_event_id": req.official_event_id,
        "event_status": "IN_PROGRESS",
        "home_team": provider_home,
        "away_team": provider_away,
        "raw_source_snapshot_id": snapshot_id,
        "raw_state_schema_version": schema,
        "raw_state_hash": state_hash,
        "source_provider": provider,
        "source_retrieved_at": captured.isoformat(),
        "model_ready": False,
        "model_ready_source_snapshot_id": None,
        "next_stage": "CERTIFIED_LIVE_FEATURE_MATERIALIZATION_REQUIRED",
        "probability_publishable": False,
        "rank_eligible": False,
        "can_execute": False,
    }


def install_live_state_acquisition_routes(app: Any, *, auth_dependency: Any, db_client_fn: Callable[[], Any]) -> None:
    if any(
        getattr(route, "path", None) == "/capture-live-event-state"
        and "POST" in (getattr(route, "methods", set()) or set())
        for route in app.router.routes
    ):
        return

    @app.post(
        "/capture-live-event-state",
        dependencies=[auth_dependency],
        operation_id="captureWowLiveEventState",
    )
    def post_capture_live_event_state(req: LiveStateCaptureRequest):
        try:
            return capture_live_event_state(req, db_client_fn())
        except LiveStateCaptureError as exc:
            raise HTTPException(
                status_code=exc.http_status,
                detail={
                    "code": exc.code,
                    "message": str(exc),
                    **exc.detail,
                    "probability_publishable": False,
                    "rank_eligible": False,
                    "can_execute": False,
                },
            ) from exc


__all__ = [
    "CAN_EXECUTE",
    "ESPN_TEAM_SUMMARY_PATHS",
    "LIVE_STATE_CAPTURE_SPORTS",
    "LiveStateCaptureError",
    "LiveStateCaptureRequest",
    "capture_live_event_state",
    "install_live_state_acquisition_routes",
]
