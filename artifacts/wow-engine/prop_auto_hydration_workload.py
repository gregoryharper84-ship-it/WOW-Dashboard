"""Official MLB evidence hydrator for certified workload/PA props.

Builds auditable raw evidence for PITCHING_OUTS, STRIKES_THROWN,
BALLS_THROWN, and PLATE_APPEARANCES. Missing history is never padded or
estimated. Plate appearances delegate to the batter-specific official-MLB
hydrator while retaining this module's route-dispatch compatibility contract.
"""
from __future__ import annotations

from datetime import datetime, timezone
from typing import Any, Callable, Optional

import httpx

from prop_auto_hydration_plate_appearances import hydrate_mlb_plate_appearance_evidence
from prop_hydration_resilience import fetch_cross_season_pitching_splits

WORKLOAD_STATS = {"PITCHING_OUTS", "STRIKES_THROWN", "BALLS_THROWN", "PLATE_APPEARANCES"}


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

    if stat_key == "PLATE_APPEARANCES":
        return hydrate_mlb_plate_appearance_evidence(
            player=player,
            event_start_time=event_start_time,
            resolve_player_id=resolve_player_id,
            request_json=request_json,
            int_value=int_value,
            error_type=error_type,
            mlb_stats_api_base=mlb_stats_api_base,
            evidence_version=evidence_version,
            min_games=min_starts,
            http_get=http_get,
            now=now,
            source_capture_timestamp=source_capture_timestamp,
            source_label=source_label,
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

    try:
        player_id, _official_name = resolve_player_id(normalized_player, event_start=event_start, http_get=http_get)
    except TypeError:
        player_id, _official_name = resolve_player_id(normalized_player, http_get=http_get)
    schedule = schedule_context(player_id, event_start=event_start, http_get=http_get)

    season_splits, seasons_queried = fetch_cross_season_pitching_splits(
        player_id,
        event_start=event_start,
        request_json=request_json,
        http_get=http_get,
        mlb_stats_api_base=mlb_stats_api_base,
    )

    parsed: list[tuple[str, dict[str, Any], float]] = []
    for season, split in season_splits:
        stat = split.get("stat") if isinstance(split, dict) else None
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
            "season": season,
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
            "fewer than ten official regular-season starts with required target evidence were available across the supported history window",
            detail={
                "starts_found": len(recent),
                "required": min_starts,
                "stat_type": stat_key,
                "seasons_queried": seasons_queried,
            },
        )

    game_log = [target for _, _, target in recent]
    box_score_log = [row for _, row, _ in recent]
    selected_seasons = sorted({int(row["season"]) for row in box_score_log}, reverse=True)
    timestamp = captured_at.isoformat()
    source_timestamps = {
        "MLB_STATS_API_PLAYER_IDENTITY": timestamp,
        "MLB_STATS_API_PITCHING_GAME_LOG": timestamp,
        "MLB_STATS_API_SCHEDULE_PROBABLE_PITCHER": timestamp,
    }
    if len(selected_seasons) > 1:
        source_timestamps["MLB_STATS_API_CROSS_SEASON_HISTORY"] = timestamp
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
            "history_seasons_queried": seasons_queried,
            "history_seasons_used": selected_seasons,
            "history_selection": "MOST_RECENT_OFFICIAL_STARTS_NO_IMPUTATION",
        },
        "source_timestamps": source_timestamps,
        "evidence_version": evidence_version,
        "rate_provenance": f"MLB StatsAPI official pitching gameLog; target={stat_key}; bounded cross-season L10 by recency; no target values estimated",
    }
