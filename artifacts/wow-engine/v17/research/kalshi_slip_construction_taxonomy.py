"""Fail-closed Kalshi slip topology diagnostics. RESEARCH ONLY, never a scorer.

The classifier identifies construction patterns, not profitable strategies.
It does not infer missing market families, probabilities, dependence strength,
or entry-time information from a post-settlement ticket.
"""
from __future__ import annotations

from collections import defaultdict
import math
from typing import Any


def _id(leg: dict[str, Any], field: str) -> str | None:
    value = leg.get(field)
    if value is None or value == "":
        return None
    if not isinstance(value, str):
        raise ValueError(f"{field} must be a nonblank identifier string or null")
    normalized = value.strip()
    return normalized or None


def diagnose_slip_structure(legs: list[dict[str, Any]]) -> dict[str, Any]:
    """Classify structural overlap from independently verified market metadata.

    Required fields: sport, event_id, player_id, team_id, stat_family, market_id.
    Optional nested-event authentication fields: threshold, direction ('GE'
    or 'LE'), period_id, settlement_rule_id. These must be verified from
    exact underlying market terms, never inferred from notification labels.
    Stable IDs and exact stat families must be authenticated by the intake.
    """
    if not isinstance(legs, list) or len(legs) < 2 or len(legs) > 50:
        raise ValueError("legs must be a list with 2..50 items")
    clean: list[dict[str, str | None]] = []
    missing: list[str] = []
    for i, leg in enumerate(legs):
        if not isinstance(leg, dict):
            raise ValueError("leg must be a dictionary")
        row = {field: _id(leg, field) for field in
               ("sport", "event_id", "player_id", "team_id", "stat_family", "market_id")}
        for field, value in row.items():
            if value is None or value.upper() in {"UNKNOWN", "UNVERIFIED", "N/A"}:
                missing.append(f"leg[{i}].{field}")
        value = leg.get("threshold")
        if value is not None and (
            isinstance(value, bool) or not isinstance(value, (int, float))
            or not math.isfinite(float(value))
        ):
            raise ValueError("threshold must be a finite numeric value or null")
        row["threshold"] = None if value is None else float(value)
        direction = _id(leg, "direction")
        if direction is not None and direction not in {"GE", "LE"}:
            raise ValueError("direction must be GE, LE, or null")
        row["direction"] = direction
        row["period_id"] = _id(leg, "period_id")
        row["settlement_rule_id"] = _id(leg, "settlement_rule_id")
        clean.append(row)
    if missing:
        return {
            "status": "UNVERIFIED_STRUCTURE_METADATA",
            "missing_fields": sorted(missing),
            "structure": None,
            "shared_failure_factors": [],
            "same_team_receiver_competition_groups": 0,
            "potential_same_stat_groups": 0,
            "verified_nested_threshold_groups": 0,
            "complementary_stat_player_groups": 0,
            "correlation_direction": "UNKNOWN",
            "joint_probability": None,
            "calibration_eligible": False,
            "model_feature_eligible": False,
            "probability_publishable": False,
            "can_execute": False,
        }

    events = {(r["sport"], r["event_id"]) for r in clean}
    players = {(r["sport"], r["event_id"], r["player_id"]) for r in clean}
    if len(events) == 1 and len(players) == 1:
        structure = "SINGLE_PLAYER_SAME_GAME_MULTI_MARKET"
    elif len(events) == 1:
        structure = "MULTI_PLAYER_SAME_GAME"
    else:
        structure = "MULTI_EVENT_COMBINATION"

    receiving: dict[tuple[str, str, str], set[str]] = defaultdict(set)
    for r in clean:
        if r["stat_family"].upper() == "RECEIVING_YARDS":
            receiving[(r["sport"], r["event_id"], r["team_id"])].add(r["player_id"])
    receiver_groups = sum(len(group) >= 2 for group in receiving.values())
    duplicate_market_count = len(clean) - len({r["market_id"] for r in clean})

    # A same player/stat family can have nested thresholds, but only matching
    # periods, directions, and settlement rules make the logic comparable.
    # Do not infer stat families or thresholds from abbreviated email labels.
    by_player: dict[tuple[str, str, str], set[str]] = defaultdict(set)
    by_stat: dict[tuple[str, str, str, str], list[dict[str, Any]]] = defaultdict(list)
    for r in clean:
        player_key = (r["sport"], r["event_id"], r["player_id"])
        by_player[player_key].add(r["stat_family"])
        by_stat[(*player_key, r["stat_family"])].append(r)
    complementary_groups = sum(len(families) >= 2 for families in by_player.values())
    potential_same_stat = 0
    verified_nested = 0
    for group in by_stat.values():
        if len(group) < 2:
            continue
        potential_same_stat += 1
        directions = {r["direction"] for r in group}
        periods = {r["period_id"] for r in group}
        rules = {r["settlement_rule_id"] for r in group}
        thresholds = [r["threshold"] for r in group]
        if (
            None not in directions and len(directions) == 1
            and None not in periods and len(periods) == 1
            and None not in rules and len(rules) == 1
            and all(v is not None for v in thresholds)
            and len(set(thresholds)) >= 2
        ):
            verified_nested += 1

    factors = []
    if len(events) == 1:
        factors.append("SAME_GAME_SCRIPT_COMMON_CAUSE")
    if len(players) == 1:
        factors.append("ONE_PLAYER_MINUTES_OR_AVAILABILITY_COMMON_CAUSE")
    if receiver_groups:
        factors.append("SAME_TEAM_RECEIVING_OPPORTUNITY_COMPETITION")
    if duplicate_market_count:
        factors.append("DUPLICATE_MARKET_IDENTITY")
    if potential_same_stat:
        factors.append("POTENTIAL_SAME_STAT_THRESHOLD_NESTING")
    if verified_nested:
        factors.append("VERIFIED_NESTED_THRESHOLD_REDUNDANCY")
    if complementary_groups:
        factors.append("COMPLEMENTARY_STATS_SHARED_PLAYER_ROLE")

    return {
        "status": "STRUCTURAL_TOPOLOGY_ONLY_NOT_MODEL_CERTIFIED",
        "missing_fields": [],
        "structure": structure,
        "shared_failure_factors": factors,
        "same_team_receiver_competition_groups": int(receiver_groups),
        "duplicate_market_count": duplicate_market_count,
        "potential_same_stat_groups": potential_same_stat,
        "verified_nested_threshold_groups": verified_nested,
        "complementary_stat_player_groups": complementary_groups,
        "correlation_direction": "UNKNOWN_NOT_ESTIMATED",
        "joint_probability": None,
        "calibration_eligible": False,
        "model_feature_eligible": False,
        "probability_publishable": False,
        "can_execute": False,
    }
