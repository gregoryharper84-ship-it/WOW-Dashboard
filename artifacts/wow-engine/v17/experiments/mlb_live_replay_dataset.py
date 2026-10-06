"""Leakage-safe MLB in-play replay rows from the official Stats API event tree.

Class C research input only. This module reconstructs point-in-time sporting
state after completed plate appearances from a final official game feed. Final
game outcome is attached only as the supervised label; final score and future
plays are never copied into the feature payload.

This module does not fit, calibrate, rank, publish, or execute anything.
"""
from __future__ import annotations

from dataclasses import dataclass
from typing import Any, Mapping

CAN_EXECUTE = False
PROBABILITY_PUBLISHABLE = False
AUTOMATIC_PROMOTION = False
SOURCE_PROVIDER = "MLB_STATS_API_OFFICIAL_GAME_FEED"
STATE_SCHEMA_VERSION = "MLB_LIVE_REPLAY_STATE_V1"


class MLBReplayError(RuntimeError):
    """Typed fail-closed replay construction error."""

    def __init__(self, code: str, message: str):
        super().__init__(message)
        self.code = code


@dataclass(frozen=True)
class MLBReplayReceipt:
    official_event_id: str
    row_count: int
    final_outcome: str
    source_payload_hash: str
    probability_publishable: bool = False
    automatic_promotion: bool = False
    can_execute: bool = False


def _mapping(value: Any) -> Mapping[str, Any]:
    return value if isinstance(value, Mapping) else {}


def _integer(value: Any, *, code: str, field: str) -> int:
    if isinstance(value, bool):
        raise MLBReplayError(code, field)
    try:
        number = float(value)
    except (TypeError, ValueError) as exc:
        raise MLBReplayError(code, field) from exc
    if not number.is_integer():
        raise MLBReplayError(code, field)
    return int(number)


def _optional_id(value: Any) -> str | None:
    text = str(value or "").strip()
    return text or None


def _final_outcome(feed: Mapping[str, Any]) -> tuple[str, int, int]:
    game_data = _mapping(feed.get("gameData"))
    status = _mapping(game_data.get("status"))
    if str(status.get("abstractGameState") or "").casefold() != "final":
        raise MLBReplayError(
            "MLB_LIVE_REPLAY_EVENT_NOT_FINAL",
            "historical replay requires a final official game feed",
        )

    live = _mapping(feed.get("liveData"))
    linescore = _mapping(live.get("linescore"))
    teams = _mapping(linescore.get("teams"))
    home = _mapping(teams.get("home"))
    away = _mapping(teams.get("away"))
    home_runs = _integer(
        home.get("runs"),
        code="MLB_LIVE_REPLAY_FINAL_SCORE_INVALID",
        field="home_runs",
    )
    away_runs = _integer(
        away.get("runs"),
        code="MLB_LIVE_REPLAY_FINAL_SCORE_INVALID",
        field="away_runs",
    )
    if home_runs == away_runs:
        raise MLBReplayError(
            "MLB_LIVE_REPLAY_FINAL_TIE_UNSUPPORTED",
            "final MLB training label must resolve to one winner",
        )
    return ("HOME_WIN" if home_runs > away_runs else "AWAY_WIN"), home_runs, away_runs


def _team_identity(feed: Mapping[str, Any]) -> tuple[str, str]:
    game_data = _mapping(feed.get("gameData"))
    teams = _mapping(game_data.get("teams"))
    home = str(_mapping(teams.get("home")).get("name") or "").strip()
    away = str(_mapping(teams.get("away")).get("name") or "").strip()
    if not home or not away or home == away:
        raise MLBReplayError(
            "MLB_LIVE_REPLAY_EVENT_IDENTITY_INVALID",
            "official game feed must contain distinct home and away teams",
        )
    return home, away


def _runner_id(row: Mapping[str, Any]) -> str | None:
    details = _mapping(row.get("details"))
    runner = _mapping(details.get("runner"))
    return _optional_id(runner.get("id"))


def _normalize_base(value: Any) -> str | None:
    text = str(value or "").strip().upper()
    aliases = {
        "1B": "1B",
        "FIRST": "1B",
        "2B": "2B",
        "SECOND": "2B",
        "3B": "3B",
        "THIRD": "3B",
    }
    return aliases.get(text)


def _apply_runner_movements(
    runners: Mapping[str, str],
    play: Mapping[str, Any],
) -> dict[str, str]:
    """Apply only movements present in this completed plate appearance."""
    updated = dict(runners)
    raw_rows = play.get("runners")
    rows = raw_rows if isinstance(raw_rows, list) else []
    for raw in rows:
        if not isinstance(raw, Mapping):
            continue
        runner_id = _runner_id(raw)
        if not runner_id:
            continue
        movement = _mapping(raw.get("movement"))
        start = _normalize_base(movement.get("start"))
        end = _normalize_base(movement.get("end"))
        is_out = bool(movement.get("isOut"))

        if start and updated.get(runner_id) == start:
            updated.pop(runner_id, None)
        else:
            # A runner may advance from a base captured by a prior play even
            # when the provider omits/renames the start token. Identity is the
            # authoritative key, so remove its prior location before re-adding.
            updated.pop(runner_id, None)

        if not is_out and end:
            updated[runner_id] = end
    return updated


def _base_occupancy(runners: Mapping[str, str]) -> dict[str, bool]:
    occupied = set(runners.values())
    return {
        "first": "1B" in occupied,
        "second": "2B" in occupied,
        "third": "3B" in occupied,
    }


def _play_score(play: Mapping[str, Any]) -> tuple[int, int]:
    result = _mapping(play.get("result"))
    return (
        _integer(
            result.get("homeScore"),
            code="MLB_LIVE_REPLAY_PLAY_SCORE_INVALID",
            field="homeScore",
        ),
        _integer(
            result.get("awayScore"),
            code="MLB_LIVE_REPLAY_PLAY_SCORE_INVALID",
            field="awayScore",
        ),
    )


def _play_sequence(play: Mapping[str, Any], fallback: int) -> int:
    about = _mapping(play.get("about"))
    raw = about.get("atBatIndex")
    if raw is None:
        return fallback
    return _integer(
        raw,
        code="MLB_LIVE_REPLAY_SEQUENCE_INVALID",
        field="atBatIndex",
    )


def _play_state(
    play: Mapping[str, Any],
    *,
    runners: Mapping[str, str],
    sequence: int,
) -> dict[str, Any]:
    about = _mapping(play.get("about"))
    count = _mapping(play.get("count"))
    matchup = _mapping(play.get("matchup"))
    pitcher = _mapping(matchup.get("pitcher"))
    batter = _mapping(matchup.get("batter"))

    inning = _integer(
        about.get("inning"),
        code="MLB_LIVE_REPLAY_INNING_INVALID",
        field="inning",
    )
    outs = _integer(
        count.get("outs"),
        code="MLB_LIVE_REPLAY_OUTS_INVALID",
        field="outs",
    )
    if inning < 1 or outs < 0 or outs > 3:
        raise MLBReplayError(
            "MLB_LIVE_REPLAY_GAME_STATE_INVALID",
            f"inning={inning}:outs={outs}",
        )

    half_raw = str(about.get("halfInning") or "").strip().casefold()
    if half_raw == "top":
        half = "TOP"
        offense_home = False
    elif half_raw == "bottom":
        half = "BOTTOM"
        offense_home = True
    else:
        raise MLBReplayError(
            "MLB_LIVE_REPLAY_HALF_INNING_INVALID",
            str(about.get("halfInning") or ""),
        )

    home_score, away_score = _play_score(play)
    base_state = _base_occupancy(runners)
    return {
        "state_schema_version": STATE_SCHEMA_VERSION,
        "plate_appearance_index": sequence,
        "inning": inning,
        "half": half,
        "outs": outs,
        "home_score": home_score,
        "away_score": away_score,
        "home_score_diff": home_score - away_score,
        "offense_home": offense_home,
        "base_occupancy": base_state,
        "batter_id": _optional_id(batter.get("id")),
        "pitcher_id": _optional_id(pitcher.get("id")),
        "is_extra_inning": inning > 9,
        "state_timestamp": str(about.get("endTime") or about.get("startTime") or "").strip()
        or None,
    }


def build_mlb_live_replay_rows(
    feed: Mapping[str, Any],
    *,
    official_event_id: str,
    source_payload_hash: str,
) -> tuple[tuple[dict[str, Any], ...], MLBReplayReceipt]:
    """Build chronological research rows from one final official MLB feed."""
    event_id = str(official_event_id or "").strip()
    payload_hash = str(source_payload_hash or "").strip().lower()
    if not event_id:
        raise MLBReplayError(
            "MLB_LIVE_REPLAY_EVENT_ID_REQUIRED",
            "official_event_id is required",
        )
    if len(payload_hash) != 64 or any(ch not in "0123456789abcdef" for ch in payload_hash):
        raise MLBReplayError(
            "MLB_LIVE_REPLAY_SOURCE_HASH_INVALID",
            "source_payload_hash must be a lowercase SHA-256 hex digest",
        )

    final_outcome, _final_home, _final_away = _final_outcome(feed)
    home_team, away_team = _team_identity(feed)

    live = _mapping(feed.get("liveData"))
    plays_node = _mapping(live.get("plays"))
    raw_plays = plays_node.get("allPlays")
    if not isinstance(raw_plays, list) or not raw_plays:
        raise MLBReplayError(
            "MLB_LIVE_REPLAY_EVENT_TREE_MISSING",
            "official final feed contained no play tree",
        )

    rows: list[dict[str, Any]] = []
    runners: dict[str, str] = {}
    previous_sequence = -1
    for fallback, raw_play in enumerate(raw_plays):
        if not isinstance(raw_play, Mapping):
            continue
        about = _mapping(raw_play.get("about"))
        if about.get("isComplete") is False:
            continue

        sequence = _play_sequence(raw_play, fallback)
        if sequence <= previous_sequence:
            raise MLBReplayError(
                "MLB_LIVE_REPLAY_SEQUENCE_NOT_STRICT",
                f"previous={previous_sequence}:current={sequence}",
            )
        previous_sequence = sequence

        runners = _apply_runner_movements(runners, raw_play)
        state = _play_state(raw_play, runners=runners, sequence=sequence)

        # End-of-half snapshots must not carry stranded bases into the next half.
        if state["outs"] == 3:
            state["base_occupancy"] = {
                "first": False,
                "second": False,
                "third": False,
            }
            runners = {}

        rows.append(
            {
                "official_event_id": event_id,
                "sport": "MLB",
                "home_team": home_team,
                "away_team": away_team,
                "source_provider": SOURCE_PROVIDER,
                "source_payload_hash": payload_hash,
                "features": state,
                "label": final_outcome,
                "probability_publishable": False,
                "automatic_promotion": False,
                "can_execute": False,
            }
        )

    if not rows:
        raise MLBReplayError(
            "MLB_LIVE_REPLAY_NO_COMPLETE_STATES",
            "official event tree yielded no complete replay states",
        )

    receipt = MLBReplayReceipt(
        official_event_id=event_id,
        row_count=len(rows),
        final_outcome=final_outcome,
        source_payload_hash=payload_hash,
    )
    return tuple(rows), receipt


__all__ = [
    "AUTOMATIC_PROMOTION",
    "CAN_EXECUTE",
    "MLBReplayError",
    "MLBReplayReceipt",
    "PROBABILITY_PUBLISHABLE",
    "SOURCE_PROVIDER",
    "STATE_SCHEMA_VERSION",
    "build_mlb_live_replay_rows",
]
