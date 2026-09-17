"""Autonomous immutable evidence capture for the MLB Pitcher Fantasy Score candidate.

This producer exists only to build the forward calibration cohort for the
CANDIDATE/SHADOW Fantasy Score artifact.  It does not grant production model
authority and it never publishes, ranks, or executes a wager.

The source is the official MLB Stats API.  For each probable starting pitcher we
freeze the ten most recent regular-season starts available before event start and
compute the exact verified PrizePicks pitcher Fantasy Score components:

    6 * win + 4 * quality_start + 3 * strikeouts + outs_recorded - 3 * earned_runs

The autonomous threshold is a deterministic half-point around the prior-ten
median.  It is a calibration/discovery threshold, not a sportsbook market line.
Exact screenshot/PDF PrizePicks lines remain separate and are never overwritten.

can_execute=false unconditionally.
"""
from __future__ import annotations

import math
from datetime import datetime, timezone
from statistics import median
from typing import Any

import httpx

from pick_request_runtime import PickRequestRow, RawPropEvidence, _snapshot_payload, _validate_evidence
from prop_auto_hydration import (
    AUTO_HYDRATION_EVIDENCE_VERSION,
    MLB_STATS_API_BASE,
    PropAutoHydrationError,
    _int,
    _outs_from_ip,
    _request_json,
    _resolve_player_id,
    _schedule_context,
)
from prop_hydration_resilience import fetch_cross_season_pitching_splits
from v17.daily_prop_acquisition import _request_schedule, _schedule_pitchers

SPORT = "MLB"
STAT_TYPE = "PITCHER_FANTASY_SCORE"
SOURCE_TYPE = "AUTONOMOUS_DISCOVERY"
PLATFORM = "MLB_STATS_API_OFFICIAL_V1"
SCORING_PROFILE_ID = "PRIZEPICKS_MLB_PITCHER_FANTASY_SCORE_2025_07_11_VERIFIED_V1"
MIN_STARTS = 10
CAN_EXECUTE = False


def _aware(value: Any) -> datetime:
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


def _fantasy_score(*, win: int, quality_start: int, strikeouts: int, outs: int, earned_runs: int) -> float:
    return float(6 * win + 4 * quality_start + 3 * strikeouts + outs - 3 * earned_runs)


def _history(
    player: str,
    *,
    event_start_time: str,
    http_get: Any,
) -> tuple[list[float], list[dict[str, Any]], dict[str, Any], list[int]]:
    event_start = _aware(event_start_time)
    player_id, official_name = _resolve_player_id(
        player,
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
        stat = split.get("stat") if isinstance(split.get("stat"), dict) else {}
        if _int(stat.get("gamesStarted")) < 1:
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
        outs = _outs_from_ip(stat.get("inningsPitched"))
        win = 1 if _int(stat.get("wins")) > 0 else 0
        quality_start = 1 if outs >= 18 and earned_runs <= 3 else 0
        score = _fantasy_score(
            win=win,
            quality_start=quality_start,
            strikeouts=strikeouts,
            outs=outs,
            earned_runs=earned_runs,
        )
        opponent = split.get("opponent") if isinstance(split.get("opponent"), dict) else {}
        parsed.append(
            (
                date_value,
                {
                    "date": date_value,
                    "season": int(split_season),
                    "opponent": opponent.get("abbreviation") or opponent.get("name") or "UNKNOWN",
                    "ip": str(stat.get("inningsPitched")),
                    "outs": outs,
                    "so": strikeouts,
                    "er": earned_runs,
                    "win": win,
                    "quality_start": quality_start,
                    "fantasy_score": score,
                    "scoring_profile_id": SCORING_PROFILE_ID,
                },
            )
        )

    parsed.sort(key=lambda item: item[0], reverse=True)
    recent = [row for _, row in parsed[:MIN_STARTS]]
    if len(recent) < MIN_STARTS:
        raise PropAutoHydrationError(
            "MLB_RECENT_STARTS_INSUFFICIENT",
            "fewer than ten official regular-season starts were available for MLB pitcher Fantasy Score evidence",
            detail={
                "player": official_name,
                "starts_found": len(recent),
                "required": MIN_STARTS,
                "seasons_queried": seasons_queried,
            },
        )
    return [float(row["fantasy_score"]) for row in recent], recent, schedule, seasons_queried


def _raw_evidence(
    player: str,
    *,
    event_start_time: str,
    now: datetime,
    http_get: Any,
) -> RawPropEvidence:
    game_log, box_score_log, schedule, seasons_queried = _history(
        player,
        event_start_time=event_start_time,
        http_get=http_get,
    )
    timestamp = now.astimezone(timezone.utc).isoformat()
    return RawPropEvidence(
        captured_at=timestamp,
        game_log=game_log,
        box_score_log=box_score_log,
        role_status={
            "status": schedule["starter_status"],
            "role": "STARTING_PITCHER",
            "confirmation_strength": "OFFICIAL_PROBABLE_PITCHER",
            "team": schedule["team"],
            "opponent": schedule["opponent"],
            "venue": schedule["venue"],
            "official_game_pk": schedule["official_game_pk"],
            "official_game_date": schedule["official_game_date"],
            "schedule_status": schedule["schedule_status"],
            "source": "MLB StatsAPI official schedule/probablePitcher",
        },
        role_timestamp=timestamp,
        opportunity_ledger={
            "status": "READY",
            "game_log_stat": "PrizePicks MLB pitcher fantasy score",
            "box_score_alignment": "1:1",
            "regular_season_prior_starts": len(box_score_log),
            "starter_confirmation": "OFFICIAL_PROBABLE_PITCHER",
            "scoring_profile_id": SCORING_PROFILE_ID,
            "history_seasons_used": seasons_queried,
            "history_selection": "MOST_RECENT_OFFICIAL_STARTS_NO_IMPUTATION",
        },
        source_timestamps={
            "MLB_STATS_API_PLAYER_IDENTITY": timestamp,
            "MLB_STATS_API_PITCHING_GAME_LOG": timestamp,
            "MLB_STATS_API_SCHEDULE_PROBABLE_PITCHER": timestamp,
        },
        evidence_version=AUTO_HYDRATION_EVIDENCE_VERSION,
        rate_provenance=(
            "MLB StatsAPI official pitching gameLog; exact verified PrizePicks MLB pitcher Fantasy Score "
            "formula; prior starts only; no history imputed"
        ),
    )


def _candidate_line(values: list[float]) -> float:
    center = float(median(values))
    return float(math.floor(center) + 0.5)


def _base_receipt(candidate: dict[str, Any]) -> dict[str, Any]:
    return {
        "event_id": candidate.get("event_id"),
        "event_start_time": candidate.get("event_start_time"),
        "sport": SPORT,
        "player": candidate.get("player"),
        "stat_type": STAT_TYPE,
        "line": None,
        "side": "MORE",
        "source_type": SOURCE_TYPE,
        "platform": PLATFORM,
        "source_snapshot_id": None,
        "write_status": "PREWRITE_PENDING",
        "terminal_reason": None,
        "probability_publishable": False,
        "rank_eligible": False,
        "can_execute": False,
    }


def acquire_mlb_pitcher_fantasy_score_snapshots(
    *,
    db: Any,
    requested_date: str,
    requested_timezone: str,
    max_candidates: int,
    now: datetime | None = None,
    http_get: Any = httpx.get,
) -> dict[str, Any]:
    """Freeze candidate-only pregame snapshots for future calibration evidence."""
    now = (now or datetime.now(timezone.utc)).astimezone(timezone.utc)
    result: dict[str, Any] = {
        "status": "COMPLETED",
        "attempted": 0,
        "hydrated": 0,
        "persisted": 0,
        "held": 0,
        "snapshot_write_succeeded": 0,
        "snapshot_write_failed": 0,
        "explicit_prewrite_exclusions": 0,
        "receipts": [],
        "blockers": [],
        "candidate_source": "MLB_OFFICIAL_PROBABLE_PITCHERS",
        "line_source": "PRIOR10_FANTASY_SCORE_MEDIAN_HALF_POINT_DISCOVERY_ONLY",
        "scoring_profile_id": SCORING_PROFILE_ID,
        "probability_publishable": False,
        "rank_eligible": False,
        "can_execute": False,
    }
    if max_candidates <= 0:
        return result
    try:
        schedule = _request_schedule(requested_date, http_get=http_get)
    except Exception as exc:
        result["status"] = "DATA_UNOBTAINABLE"
        result["blockers"] = [f"MLB_FANTASY_SCORE_SCHEDULE_ACQUISITION_FAILED:{type(exc).__name__}"]
        return result

    candidates = _schedule_pitchers(
        schedule,
        requested_date=requested_date,
        requested_timezone=requested_timezone,
        now=now,
    )
    for candidate in candidates[:max_candidates]:
        result["attempted"] += 1
        receipt = _base_receipt(candidate)
        result["receipts"].append(receipt)
        try:
            evidence = _raw_evidence(
                candidate["player"],
                event_start_time=candidate["event_start_time"],
                now=now,
                http_get=http_get,
            )
            line = _candidate_line(evidence.game_log)
            row = PickRequestRow(
                row_key=f"fantasy-forward:{candidate['event_id']}:{candidate['player']}:{line}",
                event_id=candidate["event_id"],
                event_start_time=candidate["event_start_time"],
                sport=SPORT,
                player=candidate["player"],
                stat_type=STAT_TYPE,
                line=line,
                direction="MORE",
                evidence=evidence,
                source_type=SOURCE_TYPE,
                platform=PLATFORM,
                source_capture_timestamp=now.isoformat(),
            )
            normalized = _validate_evidence(row, STAT_TYPE)
            snapshot_id, _fingerprint, snapshot = _snapshot_payload(row, normalized)
            result["hydrated"] += 1
            receipt.update(
                {
                    "line": line,
                    "source_snapshot_id": snapshot_id,
                    "write_status": "SNAPSHOT_WRITE_PENDING",
                }
            )
            try:
                db.table("wow_prop_evidence_snapshots").upsert(
                    snapshot,
                    on_conflict="source_snapshot_id",
                ).execute()
            except Exception as exc:
                result["snapshot_write_failed"] += 1
                result["held"] += 1
                receipt["write_status"] = "SNAPSHOT_WRITE_FAILED"
                receipt["terminal_reason"] = f"FANTASY_SCORE_SNAPSHOT_WRITE_FAILED:{type(exc).__name__}"
                result["blockers"].append(f"{candidate['player']}:{receipt['terminal_reason']}")
                continue
            result["snapshot_write_succeeded"] += 1
            result["persisted"] += 1
            receipt["write_status"] = "SNAPSHOT_WRITE_SUCCEEDED"
            receipt["terminal_reason"] = "PERSISTED_CANDIDATE_ONLY_AWAITING_FORWARD_SCORING"
        except PropAutoHydrationError as exc:
            result["explicit_prewrite_exclusions"] += 1
            result["held"] += 1
            receipt["write_status"] = "PREWRITE_EXCLUDED"
            receipt["terminal_reason"] = exc.code
            result["blockers"].append(f"{candidate['player']}:{exc.code}")
        except Exception as exc:
            result["explicit_prewrite_exclusions"] += 1
            result["held"] += 1
            receipt["write_status"] = "PREWRITE_EXCLUDED"
            receipt["terminal_reason"] = f"FANTASY_SCORE_ACQUISITION_ERROR:{type(exc).__name__}"
            result["blockers"].append(f"{candidate['player']}:{receipt['terminal_reason']}")

    result["blockers"] = list(dict.fromkeys(result["blockers"]))
    if result["snapshot_write_failed"] > 0 and result["snapshot_write_succeeded"] == 0:
        result["status"] = "RUN_INVALID_FANTASY_SCORE_SNAPSHOT_WRITE_FAILURE"
    elif result["persisted"] == 0 and result["attempted"] > 0:
        result["status"] = "COMPLETED_WITH_ROW_BLOCKERS"
    elif result["held"] > 0:
        result["status"] = "COMPLETED_WITH_ROW_BLOCKERS"
    return result


__all__ = [
    "CAN_EXECUTE",
    "SCORING_PROFILE_ID",
    "STAT_TYPE",
    "acquire_mlb_pitcher_fantasy_score_snapshots",
]
