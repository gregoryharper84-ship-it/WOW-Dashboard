"""
gate_engine/fantasy_score_model/generators/mlb_pitcher.py
WOW v16 — MLB Pitcher Fantasy Score 7-Regime Unconditional Mixture

Per WOW spec: "Final Fantasy probability must be an unconditional mixture
across [all seven] regimes. Never publish a normal-workload conditional
probability as final."

Regimes (7, unconditional)
──────────────────────────
  1. normal_effective         — effective start, deep into game
  2. inefficient_surviving    — high pitch count, shorter but survives
  3. early_hook               — removed before completing 5 IP
  4. command_collapse         — walks spike, pulled early, high ER
  5. health_workload_restriction — pitch limit or injury-load concern
  6. environmental_disruption — park / weather / blowout effect on IP
  7. opponent_extension       — batter-for-batter, extra-long outing

Architecture
────────────
  Stage 1  Draw regime from regime_weights (unconditional mixture)
  Stage 2  Within regime: draw IP, K, ER, BB, W
  Stage 3  Derive QS flag (IP ≥ 6 + ER ≤ 3) and outs recorded
  Stage 4  Fantasy Score = W×6 + QS×4 + K×3 + outs×1 + ER×(−3)

Integration with existing failure-path infrastructure:
  regime_weights are derived from enrichment["failure_path_matrix"]
  when available; otherwise default priors are used.
  Failure paths alter unconditional probability — they do NOT appear
  only as narrative warnings.

can_execute = False  (unconditional)
"""
from __future__ import annotations

import math
import random
from typing import Any

can_execute: bool = False

GENERATOR_ID = "mlb_pitcher_7regime_mixture_v1"

# MLB Pitcher Fantasy Score weights (PrizePicks)
_WIN_W  = 6.0
_QS_W   = 4.0
_K_W    = 3.0
_OUT_W  = 1.0
_ER_W   = -3.0

# Default regime weights (prior; overridden by failure-path enrichment)
_DEFAULT_REGIME_WEIGHTS: dict[str, float] = {
    "normal_effective":            0.40,
    "inefficient_surviving":       0.25,
    "early_hook":                  0.15,
    "command_collapse":            0.08,
    "health_workload_restriction": 0.05,
    "environmental_disruption":    0.04,
    "opponent_extension":          0.03,
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


def _ip_to_outs(ip: float) -> int:
    """Convert innings-pitched float (6.2 = 6⅔ IP) to outs recorded."""
    whole = int(ip)
    frac  = round(ip - whole, 1)
    extra = round(frac * 10)
    return whole * 3 + extra


def _is_qs(ip: float, er: int) -> bool:
    whole = int(ip)
    frac  = round(ip - whole, 1)
    adj   = whole + round(frac * 10) / 3.0
    return adj >= 6.0 and er <= 3


# ---------------------------------------------------------------------------
# Regime parameter tables
# ---------------------------------------------------------------------------

# Each regime defines:
#   ip_mean, ip_std     — innings pitched distribution
#   k_per_ip_mean       — strikeout rate (K per IP)
#   er_per_ip_mean      — earned run rate (ER per IP)
#   bb_per_ip_mean      — walk rate (BB per IP)
#   win_prob            — probability of a pitcher win
#   pitch_limit         — optional hard cap on effective IP (forces early hook)

_REGIME_PARAMS: dict[str, dict] = {
    "normal_effective": {
        "ip_mean": 6.1, "ip_std": 0.7,
        "k_per_ip": 1.05, "er_per_ip": 0.55, "bb_per_ip": 0.40,
        "win_prob": 0.40,
        "pitch_limit": None,
    },
    "inefficient_surviving": {
        "ip_mean": 5.0, "ip_std": 0.7,
        "k_per_ip": 0.90, "er_per_ip": 0.75, "bb_per_ip": 0.65,
        "win_prob": 0.28,
        "pitch_limit": None,
    },
    "early_hook": {
        "ip_mean": 3.5, "ip_std": 0.8,
        "k_per_ip": 0.85, "er_per_ip": 1.10, "bb_per_ip": 0.70,
        "win_prob": 0.05,
        "pitch_limit": None,
    },
    "command_collapse": {
        "ip_mean": 2.5, "ip_std": 0.8,
        "k_per_ip": 0.70, "er_per_ip": 1.80, "bb_per_ip": 1.40,
        "win_prob": 0.01,
        "pitch_limit": None,
    },
    "health_workload_restriction": {
        "ip_mean": 4.5, "ip_std": 0.6,
        "k_per_ip": 0.90, "er_per_ip": 0.60, "bb_per_ip": 0.45,
        "win_prob": 0.15,
        "pitch_limit": 75,   # pitch limit forces early removal
    },
    "environmental_disruption": {
        "ip_mean": 4.8, "ip_std": 0.9,
        "k_per_ip": 0.85, "er_per_ip": 0.90, "bb_per_ip": 0.55,
        "win_prob": 0.20,
        "pitch_limit": None,
    },
    "opponent_extension": {
        "ip_mean": 7.2, "ip_std": 0.5,
        "k_per_ip": 0.95, "er_per_ip": 0.45, "bb_per_ip": 0.35,
        "win_prob": 0.45,
        "pitch_limit": None,
    },
}


# ---------------------------------------------------------------------------
# Regime weight extraction from failure-path enrichment
# ---------------------------------------------------------------------------

def _weights_from_failure_path(enrichment: dict) -> dict[str, float]:
    """
    Derive regime weights from failure_path_matrix enrichment when present.
    Falls back to default priors if matrix is absent or incomplete.
    """
    fp = enrichment.get("failure_path_matrix") or {}

    # If no failure path matrix, return defaults
    if not fp or not isinstance(fp, dict):
        return dict(_DEFAULT_REGIME_WEIGHTS)

    w = dict(_DEFAULT_REGIME_WEIGHTS)

    # Primary kill path → weights command_collapse and early_hook
    primary = fp.get("PRIMARY_KILL_PATH") or {}
    pb_str  = str(primary.get("probability_band") or "")
    nums    = [float(x) for x in __import__("re").findall(r"[\d.]+", pb_str)]
    if nums:
        primary_floor = nums[0] / 100.0   # convert % to fraction
        # High primary floor → more weight to failure regimes
        extra_fail = min(0.20, primary_floor * 0.60)
        w["early_hook"]       += extra_fail * 0.50
        w["command_collapse"] += extra_fail * 0.30
        w["inefficient_surviving"] += extra_fail * 0.20
        w["normal_effective"] = max(0.10, w["normal_effective"] - extra_fail)

    # Secondary kill path floor → affects inefficient_surviving
    secondary = fp.get("SECONDARY_KILL_PATH") or {}
    sb_str    = str(secondary.get("probability_band") or "")
    s_nums    = [float(x) for x in __import__("re").findall(r"[\d.]+", sb_str)]
    if s_nums:
        s_floor = s_nums[0] / 100.0
        extra_ineff = min(0.10, s_floor * 0.40)
        w["inefficient_surviving"] += extra_ineff
        w["normal_effective"] = max(0.10, w["normal_effective"] - extra_ineff)

    # Scenario keywords → regime adjustments
    for pname in ("PRIMARY_KILL_PATH", "SECONDARY_KILL_PATH", "BLACK_SWAN_PATH"):
        scenario = str((fp.get(pname) or {}).get("scenario") or "").lower()
        if any(kw in scenario for kw in ("leash", "pitch count", "pitch limit", "workload")):
            w["health_workload_restriction"] = min(0.20,
                w["health_workload_restriction"] + 0.05)
            w["normal_effective"] = max(0.10, w["normal_effective"] - 0.05)
        if any(kw in scenario for kw in ("weather", "rain", "wind", "cold")):
            w["environmental_disruption"] = min(0.15,
                w["environmental_disruption"] + 0.04)

    # Normalize
    total = sum(w.values()) or 1.0
    return {k: round(v / total, 6) for k, v in w.items()}


# ---------------------------------------------------------------------------
# Per-regime stat generator
# ---------------------------------------------------------------------------

def _gen_regime(regime: str, params: dict, rng: random.Random) -> dict[str, Any]:
    """Generate one start's stats within the given regime."""
    rp = dict(_REGIME_PARAMS[regime])

    # Apply enrichment overrides
    win_rate  = float(params.get("win_rate", rp["win_prob"]))
    babip_ov  = params.get("babip_override")     # optional float 0.150–0.400
    pitch_lim = params.get("pitch_limit") or rp.get("pitch_limit")

    # Draw IP
    ip_raw = rng.gauss(rp["ip_mean"], rp["ip_std"])
    ip     = max(0.0, min(9.0, ip_raw))

    # Pitch limit: convert to max IP (≈ pitch_limit / 15 pitches per inning)
    if pitch_lim is not None:
        ip_from_limit = float(pitch_lim) / 15.0
        ip = min(ip, ip_from_limit)

    # Snap to thirds: 0, 0.1, 0.2
    whole = int(ip)
    frac  = ip - whole
    if frac < 0.17:
        ip = float(whole)
    elif frac < 0.50:
        ip = whole + 0.1
    elif frac < 0.83:
        ip = whole + 0.2
    else:
        ip = float(whole + 1)

    # Draw K, ER, BB (Poisson per inning)
    k_lam  = rp["k_per_ip"]  * ip
    er_lam = rp["er_per_ip"] * ip
    bb_lam = rp["bb_per_ip"] * ip

    # BABIP override: adjust ER rate
    if babip_ov is not None:
        babip_baseline = 0.295
        babip_mult = max(0.5, min(1.8, float(babip_ov) / babip_baseline))
        er_lam *= babip_mult

    k  = _poisson(k_lam,  rng)
    er = _poisson(er_lam, rng)
    bb = _poisson(bb_lam, rng)

    # Win (Bernoulli)
    win = 1 if rng.random() < win_rate else 0

    # QS flag
    qs  = 1 if _is_qs(ip, er) else 0

    # Fantasy Score
    outs = _ip_to_outs(ip)
    fs   = (win * _WIN_W
            + qs  * _QS_W
            + k   * _K_W
            + outs * _OUT_W
            + er  * _ER_W)

    return {"ip": ip, "k": k, "er": er, "bb": bb, "win": win, "qs": qs,
            "outs": outs, "fs": round(max(0.0, fs), 3)}


# ---------------------------------------------------------------------------
# Default params builder
# ---------------------------------------------------------------------------

def default_params(enrichment: dict) -> dict:
    e  = enrichment or {}
    gl = e.get("game_log") or []

    regime_weights = _weights_from_failure_path(e)

    return {
        "regime_weights": regime_weights,
        "win_rate":       float(e.get("win_rate") or 0.33),
        "pitch_limit":    e.get("pitch_limit"),        # None or int
        "babip_override": e.get("babip_override"),     # None or float
        "sample_size":    len(gl) if isinstance(gl, list) else 0,
        "game_log":       gl,
    }


# ---------------------------------------------------------------------------
# Core generator
# ---------------------------------------------------------------------------

def generate_one(params: dict, rng: random.Random) -> float:
    """
    Generate one MLB Pitcher Fantasy Score sample from the unconditional
    7-regime mixture.

    This is an UNCONDITIONAL mixture — every simulation draws a regime
    and then generates stats within that regime.  The final distribution
    is the mixture; no regime is assumed to be the "base" or "normal" case
    for the final published probability.
    """
    weights = params.get("regime_weights") or _DEFAULT_REGIME_WEIGHTS

    # Normalize weights
    total = sum(weights.values()) or 1.0
    u     = rng.random() * total
    cum   = 0.0
    regime = "normal_effective"
    for name, w in weights.items():
        cum += w
        if u <= cum:
            regime = name
            break

    result = _gen_regime(regime, params, rng)
    return result["fs"]


# ---------------------------------------------------------------------------
# Default stress scenarios
# ---------------------------------------------------------------------------

from gate_engine.fantasy_score_model.shared import StressScenario

DEFAULT_STRESS_SCENARIOS: list[StressScenario] = [
    StressScenario(
        "early_hook_elevated",
        "Early-hook regime raised to 30%; normal_effective reduced",
        {"regime_weights": {**_DEFAULT_REGIME_WEIGHTS,
                             "early_hook": 0.30,
                             "normal_effective": 0.25}},
    ),
    StressScenario(
        "command_collapse_elevated",
        "Command collapse regime raised to 18%; walk and ER spike modeled",
        {"regime_weights": {**_DEFAULT_REGIME_WEIGHTS,
                             "command_collapse": 0.18,
                             "normal_effective": 0.30}},
    ),
    StressScenario(
        "pitch_limit_90",
        "Pitch limit enforced at 90 pitches; restricts deep outings",
        {"pitch_limit": 90},
    ),
    StressScenario(
        "win_rate_zero",
        "Win probability set to 0.0 — pitcher-win component removed from FS",
        {"win_rate": 0.0},
    ),
    StressScenario(
        "high_babip",
        "BABIP elevated to 0.360 — more hits allowed, higher ER rate",
        {"babip_override": 0.360},
    ),
]
