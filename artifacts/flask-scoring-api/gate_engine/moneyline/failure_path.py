"""
gate_engine/moneyline/failure_path.py
WOW v16 — Distributional failure-path integrator for moneyline.

Consumes the existing gate_engine/failure_path.py PRIMARY/SECONDARY/BLACK_SWAN
regime matrix but treats regime probabilities as simulation weights rather
than narrative commentary.

Maps each kill-path scenario to a game-state regime, blends those weights
with simulation regime frequencies, and returns an adjusted win probability.

When failure-path matrix is absent → NOT_APPLICABLE, result unchanged.

can_execute=False unconditional.
"""
from __future__ import annotations

import re
from dataclasses import dataclass, field
from typing import Any

can_execute: bool = False

# ---------------------------------------------------------------------------
# Keyword → game-state regime mapping
# ---------------------------------------------------------------------------

_SCENARIO_TO_REGIME: list[tuple[frozenset[str], str]] = [
    (frozenset({"early hook", "early removal", "starter collapse",
                "ineffective", "poor outing"}),         "sp_early_hook"),
    (frozenset({"bullpen", "reliever", "middle relief"}), "bullpen_weak"),
    (frozenset({"blowout", "lopsided", "blew out"}),     "blowout_truncation"),
    (frozenset({"foul trouble", "disqualification"}),    "foul_trouble"),
    (frozenset({"overtime", "ot", "extra time"}),        "overtime"),
    (frozenset({"weather", "rain", "wind", "snow", "cold"}), "weather_impacted"),
    (frozenset({"injury", "scratch", "out", "dnp", "late scratch"}), "participant_injury"),
    (frozenset({"pace", "high pace", "tempo"}),          "high_pace"),
    (frozenset({"goalie", "netminder", "save percentage"}), "dominant_goalie"),
    (frozenset({"draw", "nil-nil", "defensive", "low scoring"}), "draw_preservation"),
]


def _classify_scenario(scenario_text: str) -> str | None:
    """Map a free-text scenario description to a regime name."""
    text = scenario_text.lower()
    for keywords, regime in _SCENARIO_TO_REGIME:
        if any(kw in text for kw in keywords):
            return regime
    return None


def _parse_probability_band(band_str: str | None) -> tuple[float, float]:
    """Parse '15–25%' → (0.15, 0.25)."""
    if not band_str:
        return 0.0, 0.0
    nums = re.findall(r"[\d.]+", str(band_str))
    if len(nums) >= 2:
        return float(nums[0]) / 100.0, float(nums[1]) / 100.0
    if len(nums) == 1:
        v = float(nums[0]) / 100.0
        return v, v
    return 0.0, 0.0


# ---------------------------------------------------------------------------
# Main integration function
# ---------------------------------------------------------------------------

@dataclass
class FailurePathResult:
    adjusted_win_prob:     float
    base_win_prob:         float
    failure_path_influence: float   # signed shift applied
    regime_overrides:      dict[str, float]   # regime → weight override
    path_annotations:      list[dict[str, Any]]
    status:                str     # "INTEGRATED" | "NOT_APPLICABLE" | "PARTIAL"
    notes:                 list[str] = field(default_factory=list)

    def to_dict(self) -> dict[str, Any]:
        return {
            "adjusted_win_prob":      round(self.adjusted_win_prob, 4),
            "base_win_prob":          round(self.base_win_prob, 4),
            "failure_path_influence": round(self.failure_path_influence, 4),
            "regime_overrides":       {k: round(v, 4) for k, v in self.regime_overrides.items()},
            "path_annotations":       self.path_annotations,
            "status":                 self.status,
            "notes":                  self.notes,
        }


def integrate_failure_paths(
    base_win_prob:        float,
    failure_path_matrix:  dict[str, Any] | None,
    simulation_regimes:   dict[str, float] | None = None,
) -> FailurePathResult:
    """
    Blend failure-path regime probabilities with simulation regime frequencies
    to produce a distributional adjustment to the win probability.

    Parameters
    ----------
    base_win_prob         : The independent probability (post-simulation).
    failure_path_matrix   : enrichment["failure_path_matrix"] with PRIMARY/
                            SECONDARY/BLACK_SWAN kill paths.
    simulation_regimes    : Regime frequency dict from SimulationResult
                            (optional; used to contextualise kill paths).

    Returns FailurePathResult with adjusted win probability.
    """
    if not failure_path_matrix:
        return FailurePathResult(
            adjusted_win_prob=base_win_prob,
            base_win_prob=base_win_prob,
            failure_path_influence=0.0,
            regime_overrides={},
            path_annotations=[],
            status="NOT_APPLICABLE",
            notes=["failure_path_matrix:ABSENT"],
        )

    path_names = ("PRIMARY_KILL_PATH", "SECONDARY_KILL_PATH", "BLACK_SWAN_PATH")
    annotations: list[dict[str, Any]] = []
    regime_overrides: dict[str, float] = {}
    total_influence = 0.0
    notes: list[str] = []
    any_mapped = False

    for pname in path_names:
        path = failure_path_matrix.get(pname)
        if not isinstance(path, dict):
            notes.append(f"{pname}:MISSING")
            continue

        scenario = str(path.get("scenario") or "")
        band = str(path.get("probability_band") or "")
        floor_p, ceil_p = _parse_probability_band(band)
        mid_p = (floor_p + ceil_p) / 2.0 if (floor_p + ceil_p) > 0 else 0.0

        model_adj_str = str(path.get("model_adjustment") or "")
        # Parse signed model adjustment like "-3% applied" → -0.03
        adj_nums = re.findall(r"[+-]?[\d.]+", model_adj_str)
        model_adj = float(adj_nums[0]) / 100.0 if adj_nums else 0.0
        # Clamp to reasonable range
        model_adj = max(-0.15, min(0.15, model_adj))

        # Map scenario to regime
        regime = _classify_scenario(scenario)

        # Weight influence by kill-path mid-probability and model adjustment
        influence = mid_p * model_adj
        total_influence += influence

        if regime:
            any_mapped = True
            # Merge regime override: blend with simulation frequency when available
            sim_freq = (simulation_regimes or {}).get(regime, 0.0)
            blended_weight = (mid_p + sim_freq) / 2.0 if sim_freq > 0 else mid_p
            if regime not in regime_overrides or regime_overrides[regime] < blended_weight:
                regime_overrides[regime] = blended_weight

        annotations.append({
            "path_name":       pname,
            "scenario":        scenario,
            "probability_band": band,
            "floor_prob":      round(floor_p, 4),
            "mid_prob":        round(mid_p, 4),
            "model_adjustment": round(model_adj, 4),
            "regime_mapped":   regime,
            "influence":       round(influence, 4),
        })

    # Apply total distributional adjustment to win probability
    # Clamp influence: no single failure-path pass can move prob by more than 12pp
    total_influence = max(-0.12, min(0.12, total_influence))
    adjusted = max(0.01, min(0.99, base_win_prob + total_influence))

    status = "INTEGRATED" if any_mapped else "PARTIAL"
    if not any_mapped:
        notes.append("NO_SCENARIOS_MAPPED_TO_REGIMES:influence_applied_from_adjustments_only")

    return FailurePathResult(
        adjusted_win_prob=adjusted,
        base_win_prob=base_win_prob,
        failure_path_influence=total_influence,
        regime_overrides=regime_overrides,
        path_annotations=annotations,
        status=status,
        notes=notes,
    )
