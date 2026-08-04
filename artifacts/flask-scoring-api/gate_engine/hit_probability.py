"""
gate_engine/hit_probability.py

Per-leg hit probability engine for the /analyze-and-score pipeline.

Three tiers:
  1. MLB binary props (H, HR, RBI, SB at ≤1.5 line) — Bernoulli hit rate from game_log
  2. NBA / WNBA counting stats — scipy Poisson CDF using game_log mean as λ
  3. Everything else — Claude estimate (claude_gap_fill.estimate_hit_probability)

Contract:
  compute(leg, game_log, no_vig_prob) → HitProbResult

  leg: {
    player_name, sport, stat_key, line_value, side,
    platform (optional)
  }

  game_log: list[float] — most-recent N games, descending order preferred but
            not required.  Empty list → None result via Claude fallback (or
            returns {"hit_probability": None, "model_used": "no_data"}).

  no_vig_prob: float | None — market fair-value probability for calibration.

Hard rule: never fabricate values.  If a model cannot give a defensible answer,
hit_probability is None with a clear calibration_note.
"""
from __future__ import annotations

import logging
import math
from typing import Any, NamedTuple, Optional

logger = logging.getLogger(__name__)

# ---------------------------------------------------------------------------
# Result type
# ---------------------------------------------------------------------------

class HitProbResult(NamedTuple):
    hit_probability:   Optional[float]   # 0.0–1.0, or None if unavailable
    model_used:        str               # see MODEL_* constants below
    calibration_note:  str               # one-sentence human summary
    lambda_used:       Optional[float]   # Poisson λ if applicable
    sample_size:       int               # games used
    market_calibration: Optional[float] # no-vig prob for reference, if supplied

MODEL_MLB_FORMULA = "mlb_formula_v2"
MODEL_BERNOULLI  = "bernoulli_hit_rate"   # legacy; kept for test compatibility
MODEL_POISSON    = "poisson_l10"
MODEL_CLAUDE     = "claude_estimate"
MODEL_NO_DATA    = "no_data"
MODEL_ERROR      = "error"

_POISSON_IDEAL_SAMPLE = 10    # < this → calibration warning


# ---------------------------------------------------------------------------
# Sport / stat classification
# ---------------------------------------------------------------------------

# MLB stats that are essentially binary (did the player get ≥1?) — Bernoulli from game log.
# Includes extra-base-hit markets (1B, 2B, 3B) that need game-log Bernoulli so the
# per-stat-type line threshold is honored correctly.
_MLB_BINARY_STATS = {"H", "HITS", "HR", "RBI", "SB", "BB", "R", "TB", "1B", "2B", "3B"}

# MLB hit stats specifically eligible for the mlb_formula_v2 binomial PA model.
# Only H/HITS are modelled via batting-average × PA — extra-base-hit markets
# (1B, 2B, 3B, HR, SB, RBI, BB, R, TB) have their own per-event rates that the
# generic hit formula cannot represent; they use the Bernoulli game-log path instead.
_MLB_HIT_STATS = {"H", "HITS"}

# NBA / WNBA counting stats for which Poisson is appropriate
_COUNTING_STAT_KEYWORDS = {
    "pts", "points", "reb", "rebounds", "ast", "assists",
    "stl", "steals", "blk", "blocks", "to", "tov", "turnover",
    "pra", "pr", "pa", "ra", "3pm", "fg3m", "ftm",
    "fpts", "fantasy", "pts+reb+ast", "pts+reb", "pts+ast",
    "reb+ast",
}

# MLB counting stats for which Poisson works (strikeouts, total bases, etc.)
_MLB_COUNTING_STATS = {
    "so", "k", "strikeouts", "tb", "total_bases",
    "outs", "ip", "innings",
}


def _is_mlb_binary(sport: str, stat_key: str, line: float) -> bool:
    """True for near-binary MLB props (line ≤ 1.5)."""
    sport_u = sport.upper()
    stat_u  = stat_key.upper().replace(" ", "")
    return sport_u == "MLB" and stat_u in _MLB_BINARY_STATS and line <= 1.5


def _is_mlb_hits_prop(sport: str, stat_key: str, line: float) -> bool:
    """True only for MLB 0.5-hit props (H/HITS at line < 1.0) — eligible for mlb_formula_v2.

    At line >= 1.0 (e.g. H 1.5 = "at least 2 hits"), the formula always computes
    P(>=1 hit) which is semantically wrong.  Those props route to Bernoulli instead.
    """
    sport_u = sport.upper()
    stat_u  = stat_key.upper().replace(" ", "")
    return sport_u == "MLB" and stat_u in _MLB_HIT_STATS and line < 1.0


def _is_counting_stat(sport: str, stat_key: str) -> bool:
    """True when a Poisson model is appropriate for this sport+stat combo."""
    sport_u = sport.upper()
    sk_low  = stat_key.lower().replace(" ", "").replace("+", "+")

    if sport_u in ("NBA", "WNBA"):
        # Check known counting keywords
        if any(kw in sk_low for kw in _COUNTING_STAT_KEYWORDS):
            return True
        # Combo stat (PTS+REB+AST pattern)
        if "+" in sk_low:
            return True

    if sport_u == "MLB":
        if any(kw in sk_low for kw in _MLB_COUNTING_STATS):
            return True

    return False


# ---------------------------------------------------------------------------
# Model 1: MLB formula v2  (hit_probability_model.py binomial)
# ---------------------------------------------------------------------------

_LEAGUE_AVG_PA_PER_GAME = 3.65  # mirrored from hit_probability_model.py


def _mlb_formula_v2(
    game_log:   list[float],
    line:       float,
    side:       str,
    enrichment: dict | None = None,
) -> HitProbResult:
    """
    P(1+ hits) = 1 − (1 − p_per_PA)^n_PA using hit_probability_model.py.

    batting_average is sourced from enrichment if available; otherwise derived
    from game_log (mean hits/game ÷ league-avg PA ≈ per-PA hit prob).
    """
    from .mlb.hit_probability_model import compute_hit_probability as _mlb_compute

    enr = enrichment or {}
    n   = len(game_log)

    # --- resolve inputs from enrichment or game_log ---
    batting_average = enr.get("batting_average")
    batting_order   = enr.get("batting_order")
    batter_hand     = enr.get("batter_hand")
    starter_hand    = enr.get("starter_hand")
    park_factor     = enr.get("park_factor")
    projected_pa    = enr.get("projected_pa")

    warn_parts: list[str] = []

    if batting_average is None:
        if n == 0:
            return HitProbResult(
                None, MODEL_NO_DATA,
                "No game log or batting average — cannot compute MLB formula",
                None, 0, None,
            )
        # Derive per-PA hit prob: mean hits per game ÷ league-avg PA
        mean_hits = sum(game_log) / n
        batting_average = round(mean_hits / _LEAGUE_AVG_PA_PER_GAME, 4)
        warn_parts.append(f"BA derived from game log ({n}g mean={mean_hits:.2f}), not season avg")

    if n < _POISSON_IDEAL_SAMPLE:
        warn_parts.append(f"L{n} only — L10 unavailable" if n > 0 else "L0")

    result = _mlb_compute(
        batting_average = batting_average,
        batting_order   = batting_order,
        batter_hand     = batter_hand,
        starter_hand    = starter_hand,
        park_factor     = park_factor,
        projected_pa    = projected_pa,
    )

    if side.upper() in ("LESS", "UNDER"):
        prob = result["p_zero_hits"]
        direction_note = "LESS (P(0 hits))"
    else:
        prob = result["p_at_least_one_hit"]
        direction_note = "MORE (P(≥1 hit))"

    dq   = result.get("data_quality", "PARTIAL")
    dqw  = result.get("data_quality_warning")
    n_pa = result.get("n_projected_pa", _LEAGUE_AVG_PA_PER_GAME)

    note_parts = [
        f"MLB formula v2: {direction_note}={prob:.4f}, "
        f"BA={batting_average:.3f}, n_PA={n_pa:.1f}, dq={dq}"
    ]
    if warn_parts:
        note_parts.extend(warn_parts)
    if dq == "MINIMAL" and dqw:
        note_parts.append(dqw[:100])

    return HitProbResult(
        hit_probability  = round(max(0.0, min(1.0, prob)), 4),
        model_used       = MODEL_MLB_FORMULA,
        calibration_note = "; ".join(note_parts),
        lambda_used      = None,
        sample_size      = n,
        market_calibration = None,
    )


def _bernoulli_hit_rate(
    game_log: list[float],
    line:     float,
    side:     str,
) -> HitProbResult:
    """Legacy fallback: P(X ≥ line) = fraction of games where value ≥ line."""
    n = len(game_log)
    if n == 0:
        return HitProbResult(None, MODEL_NO_DATA, "No game log", None, 0, None)

    if side.upper() in ("LESS", "UNDER"):
        hits = sum(1 for v in game_log if v < line)
        note = f"Bernoulli LESS: {hits}/{n} games below {line}"
    else:
        hits = sum(1 for v in game_log if v >= line)
        note = f"Bernoulli MORE: {hits}/{n} games ≥ {line}"

    prob = round(hits / n, 4)
    return HitProbResult(
        hit_probability  = prob,
        model_used       = MODEL_BERNOULLI,
        calibration_note = note,
        lambda_used      = None,
        sample_size      = n,
        market_calibration = None,
    )


# ---------------------------------------------------------------------------
# Model 2: Poisson CDF
# ---------------------------------------------------------------------------

def _poisson_cdf(k: int, lam: float) -> float:
    """P(X ≤ k) for X ~ Poisson(λ), computed in log-space to avoid overflow."""
    if lam <= 0:
        return 1.0
    try:
        from scipy.stats import poisson as _sp_poisson
        return float(_sp_poisson.cdf(k, lam))
    except ImportError:
        pass

    # Fallback: log-space summation
    log_lam = math.log(lam)
    log_prob = -lam
    total = math.exp(log_prob)
    for i in range(1, min(k + 1, 1000)):
        log_prob += log_lam - math.log(i)
        total += math.exp(log_prob)
    return min(total, 1.0)


def _poisson_model(
    game_log: list[float],
    line:     float,
    side:     str,
) -> HitProbResult:
    """Poisson P(X ≥ threshold) using game_log mean as λ."""
    n = len(game_log)
    if n == 0:
        return HitProbResult(None, MODEL_NO_DATA, "No game log", None, 0, None)

    lam = sum(game_log) / n

    if side.upper() in ("LESS", "UNDER"):
        # P(X < line) — for non-integer line this is P(X ≤ floor(line))
        threshold = math.floor(line)
        prob = _poisson_cdf(threshold, lam)
        note = f"Poisson LESS: λ={lam:.2f}, P(X≤{threshold})={prob:.4f}, n={n}"
    else:
        # P(X ≥ ceil(line)) for non-integer; P(X ≥ line) for integer
        if line != math.floor(line):
            threshold = math.ceil(line)
        else:
            threshold = int(line)
        # P(X ≥ threshold) = 1 - P(X ≤ threshold - 1)
        cdf_below = _poisson_cdf(threshold - 1, lam) if threshold > 0 else 0.0
        prob = 1.0 - cdf_below
        note = f"Poisson MORE: λ={lam:.2f}, P(X≥{threshold})={prob:.4f}, n={n}"

    # Append sample-size warning when below ideal threshold
    if n < _POISSON_IDEAL_SAMPLE:
        note += f"; Poisson λ from {n} game{'s' if n != 1 else ''}, below {_POISSON_IDEAL_SAMPLE}-game ideal"

    return HitProbResult(
        hit_probability  = round(max(0.0, min(1.0, prob)), 4),
        model_used       = MODEL_POISSON,
        calibration_note = note,
        lambda_used      = round(lam, 3),
        sample_size      = n,
        market_calibration = None,
    )


# ---------------------------------------------------------------------------
# Model 3: Claude fallback
# ---------------------------------------------------------------------------

def _claude_fallback(
    player_name: str,
    sport:       str,
    stat_key:    str,
    line:        float,
    side:        str,
    game_log:    list[float],
    no_vig_prob: Optional[float],
) -> HitProbResult:
    if not game_log:
        return HitProbResult(None, MODEL_NO_DATA,
                             "No game log — cannot estimate probability",
                             None, 0, no_vig_prob)
    try:
        from gate_engine.claude_gap_fill import estimate_hit_probability
        result = estimate_hit_probability(
            player_name  = player_name,
            sport        = sport,
            prop_type    = stat_key,
            side         = side,
            line         = line,
            game_log     = game_log,
            no_vig_prob  = no_vig_prob,
        )
        return HitProbResult(
            hit_probability  = result.get("hit_probability"),
            model_used       = MODEL_CLAUDE,
            calibration_note = result.get("calibration_note", ""),
            lambda_used      = None,
            sample_size      = len(game_log),
            market_calibration = no_vig_prob,
        )
    except Exception as exc:
        logger.warning("hit_probability._claude_fallback: %s", exc)
        return HitProbResult(None, MODEL_ERROR, str(exc), None, 0, no_vig_prob)


# ---------------------------------------------------------------------------
# Public API
# ---------------------------------------------------------------------------

def compute(
    leg:         dict[str, Any],
    game_log:    list[float],
    no_vig_prob: Optional[float] = None,
    enrichment:  Optional[dict[str, Any]] = None,
) -> HitProbResult:
    """
    Compute hit probability for one leg.

    leg keys used:
      player_name, sport, stat_key, line_value, side

    enrichment: per-leg enrichment dict (batting_average, batting_order, etc.)

    Returns HitProbResult (NamedTuple).
    """
    sport    = (leg.get("sport") or "").upper()
    stat_key = leg.get("stat_key") or leg.get("prop_type") or ""
    line     = float(leg.get("line_value") or leg.get("line") or 0)
    side     = (leg.get("side") or "MORE").upper()
    player   = leg.get("player_name_resolved") or leg.get("player_name") or leg.get("player") or ""

    if not game_log:
        return HitProbResult(None, MODEL_NO_DATA,
                             "No game log available — cannot compute probability",
                             None, 0, no_vig_prob)

    # Tier 1a: MLB H/HITS — binomial PA formula from hit_probability_model.py
    if _is_mlb_hits_prop(sport, stat_key, line):
        result = _mlb_formula_v2(game_log, line, side, enrichment=enrichment)
        return result._replace(market_calibration=no_vig_prob)

    # Tier 1b: Other near-binary MLB props (HR, RBI, SB, BB, R, TB at ≤1.5)
    # Use game-log Bernoulli so the actual line threshold is honored correctly.
    if _is_mlb_binary(sport, stat_key, line):
        result = _bernoulli_hit_rate(game_log, line, side)
        return result._replace(market_calibration=no_vig_prob)

    # Tier 2: Counting stats (NBA, WNBA, MLB SO/TB)
    if _is_counting_stat(sport, stat_key):
        result = _poisson_model(game_log, line, side)
        return result._replace(market_calibration=no_vig_prob)

    # Tier 3: Claude fallback
    return _claude_fallback(player, sport, stat_key, line, side, game_log, no_vig_prob)


def compute_batch(
    legs:        list[dict[str, Any]],
    enrichment:  dict[str, dict[str, Any]],
    no_vig_map:  dict[str, Optional[float]] | None = None,
) -> list[dict[str, Any]]:
    """
    Compute hit probabilities for all legs in one call.

    enrichment: keyed by leg_id → {"game_log": [...], ...}
    no_vig_map: keyed by leg_id → float | None

    Returns list of dicts:
      { leg_id, hit_probability, model_used, calibration_note,
        lambda_used, sample_size, market_calibration }
    """
    no_vig_map = no_vig_map or {}
    results = []
    for leg in legs:
        leg_id   = leg.get("leg_id") or leg.get("row_id") or ""
        enr      = enrichment.get(leg_id) or {}
        raw_log  = enr.get("game_log") or []
        # Flatten per-game dicts to floats if game_log is a list of dicts
        game_log = _coerce_game_log(raw_log, leg)
        no_vig   = no_vig_map.get(leg_id) or enr.get("sharp_no_vig_prob")

        result = compute(leg, game_log, no_vig, enrichment=enr)
        results.append({
            "leg_id":            leg_id,
            "hit_probability":   result.hit_probability,
            "model_used":        result.model_used,
            "calibration_note":  result.calibration_note,
            "lambda_used":       result.lambda_used,
            "sample_size":       result.sample_size,
            "market_calibration": result.market_calibration,
        })
    return results


# ---------------------------------------------------------------------------
# Helpers
# ---------------------------------------------------------------------------

def _coerce_game_log(
    raw: list,
    leg: dict[str, Any],
) -> list[float]:
    """
    Convert game_log to a flat list of floats.

    Handles:
      - list[float | int]           → pass-through
      - list[dict]  (per-game dicts) → extract the relevant stat column(s)
    """
    if not raw:
        return []
    if isinstance(raw[0], (int, float)):
        return [float(v) for v in raw if v is not None]

    if isinstance(raw[0], dict):
        stat_key = (leg.get("stat_key") or leg.get("prop_type") or "").upper()
        col = _stat_to_column(stat_key)
        values = []
        for game in raw:
            if "+" in (stat_key or ""):
                # Combo stat: sum the component columns
                parts = [p.strip() for p in stat_key.split("+")]
                total = sum(float(game.get(_stat_to_column(p), 0) or 0) for p in parts)
                values.append(total)
            else:
                v = game.get(col)
                if v is not None:
                    try:
                        values.append(float(v))
                    except (ValueError, TypeError):
                        pass
        return values

    return []


_STAT_COL_MAP = {
    "PTS": "PTS", "POINTS": "PTS",
    "REB": "REB", "REBOUNDS": "REB",
    "AST": "AST", "ASSISTS": "AST",
    "STL": "STL", "STEALS": "STL",
    "BLK": "BLK", "BLOCKS": "BLK",
    "TOV": "TOV", "TO": "TOV", "TURNOVERS": "TOV",
    "3PM": "FG3M", "FG3M": "FG3M",
    "FTM": "FTM",
    "MIN": "MIN",
    "H": "H", "HITS": "H",
    "HR": "HR", "HOME RUNS": "HR",
    "RBI": "RBI",
    "SB": "SB",
    "SO": "SO", "K": "SO", "STRIKEOUTS": "SO",
    "BB": "BB",
}


def _stat_to_column(stat_key: str) -> str:
    return _STAT_COL_MAP.get(stat_key.upper().strip(), stat_key.upper().strip())
