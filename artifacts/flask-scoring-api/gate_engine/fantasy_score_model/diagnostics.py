"""
gate_engine/fantasy_score_model/diagnostics.py
WOW v16 — Fantasy Score Dependency Metrics and Counterfactuals

All metrics are diagnostic only — no hard rejection thresholds are defined here.
Volatile dependencies (stocks, TDs, HRs, etc.) are exposed via counterfactual
probabilities so callers can understand what the prop depends on, without
automatically rejecting it.

can_execute = False  (unconditional)
"""
from __future__ import annotations

import random
from typing import Any, Callable

can_execute: bool = False

_DIAG_N = 2000   # smaller N for counterfactuals — diagnostic, not scoring


def _p_more(sims: list[float], line: float) -> float:
    if not sims:
        return 0.0
    return sum(1 for s in sims if s > line) / len(sims)


def _run(gen_fn, params, n, rng):
    return [gen_fn(params, rng) for _ in range(n)]


# ---------------------------------------------------------------------------
# Basketball (NBA / WNBA)
# ---------------------------------------------------------------------------

def basketball_diagnostics(
    generator_fn: Callable,
    base_params:  dict,
    base_sims:    list[float],
    line:         float,
    rng:          random.Random,
) -> dict[str, Any]:
    """
    Dependency metrics for NBA / WNBA Fantasy Score props.

    Metrics returned (all diagnostic, no hard gates):
      minutes_sensitivity     — delta if player gets normal (not expanded) minutes
      overtime_impact         — P(MORE) with vs without overtime probability
      stocks_spike_dependency — P(MORE) with stocks artificially zeroed
      usage_spike_dependency  — P(MORE) at base vs elevated usage
      hot_cold_asymmetry      — P(MORE) hot vs cold shooting state
    """
    base_p = _p_more(base_sims, line)

    # 1. Minutes sensitivity: force avg_minutes = normal (remove any expansion)
    normal_min = base_params.get("avg_minutes", 30.0)
    expansion  = base_params.get("teammate_out_min_expansion", 0.0)
    min_normal_params = {**base_params,
                         "avg_minutes": max(normal_min - expansion, 15.0),
                         "teammate_out_prob": 0.0}
    normal_sims = _run(generator_fn, min_normal_params, _DIAG_N, rng)
    p_normal_min = _p_more(normal_sims, line)

    # 2. Overtime: remove OT probability
    no_ot_params = {**base_params, "overtime_prob": 0.0}
    no_ot_sims   = _run(generator_fn, no_ot_params, _DIAG_N, rng)
    p_no_ot = _p_more(no_ot_sims, line)

    # 3. Stocks spike: reduce STL/BLK rates to league average (no spike)
    no_stocks_params = {**base_params,
                        "stl_per_min": min(base_params.get("stl_per_min", 0.04), 0.04),
                        "blk_per_min": min(base_params.get("blk_per_min", 0.022), 0.022),
                        "stocks_spike_prob": 0.0}
    no_stocks_sims = _run(generator_fn, no_stocks_params, _DIAG_N, rng)
    p_no_stocks = _p_more(no_stocks_sims, line)

    # 4. Usage spike (teammate_out): zero out expansion
    no_bump_params = {**base_params, "teammate_out_prob": 0.0,
                      "teammate_out_usage_mult": 1.0}
    no_bump_sims   = _run(generator_fn, no_bump_params, _DIAG_N, rng)
    p_no_bump = _p_more(no_bump_sims, line)

    # 5. Hot vs cold shooting asymmetry
    hot_params  = {**base_params, "hot_shoot_prob": 1.0, "cold_shoot_prob": 0.0}
    cold_params = {**base_params, "hot_shoot_prob": 0.0, "cold_shoot_prob": 1.0}
    hot_sims  = _run(generator_fn, hot_params, _DIAG_N, rng)
    cold_sims = _run(generator_fn, cold_params, _DIAG_N, rng)
    p_hot  = _p_more(hot_sims,  line)
    p_cold = _p_more(cold_sims, line)

    return {
        "base_p_more":              round(base_p, 4),
        "minutes_sensitivity_delta": round(base_p - p_normal_min, 4),
        "p_normal_minutes":          round(p_normal_min, 4),
        "overtime_impact":           round(base_p - p_no_ot, 4),
        "p_no_overtime":             round(p_no_ot, 4),
        "stocks_spike_dependency":   round(base_p - p_no_stocks, 4),
        "p_no_stocks_spike":         round(p_no_stocks, 4),
        "usage_bump_dependency":     round(base_p - p_no_bump, 4),
        "p_no_usage_bump":           round(p_no_bump, 4),
        "hot_shooting_uplift":       round(p_hot - base_p, 4),
        "cold_shooting_drag":        round(base_p - p_cold, 4),
        "note": "All metrics diagnostic only — no hard rejection thresholds",
    }


# ---------------------------------------------------------------------------
# NFL
# ---------------------------------------------------------------------------

def nfl_diagnostics(
    generator_fn: Callable,
    base_params:  dict,
    base_sims:    list[float],
    line:         float,
    position:     str,
    rng:          random.Random,
) -> dict[str, Any]:
    """
    Dependency metrics for NFL Fantasy Score props.

    Metrics (diagnostic only):
      td_dependency      — P(MORE) with no TD scored
      target_dependency  — P(MORE) at median targets (WR/TE)
      big_play_dependency— P(MORE) capped YPC to remove big-play variance (WR/TE/RB)
      game_script_trailing_big — P(MORE) when trailing big (pass-heavy)
      game_script_leading_big  — P(MORE) when leading big (run-heavy)
    """
    base_p = _p_more(base_sims, line)
    pos    = (position or "WR").upper()

    results: dict[str, Any] = {"base_p_more": round(base_p, 4)}

    # 1. No TD scored: zero out TD probability
    no_td_params = {**base_params,
                    "td_rate_per_target": 0.0,
                    "td_rate_per_carry": 0.0,
                    "pass_td_rate": 0.0}
    no_td_sims = _run(generator_fn, no_td_params, _DIAG_N, rng)
    p_no_td = _p_more(no_td_sims, line)
    results["td_dependency"]    = round(base_p - p_no_td, 4)
    results["p_no_td"]          = round(p_no_td, 4)

    if pos in ("WR", "TE"):
        # 2. Median target volume (remove target spike)
        median_tgt_params = {**base_params,
                              "avg_targets": min(base_params.get("avg_targets", 6.0), 6.0)}
        med_sims = _run(generator_fn, median_tgt_params, _DIAG_N, rng)
        p_med_tgt = _p_more(med_sims, line)
        results["target_volume_dependency"] = round(base_p - p_med_tgt, 4)
        results["p_median_targets"]          = round(p_med_tgt, 4)

        # 3. Big-play cap (no reception ≥ 20 yards in avg)
        no_big_params = {**base_params, "yds_per_rec": min(
            base_params.get("yds_per_rec", 10.0), 10.0)}
        no_big_sims = _run(generator_fn, no_big_params, _DIAG_N, rng)
        results["big_play_dependency"] = round(base_p - _p_more(no_big_sims, line), 4)

    if pos == "RB":
        # Target/reception volume for RB
        no_rec_params = {**base_params, "avg_targets": 0.0, "rec_yards_per_rec": 0.0}
        no_rec_sims   = _run(generator_fn, no_rec_params, _DIAG_N, rng)
        results["receiving_volume_dependency"] = round(base_p - _p_more(no_rec_sims, line), 4)

    if pos == "QB":
        # Rushing dependency
        no_rush_params = {**base_params, "avg_rush_attempts": 0.0}
        no_rush_sims   = _run(generator_fn, no_rush_params, _DIAG_N, rng)
        results["qb_rushing_dependency"] = round(base_p - _p_more(no_rush_sims, line), 4)

    # 4. Game script: trailing big (pass lean) vs leading big (run lean)
    trailing_params = {**base_params, "game_script_weights":
                       {"leading_big": 0.0, "leading_small": 0.0,
                        "neutral": 0.1, "trailing_small": 0.2, "trailing_big": 0.7}}
    leading_params  = {**base_params, "game_script_weights":
                       {"leading_big": 0.7, "leading_small": 0.2,
                        "neutral": 0.1, "trailing_small": 0.0, "trailing_big": 0.0}}
    trail_sims = _run(generator_fn, trailing_params, _DIAG_N, rng)
    lead_sims  = _run(generator_fn, leading_params,  _DIAG_N, rng)
    results["p_trailing_big_script"] = round(_p_more(trail_sims, line), 4)
    results["p_leading_big_script"]  = round(_p_more(lead_sims,  line), 4)
    results["note"] = "All metrics diagnostic only — no hard rejection thresholds"
    return results


# ---------------------------------------------------------------------------
# MLB Hitter
# ---------------------------------------------------------------------------

def mlb_hitter_diagnostics(
    generator_fn: Callable,
    base_params:  dict,
    base_sims:    list[float],
    line:         float,
    rng:          random.Random,
) -> dict[str, Any]:
    """
    Dependency metrics for MLB Hitter Fantasy Score props.

    Metrics (diagnostic only):
      fifth_pa_dependency  — P(MORE) when PA capped at 4 vs base
      hr_dependency        — P(MORE) with HR rate set to zero
      multi_hit_dependency — P(MORE) with ≤1 hit in a game (set hit rate to 0.20)
      run_env_normalized   — P(MORE) at league-avg run environment
      walk_contribution    — P(MORE) with walk rate zeroed
    """
    base_p = _p_more(base_sims, line)

    # 1. Fifth PA: cap expected PA at 4.0
    pa4_params   = {**base_params, "pa_per_game_mean": min(base_params.get("pa_per_game_mean", 4.0), 4.0)}
    pa4_sims     = _run(generator_fn, pa4_params, _DIAG_N, rng)
    p_pa4        = _p_more(pa4_sims, line)

    # 2. No HR: zero out HR rate
    no_hr_params = {**base_params, "hr_rate_per_pa": 0.0}
    no_hr_sims   = _run(generator_fn, no_hr_params, _DIAG_N, rng)
    p_no_hr      = _p_more(no_hr_sims, line)

    # 3. Multi-hit: reduce single rate to league avg
    single_hit_params = {**base_params,
                          "single_rate_per_pa": min(base_params.get("single_rate_per_pa", 0.147), 0.147)}
    single_sims       = _run(generator_fn, single_hit_params, _DIAG_N, rng)
    p_single_hit      = _p_more(single_sims, line)

    # 4. Normalized run environment (pop avg run/rbi rates)
    norm_run_params  = {**base_params,
                         "run_per_pa": 0.052, "rbi_per_pa": 0.048}
    norm_run_sims    = _run(generator_fn, norm_run_params, _DIAG_N, rng)
    p_norm_run       = _p_more(norm_run_sims, line)

    # 5. No walk contribution
    no_walk_params = {**base_params, "bb_rate_per_pa": 0.0}
    no_walk_sims   = _run(generator_fn, no_walk_params, _DIAG_N, rng)
    p_no_walk      = _p_more(no_walk_sims, line)

    return {
        "base_p_more":          round(base_p, 4),
        "fifth_pa_dependency":  round(base_p - p_pa4, 4),
        "p_pa_capped_4":        round(p_pa4, 4),
        "hr_dependency":        round(base_p - p_no_hr, 4),
        "p_no_hr":              round(p_no_hr, 4),
        "multi_hit_dependency": round(base_p - p_single_hit, 4),
        "p_single_normalized":  round(p_single_hit, 4),
        "run_env_normalized":   round(base_p - p_norm_run, 4),
        "p_norm_run_env":       round(p_norm_run, 4),
        "walk_contribution":    round(base_p - p_no_walk, 4),
        "p_no_walk":            round(p_no_walk, 4),
        "note": "All metrics diagnostic only — no hard rejection thresholds",
    }


# ---------------------------------------------------------------------------
# MLB Pitcher
# ---------------------------------------------------------------------------

def mlb_pitcher_diagnostics(
    generator_fn: Callable,
    base_params:  dict,
    base_sims:    list[float],
    line:         float,
    rng:          random.Random,
) -> dict[str, Any]:
    """
    Dependency metrics for MLB Pitcher Fantasy Score props.

    Metrics (diagnostic only):
      sixth_inning_prob    — P(pitcher survives ≥6 IP) from simulation
      pitcher_win_dep      — P(MORE) with win probability zeroed
      low_er_dependency    — P(MORE) conditional on ER=0 vs base
      babip_normalized     — P(MORE) with BABIP set to .300
      pitch_count_limited  — P(MORE) with pitch limit at 90
    """
    base_p = _p_more(base_sims, line)

    # 1. Sixth inning survival: fraction of sims with IP ≥ 6.0
    # Generator produces FS; reconstruct IP from params
    # Approximate: fraction of sims where FS is consistent with ≥6 IP
    # (This requires separate IP tracking — use _ip_sims if available,
    #  else approximate from params)
    regime_weights = base_params.get("regime_weights", {})
    p_sixth_inning = (
        regime_weights.get("normal_effective", 0.40) +
        regime_weights.get("opponent_extension", 0.03)
    )

    # 2. No pitcher win: zero win rate across all regimes
    no_win_params = {**base_params, "win_rate": 0.0}
    no_win_sims   = _run(generator_fn, no_win_params, _DIAG_N, rng)
    p_no_win      = _p_more(no_win_sims, line)

    # 3. Low ER (ER=0 or ER=1): increase normal_effective regime weight
    low_er_params = {**base_params,
                     "regime_weights": {
                         **regime_weights,
                         "normal_effective": min(1.0,
                             regime_weights.get("normal_effective", 0.40) * 1.5),
                         "command_collapse": 0.0,
                     }}
    low_er_sims  = _run(generator_fn, low_er_params, _DIAG_N, rng)
    p_low_er     = _p_more(low_er_sims, line)

    # 4. BABIP normalized to .300 (affects hits allowed and ER)
    babip_params = {**base_params, "babip_override": 0.300}
    babip_sims   = _run(generator_fn, babip_params, _DIAG_N, rng)
    p_babip_norm = _p_more(babip_sims, line)

    # 5. Pitch count limit at 90
    pitch_limit_params = {**base_params, "pitch_limit": 90}
    pitch_sims         = _run(generator_fn, pitch_limit_params, _DIAG_N, rng)
    p_pitch_limited    = _p_more(pitch_sims, line)

    return {
        "base_p_more":           round(base_p, 4),
        "p_sixth_inning_approx": round(p_sixth_inning, 4),
        "pitcher_win_dependency":round(base_p - p_no_win, 4),
        "p_no_pitcher_win":      round(p_no_win, 4),
        "low_er_uplift":         round(p_low_er - base_p, 4),
        "p_low_er_scenario":     round(p_low_er, 4),
        "babip_normalized_delta":round(base_p - p_babip_norm, 4),
        "p_babip_300":           round(p_babip_norm, 4),
        "pitch_count_limit_drag":round(base_p - p_pitch_limited, 4),
        "p_pitch_limit_90":      round(p_pitch_limited, 4),
        "note": "All metrics diagnostic only — no hard rejection thresholds",
    }
