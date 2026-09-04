"""Additive official MLB evidence hydrator for certified pitcher workload props.

This module is intentionally separate from the established pitcher-strikeout
hydrator so the certified K path remains unchanged. It builds only auditable
raw evidence for PITCHING_OUTS, STRIKES_THROWN, and BALLS_THROWN.
"""
from __future__ import annotations

from datetime import datetime, timezone
from typing import Any, Callable, Optional

import httpx

WORKLOAD_STATS = {"PITCHING_OUTS", "STRIKES_THROWN", "BALLS_THROWN"}


def hydrate_mlb_workload_evidence(
    *,
    player: str,
    stat_type: str,
    event_start_time: str,
    resolve_player_id: Callable[..., tuple[int, str]],
    schedule_context: Callable[..., dict[str, Any]],
    request_json: Callable[..., dict[str, Any]],
    outs_from_ip: Callable[[Any], int],
    int_value: Callable[..., int],
    error_type: type[RuntimeError],
    mlb_stats_api_base: str,
    evidence_version: str,
    min_starts: int,
    http_get: Callable[..., Any] = httpx.get,
    now: Optional[datetime] = None,
    source_capture_timestamp: Optional[str] = None,
    source_label: str = "NORMALIZED_PICK_REQUEST",
) -> dict[str, Any]:
    stat_key = str(stat_type or "").strip().upper()
    if stat_key not in WORKLOAD_STATS:
        raise error_type(
            "PROP_AUTO_HYDRATION_UNSUPPORTED_ROUTE",
            "automatic workload evidence hydration is not certified for this stat route",
            detail={"sport": "MLB", "stat_type": stat_key},
        )
    normalized_player = " ".join(str(player or "").strip().split())
    if not normalized_player:
        raise error_type("PROP_PLAYER_IDENTITY_REQUIRED", "player is required for prop hydration")

    captured_at = (now or datetime.now(timezone.utc)).astimezone(timezone.utc)
    try:
        event_start = datetime.fromisoformat(str(event_start_time).replace("Z", "+00:00"))
    except (TypeError, ValueError) as exc:
        raise error_type("PROP_EVENT_START_INVALID", "event_start_time must be timezone-aware ISO 8601") from exc
    if event_start.utcoffset() is None:
        raise error_type("PROP_EVENT_START_INVALID", "event_start_time must include a timezone")
    event_start = event_start.astimezone(timezone.utc)
    if event_start <= captured_at:
        raise error_type("EVENT_ALREADY_STARTED", "pregame evidence cannot be hydrated after event start")

    player_id, _official_name = resolve_player_id(normalized_player, http_get=http_get)
    schedule = schedule_context(player_id, event_start=event_start, http_get=http_get)
    payload = request_json(
        f"{mlb_stats_api_base}/people/{player_id}/stats",
        params={"stats": "gameLog", "group": "pitching", "season": str(event_start.year), "gameType": "R"},
        http_get=http_get,
    )
    splits: list[Any] = []
    blocks = payload.get("stats")
    if isinstance(blocks, list):
        for block in blocks:
            if isinstance(block, dict) and isinstance(block.get("splits"), list):
                splits.extend(block["splits"])

    parsed: list[tuple[str, dict[str, Any], float]] = []
    for split in splits:
        if not isinstance(split, dict):
            continue
        stat = split.get("stat")
        if not isinstance(stat, dict) or int_value(stat.get("gamesStarted")) < 1:
            continue
        date_value = str(split.get("date") or "")
        try:
            game_date = datetime.fromisoformat(date_value).date()
        except ValueError:
            continue
        if game_date >= event_start.date():
            continue

        ip = stat.get("inningsPitched")
        try:
            outs = outs_from_ip(ip)
        except Exception:
            continue
        strikeouts = int_value(stat.get("strikeOuts"), default=-1)
        pitches = int_value(stat.get("numberOfPitches"), default=-1)
        strikes = int_value(stat.get("strikes"), default=-1)

        if stat_key == "PITCHING_OUTS":
            target = float(outs)
        else:
            if pitches < 0 or strikes < 0 or pitches < strikes:
                continue
            target = float(strikes if stat_key == "STRIKES_THROWN" else pitches - strikes)

        opponent = split.get("opponent") if isinstance(split.get("opponent"), dict) else {}
        row: dict[str, Any] = {
            "date": date_value,
            "opponent": opponent.get("abbreviation") or opponent.get("name") or "UNKNOWN",
            "ip": str(ip),
            "outs": outs,
            "bf": int_value(stat.get("battersFaced")),
            "so": max(strikeouts, 0),
            "bb": int_value(stat.get("baseOnBalls")),
            "er": int_value(stat.get("earnedRuns")),
        }
        if pitches >= 0:
            row["pitches"] = pitches
        if strikes >= 0:
            row["strikes"] = strikes
        parsed.append((date_value, row, target))

    parsed.sort(key=lambda item: item[0], reverse=True)
    recent = parsed[:min_starts]
    if len(recent) < min_starts:
        code = "MLB_PITCH_COMPOSITION_INSUFFICIENT" if stat_key in {"STRIKES_THROWN", "BALLS_THROWN"} else "MLB_RECENT_STARTS_INSUFFICIENT"
        raise error_type(
            code,
            "fewer than ten official regular-season starts with required target evidence were available before the event",
            detail={"starts_found": len(recent), "required": min_starts, "stat_type": stat_key},
        )

    game_log = [target for _, _, target in recent]
    box_score_log = [row for _, row, _ in recent]
    timestamp = captured_at.isoformat()
    source_timestamps = {
        "MLB_STATS_API_PLAYER_SEARCH": timestamp,
        "MLB_STATS_API_PITCHING_GAME_LOG": timestamp,
        "MLB_STATS_API_SCHEDULE_PROBABLE_PITCHER": timestamp,
    }
    if source_capture_timestamp:
        source_timestamps[f"INPUT_CAPTURE_{str(source_label).strip().upper()}"] = source_capture_timestamp

    stat_label = {
        "PITCHING_OUTS": "pitching outs",
        "STRIKES_THROWN": "strikes thrown",
        "BALLS_THROWN": "balls thrown",
    }[stat_key]
    return {
        "captured_at": timestamp,
        "game_log": game_log,
        "box_score_log": box_score_log,
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
            "source": "MLB StatsAPI official schedule/probablePitcher",
        },
        "role_timestamp": timestamp,
        "opportunity_ledger": {
            "status": "READY",
            "game_log_stat": stat_label,
            "box_score_alignment": "1:1",
            "regular_season_prior_starts": len(box_score_log),
            "starter_confirmation": "OFFICIAL_PROBABLE_PITCHER",
            "target_stat_type": stat_key,
        },
        "source_timestamps": source_timestamps,
        "evidence_version": evidence_version,
        "rate_provenance": f"MLB StatsAPI official pitching gameLog; target={stat_key}; no target values estimated",
    }
