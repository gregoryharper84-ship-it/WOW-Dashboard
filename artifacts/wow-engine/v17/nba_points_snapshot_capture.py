"""Server-owned NBA POINTS pregame evidence acquisition.

Only persisted autonomous-discovery request rows are eligible. Market identity
comes from the stored row; current roster/event identity comes from ESPN + NBA
CDN; L10 comes from NBA Stats and final NBA CDN boxscores. No caller-supplied
source callback, evidence payload, probability, or execution authority exists.
"""
from __future__ import annotations

import hashlib
import json
import re
import urllib.error
import urllib.request
from contextvars import ContextVar
from dataclasses import dataclass
from datetime import date, datetime, time, timedelta, timezone
from typing import Any, Mapping
from urllib.parse import urlencode, urlsplit, urlunsplit
from zoneinfo import ZoneInfo

from pydantic import BaseModel

from v17.nba_event_identity import resolve_nba_current_event_identity
from v17.nba_points_official_history import acquire_nba_points_l10
from v17.nba_points_pregame_roster import verify_nba_points_current_roster
from v17.nba_points_snapshot_bridge import build_nba_points_snapshot
from v17.nba_points_snapshot_persistence import (
    NBAPointsSnapshotPersistenceError,
    persist_validated_nba_points_snapshot,
    seal_source_provenance,
)

NBA_CDN_BOX_SCORE = "https://cdn.nba.com/static/json/liveData/boxscore/boxscore_{game_id}.json"
ESPN_SCOREBOARD = "https://site.api.espn.com/apis/site/v2/sports/basketball/nba/scoreboard"
ESPN_SUMMARY = "https://site.api.espn.com/apis/site/v2/sports/basketball/nba/summary"
NBA_ZONE = ZoneInfo("America/New_York")
_ALLOWED_PATHS = {
    "cdn.nba.com": re.compile(r"^/static/json/liveData/boxscore/boxscore_[0-9]{10}\.json$"),
    "site.api.espn.com": {
        "/apis/site/v2/sports/basketball/nba/scoreboard",
        "/apis/site/v2/sports/basketball/nba/summary",
    },
    "stats.nba.com": {"/stats/playergamelog"},
}
_RECORDER: ContextVar[list[dict[str, Any]] | None] = ContextVar("nba_points_http_recorder", default=None)
_CURRENT_GAME_ID: ContextVar[str | None] = ContextVar("nba_points_current_game_id", default=None)


class NBAPointsSnapshotCaptureRequest(BaseModel):
    run_id: str
    row_key: str


class NBAPointsSnapshotCaptureError(RuntimeError):
    def __init__(self, code: str, detail: str = "") -> None:
        self.code = str(code)
        super().__init__(detail or self.code)


@dataclass(frozen=True)
class _HTTPResponse:
    status_code: int
    content: bytes
    url: str
    captured_at: str

    def json(self) -> Any:
        return json.loads(self.content.decode("utf-8"))


class _NoRedirect(urllib.request.HTTPRedirectHandler):
    def redirect_request(self, req: Any, fp: Any, code: int, msg: str, headers: Any, newurl: str) -> None:
        return None


def _utcnow() -> datetime:
    return datetime.now(timezone.utc)


def _parse_aware(value: Any, code: str = "NBA_POINTS_SOURCE_TIMESTAMP_INVALID") -> datetime:
    if isinstance(value, datetime):
        parsed = value
    elif isinstance(value, str) and value.strip():
        parsed = datetime.fromisoformat(value.replace("Z", "+00:00"))
    else:
        raise NBAPointsSnapshotCaptureError(code, "A timezone-aware source timestamp is required.")
    if parsed.utcoffset() is None:
        raise NBAPointsSnapshotCaptureError(code, "A timezone-aware source timestamp is required.")
    return parsed.astimezone(timezone.utc)


def _source_name(url: str) -> str:
    parsed = urlsplit(url)
    if parsed.hostname == "site.api.espn.com" and parsed.path.endswith("/scoreboard"):
        return "ESPN_SCOREBOARD"
    if parsed.hostname == "site.api.espn.com" and parsed.path.endswith("/summary"):
        return "ESPN_GAME_SUMMARY"
    if parsed.hostname == "stats.nba.com" and parsed.path == "/stats/playergamelog":
        return "NBA_STATS_PLAYERGAMELOG"
    if parsed.hostname == "cdn.nba.com" and "/boxscore_" in parsed.path:
        match = re.search(r"boxscore_([0-9]{10})\.json$", parsed.path)
        if match and match.group(1) == _CURRENT_GAME_ID.get():
            return "NBA_CDN_CURRENT_BOXSCORE"
        return "NBA_CDN_SETTLED_BOXSCORE"
    return "UNCLASSIFIED"


def _validate_http_target(url: str) -> None:
    parsed = urlsplit(url)
    allowed = _ALLOWED_PATHS.get(parsed.hostname or "")
    valid = (
        parsed.scheme == "https"
        and (
            (parsed.hostname == "cdn.nba.com" and isinstance(allowed, re.Pattern) and bool(allowed.fullmatch(parsed.path)))
            or (isinstance(allowed, set) and parsed.path in allowed)
        )
    )
    if not valid:
        raise NBAPointsSnapshotCaptureError(
            "NBA_POINTS_SOURCE_ORIGIN_NOT_ALLOWED",
            "The source URL is outside the fixed ESPN/NBA HTTPS allowlist.",
        )


def _http_get(
    url: str,
    *,
    params: Mapping[str, Any] | None = None,
    headers: Mapping[str, str] | None = None,
    timeout: float = 12.0,
) -> _HTTPResponse:
    parsed = urlsplit(url)
    query = parsed.query
    if params:
        extra = urlencode(dict(params), doseq=True)
        query = f"{query}&{extra}" if query else extra
    request_url = urlunsplit((parsed.scheme, parsed.netloc, parsed.path, query, ""))
    _validate_http_target(request_url)
    request = urllib.request.Request(request_url, headers=dict(headers or {}), method="GET")
    opener = urllib.request.build_opener(_NoRedirect())
    try:
        with opener.open(request, timeout=float(timeout)) as response:
            status = int(getattr(response, "status", 0) or 0)
            body = response.read(5 * 1024 * 1024 + 1)
            final_url = response.geturl()
    except urllib.error.HTTPError as exc:
        raise NBAPointsSnapshotCaptureError(
            "NBA_POINTS_SOURCE_HTTP_FAILURE",
            f"Fixed-origin source returned HTTP {exc.code}.",
        ) from exc
    except (urllib.error.URLError, TimeoutError, OSError) as exc:
        raise NBAPointsSnapshotCaptureError(
            "NBA_POINTS_SOURCE_HTTP_FAILURE",
            type(exc).__name__,
        ) from exc
    if final_url != request_url:
        raise NBAPointsSnapshotCaptureError("NBA_POINTS_SOURCE_REDIRECT_REJECTED")
    if status != 200:
        raise NBAPointsSnapshotCaptureError("NBA_POINTS_SOURCE_HTTP_FAILURE", f"HTTP {status}")
    if len(body) > 5 * 1024 * 1024:
        raise NBAPointsSnapshotCaptureError("NBA_POINTS_SOURCE_RESPONSE_TOO_LARGE")
    captured_at = _utcnow().isoformat()
    response = _HTTPResponse(status, body, final_url, captured_at)
    recorder = _RECORDER.get()
    if recorder is not None:
        recorder.append(
            {
                "source": _source_name(final_url),
                "url": final_url,
                "status_code": status,
                "sha256": hashlib.sha256(body).hexdigest(),
                "captured_at": captured_at,
            }
        )
    return response


def _event_start_from_competition(event: Mapping[str, Any]) -> datetime | None:
    competitions = event.get("competitions")
    competition = competitions[0] if isinstance(competitions, list) and competitions else {}
    value = competition.get("date") or event.get("date")
    try:
        return _parse_aware(value)
    except Exception:
        return None


def _resolve_current_event(nba_game_id: str, event_start: datetime) -> dict[str, Any]:
    current_url = NBA_CDN_BOX_SCORE.format(game_id=nba_game_id)
    nba_response = _http_get(current_url)
    try:
        nba_payload = nba_response.json()
    except Exception as exc:
        raise NBAPointsSnapshotCaptureError("NBA_POINTS_CURRENT_ROSTER_UNVERIFIED", "NBA CDN boxscore was not valid JSON.") from exc
    game = nba_payload.get("game") if isinstance(nba_payload, Mapping) else None
    if not isinstance(game, Mapping):
        raise NBAPointsSnapshotCaptureError("NBA_POINTS_CURRENT_ROSTER_UNVERIFIED")
    if str(game.get("gameId") or "") != nba_game_id or int(game.get("gameStatus") or 0) != 1:
        raise NBAPointsSnapshotCaptureError("NBA_POINTS_CURRENT_ROSTER_UNVERIFIED")
    try:
        observed_start = _parse_aware(game.get("gameTimeUTC"))
    except Exception as exc:
        raise NBAPointsSnapshotCaptureError("NBA_POINTS_CANONICAL_EVENT_UNRESOLVED") from exc
    if observed_start != event_start:
        raise NBAPointsSnapshotCaptureError("NBA_POINTS_CANONICAL_EVENT_UNRESOLVED")

    home = game.get("homeTeam") if isinstance(game.get("homeTeam"), Mapping) else {}
    away = game.get("awayTeam") if isinstance(game.get("awayTeam"), Mapping) else {}
    home_tricode = str(home.get("teamTricode") or "").upper()
    away_tricode = str(away.get("teamTricode") or "").upper()
    if not home_tricode or not away_tricode or home_tricode == away_tricode:
        raise NBAPointsSnapshotCaptureError("NBA_POINTS_CANONICAL_EVENT_UNRESOLVED")

    scoreboard_date = event_start.astimezone(NBA_ZONE).strftime("%Y%m%d")
    scoreboard_response = _http_get(ESPN_SCOREBOARD, params={"dates": scoreboard_date})
    try:
        scoreboard = scoreboard_response.json()
    except Exception as exc:
        raise NBAPointsSnapshotCaptureError("NBA_POINTS_CANONICAL_EVENT_UNRESOLVED") from exc

    matches: list[dict[str, Any]] = []
    for event in scoreboard.get("events", []) if isinstance(scoreboard, Mapping) else []:
        if not isinstance(event, Mapping):
            continue
        competitions = event.get("competitions")
        if not isinstance(competitions, list) or not competitions:
            continue
        competitors = competitions[0].get("competitors", [])
        if not isinstance(competitors, list):
            continue
        home_competitor = next(
            (item for item in competitors if item.get("homeAway") == "home"), None
        )
        away_competitor = next(
            (item for item in competitors if item.get("homeAway") == "away"), None
        )
        if not isinstance(home_competitor, Mapping) or not isinstance(away_competitor, Mapping):
            continue
        home_team = home_competitor.get("team") if isinstance(home_competitor.get("team"), Mapping) else {}
        away_team = away_competitor.get("team") if isinstance(away_competitor.get("team"), Mapping) else {}
        if (
            str(home_team.get("abbreviation") or "").upper() != home_tricode
            or str(away_team.get("abbreviation") or "").upper() != away_tricode
        ):
            continue
        espn_start = _event_start_from_competition(event)
        if espn_start is None or abs((espn_start - event_start).total_seconds()) > 60:
            continue
        matches.append(
            {
                "event_id": str(event.get("id") or ""),
                "home_team_id": str(home_team.get("id") or ""),
                "away_team_id": str(away_team.get("id") or ""),
                "home_tricode": home_tricode,
                "away_tricode": away_tricode,
            }
        )
    if len(matches) != 1 or not matches[0]["event_id"] or not matches[0]["home_team_id"] or not matches[0]["away_team_id"]:
        raise NBAPointsSnapshotCaptureError("NBA_POINTS_CANONICAL_EVENT_UNRESOLVED")

    match = matches[0]
    summary_response = _http_get(ESPN_SUMMARY, params={"event": match["event_id"]})
    try:
        espn_summary = summary_response.json()
    except Exception as exc:
        raise NBAPointsSnapshotCaptureError("NBA_POINTS_CURRENT_ROSTER_UNVERIFIED") from exc
    return {
        "nba_game_id": nba_game_id,
        "nba_boxscore": nba_payload,
        "espn_event_id": match["event_id"],
        "espn_summary": espn_summary,
        "home_team_alias": f"espn-{match['home_team_id']}",
        "away_team_alias": f"espn-{match['away_team_id']}",
        "home_team_id": f"espn-{match['home_team_id']}",
        "away_team_id": f"espn-{match['away_team_id']}",
        "home_tricode": home_tricode,
        "away_tricode": away_tricode,
        "nba_captured_at": _parse_aware(nba_response.captured_at),
        "scoreboard_captured_at": _parse_aware(scoreboard_response.captured_at),
        "espn_captured_at": _parse_aware(summary_response.captured_at),
    }


def _nba_seasons(event_start: datetime) -> tuple[str, str]:
    year = event_start.astimezone(NBA_ZONE).year
    if event_start.astimezone(NBA_ZONE).month < 7:
        year -= 1
    return (f"{year}-{(year + 1) % 100:02d}", f"{year - 1}-{year % 100:02d}")


def _response_time(responses: list[dict[str, Any]], source: str, *, latest: bool = True) -> str:
    times = [
        _parse_aware(item["captured_at"])
        for item in responses
        if item.get("source") == source and item.get("captured_at")
    ]
    if not times:
        raise NBAPointsSnapshotCaptureError("NBA_POINTS_SOURCE_IDENTITY_INVALID", f"Missing source response: {source}")
    selected = max(times) if latest else min(times)
    return selected.isoformat()


def _load_request_row(db: Any, run_id: str, row_key: str) -> dict[str, Any]:
    try:
        result = (
            db.table("wow_pick_request_row_states")
            .select("*")
            .eq("run_id", run_id)
            .eq("row_key", row_key)
            .limit(2)
            .execute()
        )
    except Exception as exc:
        raise NBAPointsSnapshotCaptureError("NBA_POINTS_SNAPSHOT_REQUEST_ROW_NOT_FOUND", type(exc).__name__) from exc
    rows = getattr(result, "data", None) or []
    if not isinstance(rows, list) or len(rows) != 1 or not isinstance(rows[0], Mapping):
        raise NBAPointsSnapshotCaptureError("NBA_POINTS_SNAPSHOT_REQUEST_ROW_NOT_FOUND")
    return dict(rows[0])


def _validate_request_row(row: Mapping[str, Any], run_id: str, row_key: str) -> dict[str, Any]:
    payload = row.get("request_payload")
    if not isinstance(payload, Mapping):
        raise NBAPointsSnapshotCaptureError("NBA_POINTS_SNAPSHOT_SOURCE_ROW_INCOMPLETE")
    evidence = payload.get("evidence")
    if not isinstance(evidence, Mapping):
        raise NBAPointsSnapshotCaptureError("NBA_POINTS_SNAPSHOT_SOURCE_ROW_INCOMPLETE")
    if (
        str(row.get("sport") or "").upper() != "NBA"
        or str(row.get("stat_type") or "").upper() != "POINTS"
        or str(row.get("source_type") or "").upper() != "AUTONOMOUS_DISCOVERY"
        or str(row.get("platform") or "").upper() != "DRAFTKINGS"
    ):
        raise NBAPointsSnapshotCaptureError("NBA_POINTS_SNAPSHOT_SOURCE_ROW_INCOMPLETE")

    nba_game_id = str(row.get("event_id") or "")
    if not re.fullmatch(r"[0-9]{10}", nba_game_id):
        raise NBAPointsSnapshotCaptureError("NBA_POINTS_CANONICAL_EVENT_UNRESOLVED")
    player = str(row.get("player") or "").strip()
    direction = str(row.get("direction") or "").upper()
    line = row.get("exact_line")
    if not player or direction not in {"MORE", "LESS", "OVER", "UNDER"}:
        raise NBAPointsSnapshotCaptureError("NBA_POINTS_SNAPSHOT_SOURCE_ROW_INCOMPLETE")
    try:
        line = float(line)
    except (TypeError, ValueError):
        raise NBAPointsSnapshotCaptureError("NBA_POINTS_SNAPSHOT_SOURCE_ROW_INCOMPLETE")
    if line <= 0:
        raise NBAPointsSnapshotCaptureError("NBA_POINTS_SNAPSHOT_SOURCE_ROW_INCOMPLETE")
    direction = {"OVER": "MORE", "UNDER": "LESS"}.get(direction, direction)

    event_start = _parse_aware(row.get("event_start_time"))
    market_capture = _parse_aware(payload.get("source_capture_timestamp"))
    role_timestamp = _parse_aware(evidence.get("role_timestamp"))
    role = evidence.get("role_status")
    provenance = str(evidence.get("rate_provenance") or "")
    if not isinstance(role, Mapping) or not provenance:
        raise NBAPointsSnapshotCaptureError("NBA_POINTS_SNAPSHOT_SOURCE_ROW_INCOMPLETE")
    # The period is encoded twice by the server discovery row: the stable row key
    # and the captured DraftKings market description. No caller-supplied period.
    if "NBA-FULL-GAME-POINTS" not in row_key.upper() or "draftkings full-game points line" not in provenance.casefold():
        raise NBAPointsSnapshotCaptureError("NBA_POINTS_PERIOD_UNSUPPORTED")
    if market_capture >= event_start or role_timestamp >= event_start:
        raise NBAPointsSnapshotCaptureError("NBA_POINTS_SOURCE_TIMESTAMP_INVALID")
    if market_capture > _utcnow():
        raise NBAPointsSnapshotCaptureError("NBA_POINTS_SOURCE_TIMESTAMP_INVALID")
    if str(payload.get("event_id") or nba_game_id) != nba_game_id:
        raise NBAPointsSnapshotCaptureError("NBA_POINTS_CANONICAL_EVENT_UNRESOLVED")
    return {
        "run_id": run_id,
        "row_key": row_key,
        "nba_game_id": nba_game_id,
        "player": player,
        "direction": direction,
        "line": line,
        "event_start_time": event_start,
        "market_capture_at": market_capture,
        "role_timestamp": role_timestamp,
        "injury_role": dict(role),
        "rate_provenance": provenance,
        "evidence_version": str(evidence.get("evidence_version") or "PROP_EVIDENCE_V1"),
    }


def capture_nba_points_snapshot(*, db: Any, run_id: str, row_key: str) -> dict[str, Any]:
    """Capture one row using only persisted request data and fixed official sources."""
    from v17.nba_points_snapshot_persistence import _require_service_role_client

    _require_service_role_client(db)
    if not isinstance(run_id, str) or not run_id.strip() or not isinstance(row_key, str) or not row_key.strip():
        return {
            "status": "HELD",
            "blockers": [{"code": "NBA_POINTS_SNAPSHOT_SOURCE_ROW_INCOMPLETE"}],
            "can_execute": False,
        }

    recorder: list[dict[str, Any]] = []
    recorder_token = _RECORDER.set(recorder)
    try:
        row = _load_request_row(db, run_id, row_key)
        request = _validate_request_row(row, run_id, row_key)
        if request["event_start_time"] <= _utcnow():
            raise NBAPointsSnapshotCaptureError("NBA_POINTS_EVENT_ALREADY_STARTED")
        current_game_token = _CURRENT_GAME_ID.set(request["nba_game_id"])
        try:
            event = _resolve_current_event(request["nba_game_id"], request["event_start_time"])
            roster_receipt = verify_nba_points_current_roster(
                espn_summary=event["espn_summary"],
                nba_boxscore=event["nba_boxscore"],
                espn_event_id=event["espn_event_id"],
                nba_game_id=request["nba_game_id"],
                player_name=request["player"],
                now=_utcnow(),
                espn_captured_at=event["espn_captured_at"],
                nba_captured_at=event["nba_captured_at"],
            )
            if roster_receipt.get("status") != "NBA_CURRENT_ROSTER_ACTIVE_SOURCE_VERIFIED_ROLE_NOT_PROVEN":
                raise NBAPointsSnapshotCaptureError(
                    str(roster_receipt.get("code") or "NBA_POINTS_CURRENT_ROSTER_UNVERIFIED")
                )
            roster = dict(roster_receipt.get("roster") or {})
            roster_identity = dict(roster_receipt.get("source_identity") or {})
            if (
                not roster
                or str(roster.get("event_id") or "") != f"espn-{event['espn_event_id']}"
                or str(roster.get("availability") or "").upper() != "ACTIVE"
            ):
                raise NBAPointsSnapshotCaptureError("NBA_POINTS_CURRENT_ROSTER_UNVERIFIED")
            roster_team = str(roster.get("team_tricode") or roster_identity.get("team_tricode") or "").upper()
            if request["injury_role"].get("team") and str(request["injury_role"].get("team")).upper() != roster_team:
                raise NBAPointsSnapshotCaptureError("NBA_POINTS_ROSTER_PLAYER_MISMATCH")

            history = acquire_nba_points_l10(
                player_id=str(roster.get("player_id") or ""),
                event_start_time=request["event_start_time"].isoformat(),
                seasons=_nba_seasons(request["event_start_time"]),
                now=_utcnow(),
                http_get=_http_get,
            )
            game_log = list(history.get("game_log") or [])
            box_score_log = list(history.get("box_score_log") or [])
            if len(game_log) < 10 or len(box_score_log) < 10:
                raise NBAPointsSnapshotCaptureError("NBA_POINTS_HISTORY_L10_INSUFFICIENT")
            captured_at = _utcnow()
            provenance = seal_source_provenance(recorder, captured_at=captured_at.isoformat())

            official_identity = {
                **roster_identity,
                "nba_game_id": request["nba_game_id"],
                "nba_person_id": str(roster.get("player_id") or ""),
                "espn_event_id": f"espn-{event['espn_event_id']}",
                "canonical_event_id": str(roster.get("event_id") or ""),
                "player": request["player"],
                "team_id": str(roster.get("team_id") or ""),
                "team_tricode": roster_team,
                "opponent_id": (
                    event["away_team_id"] if str(roster.get("team_id")) == event["home_team_id"] else event["home_team_id"]
                ),
                "event_start_time": request["event_start_time"].isoformat(),
                "line": request["line"],
                "direction": request["direction"],
                "period": "FULL_GAME",
                "stat_type": "POINTS",
                "request_run_id": run_id,
                "request_row_key": row_key,
                "market_capture_at": request["market_capture_at"].isoformat(),
            }
            role_availability = str(request["injury_role"].get("availability") or "").upper()
            if role_availability not in {"ACTIVE", "AVAILABLE", "PROBABLE", "QUESTIONABLE"}:
                raise NBAPointsSnapshotCaptureError("NBA_POINTS_PLAYER_AVAILABILITY_HOLD")
            role_status = {
                "status": "ACTIVE",
                "role": str(roster.get("position") or "PLAYER"),
                "position": str(roster.get("position") or ""),
                "availability": role_availability,
                "starter_status": str(request["injury_role"].get("starter_status") or "UNCONFIRMED"),
                "reason": str(request["injury_role"].get("reason") or ""),
                "team": roster_team,
                "team_id": str(roster.get("team_id") or ""),
                "source": "NBA official injury report + ESPN game summary + NBA CDN scheduled roster",
                "official_source_identity": official_identity,
                "official_source_provenance": provenance,
            }
            source_timestamps = {
                "market_board_capture": request["market_capture_at"].isoformat(),
                "nba_official_injury_report": request["role_timestamp"].isoformat(),
                "nba_cdn_current_boxscore": event["nba_captured_at"].isoformat(),
                "espn_scoreboard": event["scoreboard_captured_at"].isoformat(),
                "espn_game_summary": event["espn_captured_at"].isoformat(),
                "nba_stats_playergamelog": _response_time(recorder, "NBA_STATS_PLAYERGAMELOG", latest=False),
                "nba_cdn_settled_boxscores_l10": _response_time(recorder, "NBA_CDN_SETTLED_BOXSCORE"),
            }
            evidence = {
                "captured_at": captured_at.isoformat(),
                "game_log": game_log,
                "box_score_log": box_score_log,
                "role_status": role_status,
                "role_timestamp": request["role_timestamp"].isoformat(),
                "opportunity_ledger": {
                    "status": "READY",
                    "scope": "NBA_POINTS_CANDIDATE_RESEARCH",
                    "stat_type": "POINTS",
                    "prior_games": len(game_log),
                    "l10_points_mean": round(sum(float(value) for value in game_log) / len(game_log), 6),
                    "stat_source_columns": ["PTS"],
                    "box_score_alignment": "PASS",
                    "current_roster_confirmation": "PASS",
                    "availability_gate": "PASS",
                    "live_usage_rate_consumed_by_candidate": False,
                },
                "source_timestamps": source_timestamps,
                "evidence_version": request["evidence_version"],
                "rate_provenance": (
                    f"{request['rate_provenance']}; official NBA Stats PlayerGameLog and "
                    "final NBA CDN boxscores provide the exact prior-game POINTS history; "
                    "current player roster verified against ESPN summary and NBA CDN."
                ),
            }
            opponent_id = official_identity["opponent_id"]
            target = {
                "home_team_alias": event["home_team_alias"],
                "away_team_alias": event["away_team_alias"],
                "team_id": str(roster.get("team_id") or ""),
                "opponent_id": opponent_id,
                "player_id": str(roster.get("player_id") or ""),
                "player": request["player"],
                "event_start_time": request["event_start_time"].isoformat(),
                "period": "FULL_GAME",
                "line": request["line"],
                "direction": request["direction"],
            }
            line_observation = {
                "provider": "DRAFTKINGS",
                "line_type": "EXACT_BOARD_LINE",
                "event_id": str(roster.get("event_id") or ""),
                "player_id": str(roster.get("player_id") or ""),
                "stat_type": "POINTS",
                "direction": request["direction"],
                "period": "FULL_GAME",
                "event_start_time": request["event_start_time"].isoformat(),
                "line": request["line"],
                "captured_at": request["market_capture_at"].isoformat(),
                "source": request["rate_provenance"],
            }
            receipt = build_nba_points_snapshot(
                target=target,
                roster=roster,
                line_observation=line_observation,
                evidence=evidence,
                now=captured_at,
                resolve_identity=resolve_nba_current_event_identity,
            )
            if not receipt.get("ok"):
                return {
                    "status": "HELD",
                    "blockers": receipt.get("blockers") or [{"code": "NBA_POINTS_SNAPSHOT_SOURCE_ROW_INCOMPLETE"}],
                    "source_identity": receipt.get("source_identity"),
                    "can_execute": False,
                    "probability_publishable": False,
                    "rank_eligible": False,
                }
            snapshot = receipt.get("snapshot")
            if not isinstance(snapshot, Mapping):
                raise NBAPointsSnapshotCaptureError("NBA_POINTS_SNAPSHOT_SOURCE_IDENTITY_INVALID")
            persisted = persist_validated_nba_points_snapshot(db=db, snapshot=snapshot)
            return {
                "status": persisted["status"],
                "source_snapshot_id": persisted["source_snapshot_id"],
                "readback_verified": persisted["readback_verified"],
                "source_identity": receipt.get("source_identity"),
                "source_provenance_digest": provenance["digest"],
                "probability_publishable": False,
                "rank_eligible": False,
                "can_execute": False,
            }
        finally:
            _CURRENT_GAME_ID.reset(current_game_token)
    except (NBAPointsSnapshotCaptureError, NBAPointsSnapshotPersistenceError) as exc:
        return {
            "status": "HELD",
            "blockers": [{"code": exc.code, "detail": str(exc)}],
            "write_may_have_committed": bool(getattr(exc, "write_may_have_committed", False)),
            "can_execute": False,
            "probability_publishable": False,
            "rank_eligible": False,
        }
    except Exception as exc:
        code = str(getattr(exc, "code", "") or "NBA_POINTS_SOURCE_ACQUISITION_FAILED")
        return {
            "status": "HELD",
            "blockers": [{"code": code, "detail": type(exc).__name__}],
            "can_execute": False,
            "probability_publishable": False,
            "rank_eligible": False,
        }
    finally:
        _RECORDER.reset(recorder_token)


def acquire_daily_nba_points_snapshots(
    *,
    db: Any,
    requested_date: str,
    timezone_name: str = "America/Chicago",
    max_candidates: int = 24,
) -> dict[str, Any]:
    """Capture eligible NBA POINTS request rows for one local event date."""
    from v17.nba_points_snapshot_persistence import _require_service_role_client

    _require_service_role_client(db)
    local_day = date.fromisoformat(requested_date)
    zone = ZoneInfo(timezone_name)
    start = datetime.combine(local_day, time.min, tzinfo=zone).astimezone(timezone.utc)
    end = (datetime.combine(local_day, time.min, tzinfo=zone) + timedelta(days=1)).astimezone(timezone.utc)
    now = _utcnow()
    try:
        result = (
            db.table("wow_pick_request_row_states")
            .select("run_id,row_key,event_start_time,sport,stat_type,source_type,platform,request_payload")
            .eq("sport", "NBA")
            .eq("stat_type", "POINTS")
            .gte("event_start_time", start.isoformat())
            .lt("event_start_time", end.isoformat())
            .gt("event_start_time", now.isoformat())
            .order("event_start_time")
            .limit(max(1, min(int(max_candidates), 100)))
            .execute()
        )
    except Exception as exc:
        return {
            "status": "FAILED",
            "attempted": 0,
            "persisted": 0,
            "held": 0,
            "blockers": [{"code": "NBA_POINTS_SNAPSHOT_REQUEST_ROW_NOT_FOUND", "detail": type(exc).__name__}],
        }
    rows = getattr(result, "data", None) or []
    if not isinstance(rows, list):
        rows = []
    attempted = persisted = held = 0
    blockers: list[dict[str, Any]] = []
    for row in rows:
        if not isinstance(row, Mapping):
            continue
        attempted += 1
        outcome = capture_nba_points_snapshot(
            db=db,
            run_id=str(row.get("run_id") or ""),
            row_key=str(row.get("row_key") or ""),
        )
        if outcome.get("status") in {"PERSISTED", "ALREADY_PERSISTED"}:
            persisted += 1
        else:
            held += 1
            blockers.extend(outcome.get("blockers") or [])
    return {
        "status": "OK" if attempted and persisted else ("HELD" if attempted else "NO_ELIGIBLE_ROWS"),
        "attempted": attempted,
        "persisted": persisted,
        "held": held,
        "blockers": blockers[:50],
        "can_execute": False,
        "probability_publishable": False,
        "rank_eligible": False,
    }


__all__ = [
    "NBAPointsSnapshotCaptureRequest",
    "NBAPointsSnapshotCaptureError",
    "capture_nba_points_snapshot",
    "acquire_daily_nba_points_snapshots",
]
