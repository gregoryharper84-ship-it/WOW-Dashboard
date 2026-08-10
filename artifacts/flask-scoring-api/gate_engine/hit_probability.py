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
import statistics
from typing import Any, NamedTuple, Optional

logger = logging.getLogger(__name__)

# ---------------------------------------------------------------------------
# Result type
# ---------------------------------------------------------------------------

class HitProbResult(NamedTuple):
    # Core fields (original)
    hit_probability:          Optional[float]   # always the CALIBRATED value (= calibrated_probability)
    model_used:               str               # see MODEL_* constants below
    calibration_note:         str               # one-sentence human summary
    lambda_used:              Optional[float]   # Poisson λ if applicable
    sample_size:              int               # games used
    market_calibration:       Optional[float]   # no-vig prob for reference, if supplied
    # Extended output schema (Step F contract)
    # raw_model_probability and opposite_raw_probability sum to ~1.0 (complementary event).
    # calibrated_lower_bound is a conservative floor and does NOT sum to anything with its opposite.
    raw_model_probability:    Optional[float]   = None  # pre-calibration probability from the model
    calibrated_probability:   Optional[float]   = None  # = hit_probability (always calibrated)
    calibrated_lower_bound:   Optional[float]   = None  # conservative uncertainty-adjusted floor (FS only)
    opposite_raw_probability: Optional[float]   = None  # 1 - raw_model_probability (complementary side)
    # Formula provenance — populated for FS models so calibration back-tests know which
    # formula version produced each historical prediction.
    formula_registry_version: Optional[str]     = None
    formula_registry_hash:    Optional[str]     = None

MODEL_MLB_FORMULA        = "mlb_formula_v2"
MODEL_BERNOULLI          = "bernoulli_hit_rate"   # legacy; kept for test compatibility
MODEL_POISSON            = "poisson_l10"
MODEL_GAUSSIAN           = "gaussian_match_log"   # Tennis / continuous distributions
MODEL_CLAUDE             = "claude_estimate"
MODEL_NO_DATA            = "no_data"
MODEL_ERROR              = "error"
MODEL_NO_REGISTERED_MODEL    = "no_registered_model"     # unsupported sport/prop — fail closed
MODEL_FS_UNVERIFIED          = "fs_formula_unverified"   # formula not yet verified in registry
MODEL_FS_GAUSSIAN_PROVISIONAL = "gaussian_fs_provisional" # verified formula, Gaussian + PROVISIONAL flags
# 1IP_PITCHES_THROWN firewall: Poisson (mlb_1ip_pitches_poisson_v1) is unconditionally
# excluded.  The event-tree model is GPT-supplied; backend surfaces this status until
# the full posterior simulation is implemented natively.
MODEL_1IP_EVENT_TREE_REQUIRED = "1ip_event_tree_required"

_POISSON_IDEAL_SAMPLE = 10    # < this → calibration warning

# ---------------------------------------------------------------------------
# Fantasy Score formula registry — lazy-loaded singleton
# ---------------------------------------------------------------------------

_FS_REGISTRY       = None
_FS_REGISTRY_ERROR: str | None = None


def _get_fs_registry():
    """
    Load (or hot-reload) the Fantasy Score formula registry from
    config/fantasy_score_formulas.json.

    Hot-reload: checks the file's mtime on every call.  If the file has changed
    since the last load, the registry is reloaded automatically — no worker restart
    required.  This ensures that formula corrections take effect immediately and
    that every prediction record carries the formula version that actually produced
    it, which is required for calibration back-tests to work correctly.

    Gunicorn workers each hold their own in-process copy.  No inter-process
    sharing is needed since formula updates are rare and consistency within a
    request is guaranteed.
    """
    global _FS_REGISTRY, _FS_REGISTRY_ERROR
    import os as _os

    # Hot-reload check: if the file's mtime has changed, force a reload so
    # a formula edit on disk is picked up without restarting workers.
    if _FS_REGISTRY is not None and _FS_REGISTRY.file_path:
        try:
            current_mtime = _os.path.getmtime(_FS_REGISTRY.file_path)
            if current_mtime != _FS_REGISTRY.file_mtime:
                logger.info(
                    "FS formula registry file changed (mtime %.3f → %.3f); reloading.",
                    _FS_REGISTRY.file_mtime,
                    current_mtime,
                )
                _FS_REGISTRY = None
                _FS_REGISTRY_ERROR = None  # also clear error so a fixed file can reload
        except OSError:
            # File temporarily missing — keep the existing registry until next check.
            pass

    if _FS_REGISTRY is not None:
        return _FS_REGISTRY
    if _FS_REGISTRY_ERROR is not None:
        raise RuntimeError(f"FS registry unavailable: {_FS_REGISTRY_ERROR}")

    try:
        from .wow_fantasy_score.formula import FormulaRegistry
        _path = _os.path.normpath(
            _os.path.join(_os.path.dirname(__file__), "..", "config", "fantasy_score_formulas.json")
        )
        _FS_REGISTRY = FormulaRegistry.from_json(_path)
        return _FS_REGISTRY
    except Exception as exc:
        _FS_REGISTRY_ERROR = str(exc)
        logger.error("FS formula registry load failed: %s", exc)
        raise


def _fs_sport_key(sport: str, stat_key: str) -> str:
    """Map (sport, stat_key) to the formula registry key."""
    s = sport.upper()
    k = stat_key.upper().replace(" ", "_")
    if s == "MLB":
        return "MLB_PITCHER" if "PIT" in k else "MLB_HITTER"
    return s


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
# 1IP_PITCHES_THROWN is included — the model registry entry is
# mlb_1ip_pitches_poisson_v1 (PROVISIONAL).  A scalar game_log (from the
# Baseball Savant ledger) routes to Poisson; dict game_logs are coerced via
# _STAT_COL_MAP ("1IP_PITCHES_THROWN" → "first_inning_pitches").
# "ip" is a substring of "1ip_pitches_thrown" so the entry is listed explicitly
# to make the intent clear; _is_counting_stat also has a direct equality guard.
_MLB_COUNTING_STATS = {
    "so", "k", "strikeouts", "tb", "total_bases",
    "outs", "ip", "innings",
    # Plate appearances: per-game counting stat (3–5 PA/game); Poisson λ=game-log mean.
    # PA is a batter stat — normalizer maps "plate appearances"/"pa" → "PA".
    "pa", "plate_appearances",
    # 1st-inning pitches thrown: routed from Baseball Savant ledger.
    "1ip_pitches_thrown",
}

# NFL counting stats eligible for Poisson model (yards, receptions, etc.)
# Also includes TD columns — when line > 1.5 the Bernoulli branch doesn't
# fire and we fall through to Poisson ("how many TDs will he score?")
_NFL_COUNTING_STATS = {
    "pass_yds", "passing_yards", "rush_yds", "rushing_yards",
    "rec_yds", "receiving_yards", "rec", "receptions",
    "targets", "pass_att", "pass_cmp", "completions",
    "sack", "sacks", "int", "interceptions",
    "fpts", "fpts_ppr", "tackle", "kick_pts",
    # TD stats included here so line > 1.5 routes to Poisson
    "td", "pass_td", "rush_td", "rec_td", "anytime_td",
    "passing_tds", "rushing_tds", "receiving_tds",
}

# NFL TD props that are near-binary (line ≤ 1.5) — checked BEFORE counting
_NFL_TD_STATS = {"td", "pass_td", "rush_td", "rec_td", "anytime_td",
                 "passing_tds", "rushing_tds", "receiving_tds"}

# Tennis stats that use Gaussian (match-level continuous distributions)
# total_games: historical Gaussian baseline; definitive model is tennis_total_games_gate
_TENNIS_GAUSSIAN_STATS = {"fantasy_score", "fpts", "fantasy", "games_won", "games",
                           "total_games", "total_game"}
# Tennis stats where Poisson still fits (discrete counts: aces, DFs)
_TENNIS_POISSON_STATS  = {"aces", "ace", "double_faults", "df", "double_fault"}


def _is_nfl_counting(sport: str, stat_key: str) -> bool:
    """True when NFL Poisson model is appropriate."""
    return sport.upper() == "NFL" and stat_key.lower().replace(" ", "_") in _NFL_COUNTING_STATS


def _is_nfl_binary(sport: str, stat_key: str, line: float) -> bool:
    """True for near-binary NFL TD props (line ≤ 1.5)."""
    return (sport.upper() == "NFL"
            and stat_key.lower().replace(" ", "_") in _NFL_TD_STATS
            and line <= 1.5)


# Fantasy Score composite stat keys that always route to Gaussian (all sports)
_FANTASY_SCORE_COMPOSITE_KEYS = {
    "fantasy_score", "fantasy_score_hit", "fantasy_score_pit",
}


def _is_fantasy_score_composite(sport: str, stat_key: str) -> bool:
    """
    True for any Fantasy Score composite prop on any sport.
    Fantasy Score is a weighted sum of multiple component stats — it is
    NOT a single Poisson draw.  Always routes to the Gaussian model.
    """
    return stat_key.lower().replace(" ", "_") in _FANTASY_SCORE_COMPOSITE_KEYS


def _is_tennis_gaussian(sport: str, stat_key: str) -> bool:
    return sport.upper() == "TENNIS" and stat_key.lower().replace(" ", "_") in _TENNIS_GAUSSIAN_STATS


def _is_tennis_poisson(sport: str, stat_key: str) -> bool:
    return sport.upper() == "TENNIS" and stat_key.lower().replace(" ", "_") in _TENNIS_POISSON_STATS


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
        # "1ip_pitches_thrown" is explicitly listed in _MLB_COUNTING_STATS and
        # also contains "ip" as a substring, so both the direct-equality and
        # substring checks would match — the explicit set membership is
        # authoritative; no exclusion guard is needed.
        if sk_low == "1ip_pitches_thrown":
            return True
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
# Model 3: Gaussian CDF (Tennis match-level continuous distributions)
# ---------------------------------------------------------------------------

def _gaussian_model(
    game_log: list[float],
    line:     float,
    side:     str,
) -> HitProbResult:
    """
    Gaussian P(X ≥ line) using game_log mean as μ and sample std as σ.

    Used for Tennis Fantasy Score / Games Won, where the match-level
    distribution is approximately normal over L10 matches.

    Minimum 3 samples required for a meaningful standard deviation.
    """
    n = len(game_log)
    if n == 0:
        return HitProbResult(None, MODEL_NO_DATA, "No game log", None, 0, None)
    if n < 3:
        return HitProbResult(
            None, MODEL_NO_DATA,
            f"Gaussian model requires ≥3 samples (have {n})",
            None, n, None,
        )

    mu  = sum(game_log) / n
    std = statistics.stdev(game_log)

    if std < 0.01:
        # All values identical — degenerate distribution; fall back to Bernoulli
        return _bernoulli_hit_rate(game_log, line, side)

    try:
        from scipy.stats import norm as _norm
        if side.upper() in ("LESS", "UNDER"):
            prob = float(_norm.cdf(line, mu, std))
            note = f"Gaussian LESS: μ={mu:.2f} σ={std:.2f} P(X<{line})={prob:.4f} n={n}"
        else:
            prob = 1.0 - float(_norm.cdf(line, mu, std))
            note = f"Gaussian MORE: μ={mu:.2f} σ={std:.2f} P(X≥{line})={prob:.4f} n={n}"
    except ImportError:
        # scipy unavailable — use math.erf approximation
        import math as _math
        z = (line - mu) / (std * _math.sqrt(2))
        cdf = 0.5 * (1.0 + _math.erf(z))
        if side.upper() in ("LESS", "UNDER"):
            prob = cdf
        else:
            prob = 1.0 - cdf
        note = f"Gaussian(erf fallback) μ={mu:.2f} σ={std:.2f} n={n}"

    if n < _POISSON_IDEAL_SAMPLE:
        note += f"; only {n} match{'es' if n != 1 else ''}, below {_POISSON_IDEAL_SAMPLE}-match ideal"

    return HitProbResult(
        hit_probability  = round(max(0.0, min(1.0, prob)), 4),
        model_used       = MODEL_GAUSSIAN,
        calibration_note = note,
        lambda_used      = round(mu, 3),
        sample_size      = n,
        market_calibration = None,
    )


# ---------------------------------------------------------------------------
# Model 4: Claude fallback
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
        return _finalize(HitProbResult(
            hit_probability  = result.get("hit_probability"),
            model_used       = MODEL_CLAUDE,
            calibration_note = result.get("calibration_note", ""),
            lambda_used      = None,
            sample_size      = len(game_log),
            market_calibration = no_vig_prob,
        ))
    except Exception as exc:
        logger.warning("hit_probability._claude_fallback: %s", exc)
        return _finalize(HitProbResult(None, MODEL_ERROR, str(exc), None, 0, no_vig_prob))


# ---------------------------------------------------------------------------
# Output schema helper
# ---------------------------------------------------------------------------

def _finalize(result: "HitProbResult") -> "HitProbResult":
    """
    Populate the extended output-schema fields for models that don't apply
    a separate calibration step (all non-FS tiers).

    For these models hit_probability IS the raw model output, so:
      raw_model_probability    = hit_probability
      calibrated_probability   = hit_probability
      calibrated_lower_bound   = None  (no conservative floor for non-FS models)
      opposite_raw_probability = 1 − hit_probability  (complementary event)

    The FS Tier 1d path builds these fields manually and does NOT call _finalize.
    """
    raw = result.hit_probability
    opp = (round(1.0 - raw, 4) if raw is not None else None)
    return result._replace(
        raw_model_probability    = raw,
        calibrated_probability   = raw,
        calibrated_lower_bound   = None,
        opposite_raw_probability = opp,
    )


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
        return _finalize(HitProbResult(None, MODEL_NO_DATA,
                                       "No game log available — cannot compute probability",
                                       None, 0, no_vig_prob))

    # Tier 1a: MLB H/HITS — binomial PA formula from hit_probability_model.py
    if _is_mlb_hits_prop(sport, stat_key, line):
        result = _mlb_formula_v2(game_log, line, side, enrichment=enrichment)
        return _finalize(result._replace(market_calibration=no_vig_prob))

    # Tier 1b: Other near-binary MLB props (HR, RBI, SB, BB, R, TB at ≤1.5)
    # Use game-log Bernoulli so the actual line threshold is honored correctly.
    if _is_mlb_binary(sport, stat_key, line):
        result = _bernoulli_hit_rate(game_log, line, side)
        return _finalize(result._replace(market_calibration=no_vig_prob))

    # Tier 1c: Near-binary NFL TD props (PASS_TD, RUSH_TD, REC_TD, TD at ≤1.5)
    if _is_nfl_binary(sport, stat_key, line):
        result = _bernoulli_hit_rate(game_log, line, side)
        return _finalize(result._replace(market_calibration=no_vig_prob))

    # Tier 1d: Fantasy Score composites — formula registry gate + Gaussian with PROVISIONAL flags.
    # Must come before _is_counting_stat to avoid FANTASY_SCORE matching counting keyword
    # heuristics (e.g. "fantasy" in _COUNTING_STAT_KEYWORDS) and being misrouted to Poisson.
    #
    # Gate: scoring is blocked (fail-closed) until each sport's formula_definition has
    # verified_formula=true in config/fantasy_score_formulas.json.
    #
    # Output schema (Step F contract):
    #   hit_probability          = calibrated value (consumers always read this)
    #   raw_model_probability    = raw Gaussian output before calibration buffers
    #   calibrated_probability   = same as hit_probability
    #   calibrated_lower_bound   = conservative floor (calibrated − 0.05)
    #   opposite_raw_probability = 1 − raw (complementary side; MORE+LESS raw sums to ~1.0)
    #   formula_registry_version / formula_registry_hash = provenance for back-test traceability
    if _is_fantasy_score_composite(sport, stat_key):
        fs_key = _fs_sport_key(sport, stat_key)
        try:
            registry = _get_fs_registry()
            formula  = registry.get(fs_key)
            formula.validate()  # gates on verified_formula; raises FormulaError if False
        except Exception as exc:
            return HitProbResult(
                hit_probability          = None,
                model_used               = MODEL_FS_UNVERIFIED,
                calibration_note         = (
                    f"FORMULA_UNVERIFIED:{fs_key} — {exc}. "
                    f"Populate config/fantasy_score_formulas.json and set "
                    f"verified_formula=true + source + retrieved_at before scoring."
                ),
                lambda_used              = None,
                sample_size              = 0,
                market_calibration       = no_vig_prob,
                raw_model_probability    = None,
                calibrated_probability   = None,
                calibrated_lower_bound   = None,
                opposite_raw_probability = None,
                formula_registry_version = None,
                formula_registry_hash    = None,
            )

        # formula_definition.validated — run Gaussian; build the full output schema.
        raw_result = _gaussian_model(game_log, line, side)
        if raw_result.hit_probability is None:
            # Not enough samples or degenerate distribution — pass through with provenance.
            return raw_result._replace(
                market_calibration       = no_vig_prob,
                formula_registry_version = registry.file_version,
                formula_registry_hash    = registry.file_hash,
            )

        raw   = raw_result.hit_probability          # raw Gaussian probability
        n     = raw_result.sample_size or len(game_log)
        cal_buf    = 0.03 + (0.02 if n < 10 else 0.0)
        calibrated = round(max(0.0, raw - cal_buf), 4)
        lower      = round(max(0.0, calibrated - 0.05), 4)
        opp_raw    = round(1.0 - raw, 4)

        # Surface verified_settlement=False in the note so consumers know edge-case
        # handling (retirement, walkover, tiebreak) hasn't been settlement-validated.
        settlement_note = (
            " | SETTLEMENT_EDGE_CASES_UNVERIFIED" if not formula.verified_settlement else ""
        )

        note = (
            f"UNCALIBRATED_FANTASY_SCORE_COHORT | POWER_INELIGIBLE | can_execute=false"
            f"{settlement_note} | "
            f"formula={formula.sport}_v{formula.version} source={formula.source} | "
            f"{raw_result.calibration_note} | "
            f"raw_p={raw:.4f} calibrated_p={calibrated:.4f} "
            f"lower_bound={lower:.4f} (buf={cal_buf:.2f})"
        )
        return HitProbResult(
            hit_probability          = calibrated,   # always calibrated for consumers
            model_used               = MODEL_FS_GAUSSIAN_PROVISIONAL,
            calibration_note         = note,
            lambda_used              = raw_result.lambda_used,
            sample_size              = raw_result.sample_size,
            market_calibration       = no_vig_prob,
            raw_model_probability    = raw,
            calibrated_probability   = calibrated,
            calibrated_lower_bound   = lower,
            opposite_raw_probability = opp_raw,
            formula_registry_version = registry.file_version,
            formula_registry_hash    = registry.file_hash,
        )

    # Tier 2: Counting stats (NBA, WNBA, MLB SO/TB, NFL yardage/receptions,
    #          Tennis aces/double-faults)
    if _is_counting_stat(sport, stat_key) or _is_nfl_counting(sport, stat_key) \
            or _is_tennis_poisson(sport, stat_key):
        result = _poisson_model(game_log, line, side)
        return _finalize(result._replace(market_calibration=no_vig_prob))

    # Tier 2b: Gaussian — Tennis match-level continuous stats (Fantasy Score, Games Won)
    if _is_tennis_gaussian(sport, stat_key):
        result = _gaussian_model(game_log, line, side)
        return _finalize(result._replace(market_calibration=no_vig_prob))

    # Tier 3: No registered model — fail closed.
    # Rule: never substitute a generic AI estimate for an unsupported sport/prop.
    # The endpoint surfaces NO_REGISTERED_MODEL; the GPT uses the terminal_label
    # from the gate engine instead of a fabricated probability.
    return _finalize(HitProbResult(
        hit_probability  = None,
        model_used       = MODEL_NO_REGISTERED_MODEL,
        calibration_note = (
            f"No registered probability model for {sport}/{stat_key}. "
            "Use terminal_label from gate engine — do not substitute a generic formula."
        ),
        lambda_used      = None,
        sample_size      = len(game_log),
        market_calibration = no_vig_prob,
    ))


def compute_batch(
    legs:        list[dict[str, Any]],
    enrichment:  dict[str, dict[str, Any]],
    no_vig_map:  dict[str, Optional[float]] | None = None,
) -> list[dict[str, Any]]:
    """
    Compute hit probabilities for all legs in one call.

    enrichment: keyed by leg_id → {"game_log": [...], ...}
    no_vig_map: keyed by leg_id → float | None

    Returns list of dicts (Step F output schema):
      { leg_id, hit_probability, raw_model_probability, calibrated_probability,
        calibrated_lower_bound, opposite_raw_probability,
        model_used, calibration_note, lambda_used, sample_size, market_calibration,
        formula_registry_version, formula_registry_hash }

    hit_probability is always the calibrated value.  raw_model_probability and
    opposite_raw_probability sum to ~1.0.  calibrated_lower_bound is only
    populated for FS PROVISIONAL models; None for all others.
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
            "leg_id":                  leg_id,
            "hit_probability":         result.hit_probability,
            "raw_model_probability":   result.raw_model_probability,
            "calibrated_probability":  result.calibrated_probability,
            "calibrated_lower_bound":  result.calibrated_lower_bound,
            "opposite_raw_probability": result.opposite_raw_probability,
            "model_used":              result.model_used,
            "calibration_note":        result.calibration_note,
            "lambda_used":             result.lambda_used,
            "sample_size":             result.sample_size,
            "market_calibration":      result.market_calibration,
            "formula_registry_version": result.formula_registry_version,
            "formula_registry_hash":   result.formula_registry_hash,
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
    # 1st-inning pitches thrown: canonical DB / savant ledger field name is
    # "first_inning_pitches" (see gate_engine/mlb/savant_1ip_ledger.py).
    # Without this mapping _coerce_game_log would look for "1IP_PITCHES_THROWN"
    # as the dict key and find nothing, returning an empty list → no_data.
    "1IP_PITCHES_THROWN": "first_inning_pitches",
}


def _stat_to_column(stat_key: str) -> str:
    return _STAT_COL_MAP.get(stat_key.upper().strip(), stat_key.upper().strip())
