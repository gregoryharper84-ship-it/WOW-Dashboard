"""Pure NHL official box-score -> player-game sporting-outcome research adapter.

Uses caller-supplied GET /v1/gamecenter/{id}/boxscore JSON only. This module
never fetches, stores, scores, authorizes training, or invents pregame lines.
NHL_PUBLIC_API is LICENSE_REVIEW_REQUIRED in WOW's historical manifest.
"""
from __future__ import annotations

from datetime import datetime, timezone
from hashlib import sha256
import json
import re
from typing import Any, Mapping

SCHEMA = "WOW_NHL_OFFICIAL_PLAYER_BOX_OUTCOME_RESEARCH_V1"
SKATER_STATS = {"GOALS": "goals", "ASSISTS": "assists",
                "POINTS": "points", "SHOTS_ON_GOAL": "sog"}
GOALIE_STATS = {"GOALIE_SAVES": "saves"}


class NHLPlayerBoxResearchHold(ValueError):
    def __init__(self, code: str):
        self.code = code
        super().__init__(code)


def require(ok: bool, code: str) -> None:
    if not ok:
        raise NHLPlayerBoxResearchHold(code)


def aware(s: Any) -> datetime:
    try:
        require(isinstance(s, str), "NHL_PLAYER_BOX_TIMESTAMP_INVALID")
        x = datetime.fromisoformat(s.replace("Z", "+00:00"))
    except (TypeError, ValueError) as ex:
        raise NHLPlayerBoxResearchHold("NHL_PLAYER_BOX_TIMESTAMP_INVALID") from ex
    require(x.utcoffset() is not None, "NHL_PLAYER_BOX_TIMESTAMP_INVALID")
    return x.astimezone(timezone.utc)


def natural(value: Any) -> int:
    require(type(value) is int and 0 <= value <= 1000,
            "NHL_PLAYER_BOX_STAT_INVALID")
    return value


def normalize_nhl_player_box_outcomes(
    payload: Mapping[str, Any], *, expected_game_id: str,
    expected_event_start_utc: str, retrieved_at: str,
) -> dict[str, Any]:
    """Outcome-only projection; call only with a full official final boxscore."""
    require(isinstance(payload, Mapping), "NHL_PLAYER_BOX_SCHEMA_INVALID")
    require(isinstance(expected_game_id, str)
            and bool(re.fullmatch(r"\d{10}", expected_game_id))
            and expected_game_id[4:6] == "02"
            and str(payload.get("id")) == expected_game_id,
            "NHL_PLAYER_BOX_IDENTITY_MISMATCH")
    try:
        season = payload["season"]
        home = payload["homeTeam"]
        away = payload["awayTeam"]
        stats = payload["playerByGameStats"]
    except KeyError as ex:
        raise NHLPlayerBoxResearchHold("NHL_PLAYER_BOX_SCHEMA_INVALID") from ex
    require(type(season) is int
            and str(season) == str(int(expected_game_id[:4])) + str(int(expected_game_id[:4]) + 1)
            and payload.get("gameType") == 2
            and payload.get("gameState") in ("FINAL", "OFF"),
            "NHL_PLAYER_BOX_NOT_FINAL_REGULAR_SEASON")
    require(isinstance(home, Mapping) and isinstance(away, Mapping)
            and isinstance(stats, Mapping), "NHL_PLAYER_BOX_SCHEMA_INVALID")
    home_abbr, away_abbr = home.get("abbrev"), away.get("abbrev")
    require(all(isinstance(v, str) and re.fullmatch(r"[A-Z]{3}", v)
                for v in (home_abbr, away_abbr))
            and home_abbr != away_abbr, "NHL_PLAYER_BOX_IDENTITY_MISMATCH")
    start, retrieved = aware(expected_event_start_utc), aware(retrieved_at)
    require(retrieved > start, "NHL_PLAYER_BOX_NOT_POSTGAME")
    # Source time may not be present in all official boxscore versions.
    if payload.get("startTimeUTC") is not None:
        require(aware(payload["startTimeUTC"]) == start,
                "NHL_PLAYER_BOX_IDENTITY_MISMATCH")
    outcomes: list[dict[str, Any]] = []
    seen_players: set[str] = set()
    for side, team, opponent in (("homeTeam", home_abbr, away_abbr),
                                 ("awayTeam", away_abbr, home_abbr)):
        block = stats.get(side)
        require(isinstance(block, Mapping), "NHL_PLAYER_BOX_SCHEMA_INVALID")
        for group in ("forwards", "defense", "goalies"):
            players = block.get(group)
            require(isinstance(players, list), "NHL_PLAYER_BOX_SCHEMA_INVALID")
            for item in players:
                require(isinstance(item, Mapping)
                        and type(item.get("playerId")) is int
                        and item["playerId"] > 0,
                        "NHL_PLAYER_BOX_PLAYER_ID_INVALID")
                player_id = str(item["playerId"])
                require(player_id not in seen_players,
                        "NHL_PLAYER_BOX_DUPLICATE_PLAYER")
                seen_players.add(player_id)
                fields = GOALIE_STATS if group == "goalies" else SKATER_STATS
                values = {stat: natural(item.get(field))
                          for stat, field in fields.items()}
                if group != "goalies":
                    require(values["POINTS"] == values["GOALS"] + values["ASSISTS"],
                            "NHL_PLAYER_BOX_STAT_CONTRADICTION")
                else:
                    against = natural(item.get("shotsAgainst"))
                    require(values["GOALIE_SAVES"] <= against,
                            "NHL_PLAYER_BOX_STAT_CONTRADICTION")
                for stat_type, val in values.items():
                    outcomes.append({
                        "sport": "NHL",
                        "official_event_id_claimed": expected_game_id,
                        "official_player_id_claimed": player_id,
                        "team": team, "opponent": opponent,
                        "stat_type": stat_type, "actual_value": val,
                        "event_start_utc_claimed": start.isoformat(),
                        "historical_closing_line": None, "historical_odds": None,
                        "historical_book": None,
                        "historical_market_timestamp": None,
                        "outcome_source_status": "OFFICIAL_BOX_RESEARCH_UNVERIFIED_ENTITLEMENT",
                        "can_execute": False,
                    })
    require(bool(outcomes), "NHL_PLAYER_BOX_NO_PLAYERS")
    sha = sha256(json.dumps(payload, sort_keys=True, separators=(",", ":"),
                            ensure_ascii=False, allow_nan=False).encode("utf-8")).hexdigest()
    return {
        "schema": SCHEMA,
        "status": "NHL_PLAYER_BOX_OUTCOME_RESEARCH_ONLY",
        "sport": "NHL", "source_provider": "NHL_PUBLIC_WEB_API",
        "source_url": f"https://api-web.nhle.com/v1/gamecenter/{expected_game_id}/boxscore",
        "official_event_id_claimed": expected_game_id,
        "event_start_utc_claimed": start.isoformat(),
        "event_start_verified_by_box": payload.get("startTimeUTC") is not None,
        "retrieved_at": retrieved.isoformat(),
        "source_payload_sha256": sha,
        "player_count": len(seen_players), "outcome_count": len(outcomes),
        "rows": outcomes,
        "source_rights_verified": False,
        "historical_training_authorized": False,
        "historical_market_line_verified": False,
        "probability_publishable": False,
        "can_execute": False,
    }
