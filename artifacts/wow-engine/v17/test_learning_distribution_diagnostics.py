import math
import pytest
from v17.learning_distribution_diagnostics import (
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


def test_lowercase_direction_is_not_accepted_as_canonical():
    pre, post = prop_pair()
    pre['direction'] = post['direction'] = 'more'
    r = discrete_prop_distribution_audit([(pre, post)])
    assert r['rows_graded'] == 0


def test_unbounded_actual_count_cannot_create_unbounded_crps_loop():
    pre, post = prop_pair()
    post['actual_count'] = 10**9
    r = discrete_prop_distribution_audit([(pre, post)])
    assert r['rows_graded'] == 0
    assert r['blockers'][0]['reason'] == 'ACTUAL_COUNT_INVALID'

from v17.learning_distribution_diagnostics import multiclass_event_distribution_audit


def soccer_pair(pid="m1", result="DRAW", dist=None):
    ident = dict(sport="SOCCER", official_event_id="soccer-1", market_family="SOCCER_1X2",
                 period="FULL_TIME", settlement_basis="REGULATION")
    before = dict(ident, prediction_id=pid, model_timestamp_utc="2026-11-23T01:00:00Z",
                  latest_material_update_at_utc="2026-11-23T00:20:00Z",
                  event_start_utc="2026-11-23T03:00:00Z", immutable_pregame=True,
                  model_artifact_digest="artifact", source_snapshot_id="snapshot",
                  controlling_specialist="SOCCER_MODEL",
                  outcome_probabilities=dist if dist is not None else
                      {"HOME": 0.4, "DRAW": 0.2, "AWAY": 0.4})
    after = dict(ident, settlement_outcome=result, settled_at_utc="2026-11-23T06:00:00Z",
                 source_type="INDEPENDENT_OFFICIAL", settlement_source_id="official")
    return before, after


def test_soccer_three_way_draw_graded_as_one_multiclass_observation():
    r = multiclass_event_distribution_audit([soccer_pair()], min_n=1)
    assert r["rows_graded"] == 1 and r["reconciled"]
    assert r["cohorts"][0]["mean_multiclass_brier"] == pytest.approx(.96)
    assert r["cohorts"][0]["mean_multiclass_log_loss"] == pytest.approx(-math.log(.2))
    assert r["can_execute"] is False


def test_missing_soccer_draw_cannot_be_graded_as_two_way():
    r = multiclass_event_distribution_audit([soccer_pair(result="HOME", dist={"HOME": .6, "AWAY": .4})])
    assert r["rows_graded"] == 0
    assert r["blockers"][0]["reason"] == "EXACT_OUTCOME_SPACE_OR_SETTLEMENT_INVALID"


def test_multiclass_non_normalized_probability_blocked():
    r = multiclass_event_distribution_audit([soccer_pair(dist={"HOME": .6, "DRAW": .2, "AWAY": .4})])
    assert r["blockers"][0]["reason"] == "FROZEN_MULTICLASS_DISTRIBUTION_INVALID"


def test_multiclass_outcome_identity_and_timing_must_match():
    before, after = soccer_pair()
    after["official_event_id"] = "another-event"
    assert multiclass_event_distribution_audit([(before, after)])["rows_graded"] == 0
    before, after = soccer_pair()
    before["model_timestamp_utc"] = "2026-11-23T04:00:00Z"
    assert multiclass_event_distribution_audit([(before, after)])["rows_graded"] == 0


def test_multiclass_voids_and_duplicate_receipts_reconciled_without_score():
    pairs = [soccer_pair(), soccer_pair(pid="other", result="VOID"), soccer_pair()]
    r = multiclass_event_distribution_audit(pairs, min_n=1)
    assert r["rows_received"] == 3 and r["rows_graded"] == 1
    assert r["rows_blocked"] == 2 and r["reconciled"]
    assert {x["reason"] for x in r["blockers"]} == {"NON_SCOREABLE_SETTLEMENT", "PREDICTION_ID_MISSING_OR_DUPLICATE"}


def test_multiclass_zero_probability_finite_log_score_never_changes_distribution():
    before, after = soccer_pair(dist={"HOME": .5, "DRAW": 0., "AWAY": .5})
    copy = dict(before["outcome_probabilities"])
    r = multiclass_event_distribution_audit([(before, after)], min_n=1)
    assert r["rows_graded"] == 1
    assert math.isfinite(r["cohorts"][0]["mean_multiclass_log_loss"])
    assert before["outcome_probabilities"] == copy


@pytest.mark.parametrize("raw", [
    [0.10000005, 0.2, 0.70000045],
    [0.09999995, 0.2, 0.69999955],
])
def test_pmf_roundoff_normalizes_before_pit_and_conserves_total_mass(raw):
    before, settled = prop_pair(pid="normalized", pmf=raw, outcome=2)
    frozen_before = list(before["predictive_count_pmf"])
    r = discrete_prop_distribution_audit([(before, settled)], bins=10, min_n=1)
    assert r["rows_graded"] == 1
    assert r["rows_pit_graded"] == 1
    assert sum(r["nonrandomized_pit_bin_proportions"]) == pytest.approx(1.0, abs=1e-12)
    assert before["predictive_count_pmf"] == frozen_before
    total = sum(raw)
    normalized = [x / total for x in raw]
    expected_crps = normalized[0]**2 + (normalized[0]+normalized[1])**2
    assert r["mean_discrete_crps"] == pytest.approx(expected_crps)


def test_pmf_mass_error_beyond_tolerance_blocks_frozen_distribution():
    p, o = prop_pair(pid="badmass", pmf=[0.1, 0.2, 0.700002], outcome=2)
    r = discrete_prop_distribution_audit([(p, o)], min_n=1)
    assert r["rows_graded"] == 0
    assert r["blockers"][0]["reason"] == "FROZEN_COUNT_PMF_INVALID"



def test_canonical_soccer_out_right_winner_requires_draw_state():
    before, after = soccer_pair(
        result="HOME", dist={"HOME": .6, "AWAY": .4}
    )
    before["market_family"] = "OUTRIGHT_WINNER"
    after["market_family"] = "OUTRIGHT_WINNER"
    r = multiclass_event_distribution_audit([(before, after)], min_n=1)
    assert r["rows_graded"] == 0
    assert r["blockers"][0]["reason"] == "EXACT_OUTCOME_SPACE_OR_SETTLEMENT_INVALID"


def test_canonical_soccer_three_way_distribution_grades_one_outcome():
    before, after = soccer_pair(result="HOME")
    before["market_family"] = after["market_family"] = "OUTRIGHT_WINNER"
    r = multiclass_event_distribution_audit([(before, after)], min_n=1)
    assert r["rows_graded"] == 1
    assert r["reconciled"] is True
