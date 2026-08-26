"""
gate_engine/fantasy_score_model/generators/nfl.py
WOW v16 — NFL Fantasy Score Joint Generative Model

Architecture (per WOW spec)
───────────────────────────
  Stage 1  Game script selection (5 scripts: leading_big, leading_small,
            neutral, trailing_small, trailing_big)
  Stage 2  Snap and opportunity distribution by position (QB/RB/WR/TE)
  Stage 3  Position-specific component generation:
            QB  — dropbacks → attempts/completions/pass_yds/pass_td/int/rush
            RB  — snaps → carries/rush_yds/rec_targets/rec/rec_yds/td
            WR/TE — snaps/routes → targets/receptions/rec_yds/rec_td
  Stage 4  Fantasy Score via NFL formula
            (PassYds/25 + PassTD×4 + INT×−2 + RushYds/10 + RushTD×6
             + RecYds/10 + RecTD×6 + Rec×0.5 + FumLost×−2)
            ⚠ RECEPTION_WEIGHT = 0.5 (half-PPR) — UNCONFIRMED

Game scripts (5)
────────────────
  leading_big     — run lean, limited pass volume
  leading_small   — slight run lean, near normal
  neutral         — standard script
  trailing_small  — slight pass lean
  trailing_big    — heavy pass lean, faster pace

can_execute = False  (unconditional)
"""
from __future__ import annotations

import math
import random

can_execute: bool = False

# NFL Fantasy Score weights
_PASS_YDS_DIV  = 25.0
_RUSH_YDS_DIV  = 10.0
_REC_YDS_DIV   = 10.0
_PASS_TD_W     = 4.0
_INT_W         = -2.0
_RUSH_TD_W     = 6.0
_REC_TD_W      = 6.0
_RECEPTION_W   = 0.5   # ⚠ UNCONFIRMED — half-PPR assumption
_FUM_LOST_W    = -2.0

GENERATOR_ID        = "nfl_positional_generative_v1"
FORMULA_FLAG        = "NFL_RECEPTION_WEIGHT_UNCONFIRMED"

# Game script modifiers (multiplier on pass / rush volume)
_GAME_SCRIPTS: dict[str, dict] = {
    "leading_big":     {"pass_mult": 0.70, "rush_mult": 1.35, "pace": 0.90},
    "leading_small":   {"pass_mult": 0.88, "rush_mult": 1.10, "pace": 0.95},
    "neutral":         {"pass_mult": 1.00, "rush_mult": 1.00, "pace": 1.00},
    "trailing_small":  {"pass_mult": 1.15, "rush_mult": 0.88, "pace": 1.05},
    "trailing_big":    {"pass_mult": 1.40, "rush_mult": 0.65, "pace": 1.15},
}

_DEFAULT_SCRIPT_WEIGHTS: dict[str, float] = {
    "leading_big":    0.12,
    "leading_small":  0.18,
    "neutral":        0.40,
    "trailing_small": 0.18,
    "trailing_big":   0.12,
}


# ---------------------------------------------------------------------------
# Poisson / normal helpers
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


def _gamma_yards(mean: float, cv: float, rng: random.Random) -> float:
    """
    Draw yards from a gamma distribution with given mean and coefficient of
    variation.  cv ≈ 0.5–0.8 gives realistic yards-per-game spread.
    """
    if mean <= 0:
        return 0.0
    shape = (1.0 / max(cv, 0.01)) ** 2
    scale = mean / shape
    # Approximate gamma via sum of exponentials (shape must be integer for speed)
    # For non-integer shape, use normal approximation when shape > 5
    if shape >= 5:
        std = mean * cv
        val = rng.gauss(mean, std)
    else:
        # Knuth/Ahrens–Dieter: approx gamma as sum of iid Exp(1/scale) when shape≈1
        val = -math.log(max(rng.random(), 1e-12)) * scale * shape
    return max(0.0, val)


# ---------------------------------------------------------------------------
# Default params builder
# ---------------------------------------------------------------------------

def default_params(enrichment: dict, position: str) -> dict:
    e = enrichment or {}
    pos = (position or "WR").upper()

    script_weights = e.get("game_script_weights") or _DEFAULT_SCRIPT_WEIGHTS
    # Normalize
    total = sum(script_weights.values()) or 1.0
    script_weights = {k: v / total for k, v in script_weights.items()}

    base: dict = {
        "position":       pos,
        "game_script_weights": script_weights,
        "weather_penalty": float(e.get("weather_penalty") or 0.0),   # 0–0.3
        # Fumble lost rate
        "fumble_lost_rate": float(e.get("fumble_lost_rate") or 0.008),
        "sample_size":       len(e.get("game_log") or []),
    }

    if pos == "QB":
        base.update({
            "avg_dropbacks":   float(e.get("avg_dropbacks")   or 35.0),
            "comp_rate":       float(e.get("comp_rate")        or 0.65),
            "yds_per_att":     float(e.get("yds_per_att")      or 7.2),
            "pass_td_rate":    float(e.get("pass_td_rate")     or 0.044),  # per attempt
            "int_rate":        float(e.get("int_rate")         or 0.020),  # per attempt
            "avg_rush_attempts": float(e.get("avg_rush_attempts") or 5.0),
            "rush_yds_per_carry": float(e.get("rush_yds_per_carry") or 5.2),
            "rush_td_rate":    float(e.get("rush_td_rate")     or 0.06),   # per rush attempt
        })
    elif pos == "RB":
        base.update({
            "avg_carries":       float(e.get("avg_carries")       or 14.0),
            "rush_yds_per_carry":float(e.get("rush_yds_per_carry")or 4.2),
            "rush_td_rate":      float(e.get("rush_td_rate")      or 0.045),
            "avg_targets":       float(e.get("avg_targets")       or 4.0),
            "rec_rate":          float(e.get("rec_rate")          or 0.80),
            "rec_yards_per_rec": float(e.get("rec_yards_per_rec") or 7.5),
            "td_rate_per_target":float(e.get("td_rate_per_target")or 0.04),
        })
    else:
        # WR / TE
        base.update({
            "avg_targets":    float(e.get("avg_targets")    or 6.0),
            "rec_rate":       float(e.get("rec_rate")       or 0.68),
            "yds_per_rec":    float(e.get("yds_per_rec")    or 11.0),
            "td_rate_per_target": float(e.get("td_rate_per_target") or 0.06),
            # Carries rare for WR, common for 'gadget' TE
            "avg_rush_attempts": float(e.get("avg_rush_attempts") or 0.0),
            "rush_yds_per_carry":float(e.get("rush_yds_per_carry")or 5.0),
            "rush_td_rate":   float(e.get("rush_td_rate")   or 0.0),
        })

    return base


# ---------------------------------------------------------------------------
# Game script selector
# ---------------------------------------------------------------------------

def _select_script(weights: dict[str, float], rng: random.Random) -> dict:
    u = rng.random()
    cum = 0.0
    for name, w in weights.items():
        cum += w
        if u < cum:
            return _GAME_SCRIPTS.get(name, _GAME_SCRIPTS["neutral"])
    return _GAME_SCRIPTS["neutral"]


# ---------------------------------------------------------------------------
# Position-specific generators
# ---------------------------------------------------------------------------

def _gen_qb(params: dict, script: dict, rng: random.Random) -> float:
    weather_pen = params.get("weather_penalty", 0.0)
    pass_mult   = script["pass_mult"] * (1.0 - weather_pen)

    dropbacks = max(0, _poisson(params["avg_dropbacks"] * pass_mult, rng))
    attempts  = max(0, round(dropbacks * min(1.0, rng.gauss(0.98, 0.04))))
    comps     = 0
    pass_yds  = 0.0
    pass_tds  = 0
    ints      = 0

    for _ in range(attempts):
        if rng.random() < params["comp_rate"]:
            comps += 1
            pass_yds += _gamma_yards(params["yds_per_att"], 0.70, rng)
        if rng.random() < params["pass_td_rate"]:
            pass_tds += 1
        if rng.random() < params["int_rate"]:
            ints += 1

    # Rushing
    rush_att  = max(0, _poisson(params["avg_rush_attempts"] * script["pace"], rng))
    rush_yds  = sum(_gamma_yards(params["rush_yds_per_carry"], 0.90, rng)
                    for _ in range(rush_att))
    rush_tds  = sum(1 for _ in range(rush_att) if rng.random() < params["rush_td_rate"])
    fum_lost  = 1 if rng.random() < params.get("fumble_lost_rate", 0.008) * rush_att else 0

    fs = (pass_yds / _PASS_YDS_DIV
          + pass_tds * _PASS_TD_W
          + ints * _INT_W
          + rush_yds / _RUSH_YDS_DIV
          + rush_tds * _RUSH_TD_W
          + fum_lost * _FUM_LOST_W)
    return round(max(0.0, fs), 3)


def _gen_rb(params: dict, script: dict, rng: random.Random) -> float:
    weather_pen = params.get("weather_penalty", 0.0)
    rush_mult   = script["rush_mult"]
    pass_mult   = script["pass_mult"]

    carries  = max(0, _poisson(params["avg_carries"] * rush_mult, rng))
    rush_yds = sum(_gamma_yards(params["rush_yds_per_carry"], 0.85, rng)
                   for _ in range(carries))
    rush_tds = sum(1 for _ in range(carries) if rng.random() < params["rush_td_rate"])

    targets  = max(0, _poisson(params["avg_targets"] * pass_mult, rng))
    recs     = sum(1 for _ in range(targets) if rng.random() < params["rec_rate"])
    rec_yds  = sum(_gamma_yards(params["rec_yards_per_rec"], 0.75, rng)
                   for _ in range(recs))
    rec_tds  = sum(1 for _ in range(targets) if rng.random() < params["td_rate_per_target"])

    fum_lost = 1 if rng.random() < params.get("fumble_lost_rate", 0.008) * max(carries, 1) else 0

    fs = (rush_yds / _RUSH_YDS_DIV
          + rush_tds * _RUSH_TD_W
          + rec_yds  / _REC_YDS_DIV
          + rec_tds  * _REC_TD_W
          + recs     * _RECEPTION_W
          + fum_lost * _FUM_LOST_W)
    return round(max(0.0, fs), 3)


def _gen_wr_te(params: dict, script: dict, rng: random.Random) -> float:
    weather_pen = params.get("weather_penalty", 0.0)
    pass_mult   = script["pass_mult"] * (1.0 - weather_pen * 0.5)

    targets = max(0, _poisson(params["avg_targets"] * pass_mult, rng))
    recs    = sum(1 for _ in range(targets) if rng.random() < params["rec_rate"])
    rec_yds = sum(_gamma_yards(params["yds_per_rec"], 0.80, rng) for _ in range(recs))
    rec_tds = sum(1 for _ in range(targets) if rng.random() < params["td_rate_per_target"])

    # Gadget carries (rare for WR, zero for most TE)
    rush_att = max(0, _poisson(params.get("avg_rush_attempts", 0.0), rng))
    rush_yds = sum(_gamma_yards(params.get("rush_yds_per_carry", 5.0), 0.90, rng)
                   for _ in range(rush_att))
    rush_tds = sum(1 for _ in range(rush_att) if rng.random() < params.get("rush_td_rate", 0.0))

    fs = (rec_yds  / _REC_YDS_DIV
          + rec_tds  * _REC_TD_W
          + recs     * _RECEPTION_W
          + rush_yds / _RUSH_YDS_DIV
          + rush_tds * _RUSH_TD_W)
    return round(max(0.0, fs), 3)


# ---------------------------------------------------------------------------
# Public interface
# ---------------------------------------------------------------------------

def generate_one(params: dict, rng: random.Random) -> float:
    """
    Generate one NFL Fantasy Score sample.
    Dispatches to position-specific generator after selecting game script.
    """
    script = _select_script(params.get("game_script_weights",
                                        _DEFAULT_SCRIPT_WEIGHTS), rng)
    pos    = (params.get("position") or "WR").upper()

    if pos == "QB":
        return _gen_qb(params, script, rng)
    if pos == "RB":
        return _gen_rb(params, script, rng)
    return _gen_wr_te(params, script, rng)


# ---------------------------------------------------------------------------
# Default stress scenarios
# ---------------------------------------------------------------------------

from gate_engine.fantasy_score_model.shared import StressScenario

DEFAULT_STRESS_SCENARIOS: list[StressScenario] = [
    StressScenario(
        "leading_big_lock",
        "Game script locked to leading_big — heavy run lean, minimal pass volume",
        {"game_script_weights": {"leading_big": 1.0, "leading_small": 0.0,
                                  "neutral": 0.0, "trailing_small": 0.0,
                                  "trailing_big": 0.0}},
    ),
    StressScenario(
        "no_td",
        "TD rates zeroed — pure yardage scoring only",
        {"pass_td_rate": 0.0, "rush_td_rate": 0.0,
         "td_rate_per_target": 0.0, "rec_td_rate": 0.0},
    ),
    StressScenario(
        "bad_weather",
        "Weather penalty at 0.25 — pass efficiency and volume reduced",
        {"weather_penalty": 0.25},
    ),
    StressScenario(
        "low_target_volume",
        "Target volume reduced by 30% (WR/TE) or carry volume by 20% (RB)",
        {"avg_targets": None, "avg_carries": None,
         "_stress_volume_cut": 0.70},   # handled in generate_one if needed
    ),
]
