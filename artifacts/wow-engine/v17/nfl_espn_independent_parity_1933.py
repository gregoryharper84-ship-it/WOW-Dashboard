"""Independent ESPN NFL final-event parity for WOW backlog #1933.

Scope: one historical scoreboard calendar date, exact NFLverse schedule identity,
score and UTC kickoff matching. No model capability, staging write or execution.
"""
from __future__ import annotations

import hashlib
import json
from datetime import date, datetime, timezone
from typing import Any, Callable, Mapping
from urllib.error import HTTPError, URLError
from urllib.request import HTTPRedirectHandler, Request, build_opener
from urllib.parse import urlparse

from nflverse_historical_adapter import normalize_schedule_row
from v17.multi_source_historical_collector import CollectionBlocked
from v17.nfl_multi_source_pilot import _read_csv, REQUIRED_SCHEDULE

CAN_EXECUTE = False
MAX_ESPN_BYTES = 5 * 1024 * 1024
ESPN_SCOREBOARD_URL = (
    "https://site.api.espn.com/apis/site/v2/sports/football/nfl/"
    "scoreboard?dates=20250907"
)
PILOT_DAY = "2025-09-07"
ESPN_TO_NFLVERSE_TEAM = {
    "LAR": "LA", "WSH": "WAS", "JAC": "JAX", "GNB": "GB", "KAN": "KC",
    "NWE": "NE", "NOR": "NO", "SFO": "SF", "TBB": "TB",
}


class _NoRedirects(HTTPRedirectHandler):
    def redirect_request(self, req, fp, code, msg, headers, newurl):
        raise CollectionBlocked("NFL_ESPN_UNEXPECTED_REDIRECT")


def fetch_espn_historical_scoreboard() -> bytes:
    """Exactly one public ESPN endpoint, no arbitrary URL inputs or redirects."""
    parsed = urlparse(ESPN_SCOREBOARD_URL)
    if parsed.scheme != "https" or parsed.hostname != "site.api.espn.com":
        raise CollectionBlocked("NFL_ESPN_ORIGIN_INVALID")
    try:
        with build_opener(_NoRedirects()).open(
            Request(ESPN_SCOREBOARD_URL, headers={
                "User-Agent": "WOW-V17-backlog-historical-identity/1.0",
                "Accept": "application/json",
            }), timeout=18,
        ) as response:
            payload = response.read(MAX_ESPN_BYTES + 1)
    except HTTPError as exc:
        raise CollectionBlocked("NFL_ESPN_HTTP_UNAVAILABLE", str(exc.code)) from exc
    except URLError as exc:
        raise CollectionBlocked("NFL_ESPN_NETWORK_UNAVAILABLE") from exc
    if not payload or len(payload) > MAX_ESPN_BYTES:
        raise CollectionBlocked("NFL_ESPN_PAYLOAD_INVALID_SIZE")
    return payload


def _event_identity(event: Mapping[str, Any], payload: Mapping[str, Any]) -> dict[str, Any]:
    """Return one ESPN event only if finalized, correctly typed and traceable."""
    eid = str(event.get("id") or "").strip()
    if not eid:
        raise CollectionBlocked("NFL_ESPN_EVENT_ID_MISSING")
    comps = event.get("competitions")
    if not isinstance(comps, list) or len(comps) != 1:
        raise CollectionBlocked("NFL_ESPN_COMPETITION_AMBIGUOUS", eid)
    comp = comps[0]
    sides = comp.get("competitors") if isinstance(comp, dict) else None
    if not isinstance(sides, list) or len(sides) != 2:
        raise CollectionBlocked("NFL_ESPN_SIDES_INVALID", eid)
    teams = {}
    for side in sides:
        if not isinstance(side, dict):
            raise CollectionBlocked("NFL_ESPN_SIDES_INVALID", eid)
        k = str(side.get("homeAway") or "")
        team = (side.get("team") or {}).get("abbreviation") or ""
        team = ESPN_TO_NFLVERSE_TEAM.get(str(team).upper(), str(team).upper())
        if k not in ("home", "away") or k in teams or not team:
            raise CollectionBlocked("NFL_ESPN_SIDES_INVALID", eid)
        try:
            score = int(str(side["score"]))
            if score < 0:
                raise ValueError("score negative")
        except (ValueError, TypeError, KeyError) as exc:
            raise CollectionBlocked("NFL_ESPN_FINAL_SCORE_MISSING", eid) from exc
        teams[k] = (team, score)
    if len(teams) != 2 or teams["home"][0] == teams["away"][0]:
        raise CollectionBlocked("NFL_ESPN_SIDES_INVALID", eid)
    raw_season = event.get("season") or payload.get("season") or {}
    raw_week = event.get("week") or payload.get("week") or {}
    try:
        season = int(raw_season["year"])
        week = int(raw_week["number"])
        kickoff = datetime.fromisoformat(str(event["date"]).replace("Z", "+00:00"))
        if kickoff.utcoffset() is None:
            raise ValueError("timezone required")
    except (KeyError, ValueError, TypeError) as exc:
        raise CollectionBlocked("NFL_ESPN_CHRONOLOGY_INCOMPLETE", eid) from exc
    status = event.get("status") or {}
    completed = (status.get("type") or {}).get("completed") is True
    return {
        "event_id": eid, "season": season, "week": week,
        "kickoff": kickoff.astimezone(timezone.utc),
        "home_team": teams["home"][0], "away_team": teams["away"][0],
        "home_score": teams["home"][1], "away_score": teams["away"][1],
        "completed": completed,
    }


def compare_espn_date(
    *, schedule_csv: bytes, espn_json: bytes, game_day: str = PILOT_DAY,
) -> dict[str, Any]:
    if game_day != PILOT_DAY:
        raise CollectionBlocked("NFL_ESPN_PILOT_DATE_UNSUPPORTED")
    source_schedule = _read_csv(schedule_csv, required=REQUIRED_SCHEDULE, label="schedules")
    try:
        payload = json.loads(espn_json)
    except (UnicodeError, ValueError) as exc:
        raise CollectionBlocked("NFL_ESPN_JSON_INVALID") from exc
    if not isinstance(payload, dict) or not isinstance(payload.get("events"), list):
        raise CollectionBlocked("NFL_ESPN_RESPONSE_SCHEMA_INVALID")
    fixtures = []
    for row in source_schedule:
        if str(row["season"]) != "2025" or row["game_type"] != "REG" or row["gameday"] != game_day:
            continue
        if row["home_score"] in ("", None) or row["away_score"] in ("", None):
            continue
        fixtures.append((row, normalize_schedule_row(row)))
    if not fixtures:
        raise CollectionBlocked("NFL_ESPN_TARGET_GAMES_MISSING")
    events = [_event_identity(event, payload) for event in payload["events"]]
    ids = [e["event_id"] for e in events]
    if len(set(ids)) != len(ids):
        raise CollectionBlocked("NFL_ESPN_DUPLICATE_EVENT_IDS")
    matched, incomplete, score_conflicts = [], [], []
    for row, fixture in fixtures:
        candidates = [
            event for event in events
            if event["season"] == fixture.season
            and event["week"] == fixture.week
            and event["home_team"] == fixture.home_team
            and event["away_team"] == fixture.away_team
            and abs((event["kickoff"] - fixture.event_start_time).total_seconds()) <= 90 * 60
        ]
        if len(candidates) > 1:
            raise CollectionBlocked("NFL_ESPN_EVENT_IDENTITY_AMBIGUOUS", fixture.game_id)
        if not candidates or not candidates[0]["completed"]:
            incomplete.append(fixture.game_id)
            continue
        event = candidates[0]
        if int(row["home_score"]) != event["home_score"] or int(row["away_score"]) != event["away_score"]:
            score_conflicts.append(fixture.game_id)
            continue
        matched.append({"nflverse_game_id": fixture.game_id, "espn_event_id": event["event_id"]})
    status = "MATCHED" if len(matched) == len(fixtures) else "PARTIAL_HOLD"
    return {
        "status": status,
        "scope": "ONE_2025_REGULAR_SEASON_DATE",
        "date": game_day,
        "source_url": ESPN_SCOREBOARD_URL,
        "source_sha256": hashlib.sha256(espn_json).hexdigest(),
        "source": "ESPN_NFL_SCOREBOARD_INDEPENDENT",
        "games_in_scope": len(fixtures),
        "matched_final_games": len(matched),
        "missing_or_unfinalized_games": len(incomplete),
        "score_conflict_games": len(score_conflicts),
        "matched_event_ids": matched,
        "missing_game_ids": incomplete,
        "score_conflict_game_ids": score_conflicts,
        "full_season_parity_established": False,
        "probability_publishable": False,
        "can_execute": False,
    }
