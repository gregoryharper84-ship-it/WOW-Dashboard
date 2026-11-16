"""Fail-closed Kalshi slip topology diagnostics. RESEARCH ONLY, never a scorer.

The classifier identifies construction patterns, not profitable strategies.
It does not infer missing market families, probabilities, dependence strength,
or entry-time information from a post-settlement ticket.
"""
from __future__ import annotations

from collections import defaultdict
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

    Fields: sport, event_id, player_id, team_id, stat_family, market_id.
    Stable IDs and exact stat families must be authenticated by the intake.
    Labels shown by collapsed Kalshi emails are insufficient to infer stat_family.
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
        clean.append(row)
    if missing:
        return {
            "status": "UNVERIFIED_STRUCTURE_METADATA",
            "missing_fields": sorted(missing),
            "structure": None,
            "shared_failure_factors": [],
            "same_team_receiver_competition_groups": 0,
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

    factors = []
    if len(events) == 1:
        factors.append("SAME_GAME_SCRIPT_COMMON_CAUSE")
    if len(players) == 1:
        factors.append("ONE_PLAYER_MINUTES_OR_AVAILABILITY_COMMON_CAUSE")
    if receiver_groups:
        factors.append("SAME_TEAM_RECEIVING_OPPORTUNITY_COMPETITION")
    if duplicate_market_count:
        factors.append("DUPLICATE_MARKET_IDENTITY")

    return {
        "status": "STRUCTURAL_TOPOLOGY_ONLY_NOT_MODEL_CERTIFIED",
        "missing_fields": [],
        "structure": structure,
        "shared_failure_factors": factors,
        "same_team_receiver_competition_groups": int(receiver_groups),
        "duplicate_market_count": duplicate_market_count,
        "correlation_direction": "UNKNOWN_NOT_ESTIMATED",
        "joint_probability": None,
        "calibration_eligible": False,
        "model_feature_eligible": False,
        "probability_publishable": False,
        "can_execute": False,
    }
