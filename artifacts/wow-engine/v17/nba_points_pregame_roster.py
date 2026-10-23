"""NBA POINTS: cross-provider exact-event, current-player roster proof (read-only).

Two independently sourced, pregame JSON payloads: ESPN event summary and NBA
CDN liveData boxscore. A roster membership is NOT availability; the NBA feed
must affirm ACTIVE and have no not-playing reason. No synthetic role ledger,
market line, fitted model probability, persistence or wager authority.
"""
from __future__ import annotations

from datetime import datetime, timezone
import re
from typing import Any, Mapping

CAN_EXECUTE = False


class NBAPregameRosterError(ValueError):
    def __init__(self, code: str):
        self.code = code
        super().__init__(code)


def _aware(raw: Any, name: str) -> datetime:
    if not isinstance(raw, str) or not raw.strip():
        raise NBAPregameRosterError(f"NBA_ROSTER_{name}_MISSING")
    try:
        dt = datetime.fromisoformat(raw.replace("Z", "+00:00"))
    except ValueError as exc:
        raise NBAPregameRosterError(f"NBA_ROSTER_{name}_INVALID") from exc
    if dt.utcoffset() is None:
        raise NBAPregameRosterError(f"NBA_ROSTER_{name}_NAIVE")
    return dt.astimezone(timezone.utc)


def _name(raw: Any) -> str:
    return " ".join(str(raw or "").split()).casefold()


def _required(obj: Mapping[str, Any], key: str) -> str:
    value = obj.get(key)
    if not isinstance(value, (str, int)) or not str(value).strip():
        raise NBAPregameRosterError(f"NBA_ROSTER_{key.upper()}_MISSING")
    return str(value).strip()


def _team_key(raw: Any) -> str:
    value = str(raw or "").strip().upper()
    if not re.fullmatch(r"[A-Z]{2,4}", value):
        raise NBAPregameRosterError("NBA_ROSTER_TEAM_TRICODE_INVALID")
    return value


def verify_nba_points_current_roster(
    *,
    espn_event_id: str,
    nba_game_id: str,
    player_name: str,
    espn_summary: Mapping[str, Any],
    nba_boxscore: Mapping[str, Any],
    espn_captured_at: str,
    nba_captured_at: str,
    now: datetime,
) -> dict[str, Any]:
    """Join exact pregame event/team/player without guessing availability.

    Use this output as the roster input to build_nba_points_snapshot only
    after the separate current-role ledger and exact board quote exist.
    """
    if not re.fullmatch(r"(?:espn-)?[0-9]{6,12}", espn_event_id):
        raise NBAPregameRosterError("NBA_ROSTER_ESPN_EVENT_ID_INVALID")
    if not re.fullmatch(r"[0-9]{10}", nba_game_id):
        raise NBAPregameRosterError("NBA_ROSTER_NBA_GAME_ID_INVALID")
    if not _name(player_name):
        raise NBAPregameRosterError("NBA_ROSTER_PLAYER_NAME_MISSING")
    if now.utcoffset() is None:
        raise NBAPregameRosterError("NBA_ROSTER_NOW_NAIVE")
    now_utc = now.astimezone(timezone.utc)
    observed_espn = _aware(espn_captured_at, "ESPN_CAPTURE")
    observed_nba = _aware(nba_captured_at, "NBA_CAPTURE")
    if observed_espn > now_utc or observed_nba > now_utc:
        raise NBAPregameRosterError("NBA_ROSTER_CAPTURE_IN_FUTURE")

    header = espn_summary.get("header")
    if not isinstance(header, Mapping):
        raise NBAPregameRosterError("NBA_ROSTER_ESPN_HEADER_INVALID")
    event = espn_event_id.removeprefix("espn-")
    if str(header.get("id") or "") != event:
        raise NBAPregameRosterError("NBA_ROSTER_ESPN_EVENT_MISMATCH")
    competitions = header.get("competitions")
    if not isinstance(competitions, list) or len(competitions) != 1 or not isinstance(competitions[0], Mapping):
        raise NBAPregameRosterError("NBA_ROSTER_ESPN_COMPETITION_AMBIGUOUS")
    game = competitions[0]
    if str(game.get("id") or "") != event:
        raise NBAPregameRosterError("NBA_ROSTER_ESPN_COMPETITION_MISMATCH")
    kickoff = _aware(game.get("date"), "ESPN_KICKOFF")
    if kickoff <= now_utc or max(observed_espn, observed_nba) >= kickoff:
        raise NBAPregameRosterError("NBA_ROSTER_NOT_PREGAME")
    competitors = game.get("competitors")
    if not isinstance(competitors, list) or len(competitors) != 2:
        raise NBAPregameRosterError("NBA_ROSTER_ESPN_TEAMS_INVALID")
    espn_teams: dict[str, dict[str, str]] = {}
    for competitor in competitors:
        if not isinstance(competitor, Mapping) or not isinstance(competitor.get("team"), Mapping):
            raise NBAPregameRosterError("NBA_ROSTER_ESPN_TEAMS_INVALID")
        side = _required(competitor, "homeAway")
        team = competitor["team"]
        if side not in {"home", "away"} or side in espn_teams:
            raise NBAPregameRosterError("NBA_ROSTER_ESPN_TEAMS_INVALID")
        espn_teams[side] = {"id": _required(team, "id"), "tricode": _team_key(team.get("abbreviation"))}
    if set(espn_teams) != {"home", "away"} or espn_teams["home"]["tricode"] == espn_teams["away"]["tricode"]:
        raise NBAPregameRosterError("NBA_ROSTER_ESPN_TEAMS_INVALID")

    box_game = nba_boxscore.get("game")
    if not isinstance(box_game, Mapping) or str(box_game.get("gameId") or "") != nba_game_id:
        raise NBAPregameRosterError("NBA_ROSTER_NBA_GAME_ID_MISMATCH")
    if str(box_game.get("gameStatus") or "") != "1":
        raise NBAPregameRosterError("NBA_ROSTER_NBA_GAME_NOT_SCHEDULED")
    if _aware(box_game.get("gameTimeUTC"), "NBA_KICKOFF") != kickoff:
        raise NBAPregameRosterError("NBA_ROSTER_CROSS_PROVIDER_KICKOFF_MISMATCH")
    matches: list[tuple[str, Mapping[str, Any]]] = []
    for side in ("home", "away"):
        raw_team = box_game.get(f"{side}Team")
        if not isinstance(raw_team, Mapping) or _team_key(raw_team.get("teamTricode")) != espn_teams[side]["tricode"]:
            raise NBAPregameRosterError("NBA_ROSTER_CROSS_PROVIDER_TEAM_MISMATCH")
        players = raw_team.get("players")
        if not isinstance(players, list):
            raise NBAPregameRosterError("NBA_ROSTER_NBA_PLAYERS_MISSING")
        for person in players:
            if not isinstance(person, Mapping):
                raise NBAPregameRosterError("NBA_ROSTER_NBA_PLAYER_SCHEMA_INVALID")
            name = _name(person.get("name") or f"{person.get('firstName') or ''} {person.get('familyName') or ''}")
            if name == _name(player_name):
                matches.append((side, person))
    if len(matches) != 1:
        raise NBAPregameRosterError("NBA_ROSTER_PLAYER_MISSING_OR_AMBIGUOUS")
    side, player = matches[0]
    nba_player_id = _required(player, "personId")
    if not re.fullmatch(r"[1-9][0-9]{0,9}", nba_player_id):
        raise NBAPregameRosterError("NBA_ROSTER_NBA_PLAYER_ID_INVALID")
    if str(player.get("status") or "").strip().upper() != "ACTIVE":
        raise NBAPregameRosterError("NBA_ROSTER_ACTIVE_STATUS_NOT_AFFIRMED")
    if str(player.get("notPlayingReason") or "").strip() or str(player.get("notPlayingDescription") or "").strip():
        raise NBAPregameRosterError("NBA_ROSTER_NOT_PLAYING_REASON_PRESENT")

    return {
        "status": "NBA_CURRENT_ROSTER_ACTIVE_SOURCE_VERIFIED_ROLE_NOT_PROVEN",
        "roster": {
            "event_id": f"espn-{event}",
            "player_id": nba_player_id,
            "team_id": f"espn-{espn_teams[side]['id']}",
            "player": " ".join(player_name.split()),
            "availability": "ACTIVE",
            "captured_at": max(observed_espn, observed_nba).isoformat(),
        },
        "source_identity": {
            "espn_event_id": f"espn-{event}",
            "nba_game_id": nba_game_id,
            "nba_person_id": nba_player_id,
            "espn_team_id": espn_teams[side]["id"],
            "team_tricode": espn_teams[side]["tricode"],
            "event_start_time": kickoff.isoformat(),
            "espn_captured_at": observed_espn.isoformat(),
            "nba_captured_at": observed_nba.isoformat(),
            "source_names": ["ESPN_GAME_SUMMARY", "NBA_CDN_LIVE_BOXSCORE"],
        },
        "role_ledger_ready": False,
        "exact_board_line_present": False,
        "probability_publishable": False,
        "write_performed": False,
        "can_execute": False,
    }


__all__ = ["CAN_EXECUTE", "NBAPregameRosterError", "verify_nba_points_current_roster"]
