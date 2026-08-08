"""
gate_engine/fantasy_score_model/generators/mlb_hitter.py
WOW v16 — MLB Hitter Fantasy Score PA Event-Tree Generative Model

Architecture (per WOW spec)
───────────────────────────
  Stage 1  Plate-appearance opportunity distribution (Poisson-truncated)
            driven by: lineup slot, game environment, bullpen exposure
  Stage 2  Per-PA event branches (discrete multinomial):
            K  |  BB/HBP  |  1B  |  2B  |  3B  |  HR  |  other_out
  Stage 3  Runs, RBI, SB generated at game level from contextual rates
  Stage 4  Fantasy Score = 1B×3 + 2B×5 + 3B×8 + HR×10 + R×2 + RBI×2
                          + BB×2 + HBP×2 + SB×5

Context modifiers included:
  lineup_slot              (1 → 4.5 expected PA; 8 → 3.5 expected PA)
  starter_handedness       platoon split on hit rates
  bullpen_exposure         increases walk and contact rates late
  park_factor              scales HR rate
  weather_factor           global contact scale
  team_run_environment     scales run/RBI rates

⚠ Formula sourced from PrizePicks Baseball playbook cross-reference.
  Verify against settled results before trusting calibration.

can_execute = False  (unconditional)
"""
from __future__ import annotations

import math
import random

can_execute: bool = False

GENERATOR_ID = "mlb_hitter_pa_event_tree_v1"

# MLB Hitter Fantasy Score weights (PrizePicks)
_1B_W  = 3.0
_2B_W  = 5.0
_3B_W  = 8.0
_HR_W  = 10.0
_R_W   = 2.0
_RBI_W = 2.0
_BB_W  = 2.0
_HBP_W = 2.0
_SB_W  = 5.0

# Lineup slot → expected PA per game (approximation)
_SLOT_PA: dict[int, float] = {
    1: 4.5, 2: 4.4, 3: 4.3, 4: 4.2, 5: 4.0,
    6: 3.9, 7: 3.8, 8: 3.6, 9: 3.5,
}

# Population PA rates (per PA)
_POP = {
    "k_rate":      0.220,
    "bb_rate":     0.084,
    "hbp_rate":    0.009,
    "single_rate": 0.147,
    "double_rate": 0.049,
    "triple_rate": 0.004,
    "hr_rate":     0.035,
    "run_per_pa":  0.052,
    "rbi_per_pa":  0.048,
    "sb_per_game": 0.12,
}


# ---------------------------------------------------------------------------
# Poisson sampler
# ---------------------------------------------------------------------------

def _poisson(lam: float, rng: random.Random) -> int:
    if lam <= 0:
        return 0
    if lam > 30:
        return max(0, round(lam + math.sqrt(lam) * rng.gauss(0, 1)))
    L = math.exp(-lam)
    k, p = 0, 1.0
    while p > L:
        k += 1
        p *= rng.random()
    return k - 1


# ---------------------------------------------------------------------------
# Default params builder
# ---------------------------------------------------------------------------

def default_params(enrichment: dict) -> dict:
    e = enrichment or {}
    gl = e.get("game_log") or []

    # Per-PA rates from enrichment, fall back to population
    def _r(key: str) -> float:
        v = e.get(key)
        if v is not None:
            try:
                return float(v)
            except (TypeError, ValueError):
                pass
        return _POP.get(key, 0.0)

    slot      = int(e.get("lineup_slot") or 4)
    base_pa   = _SLOT_PA.get(max(1, min(9, slot)), 4.0)

    # Park factor (1.0 = neutral; >1.0 = hitter-friendly)
    park_f    = float(e.get("park_factor") or 1.0)
    # Platoon context: "advantage" → slight hit-rate lift
    platoon   = str(e.get("platoon_context") or "neutral").lower()
    plat_mult = 1.08 if platoon == "advantage" else (0.93 if platoon == "disadvantage" else 1.0)
    # Weather: 0.0 = fine, 1.0 = severe
    weather   = float(e.get("weather_factor") or 0.0)
    weather_mult = max(0.7, 1.0 - weather * 0.15)
    # Team run environment: 1.0 = league avg
    run_env   = float(e.get("team_run_environment") or 1.0)
    # Bullpen exposure (late game): increases BB and contact
    bullpen_x = float(e.get("bullpen_exposure") or 0.0)  # 0–1
    bb_bullpen_lift = bullpen_x * 0.015

    return {
        "base_pa_per_game":  base_pa,
        "pa_variance":       float(e.get("pa_variance") or 0.8),
        # Per-PA event rates (net modifiers applied)
        "k_rate":       _r("k_rate"),
        "bb_rate":      min(_r("bb_rate") + bb_bullpen_lift, 0.18),
        "hbp_rate":     _r("hbp_rate"),
        "single_rate":  _r("single_rate") * plat_mult * weather_mult,
        "double_rate":  _r("double_rate") * plat_mult * weather_mult,
        "triple_rate":  _r("triple_rate"),
        "hr_rate":      _r("hr_rate") * park_f * plat_mult,
        "run_per_pa":   _r("run_per_pa") * run_env,
        "rbi_per_pa":   _r("rbi_per_pa") * run_env,
        "sb_per_game":  _r("sb_per_game"),
        # Aliases used by diagnostics
        "pa_per_game_mean":    base_pa,
        "hr_rate_per_pa":      _r("hr_rate") * park_f * plat_mult,
        "bb_rate_per_pa":      min(_r("bb_rate") + bb_bullpen_lift, 0.18),
        "single_rate_per_pa":  _r("single_rate") * plat_mult * weather_mult,
        "sample_size":         len(gl) if isinstance(gl, list) else 0,
        "game_log":            gl,
    }


# ---------------------------------------------------------------------------
# Core generator
# ---------------------------------------------------------------------------

def generate_one(params: dict, rng: random.Random) -> float:
    """
    Generate one MLB Hitter Fantasy Score sample.

    Steps:
      1. Draw PA count for this game (truncated Poisson)
      2. For each PA: draw event from multinomial
      3. Accumulate R/RBI from game-level Poisson conditioned on PA
      4. Draw SB from separate Poisson
      5. Compute Fantasy Score
    """
    # 1. PA count (truncated Poisson, min 1)
    pa_mean = max(1.0, params.get("base_pa_per_game", 4.0))
    pa_var  = params.get("pa_variance", 0.8)
    pa_raw  = _poisson(pa_mean, rng) if pa_var < 0.5 else max(1, round(rng.gauss(pa_mean, pa_var)))
    n_pa    = max(1, min(8, pa_raw))

    # 2. Per-PA event probabilities (must sum ≤ 1)
    k_r   = max(0.0, params.get("k_rate",     0.220))
    bb_r  = max(0.0, params.get("bb_rate",    0.084))
    hbp_r = max(0.0, params.get("hbp_rate",   0.009))
    s_r   = max(0.0, params.get("single_rate", 0.147))
    d_r   = max(0.0, params.get("double_rate", 0.049))
    t_r   = max(0.0, params.get("triple_rate", 0.004))
    hr_r  = max(0.0, params.get("hr_rate",     0.035))

    # Sanity-clamp: total hit rate must not exceed 0.95
    total = k_r + bb_r + hbp_r + s_r + d_r + t_r + hr_r
    if total > 0.95:
        scale = 0.95 / total
        k_r *= scale; bb_r *= scale; hbp_r *= scale
        s_r *= scale; d_r  *= scale; t_r   *= scale; hr_r *= scale

    # Cumulative breakpoints
    cum_k   = k_r
    cum_bb  = cum_k  + bb_r
    cum_hbp = cum_bb + hbp_r
    cum_1b  = cum_hbp + s_r
    cum_2b  = cum_1b + d_r
    cum_3b  = cum_2b + t_r
    cum_hr  = cum_3b + hr_r
    # > cum_hr = other out

    singles = doubles = triples = home_runs = walks = hbp = 0

    for _ in range(n_pa):
        u = rng.random()
        if u < cum_k:
            pass              # strikeout
        elif u < cum_bb:
            walks += 1
        elif u < cum_hbp:
            hbp += 1
        elif u < cum_1b:
            singles += 1
        elif u < cum_2b:
            doubles += 1
        elif u < cum_3b:
            triples += 1
        elif u < cum_hr:
            home_runs += 1
        # else: other out (DP, flyout, etc.)

    # 3. Runs and RBI (game-level Poisson, scaled by n_pa)
    run_lam = params.get("run_per_pa", 0.052) * n_pa
    rbi_lam = params.get("rbi_per_pa", 0.048) * n_pa
    runs  = _poisson(run_lam, rng)
    rbi   = _poisson(rbi_lam, rng)

    # 4. Stolen bases (game-level)
    sb_lam = params.get("sb_per_game", 0.12)
    sb = _poisson(sb_lam, rng)

    # 5. Fantasy Score
    fs = (singles   * _1B_W
          + doubles  * _2B_W
          + triples  * _3B_W
          + home_runs* _HR_W
          + runs     * _R_W
          + rbi      * _RBI_W
          + walks    * _BB_W
          + hbp      * _HBP_W
          + sb       * _SB_W)
    return round(max(0.0, fs), 3)


# ---------------------------------------------------------------------------
# Default stress scenarios
# ---------------------------------------------------------------------------

from gate_engine.fantasy_score_model.shared import StressScenario

DEFAULT_STRESS_SCENARIOS: list[StressScenario] = [
    StressScenario(
        "platoon_disadvantage",
        "Platoon disadvantage applied: hit rates reduced by 7%",
        {"single_rate": None, "double_rate": None,
         "_platoon_mult_override": 0.93},
    ),
    StressScenario(
        "elite_starter_suppression",
        "Opposing elite starter: K rate +6%, hit rates −8%",
        {"k_rate": None, "_elite_starter": True},
    ),
    StressScenario(
        "pa_capped_4",
        "PA capped at 4 (no fifth PA opportunity)",
        {"base_pa_per_game": 4.0, "pa_variance": 0.3},
    ),
    StressScenario(
        "hr_rate_zero",
        "HR rate set to zero — power completely removed",
        {"hr_rate": 0.0, "hr_rate_per_pa": 0.0},
    ),
    StressScenario(
        "bad_weather_park",
        "Weather suppression 0.20 + pitcher-friendly park factor 0.88",
        {"weather_factor": 0.20, "park_factor": 0.88},
    ),
]
