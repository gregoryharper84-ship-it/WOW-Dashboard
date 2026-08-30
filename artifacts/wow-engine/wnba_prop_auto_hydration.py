"""Fail-closed automatic evidence hydration for certified WNBA prop routes.

Official/current sources only:
* public WNBA CDN schedule (current-season ScheduleLeagueV2 payload),
* stats.wnba.com CommonTeamRoster + LeagueGameLog (LeagueID=10),
* WNBA official timestamped injury-report PDFs hosted by NBA CMS.

The hydrator owns acquisition only. It does not calculate probability, certify a
model, calibrate, persist, approve, price, or execute. Any ambiguous event,
roster, availability, or L10 history state raises WNBAPropHydrationError.
"""
from __future__ import annotations

from datetime import datetime, timedelta, timezone
from io import BytesIO
import math
import re
import time
import unicodedata
from typing import Any, Callable, Mapping, Optional
from zoneinfo import ZoneInfo

import httpx
from pypdf import PdfReader


WNBA_SCHEDULE_URL = "https://cdn.wnba.com/static/json/staticData/scheduleLeagueV2.json"
WNBA_STATS_BASE = "https://stats.wnba.com/stats"
WNBA_INJURY_BASE = "https://ak-static.cms.nba.com/referee/wnba_injury"
PROVIDER_ID = "WNBA_OFFICIAL_STATS_CDN_INJURY_V1"
EVIDENCE_VERSION = "PROP_EVIDENCE_V1"
HTTP_TIMEOUT_SECONDS = 10.0
HTTP_ATTEMPTS = 2
MIN_PRIOR_GAMES = 10
MAX_EVENT_START_DELTA_SECONDS = 30 * 60
MAX_INJURY_REPORT_AGE_SECONDS = 2 * 60 * 60
ET = ZoneInfo("America/New_York")

STAT_COLUMNS = {
    "POINTS": "PTS",
    "PTS": "PTS",
    "REBOUNDS": "REB",
    "REB": "REB",
    "ASSISTS": "AST",
    "AST": "AST",
    "THREE_POINTERS_MADE": "FG3M",
    "3PM": "FG3M",
    "THREES_MADE": "FG3M",
}
CANONICAL_STATS = {
    "PTS": "POINTS",
    "REB": "REBOUNDS",
    "AST": "ASSISTS",
    "FG3M": "THREE_POINTERS_MADE",
}


class WNBAPropHydrationError(RuntimeError):
    def __init__(self, code: str, message: str, *, detail: Optional[dict[str, Any]] = None):
        super().__init__(message)
        self.code = code
        self.detail = detail or {}


def _name_key(value: Any) -> str:
    text = unicodedata.normalize("NFKD", str(value or ""))
    text = "".join(ch for ch in text if not unicodedata.combining(ch))
    text = re.sub(r"[^A-Za-z0-9]+", " ", text).strip().casefold()
    return " ".join(text.split())


def _aware(value: Any) -> datetime:
    try:
        parsed = datetime.fromisoformat(str(value).replace("Z", "+00:00"))
    except (TypeError, ValueError) as exc:
        raise WNBAPropHydrationError(
            "PROP_EVENT_START_INVALID", "event_start_time must be timezone-aware ISO 8601"
        ) from exc
    if parsed.utcoffset() is None:
        raise WNBAPropHydrationError(
            "PROP_EVENT_START_INVALID", "event_start_time must include a timezone"
        )
    return parsed.astimezone(timezone.utc)


def _stats_headers() -> dict[str, str]:
    return {
        "User-Agent": "Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36 (KHTML, like Gecko) Chrome/120.0.0.0 Safari/537.36",
        "Accept": "application/json, text/plain, */*",
        "Accept-Language": "en-US,en;q=0.9",
        "Origin": "https://stats.wnba.com",
        "Referer": "https://www.wnba.com/",
        "x-nba-stats-origin": "stats",
        "x-nba-stats-token": "true",
    }


def _request(
    url: str,
    *,
    http_get: Callable[..., Any],
    params: Optional[dict[str, Any]] = None,
    headers: Optional[dict[str, str]] = None,
    expect_json: bool = True,
) -> Any:
    errors: list[str] = []
    for attempt in range(1, HTTP_ATTEMPTS + 1):
        try:
            response = http_get(
                url,
                params=params or {},
                headers=headers or {},
                timeout=HTTP_TIMEOUT_SECONDS,
                follow_redirects=True,
            )
            status = int(getattr(response, "status_code", 200))
            if status >= 400:
                raise RuntimeError(f"HTTP_{status}")
            if expect_json:
                payload = response.json()
                if not isinstance(payload, Mapping):
                    raise TypeError("JSON_NOT_OBJECT")
                return dict(payload)
            content = bytes(getattr(response, "content", b""))
            if not content:
                raise TypeError("EMPTY_BODY")
            return content
        except Exception as exc:
            errors.append(f"{type(exc).__name__}:{exc}")
            if attempt < HTTP_ATTEMPTS:
                time.sleep(0.05)
    raise WNBAPropHydrationError(
        "WNBA_OFFICIAL_SOURCE_UNAVAILABLE",
        "a required official WNBA evidence source could not be retrieved",
        detail={"url": url, "attempts": HTTP_ATTEMPTS, "errors": errors[-4:]},
    )


def _result_rows(payload: Mapping[str, Any], preferred_name: str) -> list[dict[str, Any]]:
    result_sets = payload.get("resultSets")
    if not isinstance(result_sets, list):
        single = payload.get("resultSet")
        result_sets = [single] if isinstance(single, Mapping) else []
    target: Optional[Mapping[str, Any]] = None
    for item in result_sets:
        if isinstance(item, Mapping) and str(item.get("name") or "").casefold() == preferred_name.casefold():
            target = item
            break
    if target is None and len(result_sets) == 1 and isinstance(result_sets[0], Mapping):
        target = result_sets[0]
    if target is None:
        raise WNBAPropHydrationError(
            "WNBA_STATS_RESULT_SET_MISSING", f"required result set {preferred_name!r} was missing"
        )
    headers = target.get("headers")
    rows = target.get("rowSet")
    if not isinstance(headers, list) or not all(isinstance(v, str) for v in headers) or not isinstance(rows, list):
        raise WNBAPropHydrationError(
            "WNBA_STATS_RESULT_SET_INVALID", f"result set {preferred_name!r} had invalid shape"
        )
    out: list[dict[str, Any]] = []
    for row in rows:
        if not isinstance(row, list) or len(row) != len(headers):
            raise WNBAPropHydrationError("WNBA_STATS_ROW_SHAPE_INVALID", preferred_name)
        out.append(dict(zip(headers, row)))
    return out


def _schedule(event_start: datetime, *, http_get: Callable[..., Any]) -> dict[str, Any]:
    payload = _request(
        WNBA_SCHEDULE_URL,
        http_get=http_get,
        headers={"User-Agent": _stats_headers()["User-Agent"], "Accept": "application/json"},
    )
    league = payload.get("leagueSchedule")
    blocks = league.get("gameDates") if isinstance(league, Mapping) else None
    if not isinstance(blocks, list):
        raise WNBAPropHydrationError("WNBA_SCHEDULE_INVALID", "leagueSchedule.gameDates was missing")

    candidates: list[tuple[float, dict[str, Any]]] = []
    for block in blocks:
        games = block.get("games") if isinstance(block, Mapping) else None
        if not isinstance(games, list):
            continue
        for game in games:
            if not isinstance(game, Mapping):
                continue
            raw_start = game.get("gameDateTimeUTC") or game.get("gameDateUTC")
            if not raw_start:
                continue
            try:
                scheduled = _aware(raw_start)
            except WNBAPropHydrationError:
                continue
            delta = abs((scheduled - event_start).total_seconds())
            if delta <= MAX_EVENT_START_DELTA_SECONDS:
                candidates.append((delta, dict(game)))
    if not candidates:
        raise WNBAPropHydrationError(
            "PROP_EVENT_IDENTITY_CONFLICT",
            "no official WNBA schedule event matched the requested start time",
            detail={"event_start_time": event_start.isoformat()},
        )
    candidates.sort(key=lambda item: item[0])
    nearest = candidates[0][0]
    tied = [game for delta, game in candidates if abs(delta - nearest) < 1.0]
    if len(tied) != 1:
        raise WNBAPropHydrationError(
            "PROP_EVENT_IDENTITY_CONFLICT",
            "requested start time did not resolve to exactly one official WNBA event",
            detail={"matching_event_n": len(tied)},
        )
    game = tied[0]
    if int(game.get("gameStatus") or 0) != 1:
        raise WNBAPropHydrationError(
            "EVENT_ALREADY_STARTED",
            "official WNBA schedule no longer marks the target event as scheduled",
            detail={"game_status": game.get("gameStatus"), "game_status_text": game.get("gameStatusText")},
        )
    return game


def _team_node(game: Mapping[str, Any], side: str) -> dict[str, Any]:
    node = game.get(side)
    if not isinstance(node, Mapping):
        raise WNBAPropHydrationError("WNBA_SCHEDULE_TEAM_INVALID", side)
    return dict(node)


def _roster(team_id: str, season: int, *, http_get: Callable[..., Any]) -> list[dict[str, Any]]:
    payload = _request(
        f"{WNBA_STATS_BASE}/commonteamroster",
        params={"LeagueID": "10", "Season": str(season), "TeamID": str(team_id)},
        headers=_stats_headers(),
        http_get=http_get,
    )
    return _result_rows(payload, "CommonTeamRoster")


def _resolve_player_and_team(
    game: Mapping[str, Any],
    player: str,
    season: int,
    *,
    http_get: Callable[..., Any],
) -> dict[str, Any]:
    matches: list[dict[str, Any]] = []
    for side in ("homeTeam", "awayTeam"):
        team = _team_node(game, side)
        team_id = str(team.get("teamId") or "").strip()
        if not team_id:
            raise WNBAPropHydrationError("WNBA_SCHEDULE_TEAM_INVALID", "teamId missing")
        rows = _roster(team_id, season, http_get=http_get)
        exact = [row for row in rows if _name_key(row.get("PLAYER")) == _name_key(player)]
        for row in exact:
            matches.append({"side": side, "team": team, "roster": row})
    if len(matches) != 1:
        raise WNBAPropHydrationError(
            "PROP_PLAYER_IDENTITY_UNRESOLVED",
            "current official WNBA rosters did not produce exactly one event-team player match",
            detail={"player": player, "match_n": len(matches)},
        )
    match = matches[0]
    other_side = "awayTeam" if match["side"] == "homeTeam" else "homeTeam"
    opponent = _team_node(game, other_side)
    return {**match, "opponent": opponent}


def _player_game_log(
    player_id: str,
    player_name: str,
    stat_column: str,
    season: int,
    event_start: datetime,
    *,
    http_get: Callable[..., Any],
) -> tuple[list[float], list[dict[str, Any]]]:
    # Keep LeagueID first: the 2026 WNBA Stats endpoint is query-order-sensitive.
    payload = _request(
        f"{WNBA_STATS_BASE}/leaguegamelog",
        params={
            "LeagueID": "10",
            "PlayerOrTeam": "P",
            "Season": str(season),
            "SeasonType": "Regular Season",
            "Counter": "0",
            "DateFrom": "",
            "DateTo": "",
            "Direction": "ASC",
            "Sorter": "DATE",
        },
        headers=_stats_headers(),
        http_get=http_get,
    )
    rows = _result_rows(payload, "LeagueGameLog")
    selected: list[dict[str, Any]] = []
    for row in rows:
        row_pid = str(row.get("PLAYER_ID") or row.get("PERSON_ID") or "").strip()
        row_name = row.get("PLAYER_NAME") or row.get("PLAYER")
        if row_pid and row_pid != str(player_id):
            continue
        if not row_pid and _name_key(row_name) != _name_key(player_name):
            continue
        raw_date = str(row.get("GAME_DATE") or "")[:10]
        try:
            game_date = datetime.fromisoformat(raw_date).date()
        except ValueError:
            continue
        if game_date >= event_start.date():
            continue
        try:
            minutes = float(row.get("MIN"))
            stat = float(row.get(stat_column))
        except (TypeError, ValueError):
            continue
        if not math.isfinite(minutes) or not 0 < minutes <= 60:
            continue
        if not math.isfinite(stat) or stat < 0 or stat != int(stat):
            continue
        selected.append(
            {
                "date": raw_date,
                "game_id": str(row.get("GAME_ID") or ""),
                "team": str(row.get("TEAM_ABBREVIATION") or ""),
                "matchup": str(row.get("MATCHUP") or ""),
                "minutes": minutes,
                "stat": int(stat),
            }
        )
    selected.sort(key=lambda row: (row["date"], row["game_id"]), reverse=True)
    recent = selected[:MIN_PRIOR_GAMES]
    if len(recent) < MIN_PRIOR_GAMES:
        raise WNBAPropHydrationError(
            "WNBA_RECENT_GAMES_INSUFFICIENT",
            "fewer than ten official prior WNBA games with aligned minutes/stat were available",
            detail={"games_found": len(recent), "required": MIN_PRIOR_GAMES},
        )
    game_log = [float(row["stat"]) for row in recent]
    box = [
        {
            "date": row["date"],
            "game_id": row["game_id"],
            "team": row["team"],
            "matchup": row["matchup"],
            "minutes": row["minutes"],
        }
        for row in recent
    ]
    return game_log, box


def _pdf_report_timestamp(candidate: datetime) -> tuple[str, datetime]:
    local = candidate.astimezone(ET).replace(second=0, microsecond=0)
    minute = (local.minute // 15) * 15
    local = local.replace(minute=minute)
    suffix = local.strftime("%p")
    url = (
        f"{WNBA_INJURY_BASE}/Injury-Report_"
        f"{local.strftime('%Y-%m-%d_%I')}_{local.strftime('%M')}{suffix}.pdf"
    )
    return url, local


def _latest_injury_report(
    now: datetime,
    *,
    http_get: Callable[..., Any],
) -> tuple[str, datetime, str]:
    # Prefer newest 15-minute buckets, then wider fallbacks. 404s are expected
    # when the league did not publish at a particular bucket and are not retried.
    minute_offsets = (0, 15, 30, 45, 60, 75, 90, 105, 120)
    errors: list[str] = []
    seen: set[str] = set()
    for offset in minute_offsets:
        url, report_ts = _pdf_report_timestamp(now - timedelta(minutes=offset))
        if url in seen:
            continue
        seen.add(url)
        try:
            response = http_get(
                url,
                params={},
                headers={"User-Agent": _stats_headers()["User-Agent"], "Accept": "application/pdf"},
                timeout=HTTP_TIMEOUT_SECONDS,
                follow_redirects=True,
            )
            status = int(getattr(response, "status_code", 200))
            if status == 404:
                continue
            if status >= 400:
                errors.append(f"{status}:{url}")
                continue
            content = bytes(getattr(response, "content", b""))
            if not content:
                continue
            reader = PdfReader(BytesIO(content))
            text = "\n".join((page.extract_text() or "") for page in reader.pages)
            if "Injury Report" not in text:
                continue
            age = (now.astimezone(timezone.utc) - report_ts.astimezone(timezone.utc)).total_seconds()
            if age < -60 or age > MAX_INJURY_REPORT_AGE_SECONDS:
                continue
            return url, report_ts, text
        except Exception as exc:
            errors.append(f"{type(exc).__name__}:{url}")
    raise WNBAPropHydrationError(
        "WNBA_INJURY_REPORT_UNAVAILABLE",
        "no fresh official WNBA injury report could be retrieved and parsed",
        detail={"attempted_urls": len(seen), "errors": errors[-5:]},
    )


def _availability_from_report(
    text: str,
    *,
    player_name: str,
    team_name: str,
    matchup: str,
    game_date: str,
) -> dict[str, Any]:
    compact = " ".join(str(text).replace("\u00a0", " ").split())
    normalized = _name_key(compact)
    matchup_key = _name_key(matchup)
    team_key = _name_key(team_name)
    if matchup_key not in normalized or team_key not in normalized:
        raise WNBAPropHydrationError(
            "WNBA_INJURY_REPORT_EVENT_UNRESOLVED",
            "fresh official injury report did not contain the target matchup/team",
            detail={"matchup": matchup, "team": team_name, "game_date": game_date},
        )

    # Inspect a bounded team section around the team name. If the team has not
    # submitted, no player from that team is allowed to pass by omission.
    team_pos = normalized.find(team_key)
    section = normalized[team_pos : team_pos + 1800]
    if "not yet submitted" in section[:250]:
        raise WNBAPropHydrationError(
            "WNBA_INJURY_REPORT_NOT_SUBMITTED",
            "target team has not submitted its official WNBA injury report",
            detail={"team": team_name, "game_date": game_date},
        )

    name_parts = _name_key(player_name).split()
    forward = " ".join(name_parts)
    reverse = " ".join(reversed(name_parts)) if len(name_parts) >= 2 else forward
    positions = [p for p in (section.find(forward), section.find(reverse)) if p >= 0]
    if not positions:
        return {
            "availability": "NOT_LISTED_ON_FRESH_OFFICIAL_INJURY_REPORT",
            "designation": None,
            "injury_reason": None,
        }

    pos = min(positions)
    after = section[pos + len(forward) : pos + len(forward) + 180]
    designation = None
    for label in ("out", "doubtful", "questionable", "probable", "available"):
        if re.search(rf"\b{label}\b", after):
            designation = label.upper()
            break
    if designation is None:
        raise WNBAPropHydrationError(
            "WNBA_INJURY_REPORT_PLAYER_STATUS_UNRESOLVED",
            "player appeared on the official injury report but the designation was not parsed",
            detail={"player": player_name, "team": team_name},
        )
    if designation != "AVAILABLE":
        raise WNBAPropHydrationError(
            "WNBA_PLAYER_AVAILABILITY_NOT_CLEAR",
            "player has an explicit current official injury/availability designation; unconditional model adjustment is not certified",
            detail={"player": player_name, "team": team_name, "designation": designation},
        )
    return {"availability": "AVAILABLE", "designation": designation, "injury_reason": None}


def hydrate_wnba_prop_evidence(
    *,
    player: str,
    stat_type: str,
    event_start_time: str,
    http_get: Callable[..., Any] = httpx.get,
    now: Optional[datetime] = None,
    source_capture_timestamp: Optional[str] = None,
    source_label: str = "NORMALIZED_PICK_REQUEST",
    opponent: Optional[str] = None,
) -> dict[str, Any]:
    stat_key = "_".join(str(stat_type or "").strip().upper().replace("-", " ").split())
    stat_column = STAT_COLUMNS.get(stat_key)
    if stat_column is None:
        raise WNBAPropHydrationError(
            "PROP_AUTO_HYDRATION_UNSUPPORTED_ROUTE",
            "WNBA automatic hydration is not certified for this stat route",
            detail={"sport": "WNBA", "stat_type": stat_key},
        )
    canonical_stat = CANONICAL_STATS[stat_column]
    normalized_player = " ".join(str(player or "").strip().split())
    if not normalized_player:
        raise WNBAPropHydrationError("PROP_PLAYER_IDENTITY_REQUIRED", "player is required")

    captured = (now or datetime.now(timezone.utc)).astimezone(timezone.utc)
    event_start = _aware(event_start_time)
    if event_start <= captured:
        raise WNBAPropHydrationError("EVENT_ALREADY_STARTED", "pregame evidence cannot be hydrated after event start")

    game = _schedule(event_start, http_get=http_get)
    resolved = _resolve_player_and_team(game, normalized_player, event_start.year, http_get=http_get)
    roster = resolved["roster"]
    team = resolved["team"]
    opp = resolved["opponent"]
    official_name = str(roster.get("PLAYER") or normalized_player)
    player_id = str(roster.get("PLAYER_ID") or "").strip()
    if not player_id:
        raise WNBAPropHydrationError("PROP_PLAYER_IDENTITY_UNRESOLVED", "official WNBA roster player ID missing")

    opponent_name = " ".join(
        str(opp.get("teamCity") or "").split() + str(opp.get("teamName") or "").split()
    ).strip()
    opponent_tricode = str(opp.get("teamTricode") or "").strip().upper()
    if opponent and _name_key(opponent) not in {_name_key(opponent_name), _name_key(opponent_tricode)}:
        raise WNBAPropHydrationError(
            "PROP_EVENT_IDENTITY_CONFLICT",
            "requested opponent did not match the official WNBA schedule event",
            detail={"requested_opponent": opponent, "official_opponent": opponent_name, "official_tricode": opponent_tricode},
        )

    game_log, box_score_log = _player_game_log(
        player_id,
        official_name,
        stat_column,
        event_start.year,
        event_start,
        http_get=http_get,
    )

    away = _team_node(game, "awayTeam")
    home = _team_node(game, "homeTeam")
    matchup = f"{str(away.get('teamTricode') or '').upper()}@{str(home.get('teamTricode') or '').upper()}"
    game_date = event_start.astimezone(ET).strftime("%m/%d/%Y")
    team_name = " ".join(
        str(team.get("teamCity") or "").split() + str(team.get("teamName") or "").split()
    ).strip()
    injury_url, injury_ts, injury_text = _latest_injury_report(captured, http_get=http_get)
    availability = _availability_from_report(
        injury_text,
        player_name=official_name,
        team_name=team_name,
        matchup=matchup,
        game_date=game_date,
    )

    timestamp = captured.isoformat()
    source_timestamps = {
        "WNBA_CDN_SCHEDULE_CURRENT": timestamp,
        "WNBA_STATS_COMMON_TEAM_ROSTER": timestamp,
        "WNBA_STATS_LEAGUE_GAME_LOG": timestamp,
        "WNBA_OFFICIAL_INJURY_REPORT": injury_ts.astimezone(timezone.utc).isoformat(),
    }
    if source_capture_timestamp:
        source_timestamps[f"INPUT_CAPTURE_{str(source_label).strip().upper()}"] = source_capture_timestamp

    l10_minutes = [float(row["minutes"]) for row in box_score_log]
    return {
        "captured_at": timestamp,
        "game_log": game_log,
        "box_score_log": box_score_log,
        "role_status": {
            "status": "CURRENT_ROSTER_CONFIRMED_NO_BLOCKING_INJURY_DESIGNATION",
            "role": str(roster.get("POSITION") or "WNBA_ROTATION_PLAYER"),
            "confirmation_strength": "OFFICIAL_ROSTER_PLUS_FRESH_OFFICIAL_INJURY_REPORT",
            "player_id": player_id,
            "team_id": str(team.get("teamId") or ""),
            "team": team_name,
            "team_tricode": str(team.get("teamTricode") or ""),
            "opponent": opponent_name,
            "opponent_tricode": opponent_tricode,
            "official_game_id": str(game.get("gameId") or ""),
            "official_game_start_utc": str(game.get("gameDateTimeUTC") or ""),
            "schedule_status": str(game.get("gameStatusText") or "Scheduled"),
            "availability": availability["availability"],
            "injury_designation": availability["designation"],
            "injury_report_url": injury_url,
            "source": "WNBA official CDN schedule + WNBA Stats roster + official WNBA injury report",
        },
        "role_timestamp": timestamp,
        "opportunity_ledger": {
            "status": "READY",
            "stat_type": canonical_stat,
            "stat_source_column": stat_column,
            "box_score_alignment": "1:1",
            "prior_games": len(box_score_log),
            "l10_minutes_mean": sum(l10_minutes) / len(l10_minutes),
            "l5_minutes_mean": sum(l10_minutes[:5]) / 5.0,
            "current_roster_confirmation": "PASS",
            "availability_gate": "PASS",
            "availability_policy": "BLOCK_ANY_EXPLICIT_INJURY_REPORT_DESIGNATION_UNLESS_AVAILABLE",
        },
        "source_timestamps": source_timestamps,
        "evidence_version": EVIDENCE_VERSION,
        "rate_provenance": "Official WNBA LeagueGameLog player rows; current event/team from public WNBA CDN schedule; roster from CommonTeamRoster; availability from official WNBA injury-report PDF",
        "hydration_provider": PROVIDER_ID,
    }
