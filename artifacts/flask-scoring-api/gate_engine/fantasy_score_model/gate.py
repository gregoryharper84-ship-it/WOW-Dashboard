"""
gate_engine/fantasy_score_model/gate.py
WOW v16 — Fantasy Score Model Pipeline Gate

Entry points:
  run(row, enr=None)                  — called by pipeline.py per row
  score_fantasy_row(row, enrichment)  — programmatic / test entry

Shadow mode: adds output to row["gates"]["fantasy_score_model"] only.
Does NOT set row["terminal_label"] or override any existing gate output.
No existing gate, threshold, label, or governance rule is weakened.

Supported sports / stat keys (FANTASY_SCORE variants)
────────────────────────────────────────────────────
  NBA, WNBA  — stat_key matches _BASKETBALL_KEYS
  NFL        — stat_key matches _NFL_KEYS; position required in enrichment
  MLB (hitter/pitcher) — stat_key matches _MLB_KEYS;
                          position resolved from enrichment["position"]

can_execute = False  (unconditional, module-level and in every output)
"""
from __future__ import annotations

import random
from typing import Any

can_execute: bool = False  # UNCONDITIONAL

# ---------------------------------------------------------------------------
# Stat-key normalization sets
# ---------------------------------------------------------------------------

_BASKETBALL_KEYS: frozenset[str] = frozenset({
    "FANTASY_SCORE", "FANTASY_POINTS", "FPTS", "FANTASY",
    "NBA_FANTASY", "WNBA_FANTASY",
})

_NFL_KEYS: frozenset[str] = frozenset({
    "FANTASY_SCORE", "FANTASY_POINTS", "FPTS", "FPTS_PPR", "NFL_FANTASY",
})

_MLB_KEYS: frozenset[str] = frozenset({
    "FANTASY_SCORE", "FANTASY_POINTS", "FPTS",
    "HITTER_FANTASY_SCORE", "PITCHER_FANTASY_SCORE",
    "HITTER_FS", "PITCHER_FS",
    "MLB_FANTASY", "BASEBALL_FANTASY",
})

SUPPORTED_SPORTS: frozenset[str] = frozenset({"NBA", "WNBA", "NFL", "MLB"})

# Settlement bases that are recognized (all others → settlement identity not locked)
_VALID_SETTLEMENT_BASES: frozenset[str] = frozenset({
    "FULL_GAME_STATS", "OFFICIAL_BOX_SCORE", "REGULAR_TIME_ONLY",
    "INCLUDING_OVERTIME", "FINAL_SCORE", "GAME_STATS",
})

# ---------------------------------------------------------------------------
# Lazy imports of generators (avoids import-time side effects)
# ---------------------------------------------------------------------------

def _gen_basketball():
    from gate_engine.fantasy_score_model.generators.basketball import (
        generate_one, default_params, DEFAULT_STRESS_SCENARIOS, GENERATOR_ID,
    )
    return generate_one, default_params, DEFAULT_STRESS_SCENARIOS, GENERATOR_ID

def _gen_nfl():
    from gate_engine.fantasy_score_model.generators.nfl import (
        generate_one, default_params, DEFAULT_STRESS_SCENARIOS, GENERATOR_ID,
    )
    return generate_one, default_params, DEFAULT_STRESS_SCENARIOS, GENERATOR_ID

def _gen_mlb_hitter():
    from gate_engine.fantasy_score_model.generators.mlb_hitter import (
        generate_one, default_params, DEFAULT_STRESS_SCENARIOS, GENERATOR_ID,
    )
    return generate_one, default_params, DEFAULT_STRESS_SCENARIOS, GENERATOR_ID

def _gen_mlb_pitcher():
    from gate_engine.fantasy_score_model.generators.mlb_pitcher import (
        generate_one, default_params, DEFAULT_STRESS_SCENARIOS, GENERATOR_ID,
    )
    return generate_one, default_params, DEFAULT_STRESS_SCENARIOS, GENERATOR_ID


# ---------------------------------------------------------------------------
# Helpers
# ---------------------------------------------------------------------------

def _norm_stat_key(raw: str) -> str:
    return (raw or "").upper().strip().replace(" ", "_")

def _norm_sport(raw: str) -> str:
    return (raw or "").upper().strip()

def _is_pitcher(enrichment: dict) -> bool:
    pos = str(enrichment.get("position") or "").upper()
    stat = str(enrichment.get("stat_key") or "").upper()
    return (
        "PITCH" in pos or pos in ("SP", "RP", "P")
        or "PITCHER" in stat
    )

def _detect_sport_lane(sport: str, stat_key: str) -> str | None:
    """
    Return the lane identifier or None if this row is not a Fantasy Score row
    for a supported sport.
    """
    s  = _norm_sport(sport)
    sk = _norm_stat_key(stat_key)

    if s in ("NBA",) and sk in _BASKETBALL_KEYS:
        return "NBA"
    if s in ("WNBA",) and sk in _BASKETBALL_KEYS:
        return "WNBA"
    if s == "NFL" and sk in _NFL_KEYS:
        return "NFL"
    if s == "MLB" and sk in _MLB_KEYS:
        return "MLB"
    return None


def _lock_scoring_identity(enrichment: dict, lane: str) -> tuple[bool, str | None, list[str]]:
    """
    Return (identity_locked, scoring_version, identity_blockers).

    identity_locked=True requires:
      - Scoring rules for the lane exist in the formula registry
      - formula.verified_formula=True
      - A source and retrieved_at timestamp are present

    Uses the wow_fantasy_score FormulaRegistry when available;
    falls back to hardcoded version strings for lanes not in the JSON file.
    """
    blockers: list[str] = []
    try:
        from gate_engine.wow_fantasy_score.formula import FormulaRegistry, FormulaError
        import os
        json_path = os.path.join(
            os.path.dirname(__file__), "..", "wow_fantasy_score",
            "fantasy_score_formulas.json"
        )
        if os.path.exists(json_path):
            reg = FormulaRegistry.from_json(json_path)
            formula = reg.get(lane)   # may raise FormulaError
            if not formula.verified_formula:
                blockers.append(f"FORMULA_NOT_VERIFIED:{lane}")
                return False, None, blockers
            return True, formula.version, blockers
    except Exception:
        pass

    # Fallback: hardcoded PROVISIONAL identity for supported lanes
    _HARDCODED: dict[str, str] = {
        "NBA":  "nba_fantasy_gaussian_v1",
        "WNBA": "wnba_fantasy_gaussian_v1",
        "NFL":  "nfl_fantasy_gaussian_v1",
        "MLB":  "mlb_fantasy_gaussian_v1",
    }
    if lane in _HARDCODED:
        # Mark as locked with provisional version; no external source timestamp
        # — a missing retrieved_at is flagged but not a hard block in shadow mode
        return True, _HARDCODED[lane], blockers

    blockers.append(f"SCORING_IDENTITY_UNKNOWN:{lane}")
    return False, None, blockers


def _lock_settlement_identity(enrichment: dict) -> tuple[bool, str | None]:
    """Return (settlement_locked, settlement_basis)."""
    basis = str(enrichment.get("settlement_basis") or "").upper()
    if basis in _VALID_SETTLEMENT_BASES:
        return True, basis
    if basis:
        return False, basis   # declared but unrecognized
    return False, None


def _market_weight_from_enrichment(enrichment: dict) -> float | None:
    v = enrichment.get("market_prior_weight")
    if v is not None:
        try:
            return float(v)
        except (TypeError, ValueError):
            pass
    return None


# ---------------------------------------------------------------------------
# Failure path score extraction
# ---------------------------------------------------------------------------

def _fp_score(enrichment: dict) -> tuple[float | None, str | None]:
    """Return (dominant_failure_prob, largest_path_name) from failure_path_matrix."""
    fp = (enrichment.get("failure_path_matrix") or {})
    if not fp:
        return None, None
    import re
    best_floor: float = 0.0
    best_path: str | None = None
    for pname in ("PRIMARY_KILL_PATH", "SECONDARY_KILL_PATH", "BLACK_SWAN_PATH"):
        path = fp.get(pname) or {}
        band = str(path.get("probability_band") or "")
        nums = [float(x) for x in re.findall(r"[\d.]+", band)]
        if nums and nums[0] > best_floor:
            best_floor = nums[0]
            best_path = pname
    return (round(best_floor / 100.0, 4) if best_floor else None, best_path)


# ---------------------------------------------------------------------------
# Core scoring entry point
# ---------------------------------------------------------------------------

def score_fantasy_row(
    row:        dict[str, Any],
    enrichment: dict[str, Any],
    *,
    n_sims:     int  = 8000,
    seed:       int | None = None,
    run_stress: bool = True,
    run_diag:   bool = True,
) -> dict[str, Any]:
    """
    Score one Fantasy Score row through the full generative model pipeline.

    Returns the fantasy_score_model gate output dict.
    Raises nothing — all errors are captured in blockers.
    can_execute=False is unconditional.
    """
    from gate_engine.fantasy_score_model.shared import (
        run_monte_carlo, score_line, apply_market_prior, run_stress_suite,
        check_final_refresh, build_output, determine_label,
    )
    from gate_engine.fantasy_score_model.calibration_families import (
        detect_family, get_family, compute_bounds,
    )

    sport     = _norm_sport(str(row.get("sport") or ""))
    stat_key  = _norm_stat_key(str(row.get("stat_key") or row.get("prop_type") or ""))
    direction = str(row.get("side") or row.get("direction") or "MORE").upper()
    player    = str(row.get("player_name") or row.get("player") or "Unknown")
    enr       = enrichment or {}

    line_raw = row.get("line")
    try:
        line = float(line_raw)
    except (TypeError, ValueError):
        return _reject(player, sport, stat_key, direction,
                       f"INVALID_LINE:{line_raw}", n_sims)

    if line < 0:
        return _reject(player, sport, stat_key, direction, "NEGATIVE_LINE", n_sims)

    # Detect lane
    lane = _detect_sport_lane(sport, stat_key)
    if lane is None:
        return _reject(player, sport, stat_key, direction,
                       f"UNSUPPORTED_SPORT_OR_STAT:{sport}:{stat_key}", n_sims)

    # Scoring identity lock
    identity_locked, scoring_version, id_blockers = _lock_scoring_identity(enr, lane)
    settlement_locked, settlement_basis = _lock_settlement_identity(enr)

    # Detect position (for NFL calibration family; MLB pitcher detection)
    position = str(enr.get("position") or "").upper()

    # Select generator
    try:
        if lane in ("NBA", "WNBA"):
            gen_fn, dp_fn, stress_sc, gen_id = _gen_basketball()
            params = dp_fn(enr)
        elif lane == "NFL":
            gen_fn, dp_fn, stress_sc, gen_id = _gen_nfl()
            params = dp_fn(enr, position or "WR")
        elif lane == "MLB":
            if _is_pitcher(enr):
                gen_fn, dp_fn, stress_sc, gen_id = _gen_mlb_pitcher()
                params = dp_fn(enr)
            else:
                gen_fn, dp_fn, stress_sc, gen_id = _gen_mlb_hitter()
                params = dp_fn(enr)
        else:
            return _reject(player, sport, stat_key, direction,
                           f"INTERNAL_LANE_ERROR:{lane}", n_sims)
    except Exception as exc:
        return _reject(player, sport, stat_key, direction,
                       f"GENERATOR_INIT_ERROR:{exc!s:.120}", n_sims)

    # Mark scored line for final-refresh comparison
    params["_scored_line"] = line
    enr_with_line = {**enr, "_scored_line": line}

    # Monte Carlo
    try:
        rng  = random.Random(seed)
        sims = run_monte_carlo(gen_fn, params, n=n_sims, seed=seed)
    except Exception as exc:
        return _reject(player, sport, stat_key, direction,
                       f"MONTE_CARLO_ERROR:{exc!s:.120}", n_sims)

    if not sims:
        return _reject(player, sport, stat_key, direction,
                       "EMPTY_SIMULATION", n_sims)

    # Bidirectional line scoring
    scored   = score_line(sims, line)
    p_more   = scored["p_more"]
    p_less   = scored["p_less"]
    p_push   = scored["p_push"]

    # Direction selection
    raw_prob = p_more if direction == "MORE" else p_less

    # Market prior integration
    mkt_prob    = enr.get("market_probability") or enr.get("no_vig_prob")
    mkt_weight  = _market_weight_from_enrichment(enr)
    mkt_blend   = apply_market_prior(raw_prob,
                                      float(mkt_prob) if mkt_prob is not None else None,
                                      mkt_weight)

    # Calibration family + bounds
    try:
        fam_id   = detect_family(lane, position)
    except ValueError:
        fam_id   = "NBA"   # safe fallback for unknown

    sample_size = int(params.get("sample_size") or 0)
    extra_pen   = 0.02 if mkt_blend.get("market_dependent_flag") else 0.0

    cal_lb, cal_ub, thin = compute_bounds(
        mkt_blend["blended_prob"], fam_id, sample_size,
        extra_penalty=extra_pen,
    )

    # Terminal label
    extra_blockers = list(id_blockers)
    if not settlement_locked:
        extra_blockers.append(f"SETTLEMENT_IDENTITY_UNRESOLVED:{settlement_basis or 'MISSING'}")

    terminal_label, label_blockers = determine_label(
        lb=cal_lb,
        identity_locked=identity_locked,
        settlement_locked=settlement_locked,
        model_is_provisional=True,   # all Fantasy Score models are PROVISIONAL
        market_dependent=mkt_blend.get("market_dependent_flag", False),
        extra_blockers=extra_blockers,
    )
    all_blockers = extra_blockers + [b for b in label_blockers if b not in extra_blockers]

    # Failure path score
    fp_score_val, largest_fp = _fp_score(enr)

    # Regime weights (MLB pitcher only)
    regime_wts = params.get("regime_weights")

    # Stress test
    stress_out = None
    if run_stress and identity_locked:
        try:
            stress_out = run_stress_suite(
                generator_fn=gen_fn,
                base_params=params,
                base_p_more=p_more,
                line=line,
                scenarios=stress_sc,
                rng=random.Random((seed or 0) + 1),
            )
        except Exception as exc:
            stress_out = {"error": f"STRESS_TEST_FAILED:{exc!s:.80}"}

    # Dependency diagnostics
    diag_out = None
    if run_diag and identity_locked:
        try:
            diag_rng = random.Random((seed or 0) + 2)
            if lane in ("NBA", "WNBA"):
                from gate_engine.fantasy_score_model.diagnostics import basketball_diagnostics
                diag_out = basketball_diagnostics(gen_fn, params, sims, line, diag_rng)
            elif lane == "NFL":
                from gate_engine.fantasy_score_model.diagnostics import nfl_diagnostics
                diag_out = nfl_diagnostics(gen_fn, params, sims, line, position, diag_rng)
            elif lane == "MLB":
                if _is_pitcher(enr):
                    from gate_engine.fantasy_score_model.diagnostics import mlb_pitcher_diagnostics
                    diag_out = mlb_pitcher_diagnostics(gen_fn, params, sims, line, diag_rng)
                else:
                    from gate_engine.fantasy_score_model.diagnostics import mlb_hitter_diagnostics
                    diag_out = mlb_hitter_diagnostics(gen_fn, params, sims, line, diag_rng)
        except Exception as exc:
            diag_out = {"error": f"DIAGNOSTICS_FAILED:{exc!s:.80}"}

    # Final refresh
    refresh_out = check_final_refresh(enr_with_line)

    # Build canonical output
    output = build_output(
        platform="PrizePicks",
        sport=sport,
        player=player,
        stat_key=stat_key,
        line=line,
        direction=direction,
        scoring_version=scoring_version,
        settlement_basis=settlement_basis,
        identity_locked=identity_locked,
        settlement_locked=settlement_locked,
        formula_flags=_formula_flags(lane),
        sims=sims,
        p_more=p_more,
        p_less=p_less,
        p_push=p_push,
        raw_prob=raw_prob,
        cal_lb=cal_lb,
        cal_ub=cal_ub,
        cal_family=fam_id,
        thin_sample=thin,
        sample_size=sample_size,
        market_blend=mkt_blend,
        terminal_label=terminal_label,
        blockers=all_blockers,
        stress=stress_out,
        diagnostics=diag_out,
        refresh=refresh_out,
        generator_id=gen_id,
        regime_weights=regime_wts,
        failure_path_score=fp_score_val,
        largest_failure_path=largest_fp,
        model_is_provisional=True,
    )

    return output


def _formula_flags(lane: str) -> list[str]:
    flags = ["FANTASY_SCORE_FORMULA_UNVALIDATED", "IMPLEMENTATION_READY_FOR_SHADOW_TEST"]
    if lane == "WNBA":
        flags.append("WNBA_WEIGHTS_ASSUMED_SAME_AS_NBA")
    if lane == "NFL":
        flags.append("NFL_RECEPTION_WEIGHT_UNCONFIRMED")
    return flags


def _reject(player: str, sport: str, stat_key: str, direction: str,
            reason: str, n_sims: int) -> dict[str, Any]:
    return {
        "can_execute":            False,
        "shadow_mode":            True,
        "terminal_label":         "REJECT_DATA_QUALITY",
        "blockers":               [reason],
        "player":                 player,
        "sport":                  sport,
        "stat_key":               stat_key,
        "direction":              direction,
        "n_simulations":          n_sims,
        "identity_locked":        False,
        "settlement_locked":      False,
        "model_is_provisional":   True,
        "calibrated_lower_bound": None,
        "calibrated_upper_bound": None,
        "implementation_status":  "IMPLEMENTATION_READY_FOR_SHADOW_TEST",
    }


# ---------------------------------------------------------------------------
# Pipeline gate entry point  (called by pipeline.py per row)
# ---------------------------------------------------------------------------

def run(row: dict[str, Any], enr: dict | None = None) -> None:
    """
    Pipeline gate interface.  Writes output to row["gates"]["fantasy_score_model"].

    SHADOW MODE: never sets row["terminal_label"] or modifies any existing
    gate output.  Safe to run unconditionally on every row — skips non-FS rows
    silently.
    """
    sport    = _norm_sport(str(row.get("sport") or ""))
    stat_key = _norm_stat_key(str(row.get("stat_key") or row.get("prop_type") or ""))
    lane     = _detect_sport_lane(sport, stat_key)

    # Skip rows that are not Fantasy Score props
    if lane is None:
        return

    enrichment = enr or row.get("_enr") or row.get("enrichment") or {}
    try:
        result = score_fantasy_row(row, enrichment)
    except Exception as exc:
        result = _reject(
            row.get("player_name") or "Unknown",
            sport, stat_key,
            str(row.get("side") or "MORE").upper(),
            f"GATE_EXCEPTION:{exc!s:.150}",
            8000,
        )

    row.setdefault("gates", {})["fantasy_score_model"] = result
    # SHADOW MODE: do not touch row["terminal_label"] or row["blockers"]
