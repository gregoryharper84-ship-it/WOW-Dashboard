"""Fail-closed NCAAB research pregame feature hydration (Issue #1644, step 2).

Research-only. Uses the candidate's original feature names and prior-game
statistics, never odds, projections, current-game final scores or LLM output.
This module cannot certify, register, publish, or execute a specialist.
"""
from __future__ import annotations

from datetime import datetime, timezone
from hashlib import sha256
from math import isfinite
import json
import re
from typing import Any, Iterable, Mapping

from v17.ncaab_sportsdataverse_candidate import (
    FEATURE_NAMES, FEATURE_SCHEMA_VERSION, MODEL_FAMILY, SOURCE_POLICY_ID,
    _history_entry, _summary,
)

CAN_EXECUTE = False
PROBABILITY_PUBLISHABLE = False
RESEARCH_ONLY = True
_ID = re.compile(r"^[0-9]+$")
_HASH = re.compile(r"^[0-9a-f]{64}$")
_REQUIRED_STATS = ("team_score", "total_rebounds", "field_goal_pct", "three_point_field_goal_pct")
MAX_EVENT_SOURCE_AGE_SECONDS = 24 * 60 * 60


class NCAABPregameHold(RuntimeError):
    def __init__(self, code: str, detail: str = ""):
        self.code = code
        super().__init__(f"{code}:{detail}")


def _hash(value: Any) -> str:
    return sha256(json.dumps(value, sort_keys=True, separators=(",", ":"), default=str).encode()).hexdigest()


def _time(value: Any, code: str) -> datetime:
    if not isinstance(value, (str, datetime)) or not value:
        raise NCAABPregameHold(code)
    try:
        date = value if isinstance(value, datetime) else datetime.fromisoformat(value.replace("Z", "+00:00"))
    except ValueError as exc:
        raise NCAABPregameHold(code) from exc
    if date.tzinfo is None:
        raise NCAABPregameHold(code, "timezone is required")
    return date.astimezone(timezone.utc)


def _identity(value: Any, label: str) -> str:
    text = str(value or "").strip()
    if not _ID.fullmatch(text):
        raise NCAABPregameHold("NCAAB_PREGAME_CANONICAL_ID_REQUIRED", label)
    return text


def _positive_version(value: Any) -> int:
    if isinstance(value, bool) or not isinstance(value, int) or value < 1:
        raise NCAABPregameHold("NCAAB_PREGAME_EVENT_VERSION_INVALID")
    return value


def _source(record: Mapping[str, Any], label: str) -> str:
    manifest = record.get("source_manifest")
    actual = str(record.get("source_manifest_sha256") or "")
    if not isinstance(manifest, Mapping) or not manifest or not _HASH.fullmatch(actual):
        raise NCAABPregameHold("NCAAB_PREGAME_SOURCE_MISSING", label)
    if _hash(manifest) != actual:
        raise NCAABPregameHold("NCAAB_PREGAME_SOURCE_TAMPERED", label)
    if manifest.get("market_features_used") is not False or record.get("market_features_used") is not False:
        raise NCAABPregameHold("NCAAB_PREGAME_MARKET_INPUT_REJECTED", label)
    if record.get("can_execute") is not False:
        raise NCAABPregameHold("NCAAB_PREGAME_EXECUTION_FLAG_REJECTED", label)
    return actual


def _stats(team: Mapping[str, Any], side: str, team_id: str) -> None:
    if _identity(team.get("team_id"), side) != team_id:
        raise NCAABPregameHold("NCAAB_PREGAME_TEAM_SIDE_MISMATCH", side)
    if team.get("team_home_away") != side:
        raise NCAABPregameHold("NCAAB_PREGAME_TEAM_SIDE_MISMATCH", side)
    required = _REQUIRED_STATS + (
        ("turnovers",) if team.get("turnovers") not in (None, "") else ("total_turnovers",)
    )
    for field in required:
        value = team.get(field)
        try:
            number = float(value)
        except (TypeError, ValueError) as exc:
            raise NCAABPregameHold("NCAAB_PREGAME_HISTORY_STAT_MISSING", f"{side}:{field}") from exc
        if isinstance(value, bool) or not isfinite(number):
            raise NCAABPregameHold("NCAAB_PREGAME_HISTORY_STAT_INVALID", f"{side}:{field}")


def hydrate_pregame(
    event: Mapping[str, Any],
    settled_games: Iterable[Mapping[str, Any]],
    *,
    max_event_source_age_seconds: int = MAX_EVENT_SOURCE_AGE_SECONDS,
) -> dict[str, Any]:
    """Build *research-only* original-order pregame features or issue typed HOLD.

    Canonical ESPN numeric IDs are mandatory. All game evidence must have verified
    source-manifest hashes and non-executable/non-market flags. Only current-season
    games with a settlement and source timestamp before the target's observed
    evidence timestamp are eligible. A <5-game same-season sample is held; prior
    seasons are not silently blended. Neutral target venues require separate review.
    """
    if not isinstance(event, Mapping):
        raise NCAABPregameHold("NCAAB_PREGAME_EVENT_MISSING")
    eid = _identity(event.get("game_id"), "game_id")
    if event.get("official_event_id") != f"NCAAB:{eid}":
        raise NCAABPregameHold("NCAAB_PREGAME_EVENT_ID_MISMATCH")
    home_id = _identity(event.get("home_team_id"), "home_team_id")
    away_id = _identity(event.get("away_team_id"), "away_team_id")
    if home_id == away_id:
        raise NCAABPregameHold("NCAAB_PREGAME_TEAM_ID_COLLISION")
    season = event.get("season")
    if isinstance(season, bool) or not isinstance(season, int) or season < 2000:
        raise NCAABPregameHold("NCAAB_PREGAME_SEASON_INVALID")
    _positive_version(event.get("event_version"))
    if event.get("source_policy_id") != SOURCE_POLICY_ID:
        raise NCAABPregameHold("NCAAB_PREGAME_SOURCE_POLICY_MISMATCH")
    target_start = _time(event.get("event_start_time"), "NCAAB_PREGAME_EVENT_TIME_INVALID")
    target_asof = _time(event.get("source_as_of"), "NCAAB_PREGAME_SOURCE_TIME_MISSING")
    if target_asof >= target_start:
        raise NCAABPregameHold("NCAAB_PREGAME_TARGET_NOT_PREGAME")
    age = (target_start - target_asof).total_seconds()
    if (isinstance(max_event_source_age_seconds, bool)
            or not isinstance(max_event_source_age_seconds, int)
            or max_event_source_age_seconds < 1):
        raise NCAABPregameHold("NCAAB_PREGAME_FRESHNESS_CONFIG_INVALID")
    if age > max_event_source_age_seconds:
        raise NCAABPregameHold("NCAAB_PREGAME_SOURCE_STALE")
    source_hash = _source(event, "target")
    if event.get("neutral_site") is not False:
        raise NCAABPregameHold("NCAAB_PREGAME_NEUTRAL_SITE_REVIEW_REQUIRED")
    if "home_score" in event or "away_score" in event or "outcome_json" in event:
        raise NCAABPregameHold("NCAAB_PREGAME_CURRENT_RESULT_LEAKAGE")

    histories: dict[str, list[dict[str, Any]]] = {home_id: [], away_id: []}
    lineage: list[dict[str, Any]] = []
    seen: set[str] = set()
    for game in settled_games:
        if not isinstance(game, Mapping):
            raise NCAABPregameHold("NCAAB_PREGAME_HISTORY_INVALID")
        gid = _identity(game.get("game_id"), "history_game_id")
        if gid in seen:
            raise NCAABPregameHold("NCAAB_PREGAME_DUPLICATE_GAME_VERSION", gid)
        seen.add(gid)
        if gid == eid:
            raise NCAABPregameHold("NCAAB_PREGAME_TARGET_IN_HISTORY")
        _positive_version(game.get("event_version"))
        if game.get("source_policy_id") != SOURCE_POLICY_ID:
            raise NCAABPregameHold("NCAAB_PREGAME_SOURCE_POLICY_MISMATCH", gid)
        digest = _source(game, gid)
        start = _time(game.get("event_start_time"), "NCAAB_PREGAME_HISTORY_TIME_INVALID")
        settled = _time(game.get("settled_at"), "NCAAB_PREGAME_HISTORY_NOT_SETTLED")
        observed = _time(game.get("source_as_of"), "NCAAB_PREGAME_HISTORY_SOURCE_TIME_MISSING")
        if settled < start or not (start < settled <= observed <= target_asof < target_start):
            raise NCAABPregameHold("NCAAB_PREGAME_HISTORY_FUTURE_LEAKAGE", gid)
        game_season = game.get("season")
        if not isinstance(game_season, int) or isinstance(game_season, bool):
            raise NCAABPregameHold("NCAAB_PREGAME_SEASON_INVALID", gid)
        if game_season > season:
            raise NCAABPregameHold("NCAAB_PREGAME_FUTURE_SEASON", gid)
        if game_season < season:
            continue  # explicit season reset; never import earlier-year team form
        h_id = _identity(game.get("home_team_id"), "history_home")
        a_id = _identity(game.get("away_team_id"), "history_away")
        if h_id == a_id:
            raise NCAABPregameHold("NCAAB_PREGAME_TEAM_ID_COLLISION", gid)
        if (h_id not in histories and a_id not in histories):
            continue
        h, a = game.get("home"), game.get("away")
        if not isinstance(h, Mapping) or not isinstance(a, Mapping):
            raise NCAABPregameHold("NCAAB_PREGAME_HISTORY_STAT_MISSING", gid)
        _stats(h, "home", h_id)
        _stats(a, "away", a_id)
        if h_id in histories:
            histories[h_id].append(_history_entry(dict(h), dict(a), game_id=gid, start=start))
        if a_id in histories:
            histories[a_id].append(_history_entry(dict(a), dict(h), game_id=gid, start=start))
        # Bind the *actual observed box stats* as well as the provider manifest.
        # Otherwise a modified score/feature input could reuse the same receipt hash.
        lineage.append({"game_id": gid, "event_version": game["event_version"],
                        "source_manifest_sha256": digest,
                        "observed_box_stats_sha256": _hash({"home": h, "away": a}),
                        "settled_at": settled.isoformat(),
                        "source_as_of": observed.isoformat()})
    for history in histories.values():
        history.sort(key=lambda row: (row["start"], row["event_id"]))
    home, away = _summary(histories[home_id], target_start), _summary(histories[away_id], target_start)
    if home is None or away is None:
        raise NCAABPregameHold("NCAAB_PREGAME_SMALL_SAMPLE_HOLD",
                              f"home={len(histories[home_id])},away={len(histories[away_id])}")
    feature_map = {
        "home_recent_win_rate": home["win_rate"], "away_recent_win_rate": away["win_rate"],
        "home_recent_point_diff": home["point_diff"], "away_recent_point_diff": away["point_diff"],
        "home_recent_points_for": home["points_for"], "away_recent_points_for": away["points_for"],
        "home_recent_rebound_margin_proxy": home["rebound_margin_proxy"],
        "away_recent_rebound_margin_proxy": away["rebound_margin_proxy"],
        "home_recent_turnovers": home["turnovers"], "away_recent_turnovers": away["turnovers"],
        "home_recent_fg_pct": home["fg_pct"], "away_recent_fg_pct": away["fg_pct"],
        "home_recent_3p_pct": home["three_pct"], "away_recent_3p_pct": away["three_pct"],
        "home_games_prior_log": home["games_prior_log"], "away_games_prior_log": away["games_prior_log"],
        "home_rest_days_capped": home["rest_days_capped"], "away_rest_days_capped": away["rest_days_capped"],
    }
    if tuple(feature_map) != FEATURE_NAMES or not all(isfinite(v) for v in feature_map.values()):
        raise NCAABPregameHold("NCAAB_PREGAME_FEATURE_CONTRACT_MISMATCH")
    lineage.sort(key=lambda row: (row["game_id"], row["event_version"]))
    evidence = {
        "target_event_id": f"NCAAB:{eid}", "event_version": event["event_version"],
        "home_team_id": home_id, "away_team_id": away_id, "season": season,
        "event_start_time": target_start.isoformat(), "feature_as_of": target_asof.isoformat(),
        "target_source_manifest_sha256": source_hash, "prior_game_sources": lineage,
        "home_prior_event_ids": home["prior_event_ids"],
        "away_prior_event_ids": away["prior_event_ids"],
    }
    return {
        "status": "RESEARCH_FEATURES_READY", "sport": "NCAAB",
        "official_event_id": f"NCAAB:{eid}", "model_family": MODEL_FAMILY,
        "feature_schema_version": FEATURE_SCHEMA_VERSION, "source_policy_id": SOURCE_POLICY_ID,
        "feature_names": list(FEATURE_NAMES), "feature_values": [feature_map[n] for n in FEATURE_NAMES],
        "features": feature_map, "input_sha256": _hash(evidence),
        "source_evidence": evidence, "market_features_used": False, "research_only": True,
        "probability_publishable": False, "can_execute": False,
    }


__all__ = ["CAN_EXECUTE", "PROBABILITY_PUBLISHABLE", "RESEARCH_ONLY", "NCAABPregameHold", "hydrate_pregame"]
