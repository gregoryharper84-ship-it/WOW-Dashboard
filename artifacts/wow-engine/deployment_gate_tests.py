"""
deployment_gate_tests.py
WOW-PATCH-2026-08-26-FREE-HOST-PROBABILITY-ENGINE v2

The 11-point deployment gate (Gate 11 added per review recommendation:
a real end-to-end scored prop from fitted inputs, closing the gap where
gates 1-10 could pass while /score-prop still returned 501).

Revision note: this file was rewritten after external review found that
several v1 tests did not actually prove what they claimed. See inline
comments marked [REVIEW FIX] for what changed and why.
"""
from __future__ import annotations

import numpy as np
import pytest

from regime_model import (
    PrimaryRegime, CohortCounts, PitcherCounts,
    dirichlet_multinomial_regime_probabilities,
    regime_probability_sum_check,
    classify_historical_start, StartObservation, ExitReason,
)
from simulation import (
    simulate_prop_probability, RegimeConditionalParams, MissingRegimeDataError,
    MIN_SIMULATION_DRAWS,
)
from market import MarketQuote, resolve_market_prior, blend_market_prior
from calibration import phase_a_shrinkage, MissingResamplerError, phase_b_platt, phase_c_isotonic_eligible
from ledger import PredictionRow, determine_publishability
from engine import score_prop_end_to_end


def _uniform_cohort():
    return CohortCounts(counts={r: 100 for r in PrimaryRegime})


def _sample_pitcher():
    return PitcherCounts(counts={
        PrimaryRegime.NORMAL_EFFECTIVE_OUTING: 12,
        PrimaryRegime.INEFFICIENT_SURVIVING_OUTING: 4,
        PrimaryRegime.EARLY_EXIT_PERFORMANCE: 2,
    })


# --- Gate 2: regime probabilities sum to 1 -------------------------------

def test_gate_02_regime_probabilities_sum_to_one():
    probs = dirichlet_multinomial_regime_probabilities(_uniform_cohort(), _sample_pitcher())
    assert regime_probability_sum_check(probs)
    assert abs(sum(probs.values()) - 1.0) <= 1e-6


# --- Gate 3: mutual exclusivity — REAL classifier test [REVIEW FIX] -----

def _synthetic_start_batch():
    return [
        StartObservation(6.0, 92, ExitReason.COMPLETED_NORMAL),
        StartObservation(6.1, 118, ExitReason.COMPLETED_NORMAL),
        StartObservation(3.2, 71, ExitReason.PERFORMANCE_PULL),
        StartObservation(1.1, 28, ExitReason.INJURY_OR_HEALTH),
        StartObservation(5.0, 80, ExitReason.PLANNED_RESTRICTION),
        StartObservation(2.0, 45, ExitReason.GAME_DISRUPTED),
    ]


def test_gate_03_mutual_exclusivity_real_classifier():
    batch = _synthetic_start_batch()
    labels = [classify_historical_start(s) for s in batch]
    assert all(isinstance(lbl, PrimaryRegime) for lbl in labels)

    expected = [
        PrimaryRegime.NORMAL_EFFECTIVE_OUTING,
        PrimaryRegime.INEFFICIENT_SURVIVING_OUTING,
        PrimaryRegime.EARLY_EXIT_PERFORMANCE,
        PrimaryRegime.EARLY_EXIT_HEALTH_OR_WORKLOAD,
        PrimaryRegime.PLANNED_RESTRICTION_OR_SHORT_LEASH,
        PrimaryRegime.GAME_DISRUPTION,
    ]
    assert labels == expected
    assert labels == [classify_historical_start(s) for s in batch]  # deterministic


# --- Gate 4: deterministic simulation reproducibility --------------------

def _toy_conditional_params():
    def bf_sampler(rng, n):
        return rng.integers(15, 28, size=n)

    def stat_sampler(rng, n, bf):
        return rng.poisson(bf * 0.28)

    return {
        r: RegimeConditionalParams(regime=r, batters_faced_sampler=bf_sampler, stat_rate_sampler=stat_sampler)
        for r in PrimaryRegime
    }


def test_gate_04_deterministic_reproducibility():
    probs = dirichlet_multinomial_regime_probabilities(_uniform_cohort(), _sample_pitcher())
    params = _toy_conditional_params()
    r1 = simulate_prop_probability(probs, params, line=4.5, direction="MORE", seed=42, draws=MIN_SIMULATION_DRAWS)
    r2 = simulate_prop_probability(probs, params, line=4.5, direction="MORE", seed=42, draws=MIN_SIMULATION_DRAWS)
    assert r1.p_prop_unconditional == r2.p_prop_unconditional
    assert r1.simulation_seed == r2.simulation_seed == 42


# --- Gate 5: missing-regime negative test blocks publication -------------

def test_gate_05_missing_regime_blocks_publication():
    probs = dirichlet_multinomial_regime_probabilities(_uniform_cohort(), _sample_pitcher())
    params = _toy_conditional_params()
    del params[PrimaryRegime.GAME_DISRUPTION]
    with pytest.raises(MissingRegimeDataError):
        simulate_prop_probability(probs, params, line=4.5, direction="MORE", seed=1, draws=MIN_SIMULATION_DRAWS)


def test_gate_05b_min_draws_enforced():
    probs = dirichlet_multinomial_regime_probabilities(_uniform_cohort(), _sample_pitcher())
    params = _toy_conditional_params()
    with pytest.raises(ValueError):
        simulate_prop_probability(probs, params, line=4.5, direction="MORE", seed=1, draws=1000)


def test_gate_05c_phase_a_requires_real_resampler():
    with pytest.raises(MissingResamplerError):
        phase_a_shrinkage(p_raw=0.65, n_eff=12, rng_seed=1, resample_fn=None)


# --- Gate 6: market validity — same-side, staleness, direction [REVIEW FIX]

def test_gate_06_one_sided_market_blocked():
    over = MarketQuote(side="OVER", american_odds=-1500, line=4.5, settlement_basis="official_box_score",
                        retrieved_at="2026-08-26T00:00:00Z", participant="Luzardo", stat="strikeouts",
                        period="full_game", event_id="evt1")
    result = resolve_market_prior("OVER", over, None)
    assert result.market_prior_available is False
    assert result.market_prior_probability is None
    assert result.market_prior_quality == "SINGLE_SIDED_REFERENCE_ONLY"
    assert result.reference_market_price == -1500


def test_gate_06b_two_sided_matched_market_produces_no_vig():
    over = MarketQuote(side="OVER", american_odds=-150, line=4.5, settlement_basis="official_box_score",
                        retrieved_at="2026-08-26T00:00:00Z", participant="P", stat="strikeouts",
                        period="full_game", event_id="e1")
    under = MarketQuote(side="UNDER", american_odds=+130, line=4.5, settlement_basis="official_box_score",
                         retrieved_at="2026-08-26T00:00:05Z", participant="P", stat="strikeouts",
                         period="full_game", event_id="e1")
    result = resolve_market_prior("OVER", over, under)
    assert result.market_prior_available is True
    assert result.market_prior_quality == "EXACT_TWO_WAY_NO_VIG"
    assert 0 < result.market_prior_probability < 1


def test_gate_06c_cold_start_zero_weight_even_with_valid_market():
    over = MarketQuote(side="OVER", american_odds=-150, line=4.5, settlement_basis="official_box_score",
                        retrieved_at="2026-08-26T00:00:00Z", participant="P", stat="strikeouts",
                        period="full_game", event_id="e1")
    under = MarketQuote(side="UNDER", american_odds=+130, line=4.5, settlement_basis="official_box_score",
                         retrieved_at="2026-08-26T00:00:05Z", participant="P", stat="strikeouts",
                         period="full_game", event_id="e1")
    market_prior = resolve_market_prior("OVER", over, under)
    blend = blend_market_prior(p_independent=0.6, market_prior=market_prior, settled_n_in_cohort=50)
    assert blend.weight_used == 0.0
    assert blend.weight_source == "COLD_START_ZERO_WEIGHT"
    assert blend.calibrated_probability == 0.6


def test_gate_06d_same_side_pair_rejected():
    over1 = MarketQuote(side="OVER", american_odds=-150, line=4.5, settlement_basis="official_box_score",
                         retrieved_at="2026-08-26T00:00:00Z", participant="P", stat="strikeouts",
                         period="full_game", event_id="e1")
    over2 = MarketQuote(side="OVER", american_odds=-140, line=4.5, settlement_basis="official_box_score",
                         retrieved_at="2026-08-26T00:00:02Z", participant="P", stat="strikeouts",
                         period="full_game", event_id="e1")
    result = resolve_market_prior("OVER", over1, over2)
    assert result.market_prior_available is False
    assert result.market_prior_quality == "INVALID_SAME_SIDE_PAIR"


def test_gate_06e_stale_pair_rejected():
    over = MarketQuote(side="OVER", american_odds=-150, line=4.5, settlement_basis="official_box_score",
                        retrieved_at="2026-08-26T00:00:00Z", participant="P", stat="strikeouts",
                        period="full_game", event_id="e1")
    under = MarketQuote(side="UNDER", american_odds=+130, line=4.5, settlement_basis="official_box_score",
                         retrieved_at="2026-08-26T01:00:00Z", participant="P", stat="strikeouts",
                         period="full_game", event_id="e1")
    result = resolve_market_prior("OVER", over, under, max_staleness_seconds=300)
    assert result.market_prior_available is False
    assert result.market_prior_quality == "STALE_MISMATCH"


def test_gate_06f_candidate_direction_explicitly_mapped():
    over = MarketQuote(side="OVER", american_odds=-150, line=4.5, settlement_basis="official_box_score",
                        retrieved_at="2026-08-26T00:00:00Z", participant="P", stat="strikeouts",
                        period="full_game", event_id="e1")
    under = MarketQuote(side="UNDER", american_odds=+130, line=4.5, settlement_basis="official_box_score",
                         retrieved_at="2026-08-26T00:00:02Z", participant="P", stat="strikeouts",
                         period="full_game", event_id="e1")
    result_over_candidate = resolve_market_prior("OVER", over, under)
    result_under_candidate = resolve_market_prior("UNDER", over, under)
    assert abs(
        (result_over_candidate.market_prior_probability + result_under_candidate.market_prior_probability) - 1.0
    ) < 1e-9
    assert result_over_candidate.reference_market_side == "OVER"
    assert result_under_candidate.reference_market_side == "UNDER"


# --- Gate 7: money lane and confidence lane are SEPARATE [REVIEW FIX] ---

def test_gate_07_missing_payout_blocks_money_lane_only():
    row = PredictionRow(
        event_id="e1", event_start_time="2026-08-27T00:00:00Z", sport="MLB",
        market_type="PrizePicks_Goblin", stat_type="strikeouts", line=4.5, direction="MORE",
        source_snapshot_id="snap1",
        regime_probability_sum=1.0, simulation_draws=MIN_SIMULATION_DRAWS,
        calibrated_probability=0.7, calibrated_probability_lower_bound=0.6,
        calibrated_probability_upper_bound=0.8,
        calibration_status="PLATT_TIME_SPLIT_V1",
        money_lane_status="PAYOUT_UNRESOLVED",
    )
    row = determine_publishability(row)
    assert "money_lane_status != RESOLVED (payout unresolved)" in row.blockers
    assert "_MONEY_LANE_UNRESOLVED" in row.probability_ceiling
    assert row.probability_publishable is True
    assert row.calibrated_probability == 0.7


def test_gate_07b_resolved_money_lane_reaches_clean_ceiling():
    row = PredictionRow(
        event_id="e1", event_start_time="2026-08-27T00:00:00Z", sport="MLB",
        market_type="PrizePicks_Goblin", stat_type="strikeouts", line=4.5, direction="MORE",
        source_snapshot_id="snap1",
        regime_probability_sum=1.0, simulation_draws=MIN_SIMULATION_DRAWS,
        calibrated_probability=0.7, calibrated_probability_lower_bound=0.6,
        calibrated_probability_upper_bound=0.8,
        calibration_status="PLATT_TIME_SPLIT_V1",
        money_lane_status="RESOLVED",
    )
    row = determine_publishability(row)
    assert row.probability_publishable is True
    assert row.probability_ceiling == "MODEL_QUALIFIED_HOLD"
    assert "_MONEY_LANE_UNRESOLVED" not in row.probability_ceiling


# --- Gate 9: true walk-forward, no future leakage [REVIEW FIX] ----------

def test_gate_09_platt_rejects_too_few_folds():
    rng = np.random.default_rng(0)
    raw = rng.uniform(0.3, 0.7, size=250)
    y = (rng.uniform(0, 1, size=250) < raw).astype(int)
    folds = np.arange(250) % 3
    with pytest.raises(ValueError):
        phase_b_platt(raw, y, folds)


def test_gate_09b_platt_walk_forward_no_future_leakage():
    rng = np.random.default_rng(0)
    n = 300
    raw = rng.uniform(0.3, 0.7, size=n)
    y = (rng.uniform(0, 1, size=n) < raw).astype(int)
    folds = np.sort(rng.integers(0, 6, size=n))

    outcome = phase_b_platt(raw, y, folds)

    for test_fold, train_folds in outcome.fold_train_audit.items():
        assert all(tf < test_fold for tf in train_folds), (
            f"fold {test_fold} was trained on fold(s) {train_folds}, which include same-or-future data"
        )
    assert 0 not in outcome.fold_train_audit
    assert outcome.metrics.brier >= 0
    assert outcome.coefficients is not None


def test_gate_09c_isotonic_eligibility_empty_list_bug_fixed():
    assert phase_c_isotonic_eligible(500, []) is False
    assert phase_c_isotonic_eligible(500, [30, 30, 30]) is True
    assert phase_c_isotonic_eligible(499, [30, 30, 30]) is False
    assert phase_c_isotonic_eligible(500, [30, 29, 30]) is False


# --- Gate 10: Luzardo/Boyd smoke test ------------------------------------

def test_gate_10_luzardo_smoke_test_reproduces_blocked_diagnosis():
    over = MarketQuote(side="OVER", american_odds=-1500, line=4.5, settlement_basis="official_box_score",
                        retrieved_at="2026-08-26T00:00:00Z", participant="Luzardo", stat="strikeouts",
                        period="full_game", event_id="luzardo_evt")
    market_prior = resolve_market_prior("OVER", over, None)
    assert market_prior.market_prior_probability is None
    assert market_prior.reference_market_price == -1500

    row = PredictionRow(
        event_id="luzardo_evt", event_start_time="2026-08-27T00:00:00Z", sport="MLB",
        market_type="PrizePicks_Goblin", stat_type="strikeouts", line=4.5, direction="MORE",
        source_snapshot_id="snap-luzardo",
        regime_probability_sum=None,
        simulation_draws=None,
        calibrated_probability=None,
        market_prior_available=False,
        market_prior_probability=None,
        reference_market_price=-1500,
        reference_market_side="OVER",
        money_lane_status="PAYOUT_UNRESOLVED",
    )
    row = determine_publishability(row)

    assert row.probability_publishable is False
    assert row.calibrated_probability is None
    assert row.market_prior_probability is None
    assert row.reference_market_price == -1500
    assert row.probability_ceiling == "RESEARCH_INTEREST"


# --- Gate 11: real end-to-end positive path [NEW, per review] -----------

def _synthetic_fitted_params():
    def bf_sampler(rng, n):
        return rng.integers(18, 26, size=n)

    def stat_sampler(rng, n, bf):
        return rng.poisson(bf * 0.30)

    return {
        r: RegimeConditionalParams(regime=r, batters_faced_sampler=bf_sampler, stat_rate_sampler=stat_sampler)
        for r in PrimaryRegime
    }


def _synthetic_resampler(rng, n):
    return rng.normal(loc=0.55, scale=0.08, size=n).clip(0.01, 0.99)


def test_gate_11_end_to_end_positive_path_produces_publishable_probability():
    cohort = _uniform_cohort()
    pitcher = _sample_pitcher()
    params = _synthetic_fitted_params()

    over = MarketQuote(side="OVER", american_odds=-150, line=4.5, settlement_basis="official_box_score",
                        retrieved_at="2026-08-26T00:00:00Z", participant="TestPitcher", stat="strikeouts",
                        period="full_game", event_id="synthetic_evt")
    under = MarketQuote(side="UNDER", american_odds=+130, line=4.5, settlement_basis="official_box_score",
                         retrieved_at="2026-08-26T00:00:02Z", participant="TestPitcher", stat="strikeouts",
                         period="full_game", event_id="synthetic_evt")

    result = score_prop_end_to_end(
        event_id="synthetic_evt", event_start_time="2026-08-27T00:00:00Z", sport="MLB",
        stat_type="strikeouts", line=4.5, direction="MORE", source_snapshot_id="snap-synthetic",
        cohort=cohort, pitcher=pitcher, regime_params=params,
        resample_fn=_synthetic_resampler, n_eff=16, seed=7, candidate_direction="OVER",
        market_side_a=over, market_side_b=under,
        settled_n_in_cohort=0, money_lane_status="RESOLVED",
    )

    assert result.error is None
    row = result.row

    assert row.probability_publishable is True
    assert row.calibrated_probability is not None
    assert 0 < row.calibrated_probability_lower_bound <= row.calibrated_probability <= row.calibrated_probability_upper_bound < 1
    assert row.simulation_draws >= MIN_SIMULATION_DRAWS
    assert abs(row.regime_probability_sum - 1.0) <= 1e-6
    assert row.calibration_status == "PRECALIBRATION_SHRINKAGE"
    assert row.market_prior_weight == 0.0
    assert row.market_prior_probability is not None
    assert "PROHIBITED_PRECALIBRATION" in row.probability_ceiling


def test_gate_11b_missing_regime_data_fails_end_to_end_cleanly():
    cohort = _uniform_cohort()
    pitcher = _sample_pitcher()
    params = _synthetic_fitted_params()
    del params[PrimaryRegime.GAME_DISRUPTION]

    result = score_prop_end_to_end(
        event_id="broken_evt", event_start_time="2026-08-27T00:00:00Z", sport="MLB",
        stat_type="strikeouts", line=4.5, direction="MORE", source_snapshot_id="snap-broken",
        cohort=cohort, pitcher=pitcher, regime_params=params,
        resample_fn=_synthetic_resampler, n_eff=16, seed=7, candidate_direction="OVER",
    )
    assert result.error is not None
    assert result.row.probability_publishable is False
