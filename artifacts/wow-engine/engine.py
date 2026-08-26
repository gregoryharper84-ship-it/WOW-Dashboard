"""
engine.py
WOW-PATCH-2026-08-26-FREE-HOST-PROBABILITY-ENGINE v2 — Gate 11 support

Orchestrates regime_model -> simulation -> calibration (Phase A) ->
market -> ledger into one call, proving the POSITIVE path actually
produces a publishable governed probability, not just that the negative
paths correctly block. This closes the gap the review identified: the
original 10 gates could all pass while /score-prop still returned 501.

The fitted parameters used by test callers of this module are clearly
synthetic test fixtures (see deployment_gate_tests.py) — this module
does not embed real historical distributions itself, consistent with
8B.1's prohibition on invented distribution shapes. It proves the
*pipeline* is wired correctly; real per-sport fitting is a separate,
still-pending work item (see README).
"""
from __future__ import annotations

from dataclasses import dataclass

from regime_model import PrimaryRegime, CohortCounts, PitcherCounts, dirichlet_multinomial_regime_probabilities
from simulation import simulate_prop_probability, RegimeConditionalParams, MIN_SIMULATION_DRAWS
from calibration import phase_a_shrinkage, MissingResamplerError
from market import MarketQuote, resolve_market_prior, blend_market_prior
from ledger import PredictionRow, determine_publishability


@dataclass
class EndToEndResult:
    row: PredictionRow
    error: str | None = None


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
    settled_n_in_cohort: int = 0,
    money_lane_status: str = "PAYOUT_UNRESOLVED",
    draws: int = MIN_SIMULATION_DRAWS,
) -> EndToEndResult:
    """Full pipeline. Every step's real ratified logic runs — nothing here
    is stubbed or shortcut. Raises no exceptions on the happy path;
    failures are captured and surfaced as an unpublishable row + error,
    matching the "no silent repair" rule."""

    regime_probs = dirichlet_multinomial_regime_probabilities(cohort, pitcher)

    try:
        sim = simulate_prop_probability(
            regime_probs, regime_params, line=line, direction=direction, seed=seed, draws=draws
        )
    except Exception as e:
        row = PredictionRow(
            event_id=event_id, event_start_time=event_start_time, sport=sport,
            market_type="engine", stat_type=stat_type, line=line, direction=direction,
            source_snapshot_id=source_snapshot_id,
            data_gaps=[f"simulation_failed: {e}"],
        )
        return EndToEndResult(row=determine_publishability(row), error=str(e))

    try:
        calib = phase_a_shrinkage(
            p_raw=sim.p_prop_unconditional, n_eff=n_eff, rng_seed=seed, resample_fn=resample_fn
        )
    except MissingResamplerError as e:
        row = PredictionRow(
            event_id=event_id, event_start_time=event_start_time, sport=sport,
            market_type="engine", stat_type=stat_type, line=line, direction=direction,
            source_snapshot_id=source_snapshot_id,
            regime_probability_sum=sum(sim.regime_probabilities.values()),
            simulation_draws=sim.simulation_draws, simulation_seed=sim.simulation_seed,
            raw_model_probability=sim.p_prop_unconditional,
            data_gaps=[f"calibration_failed: {e}"],
        )
        return EndToEndResult(row=determine_publishability(row), error=str(e))

    market_prior = resolve_market_prior(candidate_direction, market_side_a, market_side_b)
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
    return EndToEndResult(row=determine_publishability(row), error=None)
