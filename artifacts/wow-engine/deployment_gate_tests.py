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
    PHASE_B_MIN_N, PHASE_C_MIN_N, PlattCoefficients, PlattFitMetrics,
)
from ledger import PredictionRow, determine_publishability
import ledger
from calibrator_store import (
    _serialize_isotonic_model, _deserialize_isotonic_model, platt_coefficients_from_record,
    save_platt_calibrator, save_isotonic_calibrator,
)
import calibrator_store
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
        source_snapshot_id="snap1", model_timestamp="2026-08-26T00:00:00Z",
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
        source_snapshot_id="snap1", model_timestamp="2026-08-26T00:00:00Z",
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
        scored_at="2026-08-27T00:00:00Z", money_lane_status="RESOLVED",
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
        scored_at="2026-08-27T00:00:00Z", money_lane_status="RESOLVED",
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
        scored_at="2026-08-27T00:00:00Z", money_lane_status="RESOLVED",
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


# --- 3D-BLOCKER-01/02: Step 3d re-review of cb9060b, CHANGES_REQUIRED ----
# ChatGPT's Step 3d re-review found two real implementation blockers the
# static schema/validator review did not surface -- both invisible to any
# test that injects a fake load_historical_rows_fn/load_calibrator_fn,
# since the actual defects lived inside calibrator_store.py's real
# Supabase query construction:
#
# 3D-BLOCKER-01: load_historical_calibration_rows() required a historical
# prediction to already carry the target Phase B/C calibration_method, so
# a cohort's Phase A observations could never become that cohort's first
# Phase B training data. It also used event_start_time as the "was this
# available" timestamp instead of the outcome's settlement_timestamp,
# which could leak a not-yet-settled result into calibration for a
# candidate scored before that result was actually known.
#
# 3D-BLOCKER-02: model_timestamp/scored_at was optional, and
# determine_publishability() never checked for it -- a governed
# probability could publish with no auditable scoring timestamp.
#
# _FakeSupabaseClient below is a minimal in-memory double for the
# supabase-py chain calibrator_store.py/ledger.py actually call
# (.table().select().eq().in_().insert().update().limit().execute()), so
# these tests exercise the REAL load_historical_calibration_rows() query
# logic -- not an injected substitute -- without a live Supabase project
# (Gates 1/8 still need that; see README).

from types import SimpleNamespace


class _FakeTable:
    def __init__(self, rows: list[dict]):
        self._rows = rows  # same list object as the backing table -- inserts/updates persist into it
        self._filtered = list(rows)
        self._cols: list[str] | None = None
        self._pending_update: dict | None = None

    def select(self, cols: str):
        self._cols = None if cols.strip() == "*" else [c.strip() for c in cols.split(",")]
        return self

    def eq(self, col: str, val):
        self._filtered = [r for r in self._filtered if r.get(col) == val]
        return self

    def in_(self, col: str, vals):
        vals = set(vals)
        self._filtered = [r for r in self._filtered if r.get(col) in vals]
        return self

    def limit(self, n: int):
        self._filtered = self._filtered[:n]
        return self

    def insert(self, payload: dict):
        self._rows.append(dict(payload))
        self._filtered = [payload]
        return self

    def update(self, payload: dict):
        self._pending_update = payload
        return self

    def execute(self):
        if self._pending_update is not None:
            for r in self._filtered:
                r.update(self._pending_update)
            return SimpleNamespace(data=list(self._filtered))
        if self._cols is None:
            data = list(self._filtered)
        else:
            data = [{c: r.get(c) for c in self._cols} for r in self._filtered]
        return SimpleNamespace(data=data)


class _FakeSupabaseClient:
    """In-memory double for the subset of the supabase-py client
    calibrator_store.py/ledger.py actually call. Backed by plain dict
    tables so tests can seed exact historical-row fixtures and assert on
    the real query/filter logic, not a mock's recorded calls."""

    def __init__(self):
        self.tables: dict[str, list[dict]] = {
            "wow_predictions": [],
            "wow_outcomes": [],
            "wow_calibrators": [],
        }

    def table(self, name: str) -> _FakeTable:
        return _FakeTable(self.tables[name])


def _seed_prediction(client: _FakeSupabaseClient, prediction_id: str, **fields):
    row = {"prediction_id": prediction_id, "raw_model_probability": None,
           "calibration_parent_cohort": None, "calibration_method": None}
    row.update(fields)
    client.tables["wow_predictions"].append(row)


def _seed_outcome(client: _FakeSupabaseClient, prediction_id: str, hit, settlement_timestamp=None):
    client.tables["wow_outcomes"].append({
        "prediction_id": prediction_id, "hit": hit,
        "settlement_timestamp": settlement_timestamp,
    })


def test_3d_blocker_01a_phase_a_rows_supply_first_phase_b_historical_evidence(monkeypatch):
    # Requirements 1 & 2: Phase A rows belonging to a cohort can supply
    # historical evidence for the first Phase B candidate after a Platt
    # calibrator is promoted -- and no prior PLATT-tagged prediction is
    # required to produce that result. None of the seeded rows below
    # carry the Phase B method.
    from calibrator_store import load_historical_calibration_rows

    client = _FakeSupabaseClient()
    cohort = "MLB_SP_RH_2026"
    for i in range(3):
        pred_id = f"pred-{i}"
        _seed_prediction(
            client, pred_id,
            raw_model_probability=0.5 + i * 0.01,
            calibration_parent_cohort=cohort,
            calibration_method="CONSERVATIVE_EMPIRICAL_BAYES_SHRINKAGE_V1",  # Phase A, not Platt
        )
        _seed_outcome(client, pred_id, hit=(i % 2 == 0), settlement_timestamp=f"2026-0{i + 1}-01T00:00:00Z")

    monkeypatch.setattr(calibrator_store, "get_client", lambda: client)

    assert not any(r["calibration_method"] == "PLATT_TIME_SPLIT_V1" for r in client.tables["wow_predictions"])
    rows = load_historical_calibration_rows(cohort, "PLATT_TIME_SPLIT_V1")

    assert len(rows) == 3
    assert {r.outcome for r in rows} == {0, 1}


def test_3d_blocker_01b_late_settlement_and_missing_settlement_excluded(monkeypatch):
    # Requirements 3 & 4: a row that started before candidate_as_of but
    # settled after it must be excluded once as-of filtered (not just
    # because event_start_time alone would look eligible); a row with no
    # recorded settlement availability at all must fail closed.
    from calibrator_store import load_historical_calibration_rows

    client = _FakeSupabaseClient()
    cohort = "MLB_SP_RH_2026"
    candidate_as_of = "2026-08-27T00:00:00Z"

    _seed_prediction(client, "late-settle", raw_model_probability=0.55,
                      calibration_parent_cohort=cohort, calibration_method="PLATT_TIME_SPLIT_V1",
                      event_start_time="2026-08-01T00:00:00Z")
    _seed_outcome(client, "late-settle", hit=True, settlement_timestamp="2026-12-01T00:00:00Z")

    _seed_prediction(client, "no-settlement", raw_model_probability=0.60,
                      calibration_parent_cohort=cohort, calibration_method="PLATT_TIME_SPLIT_V1",
                      event_start_time="2026-06-01T00:00:00Z")
    _seed_outcome(client, "no-settlement", hit=True, settlement_timestamp=None)

    _seed_prediction(client, "clean", raw_model_probability=0.58,
                      calibration_parent_cohort=cohort, calibration_method="PLATT_TIME_SPLIT_V1",
                      event_start_time="2026-01-01T00:00:00Z")
    _seed_outcome(client, "clean", hit=False, settlement_timestamp="2026-01-01T03:00:00Z")

    monkeypatch.setattr(calibrator_store, "get_client", lambda: client)
    rows = load_historical_calibration_rows(cohort, "PLATT_TIME_SPLIT_V1")

    # no-settlement excluded by the loader itself (fail closed); late-settle
    # IS returned (it does have settlement data) but carries its real
    # settlement timestamp, not its earlier event_start_time.
    assert len(rows) == 2
    timestamps = {r.timestamp for r in rows}
    assert "2026-12-01T00:00:00Z" in timestamps
    assert "2026-08-01T00:00:00Z" not in timestamps
    assert "2026-06-01T00:00:00Z" not in timestamps

    eligible = [r for r in rows if r.timestamp < candidate_as_of]
    assert len(eligible) == 1  # only "clean" survives the as-of filter


def test_3d_blocker_01c_natural_phase_a_to_phase_b_lifecycle_without_synthetic_retagging(monkeypatch):
    # Requirement 5: the full natural Phase A -> promoted Platt -> first
    # Phase B lifecycle, using the REAL calibrator_store loader functions
    # (not injected fakes) for the final call, proves the cohort bootstrap
    # without ever synthetically retagging a historical row.
    #
    # This test is deliberately about the QUERY/cohort-lookup lifecycle,
    # not a claim that N=200 real historical rows exist in the fixture --
    # 20 is enough to prove the loader/bootstrap mechanics. The saved
    # calibrator's training_n (its own fit-time evidence count, a 3D-
    # BLOCKER-03 persistence-boundary field) is a SEPARATE number from how
    # many historical rows this fixture happens to seed for the bootstrap
    # step below -- a real calibrator's training_n reflects the window it
    # was actually fit over, which need not match what a later bootstrap
    # run finds still loadable for a given cohort. The N=200/500 threshold
    # itself (rejected below it, accepted at it) is covered by the
    # dedicated 3D-BLOCKER-03 boundary tests, not by this lifecycle test.
    from calibrator_store import (
        load_active_calibrator, load_historical_calibration_rows, save_platt_calibrator,
    )
    from calibration import PlattCoefficients, PlattFitMetrics

    client = _FakeSupabaseClient()
    monkeypatch.setattr(calibrator_store, "get_client", lambda: client)
    monkeypatch.setattr(ledger, "get_client", lambda: client)

    cohort = "MLB_SP_RH_2026"
    fitted = dict(cohort=_uniform_cohort(), pitcher=_sample_pitcher(), regime_params=_synthetic_fitted_params())

    for i in range(20):
        ts = f"2026-0{(i % 6) + 1}-0{(i % 9) + 1}T00:00:00Z"
        result = score_prop_end_to_end(
            event_id=f"hist-{i}", event_start_time=ts, sport="MLB", stat_type="strikeouts",
            line=4.5, direction="MORE", source_snapshot_id=f"snap-hist-{i}",
            resample_fn=_synthetic_resampler, n_eff=16, seed=i, candidate_direction="OVER",
            scored_at=ts, settled_n_in_cohort=0, parent_cohort=cohort,
            money_lane_status="RESOLVED", **fitted,
        )
        assert result.row.probability_publishable is True
        assert result.row.calibration_status == "PRECALIBRATION_SHRINKAGE"
        # The fix under test: even a Phase A row now carries its cohort
        # identity, so it can later become Phase B training evidence.
        assert result.row.calibration_parent_cohort == cohort

        persisted = ledger.insert_prediction(result.row)
        ledger.record_outcome(
            persisted["prediction_id"], hit=(i % 2 == 0), official_result="settled",
            settlement_timestamp=ts,
        )

    save_platt_calibrator(
        PlattCoefficients(a=0.1, b=1.1),
        PlattFitMetrics(brier=0.2, log_loss=0.6, ece=0.02, calibration_bias=0.0),
        parent_cohort=cohort, calibration_version="v1", training_n=200, activate=True,
    )

    # No historical row above was ever tagged with the Platt method --
    # confirm that before proving the real loader finds them anyway.
    assert not any(
        r["calibration_method"] == "PLATT_TIME_SPLIT_V1" for r in client.tables["wow_predictions"]
    )

    result = score_prop_end_to_end(
        event_id="first-phase-b", event_start_time="2026-08-28T00:00:00Z", sport="MLB",
        stat_type="strikeouts", line=4.5, direction="MORE", source_snapshot_id="snap-first-phase-b",
        resample_fn=_synthetic_resampler, n_eff=16, seed=99, candidate_direction="OVER",
        scored_at="2026-08-27T00:00:00Z", settled_n_in_cohort=200, parent_cohort=cohort,
        money_lane_status="RESOLVED",
        load_calibrator_fn=load_active_calibrator,
        load_historical_rows_fn=load_historical_calibration_rows,
        **fitted,
    )

    assert result.error is None, result.error
    row = result.row
    assert row.probability_publishable is True
    assert row.calibration_status == "PLATT_TIME_SPLIT_V1"
    assert row.calibrated_probability is not None
    assert 0 < row.calibrated_probability_lower_bound <= row.calibrated_probability <= row.calibrated_probability_upper_bound < 1


def test_3d_blocker_02_missing_model_timestamp_blocks_publication():
    row = PredictionRow(
        event_id="e1", event_start_time="2026-08-27T00:00:00Z", sport="MLB",
        market_type="engine", stat_type="strikeouts", line=4.5, direction="MORE",
        source_snapshot_id="snap1", model_timestamp=None,
        raw_model_probability=0.62,
        regime_probability_sum=1.0, simulation_draws=MIN_SIMULATION_DRAWS,
        calibrated_probability=0.7, calibrated_probability_lower_bound=0.6,
        calibrated_probability_upper_bound=0.8,
        calibration_status="PLATT_TIME_SPLIT_V1",
    )
    row = determine_publishability(row)
    assert row.probability_publishable is False
    assert any("model_timestamp" in g for g in row.data_gaps)


def test_3d_blocker_02b_invalid_model_timestamp_blocks_publication():
    row = PredictionRow(
        event_id="e1", event_start_time="2026-08-27T00:00:00Z", sport="MLB",
        market_type="engine", stat_type="strikeouts", line=4.5, direction="MORE",
        source_snapshot_id="snap1", model_timestamp="not-a-timestamp",
        raw_model_probability=0.62,
        regime_probability_sum=1.0, simulation_draws=MIN_SIMULATION_DRAWS,
        calibrated_probability=0.7, calibrated_probability_lower_bound=0.6,
        calibrated_probability_upper_bound=0.8,
        calibration_status="PLATT_TIME_SPLIT_V1",
    )
    row = determine_publishability(row)
    assert row.probability_publishable is False
    assert any("model_timestamp" in g for g in row.data_gaps)


def test_3d_blocker_02c_valid_model_timestamp_does_not_block_publication():
    row = PredictionRow(
        event_id="e1", event_start_time="2026-08-27T00:00:00Z", sport="MLB",
        market_type="engine", stat_type="strikeouts", line=4.5, direction="MORE",
        source_snapshot_id="snap1", model_timestamp="2026-08-26T00:00:00Z",
        raw_model_probability=0.62,
        regime_probability_sum=1.0, simulation_draws=MIN_SIMULATION_DRAWS,
        calibrated_probability=0.7, calibrated_probability_lower_bound=0.6,
        calibrated_probability_upper_bound=0.8,
        calibration_status="PLATT_TIME_SPLIT_V1",
    )
    row = determine_publishability(row)
    assert row.probability_publishable is True
    assert not any("model_timestamp" in g for g in row.data_gaps)


def test_3d_blocker_02d_score_prop_request_cannot_set_scored_at():
    # Ordinary HTTP callers must not be able to supply/backdate the
    # governed scoring timestamp -- it is not a field on the request model.
    assert "scored_at" not in api.ScorePropRequest.model_fields


def test_3d_blocker_02e_score_prop_endpoint_persists_server_generated_timestamp(monkeypatch):
    monkeypatch.setattr(api, "GOVERNED_PROBABILITY_CAPABILITY", "AVAILABLE")

    def _staging_provider(sport, stat_type):
        return api.FittedParamsBundle(
            cohort=_uniform_cohort(), pitcher=_sample_pitcher(),
            regime_params=_synthetic_fitted_params(), resample_fn=_synthetic_resampler, n_eff=16,
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
        "source_snapshot_id": "snap-timestamp", "money_lane_status": "RESOLVED",
        "scored_at": "2020-01-01T00:00:00Z",  # attempted backdate -- must be ignored (unknown field)
    })

    assert resp.status_code == 200
    body = resp.json()
    assert body["model_timestamp"] != "2020-01-01T00:00:00Z"
    assert body["model_timestamp"] is not None
    assert len(persisted_rows) == 1
    assert persisted_rows[0].model_timestamp == body["model_timestamp"]


# --- 3D-BLOCKER-03: calibrator training evidence can bypass phase minimums
# ChatGPT's Step 3d re-review found that while phase_b_platt()/
# phase_c_fit_isotonic() already reject fitting on too few observations,
# nothing enforced the same PHASE_B_MIN_N/PHASE_C_MIN_N invariant on the
# PERSISTED calibrator artifact's own training_n -- at either the write
# boundary (save_platt_calibrator/save_isotonic_calibrator accepted any
# positive value) or the read/use boundary (score_prop_end_to_end only
# checked the loaded record's cohort/method, never its training_n). The
# original test_3d_blocker_01c demonstrated exactly this bypass: a
# training_n=20 Platt calibrator, treated as valid Phase B evidence.

_METRICS = PlattFitMetrics(brier=0.2, log_loss=0.6, ece=0.02, calibration_bias=0.0)


def _fake_isotonic_record(parent_cohort="MLB_SP_RH_2026", calibration_version="v1", training_n=600, artifact_b64=None):
    return {
        "parent_cohort": parent_cohort, "calibration_method": "ISOTONIC_V1",
        "calibration_version": calibration_version, "training_n": training_n,
        "isotonic_artifact_b64": artifact_b64,
    }


def _fit_toy_isotonic_model():
    from sklearn.isotonic import IsotonicRegression
    rng = np.random.default_rng(0)
    raw = rng.uniform(0.3, 0.7, size=50)
    y = (rng.uniform(0, 1, size=50) < raw).astype(int)
    model = IsotonicRegression(out_of_bounds="clip", y_min=1e-9, y_max=1 - 1e-9)
    model.fit(raw, y)
    return model


# Requirement 1
def test_3d_blocker_03_1_platt_persistence_rejects_below_minimum():
    with pytest.raises(ValueError, match="training_n"):
        save_platt_calibrator(
            PlattCoefficients(a=0.1, b=1.1), _METRICS,
            parent_cohort="c", calibration_version="v1", training_n=PHASE_B_MIN_N - 1, activate=False,
        )


# Requirement 2 -- validation runs before the model is even touched, so no
# real fitted isotonic model is needed to prove the rejection.
def test_3d_blocker_03_2_isotonic_persistence_rejects_below_minimum():
    with pytest.raises(ValueError, match="training_n"):
        save_isotonic_calibrator(
            model=None, metrics=_METRICS,
            parent_cohort="c", calibration_version="v1", training_n=PHASE_C_MIN_N - 1, activate=False,
        )


# Requirement 3
def test_3d_blocker_03_3_runtime_rejects_underevidenced_platt_artifact():
    underevidenced = _fake_platt_record(training_n=PHASE_B_MIN_N - 1)
    result = score_prop_end_to_end(
        event_id="e1", event_start_time="2026-08-27T00:00:00Z", sport="MLB",
        stat_type="strikeouts", line=4.5, direction="MORE", source_snapshot_id="snap-under-platt",
        cohort=_uniform_cohort(), pitcher=_sample_pitcher(), regime_params=_synthetic_fitted_params(),
        resample_fn=_synthetic_resampler, n_eff=16, seed=7, candidate_direction="OVER",
        settled_n_in_cohort=PHASE_B_MIN_N, parent_cohort="MLB_SP_RH_2026", scored_at="2026-08-27T00:00:00Z",
        load_calibrator_fn=lambda cohort_key, method: underevidenced if method == "PLATT_TIME_SPLIT_V1" else None,
    )
    assert result.error is not None
    assert "MODEL_CALIBRATION_UNAVAILABLE" in result.error
    assert "training_n" in result.error
    assert result.row.probability_publishable is False


# Requirement 4
def test_3d_blocker_03_4_runtime_rejects_underevidenced_isotonic_artifact():
    underevidenced = _fake_isotonic_record(training_n=PHASE_C_MIN_N - 1)
    result = score_prop_end_to_end(
        event_id="e1", event_start_time="2026-08-27T00:00:00Z", sport="MLB",
        stat_type="strikeouts", line=4.5, direction="MORE", source_snapshot_id="snap-under-iso",
        cohort=_uniform_cohort(), pitcher=_sample_pitcher(), regime_params=_synthetic_fitted_params(),
        resample_fn=_synthetic_resampler, n_eff=16, seed=7, candidate_direction="OVER",
        settled_n_in_cohort=PHASE_C_MIN_N, parent_cohort="MLB_SP_RH_2026", scored_at="2026-08-27T00:00:00Z",
        load_calibrator_fn=lambda cohort_key, method: underevidenced if method == "ISOTONIC_V1" else None,
    )
    assert result.error is not None
    assert "MODEL_CALIBRATION_UNAVAILABLE" in result.error
    assert "training_n" in result.error
    assert result.row.probability_publishable is False


# Requirement 5 -- missing/malformed training_n rejected at the persistence
# boundary. 200.0 is deliberately rejected, not canonicalized to int: this
# implementation defines no float->int coercion rule.
@pytest.mark.parametrize("bad_value", [None, "200", 200.0, True])
def test_3d_blocker_03_5_malformed_training_n_rejected(bad_value):
    with pytest.raises(ValueError):
        save_platt_calibrator(
            PlattCoefficients(a=0.1, b=1.1), _METRICS,
            parent_cohort="c", calibration_version="v1", training_n=bad_value, activate=False,
        )


# Requirement 6 -- Platt boundary: training_n == PHASE_B_MIN_N proceeds,
# at both the persistence and consumption boundaries.
def test_3d_blocker_03_6_platt_boundary_at_exactly_minimum_accepted(monkeypatch):
    client = _FakeSupabaseClient()
    monkeypatch.setattr(calibrator_store, "get_client", lambda: client)

    saved = save_platt_calibrator(
        PlattCoefficients(a=0.1, b=1.1), _METRICS,
        parent_cohort="c", calibration_version="v1", training_n=PHASE_B_MIN_N, activate=True,
    )
    assert saved["training_n"] == PHASE_B_MIN_N

    boundary_record = _fake_platt_record(training_n=PHASE_B_MIN_N)
    historical_rows = _synthetic_historical_rows()
    result = score_prop_end_to_end(
        event_id="e1", event_start_time="2026-08-28T00:00:00Z", sport="MLB",
        stat_type="strikeouts", line=4.5, direction="MORE", source_snapshot_id="snap-boundary-platt",
        cohort=_uniform_cohort(), pitcher=_sample_pitcher(), regime_params=_synthetic_fitted_params(),
        resample_fn=_synthetic_resampler, n_eff=16, seed=7, candidate_direction="OVER",
        settled_n_in_cohort=250, parent_cohort="MLB_SP_RH_2026",
        scored_at="2026-08-27T00:00:00Z", money_lane_status="RESOLVED",
        load_calibrator_fn=lambda cohort_key, method: boundary_record if method == "PLATT_TIME_SPLIT_V1" else None,
        load_historical_rows_fn=lambda cohort_key, method: historical_rows,
    )
    assert result.error is None, result.error
    assert result.row.probability_publishable is True


# Requirement 7 -- Isotonic boundary: training_n == PHASE_C_MIN_N proceeds,
# at both the persistence and consumption boundaries.
def test_3d_blocker_03_7_isotonic_boundary_at_exactly_minimum_accepted(monkeypatch):
    client = _FakeSupabaseClient()
    monkeypatch.setattr(calibrator_store, "get_client", lambda: client)
    model = _fit_toy_isotonic_model()

    saved = save_isotonic_calibrator(
        model, _METRICS, parent_cohort="c", calibration_version="v1",
        training_n=PHASE_C_MIN_N, activate=True,
    )
    assert saved["training_n"] == PHASE_C_MIN_N

    boundary_record = _fake_isotonic_record(training_n=PHASE_C_MIN_N, artifact_b64=_serialize_isotonic_model(model))
    historical_rows = _synthetic_historical_rows()
    result = score_prop_end_to_end(
        event_id="e1", event_start_time="2026-08-28T00:00:00Z", sport="MLB",
        stat_type="strikeouts", line=4.5, direction="MORE", source_snapshot_id="snap-boundary-iso",
        cohort=_uniform_cohort(), pitcher=_sample_pitcher(), regime_params=_synthetic_fitted_params(),
        resample_fn=_synthetic_resampler, n_eff=16, seed=7, candidate_direction="OVER",
        settled_n_in_cohort=600, parent_cohort="MLB_SP_RH_2026",
        scored_at="2026-08-27T00:00:00Z", money_lane_status="RESOLVED",
        load_calibrator_fn=lambda cohort_key, method: boundary_record if method == "ISOTONIC_V1" else None,
        load_historical_rows_fn=lambda cohort_key, method: historical_rows,
    )
    assert result.error is None, result.error
    assert result.row.probability_publishable is True


# --- Timezone-awareness tightening (separate Step 3d correction) --------
# A governed timestamp must represent an absolute instant, not an
# ambiguous local wall-clock value. Python's datetime.fromisoformat()
# happily parses "2026-08-27T00:00:00" (no Z/offset) as a valid but
# timezone-naive datetime -- parsing success alone was insufficient.

def test_timezone_valid_iso_timestamp_examples():
    from ledger import _valid_iso_timestamp
    for good in ("2026-08-27T00:00:00Z", "2026-08-27T00:00:00+00:00", "2026-08-26T19:00:00-05:00"):
        assert _valid_iso_timestamp(good) is True, good
    for bad in ("2026-08-27T00:00:00", "2026-08-27", "not-a-timestamp", "", None):
        assert _valid_iso_timestamp(bad) is False, bad


def test_timezone_parse_ts_requires_aware_examples():
    from calibration import _parse_ts
    for good in ("2026-08-27T00:00:00Z", "2026-08-27T00:00:00+00:00", "2026-08-26T19:00:00-05:00"):
        parsed = _parse_ts(good)
        assert parsed.utcoffset() is not None
    for bad in ("2026-08-27T00:00:00", "2026-08-27", "not-a-timestamp"):
        with pytest.raises(ValueError):
            _parse_ts(bad)


def test_timezone_naive_candidate_as_of_rejected_cleanly():
    # Aware vs aware -> valid comparison is already exercised by every
    # other test_gate_09g*/11de test using "Z"-suffixed timestamps; this
    # section covers the naive/malformed side specifically.
    rows = _synthetic_historical_rows()
    with pytest.raises(ModelCalibrationUnavailableError, match="timezone-aware"):
        compute_predictive_bounds(
            method="PLATT_TIME_SPLIT_V1", historical_rows=rows,
            candidate_as_of="2026-08-27T00:00:00",  # naive -- no Z/offset
            candidate_raw_probability_sampler=lambda rng: 0.5,
            full_data_calibrated_probability=0.5, rng_seed=1,
        )


def test_timezone_naive_historical_row_excluded_not_whole_run_blocked():
    rows = _synthetic_historical_rows()  # all aware, all genuinely eligible
    naive_row = HistoricalCalibrationRow(raw_probability=0.6, outcome=1, timestamp="2020-01-01T00:00:00")
    bounds = compute_predictive_bounds(
        method="PLATT_TIME_SPLIT_V1", historical_rows=rows + [naive_row],
        candidate_as_of="2026-08-27T00:00:00Z",
        candidate_raw_probability_sampler=lambda rng: float(rng.uniform(0.4, 0.6)),
        full_data_calibrated_probability=0.55, rng_seed=7,
    )
    # The naive row is excluded (fails closed), not a crash and not treated
    # as eligible -- the run still succeeds on the remaining valid rows.
    assert bounds.realizations_used >= 2000


def test_timezone_all_naive_historical_rows_fails_closed():
    naive_rows = [HistoricalCalibrationRow(raw_probability=0.5, outcome=1, timestamp="2020-01-01T00:00:00")]
    with pytest.raises(ModelCalibrationUnavailableError, match="no historical calibration rows"):
        compute_predictive_bounds(
            method="PLATT_TIME_SPLIT_V1", historical_rows=naive_rows,
            candidate_as_of="2026-08-27T00:00:00Z",
            candidate_raw_probability_sampler=lambda rng: 0.5,
            full_data_calibrated_probability=0.5, rng_seed=1,
        )


def test_timezone_mixed_naive_aware_produces_controlled_failure_not_typeerror():
    rows = _synthetic_historical_rows()
    with pytest.raises(ModelCalibrationUnavailableError):
        compute_predictive_bounds(
            method="PLATT_TIME_SPLIT_V1", historical_rows=rows,
            candidate_as_of="2026-08-27T00:00:00",  # naive against otherwise-aware rows
            candidate_raw_probability_sampler=lambda rng: 0.5,
            full_data_calibrated_probability=0.5, rng_seed=1,
        )


def test_timezone_phase_b_platt_rejects_naive_timestamp_cleanly():
    # Same naive/aware ambiguity, inspected in the walk-forward path too.
    rng = np.random.default_rng(0)
    n = 300
    raw = rng.uniform(0.3, 0.7, size=n)
    y = (rng.uniform(0, 1, size=n) < raw).astype(int)
    folds = np.sort(rng.integers(0, 6, size=n))
    timestamps = _sequential_timestamps(n)
    timestamps[0] = "2026-01-01T00:00:00"  # naive -- no Z/offset
    with pytest.raises(ValueError):
        phase_b_platt(raw, y, folds, timestamps)
