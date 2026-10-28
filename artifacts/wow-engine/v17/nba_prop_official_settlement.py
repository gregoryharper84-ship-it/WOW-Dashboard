"""NBA official full-game P/R/A exact-route settlement; immutable evidence only.

This module is an outcome adapter, NOT a fitted model, probability calibration,
ranker, publisher, or wager execution path. It refuses to infer NBA identifiers
from player names or third-party game ids. Requires a frozen, exact cross-provider
pregame roster identity (ESPN event + NBA CDN game/player) and a postgame *final*
NBA CDN box score. Ambiguity, missing player rows, nonfinal scores, partial periods,
and threshold offers without an exact numeric adapter all remain typed holds.

V17_TERMINAL_REDUCER remains the only terminal authority; can_execute=false.
"""
from __future__ import annotations

from datetime import datetime, timezone
from typing import Any, Callable, Mapping
import re

import httpx

import v17.prop_exact_route_settlement as settlement

CAN_EXECUTE = False
NBA_CDN_BOXSCORE_BASE = "https://cdn.nba.com/static/json/liveData/boxscore/"
NBA_SETTLEMENT_SOURCE = "NBA_OFFICIAL_CDN_FINAL_BOXSCORE"
STAT_COMPONENTS = {
    "POINTS": ("points",),
    "REBOUNDS": ("reboundsTotal",),
    "ASSISTS": ("assists",),
    "PRA": ("points", "reboundsTotal", "assists"),
    "POINTS_REBOUNDS": ("points", "reboundsTotal"),
    "POINTS_ASSISTS": ("points", "assists"),
    "REBOUNDS_ASSISTS": ("reboundsTotal", "assists"),
}
NBA_SUPPORTED = frozenset(("NBA", stat) for stat in STAT_COMPONENTS)
HEADERS = {
    "Accept": "application/json, text/plain, */*",
    "User-Agent": "Mozilla/5.0 (compatible; WOW-V17-ReadOnly-Settlement/1.0)",
    "Referer": "https://www.nba.com/",
    "Origin": "https://www.nba.com",
}


def _hold(status: str, blocker: str, **evidence: Any) -> dict[str, Any]:
    return {"status": status, "blocker": blocker, **evidence, "can_execute": False}


def _text(value: Any) -> str:
    return str(value or "").strip()


def _name(value: Any) -> str:
    return " ".join(_text(value).casefold().split())


def _utc(value: Any) -> datetime | None:
    return settlement._aware(value)


def _integer_stat(value: Any) -> float | None:
    if isinstance(value, bool) or not isinstance(value, (int, float)):
        return None
    f = settlement._finite_number(value)
    if f is None or not f.is_integer() or not 0 <= f <= 500:
        return None
    return f


def _played(value: Any) -> bool | None:
    if value is True or value in ("1", "true", "True", 1):
        return True
    if value is False or value in ("0", "false", "False", 0):
        return False
    return None


def _identity(
    prediction: Mapping[str, Any], snapshot: Mapping[str, Any], *, now: datetime,
) -> tuple[dict[str, Any] | None, dict[str, Any] | None]:
    """Validate frozen source proof before constructing a provider URL."""
    event_start = _utc(prediction.get("event_start_time"))
    model_at = _utc(prediction.get("model_timestamp"))
    locked_at = _utc(prediction.get("locked_at"))
    snapshot_start = _utc(snapshot.get("event_start_time"))
    captured_at = _utc(snapshot.get("captured_at"))
    if (event_start is None or model_at is None or locked_at is None
            or captured_at is None or snapshot_start != event_start
            or captured_at > model_at or model_at > locked_at
            or not captured_at < event_start or not model_at < event_start
            or not locked_at < event_start or now <= event_start):
        return None, _hold("IDENTITY_UNRESOLVED", "NBA_FORWARD_PREGAME_TEMPORAL_PROOF_REQUIRED")

    if (prediction.get("source_snapshot_id") != snapshot.get("source_snapshot_id")
            or _text(prediction.get("event_id")) != _text(snapshot.get("event_id"))
            or _text(prediction.get("player")) != _text(snapshot.get("player"))
            or _text(prediction.get("sport")).upper() != "NBA"
            or _text(snapshot.get("sport")).upper() != "NBA"
            or _text(prediction.get("stat_type")).upper() != _text(snapshot.get("stat_type")).upper()):
        return None, _hold("IDENTITY_UNRESOLVED", "NBA_EXACT_SNAPSHOT_IDENTITY_MISMATCH")

    line = settlement._finite_number(prediction.get("line"))
    snapshot_line = settlement._finite_number(snapshot.get("line"))
    if line is None or line < 0 or line != snapshot_line or _text(prediction.get("direction")).upper() not in {"MORE", "LESS"}:
        return None, _hold("IDENTITY_UNRESOLVED", "NBA_EXACT_LINE_OR_DIRECTION_INVALID")

    role = snapshot.get("role_status")
    ledger = snapshot.get("opportunity_ledger")
    if not isinstance(role, Mapping) or not isinstance(ledger, Mapping):
        return None, _hold("IDENTITY_UNRESOLVED", "NBA_ROLE_AND_OFFER_EVIDENCE_REQUIRED")
    # The collector may only settle a full-game, literal numeric threshold.
    period = _text(ledger.get("period") or role.get("period")).upper()
    if period != "FULL_GAME":
        return None, _hold("MODEL_OR_IDENTITY_UNSUPPORTED", "NBA_EXACT_FULL_GAME_PERIOD_REQUIRED")
    threshold = _text(ledger.get("literal_threshold") or ledger.get("offer_threshold") or role.get("literal_threshold"))
    if threshold and (threshold.endswith("+") or threshold.endswith("-")):
        return None, _hold("MODEL_OR_IDENTITY_UNSUPPORTED", "NBA_AT_LEAST_OFFER_ADAPTER_NOT_CERTIFIED")

    proof = role.get("source_identity")
    if not isinstance(proof, Mapping):
        return None, _hold("IDENTITY_UNRESOLVED", "NBA_VERIFIED_PREGAME_CROSS_PROVIDER_IDENTITY_REQUIRED")
    game_id = _text(proof.get("nba_game_id"))
    person_id = _text(proof.get("nba_person_id"))
    espn_event_id = _text(proof.get("espn_event_id"))
    team_code = _text(proof.get("team_tricode")).upper()
    names = proof.get("source_names")
    if (not re.fullmatch(r"[0-9]{10}", game_id)
            or not re.fullmatch(r"[1-9][0-9]{0,9}", person_id)
            or not re.fullmatch(r"espn-[0-9]{6,12}", espn_event_id)
            or not re.fullmatch(r"[A-Z]{2,4}", team_code)
            or not isinstance(names, list)
            or not {"ESPN_GAME_SUMMARY", "NBA_CDN_LIVE_BOXSCORE"}.issubset(set(names))):
        return None, _hold("IDENTITY_UNRESOLVED", "NBA_OFFICIAL_EVENT_PLAYER_CROSSWALK_INCOMPLETE")
    if (espn_event_id != _text(prediction.get("event_id"))
            or _text(proof.get("event_start_time")) == ""
            or _utc(proof.get("event_start_time")) != event_start
            or (role.get("official_game_id") is not None and _text(role["official_game_id"]) != game_id)
            or (role.get("player_id") is not None and _text(role["player_id"]) != person_id)):
        return None, _hold("IDENTITY_UNRESOLVED", "NBA_OFFICIAL_EVENT_PLAYER_CROSSWALK_CONTRADICTION")

    espn_captured = _utc(proof.get("espn_captured_at"))
    nba_captured = _utc(proof.get("nba_captured_at"))
    if (espn_captured is None or nba_captured is None
            or espn_captured >= event_start or nba_captured >= event_start
            or max(espn_captured, nba_captured) > captured_at):
        return None, _hold("IDENTITY_UNRESOLVED", "NBA_OFFICIAL_PREGAME_SOURCE_TIMESTAMPS_INVALID")

    return {
        "game_id": game_id, "person_id": person_id, "team_tricode": team_code,
        "event_start": event_start, "player": _name(prediction.get("player")),
    }, None


def settle_nba_scalar(
    prediction: Mapping[str, Any], snapshot: Mapping[str, Any], *,
    http_get: Callable[..., Any] = httpx.get,
    now: datetime | None = None,
) -> dict[str, Any]:
    """Return one exact immutable-forward NBA settlement or a typed hold."""
    now = (now or datetime.now(timezone.utc)).astimezone(timezone.utc)
    stat = _text(prediction.get("stat_type")).upper()
    if stat not in STAT_COMPONENTS:
        return _hold("MODEL_OR_IDENTITY_UNSUPPORTED", "NBA_EXACT_STAT_SETTLEMENT_UNSUPPORTED")

    crosswalk, failure = _identity(prediction, snapshot, now=now)
    if failure is not None:
        return failure
    assert crosswalk is not None
    source = f"{NBA_CDN_BOXSCORE_BASE}boxscore_{crosswalk['game_id']}.json"
    try:
        payload = settlement._request_json(source, http_get=http_get, headers=HEADERS)
    except Exception as exc:
        return _hold("OFFICIAL_SOURCE_UNAVAILABLE", "NBA_OFFICIAL_FINAL_BOXSCORE_UNAVAILABLE", error_type=type(exc).__name__)
    game = payload.get("game")
    if not isinstance(game, Mapping):
        return _hold("OFFICIAL_STAT_SOURCE_INVALID", "NBA_FINAL_BOXSCORE_GAME_SCHEMA_INVALID")
    if _text(game.get("gameId")) != crosswalk["game_id"]:
        return _hold("OFFICIAL_EVENT_ID_MISMATCH", "NBA_OFFICIAL_GAME_ID_CONTRADICTION")
    if str(game.get("gameStatus")) != "3":
        return _hold("NOT_FINAL", "NBA_OFFICIAL_GAME_NOT_FINAL", official_state=game.get("gameStatus"))
    if _utc(game.get("gameTimeUTC")) != crosswalk["event_start"]:
        return _hold("OFFICIAL_EVENT_ID_MISMATCH", "NBA_OFFICIAL_EVENT_START_CONTRADICTION")

    teams: dict[str, Mapping[str, Any]] = {}
    for side in ("homeTeam", "awayTeam"):
        team = game.get(side)
        if (not isinstance(team, Mapping)
                or not re.fullmatch(r"[A-Z]{2,4}", _text(team.get("teamTricode")).upper())
                or not isinstance(team.get("players"), list)):
            return _hold("OFFICIAL_STAT_SOURCE_INVALID", "NBA_FINAL_BOXSCORE_TEAM_SCHEMA_INVALID")
        teams[side] = team
    if _text(teams["homeTeam"]["teamTricode"]).upper() == _text(teams["awayTeam"]["teamTricode"]).upper():
        return _hold("OFFICIAL_EVENT_ID_MISMATCH", "NBA_FINAL_BOXSCORE_TEAMS_CONFLICT")
    chosen = [team for team in teams.values() if _text(team.get("teamTricode")).upper() == crosswalk["team_tricode"]]
    if len(chosen) != 1:
        return _hold("OFFICIAL_EVENT_ID_MISMATCH", "NBA_OFFICIAL_TEAM_CROSSWALK_MISMATCH")

    players = []
    for team in teams.values():
        for player in team["players"]:
            if not isinstance(player, Mapping):
                return _hold("OFFICIAL_STAT_SOURCE_INVALID", "NBA_FINAL_BOXSCORE_PLAYER_SCHEMA_INVALID")
            if _text(player.get("personId")) == crosswalk["person_id"]:
                players.append((team, player))
    if not players:
        # Absence is not reliable evidence of DNP. Avoid retrospective false voids.
        return _hold("OFFICIAL_PLAYER_STAT_ROW_MISSING", "NBA_EXACT_OFFICIAL_PLAYER_ROW_NOT_FOUND")
    if len(players) != 1:
        return _hold("OFFICIAL_PLAYER_EVENT_IDENTITY_AMBIGUOUS", "NBA_DUPLICATE_OFFICIAL_PLAYER_ROWS", match_n=len(players))
    team, player = players[0]
    if _text(team.get("teamTricode")).upper() != crosswalk["team_tricode"]:
        return _hold("OFFICIAL_PLAYER_EVENT_IDENTITY_CONTRADICTION", "NBA_OFFICIAL_PLAYER_TEAM_MISMATCH")
    actual_name = _name(player.get("name") or f"{player.get('firstName') or ''} {player.get('familyName') or ''}")
    if not actual_name or actual_name != crosswalk["player"]:
        return _hold("OFFICIAL_PLAYER_EVENT_IDENTITY_CONTRADICTION", "NBA_OFFICIAL_PLAYER_NAME_MISMATCH")
    played = _played(player.get("played"))
    if played is False:
        reason = _text(player.get("notPlayingReason") or player.get("notPlayingDescription"))
        if not reason:
            return _hold("OFFICIAL_STAT_MISSING", "NBA_EXPLICIT_DNP_REASON_REQUIRED")
        return {
            "status": "SETTLED_VOID_DNP",
            "outcome": settlement._settlement_result(
                prediction=prediction, actual_stat=None, source=source,
                now=now, void_reason="PLAYER_DNP",
            ),
            "can_execute": False,
        }
    if played is not True:
        return _hold("OFFICIAL_STAT_MISSING", "NBA_EXPLICIT_PLAYED_STATUS_REQUIRED")
    stats = player.get("statistics")
    if not isinstance(stats, Mapping):
        return _hold("OFFICIAL_STAT_MISSING", "NBA_OFFICIAL_PLAYER_STATS_OBJECT_REQUIRED")
    components = {name: _integer_stat(stats.get(name)) for name in STAT_COMPONENTS[stat]}
    if any(value is None for value in components.values()):
        return _hold("OFFICIAL_STAT_MISSING", "NBA_EXACT_STAT_COMPONENT_MISSING_OR_INVALID", stat_type=stat)

    actual = float(sum(value for value in components.values() if value is not None))
    return {
        "status": "SETTLED",
        "outcome": settlement._settlement_result(
            prediction=prediction, actual_stat=actual, source=source, now=now,
        ),
        "settlement_components": components,
        "official_event_id": crosswalk["game_id"],
        "official_player_id": crosswalk["person_id"],
        "settlement_source_kind": NBA_SETTLEMENT_SOURCE,
        "can_execute": False,
    }


__all__ = ["CAN_EXECUTE", "NBA_SUPPORTED", "STAT_COMPONENTS", "settle_nba_scalar"]
