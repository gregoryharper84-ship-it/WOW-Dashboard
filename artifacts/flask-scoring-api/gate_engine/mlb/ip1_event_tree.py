"""
gate_engine/mlb/ip1_event_tree.py

WOW v16 MLB 1st-Inning Pitches Thrown — Monte Carlo Event-Tree Simulator.

Implements a batter-by-batter Monte Carlo simulation for the number of pitches
thrown in the first inning. The model accounts for the BF (batters faced)
distribution and per-batter pitch count draws.

can_execute = False  — unconditional TEST_ONLY governance flag.
MODEL_QUALIFIED_HOLD ceiling applies to all results from this simulator.

Architecture
------------
1. Draw BF outcome from the bf_distribution multinomial (p_bf_3, p_bf_4, p_bf_gte5).
2. For each batter faced, draw pitches_per_batter from a clipped Gaussian (min=3).
3. Fourth-batter dependency rule: when BF ≥ 4, total ≥ 4 * 3 = 12 pitches (hard floor).
4. Sum total first-inning pitches; accumulate MORE/LESS counts over n_trials.
5. Outputs bidirectional (MORE, LESS) raw probabilities summing to ≤ 1.0.

Status: TEST_ONLY — hit_probability=None in pipeline; MODEL_QUALIFIED_HOLD ceiling.

Public API
----------
simulate_1ip(bf_distribution, pitches_per_batter_dist, line_value, side, n_trials=25000)
    → dict

model_1ip_event_tree_required_result()
    → dict  (sentinel when bf_distribution is absent)
"""
from __future__ import annotations

import math
import random
from typing import Any

can_execute = False  # WOW governance: unconditional TEST_ONLY

# Model identifier constants
MODEL_1IP_MONTE_CARLO = "1ip_monte_carlo_event_tree_v1"
MODEL_1IP_REQUIRED    = "1ip_event_tree_required"

# Physical lower bound: a batter cannot face fewer than 3 pitches in a well-pitched AB
_MIN_PITCHES_PER_BATTER = 3

# Fourth-batter dependency enforcement:
# When BF ≥ 4, total pitches must be ≥ 4 batters × 3 minimum pitches = 12
_FOURTH_BATTER_MIN_TOTAL = _MIN_PITCHES_PER_BATTER * 4  # = 12


def _box_muller_normal(mu: float, sigma: float) -> float:
    """
    Draw one sample from N(mu, sigma) using Box-Muller transform.

    Uses only stdlib (math, random) — no numpy dependency.
    Retries if u1 is too close to zero (degenerate log).
    """
    while True:
        u1 = random.random()
        u2 = random.random()
        if u1 > 1e-12:
            break
    mag = math.sqrt(-2.0 * math.log(u1))
    z   = mag * math.cos(2.0 * math.pi * u2)
    return mu + sigma * z


def _draw_pitches_for_batter(ppb_dist: dict[str, Any]) -> int:
    """
    Draw the number of pitches for one batter from the per-batter distribution.

    Applies _MIN_PITCHES_PER_BATTER as a hard floor — a batter cannot be faced
    in fewer than 3 pitches in practice (3-pitch strikeout / 3-pitch hit scenario).
    """
    mean = float(ppb_dist.get("mean") or 4.2)
    std  = float(ppb_dist.get("std")  or 1.1)
    raw  = _box_muller_normal(mean, std)
    return max(_MIN_PITCHES_PER_BATTER, round(raw))


def _simulate_one_trial(
    p_bf_3:    float,
    p_bf_4:    float,
    p_bf_gte5: float,
    ppb_dist:  dict[str, Any],
) -> int:
    """
    Run a single Monte Carlo trial and return total first-inning pitch count.

    Steps:
    1. Draw BF outcome from multinomial (p_bf_3, p_bf_4, p_bf_gte5).
    2. Simulate pitches for each batter.
    3. Enforce fourth-batter dependency minimum when BF ≥ 4.
    """
    r = random.random()
    if r < p_bf_3:
        n_batters = 3
    elif r < p_bf_3 + p_bf_4:
        n_batters = 4
    else:
        n_batters = 5

    total_pitches = sum(_draw_pitches_for_batter(ppb_dist) for _ in range(n_batters))

    # Fourth-batter dependency: hard floor when 4+ batters faced
    if n_batters >= 4:
        total_pitches = max(total_pitches, _FOURTH_BATTER_MIN_TOTAL)

    return total_pitches


def simulate_1ip(
    bf_distribution:         dict[str, Any],
    pitches_per_batter_dist: dict[str, Any],
    line_value:              float,
    side:                    str,
    n_trials:                int = 25000,
) -> dict[str, Any]:
    """
    Monte Carlo simulation for 1st-inning pitches thrown.

    Parameters
    ----------
    bf_distribution : dict
        Keys: p_bf_3, p_bf_4, p_bf_gte5 (floats summing to ~1.0).
    pitches_per_batter_dist : dict
        Keys: mean (float), std (float).
    line_value : float
        The over/under line.
    side : str
        "MORE" or "LESS" (case-insensitive).
    n_trials : int
        Number of Monte Carlo trials (enforced minimum: 25,000).

    Returns
    -------
    dict with:
        raw_more    : float — P(total_pitches > line_value)
        raw_less    : float — P(total_pitches < line_value)
        model_used  : str   — MODEL_1IP_MONTE_CARLO
        n_trials    : int
        mean_pitches: float
        can_execute : False
    """
    n_trials = max(n_trials, 25000)  # enforce minimum

    # Normalize BF distribution so probabilities sum to 1.0
    p_bf_3    = float(bf_distribution.get("p_bf_3")    or 0.0)
    p_bf_4    = float(bf_distribution.get("p_bf_4")    or 0.0)
    p_bf_gte5 = float(bf_distribution.get("p_bf_gte5") or 0.0)
    total = p_bf_3 + p_bf_4 + p_bf_gte5
    if total < 1e-9:
        # Degenerate input — uniform over 3/4/5 as safe fallback
        p_bf_3 = p_bf_4 = p_bf_gte5 = 1.0 / 3.0
    else:
        p_bf_3    /= total
        p_bf_4    /= total
        p_bf_gte5 /= total

    count_more        = 0
    count_less        = 0
    total_pitches_sum = 0.0

    for _ in range(n_trials):
        pitches = _simulate_one_trial(p_bf_3, p_bf_4, p_bf_gte5, pitches_per_batter_dist)
        total_pitches_sum += pitches
        if pitches > line_value:
            count_more += 1
        elif pitches < line_value:
            count_less += 1
        # Ties (pitches == line_value for integer lines) count as neither MORE nor LESS

    raw_more     = round(count_more / n_trials, 4)
    raw_less     = round(count_less / n_trials, 4)
    mean_pitches = round(total_pitches_sum / n_trials, 2)

    return {
        "raw_more":     raw_more,
        "raw_less":     raw_less,
        "model_used":   MODEL_1IP_MONTE_CARLO,
        "n_trials":     n_trials,
        "mean_pitches": mean_pitches,
        "can_execute":  False,
    }


def model_1ip_event_tree_required_result() -> dict[str, Any]:
    """
    Return a sentinel dict for the absent-bf_distribution case.

    The pipeline gate stamps this when no bf_distribution was provided,
    blocking Poisson and requiring the GPT to supply bf_distribution.
    """
    return {
        "hit_probability":     None,
        "model_used":          MODEL_1IP_REQUIRED,
        "calibration_note":    (
            "MODEL_1IP_EVENT_TREE_REQUIRED: "
            "DATA_CONTRACT_FAIL:missing_field:first_inning_bf_distribution; "
            "mlb_1ip_pitches_poisson_v1 blocked; "
            "event-tree model required; TEST_ONLY; can_execute=False"
        ),
        "can_execute":         False,
        "probability_publishable": False,
    }
