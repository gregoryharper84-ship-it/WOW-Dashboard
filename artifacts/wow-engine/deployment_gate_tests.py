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
    simulate_prop_probability, bootstrap_candidate_raw_probability_sampler,
    RegimeConditionalParams, MissingRegimeDataError, MIN_SIMULATION_DRAWS,
)
from market import MarketQuote, resolve_market_prior, blend_market_prior
from calibration import (
    phase_a_shrinkage, MissingResamplerError, phase_b_platt, phase_c_isotonic_eligible,
    phase_c_fit_isotonic, compute_predictive_bounds, ModelCalibrationUnavailableError,
    HistoricalCalibrationRow, PREDICTIVE_BOUNDS_METHOD_VERSION,
)
from ledger import PredictionRow, determine_publishability
from calibrator_store import (
    _serialize_isotonic_model, _deserialize_isotonic_model, platt_coefficients_from_record,
)
from engine import score_prop_end_to_end
import api
from fastapi.testclient import TestClient


def _sequential_timestamps(n: int, start="2026-01-01T00:00:00Z", step_minutes: int = 1) -> list[str]:
    from datetime import datetime, timedelta
    base = datetime.fromisoformat(start.replace("Z", "+00:00"))
    return [(base + timedelta(minutes=i * step_minutes)).isoformat() for i in range(n)]


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
    result = resolve_market_prior("OVER", over, under, as_of="2026-08-26T00:00:05Z")
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
    market_prior = resolve_market_prior("OVER", over, under, as_of="2026-08-26T00:00:05Z")
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
    result_over_candidate = resolve_market_prior("OVER", over, under, as_of="2026-08-26T00:00:02Z")
    result_under_candidate = resolve_market_prior("UNDER", over, under, as_of="2026-08-26T00:00:02Z")
    assert abs(
        (result_over_candidate.market_prior_probability + result_under_candidate.market_prior_probability) - 1.0
    ) < 1e-9
    assert result_over_candidate.reference_market_side == "OVER"
    assert result_under_candidate.reference_market_side == "UNDER"


# --- Gate 6g-i: freshness actually enforced [Step 3d review fix] --------
# Step 3d re-review found two live bugs: (1) exact_match() ignored the
# caller's max_staleness_seconds and always used the hardcoded 300s
# default, so a 60s-max request still accepted quotes 120s apart; (2)
# "fresh" only meant fresh relative to each other, not relative to actual
# scoring time, so two quotes from January 2020 two seconds apart were
# accepted as a live market. Both are fixed above; these tests reproduce
# the exact failing scenarios from the review.

def test_gate_06g_custom_staleness_window_actually_enforced():
    over = MarketQuote(side="OVER", american_odds=-150, line=4.5, settlement_basis="official_box_score",
                        retrieved_at="2026-08-26T00:00:00Z", participant="P", stat="strikeouts",
                        period="full_game", event_id="e1")
    under = MarketQuote(side="UNDER", american_odds=+130, line=4.5, settlement_basis="official_box_score",
                         retrieved_at="2026-08-26T00:02:00Z", participant="P", stat="strikeouts",
                         period="full_game", event_id="e1")
    # 120 seconds apart, caller requests a 60-second maximum — must reject,
    # not silently fall back to the 300-second module default.
    result = resolve_market_prior("OVER", over, under, as_of="2026-08-26T00:02:00Z", max_staleness_seconds=60)
    assert result.market_prior_available is False
    assert result.market_prior_quality == "STALE_MISMATCH"


def test_gate_06h_absolute_quote_age_enforced_against_scoring_time():
    over = MarketQuote(side="OVER", american_odds=-150, line=4.5, settlement_basis="official_box_score",
                        retrieved_at="2020-01-01T00:00:00Z", participant="P", stat="strikeouts",
                        period="full_game", event_id="e1")
    under = MarketQuote(side="UNDER", american_odds=+130, line=4.5, settlement_basis="official_box_score",
                         retrieved_at="2020-01-01T00:00:02Z", participant="P", stat="strikeouts",
                         period="full_game", event_id="e1")
    # Fresh relative to each other (2 seconds apart) but six years stale
    # relative to the actual scoring time.
    result = resolve_market_prior("OVER", over, under, as_of="2026-08-26T00:00:00Z")
    assert result.market_prior_available is False
    assert result.market_prior_quality == "STALE_RELATIVE_TO_SCORING_TIME"


def test_gate_06i_missing_as_of_blocks_market_prior():
    over = MarketQuote(side="OVER", american_odds=-150, line=4.5, settlement_basis="official_box_score",
                        retrieved_at="2026-08-26T00:00:00Z", participant="P", stat="strikeouts",
                        period="full_game", event_id="e1")
    under = MarketQuote(side="UNDER", american_odds=+130, line=4.5, settlement_basis="official_box_score",
                         retrieved_at="2026-08-26T00:00:02Z", participant="P", stat="strikeouts",
                         period="full_game", event_id="e1")
    result = resolve_market_prior("OVER", over, under)  # no as_of supplied
    assert result.market_prior_available is False
    assert result.market_prior_quality == "MISSING_AS_OF_SCORING_TIME"


# --- Gate 7: money lane and confidence lane are SEPARATE [REVIEW FIX] ---

def test_gate_07_missing_payout_blocks_money_lane_only():
    row = PredictionRow(
        event_id="e1", event_start_time="2026-08-27T00:00:00Z", sport="MLB",
        market_type="PrizePicks_Goblin", stat_type="strikeouts", line=4.5, direction="MORE",
        source_snapshot_id="snap1",
        raw_model_probability=0.62,
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
        raw_model_probability=0.62,
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


# --- Gate 7c: structurally invalid row rejected [Step 3d review fix] ----
# ChatGPT's Step 3d re-review constructed a row with raw_model_probability
# =None, an empty source_snapshot_id, and calibration_status="BOGUS", but
# with otherwise-valid calibrated bounds/regime sum/draw count, and got
# probability_publishable=True back. determine_publishability() now
# validates all three fields explicitly instead of trusting the caller.

def test_gate_07c_structurally_invalid_row_rejected():
    row = PredictionRow(
        event_id="e1", event_start_time="2026-08-27T00:00:00Z", sport="MLB",
        market_type="engine", stat_type="strikeouts", line=4.5, direction="MORE",
        source_snapshot_id="",
        raw_model_probability=None,
        regime_probability_sum=1.0, simulation_draws=MIN_SIMULATION_DRAWS,
        calibrated_probability=0.7, calibrated_probability_lower_bound=0.6,
        calibrated_probability_upper_bound=0.8,
        calibration_status="BOGUS",
    )
    row = determine_publishability(row)
    assert row.probability_publishable is False
    assert "raw_model_probability missing or out of (0,1) bounds" in row.data_gaps
    assert "source_snapshot_id missing or empty" in row.data_gaps
    assert any(g.startswith("calibration_status not recognized") for g in row.data_gaps)


def test_gate_07d_raw_model_probability_out_of_bounds_rejected():
    row = PredictionRow(
        event_id="e1", event_start_time="2026-08-27T00:00:00Z", sport="MLB",
        market_type="engine", stat_type="strikeouts", line=4.5, direction="MORE",
        source_snapshot_id="snap1",
        raw_model_probability=1.5,
        regime_probability_sum=1.0, simulation_draws=MIN_SIMULATION_DRAWS,
        calibrated_probability=0.7, calibrated_probability_lower_bound=0.6,
        calibrated_probability_upper_bound=0.8,
        calibration_status="PLATT_TIME_SPLIT_V1",
    )
    row = determine_publishability(row)
    assert row.probability_publishable is False
    assert "raw_model_probability missing or out of (0,1) bounds" in row.data_gaps


# --- Gate 9: true walk-forward, no future leakage [REVIEW FIX] ----------

def test_gate_09_platt_rejects_too_few_folds():
    rng = np.random.default_rng(0)
    raw = rng.uniform(0.3, 0.7, size=250)
    y = (rng.uniform(0, 1, size=250) < raw).astype(int)
    folds = np.arange(250) % 3
    timestamps = _sequential_timestamps(250)
    with pytest.raises(ValueError):
        phase_b_platt(raw, y, folds, timestamps)


def test_gate_09b_platt_walk_forward_no_future_leakage():
    rng = np.random.default_rng(0)
    n = 300
    raw = rng.uniform(0.3, 0.7, size=n)
    y = (rng.uniform(0, 1, size=n) < raw).astype(int)
    folds = np.sort(rng.integers(0, 6, size=n))
    timestamps = _sequential_timestamps(n)

    outcome = phase_b_platt(raw, y, folds, timestamps)

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


def test_gate_09cb_fold_ids_alone_are_not_trusted_for_chronology():
    # Step 3d review constraint: "the walk-forward code should not merely
    # trust numeric fold IDs. It should enforce something like
    # max(train_timestamp) < min(validation_timestamp) for every
    # validation fold." Build fold IDs that *look* time-ordered (0..5,
    # monotonically non-decreasing by row) but pair them with timestamps
    # where one late-fold row is actually earlier than an early-fold row
    # -- phase_b_platt must catch this instead of trusting the fold ID.
    rng = np.random.default_rng(0)
    n = 300
    raw = rng.uniform(0.3, 0.7, size=n)
    y = (rng.uniform(0, 1, size=n) < raw).astype(int)
    folds = np.sort(rng.integers(0, 6, size=n))
    timestamps = _sequential_timestamps(n)
    # Corrupt one row deep in a late fold to have an out-of-order (early)
    # timestamp, without changing its fold assignment.
    late_fold_positions = np.where(folds == folds.max())[0]
    timestamps[late_fold_positions[0]] = "2020-01-01T00:00:00Z"

    with pytest.raises(ValueError, match="chronological"):
        phase_b_platt(raw, y, folds, timestamps)

    with pytest.raises(ValueError, match="chronological"):
        phase_c_fit_isotonic(raw, y, folds, timestamps)


def test_gate_09d_platt_coefficients_apply_does_not_crash():
    # Step 3d review: PlattCoefficients.apply() called math.log() but
    # calibration.py never imported math, so any real scoring call through
    # the fitted coefficients raised NameError despite 20/20 tests passing
    # (none of them exercised .apply()). Fixed by importing math; this
    # test exercises the path directly so a regression trips a real test.
    rng = np.random.default_rng(0)
    n = 300
    raw = rng.uniform(0.3, 0.7, size=n)
    y = (rng.uniform(0, 1, size=n) < raw).astype(int)
    folds = np.sort(rng.integers(0, 6, size=n))
    timestamps = _sequential_timestamps(n)
    outcome = phase_b_platt(raw, y, folds, timestamps)

    scored = outcome.coefficients.apply(0.62)
    assert 0.0 < scored < 1.0


# --- Gate 9e-f: calibrator persistence [Step 3d review fix] -------------
# Step 3d review: "Phase B returns PlattCoefficients in memory, but I
# found no database table/fields or artifact serialization that persists
# a, b, fit cohort/version, training interval, etc. across service
# restarts. Returning coefficients is not persistence." These tests cover
# the pure serialize/deserialize/reconstruct logic in calibrator_store.py
# without touching Supabase (see README: gates 1/8 need a live instance
# and are untestable in this sandbox) -- save_*/load_active_calibrator
# themselves are thin wrappers around that already-covered logic plus the
# Supabase client call.

def test_gate_09e_platt_coefficients_reconstructed_from_persisted_record():
    rng = np.random.default_rng(0)
    n = 300
    raw = rng.uniform(0.3, 0.7, size=n)
    y = (rng.uniform(0, 1, size=n) < raw).astype(int)
    folds = np.sort(rng.integers(0, 6, size=n))
    timestamps = _sequential_timestamps(n)
    outcome = phase_b_platt(raw, y, folds, timestamps)

    record = {"platt_a": outcome.coefficients.a, "platt_b": outcome.coefficients.b}
    reconstructed = platt_coefficients_from_record(record)

    assert reconstructed.a == outcome.coefficients.a
    assert reconstructed.b == outcome.coefficients.b
    assert reconstructed.apply(0.62) == outcome.coefficients.apply(0.62)


def test_gate_09f_isotonic_model_serialization_roundtrips():
    rng = np.random.default_rng(0)
    n = 300
    raw = rng.uniform(0.3, 0.7, size=n)
    y = (rng.uniform(0, 1, size=n) < raw).astype(int)
    folds = np.sort(rng.integers(0, 6, size=n))
    timestamps = _sequential_timestamps(n)
    fit = phase_c_fit_isotonic(raw, y, folds, timestamps)

    artifact_b64 = _serialize_isotonic_model(fit.model)
    restored = _deserialize_isotonic_model(artifact_b64)

    probe = np.array([0.2, 0.4, 0.6, 0.8])
    assert np.array_equal(restored.predict(probe), fit.model.predict(probe))


# --- Gate 9g-j: ratified Phase B/C predictive bounds [PREDICTIVE_BOUNDS_V1]
# The Step 3d re-review ratified a narrow amendment specifying HOW Phase
# B/C candidates get calibrated_probability_lower_bound/upper_bound
# (calibration.compute_predictive_bounds): resample the historical
# calibration cohort strictly before candidate_as_of, refit the active
# calibrator per bootstrap realization, score a candidate raw-probability
# realization from the sport-specific simulation/bootstrap path, and take
# q10/q90 of the resulting calibrated-probability distribution (widened
# to include the full-data point estimate).

def _synthetic_historical_rows(n=80, start="2026-01-01T00:00:00Z", step_minutes=60, seed=1):
    rng = np.random.default_rng(seed)
    raw = rng.uniform(0.3, 0.7, size=n)
    y = (rng.uniform(0, 1, size=n) < raw).astype(int)
    timestamps = _sequential_timestamps(n, start=start, step_minutes=step_minutes)
    return [
        HistoricalCalibrationRow(raw_probability=float(r), outcome=int(o), timestamp=t)
        for r, o, t in zip(raw, y, timestamps)
    ]


def test_gate_09g0_bootstrap_candidate_sampler_matches_point_estimate_on_average():
    # simulation.bootstrap_candidate_raw_probability_sampler() is the
    # "sport-specific simulation/bootstrap path" compute_predictive_bounds()
    # draws candidate raw-probability realizations from. Its mean over
    # many draws should converge to the same weighted point estimate
    # simulate_prop_probability() itself computes.
    probs = dirichlet_multinomial_regime_probabilities(_uniform_cohort(), _sample_pitcher())
    params = _toy_conditional_params()
    sim = simulate_prop_probability(probs, params, line=4.5, direction="MORE", seed=42, draws=MIN_SIMULATION_DRAWS)

    sampler = bootstrap_candidate_raw_probability_sampler(sim.regime_probabilities, sim.hits_by_regime)
    rng = np.random.default_rng(0)
    draws = [sampler(rng) for _ in range(500)]

    assert all(0.0 <= d <= 1.0 for d in draws)
    assert abs(np.mean(draws) - sim.p_prop_unconditional) < 0.01


def test_gate_09g_predictive_bounds_positive_path():
    rows = _synthetic_historical_rows()

    def sampler(rng):
        return float(rng.uniform(0.4, 0.6))

    bounds = compute_predictive_bounds(
        method="PLATT_TIME_SPLIT_V1",
        historical_rows=rows,
        candidate_as_of="2026-08-27T00:00:00Z",
        candidate_raw_probability_sampler=sampler,
        full_data_calibrated_probability=0.55,
        rng_seed=7,
    )
    assert bounds.bounds_method_version == PREDICTIVE_BOUNDS_METHOD_VERSION
    assert bounds.realizations_used >= 2000
    assert 0 < bounds.lower_bound <= bounds.calibrated_probability <= bounds.upper_bound < 1
    assert bounds.calibrated_probability == 0.55


def test_gate_09h_predictive_bounds_requires_2000_realizations():
    rows = _synthetic_historical_rows()
    with pytest.raises(ValueError):
        compute_predictive_bounds(
            method="PLATT_TIME_SPLIT_V1", historical_rows=rows,
            candidate_as_of="2026-08-27T00:00:00Z",
            candidate_raw_probability_sampler=lambda rng: 0.5,
            full_data_calibrated_probability=0.5, rng_seed=1,
            bootstrap_realizations=500,
        )


def test_gate_09i_predictive_bounds_blocks_on_missing_as_of_and_future_dated_cohort():
    rows = _synthetic_historical_rows()

    with pytest.raises(ModelCalibrationUnavailableError, match="candidate_as_of"):
        compute_predictive_bounds(
            method="PLATT_TIME_SPLIT_V1", historical_rows=rows, candidate_as_of=None,
            candidate_raw_probability_sampler=lambda rng: 0.5,
            full_data_calibrated_probability=0.5, rng_seed=1,
        )

    # Every historical row postdates candidate_as_of -- none are eligible.
    with pytest.raises(ModelCalibrationUnavailableError, match="no historical calibration rows"):
        compute_predictive_bounds(
            method="PLATT_TIME_SPLIT_V1", historical_rows=rows,
            candidate_as_of="2020-01-01T00:00:00Z",
            candidate_raw_probability_sampler=lambda rng: 0.5,
            full_data_calibrated_probability=0.5, rng_seed=1,
        )


def test_gate_09j_predictive_bounds_blocks_on_excessive_fit_failure_rate():
    rows = _synthetic_historical_rows()

    def flaky_sampler(rng):
        # ~8% of realizations produce a non-finite candidate draw --
        # above the 5% default tolerance, but with enough total
        # realizations requested (5000) that >= 2000 still succeed, so
        # this exercises the failure-RATE check specifically, distinct
        # from the too-few-valid-realizations check in the test above.
        return float("nan") if rng.uniform() < 0.08 else 0.5

    with pytest.raises(ModelCalibrationUnavailableError, match="failure rate"):
        compute_predictive_bounds(
            method="PLATT_TIME_SPLIT_V1", historical_rows=rows,
            candidate_as_of="2026-08-27T00:00:00Z",
            candidate_raw_probability_sampler=flaky_sampler,
            full_data_calibrated_probability=0.5, rng_seed=1,
            bootstrap_realizations=5000,
        )


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
        market_side_a=over, market_side_b=under, scored_at="2026-08-26T00:00:02Z",
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
    # Phase A has no persisted-calibrator identity or Phase B/C bounds
    # method to report -- these must stay None, not leak stale values.
    assert row.calibration_version is None
    assert row.bounds_method_version is None
    assert row.model_timestamp == "2026-08-26T00:00:02Z"


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


# --- Gate 11c-f: calibration-ladder routing [Step 3d review fix] --------
# Step 3d review: "The production orchestrator only implements Phase A.
# score_prop_end_to_end() always calls phase_a_shrinkage() regardless of
# cohort size. It does not select Phase B at N >= 200 or Phase C at
# N >= 500, and it does not load/apply stored calibrators. It also does
# not currently invoke the current-game signal/eligibility layer."

def test_gate_11c_phase_b_eligible_cohort_without_calibrator_falls_back_to_phase_a():
    cohort = _uniform_cohort()
    pitcher = _sample_pitcher()
    params = _synthetic_fitted_params()

    result = score_prop_end_to_end(
        event_id="e1", event_start_time="2026-08-27T00:00:00Z", sport="MLB",
        stat_type="strikeouts", line=4.5, direction="MORE", source_snapshot_id="snap-b-fallback",
        cohort=cohort, pitcher=pitcher, regime_params=params,
        resample_fn=_synthetic_resampler, n_eff=16, seed=7, candidate_direction="OVER",
        settled_n_in_cohort=250, parent_cohort="MLB_SP_RH_2026",
        money_lane_status="RESOLVED",
        load_calibrator_fn=lambda cohort_key, method: None,  # nothing promoted yet
    )
    assert result.error is None
    assert result.row.probability_publishable is True
    assert result.row.calibration_status == "PRECALIBRATION_SHRINKAGE"
    assert result.calibration_ladder_note is not None
    assert "PLATT_TIME_SPLIT_V1" in result.calibration_ladder_note
    assert "no calibrator has been promoted" in result.calibration_ladder_note


def _fake_platt_record(parent_cohort="MLB_SP_RH_2026", a=0.0, b=1.0, calibration_version="v1", training_n=300):
    return {
        "parent_cohort": parent_cohort, "calibration_method": "PLATT_TIME_SPLIT_V1",
        "platt_a": a, "platt_b": b,
        "calibration_version": calibration_version, "training_n": training_n,
    }


def test_gate_11d_phase_b_calibrator_found_but_no_historical_rows_blocks():
    # Ratified PREDICTIVE_BOUNDS_V1 still blocks -- but now for a real,
    # named reason (no eligible historical calibration cohort to bootstrap
    # from), not because the bounds method itself was unspecified.
    cohort = _uniform_cohort()
    pitcher = _sample_pitcher()
    params = _synthetic_fitted_params()

    result = score_prop_end_to_end(
        event_id="e1", event_start_time="2026-08-27T00:00:00Z", sport="MLB",
        stat_type="strikeouts", line=4.5, direction="MORE", source_snapshot_id="snap-b-found",
        cohort=cohort, pitcher=pitcher, regime_params=params,
        resample_fn=_synthetic_resampler, n_eff=16, seed=7, candidate_direction="OVER",
        settled_n_in_cohort=250, parent_cohort="MLB_SP_RH_2026",
        scored_at="2026-08-27T00:00:00Z",
        load_calibrator_fn=lambda cohort_key, method: _fake_platt_record() if method == "PLATT_TIME_SPLIT_V1" else None,
        load_historical_rows_fn=lambda cohort_key, method: [],  # nothing settled for this cohort yet
    )
    assert result.error is not None
    assert "MODEL_CALIBRATION_UNAVAILABLE" in result.error
    assert result.row.probability_publishable is False
    assert result.row.independent_model_probability is not None
    assert result.row.calibrated_probability is None


def test_gate_11dd_phase_b_calibrator_cohort_mismatch_blocks():
    cohort = _uniform_cohort()
    pitcher = _sample_pitcher()
    params = _synthetic_fitted_params()

    # A calibrator record whose own parent_cohort doesn't match what was
    # requested -- e.g. a caller bug in load_calibrator_fn returning the
    # wrong cohort's active calibrator.
    mismatched_record = _fake_platt_record(parent_cohort="WNBA_STARTER_2026")

    result = score_prop_end_to_end(
        event_id="e1", event_start_time="2026-08-27T00:00:00Z", sport="MLB",
        stat_type="strikeouts", line=4.5, direction="MORE", source_snapshot_id="snap-b-mismatch",
        cohort=cohort, pitcher=pitcher, regime_params=params,
        resample_fn=_synthetic_resampler, n_eff=16, seed=7, candidate_direction="OVER",
        settled_n_in_cohort=250, parent_cohort="MLB_SP_RH_2026", scored_at="2026-08-27T00:00:00Z",
        load_calibrator_fn=lambda cohort_key, method: mismatched_record if method == "PLATT_TIME_SPLIT_V1" else None,
    )
    assert result.error is not None
    assert "MODEL_CALIBRATION_UNAVAILABLE" in result.error
    assert "does not match" in result.error
    assert result.row.probability_publishable is False


def test_gate_11de_phase_b_real_positive_path_produces_publishable_probability():
    # The main positive-path proof for the ratified PREDICTIVE_BOUNDS_V1
    # amendment: a real calibrator + a real historical calibration cohort
    # + a real scoring time together produce a genuinely publishable
    # Phase B row -- not just a failure path correctly blocking.
    cohort = _uniform_cohort()
    pitcher = _sample_pitcher()
    params = _synthetic_fitted_params()
    historical_rows = _synthetic_historical_rows()

    result = score_prop_end_to_end(
        event_id="e1", event_start_time="2026-08-28T00:00:00Z", sport="MLB",
        stat_type="strikeouts", line=4.5, direction="MORE", source_snapshot_id="snap-b-positive",
        cohort=cohort, pitcher=pitcher, regime_params=params,
        resample_fn=_synthetic_resampler, n_eff=16, seed=7, candidate_direction="OVER",
        settled_n_in_cohort=250, parent_cohort="MLB_SP_RH_2026",
        scored_at="2026-08-27T00:00:00Z", money_lane_status="RESOLVED",
        load_calibrator_fn=lambda cohort_key, method: _fake_platt_record() if method == "PLATT_TIME_SPLIT_V1" else None,
        load_historical_rows_fn=lambda cohort_key, method: historical_rows,
    )

    assert result.error is None
    row = result.row
    assert row.probability_publishable is True
    assert row.calibration_status == "PLATT_TIME_SPLIT_V1"
    assert row.independent_model_probability is not None
    assert row.calibrated_probability is not None
    assert 0 < row.calibrated_probability_lower_bound <= row.calibrated_probability <= row.calibrated_probability_upper_bound < 1
    # Step 3d live-validation prep: these were silently left None even for
    # a real Phase B row until this fix -- required for the live endpoint
    # gate's output checklist (calibration_version, bounds_method_version, etc).
    assert row.calibration_version == "v1"
    assert row.calibration_training_n == 300
    assert row.calibration_parent_cohort == "MLB_SP_RH_2026"
    assert row.bounds_method_version == PREDICTIVE_BOUNDS_METHOD_VERSION
    assert row.model_timestamp == "2026-08-27T00:00:00Z"


def test_gate_11e_phase_c_eligibility_requests_isotonic_not_platt():
    cohort = _uniform_cohort()
    pitcher = _sample_pitcher()
    params = _synthetic_fitted_params()

    requested_methods = []

    def _spy_loader(cohort_key, method):
        requested_methods.append(method)
        return None

    result = score_prop_end_to_end(
        event_id="e1", event_start_time="2026-08-27T00:00:00Z", sport="MLB",
        stat_type="strikeouts", line=4.5, direction="MORE", source_snapshot_id="snap-c",
        cohort=cohort, pitcher=pitcher, regime_params=params,
        resample_fn=_synthetic_resampler, n_eff=16, seed=7, candidate_direction="OVER",
        settled_n_in_cohort=600, parent_cohort="MLB_SP_RH_2026",
        money_lane_status="RESOLVED",
        load_calibrator_fn=_spy_loader,
    )
    assert requested_methods == ["ISOTONIC_V1"]
    assert result.row.probability_publishable is True  # fell back to Phase A


def test_gate_11f_confirmed_opener_signal_blocks_publication():
    from regime_model import CurrentGameSignal

    cohort = _uniform_cohort()
    pitcher = _sample_pitcher()
    params = _synthetic_fitted_params()

    result = score_prop_end_to_end(
        event_id="e1", event_start_time="2026-08-27T00:00:00Z", sport="MLB",
        stat_type="strikeouts", line=4.5, direction="MORE", source_snapshot_id="snap-opener",
        cohort=cohort, pitcher=pitcher, regime_params=params,
        resample_fn=_synthetic_resampler, n_eff=16, seed=7, candidate_direction="OVER",
        current_game_signal=CurrentGameSignal(confirmed_opener=True),
    )
    assert result.row.probability_publishable is False
    assert "BLOCK" in [a.value for a in result.signal_actions]


def test_gate_11g_current_game_signal_widens_uncertainty_without_blocking():
    from regime_model import CurrentGameSignal, SignalAction as _SA

    cohort = _uniform_cohort()
    pitcher = _sample_pitcher()
    params = _synthetic_fitted_params()

    result = score_prop_end_to_end(
        event_id="e1", event_start_time="2026-08-27T00:00:00Z", sport="MLB",
        stat_type="strikeouts", line=4.5, direction="MORE", source_snapshot_id="snap-injury",
        cohort=cohort, pitcher=pitcher, regime_params=params,
        resample_fn=_synthetic_resampler, n_eff=16, seed=7, candidate_direction="OVER",
        current_game_signal=CurrentGameSignal(injury_flag=True),
        money_lane_status="RESOLVED",
    )
    assert result.error is None
    assert result.row.probability_publishable is True
    assert _SA.WIDEN_UNCERTAINTY in result.signal_actions
    assert _SA.BLOCK not in result.signal_actions


# --- Gate 11h-k: the actual /score-prop HTTP endpoint [Step 3d review fix]
# Step 3d review: "Gate 11 still does not close the endpoint gap it was
# introduced to close. The new test calls score_prop_end_to_end()
# directly. It never calls /score-prop. api.py still returns 501 after
# the capability flag becomes AVAILABLE... A true final Gate 11 should
# hit the actual endpoint/service path with a known fitted fixture or
# staging model and receive a persisted, publishable ledger result."
#
# These tests hit the real FastAPI route via TestClient. The synthetic
# fitted-params provider and fake persist function are registered only
# for the duration of each test (monkeypatch auto-restores the module
# globals afterward) — production ships with GOVERNED_PROBABILITY_
# CAPABILITY = "UNAVAILABLE" and no provider registered, so /score-prop
# keeps returning 409/501 by default, exactly as before.

client = TestClient(api.app)


def test_gate_11h_score_prop_endpoint_409s_while_capability_unavailable():
    resp = client.post("/score-prop", json={
        "event_id": "e1", "event_start_time": "2026-08-27T00:00:00Z", "sport": "MLB",
        "stat_type": "strikeouts", "line": 4.5, "direction": "OVER",
        "source_snapshot_id": "snap1",
    })
    assert resp.status_code == 409
    assert resp.json()["detail"]["governed_probability_capability"] == "UNAVAILABLE"


def test_gate_11i_score_prop_endpoint_501s_without_fitted_provider(monkeypatch):
    monkeypatch.setattr(api, "GOVERNED_PROBABILITY_CAPABILITY", "AVAILABLE")
    resp = client.post("/score-prop", json={
        "event_id": "e1", "event_start_time": "2026-08-27T00:00:00Z", "sport": "MLB",
        "stat_type": "strikeouts", "line": 4.5, "direction": "OVER",
        "source_snapshot_id": "snap1",
    })
    assert resp.status_code == 501


def test_gate_11j_score_prop_endpoint_produces_persisted_publishable_result(monkeypatch):
    monkeypatch.setattr(api, "GOVERNED_PROBABILITY_CAPABILITY", "AVAILABLE")

    def _staging_provider(sport, stat_type):
        return api.FittedParamsBundle(
            cohort=_uniform_cohort(), pitcher=_sample_pitcher(),
            regime_params=_synthetic_fitted_params(), resample_fn=_synthetic_resampler,
            n_eff=16,
        )
    monkeypatch.setattr(api, "_fitted_params_provider", _staging_provider)

    persisted_rows = []

    def _fake_persist(row):
        persisted_rows.append(row)
        return {"prediction_id": "fake-uuid", **{k: v for k, v in vars(row).items()}}
    monkeypatch.setattr(api, "_persist_fn", _fake_persist)

    resp = client.post("/score-prop", json={
        "event_id": "synthetic_evt", "event_start_time": "2026-08-27T00:00:00Z", "sport": "MLB",
        "stat_type": "strikeouts", "line": 4.5, "direction": "MORE",
        "source_snapshot_id": "snap-endpoint", "money_lane_status": "RESOLVED",
    })

    assert resp.status_code == 200
    body = resp.json()
    assert body["probability_publishable"] is True
    assert body["calibrated_probability"] is not None
    assert 0 < body["calibrated_probability_lower_bound"] <= body["calibrated_probability"] <= body["calibrated_probability_upper_bound"] < 1
    assert len(persisted_rows) == 1  # actually reached the persistence seam


def test_gate_11k_score_prop_endpoint_returns_422_for_unpublishable_result(monkeypatch):
    monkeypatch.setattr(api, "GOVERNED_PROBABILITY_CAPABILITY", "AVAILABLE")

    def _broken_provider(sport, stat_type):
        params = _synthetic_fitted_params()
        del params[PrimaryRegime.GAME_DISRUPTION]
        return api.FittedParamsBundle(
            cohort=_uniform_cohort(), pitcher=_sample_pitcher(),
            regime_params=params, resample_fn=_synthetic_resampler, n_eff=16,
        )
    monkeypatch.setattr(api, "_fitted_params_provider", _broken_provider)

    resp = client.post("/score-prop", json={
        "event_id": "broken_evt", "event_start_time": "2026-08-27T00:00:00Z", "sport": "MLB",
        "stat_type": "strikeouts", "line": 4.5, "direction": "MORE",
        "source_snapshot_id": "snap-broken",
    })
    assert resp.status_code == 422
    assert "simulation_failed" in resp.json()["detail"]["error"]
    assert resp.json()["detail"]["probability_publishable"] is False
