"""
gate_engine/moneyline/pipeline.py
WOW v16 — Moneyline pipeline orchestrator.

Enforces the exact 12-stage conceptual order:
  1.  Slate integrity + event lock
  2.  Participant / status lock
  3.  Independent sport model (zero market input)
  4.  Monte Carlo game-state simulation
  5.  Failure-path distributional integration
  6.  Independent probability (post-simulation)
  7.  Model-disagreement audit
  8.  Dynamic calibration (market enters HERE for the first time)
  9.  Calibrated lower bound
  10. Favorite / upset classification and ranking
  11. Exact no-vig market comparison
  12. Mandatory final refresh

Returns MoneylineResult with four clean outputs + full layer observability.
can_execute=False unconditional.

IMPORTANT: This module is the ONLY place market data (no_vig_probability,
sportsbook_odds) is read.  All upstream stages receive a clean enrichment
with odds fields stripped.
"""
from __future__ import annotations

import hashlib
import json
from datetime import datetime, timezone
from typing import Any

can_execute: bool = False  # UNCONDITIONAL

from gate_engine.moneyline.types import (
    MoneylineResult,
    MoneylineOutputs,
    strip_odds_fields,
    IndependentModelContaminationError,
)
from gate_engine.moneyline.slate_integrity import (
    check_participant_status,
    check_stale_model,
    check_final_refresh,
)
from gate_engine.moneyline.sport_model import compute_independent_probability
from gate_engine.moneyline.game_state_sim import run_game_state_simulation
from gate_engine.moneyline.failure_path import integrate_failure_paths
from gate_engine.moneyline.model_disagreement import audit_model_disagreement
from gate_engine.moneyline.dynamic_calibration import calibrate
from gate_engine.moneyline.classification import classify_candidate

# Import existing helpers (preserved without behavior change)
from gate_engine.moneyline_probability import (
    get_model_for_sport,
    ModelStatus,
    extract_no_vig_probability,
    audit_probability,
    validate_soccer_1x2_outcome,
    compute_1x2_three_state,
    build_prediction_snapshot,
)
from gate_engine.market_family import build_route_fields


# ---------------------------------------------------------------------------
# Market comparison helper
# ---------------------------------------------------------------------------

def _build_market_comparison(
    row: dict[str, Any],
    enrichment: dict[str, Any],
    market_no_vig: float | None,
    calibrated_prob: float | None,
) -> dict[str, Any]:
    """
    Exact no-vig market comparison (stage 11).
    Market data is only read here — never upstream.
    """
    books = enrichment.get("sportsbook_odds") or []
    team_side = row.get("team") or row.get("player") or "home"
    sport = (row.get("sport") or "").upper()
    market_type = (row.get("market_type") or "full_game_h2h").lower()

    # Match book entries to the candidate's team by the "team" field in each
    # sportsbook entry (e.g. {"team": "Boston Red Sox", "odds": -130}).
    # Fall back to the "odds" field only when no "team" key is present at all.
    team_side_lower = team_side.lower().strip()

    book_lines: list[dict[str, Any]] = []
    for book in (books if isinstance(books, list) else []):
        if not isinstance(book, dict):
            continue
        book_name  = book.get("name") or book.get("bookmaker") or "unknown"
        book_team  = (book.get("team") or book.get("side") or "").lower().strip()
        odds_val   = book.get("odds")

        # If entry has a team tag and it doesn't match this candidate, skip it
        if book_team and book_team != team_side_lower:
            continue
        if odds_val is None:
            continue
        try:
            american = float(odds_val)
            if american > 0:
                implied = 100.0 / (american + 100.0)
            else:
                implied = abs(american) / (abs(american) + 100.0)
            book_lines.append({
                "bookmaker":       book_name,
                "american_odds":   int(american),
                "implied_prob":    round(implied, 4),
                "retrieved_at":    book.get("retrieved_at"),
            })
        except (TypeError, ValueError):
            continue

    n_books = len(book_lines)
    hold_pct = None
    if n_books >= 2:
        total_implied = sum(b["implied_prob"] for b in book_lines)
        hold_pct = round(total_implied - 1.0, 4)

    return {
        "market_no_vig":     round(market_no_vig, 4) if market_no_vig else None,
        "calibrated_prob":   round(calibrated_prob, 4) if calibrated_prob else None,
        "net_edge":          round(calibrated_prob - market_no_vig, 4)
                             if (calibrated_prob and market_no_vig) else None,
        "bookmaker_count":   n_books,
        "hold_pct":          hold_pct,
        "market_type":       market_type,
        "book_lines":        book_lines,
        "comparison_status": "EXACT_H2H" if n_books >= 2 else
                             "SINGLE_BOOK" if n_books == 1 else "NO_MARKET_DATA",
        "note":              "Market data entered pipeline at stage 8 (calibration) "
                             "and stage 11 (comparison) only — never upstream.",
    }


# ---------------------------------------------------------------------------
# Terminal label logic (WOW v16 governance preserved)
# ---------------------------------------------------------------------------

def _assign_terminal_label(
    model_status:            str,
    blockers:                list[str],
    classification:          dict[str, Any] | None,
    calibration:             dict[str, Any] | None,
    market_dependent:        bool,
    market_derived_fallback: bool = False,
) -> str:
    """
    Assign terminal label from governance rules. Preserves all existing
    WOW v16 label contracts. Does NOT weaken any ceiling.

    market_derived_fallback=True: the independent model had no enrichment data;
    market no-vig was used as a bounded observation.  Cap is MODEL_QUALIFIED_HOLD
    — the observation is publishable but can never qualify for money placement.
    """
    if blockers:
        # Check for specific terminal-label blockers
        for b in blockers:
            if "STALE_MODEL_INVALIDATED" in b:
                return "STALE_MODEL_INVALIDATED"
            if "PARTICIPANT_LOCK_FAILED" in b:
                return "DATA_CONTRACT_FAIL"
            if "NO_REGISTERED_MODEL" in b:
                return "DATA_CONTRACT_FAIL"
        return "DATA_CONTRACT_FAIL"

    # Market-derived fallback: publishable observability, never qualifying
    if market_derived_fallback:
        return "MODEL_QUALIFIED_HOLD"

    # Classification gate: tail-only upsets cannot qualify
    if classification and classification.get("qualification_gate") == "TAIL_ONLY_REJECTED":
        return "REJECT_TAIL_ONLY_UPSET"

    # Market dependent: cap at MODEL_QUALIFIED_HOLD
    if market_dependent:
        return "MODEL_QUALIFIED_HOLD"

    # Model status caps
    if model_status == ModelStatus.ACTIVE:
        # CLB ≥ 0.55 with no blockers → MONEY_QUALIFIED
        clb = (calibration or {}).get("calibrated_lower_bound") or 0.0
        if clb >= 0.55:
            return "MONEY_QUALIFIED"
        return "MODEL_QUALIFIED_HOLD"
    elif model_status == ModelStatus.PROVISIONAL:
        return "MODEL_QUALIFIED_HOLD"
    else:
        return "DATA_CONTRACT_FAIL"


# ---------------------------------------------------------------------------
# Main pipeline
# ---------------------------------------------------------------------------

def run_moneyline_pipeline(
    row:            dict[str, Any],
    enrichment:     dict[str, Any] | None = None,
    prior_snapshot: dict[str, Any] | None = None,
    *,
    n_sims:         int  = 5000,
    seed:           int | None = None,
) -> MoneylineResult:
    """
    Full 12-stage moneyline pipeline.

    Returns MoneylineResult with four clean outputs and full observability.
    can_execute=False unconditional.
    """
    enrichment = enrichment or {}
    result = MoneylineResult()
    result.can_execute    = False
    result.can_approve_bets = False
    blockers: list[str]   = []

    sport       = (row.get("sport") or "").upper().strip()
    model_entry = get_model_for_sport(sport)
    model_status = model_entry.get("status", ModelStatus.UNAVAILABLE)
    result.model_id     = model_entry.get("model_id")
    result.model_status = model_status

    # -----------------------------------------------------------------------
    # Stage 1: Slate integrity + event lock
    # -----------------------------------------------------------------------
    stale = check_stale_model(row, enrichment, prior_snapshot)
    result.slate_integrity = stale
    if stale.get("stale"):
        blockers.append(f"STALE_MODEL_INVALIDATED:{stale.get('reason')}")
        result.blockers      = blockers
        result.terminal_label = "STALE_MODEL_INVALIDATED"
        return result

    # Soccer 1X2 three-state check (part of slate integrity)
    is_soccer = model_entry.get("output_type") == "three_state"
    if is_soccer:
        violations = validate_soccer_1x2_outcome(row)
        if violations:
            blockers.extend(violations)
            result.blockers       = blockers
            result.terminal_label = "DATA_CONTRACT_FAIL"
            return result

    # -----------------------------------------------------------------------
    # Stage 2: Participant / status lock
    # -----------------------------------------------------------------------
    participant_check = check_participant_status(row, enrichment)
    result.slate_integrity = {**result.slate_integrity, **participant_check}
    if not participant_check["locked"]:
        blockers.extend(participant_check["blockers"])
        result.blockers       = blockers
        result.terminal_label = "DATA_CONTRACT_FAIL"
        return result

    # -----------------------------------------------------------------------
    # Model unavailability check
    # -----------------------------------------------------------------------
    if model_status == ModelStatus.UNAVAILABLE:
        blockers.append(
            f"NO_REGISTERED_MODEL:sport={sport} "
            "sportsbook_odds_cannot_substitute_for_sport_model"
        )
        result.blockers       = blockers
        result.terminal_label = "DATA_CONTRACT_FAIL"
        return result

    # -----------------------------------------------------------------------
    # Strip all odds fields from enrichment before handing to independent model.
    # Also extract market_no_vig early — needed for the fallback path below.
    # -----------------------------------------------------------------------
    clean_enr = strip_odds_fields(enrichment)

    # Determine candidate side once — used for inversion at stage 6
    from gate_engine.moneyline.sport_model import _is_home_side
    is_home = _is_home_side(row, clean_enr)

    # Extract market_no_vig here so it is available for the fallback path.
    # Pass both candidate team and opponent for proper two-sided no-vig removal.
    _team_side_early  = row.get("team") or row.get("player") or "home"
    _opponent_early   = row.get("opponent") or row.get("opponent_team") or None
    _market_no_vig_early = extract_no_vig_probability(
        enrichment, side=_team_side_early, opponent=_opponent_early
    )

    _market_derived_fallback = False   # tracks whether the fallback path was taken

    # -----------------------------------------------------------------------
    # Stage 3: Independent sport model (zero market input)
    # Outputs P(home team wins) by convention — see sport_model.py header.
    # -----------------------------------------------------------------------
    try:
        sport_model_out = compute_independent_probability(row, clean_enr)
    except IndependentModelContaminationError as exc:
        blockers.append(f"INDEPENDENT_MODEL_CONTAMINATION:{exc!s:.120}")
        result.blockers       = blockers
        result.terminal_label = "DATA_CONTRACT_FAIL"
        result.snapshot_hash  = result.build_snapshot_hash()
        return result
    except Exception as exc:
        sport_model_out = {
            "independent_probability": None,
            "submodel_probs": {},
            "notes": [f"SPORT_MODEL_ERROR:{exc!s:.80}"],
        }

    result.sport_model   = sport_model_out
    independent_prob_raw = sport_model_out.get("independent_probability")   # P(home wins)

    if independent_prob_raw is None:
        # -----------------------------------------------------------------------
        # Independent model has no data.
        #
        # Two sub-cases:
        #
        # A) Market odds available → MARKET_OBSERVATION_ONLY early return.
        #    Market no-vig is annotated as a bounded observation but is NOT
        #    assigned to independent_probability (that field stays None) because
        #    market data cannot populate the independent-model output.
        #    Simulation, failure-path, and disagreement-audit stages are skipped.
        #    Ceiling: MODEL_QUALIFIED_HOLD.
        #
        # B) No market odds → DATA_CONTRACT_FAIL.
        # -----------------------------------------------------------------------
        if _market_no_vig_early is not None:
            _market_derived_fallback = True
            result.sport_model.setdefault("notes", []).append(
                f"MARKET_OBSERVATION_ONLY:no_independent_model_data; "
                f"market_no_vig={_market_no_vig_early:.4f}; "
                "independent_probability=None; simulation_skipped; "
                "ceiling=MODEL_QUALIFIED_HOLD (cannot qualify)"
            )
            result.sport_model["market_derived_fallback"]  = True
            result.sport_model["market_observation"]       = round(_market_no_vig_early, 4)

            # independent_probability MUST stay None — market data cannot populate it
            result.outputs.independent_probability             = None
            result.outputs.calibrated_probability              = round(_market_no_vig_early, 4)
            result.outputs.calibrated_probability_lower_bound  = round(
                max(0.01, _market_no_vig_early - 0.08), 4)
            result.outputs.calibrated_probability_upper_bound  = round(
                min(0.99, _market_no_vig_early + 0.08), 4)
            result.outputs.net_edge = None

            result.terminal_label = "MODEL_QUALIFIED_HOLD"
            result.blockers       = blockers
            result.snapshot_hash  = result.build_snapshot_hash()
            return result
        else:
            blockers.append("INDEPENDENT_PROBABILITY_UNAVAILABLE:insufficient_non_market_data")
            result.blockers       = blockers
            result.terminal_label = "DATA_CONTRACT_FAIL"
            result.snapshot_hash  = result.build_snapshot_hash()
            return result

    # -----------------------------------------------------------------------
    # Stage 4: Monte Carlo game-state simulation — also home-team perspective
    # -----------------------------------------------------------------------
    sim_result = run_game_state_simulation(row, clean_enr, independent_prob_raw,
                                           n_sims=n_sims, seed=seed)
    result.simulation = sim_result.to_dict()
    independent_prob_post_sim = sim_result.adjusted_prob   # P(home wins)

    # -----------------------------------------------------------------------
    # Stage 5: Failure-path distributional integration — home-team perspective
    # -----------------------------------------------------------------------
    fp_matrix = enrichment.get("failure_path_matrix")
    fp_result = integrate_failure_paths(
        base_win_prob=independent_prob_post_sim,
        failure_path_matrix=fp_matrix,
        simulation_regimes=sim_result.regime_distribution,
    )
    result.failure_path = fp_result.to_dict()

    # -----------------------------------------------------------------------
    # Stage 6: Candidate-side probability extraction.
    #
    # Two sub-cases:
    #
    # SOCCER (three-outcome market):
    #   The sport model computes a single coherent P(home)/P(draw)/P(away)
    #   distribution via _soccer_draw_adjusted().  Binary inversion does NOT
    #   apply — extract the candidate's component by row["outcome"].
    #   P(home) + P(draw) + P(away) = 1.0 from a single distribution, so
    #   all three soccer candidates for the same event are mutually consistent.
    #
    # NON-SOCCER (binary market):
    #   Stages 3–5 return P(home wins).  Invert for away candidates.
    # -----------------------------------------------------------------------
    independent_prob_home = fp_result.adjusted_win_prob

    if is_soccer:
        soccer_3s = sport_model_out.get("soccer_three_state") or {}
        outcome = (row.get("outcome") or "home").lower()
        if outcome == "draw":
            independent_prob_final = float(soccer_3s.get("p_draw") or 0.27)
        elif outcome == "away":
            independent_prob_final = float(soccer_3s.get("p_away") or
                                           max(0.01, 1.0 -
                                               float(soccer_3s.get("p_home") or 0.45) -
                                               float(soccer_3s.get("p_draw") or 0.27)))
        else:  # home (default)
            independent_prob_final = float(soccer_3s.get("p_home") or independent_prob_home)
        result.sport_model.setdefault("notes", []).append(
            f"soccer_three_state_extraction: outcome={outcome} "
            f"p_home={soccer_3s.get('p_home')} p_draw={soccer_3s.get('p_draw')} "
            f"p_away={soccer_3s.get('p_away')} candidate_p={independent_prob_final:.4f}"
        )
    else:
        # Binary market: stages 3–5 in home-team perspective → single inversion here
        if is_home:
            independent_prob_final = independent_prob_home
            result.sport_model.setdefault("notes", []).append(
                "side_perspective=HOME no_inversion_needed"
            )
        else:
            independent_prob_final = 1.0 - independent_prob_home
            result.sport_model.setdefault("notes", []).append(
                f"side_perspective=AWAY inversion_applied: "
                f"P(away_wins)=1-P(home_wins)={independent_prob_home:.4f}"
                f" → {independent_prob_final:.4f}"
            )

    independent_prob_final = max(0.01, min(0.99, independent_prob_final))
    result.outputs.independent_probability = round(independent_prob_final, 4)

    # -----------------------------------------------------------------------
    # Stage 7: Model-disagreement audit
    # -----------------------------------------------------------------------
    submodel_probs = dict(sport_model_out.get("submodel_probs") or {})
    submodel_probs["simulation_output"] = round(independent_prob_post_sim, 4)
    dis_audit = audit_model_disagreement(submodel_probs)
    result.disagreement_audit = dis_audit.to_dict()

    # -----------------------------------------------------------------------
    # Stage 8: Dynamic calibration (market_no_vig enters HERE for first time)
    # -----------------------------------------------------------------------
    team_side    = row.get("team") or row.get("player") or "home"
    opponent     = row.get("opponent") or row.get("opponent_team") or None
    market_no_vig = extract_no_vig_probability(enrichment, side=team_side, opponent=opponent)

    n_books = len(enrichment.get("sportsbook_odds") or [])
    hours_open = float(enrichment.get("market_hours_open") or 0.0)
    hold_pct   = float(enrichment.get("hold_pct") or 0.05)
    mkt_fresh  = float(enrichment.get("market_freshness_hours") or 0.0)
    mkt_type   = (enrichment.get("market_type") or "full_game_h2h").lower()

    market_inputs = {
        "bookmaker_count":       n_books,
        "hours_since_open":      hours_open,
        "hold_pct":              hold_pct,
        "market_freshness_hours": mkt_fresh,
        "market_type":           mkt_type,
    }

    cal_result = calibrate(
        independent_prob=independent_prob_final,
        model_status=model_status,
        sport=sport,
        enrichment=enrichment,        # FULL enrichment for sample size / lineup / freshness
        quorum_result=None,           # future: from opportunity_acquisition
        disagreement_audit=dis_audit,
        market_no_vig=market_no_vig,
        market_inputs=market_inputs,
    )
    result.calibration = cal_result.to_dict()

    # -----------------------------------------------------------------------
    # Stage 9: Calibrated lower bound
    # -----------------------------------------------------------------------
    result.outputs.calibrated_probability             = cal_result.calibrated_probability
    result.outputs.calibrated_probability_lower_bound = cal_result.calibrated_lower_bound
    result.outputs.calibrated_probability_upper_bound = cal_result.calibrated_upper_bound
    result.outputs.net_edge                           = cal_result.net_edge

    # -----------------------------------------------------------------------
    # Stage 10: Favorite / upset classification and ranking
    # -----------------------------------------------------------------------
    classification = classify_candidate(
        row=row,
        calibration_result=cal_result.to_dict(),
        enrichment=enrichment,
        failure_path_result=fp_result.to_dict(),
    )
    result.classification = classification.to_dict()

    # -----------------------------------------------------------------------
    # Stage 11: Exact no-vig market comparison
    # -----------------------------------------------------------------------
    result.market_comparison = _build_market_comparison(
        row, enrichment, market_no_vig, cal_result.calibrated_probability,
    )

    # -----------------------------------------------------------------------
    # Stage 11b: Price/edge audit (probability audit using existing helper)
    # -----------------------------------------------------------------------
    audit = audit_probability(
        raw_probability=result.outputs.independent_probability,
        calibrated_probability=cal_result.calibrated_probability,
        lower_bound=cal_result.calibrated_lower_bound,
        upper_bound=cal_result.calibrated_upper_bound,
        model_status=model_status,
    )
    if not audit["passed"]:
        for note in audit.get("audit_notes", []):
            if note.startswith("AUDIT_FAIL"):
                blockers.append(note)

    # -----------------------------------------------------------------------
    # Stage 12: Mandatory final refresh
    # -----------------------------------------------------------------------
    final_refresh = check_final_refresh(row, enrichment)
    result.final_refresh = final_refresh
    if final_refresh.get("refresh_required"):
        for flag in final_refresh.get("refresh_flags", []):
            blockers.append(f"FINAL_REFRESH_REQUIRED:{flag}")

    # -----------------------------------------------------------------------
    # Soccer 1X2 three-state output (stage 12 — coherent calibrated distribution)
    #
    # We use the sport model's single coherent P(home)/P(draw)/P(away)
    # distribution as the structural prior and adjust it proportionally so
    # that the candidate's component matches the calibrated probability.
    # P(h_cal) + P(d_cal) + P(a_cal) = 1.0 is preserved by construction.
    # -----------------------------------------------------------------------
    if is_soccer:
        outcome  = (row.get("outcome") or "home").lower()
        cal_p    = cal_result.calibrated_probability
        soccer_3s = sport_model_out.get("soccer_three_state") or {}
        p_h = float(soccer_3s.get("p_home") or 0.45)
        p_d = float(soccer_3s.get("p_draw") or 0.27)
        p_a = float(soccer_3s.get("p_away") or 0.28)

        try:
            if cal_p is not None:
                # Fix the candidate's calibrated probability; scale the remaining
                # two components proportionally so they still sum to 1.0.
                if outcome == "home":
                    p_h_cal = cal_p
                    rest = max(0.001, p_d + p_a)
                    p_d_cal = round((1.0 - p_h_cal) * (p_d / rest), 4)
                    p_a_cal = round(1.0 - p_h_cal - p_d_cal, 4)
                elif outcome == "draw":
                    p_d_cal = cal_p
                    rest = max(0.001, p_h + p_a)
                    p_h_cal = round((1.0 - p_d_cal) * (p_h / rest), 4)
                    p_a_cal = round(1.0 - p_d_cal - p_h_cal, 4)
                else:  # away
                    p_a_cal = cal_p
                    rest = max(0.001, p_h + p_d)
                    p_h_cal = round((1.0 - p_a_cal) * (p_h / rest), 4)
                    p_d_cal = round(1.0 - p_a_cal - p_h_cal, 4)
                result.three_state_1x2 = compute_1x2_three_state(
                    max(0.0, p_h_cal), max(0.0, p_d_cal), max(0.0, p_a_cal)
                )
            else:
                # No calibrated probability — use raw sport model distribution
                result.three_state_1x2 = compute_1x2_three_state(p_h, p_d, p_a)
        except ValueError as exc:
            blockers.append(f"SOCCER_1X2_PROBABILITY_ERROR:{exc}")

    # -----------------------------------------------------------------------
    # Terminal label
    # -----------------------------------------------------------------------
    result.terminal_label = _assign_terminal_label(
        model_status=model_status,
        blockers=blockers,
        classification=result.classification,
        calibration=cal_result.to_dict(),
        market_dependent=cal_result.market_dependent_flag,
        market_derived_fallback=_market_derived_fallback,
    )
    result.blockers = blockers

    # Snapshot hash
    result.snapshot_hash = result.build_snapshot_hash()

    return result
