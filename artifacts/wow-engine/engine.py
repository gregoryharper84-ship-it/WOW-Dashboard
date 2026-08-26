"""
engine.py
WOW-PATCH-2026-08-26-FREE-HOST-PROBABILITY-ENGINE v2 — Gate 11 support

Orchestrates regime_model -> simulation -> calibration ladder -> market
-> ledger into one call, proving the POSITIVE path actually produces a
publishable governed probability, not just that the negative paths
correctly block. This closes the gap the review identified: the original
10 gates could all pass while /score-prop still returned 501.

The fitted parameters used by test callers of this module are clearly
synthetic test fixtures (see deployment_gate_tests.py) — this module
does not embed real historical distributions itself, consistent with
8B.1's prohibition on invented distribution shapes. It proves the
*pipeline* is wired correctly; real per-sport fitting is a separate,
still-pending work item (see README).

Calibration ladder routing (Step 3d review fix — this previously always
called phase_a_shrinkage regardless of cohort size):
    settled_n_in_cohort <  200 -> Phase A shrinkage (always ratified)
    settled_n_in_cohort >= 200 -> load the active persisted calibrator
        (isotonic if N>=500, else Platt) for `parent_cohort`.
          - no calibrator promoted yet for this cohort -> fall back to
            Phase A; this is a real, recorded data point (not a silent
            downgrade — see PredictionRow.data_gaps), and Phase A is
            itself a fully ratified, complete pathway for exactly this
            "not yet calibrated" situation.
          - calibrator found -> score it. WOW has not yet ratified a
            per-candidate Phase B/C bounds method (calibration.py's
            PredictiveBoundsNotRatifiedError), so this currently always
            blocks publication rather than fabricate an interval —
            accurately reflecting that Phase B/C are wired but not yet
            ledger-complete, per the review.
"""
from __future__ import annotations

from dataclasses import dataclass, field

from regime_model import (
    PrimaryRegime, CohortCounts, PitcherCounts, dirichlet_multinomial_regime_probabilities,
    CurrentGameSignal, SignalAction, apply_current_game_signal,
)
from simulation import simulate_prop_probability, RegimeConditionalParams, MIN_SIMULATION_DRAWS
from calibration import (
    phase_a_shrinkage, MissingResamplerError, CalibrationStatus,
    PredictiveBoundsNotRatifiedError, PHASE_B_MIN_N, PHASE_C_MIN_N,
)
from calibrator_store import (
    load_active_calibrator, platt_coefficients_from_record, isotonic_model_from_record,
)
from market import MarketQuote, resolve_market_prior, blend_market_prior
from ledger import PredictionRow, determine_publishability


@dataclass
class EndToEndResult:
    row: PredictionRow
    error: str | None = None
    # Engine-level orchestration metadata -- NOT part of the wow_predictions
    # ledger schema, so adding fields here carries no governance/schema
    # weight. calibration_ladder_note records a non-blocking routing
    # decision (e.g. "eligible for Phase B but no calibrator promoted yet,
    # used Phase A"); signal_actions/signal_notes record what
    # apply_current_game_signal did, when a signal was supplied.
    calibration_ladder_note: str | None = None
    signal_actions: list[SignalAction] = field(default_factory=list)
    signal_notes: list[str] = field(default_factory=list)


def score_prop_end_to_end(
    *,
    event_id: str,
    event_start_time: str,
    sport: str,
    stat_type: str,
    line: float,
    direction: str,
    source_snapshot_id: str,
    cohort: CohortCounts,
    pitcher: PitcherCounts,
    regime_params: dict[PrimaryRegime, RegimeConditionalParams],
    resample_fn,
    n_eff: float,
    seed: int,
    candidate_direction: str,
    market_side_a: MarketQuote | None = None,
    market_side_b: MarketQuote | None = None,
    scored_at: str | None = None,
    settled_n_in_cohort: int = 0,
    parent_cohort: str | None = None,
    current_game_signal: CurrentGameSignal | None = None,
    money_lane_status: str = "PAYOUT_UNRESOLVED",
    draws: int = MIN_SIMULATION_DRAWS,
    load_calibrator_fn=load_active_calibrator,
) -> EndToEndResult:
    """Full pipeline. Every step's real ratified logic runs — nothing here
    is stubbed or shortcut. Raises no exceptions on the happy path;
    failures are captured and surfaced as an unpublishable row + error,
    matching the "no silent repair" rule.

    `load_calibrator_fn` defaults to the real Supabase-backed lookup
    (calibrator_store.load_active_calibrator) but is injectable so this
    pipeline stays testable without a live database — mirroring how
    `resample_fn` is already injected for Phase A."""

    def _blocked(data_gaps: list[str], **extra) -> EndToEndResult:
        row = PredictionRow(
            event_id=event_id, event_start_time=event_start_time, sport=sport,
            market_type="engine", stat_type=stat_type, line=line, direction=direction,
            source_snapshot_id=source_snapshot_id,
            data_gaps=data_gaps, **extra,
        )
        return EndToEndResult(row=determine_publishability(row), error="; ".join(data_gaps))

    regime_probs = dirichlet_multinomial_regime_probabilities(cohort, pitcher)

    signal_actions: list[SignalAction] = []
    signal_notes: list[str] = []
    if current_game_signal is not None:
        regime_probs, signal_actions, signal_notes = apply_current_game_signal(regime_probs, current_game_signal)
        if SignalAction.BLOCK in signal_actions:
            result = _blocked(
                [f"current_game_signal: {n}" for n in signal_notes] or ["current_game_signal_block"],
                regime_model_version="REGIME_MODEL_V1_DIRICHLET_MULTINOMIAL",
                regime_probabilities_json={r.value: p for r, p in regime_probs.items()},
                regime_probability_sum=sum(regime_probs.values()),
            )
            result.signal_actions = signal_actions
            result.signal_notes = signal_notes
            return result

    try:
        sim = simulate_prop_probability(
            regime_probs, regime_params, line=line, direction=direction, seed=seed, draws=draws
        )
    except Exception as e:
        result = _blocked([f"simulation_failed: {e}"])
        result.signal_actions, result.signal_notes = signal_actions, signal_notes
        return result

    calibration_ladder_note = None
    if settled_n_in_cohort >= PHASE_B_MIN_N:
        method = CalibrationStatus.ISOTONIC_V1 if settled_n_in_cohort >= PHASE_C_MIN_N else CalibrationStatus.PLATT_TIME_SPLIT_V1
        record = load_calibrator_fn(parent_cohort, method) if parent_cohort else None
        if record is not None:
            if method == CalibrationStatus.ISOTONIC_V1:
                model = isotonic_model_from_record(record)
                point_estimate = float(model.predict([sim.p_prop_unconditional])[0])
            else:
                coefficients = platt_coefficients_from_record(record)
                point_estimate = coefficients.apply(sim.p_prop_unconditional)
            bounds_error = PredictiveBoundsNotRatifiedError(
                f"{method} calibrator active for cohort {parent_cohort!r} (point estimate "
                f"{point_estimate:.4f}) but WOW has not yet ratified a per-candidate Phase B/C "
                f"predictive-bounds method — cannot publish without lower/upper bounds."
            )
            result = _blocked(
                [f"calibration_failed: {bounds_error}"],
                regime_probability_sum=sum(sim.regime_probabilities.values()),
                simulation_draws=sim.simulation_draws, simulation_seed=sim.simulation_seed,
                raw_model_probability=sim.p_prop_unconditional,
                independent_model_probability=point_estimate,
                calibration_status=method,
                calibration_method=method,
            )
            result.signal_actions, result.signal_notes = signal_actions, signal_notes
            return result
        calibration_ladder_note = (
            f"cohort {parent_cohort!r} eligible for {method} (settled_n_in_cohort="
            f"{settled_n_in_cohort}) but no calibrator has been promoted for it yet — "
            f"falling back to Phase A shrinkage"
        )

    try:
        calib = phase_a_shrinkage(
            p_raw=sim.p_prop_unconditional, n_eff=n_eff, rng_seed=seed, resample_fn=resample_fn
        )
    except MissingResamplerError as e:
        result = _blocked(
            [f"calibration_failed: {e}"],
            regime_probability_sum=sum(sim.regime_probabilities.values()),
            simulation_draws=sim.simulation_draws, simulation_seed=sim.simulation_seed,
            raw_model_probability=sim.p_prop_unconditional,
        )
        result.signal_actions, result.signal_notes = signal_actions, signal_notes
        return result

    market_prior = resolve_market_prior(candidate_direction, market_side_a, market_side_b, as_of=scored_at)
    blend = blend_market_prior(
        p_independent=calib.calibrated_probability,
        market_prior=market_prior,
        settled_n_in_cohort=settled_n_in_cohort,
    )

    row = PredictionRow(
        event_id=event_id, event_start_time=event_start_time, sport=sport,
        market_type="engine", stat_type=stat_type, line=line, direction=direction,
        source_snapshot_id=source_snapshot_id,
        regime_model_version="REGIME_MODEL_V1_DIRICHLET_MULTINOMIAL",
        regime_probabilities_json={r.value: p for r, p in sim.regime_probabilities.items()},
        regime_probability_sum=sum(sim.regime_probabilities.values()),
        primary_failure_path=sim.primary_failure_path.value if sim.primary_failure_path else None,
        simulation_seed=sim.simulation_seed,
        simulation_draws=sim.simulation_draws,
        raw_model_probability=sim.p_prop_unconditional,
        independent_model_probability=calib.calibrated_probability,
        effective_sample_size=n_eff,
        market_prior_available=market_prior.market_prior_available,
        market_prior_probability=market_prior.market_prior_probability,
        market_prior_quality=market_prior.market_prior_quality,
        market_prior_weight=blend.weight_used,
        market_prior_weight_source=blend.weight_source,
        reference_market_probability_raw=market_prior.reference_market_probability_raw,
        reference_market_side=market_prior.reference_market_side,
        reference_market_price=market_prior.reference_market_price,
        calibration_status=calib.calibration_status,
        calibration_method=calib.calibration_method,
        calibrated_probability=blend.calibrated_probability,
        calibrated_probability_lower_bound=calib.lower_bound,
        calibrated_probability_upper_bound=calib.upper_bound,
        money_lane_status=money_lane_status,
    )
    return EndToEndResult(
        row=determine_publishability(row), error=None,
        calibration_ladder_note=calibration_ladder_note,
        signal_actions=signal_actions, signal_notes=signal_notes,
    )
