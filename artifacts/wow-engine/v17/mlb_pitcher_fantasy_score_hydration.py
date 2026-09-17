"""Official MLB evidence hydration for the Pitcher Fantasy Score research lane.

This module creates only immutable pregame evidence. It does not calibrate,
publish, rank, approve, or execute a wager. Fantasy Score is reconstructed from
the verified PrizePicks pitcher scoring profile used by the fitted candidate:

    6*W + 4*QS + 3*K + outs - 3*ER

The output is compatible with ``RawPropEvidence`` and remains research-only
until the governed Fantasy Score calibration/certification lifecycle promotes an
exact artifact. ``can_execute`` is never granted here.
"""
from __future__ import annotations

from datetime import datetime, timezone
from typing import Any, Callable, Optional

import httpx

from prop_auto_hydration import (
    AUTO_HYDRATION_EVIDENCE_VERSION,
    AUTO_HYDRATION_PROVIDER,
    MLB_STATS_API_BASE,
    MIN_STARTS,
    PropAutoHydrationError,
    _aware,
    _int,
    _outs_from_ip,
    _request_json,
    _resolve_player_id,
    _schedule_context,
)
from prop_hydration_resilience import fetch_cross_season_pitching_splits

STAT_TYPE = "PITCHER_FANTASY_SCORE"
SCORING_PROFILE_ID = "PRIZEPICKS_MLB_PITCHER_FANTASY_SCORE_2025_07_11_VERIFIED_V1"


def _fantasy_score(*, win: int, quality_start: int, strikeouts: int, outs: int, earned_runs: int) -> float:
    return float(6 * win + 4 * quality_start + 3 * strikeouts + outs - 3 * earned_runs)


def hydrate_mlb_pitcher_fantasy_score_evidence(
    *,
    player: str,
    event_start_time: str,
    http_get: Callable[..., Any] = httpx.get,
    now: Optional[datetime] = None,
    source_capture_timestamp: Optional[str] = None,
    source_label: str = "NORMALIZED_PICK_REQUEST",
) -> dict[str, Any]:
    """Return official pregame L10 pitcher Fantasy Score evidence."""
    normalized_player = " ".join(str(player or "").strip().split())
    if not normalized_player:
        raise PropAutoHydrationError("PROP_PLAYER_IDENTITY_REQUIRED", "player is required for prop hydration")

    captured_at = (now or datetime.now(timezone.utc)).astimezone(timezone.utc)
    event_start = _aware(event_start_time)
    if event_start <= captured_at:
        raise PropAutoHydrationError("EVENT_ALREADY_STARTED", "pregame evidence cannot be hydrated after event start")

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

    parsed: list[tuple[str, dict[str, Any]]] = []
    for split_season, split in season_splits:
        if not isinstance(split, dict):
            continue
        stat = split.get("stat") if isinstance(split.get("stat"), dict) else None
        if not isinstance(stat, dict) or _int(stat.get("gamesStarted")) < 1:
            continue
        date_value = str(split.get("date") or "")
        try:
            game_date = datetime.fromisoformat(date_value).date()
        except ValueError:
            continue
        if game_date >= event_start.date():
            continue

        strikeouts = _int(stat.get("strikeOuts"), default=-1)
        earned_runs = _int(stat.get("earnedRuns"), default=-1)
        if strikeouts < 0 or earned_runs < 0:
            continue
        ip = stat.get("inningsPitched")
        outs = _outs_from_ip(ip)
        win = 1 if _int(stat.get("wins")) > 0 else 0
        quality_start = 1 if outs >= 18 and earned_runs <= 3 else 0
        fantasy_score = _fantasy_score(
            win=win,
            quality_start=quality_start,
            strikeouts=strikeouts,
            outs=outs,
            earned_runs=earned_runs,
        )
        opponent = split.get("opponent") if isinstance(split.get("opponent"), dict) else {}
        parsed.append((date_value, {
            "date": date_value,
            "season": split_season,
            "opponent": opponent.get("abbreviation") or opponent.get("name") or "UNKNOWN",
            "ip": str(ip),
            "outs": outs,
            "outs_recorded": outs,
            "bf": _int(stat.get("battersFaced")),
            "so": strikeouts,
            "strikeouts": strikeouts,
            "bb": _int(stat.get("baseOnBalls")),
            "er": earned_runs,
            "earned_runs": earned_runs,
            "wins": win,
            "quality_starts": quality_start,
            "fantasy_score": fantasy_score,
        }))

    parsed.sort(key=lambda item: item[0], reverse=True)
    recent = [row for _, row in parsed[:MIN_STARTS]]
    if len(recent) < MIN_STARTS:
        raise PropAutoHydrationError(
            "MLB_RECENT_STARTS_INSUFFICIENT",
            "fewer than ten official regular-season starts were available across the supported history window",
            detail={
                "starts_found": len(recent),
                "required": MIN_STARTS,
                "seasons_queried": seasons_queried,
                "stat_type": STAT_TYPE,
            },
        )

    timestamp = captured_at.isoformat()
    selected_seasons = sorted({int(row.get("season", event_start.year)) for row in recent}, reverse=True)
    source_timestamps = {
        "MLB_STATS_API_PLAYER_IDENTITY": timestamp,
        "MLB_STATS_API_PITCHING_GAME_LOG": timestamp,
        "MLB_STATS_API_SCHEDULE_PROBABLE_PITCHER": timestamp,
        "FANTASY_SCORE_SCORING_PROFILE": timestamp,
    }
    if len(selected_seasons) > 1:
        source_timestamps["MLB_STATS_API_CROSS_SEASON_HISTORY"] = timestamp
    if source_capture_timestamp:
        source_timestamps[f"INPUT_CAPTURE_{str(source_label).strip().upper()}"] = source_capture_timestamp

    return {
        "captured_at": timestamp,
        "game_log": [float(row["fantasy_score"]) for row in recent],
        "box_score_log": recent,
        "role_status": {
            "status": schedule["starter_status"],
            "role": "STARTING_PITCHER",
            "confirmation_strength": "OFFICIAL_PROBABLE_PITCHER",
            "team": schedule["team"],
            "opponent": schedule["opponent"],
            "venue": schedule["venue"],
            "official_game_pk": schedule["official_game_pk"],
            "official_game_date": schedule["official_game_date"],
            "schedule_status": schedule["schedule_status"],
            "official_name": official_name,
            "source": "MLB StatsAPI official schedule/probablePitcher",
        },
        "role_timestamp": timestamp,
        "opportunity_ledger": {
            "status": "READY",
            "game_log_stat": "pitcher fantasy score",
            "box_score_alignment": "1:1",
            "regular_season_prior_starts": len(recent),
            "starter_confirmation": "OFFICIAL_PROBABLE_PITCHER",
            "history_seasons_used": selected_seasons,
            "history_selection": "MOST_RECENT_OFFICIAL_STARTS_NO_IMPUTATION",
            "scoring_profile_id": SCORING_PROFILE_ID,
        },
        "source_timestamps": source_timestamps,
        "evidence_version": AUTO_HYDRATION_EVIDENCE_VERSION,
        "rate_provenance": (
            "MLB StatsAPI official pitching gameLog; Fantasy Score reconstructed per verified PrizePicks "
            "pitcher profile (6W + 4QS + 3K + outs - 3ER); bounded cross-season L10; no history imputed"
        ),
    }


__all__ = [
    "SCORING_PROFILE_ID",
    "STAT_TYPE",
    "hydrate_mlb_pitcher_fantasy_score_evidence",
]
