"""
gate_engine/moneyline/sport_model.py
WOW v16 — Independent Sport Model

Computes win probability from NON-MARKET inputs ONLY:
  - Historical H2H win rate (game_log / season_log)
  - Elo differential (home_elo, away_elo in enrichment)
  - Power-rating differential (home_power, away_power in enrichment)
  - Home-court / home-field advantage
  - Sport-specific adjustments (draw base rate for soccer)

Hard boundary: raises IndependentModelContaminationError if any odds-derived
field reaches this function's enrichment argument.

can_execute=False unconditional.
"""
from __future__ import annotations

import math
from typing import Any

from gate_engine.moneyline.types import (
    check_independence_boundary,
    IndependentModelContaminationError,
    can_execute,
)

can_execute: bool = False  # UNCONDITIONAL re-declaration

# ---------------------------------------------------------------------------
# Sport-specific parameters
# ---------------------------------------------------------------------------

# Home advantage (additive logit adjustment)
_HOME_ADV_LOGIT: dict[str, float] = {
    "NBA":    0.25,
    "WNBA":   0.20,
    "MLB":    0.10,
    "NFL":    0.30,
    "NHL":    0.15,
    "SOCCER": 0.35,
    "EPL":    0.35,
    "MLS":    0.30,
    "ATP":    0.0,    # neutral site or court-surface adjusted separately
    "WTA":    0.0,
    "TENNIS": 0.0,
    "MMA":    0.0,
    "UFC":    0.0,
}

# Logistic regression coefficient for Elo differential (per 100 Elo points)
_ELO_COEF = 0.173      # ≈ ln(10)/13.35  (standard Elo formula)

# Logistic regression coefficient for power-rating differential
_POWER_COEF = 0.05

# Soccer: baseline draw rate (league-average)
_SOCCER_DRAW_BASE = 0.27

# Weights for ensemble: how to weight each submodel when present
_ENSEMBLE_WEIGHTS = {
    "h2h_historical": 0.35,
    "elo_differential": 0.30,
    "power_rating": 0.25,
    "home_adjustment": 0.10,
}

# ---------------------------------------------------------------------------
# Logistic function
# ---------------------------------------------------------------------------

def _logistic(x: float) -> float:
    """Sigmoid (logistic) function, numerically stable."""
    if x >= 0:
        return 1.0 / (1.0 + math.exp(-x))
    ex = math.exp(x)
    return ex / (1.0 + ex)


# ---------------------------------------------------------------------------
# Individual submodels
# ---------------------------------------------------------------------------

def _h2h_historical(enrichment: dict[str, Any]) -> float | None:
    """
    Historical head-to-head win rate from game_log or season stats.
    Returns home-team win probability or None if insufficient data.
    """
    # Prefer explicit H2H win rate
    h2h = enrichment.get("h2h_win_rate") or enrichment.get("h2h_win_pct")
    if h2h is not None:
        try:
            v = float(h2h)
            if 0.0 < v < 1.0:
                return v
        except (TypeError, ValueError):
            pass

    # Derive from season win percentages
    home_wp = enrichment.get("home_win_pct") or enrichment.get("season_win_pct")
    away_wp = enrichment.get("away_win_pct") or enrichment.get("opponent_win_pct")
    if home_wp is not None and away_wp is not None:
        try:
            hw = float(home_wp)
            aw = float(away_wp)
            if 0.0 <= hw <= 1.0 and 0.0 <= aw <= 1.0:
                # Bradley-Terry-like: P(home) = hw / (hw + aw)
                denom = hw + aw
                if denom > 0.0:
                    return hw / denom
        except (TypeError, ValueError):
            pass

    # Fall back to game_log win rate
    game_log = enrichment.get("game_log")
    if isinstance(game_log, list) and len(game_log) >= 3:
        # game_log entries expected to have "result": "W"|"L"|"D"
        # or numeric (1=win, 0=loss)
        wins = 0
        total = 0
        for g in game_log:
            if isinstance(g, dict):
                r = str(g.get("result") or "").upper()
                if r == "W":
                    wins += 1; total += 1
                elif r in ("L", "D"):
                    total += 1
            elif isinstance(g, (int, float)):
                wins += int(g > 0); total += 1
        if total >= 3:
            return wins / total

    return None


def _elo_differential(enrichment: dict[str, Any]) -> float | None:
    """
    Logistic model from Elo differential.
    home_elo, away_elo must be present in enrichment.
    """
    home_elo = enrichment.get("home_elo")
    away_elo = enrichment.get("away_elo")
    if home_elo is None or away_elo is None:
        return None
    try:
        diff = float(home_elo) - float(away_elo)
        # Standard Elo win-probability formula
        return 1.0 / (1.0 + 10.0 ** (-diff / 400.0))
    except (TypeError, ValueError):
        return None


def _power_rating(enrichment: dict[str, Any]) -> float | None:
    """
    Logistic model from power-rating differential.
    home_power, away_power must be present.
    """
    hp = enrichment.get("home_power") or enrichment.get("home_power_rating")
    ap = enrichment.get("away_power") or enrichment.get("away_power_rating")
    if hp is None or ap is None:
        return None
    try:
        diff = float(hp) - float(ap)
        return _logistic(_POWER_COEF * diff)
    except (TypeError, ValueError):
        return None


def _home_advantage_prior(sport: str) -> float:
    """
    Return a simple home-team win probability from historical home advantage.
    Used as a weak prior when no other data is available.
    """
    # Convert logit home advantage to probability
    logit = _HOME_ADV_LOGIT.get(sport.upper(), 0.0)
    return _logistic(logit)


# ---------------------------------------------------------------------------
# Soccer draw adjustment
# ---------------------------------------------------------------------------

def _soccer_draw_adjusted(raw_home_prob: float, enrichment: dict[str, Any]) -> dict[str, float]:
    """
    Given a binary P(home_wins_excl_draw), distribute into three outcomes.
    Uses historical H2H draw rate when available; falls back to league average.
    """
    h2h_draw_rate = enrichment.get("h2h_draw_rate")
    league_draw_rate = enrichment.get("league_draw_rate")
    draw_base = (
        float(h2h_draw_rate) if h2h_draw_rate is not None else
        float(league_draw_rate) if league_draw_rate is not None else
        _SOCCER_DRAW_BASE
    )
    draw_base = max(0.05, min(0.45, draw_base))
    p_decisive = 1.0 - draw_base
    # Apportion decisive outcomes proportionally to raw_home_prob
    # raw_home_prob here represents P(home | decisive)
    raw_home_prob = max(0.01, min(0.99, raw_home_prob))
    p_home = p_decisive * raw_home_prob
    p_away = p_decisive * (1.0 - raw_home_prob)
    p_draw = draw_base
    total = p_home + p_draw + p_away
    return {
        "p_home": round(p_home / total, 4),
        "p_draw": round(p_draw / total, 4),
        "p_away": round(p_away / total, 4),
    }


# ---------------------------------------------------------------------------
# Main entry point
# ---------------------------------------------------------------------------

def compute_independent_probability(
    row: dict[str, Any],
    clean_enrichment: dict[str, Any],
) -> dict[str, Any]:
    """
    Compute an independent win probability using only non-market inputs.

    Parameters
    ----------
    row              : The candidate row (sport, team, opponent, etc.)
    clean_enrichment : Enrichment with ALL odds-derived fields already removed
                       (call strip_odds_fields before passing).

    Returns
    -------
    {
      "independent_probability": float | None,
      "submodel_probs": {name: float},   # used by disagreement auditor
      "submodels_active": [str],
      "ensemble_weights_used": {name: float},
      "home_advantage_logit": float,
      "soccer_three_state": dict | None,
      "notes": [str],
    }

    Raises IndependentModelContaminationError if odds fields are detected.
    """
    # Hard boundary check
    check_independence_boundary(clean_enrichment)

    sport    = (row.get("sport") or "").upper().strip()
    is_home  = _is_home_side(row, clean_enrichment)
    is_soccer = sport in ("SOCCER", "EPL", "MLS")

    submodel_probs:   dict[str, float] = {}
    active_submodels: list[str]        = []
    notes:            list[str]        = []

    # --- H2H historical ---
    p_h2h = _h2h_historical(clean_enrichment)
    if p_h2h is not None:
        submodel_probs["h2h_historical"] = p_h2h
        active_submodels.append("h2h_historical")
    else:
        notes.append("h2h_historical:NO_DATA")

    # --- Elo differential ---
    p_elo = _elo_differential(clean_enrichment)
    if p_elo is not None:
        submodel_probs["elo_differential"] = p_elo
        active_submodels.append("elo_differential")
    else:
        notes.append("elo_differential:NO_DATA")

    # --- Power rating ---
    p_pwr = _power_rating(clean_enrichment)
    if p_pwr is not None:
        submodel_probs["power_rating"] = p_pwr
        active_submodels.append("power_rating")
    else:
        notes.append("power_rating:NO_DATA")

    if not submodel_probs:
        # No data at all — cannot produce independent probability
        notes.append("NO_SUBMODEL_DATA:independent_probability_unavailable")
        return {
            "independent_probability": None,
            "submodel_probs":          {},
            "submodels_active":        [],
            "ensemble_weights_used":   {},
            "home_advantage_logit":    _HOME_ADV_LOGIT.get(sport, 0.0),
            "soccer_three_state":      None,
            "notes":                   notes,
        }

    # --- Weighted ensemble (equal weights among active submodels) ---
    # Use configured weights, normalised to present submodels
    raw_weights = {k: _ENSEMBLE_WEIGHTS.get(k, 0.1) for k in submodel_probs}
    total_w = sum(raw_weights.values())
    norm_weights = {k: v / total_w for k, v in raw_weights.items()}

    ensemble_prob = sum(norm_weights[k] * submodel_probs[k] for k in submodel_probs)

    # ---------------------------------------------------------------------------
    # Convention: all submodels above return P(home team wins).
    # The pipeline is responsible for inverting to P(away wins) for away candidates.
    # We annotate is_home and probability_perspective so downstream layers
    # and the pipeline inversion step are unambiguous.
    #
    # We deliberately do NOT apply a separate home-advantage logit adjustment here:
    # H2H win-rate data already embeds the historical home-field effect; adding a
    # logit on top would double-count it.  The home-advantage constant is annotated
    # for observability only.
    # ---------------------------------------------------------------------------
    home_adv_logit = _HOME_ADV_LOGIT.get(sport, 0.0)
    ensemble_prob = max(0.01, min(0.99, ensemble_prob))

    notes.append(
        f"probability_perspective=HOME_WIN "
        f"is_home={is_home} "
        f"home_adv_logit={home_adv_logit:+.3f} "
        f"(pipeline will invert to P(candidate_wins) for away rows)"
    )

    # --- Soccer three-state (uses home-win perspective consistently) ---
    soccer_3state = None
    if is_soccer:
        soccer_3state = _soccer_draw_adjusted(ensemble_prob, clean_enrichment)
        notes.append("soccer_1x2_draw_adjustment_applied")

    return {
        "independent_probability":   round(ensemble_prob, 4),   # P(home wins) — see note above
        "probability_perspective":   "HOME_WIN",                 # always home-team perspective here
        "is_home":                   is_home,
        "submodel_probs":            {k: round(v, 4) for k, v in submodel_probs.items()},
        "submodels_active":          active_submodels,
        "ensemble_weights_used":     {k: round(v, 4) for k, v in norm_weights.items()},
        "home_advantage_logit":      home_adv_logit,
        "soccer_three_state":        soccer_3state,
        "notes":                     notes,
    }


def _is_home_side(row: dict[str, Any], enrichment: dict[str, Any]) -> bool:
    """
    Determine whether the candidate row represents the home side.

    Understands all home/away conventions used in the pipeline:
      HOME:  "HOME", "TRUE", "1", "YES", "VS", "vs", "vs."   (home team notation)
      AWAY:  "AWAY", "FALSE", "0", "NO", "@"                  (away team notation)

    app.py emits home_away="vs" for home games and home_away="@" for away games
    (see scoring endpoint lines that build the outright row from game objects).
    """
    home_flag = row.get("home_away") or row.get("is_home") or enrichment.get("home_away")
    if home_flag is not None:
        normalized = str(home_flag).strip().upper()
        # Explicit away markers — anything not in this set is treated as home
        _AWAY_MARKERS = {"AWAY", "FALSE", "0", "NO", "@"}
        if normalized in _AWAY_MARKERS:
            return False
        # Explicit home markers (and catch-all for unrecognized values)
        return True
    # Default: assume home if side not specified
    return True
