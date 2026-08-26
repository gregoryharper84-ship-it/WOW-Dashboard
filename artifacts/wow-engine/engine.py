"""
engine.py
WOW-PATCH-2026-08-26-FREE-HOST-PROBABILITY-ENGINE v2 — Gate 11 support
+ ratified PREDICTIVE_BOUNDS_V1 amendment (Step 3d re-review, 2026-08-26)

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
            downgrade — see EndToEndResult.calibration_ladder_note), and
            Phase A is itself a fully ratified, complete pathway for
            exactly this "not yet calibrated" situation.
          - calibrator found -> score it via compute_predictive_bounds()
            (PREDICTIVE_BOUNDS_V1, ratified as a narrow amendment after
            the Step 3d review flagged the original implementation had
            no bounds method for these phases at all). Any of the
            ratified failure conditions blocks publication with a
            recorded MODEL_CALIBRATION_UNAVAILABLE gap, same "no silent
            repair" pattern as everywhere else in this pipeline.
"""
from __future__ import annotations

from dataclasses import dataclass, field

from regime_model import (
    PrimaryRegime, CohortCounts, PitcherCounts, dirichlet_multinomial_regime_probabilities,
    CurrentGameSignal, SignalAction, apply_current_game_signal,
)
from simulation import (
    simulate_prop_probability, bootstrap_candidate_raw_probability_sampler,
    RegimeConditionalParams, MIN_SIMULATION_DRAWS,
)
from calibration import (
    phase_a_shrinkage, MissingResamplerError, CalibrationStatus,
    compute_predictive_bounds, ModelCalibrationUnavailableError,
    PHASE_B_MIN_N, PHASE_C_MIN_N,
)
from calibrator_store import (
    load_active_calibrator, load_historical_calibration_rows,
    platt_coefficients_from_record, isotonic_model_from_record,
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
    load_historical_rows_fn=load_historical_calibration_rows,
) -> EndToEndResult:
    """Full pipeline. Every step's real ratified logic runs — nothing here
    is stubbed or shortcut. Raises no exceptions on the happy path;
    failures are captured and surfaced as an unpublishable row + error,
    matching the "no silent repair" rule.

    `load_calibrator_fn`/`load_historical_rows_fn` default to the real
    Supabase-backed lookups (calibrator_store.py) but are injectable so
    this pipeline stays testable without a live database — mirroring how
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

    # --- Calibration ladder: Phase A, or Phase B/C if eligible + promoted.
    # Both branches converge on the same (status, method, pre-blend point
    # estimate, lower_bound, upper_bound) shape so the downstream market/
    # blend/row-construction code below runs exactly once either way.
    calibration_ladder_note: str | None = None
    calibration_status = calibration_method = None
    calibration_version = calibration_training_n = calibration_parent_cohort = None
    bounds_method_version = None
    pre_blend_probability = lower_bound = upper_bound = None

    if settled_n_in_cohort >= PHASE_B_MIN_N and parent_cohort:
        method = CalibrationStatus.ISOTONIC_V1 if settled_n_in_cohort >= PHASE_C_MIN_N else CalibrationStatus.PLATT_TIME_SPLIT_V1
        record = load_calibrator_fn(parent_cohort, method)
        if record is not None:
            if record.get("parent_cohort") != parent_cohort or record.get("calibration_method") != method:
                result = _blocked(
                    [f"calibration_failed: MODEL_CALIBRATION_UNAVAILABLE: active calibrator "
                     f"record ({record.get('parent_cohort')!r}, {record.get('calibration_method')!r}) "
                     f"does not match requested ({parent_cohort!r}, {method!r})"],
                    regime_probability_sum=sum(sim.regime_probabilities.values()),
                    simulation_draws=sim.simulation_draws, simulation_seed=sim.simulation_seed,
                    raw_model_probability=sim.p_prop_unconditional,
                )
                result.signal_actions, result.signal_notes = signal_actions, signal_notes
                return result

            if method == CalibrationStatus.ISOTONIC_V1:
                model = isotonic_model_from_record(record)
                point_estimate = float(model.predict([sim.p_prop_unconditional])[0])
            else:
                coefficients = platt_coefficients_from_record(record)
                point_estimate = coefficients.apply(sim.p_prop_unconditional)

            try:
                historical_rows = load_historical_rows_fn(parent_cohort, method)
                candidate_sampler = bootstrap_candidate_raw_probability_sampler(
                    sim.regime_probabilities, sim.hits_by_regime
                )
                bounds = compute_predictive_bounds(
                    method=method,
                    historical_rows=historical_rows,
                    candidate_as_of=scored_at,
                    candidate_raw_probability_sampler=candidate_sampler,
                    full_data_calibrated_probability=point_estimate,
                    rng_seed=seed,
                )
            except ModelCalibrationUnavailableError as e:
                result = _blocked(
                    [f"calibration_failed: MODEL_CALIBRATION_UNAVAILABLE: {e}"],
                    regime_probability_sum=sum(sim.regime_probabilities.values()),
                    simulation_draws=sim.simulation_draws, simulation_seed=sim.simulation_seed,
                    raw_model_probability=sim.p_prop_unconditional,
                    independent_model_probability=point_estimate,
                    calibration_status=method,
                    calibration_method=method,
                    calibration_version=record.get("calibration_version"),
                    calibration_training_n=record.get("training_n"),
                    calibration_parent_cohort=parent_cohort,
                )
                result.signal_actions, result.signal_notes = signal_actions, signal_notes
                return result

            calibration_status = method
            calibration_method = method
            calibration_version = record.get("calibration_version")
            calibration_training_n = record.get("training_n")
            calibration_parent_cohort = parent_cohort
            bounds_method_version = bounds.bounds_method_version
            pre_blend_probability = bounds.calibrated_probability
            lower_bound = bounds.lower_bound
            upper_bound = bounds.upper_bound
        else:
            calibration_ladder_note = (
                f"cohort {parent_cohort!r} eligible for {method} (settled_n_in_cohort="
                f"{settled_n_in_cohort}) but no calibrator has been promoted for it yet — "
                f"falling back to Phase A shrinkage"
            )

    if pre_blend_probability is None:
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

        calibration_status = calib.calibration_status
        calibration_method = calib.calibration_method
        pre_blend_probability = calib.calibrated_probability
        lower_bound = calib.lower_bound
        upper_bound = calib.upper_bound

    market_prior = resolve_market_prior(candidate_direction, market_side_a, market_side_b, as_of=scored_at)
    blend = blend_market_prior(
        p_independent=pre_blend_probability,
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
        independent_model_probability=pre_blend_probability,
        effective_sample_size=n_eff,
        market_prior_available=market_prior.market_prior_available,
        market_prior_probability=market_prior.market_prior_probability,
        market_prior_quality=market_prior.market_prior_quality,
        market_prior_weight=blend.weight_used,
        market_prior_weight_source=blend.weight_source,
        reference_market_probability_raw=market_prior.reference_market_probability_raw,
        reference_market_side=market_prior.reference_market_side,
        reference_market_price=market_prior.reference_market_price,
        calibration_status=calibration_status,
        calibration_method=calibration_method,
        calibration_version=calibration_version,
        calibration_training_n=calibration_training_n,
        calibration_parent_cohort=calibration_parent_cohort,
        bounds_method_version=bounds_method_version,
        calibrated_probability=blend.calibrated_probability,
        calibrated_probability_lower_bound=lower_bound,
        calibrated_probability_upper_bound=upper_bound,
        money_lane_status=money_lane_status,
    )
    return EndToEndResult(
        row=determine_publishability(row), error=None,
        calibration_ladder_note=calibration_ladder_note,
        signal_actions=signal_actions, signal_notes=signal_notes,
    )
