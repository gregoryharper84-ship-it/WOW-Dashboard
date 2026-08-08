"""
gate_engine/moneyline/game_state_sim.py
WOW v16 — Monte Carlo game-state simulator.

Adjusts the base independent probability through sport-specific regime draws.
Regime frequencies emerge from simulation draws driven by enrichment inputs —
NOT manually assigned static weights.

Supported sports:
  MLB  : starter-effectiveness × bullpen × lineup-vs-SP matchup
  NBA/WNBA : rotation/blowout/foul-trouble/OT branches
  NFL  : QB-efficiency × trench × weather × game-script
  NHL  : goalie × special-teams × OT
  SOCCER/EPL/MLS: tactical draw-preserving three-outcome Poisson
  DEFAULT : Gaussian noise only (wider uncertainty)

can_execute=False unconditional.
"""
from __future__ import annotations

import math
import random
from dataclasses import dataclass, field
from typing import Any

can_execute: bool = False

# ---------------------------------------------------------------------------
# Result type
# ---------------------------------------------------------------------------

@dataclass
class SimulationResult:
    adjusted_prob:        float
    base_prob:            float
    regime_distribution:  dict[str, float]   # regime_name → frequency (0–1)
    n_sims:               int
    sport:                str
    simulation_notes:     list[str]           = field(default_factory=list)
    simulation_ran:       bool                = True

    def to_dict(self) -> dict[str, Any]:
        return {
            "adjusted_prob":       round(self.adjusted_prob, 4),
            "base_prob":           round(self.base_prob, 4),
            "regime_distribution": {k: round(v, 4) for k, v in self.regime_distribution.items()},
            "n_sims":              self.n_sims,
            "sport":               self.sport,
            "simulation_notes":    self.simulation_notes,
            "simulation_ran":      self.simulation_ran,
        }


# ---------------------------------------------------------------------------
# Helpers
# ---------------------------------------------------------------------------

def _logistic(x: float) -> float:
    if x >= 0:
        return 1.0 / (1.0 + math.exp(-x))
    e = math.exp(x)
    return e / (1.0 + e)


def _safe_float(v: Any, default: float) -> float:
    if v is None:
        return default
    try:
        return float(v)
    except (TypeError, ValueError):
        return default


# ---------------------------------------------------------------------------
# MLB simulator
# ---------------------------------------------------------------------------

def _mlb_sim(base_prob: float, enr: dict, n: int, rng: random.Random) -> SimulationResult:
    """
    Per-game simulation:
      1. Draw SP effectiveness regime (dominant/normal/early_hook)
      2. Draw bullpen quality state (strong/average/weak)
      3. Draw lineup-vs-SP matchup quality
      4. Compute per-sim win probability and average
    """
    sp_era     = _safe_float(enr.get("sp_era"),          4.00)
    sp_whip    = _safe_float(enr.get("sp_whip"),         1.25)
    bullpen_era = _safe_float(enr.get("bullpen_era"),    4.20)
    opp_k_pct  = _safe_float(enr.get("opp_k_pct"),      0.22)

    # SP dominant if ERA < 3.50, WHIP < 1.10; early hook if ERA > 5.0 or WHIP > 1.5
    p_dominant  = max(0.0, min(0.5, (4.0 - sp_era) / 8.0 + (1.4 - sp_whip) / 1.2))
    p_early_hook = max(0.0, min(0.5, (sp_era - 4.0) / 4.0 + (sp_whip - 1.1) / 1.5))
    p_dominant   = max(0.0, p_dominant)
    p_early_hook = max(0.0, p_early_hook)
    p_normal     = max(0.0, 1.0 - p_dominant - p_early_hook)
    total = p_dominant + p_normal + p_early_hook
    p_dominant /= total; p_normal /= total; p_early_hook /= total

    # Bullpen: logistic from ERA
    p_strong_bullpen = _logistic((4.5 - bullpen_era) * 0.8)

    regime_counts: dict[str, int] = {
        "sp_dominant": 0, "sp_normal": 0, "sp_early_hook": 0,
        "bullpen_strong": 0, "bullpen_weak": 0,
    }
    sim_probs: list[float] = []

    for _ in range(n):
        # SP regime
        r = rng.random()
        if r < p_dominant:
            sp_adj = 0.06   # SP dominant → home team more likely to win
            regime_counts["sp_dominant"] += 1
        elif r < p_dominant + p_normal:
            sp_adj = 0.0
            regime_counts["sp_normal"] += 1
        else:
            sp_adj = -0.08   # early hook → bullpen exposed
            regime_counts["sp_early_hook"] += 1

        # Bullpen regime
        bull_r = rng.random()
        if bull_r < p_strong_bullpen:
            bull_adj = 0.02
            regime_counts["bullpen_strong"] += 1
        else:
            bull_adj = -0.03
            regime_counts["bullpen_weak"] += 1

        # Lineup-vs-SP K%: strong strikeout pitcher vs contact team → home advantage
        k_adj = (opp_k_pct - 0.22) * 0.20   # re-centered around league avg

        # Combine adjustments as logit perturbation
        base_logit = math.log(base_prob / (1.0 - base_prob + 1e-9))
        final_logit = base_logit + sp_adj + bull_adj + k_adj + rng.gauss(0, 0.05)
        sim_probs.append(_logistic(final_logit))

    adjusted = sum(sim_probs) / len(sim_probs)
    adjusted = max(0.01, min(0.99, adjusted))

    freq = {k: v / n for k, v in regime_counts.items()}
    notes = [
        f"SP_regime: dominant={freq['sp_dominant']:.2f} normal={freq['sp_normal']:.2f} "
        f"early_hook={freq['sp_early_hook']:.2f}",
        f"Bullpen: strong={freq['bullpen_strong']:.2f}",
    ]
    return SimulationResult(adjusted, base_prob, freq, n, "MLB", notes)


# ---------------------------------------------------------------------------
# NBA / WNBA simulator
# ---------------------------------------------------------------------------

def _nba_sim(base_prob: float, enr: dict, n: int, rng: random.Random,
             sport: str = "NBA") -> SimulationResult:
    """
    Per-possession simulation via game-state regime:
      normal_rotation / blowout_truncation / foul_trouble / late_game / overtime
    """
    pace        = _safe_float(enr.get("pace"), 98.0)
    home_off_rtg = _safe_float(enr.get("home_off_rtg"), 112.0)
    away_def_rtg = _safe_float(enr.get("away_def_rtg"), 112.0)
    ot_prob     = 0.10  # ~10% games go to OT

    # Blowout probability: high when home is heavy favorite
    p_blowout = max(0.0, min(0.40, (base_prob - 0.60) * 1.5))

    regime_counts = {
        "normal_rotation": 0, "blowout_truncation": 0,
        "foul_trouble": 0, "late_game": 0, "overtime": 0,
    }
    sim_probs: list[float] = []

    for _ in range(n):
        r = rng.random()
        logit = math.log(base_prob / (1.0 - base_prob + 1e-9))

        if r < p_blowout:
            # Blowout: winner was always going to win; slightly inflate home prob
            adj = 0.06 if base_prob > 0.5 else -0.04
            regime_counts["blowout_truncation"] += 1
        elif r < p_blowout + 0.15:
            # Foul trouble: random effect, adds variance
            adj = rng.gauss(0, 0.08)
            regime_counts["foul_trouble"] += 1
        elif rng.random() < ot_prob:
            # Overtime: narrow the probability toward 0.50
            adj = (0.50 - base_prob) * 0.4 + rng.gauss(0, 0.04)
            regime_counts["overtime"] += 1
        elif rng.random() < 0.20:
            # Late-game: high-leverage, slight variance
            adj = rng.gauss(0, 0.06)
            regime_counts["late_game"] += 1
        else:
            # Normal rotation
            eff_adj = (home_off_rtg - away_def_rtg) * 0.002
            adj = eff_adj + rng.gauss(0, 0.04)
            regime_counts["normal_rotation"] += 1

        sim_probs.append(_logistic(logit + adj))

    adjusted = max(0.01, min(0.99, sum(sim_probs) / len(sim_probs)))
    freq = {k: v / n for k, v in regime_counts.items()}
    return SimulationResult(adjusted, base_prob, freq, n, sport,
                            [f"pace={pace:.1f} home_off={home_off_rtg:.1f}"])


# ---------------------------------------------------------------------------
# NFL simulator
# ---------------------------------------------------------------------------

def _nfl_sim(base_prob: float, enr: dict, n: int, rng: random.Random) -> SimulationResult:
    """
    QB-efficiency regime × trench domination × weather modifier × game-script.
    """
    qb_passer_rating = _safe_float(enr.get("home_qb_passer_rating"), 90.0)
    opp_sacks_per_g  = _safe_float(enr.get("away_sacks_per_game"),  2.5)
    wind_mph         = _safe_float(enr.get("wind_mph"), 5.0)
    temp_f           = _safe_float(enr.get("temp_f"),   65.0)

    # QB tier
    p_elite_qb = _logistic((qb_passer_rating - 90.0) / 15.0)
    # Weather penalty: high wind reduces passing game → more variance
    weather_variance = max(0.0, (wind_mph - 15.0) * 0.004)
    if temp_f < 20:
        weather_variance += 0.03

    # Trench: sacks pressure narrows home advantage
    trench_adj_per_sim = (opp_sacks_per_g - 2.5) * -0.015

    regime_counts = {
        "elite_qb_game": 0, "normal_qb_game": 0, "ground_game": 0,
        "weather_impacted": 0, "game_script_leading": 0, "game_script_trailing": 0,
    }
    sim_probs: list[float] = []

    for _ in range(n):
        logit = math.log(base_prob / (1.0 - base_prob + 1e-9))

        # QB regime
        if rng.random() < p_elite_qb:
            qb_adj = 0.05
            regime_counts["elite_qb_game"] += 1
        else:
            qb_adj = 0.0
            regime_counts["normal_qb_game"] += 1

        # Weather
        weather_adj = rng.gauss(0, weather_variance)
        if weather_variance > 0.02:
            regime_counts["weather_impacted"] += 1

        # Trench + game-script
        gs_adj = rng.gauss(trench_adj_per_sim, 0.04)
        if base_prob > 0.6:
            regime_counts["game_script_leading"] += 1
            gs_adj += 0.02   # leading team can run clock
        else:
            regime_counts["game_script_trailing"] += 1

        # Ground game fallback in poor weather
        if wind_mph > 20:
            regime_counts["ground_game"] += 1
            gs_adj -= 0.03   # passing teams penalised

        final = _logistic(logit + qb_adj + weather_adj + gs_adj)
        sim_probs.append(final)

    adjusted = max(0.01, min(0.99, sum(sim_probs) / len(sim_probs)))
    freq = {k: v / n for k, v in regime_counts.items()}
    return SimulationResult(adjusted, base_prob, freq, n, "NFL",
                            [f"wind={wind_mph}mph temp={temp_f}F qb_tier={p_elite_qb:.2f}"])


# ---------------------------------------------------------------------------
# NHL simulator
# ---------------------------------------------------------------------------

def _nhl_sim(base_prob: float, enr: dict, n: int, rng: random.Random) -> SimulationResult:
    """
    Goalie save-percentage draw × special-teams × OT branch.
    """
    home_sv_pct = _safe_float(enr.get("home_goalie_sv_pct"), 0.912)
    away_sv_pct = _safe_float(enr.get("away_goalie_sv_pct"), 0.912)
    home_pp_pct = _safe_float(enr.get("home_pp_pct"),  0.20)
    away_pk_pct = _safe_float(enr.get("away_pk_pct"),  0.80)
    ot_freq     = 0.24   # ~24% NHL games go to OT/SO

    regime_counts = {
        "dominant_goalie": 0, "normal_goalie": 0, "pp_driven": 0, "overtime": 0,
    }
    sim_probs: list[float] = []

    for _ in range(n):
        logit = math.log(base_prob / (1.0 - base_prob + 1e-9))

        # Goalie variance
        sv_diff = home_sv_pct - away_sv_pct
        goalie_adj = sv_diff * 3.0 + rng.gauss(0, 0.05)
        if abs(sv_diff) > 0.015:
            regime_counts["dominant_goalie"] += 1
        else:
            regime_counts["normal_goalie"] += 1

        # Power play: net PP advantage
        pp_adj = (home_pp_pct - (1.0 - away_pk_pct)) * 0.5
        if abs(pp_adj) > 0.02:
            regime_counts["pp_driven"] += 1

        # OT: toss-up between 2-regulation teams
        ot_adj = 0.0
        if rng.random() < ot_freq:
            ot_adj = (0.50 - base_prob) * 0.30 + rng.gauss(0, 0.03)
            regime_counts["overtime"] += 1

        sim_probs.append(_logistic(logit + goalie_adj + pp_adj + ot_adj))

    adjusted = max(0.01, min(0.99, sum(sim_probs) / len(sim_probs)))
    freq = {k: v / n for k, v in regime_counts.items()}
    return SimulationResult(adjusted, base_prob, freq, n, "NHL")


# ---------------------------------------------------------------------------
# Soccer / EPL / MLS simulator
# ---------------------------------------------------------------------------

def _soccer_sim(base_prob: float, enr: dict, n: int, rng: random.Random,
                sport: str = "SOCCER") -> SimulationResult:
    """
    Tactical-state draw (attacking / balanced / defensive / draw-preservation).
    Final two-way P(home win) derived from three-outcome Poisson.
    """
    home_xg = _safe_float(enr.get("home_xg_per_game"), 1.40)
    away_xg = _safe_float(enr.get("away_xg_per_game"), 1.15)
    h2h_draw_rate = _safe_float(enr.get("h2h_draw_rate"), 0.28)

    # Tactical regime probabilities
    p_attack   = max(0.0, (home_xg - 1.2) / 1.5)  # high home xG → attacking
    p_def      = max(0.0, (1.2 - home_xg) / 1.5)
    p_attack   = min(0.4, p_attack)
    p_def      = min(0.4, p_def)
    p_balanced = max(0.0, 1.0 - p_attack - p_def - h2h_draw_rate)

    regime_counts = {
        "attacking": 0, "balanced": 0, "defensive": 0, "draw_preservation": 0,
    }
    sim_probs: list[float] = []

    for _ in range(n):
        r = rng.random()
        if r < p_attack:
            lam_home, lam_away = home_xg * 1.15, away_xg * 0.90
            regime_counts["attacking"] += 1
        elif r < p_attack + p_def:
            lam_home, lam_away = home_xg * 0.85, away_xg * 0.85
            regime_counts["defensive"] += 1
        elif r < p_attack + p_def + h2h_draw_rate:
            lam_home = lam_away = (home_xg + away_xg) / 2.0 * 0.75
            regime_counts["draw_preservation"] += 1
        else:
            lam_home, lam_away = home_xg, away_xg
            regime_counts["balanced"] += 1

        # Poisson score draw: simulate goals
        home_g = _poisson_draw(max(0.2, lam_home), rng)
        away_g = _poisson_draw(max(0.2, lam_away), rng)

        if home_g > away_g:
            sim_probs.append(1.0)
        elif home_g < away_g:
            sim_probs.append(0.0)
        else:
            # Draw — allocate 50/50 for binary P(home wins)
            sim_probs.append(0.5)

    # Exclude draws from the win probability (three-outcome structure)
    non_draw = [p for p in sim_probs if p != 0.5]
    draw_freq = 1.0 - len(non_draw) / len(sim_probs)
    if non_draw:
        adjusted = sum(non_draw) / len(non_draw)
    else:
        adjusted = 0.5

    adjusted = max(0.01, min(0.99, adjusted))
    freq = {k: v / n for k, v in regime_counts.items()}
    freq["draw_frequency"] = round(draw_freq, 4)

    return SimulationResult(adjusted, base_prob, freq, n, sport,
                            [f"home_xg={home_xg:.2f} away_xg={away_xg:.2f} draw_freq={draw_freq:.2f}"])


def _poisson_draw(lam: float, rng: random.Random) -> int:
    """Knuth Poisson draw."""
    L = math.exp(-lam)
    k = 0
    p = 1.0
    while p > L:
        k += 1
        p *= rng.random()
    return k - 1


# ---------------------------------------------------------------------------
# Default simulator (all other sports)
# ---------------------------------------------------------------------------

def _default_sim(base_prob: float, n: int, rng: random.Random,
                 sport: str) -> SimulationResult:
    """Gaussian noise only — returns base_prob with wider uncertainty."""
    sims = [_logistic(math.log(base_prob/(1-base_prob+1e-9)) + rng.gauss(0, 0.06))
            for _ in range(n)]
    adjusted = max(0.01, min(0.99, sum(sims) / len(sims)))
    return SimulationResult(
        adjusted, base_prob, {"gaussian_noise": 1.0}, n, sport,
        [f"DEFAULT_SIM:no_sport_specific_regime_for_{sport}"],
        simulation_ran=False,   # flag: no real sport model
    )


# ---------------------------------------------------------------------------
# Main entry point
# ---------------------------------------------------------------------------

def run_game_state_simulation(
    row:       dict[str, Any],
    enrichment: dict[str, Any],
    base_prob: float,
    n_sims:    int  = 5000,
    seed:      int | None = None,
) -> SimulationResult:
    """
    Adjust base independent probability through sport-specific Monte Carlo
    regime draws.  Returns SimulationResult with adjusted_prob, regime
    distribution, and simulation notes.

    base_prob must be the output of the independent sport model (zero market
    contamination).  This function never reads sportsbook odds.
    """
    if not (0.0 < base_prob < 1.0):
        return SimulationResult(base_prob, base_prob, {}, 0, "UNKNOWN",
                                ["INVALID_BASE_PROB:simulation_skipped"], False)

    sport = (row.get("sport") or "").upper().strip()
    rng   = random.Random(seed)

    dispatchers: dict[str, Any] = {
        "MLB":    _mlb_sim,
        "NBA":    _nba_sim,
        "WNBA":   lambda bp, e, n, r: _nba_sim(bp, e, n, r, "WNBA"),
        "NFL":    _nfl_sim,
        "NHL":    _nhl_sim,
        "SOCCER": lambda bp, e, n, r: _soccer_sim(bp, e, n, r, "SOCCER"),
        "EPL":    lambda bp, e, n, r: _soccer_sim(bp, e, n, r, "EPL"),
        "MLS":    lambda bp, e, n, r: _soccer_sim(bp, e, n, r, "MLS"),
    }

    fn = dispatchers.get(sport)
    if fn is None:
        return _default_sim(base_prob, n_sims, rng, sport)

    try:
        return fn(base_prob, enrichment, n_sims, rng)
    except Exception as exc:
        return SimulationResult(
            base_prob, base_prob, {}, 0, sport,
            [f"SIMULATION_ERROR:{type(exc).__name__}:{str(exc)[:80]}"],
            simulation_ran=False,
        )
