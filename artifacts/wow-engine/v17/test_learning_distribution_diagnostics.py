import math
import pytest
from learning_distribution_diagnostics import (
    binary_murphy_diagnostics, discrete_prop_distribution_audit,
)


def prop_pair(pid="p", pmf=None, outcome=1):
    identity = dict(sport="NHL", official_event_id="nhl-game-1", player_id="P1",
                    stat_type="SHOTS", period="FULL", exact_offer=1.5,
                    direction="MORE", settlement_basis="REGULATION_PLUS_OT")
    pre = dict(identity, prediction_id=pid, event_start_utc="2026-11-23T02:00:00Z",
               model_timestamp_utc="2026-11-23T01:00:00Z",
               latest_material_update_at_utc="2026-11-23T00:50:00Z",
               immutable_pregame=True, model_artifact_digest="real-hash-required",
               source_snapshot_id="immutable-source", controlling_specialist="NHL_SHOTS",
               predictive_count_pmf=pmf if pmf is not None else [0.1, 0.2, 0.7])
    post = dict(identity, source_type="INDEPENDENT_OFFICIAL", settlement_source_id="official",
                settled_at_utc="2026-11-23T04:00:00Z", official_result="WIN" if outcome > 1 else "LOSS",
                actual_count=outcome)
    return pre, post


def test_murphy_perfect_constant_bin_reconstructs_brier():
    r = binary_murphy_diagnostics([(0.5, 1), (0.5, 0)], min_n=2)
    assert r['brier'] == pytest.approx(0.25)
    assert r['murphy_binned_reconstruction'] == pytest.approx(0.25)
    assert r['murphy_binning_residual'] == pytest.approx(0)
    assert r['automatic_parameter_mutation'] is False


def test_murphy_preserves_binning_residual_not_fake_drift():
    r = binary_murphy_diagnostics([(0.51, 1), (0.59, 0)], bins=10, min_n=1)
    assert r['murphy_binning_residual'] != 0
    assert r['binning_residual_is_not_model_drift'] is True


def test_murphy_rejects_nonfinite_bool_and_bad_outcome():
    for pair in [((True, 1),), ((math.nan, 1),), ((0.6, True),), ((0.6, 2),)]:
        with pytest.raises(ValueError):
            binary_murphy_diagnostics(pair)


def test_discrete_pit_and_crps_reconcile():
    r = discrete_prop_distribution_audit([prop_pair()], min_n=1, bins=10)
    assert r['status'] == 'DIAGNOSTIC_ONLY'
    assert r['rows_graded'] == 1 and r['reconciled']
    assert sum(r['nonrandomized_pit_bin_proportions']) == pytest.approx(1)
    assert r['mean_discrete_crps'] == pytest.approx(0.1**2 + (0.3 - 1)**2)
    assert r['can_execute'] is False


def test_started_or_stale_joins_do_not_grade():
    pre, post = prop_pair()
    pre['model_timestamp_utc'] = '2026-11-23T03:00:00Z'
    r = discrete_prop_distribution_audit([(pre, post)], min_n=1)
    assert r['rows_graded'] == 0
    assert r['blockers'][0]['reason'] == 'PREGAME_OR_SETTLEMENT_CHRONOLOGY_INVALID'


def test_material_update_after_model_blocks():
    pre, post = prop_pair()
    pre['latest_material_update_at_utc'] = '2026-11-23T01:30:00Z'
    r = discrete_prop_distribution_audit([(pre, post)], min_n=1)
    assert r['rows_graded'] == 0


def test_different_exact_line_direction_no_substitute():
    pre, post = prop_pair()
    post['exact_offer'] = '2+'
    r = discrete_prop_distribution_audit([(pre, post)], min_n=1)
    assert r['blockers'][0]['reason'] == 'EXACT_IDENTITY_OR_FROZEN_MODEL_EVIDENCE_MISSING'


def test_post_event_fabrication_not_gradeable():
    pre, post = prop_pair()
    pre['immutable_pregame'] = False
    r = discrete_prop_distribution_audit([(pre, post)], min_n=1)
    assert r['rows_graded'] == 0


def test_duplicate_receipt_counts_once_with_blocker():
    pair = prop_pair()
    r = discrete_prop_distribution_audit([pair, pair], min_n=1)
    assert r['rows_graded'] == 1 and r['rows_blocked'] == 1 and r['reconciled']


def test_no_available_distribution_is_not_zero_crps():
    pre, post = prop_pair()
    del pre['predictive_count_pmf']
    r = discrete_prop_distribution_audit([(pre, post)])
    assert r['mean_discrete_crps'] is None and r['rows_graded'] == 0


def test_official_result_contradiction_blocks():
    pre, post = prop_pair(outcome=2)
    post['official_result'] = 'LOSS'
    r = discrete_prop_distribution_audit([(pre, post)], min_n=1)
    assert r['blockers'][0]['reason'] == 'RESULT_CONTRADICTS_EXACT_LINE'


def test_push_not_counted_as_loss():
    pre, post = prop_pair()
    post['official_result'] = 'PUSH'
    r = discrete_prop_distribution_audit([(pre, post)])
    assert r['rows_graded'] == 0 and r['mean_discrete_crps'] is None


def test_zero_mass_outcome_blocks_histogram_without_leaking_crps():
    pre, post = prop_pair(outcome=3)
    r = discrete_prop_distribution_audit([(pre, post)])
    assert r['rows_graded'] == 1
    assert r['rows_pit_graded'] == 0
    assert r['pit_unavailable_prediction_ids'] == ['p']
    assert r['mean_discrete_crps'] is not None


def test_missing_utc_timezone_not_accepted():
    pre, post = prop_pair()
    pre['event_start_utc'] = '2026-11-23T02:00:00'
    r = discrete_prop_distribution_audit([(pre, post)])
    assert r['rows_graded'] == 0
