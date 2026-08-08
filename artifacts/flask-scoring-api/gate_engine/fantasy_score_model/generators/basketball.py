"""
gate_engine/fantasy_score_model/generators/basketball.py
WOW v16 — NBA / WNBA Fantasy Score Joint Generative Model

Architecture (per WOW spec)
───────────────────────────
  Stage 1  Minutes distribution (truncated normal, conditional on game state)
  Stage 2  Game state selection (11 role / situation regimes)
  Stage 3  Possessions (minutes × pace)
  Stage 4  Per-minute Poisson rates → jointly generate PTS/REB/AST/STL/BLK/TOV
            Correlation preserved through SHARED MINUTES — all components
            conditioned on the same realized minute draw.
  Stage 5  Fantasy Score = PTS×1.0 + REB×1.2 + AST×1.5 + STL×3.0 + BLK×3.0 + TOV×(−1.0)

Game states (11)
────────────────
  normal_role             base state
  teammate_out_expansion  teammate DNP → usage / minutes expansion
  teammate_return         teammate returns from injury → usage compression
  small_ball_role         playing up a position
  secondary_creator       primary ball-handler sits → this player runs more offense
  foul_trouble            conservative usage, early sub risk
  blowout_reduced_minutes leading or trailing big → fewer minutes late
  overtime                +5 expected extra minutes
  hot_shooting            efficiency multiplier on FG-dependent stats
  cold_shooting           efficiency drag on FG-dependent stats
  stocks_spike            elevated STL+BLK game

can_execute = False  (unconditional)
"""
from __future__ import annotations

import math
import random
from typing import Any

can_execute: bool = False

# ---------------------------------------------------------------------------
# League-average baseline rates (per minute, WNBA/NBA starter)
# ---------------------------------------------------------------------------
_PTS_PER_MIN  = 0.563   # ≈18 pts per 32 min
_REB_PER_MIN  = 0.156   # ≈5 reb per 32 min
_AST_PER_MIN  = 0.094   # ≈3 ast per 32 min
_STL_PER_MIN  = 0.038   # ≈1.2 stl per 32 min
_BLK_PER_MIN  = 0.022   # ≈0.7 blk per 32 min
_TOV_PER_MIN  = 0.063   # ≈2.0 tov per 32 min

# NBA/WNBA FS weights (PrizePicks)
_FS_PTS = 1.0
_FS_REB = 1.2
_FS_AST = 1.5
_FS_STL = 3.0
_FS_BLK = 3.0
_FS_TOV = -1.0

# Overtime
_OT_EXTRA_MIN   = 5.0
_OT_PROB_DEFAULT = 0.12   # 12% WNBA / NBA OT rate

GENERATOR_ID = "basketball_joint_generative_v1"


# ---------------------------------------------------------------------------
# Poisson sampler
# ---------------------------------------------------------------------------

def _poisson(lam: float, rng: random.Random) -> int:
    if lam <= 0:
        return 0
    if lam > 50:
        return max(0, round(lam + math.sqrt(lam) * rng.gauss(0, 1)))
    L = math.exp(-lam)
    k, p = 0, 1.0
    while p > L:
        k += 1
        p *= rng.random()
    return k - 1


# ---------------------------------------------------------------------------
# Default params builder  (called from gate.py)
# ---------------------------------------------------------------------------

def default_params(enrichment: dict) -> dict:
    """
    Build generator params from GPT-supplied enrichment.
    All missing fields default to league-average values.
    """
    e = enrichment or {}
    gl = e.get("game_log") or []

    # Derive per-minute rates from enrichment or fall back to league averages
    avg_min  = float(e.get("avg_minutes") or 30.0)

    def _rate(key: str, default: float) -> float:
        v = e.get(key)
        if v is not None:
            try:
                return float(v) / max(avg_min, 1.0)
            except (TypeError, ValueError):
                pass
        return default

    pts_rate = _rate("pts_per_game",  _PTS_PER_MIN)
    reb_rate = _rate("reb_per_game",  _REB_PER_MIN)
    ast_rate = _rate("ast_per_game",  _AST_PER_MIN)
    stl_rate = _rate("stl_per_game",  _STL_PER_MIN)
    blk_rate = _rate("blk_per_game",  _BLK_PER_MIN)
    tov_rate = _rate("tov_per_game",  _TOV_PER_MIN)

    # Minutes std heuristic: ~20% of avg
    min_std  = float(e.get("minutes_std") or max(avg_min * 0.20, 3.0))

    # Game state probabilities (callers may override individually)
    ot_prob  = float(e.get("overtime_prob") or _OT_PROB_DEFAULT)

    return {
        # Rates (per minute)
        "pts_per_min":     pts_rate,
        "reb_per_min":     reb_rate,
        "ast_per_min":     ast_rate,
        "stl_per_min":     stl_rate,
        "blk_per_min":     blk_rate,
        "tov_per_min":     tov_rate,
        # Minutes distribution
        "avg_minutes":     avg_min,
        "minutes_std":     min_std,
        "min_minutes":     float(e.get("min_minutes") or 5.0),
        "max_minutes":     float(e.get("max_minutes") or 48.0),
        # Game states
        "teammate_out_prob":        float(e.get("teammate_out_prob")        or 0.08),
        "teammate_out_usage_mult":  float(e.get("teammate_out_usage_mult")  or 1.20),
        "teammate_out_min_expansion": float(e.get("teammate_out_min_expansion") or 3.0),
        "teammate_return_prob":     float(e.get("teammate_return_prob")     or 0.04),
        "teammate_return_usage_mult": float(e.get("teammate_return_usage_mult") or 0.90),
        "small_ball_prob":          float(e.get("small_ball_prob")          or 0.05),
        "small_ball_reb_mult":      float(e.get("small_ball_reb_mult")      or 1.30),
        "secondary_creator_prob":   float(e.get("secondary_creator_prob")   or 0.06),
        "secondary_creator_ast_mult":float(e.get("secondary_creator_ast_mult") or 1.30),
        "foul_trouble_prob":        float(e.get("foul_trouble_prob")        or 0.10),
        "foul_trouble_min_mult":    float(e.get("foul_trouble_min_mult")    or 0.75),
        "blowout_prob":             float(e.get("blowout_prob")             or 0.12),
        "blowout_min_mult":         float(e.get("blowout_min_mult")         or 0.82),
        "overtime_prob":            ot_prob,
        "hot_shoot_prob":           float(e.get("hot_shoot_prob")           or 0.08),
        "hot_shoot_pts_mult":       float(e.get("hot_shoot_pts_mult")       or 1.20),
        "cold_shoot_prob":          float(e.get("cold_shoot_prob")          or 0.10),
        "cold_shoot_pts_mult":      float(e.get("cold_shoot_pts_mult")      or 0.80),
        "stocks_spike_prob":        float(e.get("stocks_spike_prob")        or 0.08),
        "stocks_spike_mult":        float(e.get("stocks_spike_mult")        or 1.80),
        # DNP risk integrated as a failure regime
        "dnp_risk_prob":            float(e.get("dnp_risk_prob")            or 0.02),
        # Sample metadata
        "sample_size":              len(gl) if isinstance(gl, list) else 0,
        "game_log":                 gl,
    }


# ---------------------------------------------------------------------------
# Core generator
# ---------------------------------------------------------------------------

def generate_one(params: dict, rng: random.Random) -> float:
    """
    Generate one NBA/WNBA Fantasy Score sample.

    Steps:
      1. DNP check (failure regime — returns 0 if player doesn't play)
      2. Determine game state (one of 11 regimes)
      3. Apply state modifiers to per-minute rates and minutes draw
      4. Draw actual minutes from truncated normal
      5. Conditionally add overtime minutes
      6. Generate each component from Poisson(rate × minutes)
         → shared minutes preserves natural correlation
      7. Compute Fantasy Score
    """
    # 1. DNP risk (material failure regime)
    if rng.random() < params.get("dnp_risk_prob", 0.02):
        return 0.0

    # 2. Determine game state (mutually exclusive regimes)
    u = rng.random()
    cum = 0.0

    # State multipliers (initialized to 1.0 = no change)
    min_mult   = 1.0
    min_add    = 0.0
    pts_mult   = 1.0
    reb_mult   = 1.0
    ast_mult   = 1.0
    stl_mult   = 1.0
    blk_mult   = 1.0

    def _advance(prob: float) -> bool:
        nonlocal cum
        cum += prob
        return u < cum

    if _advance(params.get("foul_trouble_prob", 0.10)):
        # Foul trouble: reduced minutes, conservative role
        min_mult = params.get("foul_trouble_min_mult", 0.75)
        ast_mult = 0.85
        pts_mult = 0.90

    elif _advance(params.get("blowout_prob", 0.12)):
        # Blowout: starter sits late
        min_mult = params.get("blowout_min_mult", 0.82)

    elif _advance(params.get("teammate_out_prob", 0.08)):
        # Teammate out: usage expansion
        um = params.get("teammate_out_usage_mult", 1.20)
        pts_mult = um
        ast_mult = um
        min_add  = params.get("teammate_out_min_expansion", 3.0)

    elif _advance(params.get("teammate_return_prob", 0.04)):
        # Teammate returns: usage compression
        rm = params.get("teammate_return_usage_mult", 0.90)
        pts_mult = rm
        ast_mult = rm
        min_add  = -2.0

    elif _advance(params.get("small_ball_prob", 0.05)):
        # Small-ball: rebounding bump, slight pts drop
        reb_mult = params.get("small_ball_reb_mult", 1.30)
        pts_mult = 0.95

    elif _advance(params.get("secondary_creator_prob", 0.06)):
        # Secondary creator: ast and pts spike
        am = params.get("secondary_creator_ast_mult", 1.30)
        ast_mult = am
        pts_mult = 1.10

    elif _advance(params.get("hot_shoot_prob", 0.08)):
        # Hot shooting: pts efficiency up
        pts_mult = params.get("hot_shoot_pts_mult", 1.20)

    elif _advance(params.get("cold_shoot_prob", 0.10)):
        # Cold shooting: pts efficiency down
        pts_mult = params.get("cold_shoot_pts_mult", 0.80)

    elif _advance(params.get("stocks_spike_prob", 0.08)):
        # Stocks spike: defensive gem
        stl_mult = params.get("stocks_spike_mult", 1.80)
        blk_mult = params.get("stocks_spike_mult", 1.80)

    # else: normal_role — all multipliers stay at 1.0

    # 3. Draw minutes from truncated normal
    avg_min = max(1.0, params.get("avg_minutes", 30.0) * min_mult + min_add)
    std_min = max(0.5, params.get("minutes_std", 6.0))
    lo_min  = max(0.0, params.get("min_minutes", 5.0))
    hi_min  = min(48.0, params.get("max_minutes", 48.0))

    # Rejection-sample truncated normal (fast for typical ranges)
    for _ in range(20):
        m = rng.gauss(avg_min, std_min)
        if lo_min <= m <= hi_min:
            minutes = m
            break
    else:
        minutes = max(lo_min, min(hi_min, avg_min))

    # 4. Overtime: add expected extra minutes
    if rng.random() < params.get("overtime_prob", 0.12):
        minutes = min(hi_min, minutes + _OT_EXTRA_MIN)

    # 5. Generate components (Poisson, all conditioned on shared minutes)
    pts = _poisson(params.get("pts_per_min", _PTS_PER_MIN) * pts_mult * minutes, rng)
    reb = _poisson(params.get("reb_per_min", _REB_PER_MIN) * reb_mult * minutes, rng)
    ast = _poisson(params.get("ast_per_min", _AST_PER_MIN) * ast_mult * minutes, rng)
    stl = _poisson(params.get("stl_per_min", _STL_PER_MIN) * stl_mult * minutes, rng)
    blk = _poisson(params.get("blk_per_min", _BLK_PER_MIN) * blk_mult * minutes, rng)
    tov = _poisson(params.get("tov_per_min", _TOV_PER_MIN) * minutes, rng)

    # 6. Fantasy Score
    fs = (
        pts * _FS_PTS
        + reb * _FS_REB
        + ast * _FS_AST
        + stl * _FS_STL
        + blk * _FS_BLK
        + tov * _FS_TOV
    )
    return round(fs, 3)


# ---------------------------------------------------------------------------
# Default stress scenarios
# ---------------------------------------------------------------------------

from gate_engine.fantasy_score_model.shared import StressScenario

DEFAULT_STRESS_SCENARIOS: list[StressScenario] = [
    StressScenario(
        "foul_trouble_elevated",
        "Foul trouble probability raised to 25%; minutes and usage compressed",
        {"foul_trouble_prob": 0.25, "foul_trouble_min_mult": 0.70},
    ),
    StressScenario(
        "blowout_elevated",
        "Blowout probability raised to 25%; starter sits late",
        {"blowout_prob": 0.25, "blowout_min_mult": 0.78},
    ),
    StressScenario(
        "dnp_elevated",
        "DNP risk raised to 8% (injury concern / late scratch)",
        {"dnp_risk_prob": 0.08},
    ),
    StressScenario(
        "cold_shooting_double",
        "Cold shooting probability doubled; efficiency drag applied",
        {"cold_shoot_prob": 0.20, "cold_shoot_pts_mult": 0.75},
    ),
    StressScenario(
        "no_teammate_out_bump",
        "Teammate-out usage expansion removed; base role only",
        {"teammate_out_prob": 0.0, "teammate_out_usage_mult": 1.0,
         "teammate_out_min_expansion": 0.0},
    ),
]
