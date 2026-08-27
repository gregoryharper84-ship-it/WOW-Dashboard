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
from gate_engine.moneyline.orientation import (
    orientation_blocker,
    resolve_participant_orientation,
)
from gate_engine.moneyline.game_state_sim import run_game_state_simulation
from gate_engine.moneyline.failure_path import integrate_failure_paths
from gate_engine.moneyline.model_disagreement import audit_model_disagreement
from gate_engine.moneyline.dynamic_calibration import calibrate
from gate_engine.moneyline.classification import classify_candidate
from gate_engine.moneyline.teamrankings_adapter import (
    extract_teamrankings_enrichment,
    inject_tr_features_into_clean_enrichment,
    TeamRankingsMatchupEnrichment,
)
from gate_engine.moneyline.external_analyst.orchestrator import (
    run_external_analyst_intelligence,
)
from gate_engine.moneyline.mlb_starter_change import analyze_mlb_starter_change

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


_BINARY_PROBABILITY_SUM_TOLERANCE = 0.01


def _validate_binary_probability_pair(
    home_probability: Any,
    away_probability: Any,
) -> tuple[dict[str, float] | None, str | None]:
    """Validate a complete binary pair without silently renormalizing it."""
    try:
        home = float(home_probability)
        away = float(away_probability)
    except (TypeError, ValueError):
        return None, (
            "PROBABILITY_ORIENTATION_CONTRACT_BREACH:"
            "home_and_away_probabilities_required"
        )
    if not 0.0 <= home <= 1.0 or not 0.0 <= away <= 1.0:
        return None, (
            "PROBABILITY_ORIENTATION_CONTRACT_BREACH:"
            f"out_of_range:home={home!r}:away={away!r}"
        )
    total = home + away
    if abs(total - 1.0) > _BINARY_PROBABILITY_SUM_TOLERANCE:
        return None, (
            "PROBABILITY_ORIENTATION_CONTRACT_BREACH:"
            f"sum_out_of_tolerance:sum={total:.6f}:"
            f"tolerance={_BINARY_PROBABILITY_SUM_TOLERANCE:.2f}"
        )
    return {"HOME": home, "AWAY": away}, None


def _select_binary_candidate_probability(
    probabilities: dict[str, float],
    *,
    orientation: Any,
) -> tuple[float | None, str | None]:
    """Select one pre-oriented probability; never invert downstream."""
    if not getattr(orientation, "resolved", False):
        return None, "PROBABILITY_ORIENTATION_CONTRACT_BREACH:orientation_unresolved"
    selected = "HOME" if orientation.is_home else "AWAY"
    if selected not in probabilities:
        return None, (
            "PROBABILITY_ORIENTATION_CONTRACT_BREACH:"
            f"orientation_count_not_exactly_one:selected={selected}"
        )
    return probabilities[selected], None


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

# Review-flag prefixes that are surfaced in MoneylineResult.blockers for
# observability but MUST NOT trigger a terminal DATA_CONTRACT_FAIL.
_NON_TERMINAL_REVIEW_PREFIXES: tuple[str, ...] = (
    "TEAMRANKINGS_CONTRADICTION_REVIEW",
    "EXTERNAL_ANALYST_CONTRADICTION_REVIEW",
    "ANALYST_CONSENSUS_UNRESOLVED",
)


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
    # Partition: review flags are kept in MoneylineResult.blockers for observability
    # but must NOT trigger a terminal DATA_CONTRACT_FAIL.
    terminal_blockers = [
        b for b in blockers
        if not any(b.startswith(pfx) for pfx in _NON_TERMINAL_REVIEW_PREFIXES)
    ]
    if terminal_blockers:
        # Check for specific terminal-label blockers
        for b in terminal_blockers:
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

    sport        = (row.get("sport") or "").upper().strip()
    model_entry  = get_model_for_sport(sport)
    model_status = model_entry.get("status", ModelStatus.UNAVAILABLE)
    result.model_id     = model_entry.get("model_id")
    result.model_status = model_status

    # Orientation is a Layer-0 data contract.  Resolve it before market
    # adaptation, model evaluation, inversion, calibration, or classification.
    orientation = resolve_participant_orientation(row, enrichment)
    result.slate_integrity = {
        "participant_orientation": orientation.to_dict(),
    }
    if not orientation.resolved:
        blockers.append(orientation_blocker(orientation))
        result.blockers       = blockers
        result.terminal_label = "DATA_CONTRACT_FAIL"
        result.snapshot_hash  = result.build_snapshot_hash()
        return result

    # -----------------------------------------------------------------------
    # Stage 0: Market snapshot handoff (WOW-PATCH-2026-08-17-MONEYLINE-MARKET-SNAPSHOT)
    #
    # When the odds endpoint supplies a normalized MoneylineMarketSnapshot in
    # enrichment["market_snapshot"], it is the SINGLE source of sportsbook
    # odds — the adapter replaces any caller-supplied sportsbook_odds so no
    # duplicate mapping path exists.  Hard invariant: books fetched but zero
    # sent to the scorer → MARKET_PIPELINE_CONTRACT_BREACH, scoring blocked.
    # -----------------------------------------------------------------------
    from gate_engine.moneyline.market_snapshot import (
        attach_snapshot_to_enrichment,
        MARKET_PIPELINE_CONTRACT_BREACH,
    )
    # Key PRESENCE (not truthiness) marks a supplied snapshot: an explicitly
    # supplied empty/malformed snapshot must fail closed, never be ignored.
    if "market_snapshot" in enrichment:
        _raw_snapshot = enrichment.get("market_snapshot")
        enrichment, _snap, _breached = attach_snapshot_to_enrichment(
            enrichment, _raw_snapshot, row=row,
        )
        result.market_comparison = {
            "market_snapshot_counters": dict(_snap.counters),
            "market_snapshot_event_id": _snap.event_id,
            "market_snapshot_status":   _snap.status,
        }
        if _breached:
            blockers.append(
                f"{MARKET_PIPELINE_CONTRACT_BREACH}:"
                f"books_fetched={_snap.counters.get('books_fetched', 0)}:"
                "books_sent_to_scorer=0:scoring_blocked"
            )
            result.blockers       = blockers
            result.terminal_label = MARKET_PIPELINE_CONTRACT_BREACH
            result.snapshot_hash  = result.build_snapshot_hash()
            return result

    # -----------------------------------------------------------------------
    # Stage 1: Slate integrity + event lock
    # -----------------------------------------------------------------------
    stale = check_stale_model(row, enrichment, prior_snapshot)
    result.slate_integrity = {
        **result.slate_integrity,
        **stale,
    }
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
    # Stage 2.5: TeamRankings secondary enrichment extraction.
    # Runs AFTER participant lock (stage 2) so lineup blockers still fire first.
    # TR data is extracted here; contradiction analysis is completed after
    # stage 6 once independent_prob_final is available.
    # TR display_odds are stored in tr_enr but NEVER passed to the independent
    # model or to extract_no_vig_probability().
    # -----------------------------------------------------------------------
    tr_enr: TeamRankingsMatchupEnrichment = extract_teamrankings_enrichment(
        enrichment, sport, core_independent_prob_home=None,
    )

    # -----------------------------------------------------------------------
    # Strip all odds fields from enrichment before handing to independent model.
    # Also extract market_no_vig early — needed for the fallback path below.
    # -----------------------------------------------------------------------
    clean_enr = strip_odds_fields(enrichment)

    # Inject non-market TR features into clean_enr for the sport model.
    # Only injected when TR is RETRIEVED, non-stale, and has a direct matchup prob.
    # TR display_odds are NEVER injected (they stay in tr_enr only).
    clean_enr = inject_tr_features_into_clean_enrichment(clean_enr, tr_enr)

    # Store initial TR record (contradiction fields = ABSENT until stage 6.5).
    # This ensures all early-return paths below also carry the TR acquisition audit.
    result.teamrankings = tr_enr.to_dict()

    # Candidate side was resolved once at Layer 0 and is used for inversion at
    # stage 6.  An unresolved value cannot reach this point.
    is_home = bool(orientation.is_home)

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
        sport_model_out = compute_independent_probability(
            row, clean_enr, orientation=orientation
        )
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
    _mlb_v2_native = (
        sport == "MLB"
        and sport_model_out.get("native_calibrated") is True
        and sport_model_out.get("point_estimate_locked") is True
    )

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
            # ---------------------------------------------------------------
            # Typed hydration failure object.
            # Per verifier spec: emit hydration_profile, missing_fields[],
            # specialist_status, eligible_for_model, retryable — not the
            # vague "insufficient_non_market_data" string.
            # ---------------------------------------------------------------
            _profile_map = {
                "WNBA":   "WNBA_ML_V1",
                "ATP":    "TENNIS_MATCH_WINNER_V1",
                "WTA":    "TENNIS_MATCH_WINNER_V1",
                "TENNIS": "TENNIS_MATCH_WINNER_V1",
                "NBA":    "MONEYLINE_V1",
                "MLB":    "MONEYLINE_V1",
            }
            _hydration_profile = (
                (enrichment or {}).get("hydration_profile")
                or _profile_map.get(sport, f"MONEYLINE_V1:{sport}")
            )
            _notes = sport_model_out.get("notes") or []
            _missing_fields = sorted({
                n.split(":")[0]
                for n in _notes
                if ":NO_DATA" in n
                and not n.startswith("NO_SUBMODEL_DATA")
            })
            _typed_failure: dict[str, Any] = {
                "hydration_profile":  _hydration_profile,
                "missing_fields":     _missing_fields,
                "specialist_status":  "NOT_READY",
                "eligible_for_model": False,
                "retryable":          True,
            }
            result.sport_model["hydration_failure"] = _typed_failure
            blockers.append(
                f"INDEPENDENT_PROBABILITY_UNAVAILABLE:"
                f"hydration_profile={_hydration_profile};"
                f"missing_fields={_missing_fields};"
                f"specialist_status=NOT_READY;"
                f"eligible_for_model=false;"
                f"retryable=true"
            )
            result.blockers       = blockers
            result.terminal_label = "DATA_CONTRACT_FAIL"
            result.snapshot_hash  = result.build_snapshot_hash()
            return result

    # The sport model must produce a complete, reconciled binary pair before
    # simulation output can be selected for a candidate. This only validates
    # representation; it does not change any model formula or coefficient.
    if not is_soccer:
        _model_pair, _model_pair_error = _validate_binary_probability_pair(
            sport_model_out.get("home_probability"),
            sport_model_out.get("away_probability"),
        )
        if _model_pair_error:
            blockers.append(_model_pair_error)
            result.blockers = blockers
            result.terminal_label = "DATA_CONTRACT_FAIL"
            result.snapshot_hash = result.build_snapshot_hash()
            return result
        result.sport_model["canonical_home_away_probability"] = _model_pair

    # -----------------------------------------------------------------------
    # Stage 4: Monte Carlo game-state simulation — also home-team perspective
    # -----------------------------------------------------------------------
    sim_result = run_game_state_simulation(row, clean_enr, independent_prob_raw,
                                           n_sims=n_sims, seed=seed)
    result.simulation = sim_result.to_dict()
    if _mlb_v2_native:
        independent_prob_post_sim = float(independent_prob_raw)
        result.simulation["point_estimate_applied"] = False
        result.simulation["point_estimate_lock_reason"] = "MLB_V2_NATIVE_PLATT_ALREADY_VALIDATED"
    else:
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
    if _mlb_v2_native:
        # Failure-path simulation remains an uncertainty/diagnostic layer. The
        # live V2 vector already encodes the current pregame state, so it may not
        # overwrite the validated Platt point estimate.
        fp_result.adjusted_win_prob = float(independent_prob_post_sim)
        result.failure_path = fp_result.to_dict()
        result.failure_path["point_estimate_applied"] = False
        result.failure_path["point_estimate_lock_reason"] = "MLB_V2_NATIVE_PLATT_ALREADY_VALIDATED"

    # -----------------------------------------------------------------------
    # Stage 5.5: MLB Starter-Change analysis (WOW-PATCH-2026-08-08-MLB-SP-SCRATCH)
    #
    # Only fires for MLB.  For all other sports the module returns immediately
    # with NO_CHANGE_DETECTED and zero adjustments.
    #
    # Two separate effects — no double-counting:
    #   probability_adjustment — quality-delta applied to independent_prob_post_sim
    #                            (replacement ERA vs original ERA; ±8pp cap)
    #   uncertainty_expansion  — injected into enrichment for calibration (stage 8)
    #                            to widen the distribution and lower the CLB
    #
    # UNRESOLVED_REPLACEMENT → fail-closed to MODEL_QUALIFIED_HOLD immediately.
    # Opener/bulk and bullpen-game plans are treated as legitimate architectures.
    # Market data does NOT enter here; it only enters at stage 8.
    # -----------------------------------------------------------------------
    _sc_result = analyze_mlb_starter_change(row, clean_enr)
    result.starter_change = _sc_result.to_dict()

    if _sc_result.should_hold:
        # Unresolved replacement plan — fail closed before candidate extraction
        blockers.append(
            "MLB_SP_SCRATCH:UNRESOLVED_REPLACEMENT_PLAN:"
            "replacement_era_unavailable:fail_closed_to_HOLD"
        )
        result.blockers       = blockers
        result.terminal_label = "MODEL_QUALIFIED_HOLD"
        result.snapshot_hash  = result.build_snapshot_hash()
        return result

    # Apply quality-delta adjustment in HOME-TEAM perspective.
    # Stages 3–5 all operate in home-team perspective; stage 6 reads
    # fp_result.adjusted_win_prob as independent_prob_home.  We adjust that
    # value in-place so stage 6 naturally inherits the shifted probability.
    if _sc_result.probability_adjustment != 0.0 and not _mlb_v2_native:
        _before_adj = fp_result.adjusted_win_prob
        fp_result.adjusted_win_prob = max(
            0.01, min(0.99, fp_result.adjusted_win_prob + _sc_result.probability_adjustment)
        )
        # Keep independent_prob_post_sim consistent for logging/observability
        independent_prob_post_sim = fp_result.adjusted_win_prob
        result.starter_change["probability_adjustment_applied"] = {
            "before":     round(_before_adj, 4),
            "adjustment": round(_sc_result.probability_adjustment, 4),
            "after":      round(fp_result.adjusted_win_prob, 4),
            "note":       "quality_delta_only:not_a_fixed_scratch_penalty",
        }
    elif _sc_result.probability_adjustment != 0.0 and _mlb_v2_native:
        result.starter_change["probability_adjustment_applied"] = {
            "suppressed": True,
            "proposed_adjustment": round(_sc_result.probability_adjustment, 4),
            "note": "MLB_V2_CURRENT_STARTER_ALREADY_IN_FEATURE_VECTOR:no_double_count",
        }

    # Inject uncertainty expansion into enrichment so calibration (stage 8) can
    # widen the distribution without moving the point estimate.
    # This is the second, separate effect — not double-counted with the delta above.
    if _sc_result.uncertainty_expansion > 0.0:
        enrichment = dict(enrichment)   # shallow copy — do not mutate caller's dict
        enrichment["starter_change_uncertainty_expansion"] = _sc_result.uncertainty_expansion

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
    #   Stages 3–5 return P(home wins). At this boundary it becomes one
    #   canonical pair and the candidate side is selected by lookup only.
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
        _post_sim_pair, _post_sim_error = _validate_binary_probability_pair(
            independent_prob_home,
            1.0 - independent_prob_home,
        )
        if _post_sim_error:
            blockers.append(_post_sim_error)
            result.blockers = blockers
            result.terminal_label = "DATA_CONTRACT_FAIL"
            result.snapshot_hash = result.build_snapshot_hash()
            return result
        independent_prob_final, _selection_error = _select_binary_candidate_probability(
            _post_sim_pair, orientation=orientation,
        )
        if _selection_error or independent_prob_final is None:
            blockers.append(
                _selection_error
                or "PROBABILITY_ORIENTATION_CONTRACT_BREACH:selection_unavailable"
            )
            result.blockers = blockers
            result.terminal_label = "DATA_CONTRACT_FAIL"
            result.snapshot_hash = result.build_snapshot_hash()
            return result
        result.sport_model["post_sim_home_away_probability"] = _post_sim_pair
        result.sport_model.setdefault("notes", []).append(
            "candidate_probability_selected_once:"
            f"orientation={'HOME' if is_home else 'AWAY'}:"
            f"value={independent_prob_final:.4f}"
        )

    independent_prob_final = max(0.01, min(0.99, independent_prob_final))
    result.outputs.independent_probability = round(independent_prob_final, 4)

    # -----------------------------------------------------------------------
    # Stage 6.5: TeamRankings contradiction analysis.
    # Now that independent_prob_final is known (home-team perspective), fill in
    # the contradiction fields.  TR contradictions are surfaced in the
    # disagreement audit and, for OPPOSITE_SIDE, also logged as a review flag.
    # TR contradiction NEVER flips the pick — it lowers confidence only.
    # -----------------------------------------------------------------------
    # The canonical pair retains the home perspective for downstream analysis;
    # no candidate-side complement is permitted after Stage 6.
    _prob_for_tr_comparison = (
        independent_prob_final
        if is_soccer
        else result.sport_model["post_sim_home_away_probability"]["HOME"]
    )
    tr_enr.fill_contradiction(_prob_for_tr_comparison)
    result.teamrankings = tr_enr.to_dict()

    # -----------------------------------------------------------------------
    # Stage 7: Model-disagreement audit
    # -----------------------------------------------------------------------
    submodel_probs = dict(sport_model_out.get("submodel_probs") or {})
    submodel_probs["simulation_output"] = round(independent_prob_post_sim, 4)
    dis_audit = audit_model_disagreement(submodel_probs)
    dis_audit_dict = dis_audit.to_dict()

    # Annotate TR contradiction into the disagreement audit for full observability
    if tr_enr.teamrankings_contradiction_flag:
        dis_audit_dict.setdefault("notes", []).append(
            f"TEAMRANKINGS_CONTRADICTION:{tr_enr.teamrankings_model_agreement}"
            f" delta={tr_enr.teamrankings_model_delta}"
            f" reason={tr_enr.teamrankings_contradiction_reason}"
        )
        if tr_enr.teamrankings_model_agreement == "OPPOSITE_SIDE":
            # OPPOSITE_SIDE → add a review flag (not a terminal blocker) so the
            # candidate is routed through the existing contradiction/final-refresh audit
            blockers.append(
                f"TEAMRANKINGS_CONTRADICTION_REVIEW:"
                f"TR_favors_opposite_side:delta={tr_enr.teamrankings_model_delta:.4f}"
            )

    result.disagreement_audit = dis_audit_dict

    # -----------------------------------------------------------------------
    # Stage 7.5: External Analyst Intelligence (discovery / contradiction only)
    # direct_probability_weight = 0.0 — analyst opinions NEVER adjust P(win)
    # -----------------------------------------------------------------------
    team_side = row.get("team") or row.get("player") or "home"
    opponent  = row.get("opponent") or row.get("opponent_team") or None
    # Compute market_no_vig early so the ledger snapshot is complete.
    # This is the same call that Stage 8 makes — read-only, no side effects.
    _market_no_vig_early = extract_no_vig_probability(
        enrichment, side=team_side, opponent=opponent
    )
    wow_side_for_analyst = "home" if is_home else "away"
    try:
        ai_result = run_external_analyst_intelligence(
            row                  = row,
            enrichment           = enrichment,
            sport                = sport,
            team                 = team_side,
            opponent             = opponent or "",
            wow_side             = wow_side_for_analyst,
            wow_independent_prob = independent_prob_final,
            wow_calibrated_lb    = None,   # not yet computed; filled after stage 9
            market_no_vig        = _market_no_vig_early,
        )
        result.external_analyst_intelligence = ai_result.to_dict()

        # Annotate disagreement audit with analyst contradiction
        cr = ai_result.contradiction_report
        if cr.external_analyst_conflict_flag:
            dis_audit_dict.setdefault("notes", []).append(
                f"EXTERNAL_ANALYST_CONTRADICTION:"
                f"oppose={cr.external_analyst_contradiction_count}"
                f":agree={cr.external_analyst_agreement_count}"
                f":consensus={cr.external_analyst_consensus_side}"
            )
            result.disagreement_audit = dis_audit_dict

        # Non-terminal contradiction review blockers
        if cr.force_contradiction_review:
            blockers.append(
                "EXTERNAL_ANALYST_CONTRADICTION_REVIEW:"
                f"force_review=True:"
                f"opposing_analysts={cr.external_analyst_contradiction_count}"
            )
        elif cr.external_analyst_conflict_flag:
            blockers.append(
                "EXTERNAL_ANALYST_CONTRADICTION_REVIEW:"
                f"opposing_analysts={cr.external_analyst_contradiction_count}"
            )

        # ANALYST_CONSENSUS_UNRESOLVED → lower confidence ceiling (non-terminal review)
        if cr.external_analyst_consensus_side == "ANALYST_CONSENSUS_UNRESOLVED":
            blockers.append(
                "ANALYST_CONSENSUS_UNRESOLVED:"
                "analysts_disagree_with_each_other:held_at_lower_confidence_ceiling"
            )

    except Exception as _eai_exc:
        # Analyst layer failure is NON-FATAL — base model continues unchanged
        result.external_analyst_intelligence = {
            "direct_probability_weight": 0.0,
            "sources_consulted": [],
            "sources_failed": [],
            "acquisition_notes": [f"LAYER_ERROR:{_eai_exc!s:.80}"],
        }

    # -----------------------------------------------------------------------
    # Stage 8: Dynamic calibration (market_no_vig enters HERE for first time)
    # -----------------------------------------------------------------------
    market_no_vig = _market_no_vig_early   # reuse already-computed value

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
        market_no_vig=(None if _mlb_v2_native else market_no_vig),
        market_inputs=({} if _mlb_v2_native else market_inputs),
    )
    if _mlb_v2_native:
        # MLB V2 already includes an independently-fitted Platt calibrator. Keep
        # the validated point estimate immutable and use the legacy calibration
        # layer only for uncertainty accounting. Market comparison remains Stage 11.
        cal_result.calibrated_probability = float(independent_prob_final)
        cal_result.model_weight = 1.0
        cal_result.market_weight = 0.0
        cal_result.market_no_vig_used = None
        cal_result.market_dependent_flag = False
        cal_result.net_edge = (
            float(independent_prob_final) - float(market_no_vig)
            if market_no_vig is not None else None
        )
        _home_lo = sport_model_out.get("model_native_home_lower_bound")
        _home_hi = sport_model_out.get("model_native_home_upper_bound")
        if _home_lo is not None and _home_hi is not None:
            if is_home:
                _emp_lo, _emp_hi = float(_home_lo), float(_home_hi)
            else:
                _emp_lo, _emp_hi = 1.0 - float(_home_hi), 1.0 - float(_home_lo)
            cal_result.calibrated_lower_bound = min(cal_result.calibrated_lower_bound, _emp_lo)
            cal_result.calibrated_upper_bound = max(cal_result.calibrated_upper_bound, _emp_hi)
        cal_result.calibration_notes.append("MLB_V2_NATIVE_PLATT_POINT_LOCK:market_weight=0")
        cal_result.calibration_notes.append("MLB_V2_BOUND=conservative_union_dynamic_and_empirical_calibration_interval")
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
    _snapshot_observability = {
        k: v for k, v in (result.market_comparison or {}).items()
        if k.startswith("market_snapshot_")
    }
    result.market_comparison = _build_market_comparison(
        row, enrichment, market_no_vig, cal_result.calibrated_probability,
    )
    # Preserve stage-0 snapshot handoff counters through stage 11 rebuild
    result.market_comparison.update(_snapshot_observability)

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
