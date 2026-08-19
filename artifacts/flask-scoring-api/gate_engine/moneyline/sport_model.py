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
from gate_engine.moneyline.orientation import (
    OrientationResolution,
    orientation_blocker,
    resolve_participant_orientation,
)

can_execute: bool = False  # UNCONDITIONAL re-declaration

from gate_engine.moneyline.teamrankings_adapter import TR_WEIGHT_MAX, TR_WEIGHT_ZERO

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

# Weights for ensemble: how to weight each submodel when present.
# teamrankings_predictive has a hard ceiling enforced post-normalization (see below).
_ENSEMBLE_WEIGHTS = {
    "h2h_historical":         0.35,
    "elo_differential":       0.30,
    "power_rating":           0.25,
    "home_adjustment":        0.10,
    "teamrankings_predictive": 0.075,   # 7.5% default; ceiling TR_WEIGHT_MAX=10%
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


def _teamrankings_predictive(enrichment: dict[str, Any]) -> float | None:
    """
    TeamRankings secondary enrichment submodel.

    Uses ONLY enrichment["teamrankings_matchup_win_prob_home"] — a direct
    win-probability projection from TeamRankings (already in home-team perspective).

    Raw predictive ratings are NOT converted to a probability here: no calibrated
    logistic mapping exists, per WOW governance spec.

    Returns None when:
    - teamrankings_matchup_win_prob_home is absent
    - effective_weight is 0 (stale, unavailable, proxy, conflict)
    - value is outside (0.01, 0.99)

    Display odds (display_odds) are NEVER read here — they are market data
    and live only in the TeamRankings record stored in MoneylineResult.teamrankings.
    """
    eff_w = enrichment.get("teamrankings_effective_weight")
    if eff_w is not None:
        try:
            if float(eff_w) <= 0.0:
                return None
        except (TypeError, ValueError):
            return None

    prob = enrichment.get("teamrankings_matchup_win_prob_home")
    if prob is None:
        return None
    try:
        p = float(prob)
        if 0.01 < p < 0.99:
            return p
    except (TypeError, ValueError):
        pass
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
# Sport-specific specialist submodels
# ---------------------------------------------------------------------------

def _wnba_ml_specialist(enrichment: dict[str, Any]) -> float | None:
    """
    WNBA_ML_V1 specialist submodel.

    Activates only for WNBA rows (called conditionally in compute_independent_probability).
    Uses Bradley-Terry from win percentages with a lightweight rest-days adjustment.
    Never reads game_log / box_score_log.

    Returns P(home team wins) or None when insufficient data.
    """
    home_wp = enrichment.get("home_win_pct")
    away_wp = enrichment.get("away_win_pct")

    if home_wp is not None and away_wp is not None:
        try:
            hw = float(home_wp)
            aw = float(away_wp)
            if 0.0 <= hw <= 1.0 and 0.0 <= aw <= 1.0:
                denom = hw + aw
                if denom > 0.0:
                    p = hw / denom
                    # Light rest-days penalty for home team fatigue
                    rest = enrichment.get("rest_days")
                    try:
                        if rest is not None and float(rest) < 2:
                            p = max(0.01, p - 0.02)  # 2pp fatigue nudge
                    except (TypeError, ValueError):
                        pass
                    return max(0.01, min(0.99, p))
        except (TypeError, ValueError):
            pass

    # Fallback: offensive/defensive rating differential
    off_h = enrichment.get("offensive_rating")
    def_h = enrichment.get("defensive_rating")
    if off_h is not None and def_h is not None:
        try:
            # Net rating as a probability proxy via logistic
            net = float(off_h) - float(def_h)
            return _logistic(net * 0.04)   # 0.04 ≈ empirical WNBA scale
        except (TypeError, ValueError):
            pass

    return None


def _tennis_match_winner_specialist(enrichment: dict[str, Any]) -> float | None:
    """
    TENNIS_MATCH_WINNER_V1 specialist submodel.

    Activates only for ATP/WTA/TENNIS rows.
    Priority order:
      1. surface_adjusted_form (direct probability; highest fidelity)
      2. Elo differential (standard tennis rating formula)
      3. Hold/break rate dominance (serve advantage proxy)
      4. H2H win rate

    Returns P(candidate/home player wins) or None when insufficient data.
    """
    # 1. Surface-adjusted form (already a probability)
    saf = enrichment.get("surface_adjusted_form")
    if saf is not None:
        try:
            v = float(saf)
            if 0.0 < v < 1.0:
                return v
        except (TypeError, ValueError):
            pass

    # 2. Elo differential
    p_elo = _elo_differential(enrichment)
    if p_elo is not None:
        return p_elo

    # 3. Hold-rate dominance
    hold      = enrichment.get("hold_rate")
    opp_hold  = enrichment.get("opp_hold_rate")
    if hold is not None and opp_hold is not None:
        try:
            h, oh = float(hold), float(opp_hold)
            if h + oh > 0:
                return max(0.01, min(0.99, h / (h + oh)))
        except (TypeError, ValueError):
            pass

    # 4. H2H win rate (already home-player perspective)
    h2h = enrichment.get("h2h_win_rate") or enrichment.get("h2h_win_pct")
    if h2h is not None:
        try:
            v = float(h2h)
            if 0.0 < v < 1.0:
                return v
        except (TypeError, ValueError):
            pass

    return None


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
    orientation: OrientationResolution | None = None,
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

    sport = (row.get("sport") or "").upper().strip()
    orientation = orientation or resolve_participant_orientation(
        row, clean_enrichment
    )
    if not orientation.resolved:
        return {
            "independent_probability": None,
            "independent_probability_raw": None,
            "submodel_probs": {},
            "submodels_active": [],
            "ensemble_weights_used": {},
            "home_advantage_logit": 0.0,
            "soccer_three_state": None,
            "notes": [orientation_blocker(orientation)],
            "orientation_resolution": orientation.to_dict(),
            "data_contract_status": "DATA_CONTRACT_FAIL",
            "can_execute": False,
        }
    is_home = orientation.is_home
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

    # --- TeamRankings secondary enrichment submodel ---
    # Only fires when: RETRIEVED, not stale, direct matchup_win_prob_home present.
    # Raw predictive ratings are NOT converted to probability (no calibrated mapping).
    # TR display_odds are never read here.
    p_tr = _teamrankings_predictive(clean_enrichment)
    if p_tr is not None:
        submodel_probs["teamrankings_predictive"] = p_tr
        active_submodels.append("teamrankings_predictive")
        notes.append(
            f"teamrankings_predictive:active matchup_win_prob_home={p_tr:.4f}"
        )
    else:
        notes.append("teamrankings_predictive:NO_DATA_OR_INACTIVE")

    # --- WNBA specialist (WNBA_ML_V1) ---
    # Activates only for WNBA rows.  Uses BDL WNBA standings win_pct and
    # optional efficiency metrics.  Never reads game_log / box_score_log.
    if sport == "WNBA":
        p_wnba = _wnba_ml_specialist(clean_enrichment)
        if p_wnba is not None:
            submodel_probs["wnba_ml_specialist"] = p_wnba
            active_submodels.append("wnba_ml_specialist")
            notes.append(f"wnba_ml_specialist:ACTIVE p={p_wnba:.4f}")
        else:
            notes.append("wnba_ml_specialist:NO_DATA")

    # --- Tennis specialist (TENNIS_MATCH_WINNER_V1) ---
    # Activates only for ATP/WTA/TENNIS rows.  Uses surface-adjusted form,
    # Elo differential, or hold/break rate dominance.
    if sport in ("ATP", "WTA", "TENNIS"):
        p_tennis = _tennis_match_winner_specialist(clean_enrichment)
        if p_tennis is not None:
            submodel_probs["tennis_match_winner_specialist"] = p_tennis
            active_submodels.append("tennis_match_winner_specialist")
            notes.append(f"tennis_match_winner_specialist:ACTIVE p={p_tennis:.4f}")
        else:
            notes.append("tennis_match_winner_specialist:NO_DATA")

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

    # --- Weighted ensemble (normalised to present submodels) ---
    # Use configured weights, normalised to present submodels.
    raw_weights = {k: _ENSEMBLE_WEIGHTS.get(k, 0.1) for k in submodel_probs}
    total_w = sum(raw_weights.values())
    norm_weights = {k: v / total_w for k, v in raw_weights.items()}

    # Enforce TeamRankings hard ceiling (TR_WEIGHT_MAX = 10%) after normalisation.
    # When TR's normalised share exceeds the ceiling (e.g. when few other submodels
    # are active), redistribute the excess proportionally to remaining submodels.
    if "teamrankings_predictive" in norm_weights:
        tr_cap = min(
            clean_enrichment.get("teamrankings_effective_weight") or TR_WEIGHT_ZERO,
            TR_WEIGHT_MAX,
        )
        tr_normed = norm_weights["teamrankings_predictive"]
        if tr_normed > tr_cap and tr_cap > 0.0:
            excess = tr_normed - tr_cap
            norm_weights["teamrankings_predictive"] = tr_cap
            others = [k for k in norm_weights if k != "teamrankings_predictive"]
            other_total = sum(norm_weights[k] for k in others)
            if other_total > 0.0:
                for k in others:
                    norm_weights[k] += excess * (norm_weights[k] / other_total)
            notes.append(
                f"teamrankings_weight_capped:{tr_normed:.4f}->{tr_cap:.4f} "
                f"(hard_ceiling={TR_WEIGHT_MAX})"
            )
        elif tr_cap <= 0.0:
            # Should not reach here (TR excluded from submodel_probs when weight=0)
            # but defensively zero it out and redistribute
            norm_weights["teamrankings_predictive"] = 0.0
            others = [k for k in norm_weights if k != "teamrankings_predictive"]
            other_total = sum(norm_weights[k] for k in others)
            if other_total > 0.0:
                for k in norm_weights:
                    if k != "teamrankings_predictive":
                        norm_weights[k] /= other_total
            notes.append("teamrankings_weight_zeroed:effective_weight_zero")

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


def _is_home_side(
    row: dict[str, Any],
    enrichment: dict[str, Any],
) -> OrientationResolution:
    """
    Return the typed participant-orientation result.

    The legacy helper returned bool and silently treated unresolved data as
    HOME.  It is intentionally retained by name as a migration boundary, but
    no longer collapses unresolved input or raises.
    """
    return resolve_participant_orientation(row, enrichment)
