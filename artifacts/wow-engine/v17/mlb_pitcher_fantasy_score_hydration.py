"""Official MLB evidence hydration for Pitcher Fantasy Score research candidates.

This adapter exists only to build auditable pregame evidence for the frozen
MLB_PITCHER_FANTASY_SCORE candidate lane.  It never supplies a model
probability, calibration result, lower bound, promotion flag, or execution
authority.

The historical target is computed from official MLB game-log components under
the exact verified PrizePicks profile frozen in the fitted candidate artifact:

    6*W + 3*K - 3*ER + 1*OUT + 4*QS

A quality start is derived deterministically as >=18 recorded outs and <=3
earned runs.  No missing result component is imputed.
"""
from __future__ import annotations

from datetime import datetime, timezone
from typing import Any, Callable, Optional

import httpx

from prop_auto_hydration import (
    AUTO_HYDRATION_EVIDENCE_VERSION,
    MLB_STATS_API_BASE,
    MIN_STARTS,
    PropAutoHydrationError,
    _int,
    _outs_from_ip,
    _request_json,
    _resolve_player_id,
    _schedule_context,
)
from prop_hydration_resilience import fetch_cross_season_pitching_splits

PROVIDER_ID = "MLB_STATS_API_OFFICIAL_PITCHER_FANTASY_SCORE_V1"
STAT_TYPE = "PITCHER_FANTASY_SCORE"
SCORING_PROFILE_ID = "PRIZEPICKS_MLB_PITCHER_FANTASY_SCORE_2025_07_11_VERIFIED_V1"
SCORING_WEIGHTS = {
    "wins": 6.0,
    "strikeouts": 3.0,
    "earned_runs": -3.0,
    "outs_recorded": 1.0,
    "quality_starts": 4.0,
}


def _event_start(value: str) -> datetime:
    try:
        parsed = datetime.fromisoformat(str(value).replace("Z", "+00:00"))
    except (TypeError, ValueError) as exc:
        raise PropAutoHydrationError(
            "PROP_EVENT_START_INVALID",
            "event_start_time must be timezone-aware ISO 8601",
        ) from exc
    if parsed.utcoffset() is None:
        raise PropAutoHydrationError(
            "PROP_EVENT_START_INVALID",
            "event_start_time must include a timezone",
        )
    return parsed.astimezone(timezone.utc)


def _fantasy_score(*, win: int, strikeouts: int, earned_runs: int, outs: int, quality_start: int) -> float:
    return float(
        SCORING_WEIGHTS["wins"] * win
        + SCORING_WEIGHTS["strikeouts"] * strikeouts
        + SCORING_WEIGHTS["earned_runs"] * earned_runs
        + SCORING_WEIGHTS["outs_recorded"] * outs
        + SCORING_WEIGHTS["quality_starts"] * quality_start
    )


def hydrate_mlb_pitcher_fantasy_score_evidence(
    *,
    player: str,
    event_start_time: str,
    http_get: Callable[..., Any] = httpx.get,
    now: Optional[datetime] = None,
    source_capture_timestamp: Optional[str] = None,
    source_label: str = "NORMALIZED_PICK_REQUEST",
) -> dict[str, Any]:
    normalized_player = " ".join(str(player or "").strip().split())
    if not normalized_player:
        raise PropAutoHydrationError(
            "PROP_PLAYER_IDENTITY_REQUIRED",
            "player is required for prop hydration",
        )

    captured_at = (now or datetime.now(timezone.utc)).astimezone(timezone.utc)
    event_start = _event_start(event_start_time)
    if event_start <= captured_at:
        raise PropAutoHydrationError(
            "EVENT_ALREADY_STARTED",
            "pregame evidence cannot be hydrated after event start",
        )

    player_id, official_name = _resolve_player_id(
        normalized_player,
        event_start=event_start,
        http_get=http_get,
    )
    schedule = _schedule_context(player_id, event_start=event_start, http_get=http_get)
    season_splits, seasons_queried = fetch_cross_season_pitching_splits(
        player_id,
        event_start=event_start,
        request_json=_request_json,
        http_get=http_get,
        mlb_stats_api_base=MLB_STATS_API_BASE,
    )

    parsed: list[tuple[str, dict[str, Any], float]] = []
    for season, split in season_splits:
        stat = split.get("stat") if isinstance(split, dict) else None
        if not isinstance(stat, dict) or _int(stat.get("gamesStarted")) < 1:
            continue
        date_value = str(split.get("date") or "")
        try:
            game_date = datetime.fromisoformat(date_value).date()
        except ValueError:
            continue
        if game_date >= event_start.date():
            continue

        try:
            outs = _outs_from_ip(stat.get("inningsPitched"))
        except PropAutoHydrationError:
            continue
        strikeouts = _int(stat.get("strikeOuts"), default=-1)
        earned_runs = _int(stat.get("earnedRuns"), default=-1)
        wins = _int(stat.get("wins"), default=-1)
        if strikeouts < 0 or earned_runs < 0 or wins not in {0, 1}:
            continue
        quality_start = int(outs >= 18 and earned_runs <= 3)
        score = _fantasy_score(
            win=wins,
            strikeouts=strikeouts,
            earned_runs=earned_runs,
            outs=outs,
            quality_start=quality_start,
        )
        opponent = split.get("opponent") if isinstance(split.get("opponent"), dict) else {}
        parsed.append(
            (
                date_value,
                {
                    "date": date_value,
                    "season": season,
                    "opponent": opponent.get("abbreviation") or opponent.get("name") or "UNKNOWN",
                    "ip": str(stat.get("inningsPitched")),
                    "outs": outs,
                    "outs_recorded": outs,
                    "bf": _int(stat.get("battersFaced")),
                    "so": strikeouts,
                    "strikeouts": strikeouts,
                    "bb": _int(stat.get("baseOnBalls")),
                    "er": earned_runs,
                    "earned_runs": earned_runs,
                    "wins": wins,
                    "quality_starts": quality_start,
                    "fantasy_score": score,
                    "scoring_profile_id": SCORING_PROFILE_ID,
                },
                score,
            )
        )

    parsed.sort(key=lambda item: item[0], reverse=True)
    recent = parsed[:MIN_STARTS]
    if len(recent) < MIN_STARTS:
        raise PropAutoHydrationError(
            "MLB_PITCHER_FANTASY_SCORE_HISTORY_INSUFFICIENT",
            "fewer than ten official prior starts contained every required Pitcher Fantasy Score component",
            detail={
                "starts_found": len(recent),
                "required": MIN_STARTS,
                "seasons_queried": seasons_queried,
                "scoring_profile_id": SCORING_PROFILE_ID,
            },
        )

    game_log = [score for _, _, score in recent]
    box_score_log = [row for _, row, _ in recent]
    selected_seasons = sorted({int(row["season"]) for row in box_score_log}, reverse=True)
    timestamp = captured_at.isoformat()
    source_timestamps = {
        "MLB_STATS_API_PLAYER_IDENTITY": timestamp,
        "MLB_STATS_API_PITCHING_GAME_LOG": timestamp,
        "MLB_STATS_API_SCHEDULE_PROBABLE_PITCHER": timestamp,
        "PRIZEPICKS_SCORING_PROFILE_FROZEN": timestamp,
    }
    if len(selected_seasons) > 1:
        source_timestamps["MLB_STATS_API_CROSS_SEASON_HISTORY"] = timestamp
    if source_capture_timestamp:
        source_timestamps[f"INPUT_CAPTURE_{str(source_label).strip().upper()}"] = source_capture_timestamp

    return {
        "captured_at": timestamp,
        "game_log": game_log,
        "box_score_log": box_score_log,
        "role_status": {
            "status": schedule["starter_status"],
            "role": "STARTING_PITCHER",
            "confirmation_strength": "OFFICIAL_PROBABLE_PITCHER",
            "official_player_name": official_name,
            "team": schedule["team"],
            "opponent": schedule["opponent"],
            "venue": schedule["venue"],
            "official_game_pk": schedule["official_game_pk"],
            "official_game_date": schedule["official_game_date"],
            "schedule_status": schedule["schedule_status"],
            "source": "MLB StatsAPI official schedule/probablePitcher",
        },
        "role_timestamp": timestamp,
        "opportunity_ledger": {
            "status": "READY",
            "game_log_stat": "pitcher fantasy score",
            "box_score_alignment": "1:1",
            "regular_season_prior_starts": len(box_score_log),
            "starter_confirmation": "OFFICIAL_PROBABLE_PITCHER",
            "history_seasons_used": selected_seasons,
            "history_selection": "MOST_RECENT_OFFICIAL_STARTS_NO_IMPUTATION",
            "scoring_profile_id": SCORING_PROFILE_ID,
            "scoring_weights": dict(SCORING_WEIGHTS),
        },
        "source_timestamps": source_timestamps,
        "evidence_version": AUTO_HYDRATION_EVIDENCE_VERSION,
        "rate_provenance": (
            "MLB StatsAPI official pitching gameLog; exact frozen PrizePicks pitcher Fantasy Score formula; "
            "quality start derived from official outs/ER; no history or probability imputed"
        ),
    }


__all__ = [
    "PROVIDER_ID",
    "SCORING_PROFILE_ID",
    "SCORING_WEIGHTS",
    "STAT_TYPE",
    "hydrate_mlb_pitcher_fantasy_score_evidence",
]
