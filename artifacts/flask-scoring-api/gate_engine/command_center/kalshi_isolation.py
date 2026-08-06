"""
gate_engine/command_center/kalshi_isolation.py
WOW Sports Intelligence Command Center — Phase 1

KALSHI_RECOVERY_MODE=ACTIVE enforcement.

Kalshi candidates (KALSHI_SPORTS and KALSHI_WEATHER) run in a fully
isolated pool. Their governance caps (max 2 total, max 1/event) are
applied by the existing kalshi_engine/portfolio_governor module.

This module:
  1. Enforces that Kalshi candidates never contaminate Prop or LLP pools
  2. Applies Recovery Mode caps to the Kalshi pool as a post-processing step
  3. Adds CC:KALSHI_RECOVERY_MODE_CAP or CC:KALSHI_CROSS_ENGINE_CONTAMINATION_BLOCK
     to any candidate that violates isolation

The kalshi_engine/portfolio_governor.run() is used for cap enforcement.
No Kalshi result label can appear on a Prop or LLP candidate and vice versa.

can_execute = False (unconditional)
"""
from __future__ import annotations

from typing import Any

from .cc_labels import (
    CAN_EXECUTE,
    KALSHI_RECOVERY_MODE,
    FAMILY_PROP, FAMILY_LLP,
    FAMILY_KALSHI_SPORTS, FAMILY_KALSHI_WEATHER,
    CC_KALSHI_RECOVERY_CAP,
    CC_KALSHI_CONTAMINATION_BLOCK,
)
from .ceiling_resolver import apply_ceiling_to_row

# Labels that should ONLY appear on Kalshi candidates
_KALSHI_EXCLUSIVE_LABELS = frozenset({
    "KALSHI_SCOUT", "KALSHI_WATCH", "KALSHI_QUALIFIED_HOLD",
    "PORTFOLIO_QUALIFIED_HOLD",
    "RECOVERY_CAP_SINGLES_ONLY",
    "RECOVERY_CAP_MAX_2_PER_DAY",
    "RECOVERY_CAP_MAX_1_PER_EVENT",
    "RECOVERY_CAP_MAX_1_WEATHER_PER_CITY_DATE",
    "RECOVERY_CAP_NO_NARROW_BRACKET_YES",
    "RECOVERY_CAP_NO_SAME_CITY_MULTI_BRACKET",
})

# Labels that should ONLY appear on non-Kalshi (Prop/LLP) candidates
_NON_KALSHI_LABELS = frozenset({
    "FINAL_APPROVED", "MONEY_QUALIFIED",
    "MARKET_VERIFIED_HOLD", "MODEL_QUALIFIED_HOLD",
})

_KALSHI_FAMILIES = frozenset({FAMILY_KALSHI_SPORTS, FAMILY_KALSHI_WEATHER})
_NON_KALSHI_FAMILIES = frozenset({FAMILY_PROP, FAMILY_LLP})


class KalshiIsolationContext:
    """
    Holds the isolated Kalshi pool state for one CC run.
    Weather and sports are sub-isolated within the Kalshi pool.
    """

    def __init__(self) -> None:
        self.recovery_mode: str = KALSHI_RECOVERY_MODE
        self.sports_pool: list[dict[str, Any]] = []
        self.weather_pool: list[dict[str, Any]] = []
        self.sports_rejected: list[dict[str, Any]] = []
        self.weather_rejected: list[dict[str, Any]] = []

    def add(self, candidate: dict[str, Any]) -> None:
        family = candidate.get("market_family") or candidate.get("assigned_family", "")
        if family == FAMILY_KALSHI_SPORTS:
            self.sports_pool.append(candidate)
        elif family == FAMILY_KALSHI_WEATHER:
            self.weather_pool.append(candidate)

    def all_kalshi(self) -> list[dict[str, Any]]:
        return self.sports_pool + self.weather_pool

    def snapshot(self) -> dict[str, Any]:
        return {
            "recovery_mode":     self.recovery_mode,
            "sports_candidates": len(self.sports_pool),
            "weather_candidates": len(self.weather_pool),
            "sports_rejected":   len(self.sports_rejected),
            "weather_rejected":  len(self.weather_rejected),
            "can_execute":       CAN_EXECUTE,
        }


def apply_recovery_mode_caps(
    kalshi_candidates: list[dict[str, Any]],
) -> dict[str, Any]:
    """
    Apply Recovery Mode portfolio governor caps to the Kalshi candidate pool.

    Uses kalshi_engine.portfolio_governor.run() for the actual enforcement.
    Falls back to a local cap implementation if the import fails.

    Returns a report with survivors and rejected candidates.
    """
    try:
        from kalshi_engine import portfolio_governor as _pg
        # portfolio_governor.run() needs candidates in its own shape;
        # build lightweight proxies with the required ranking fields
        proxies = []
        for c in kalshi_candidates:
            er = c.get("engine_result") or {}
            proxies.append({
                "candidate_id":              c.get("candidate_id"),
                "category":                  _resolve_category(c),
                "event_id":                  c.get("event_id") or "",
                "city":                      (c.get("raw_data") or {}).get("city"),
                "scan_date":                 c.get("slate_date"),
                "bracket_span_f":            er.get("bracket_span_f"),
                "is_multi_leg":              er.get("is_multi_leg", False),
                "research_eligible":         er.get("research_eligible", True),
                "net_edge_lower_bound":      er.get("net_edge_lower_bound"),
                "calibration_strength":      er.get("calibration_strength"),
                "model_uncertainty":         er.get("model_uncertainty"),
                "price_age_minutes":         er.get("price_age_minutes"),
                "calibrated_prob_lower_bound": er.get("calibrated_prob_lower_bound"),
                "settlement_clarity_grade":  er.get("settlement_clarity_grade"),
                "spread_cents":              er.get("spread_cents"),
                "exposure_overlap":          er.get("exposure_overlap", False),
                "_original":                 c,   # carry original for blocker stamping
            })

        pg_result = _pg.run(proxies)

        # Stamp CC blockers on candidates that the governor rejected
        rejected_ids = {p.get("candidate_id") for p in pg_result["rejected"]}
        for proxy in proxies:
            cid = proxy.get("candidate_id")
            if cid in rejected_ids:
                original = proxy["_original"]
                reason = next(
                    (p.get("portfolio_rejection_reason") for p in pg_result["rejected"]
                     if p.get("candidate_id") == cid),
                    "RECOVERY_CAP_UNKNOWN",
                )
                note = f"{CC_KALSHI_RECOVERY_CAP}:{reason}"
                original.setdefault("cc_blockers", []).append(note)
                apply_ceiling_to_row(original, CC_KALSHI_RECOVERY_CAP,
                                     source="kalshi_isolation")
                original["kalshi_recovery_caps_applied"] = True
                original["kalshi_recovery_rejection"] = reason

        # Survivors
        survivor_ids = {p.get("candidate_id") for p in pg_result["survivors"]}
        for proxy in proxies:
            if proxy.get("candidate_id") in survivor_ids:
                proxy["_original"]["kalshi_recovery_caps_applied"] = True
                proxy["_original"]["kalshi_recovery_rejection"] = None

        return {
            "status":          "APPLIED",
            "recovery_mode":   KALSHI_RECOVERY_MODE,
            "total":           len(kalshi_candidates),
            "survivors":       len(pg_result["survivors"]),
            "rejected":        len(pg_result["rejected"]),
            "final_pool_size": len(pg_result["final_pool"]),
            "ranking_detail":  pg_result["ranking_detail"],
            "can_execute":     CAN_EXECUTE,
        }

    except ImportError:
        # Fallback: apply hard caps manually
        return _apply_caps_fallback(kalshi_candidates)


def _resolve_category(c: dict[str, Any]) -> str:
    family = c.get("market_family") or c.get("assigned_family", "")
    if family == FAMILY_KALSHI_SPORTS:
        return "sports_winner"
    return "weather"


def _apply_caps_fallback(candidates: list[dict[str, Any]]) -> dict[str, Any]:
    """Local cap enforcement when kalshi_engine import fails."""
    accepted: list[dict[str, Any]] = []
    for c in candidates:
        if len(accepted) >= 2:
            note = f"{CC_KALSHI_RECOVERY_CAP}:RECOVERY_CAP_MAX_2_PER_DAY"
            c.setdefault("cc_blockers", []).append(note)
            apply_ceiling_to_row(c, CC_KALSHI_RECOVERY_CAP, source="kalshi_isolation_fallback")
            c["kalshi_recovery_rejection"] = "RECOVERY_CAP_MAX_2_PER_DAY"
        else:
            accepted.append(c)
            c["kalshi_recovery_caps_applied"] = True
    return {
        "status":          "APPLIED_FALLBACK",
        "recovery_mode":   KALSHI_RECOVERY_MODE,
        "total":           len(candidates),
        "survivors":       len(accepted),
        "rejected":        len(candidates) - len(accepted),
        "final_pool_size": len(accepted),
        "can_execute":     CAN_EXECUTE,
    }


def check_cross_contamination(
    all_candidates: list[dict[str, Any]],
) -> list[dict[str, Any]]:
    """
    Verify that Kalshi-exclusive labels do not appear on Prop/LLP candidates
    and non-Kalshi labels do not appear on Kalshi candidates as engine_label.

    Returns list of contamination violations (should be empty in correct runs).
    """
    violations = []

    for c in all_candidates:
        family = c.get("market_family") or c.get("assigned_family", "")
        engine_label = c.get("engine_label") or ""

        if family in _KALSHI_FAMILIES:
            if engine_label in _NON_KALSHI_LABELS:
                note = (f"{CC_KALSHI_CONTAMINATION_BLOCK}:"
                        f"kalshi_candidate_has_non_kalshi_label={engine_label}")
                c.setdefault("cc_blockers", []).append(note)
                apply_ceiling_to_row(c, CC_KALSHI_CONTAMINATION_BLOCK,
                                     source="kalshi_isolation")
                violations.append({
                    "candidate_id":  c.get("candidate_id"),
                    "family":        family,
                    "engine_label":  engine_label,
                    "violation":     "KALSHI_CANDIDATE_HAS_NON_KALSHI_LABEL",
                })

        elif family in _NON_KALSHI_FAMILIES:
            if engine_label in _KALSHI_EXCLUSIVE_LABELS:
                note = (f"{CC_KALSHI_CONTAMINATION_BLOCK}:"
                        f"non_kalshi_candidate_has_kalshi_label={engine_label}")
                c.setdefault("cc_blockers", []).append(note)
                apply_ceiling_to_row(c, CC_KALSHI_CONTAMINATION_BLOCK,
                                     source="kalshi_isolation")
                violations.append({
                    "candidate_id":  c.get("candidate_id"),
                    "family":        family,
                    "engine_label":  engine_label,
                    "violation":     "NON_KALSHI_CANDIDATE_HAS_KALSHI_LABEL",
                })

    return violations
