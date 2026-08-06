"""
gate_engine/command_center/market_router.py
WOW Sports Intelligence Command Center — Phase 1

Strict single-engine routing: each candidate is assigned to EXACTLY ONE
controlling engine family. A candidate that matches multiple families or
none receives a CC blocker and is not dispatched to any engine.

Routing is deterministic. The declared market_family field on the envelope
is the primary signal (intake already validated it). Additional structural
checks confirm the declaration is consistent with the candidate's payload.

can_execute = False (unconditional)
"""
from __future__ import annotations

from typing import Any

from .cc_labels import (
    CAN_EXECUTE,
    FAMILY_PROP, FAMILY_LLP,
    FAMILY_KALSHI_SPORTS, FAMILY_KALSHI_WEATHER,
    ALL_FAMILIES,
    CC_ROUTING_CONFLICT,
    CC_ROUTING_UNRESOLVABLE,
    CC_ROUTING_ASSIGNED,
)

# ---------------------------------------------------------------------------
# Structural consistency checks per family
# These are secondary validation after the declared market_family is trusted.
# They detect misdeclared families (e.g. PROP candidate declaring LLP).
# ---------------------------------------------------------------------------

_KALSHI_PLATFORMS = frozenset({"KALSHI", "KALSHI_SPORTS", "KALSHI_WEATHER"})
_LLP_MARKETS      = frozenset({"moneyline", "game_winner", "match_winner", "llp"})
_PROP_SPORTS      = frozenset({"MLB", "NBA", "WNBA", "NFL", "NHL", "TENNIS", "SOCCER", "MLS"})


def _check_prop_consistency(env: dict[str, Any]) -> list[str]:
    """Return warning strings if a PROP candidate looks structurally inconsistent."""
    warnings = []
    platform = (env.get("raw_data") or {}).get("platform", "")
    if platform.upper() in _KALSHI_PLATFORMS:
        warnings.append("PLATFORM_MISMATCH:platform=KALSHI declared as PROP")
    market_family_raw = (env.get("raw_data") or {}).get("market_family", "")
    if str(market_family_raw).upper() in {"LLP"}:
        warnings.append("MARKET_FAMILY_CONFLICT:raw_data.market_family=LLP declared as PROP")
    return warnings


def _check_llp_consistency(env: dict[str, Any]) -> list[str]:
    warnings = []
    raw = env.get("raw_data") or {}
    platform = raw.get("platform", "")
    if platform.upper() in _KALSHI_PLATFORMS:
        warnings.append("PLATFORM_MISMATCH:platform=KALSHI declared as LLP")
    if raw.get("prop_type") and not raw.get("market_family"):
        warnings.append("STRUCTURAL_WARNING:prop_type present but family declared LLP")
    return warnings


def _check_kalshi_sports_consistency(env: dict[str, Any]) -> list[str]:
    warnings = []
    raw = env.get("raw_data") or {}
    category = (raw.get("category") or "").lower()
    if category and category not in {"sports_winner", "sports", ""}:
        warnings.append(f"CATEGORY_MISMATCH:category={category} in KALSHI_SPORTS")
    if raw.get("prop_type"):
        warnings.append("STRUCTURAL_WARNING:prop_type present in KALSHI_SPORTS candidate")
    return warnings


def _check_kalshi_weather_consistency(env: dict[str, Any]) -> list[str]:
    warnings = []
    raw = env.get("raw_data") or {}
    category = (raw.get("category") or "").lower()
    if category and category not in {"weather", ""}:
        warnings.append(f"CATEGORY_MISMATCH:category={category} in KALSHI_WEATHER")
    return warnings


_CONSISTENCY_CHECKS = {
    FAMILY_PROP:           _check_prop_consistency,
    FAMILY_LLP:            _check_llp_consistency,
    FAMILY_KALSHI_SPORTS:  _check_kalshi_sports_consistency,
    FAMILY_KALSHI_WEATHER: _check_kalshi_weather_consistency,
}


# ---------------------------------------------------------------------------
# Public API
# ---------------------------------------------------------------------------

def route_candidate(env: dict[str, Any]) -> dict[str, Any]:
    """
    Route one canonical envelope to exactly one engine family.

    Returns a routing result dict:
      {
        assigned_family: str | None,
        routing_status:  "ASSIGNED" | "CONFLICT" | "UNRESOLVABLE",
        consistency_warnings: list[str],
        cc_blockers_added:    list[str],
      }

    Blockers are added to env["cc_blockers"] in place if routing fails.
    """
    declared = env.get("market_family")
    new_blockers: list[str] = []
    warnings: list[str] = []

    # --- Primary signal: declared market_family (validated at intake) ---
    if not declared or declared not in ALL_FAMILIES:
        new_blockers.append(CC_ROUTING_UNRESOLVABLE)
        env["cc_blockers"].extend(new_blockers)
        return {
            "assigned_family":      None,
            "routing_status":       "UNRESOLVABLE",
            "consistency_warnings": [],
            "cc_blockers_added":    new_blockers,
        }

    # --- Structural consistency check for the declared family ---
    check_fn = _CONSISTENCY_CHECKS.get(declared)
    if check_fn:
        warnings = check_fn(env)

    # If structural check found a hard conflict (PLATFORM_MISMATCH), escalate
    hard_conflicts = [w for w in warnings if "MISMATCH" in w and "WARNING" not in w]
    if hard_conflicts:
        # Two families could claim this candidate — treat as routing conflict
        new_blockers.append(CC_ROUTING_CONFLICT)
        env["cc_blockers"].extend(new_blockers)
        return {
            "assigned_family":      None,
            "routing_status":       "CONFLICT",
            "consistency_warnings": warnings,
            "cc_blockers_added":    new_blockers,
        }

    # --- Successful assignment ---
    env["assigned_family"] = declared
    return {
        "assigned_family":      declared,
        "routing_status":       "ASSIGNED",
        "consistency_warnings": warnings,
        "cc_blockers_added":    [],
    }


def route_batch(
    envelopes: list[dict[str, Any]],
) -> dict[str, Any]:
    """
    Route a batch of canonical envelopes.

    Returns:
      {
        by_family: {family: [envelopes]},  # successfully routed
        conflicts:  [envelopes],           # routing conflicts
        unresolvable: [envelopes],         # no family could be assigned
        routing_summary: {family: count, ...},
        total_routed: int,
        total_failed: int,
      }
    """
    by_family: dict[str, list[dict[str, Any]]] = {f: [] for f in ALL_FAMILIES}
    conflicts:   list[dict[str, Any]] = []
    unresolvable: list[dict[str, Any]] = []
    routing_detail: list[dict[str, Any]] = []

    for env in envelopes:
        result = route_candidate(env)
        routing_detail.append({
            "candidate_id": env.get("candidate_id"),
            **result,
        })
        status = result["routing_status"]
        if status == "ASSIGNED":
            by_family[result["assigned_family"]].append(env)
        elif status == "CONFLICT":
            conflicts.append(env)
        else:
            unresolvable.append(env)

    total_routed = sum(len(v) for v in by_family.values())
    total_failed = len(conflicts) + len(unresolvable)

    return {
        "by_family":      by_family,
        "conflicts":      conflicts,
        "unresolvable":   unresolvable,
        "routing_detail": routing_detail,
        "routing_summary": {f: len(v) for f, v in by_family.items()},
        "total_routed":   total_routed,
        "total_failed":   total_failed,
        "can_execute":    CAN_EXECUTE,
    }
