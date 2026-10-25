"""Read-only NBA CDN settlement proof for exact, immutable NBA scalar research rows.

Only an official FINAL boxscore with pregame cross-provider identity can supply
an outcome. This module cannot score, certify, promote, publish, or execute.
"""
from __future__ import annotations

from datetime import datetime, timezone
from math import isfinite
import re
from typing import Any, Callable, Mapping

import httpx

CAN_EXECUTE = False
NBA_SCALAR_STATS = frozenset({
    "POINTS", "REBOUNDS", "ASSISTS", "PRA", "POINTS_REBOUNDS",
    "POINTS_ASSISTS", "REBOUNDS_ASSISTS",
})
COMPONENTS = {
    "POINTS": ("points",),
    "REBOUNDS": ("reboundsTotal",),
    "ASSISTS": ("assists",),
    "PRA": ("points", "reboundsTotal", "assists"),
    "POINTS_REBOUNDS": ("points", "reboundsTotal"),
    "POINTS_ASSISTS": ("points", "assists"),
    "REBOUNDS_ASSISTS": ("reboundsTotal", "assists"),
}


def _stamp(value: Any) -> datetime | None:
    try:
        parsed = datetime.fromisoformat(str(value).replace("Z", "+00:00"))
    except (ValueError, TypeError):
        return None
    if parsed.tzinfo is None or parsed.utcoffset() is None:
        return None
    return parsed.astimezone(timezone.utc)


def _same_name(first: Any, second: Any) -> bool:
    return " ".join(str(first or "").casefold().split()) == " ".join(str(second or "").casefold().split())


def evaluate_nba_scalar_outcome(
    prediction: Mapping[str, Any],
    snapshot: Mapping[str, Any],
    *,
    http_get: Callable[..., Any] = httpx.get,
    now: datetime | None = None,
) -> dict[str, Any]:
    """Return typed official-stat proof, never a fitted model probability."""
    now = (now or datetime.now(timezone.utc)).astimezone(timezone.utc)
    stat = str(prediction.get("stat_type") or "").upper()
    if stat not in NBA_SCALAR_STATS:
        return {"status": "MODEL_OR_IDENTITY_UNSUPPORTED", "blocker": "NBA_EXACT_SCALAR_STAT_UNSUPPORTED", "can_execute": False}
    start = _stamp(prediction.get("event_start_time"))
    snap_start = _stamp(snapshot.get("event_start_time"))
    if (start is None or snap_start != start or now <= start
        or str(prediction.get("sport")) != "NBA" or str(snapshot.get("sport")) != "NBA"
        or str(prediction.get("event_id")) != str(snapshot.get("event_id"))
        or not _same_name(prediction.get("player"), snapshot.get("player"))
        or stat != str(snapshot.get("stat_type") or "").upper()
        or prediction.get("source_snapshot_id") != snapshot.get("source_snapshot_id")):
        return {"status": "IDENTITY_UNRESOLVED", "blocker": "NBA_PREDICTION_SNAPSHOT_IDENTITY_MISMATCH", "can_execute": False}
    try:
        if float(prediction.get("line")) != float(snapshot.get("line")):
            raise ValueError("line mismatch")
    except (ValueError, TypeError):
        return {"status": "IDENTITY_UNRESOLVED", "blocker": "NBA_EXACT_LINE_MISMATCH", "can_execute": False}
    role = snapshot.get("role_status")
    identity = role.get("source_identity") if isinstance(role, Mapping) else None
    if not isinstance(identity, Mapping):
        return {"status": "IDENTITY_UNRESOLVED", "blocker": "NBA_IMMUTABLE_PREGAME_SOURCE_IDENTITY_REQUIRED", "can_execute": False}
    game_id = str(identity.get("nba_game_id") or "")
    player_id = str(identity.get("nba_person_id") or "")
    if (not re.fullmatch(r"\d{10}", game_id) or not re.fullmatch(r"[1-9]\d{0,9}", player_id)
        or identity.get("espn_event_id") != prediction.get("event_id")
        or _stamp(identity.get("event_start_time")) != start
        or not all((stamp := _stamp(identity.get(k))) is not None and stamp < start and stamp <= now
                   for k in ("espn_captured_at", "nba_captured_at"))):
        return {"status": "IDENTITY_UNRESOLVED", "blocker": "NBA_PREGAME_SOURCE_PROVENANCE_INVALID", "can_execute": False}
    url = f"https://cdn.nba.com/static/json/liveData/boxscore/boxscore_{game_id}.json"
    try:
        response = http_get(url, timeout=12.0, follow_redirects=False)
        if response.status_code != 200:
            raise RuntimeError("NBA_CDN_HTTP_FAILURE")
        payload = response.json()
    except Exception:
        return {"status": "OFFICIAL_SOURCE_UNAVAILABLE", "blocker": "NBA_OFFICIAL_FINAL_BOXSCORE_UNAVAILABLE", "can_execute": False}
    game = payload.get("game") if isinstance(payload, Mapping) else None
    if not isinstance(game, Mapping) or str(game.get("gameId")) != game_id or _stamp(game.get("gameTimeUTC")) != start:
        return {"status": "OFFICIAL_EVENT_ID_MISMATCH", "can_execute": False}
    if str(game.get("gameStatus")) != "3":
        return {"status": "NOT_FINAL", "can_execute": False}
    roster = []
    for side in ("homeTeam", "awayTeam"):
        team = game.get(side)
        if not isinstance(team, Mapping) or not isinstance(team.get("players"), list):
            return {"status": "OFFICIAL_PLAYER_EVENT_IDENTITY_AMBIGUOUS", "can_execute": False}
        roster.extend(x for x in team["players"] if isinstance(x, Mapping) and str(x.get("personId")) == player_id)
    if len(roster) != 1:
        return {"status": "OFFICIAL_PLAYER_EVENT_IDENTITY_AMBIGUOUS", "match_n": len(roster), "can_execute": False}
    player = roster[0]
    if not _same_name(player.get("name"), prediction.get("player")):
        return {"status": "OFFICIAL_PLAYER_EVENT_IDENTITY_AMBIGUOUS", "can_execute": False}
    if str(player.get("played")) == "0" and (player.get("notPlayingReason") or player.get("notPlayingDescription")):
        return {"status": "SETTLED_VOID_DNP", "void_reason": "PLAYER_DNP", "official_source": url, "can_execute": False}
    if str(player.get("played")) != "1":
        return {"status": "OFFICIAL_PLAYER_PARTICIPATION_UNPROVEN", "can_execute": False}
    stats = player.get("statistics")
    if not isinstance(stats, Mapping):
        return {"status": "OFFICIAL_STAT_MISSING", "can_execute": False}
    actual = 0.0
    for key in COMPONENTS[stat]:
        value = stats.get(key)
        if isinstance(value, bool) or not isinstance(value, (int, float)) or not isfinite(value) or value < 0 or value > 200:
            return {"status": "OFFICIAL_STAT_MISSING", "stat_column": key, "can_execute": False}
        actual += float(value)
    return {"status": "SETTLED", "actual_stat": actual, "official_source": url, "can_execute": False}
