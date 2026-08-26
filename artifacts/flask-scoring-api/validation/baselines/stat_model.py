"""
validation/baselines/stat_model.py

Baseline C — Simple statistical model using:
  - Pitcher mean pitches/PA (from ledger_rows first_inning_pitches / batters_faced)
  - BF distribution from savant_1ip_ledger bf_distribution (p_bf_3/4/5+)
  - Optional: opponent pitches/PA (if supplied in row enrichment)

Method
------
Monte Carlo simulation (n=5 000 trials) drawing:
  1. BF count from the categorical BF distribution.
  2. Pitches per batter from pitcher mean ± 1 std (Normal, clipped to ≥1).
  3. Total 1IP pitches = sum over BF sampled pitches.
P(hit) = fraction of trials where total satisfies the line.

This is intentionally simpler than ip1_event_tree (no game script, no
handedness, no health) so we get a clean baseline gap.

Note: uses Python stdlib random only (no numpy required).
"""
from __future__ import annotations

import math
import random
from typing import Any, List, Optional, Tuple

BASELINE_ID      = "stat_model_pitches_bf"
BASELINE_VERSION = "1.0"
_DEFAULT_PPB_MEAN = 4.2
_DEFAULT_PPB_STD  = 1.1
_MIN_PITCHES_PER_BATTER = 1
_DEFAULT_BF_DIST = {"p_bf_3": 0.40, "p_bf_4": 0.35, "p_bf_gte5": 0.25}


def _normal_sample(mean: float, std: float, rng: random.Random) -> float:
    """Box-Muller normal sample, clipped to ≥ _MIN_PITCHES_PER_BATTER."""
    u1, u2 = rng.random(), rng.random()
    z = math.sqrt(-2 * math.log(max(u1, 1e-12))) * math.cos(2 * math.pi * u2)
    return max(float(_MIN_PITCHES_PER_BATTER), mean + std * z)


def _sample_bf(bf_dist: dict, rng: random.Random) -> int:
    """Sample a batters-faced count from the categorical distribution."""
    p3    = bf_dist.get("p_bf_3") or 0.0
    p4    = bf_dist.get("p_bf_4") or 0.0
    p5p   = bf_dist.get("p_bf_gte5") or bf_dist.get("p_bf_5plus") or 0.0
    total = p3 + p4 + p5p
    if total <= 0:
        # Uniform fallback
        return rng.choice([3, 4, 5])
    r = rng.random() * total
    if r < p3:
        return 3
    elif r < p3 + p4:
        return 4
    else:
        return 5   # represents "≥5"


def predict_single(
    ledger_rows: list,
    bf_distribution: Optional[dict],
    line: float,
    direction: str,
    *,
    opp_pitches_per_pa: Optional[float] = None,
    n_trials: int = 5_000,
    seed: int = 42,
) -> dict:
    """
    Run baseline C simulation for a single pitcher.

    Parameters
    ----------
    ledger_rows       From savant_1ip_ledger — used to compute pitcher ppb mean/std.
    bf_distribution   From savant_1ip_ledger bf_distribution dict.
    line              Pitch-count line.
    direction         "LESS" | "MORE".
    opp_pitches_per_pa  Optional opponent pitches/PA adjustment (not yet used
                         to modify the simulation; reported as UNAVAILABLE if None).
    n_trials          Monte Carlo trial count (default 5 000 — cheaper than WOW's 25k).
    seed              RNG seed for reproducibility.

    Returns
    -------
    dict with: probability, ppb_mean, ppb_std, bf_dist_used, n_trials,
               opp_adjustment_applied, baseline_id, baseline_version
    """
    direction = direction.upper()
    rng       = random.Random(seed)

    # Derive pitcher ppb mean/std from ledger rows
    ratios = []
    for r in (ledger_rows or []):
        pit = r.get("first_inning_pitches")
        bf  = r.get("first_inning_batters_faced")
        if pit is not None and bf and bf > 0:
            ratios.append(float(pit) / float(bf))

    if len(ratios) >= 3:
        ppb_mean = sum(ratios) / len(ratios)
        var      = sum((x - ppb_mean) ** 2 for x in ratios) / (len(ratios) - 1)
        ppb_std  = math.sqrt(var)
    else:
        ppb_mean = _DEFAULT_PPB_MEAN
        ppb_std  = _DEFAULT_PPB_STD

    bf_dist = bf_distribution or _DEFAULT_BF_DIST

    # Opponent adjustment: reported unavailable if not supplied
    opp_applied = opp_pitches_per_pa is not None
    # Future: adjust ppb_mean by (opp_pitches_per_pa / league_mean) ratio

    # Simulate
    hits = 0
    for _ in range(n_trials):
        bf_count      = _sample_bf(bf_dist, rng)
        total_pitches = sum(_normal_sample(ppb_mean, ppb_std, rng)
                            for _ in range(bf_count))
        if direction == "LESS":
            hits += int(total_pitches < line)
        else:
            hits += int(total_pitches > line)

    prob = round(hits / n_trials, 4)

    return {
        "probability":            prob,
        "ppb_mean":               round(ppb_mean, 3),
        "ppb_std":                round(ppb_std, 3),
        "ppb_n_starts":           len(ratios),
        "bf_dist_used":           bf_dist,
        "n_trials":               n_trials,
        "opp_adjustment_applied": opp_applied,
        "opp_pitches_per_pa":     opp_pitches_per_pa if opp_applied else "UNAVAILABLE",
        "baseline_id":            BASELINE_ID,
        "baseline_version":       BASELINE_VERSION,
    }


def predict_batch(
    rows: List[dict[str, Any]],
    *,
    n_trials: int = 5_000,
    seed: int = 42,
) -> List[Tuple[Optional[float], str]]:
    results = []
    for i, row in enumerate(rows):
        r = predict_single(
            row.get("ledger_rows") or [],
            row.get("bf_distribution"),
            float(row.get("line", 0)),
            row.get("direction", "LESS"),
            n_trials=n_trials,
            seed=seed + i,
        )
        results.append((r["probability"], BASELINE_ID))
    return results
