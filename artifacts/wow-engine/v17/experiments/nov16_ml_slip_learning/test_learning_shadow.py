"""Synthetic chronology and regression tests. They cannot certify sporting lift."""
import copy
from datetime import datetime, timedelta, timezone

import pytest
from learning_shadow import (
    CAN_EXECUTE, AUTOMATIC_PROMOTION, TERMINAL_AUTHORITY,
    score_prop_projection, conditional_usage_shadow, revision_regret_shadow,
    slip_exposure_shadow, chronological_holdout_shadow,
)


def prop(**overrides):
    x = dict(sport="NBA", official_event_id="nba-101", receipt_id="immutable-1",
             market_id="player-reb-assists-8.5", source_snapshot_hash="sha256:pre",
             event_start_utc="2026-11-16T23:00:00Z",
             snapshot_at_utc="2026-11-16T19:00:00Z",
             exact_line=8.5, side="OVER", projected_mean=12.0)
    x.update(overrides)
    return x


def settlement(**overrides):
    x = dict(sport="NBA", official_event_id="nba-101", market_id="player-reb-assists-8.5",
             actual_count=9, source_type="INDEPENDENT_OFFICIAL",
             source_id="nba-boxscore-101", finalized_at_utc="2026-11-17T02:30:00Z")
    x.update(overrides)
    return x


def scenario(**overrides):
    x = dict(sport="NFL", official_event_id="nfl-101",
             event_start_utc="2026-11-17T01:20:00Z",
             snapshot_at_utc="2026-11-16T20:00:00Z",
             team_event_scenario_at_utc="2026-11-16T19:30:00Z",
             team_event_scenario_receipt_id="llp-score-state-1",
             team_event_scenario_sport="NFL",
             team_event_scenario_official_event_id="nfl-101",
             team_event_model_artifact_hash="sha256:llp",
             prop_fit_artifact_hash="sha256:prop",
             prop_source_snapshot_hash="sha256:inputs",
             prop_fit_cutoff_utc="2026-11-13T00:00:00Z",
             score_states=[
                 dict(state="LEADING", weight=.3, expected_count=24,
                      count_variance=8, p_over_exact_line=.78),
                 dict(state="TIED", weight=.2, expected_count=19,
                      count_variance=7, p_over_exact_line=.50),
                 dict(state="TRAILING", weight=.5, expected_count=11,
                      count_variance=6, p_over_exact_line=.12),
             ])
    x.update(overrides)
    return x


def version(receipt_id, **kw):
    x = dict(sport="NBA", official_event_id="nba-101", proposition_id="player-a-over-8.5",
             slot_id="prop-priority-5", market_family="PLAYER_PROP", exact_line=8.5,
             event_start_utc="2026-11-16T23:00:00Z", receipt_id=receipt_id,
             forecast_at_utc="2026-11-16T17:00:00Z" if receipt_id == "original" else "2026-11-16T20:00:00Z",
             predicted_probability=.69 if receipt_id == "original" else .62,
             calibrated_lower_bound=.60 if receipt_id == "original" else .54)
    if receipt_id != "original":
        x.update(new_evidence_at_utc="2026-11-16T19:30:00Z",
                 new_evidence_source_id="lineup-report", revision_reason="minutes change")
    x.update(kw)
    return x


def outcomes(**kw):
    return {r: dict(source_type="INDEPENDENT_OFFICIAL", source_id="nba-box",
                    hit=h, finalized_at_utc="2026-11-17T02:30:00Z") for r,h in kw.items()}


def slip(sid, games, score=.9, thesis=""):
    return dict(slip_id=sid, priority_score=score, legs=[
        dict(sport=sport, official_event_id=event, leg_id=f"{sid}-{i}", thesis_key=thesis)
        for i, (sport, event) in enumerate(games)])


def replay(**kw):
    x = dict(sport="NBA", official_event_id="nba-holdout-1", proposition_id="win-home",
             exact_line=0.5, event_start_utc="2026-11-10T23:00:00Z",
             forecast_at_utc="2026-11-10T21:00:00Z",
             model_fit_cutoff_utc="2026-09-29T23:00:00Z",
             calibration_fit_cutoff_utc="2026-10-30T23:00:00Z",
             finalized_at_utc="2026-11-11T02:00:00Z",
             immutable_forecast_receipt_id="frozen-1",
             source_snapshot_hash="sha256:freeze",
             baseline_model_hash="sha256:baseline",
             challenger_model_hash="sha256:challenger",
             calibration_receipt_id="cal-1", outcome=True,
             settlement_source_type="INDEPENDENT_OFFICIAL",
             settlement_source_id="official-final-1",
             baseline_probability=.65, challenger_probability=.75,
             baseline_lower_bound=.57, challenger_lower_bound=.68)
    x.update(kw)
    return x


def evaluate(rows):
    return chronological_holdout_shadow(rows, train_end_utc="2026-09-30T00:00:00Z",
                                      calibration_end_utc="2026-10-31T00:00:00Z",
                                      holdout_start_utc="2026-11-01T00:00:00Z",
                                      holdout_end_utc="2026-11-30T00:00:00Z")


def test_prop_continuous_error_separate_from_hit():
    r = score_prop_projection(prop(), settlement())
    assert r["status"] == "GRADED_RESEARCH_ONLY" and r["hit"] is True
    assert r["projection_absolute_error"] == 3 and r["projection_signed_error"] == 3
    assert r["margin_vs_line"] == .5 and r["discrete_increments_above"] == 0
    assert r["near_threshold"] and not r["near_threshold_implies_median"]


def test_nhl_miller_near_threshold_without_claiming_median():
    p = prop(sport="NHL", official_event_id="nhl-x", market_id="miller-shots", exact_line=1.5,
             projected_mean=None)
    s = settlement(sport="NHL", official_event_id="nhl-x", market_id="miller-shots", actual_count=2)
    r = score_prop_projection(p, s)
    assert r["hit"] and r["near_threshold"] and r["projection_absolute_error"] is None


def test_prop_under_and_push_settlement():
    assert score_prop_projection(prop(side="UNDER", exact_line=10.5), settlement())["hit"] is True
    assert score_prop_projection(prop(exact_line=9), settlement())["push"] is True
    assert score_prop_projection(prop(exact_line=9), settlement())["hit"] is None


def test_under_near_threshold_exact_integer_and_half_integer():
    half = score_prop_projection(prop(side="UNDER", exact_line=9.5), settlement(actual_count=9))
    integer = score_prop_projection(prop(side="UNDER", exact_line=10), settlement(actual_count=9))
    for rec in (half, integer):
        assert rec["hit"] is True
        assert rec["near_threshold"] is True
        assert rec["distance_to_threshold_increments"] == 0
        assert rec["discrete_increments_above"] is None


def test_under_with_larger_margin_not_flagged_as_near_threshold():
    rec = score_prop_projection(prop(side="UNDER", exact_line=10.5), settlement(actual_count=6))
    assert rec["hit"] is True
    assert rec["distance_to_threshold_increments"] == 4
    assert rec["near_threshold"] is False


def test_user_prop_line_not_substitutable_for_revised_line():
    assert score_prop_projection(prop(exact_line=5.5, market_id="davion-assists"),
                                 settlement(market_id="davion-assists", actual_count=10))["hit"] is True
    assert score_prop_projection(prop(exact_line=7.5, market_id="davion-assists"),
                                 settlement(market_id="davion-assists", actual_count=10))["hit"] is True
    assert score_prop_projection(prop(market_id="davion-other"), settlement())["status"] == "UNVERIFIED_PROP_GRADE"


@pytest.mark.parametrize("change", [
    {"snapshot_at_utc": "2026-11-17T00:00:00Z"},
    {"source_snapshot_hash": ""}, {"receipt_id": ""}, {"exact_line": float("nan")},
])
def test_prop_bad_receipts_fail_closed(change):
    assert score_prop_projection(prop(**change), settlement())["status"] == "UNVERIFIED_PROP_GRADE"


def test_prop_official_after_start_and_no_screenshot_paid_inference():
    assert score_prop_projection(prop(), settlement(source_type="USER_SCREENSHOT"))["hit"] is None
    assert score_prop_projection(prop(), settlement(finalized_at_utc="2026-11-16T20:00:00Z"))["hit"] is None


def test_henry_trailing_regime_reduces_rushes():
    r = conditional_usage_shadow(scenario())
    assert r["status"] == "CONDITIONAL_USAGE_RESEARCH_ONLY"
    assert r["trailing_expected_count"] == 11 < r["leading_expected_count"] == 24
    assert abs(r["expected_count"] - 16.5) < 1e-10
    assert abs(r["p_over"]-(.3*.78+.2*.5+.5*.12)) < 1e-10
    assert r["variance"] > 0 and not r["production_publication_allowed"]


def test_henry_more_trailing_weight_lowers_volume():
    baseline = scenario()["score_states"]
    other = copy.deepcopy(baseline)
    other[0]["weight"], other[2]["weight"] = .1, .7
    assert conditional_usage_shadow(scenario(score_states=other))["expected_count"] < conditional_usage_shadow(scenario())["expected_count"]


@pytest.mark.parametrize("change", [
    {"team_event_scenario_receipt_id": ""},
    {"team_event_scenario_at_utc":"2026-11-16T21:00:00Z"},
    {"prop_fit_cutoff_utc":"2026-11-16T22:00:00Z"},
    {"score_states": [{"state":"LEADING","weight":1,"expected_count":22,"count_variance":3,"p_over_exact_line":.6}]},
])
def test_untrusted_or_missing_scenario_fails(change):
    assert conditional_usage_shadow(scenario(**change))["status"] == "SCENARIO_EVIDENCE_INSUFFICIENT"


def test_scenario_weights_not_silently_normalized():
    states = copy.deepcopy(scenario()["score_states"])
    states[2]["weight"] = .9
    assert conditional_usage_shadow(scenario(score_states=states))["status"] == "SCENARIO_EVIDENCE_INSUFFICIENT"


def test_revision_brier_regret_not_automatic_lock():
    r = revision_regret_shadow(version("original"), version("revised"),
                               outcomes(original=True, revised=True))
    assert r["status"] == "PAIRED_REVISION_DIAGNOSTIC" and r["brier_regret"] > 0
    assert r["lower_bound_delta"] == pytest.approx(-.06)
    assert r["same_target"] and r["automatic_revision_lock"] is False


def test_same_target_conflicting_official_outcomes_rejected():
    r = revision_regret_shadow(version("original"), version("revised"),
                               outcomes(original=True, revised=False))
    assert r["status"] == "CONFLICTING_OFFICIAL_SETTLEMENT"
    assert r["brier_regret"] is None


def test_cross_event_replacement_after_original_start_rejected():
    newer = version("revised", sport="NFL", official_event_id="next-week",
                    proposition_id="another-bet", event_start_utc="2026-11-18T23:00:00Z",
                    forecast_at_utc="2026-11-17T12:00:00Z",
                    new_evidence_at_utc="2026-11-17T11:30:00Z")
    r = revision_regret_shadow(version("original"), newer,
                               outcomes(original=True, revised=False))
    assert r["status"] == "REVISION_UNVERIFIED"
    assert r["brier_regret"] is None


def test_cross_event_replacement_before_original_start_still_allowed():
    newer = version("revised", sport="NFL", official_event_id="next-week",
                    proposition_id="another-bet", event_start_utc="2026-11-18T23:00:00Z",
                    forecast_at_utc="2026-11-16T22:00:00Z",
                    new_evidence_at_utc="2026-11-16T21:30:00Z")
    settled = outcomes(original=True, revised=False)
    settled["revised"]["finalized_at_utc"] = "2026-11-19T02:30:00Z"
    r = revision_regret_shadow(version("original"), newer, settled)
    assert r["status"] == "PAIRED_REVISION_DIAGNOSTIC"
    assert r["same_target"] is False
    assert r["automatic_revision_lock"] is False


def test_different_candidates_are_not_same_calibration_target():
    revised = version("revised", proposition_id="derrick-henry-over-18.5", sport="NFL",
                      official_event_id="nfl-1", market_family="RUSHING_ATTEMPTS",
                      exact_line=18.5, event_start_utc="2026-11-17T01:20:00Z")
    r = revision_regret_shadow(version("original"), revised, outcomes(original=True, revised=False))
    assert r["status"] == "PAIRED_REVISION_DIAGNOSTIC"
    assert r["comparison_basis"] == "CANDIDATE_SELECTION_DIFFERENT_PROPOSITIONS"
    assert r["lower_bound_delta"] is None


def test_unexplained_revision_is_held_and_original_receipt_stays_intact():
    original, revised = version("original"), version("revised", new_evidence_source_id="")
    before = copy.deepcopy((original, revised))
    r = revision_regret_shadow(original, revised, outcomes(original=True, revised=False))
    assert r["status"] == "REVISION_EVIDENCE_NOT_DOCUMENTED"
    assert (original,revised) == before


def test_post_start_revision_is_not_a_pregame_override():
    assert revision_regret_shadow(version("original"),
                                  version("revised", forecast_at_utc="2026-11-17T00:00:00Z"),
                                  outcomes(original=True, revised=False))["status"] == "REVISION_UNVERIFIED"


def test_utah_repeated_across_three_slips_counted():
    slips = [
        slip("A", [("NHL", "uta-van"),("NBA","phi-bkn")]),
        slip("B", [("NHL", "uta-van"),("NHL","nyr-mtl")]),
        slip("C", [("NBA", "min-por")]),
        slip("D", [("NBA", "bos-orl")]),
        slip("E", [("NFL", "lac-bal")]),
        slip("F", [("NHL", "uta-van"),("NBA","bos-orl"),("NBA","phi-bkn")]),
    ]
    r = slip_exposure_shadow(slips)
    assert r["event_reuse"]["NHL:uta-van"] == 3
    assert r["event_reuse"]["NBA:bos-orl"] == 2
    assert r["event_reuse"]["NBA:phi-bkn"] == 2
    assert r["slips"]["A"]["soft_adjusted_priority_score"] < .9
    assert r["slips"]["C"]["soft_adjusted_priority_score"] == .9
    assert r["slips"]["A"]["joint_probability"] is None
    assert r["hard_exposure_limit_applied"] is False


def test_same_raw_event_different_sport_not_overlap():
    out = slip_exposure_shadow([slip("X", [("NHL","100")]), slip("Y", [("NBA","100")])])
    assert out["event_reuse"] == {}
    assert out["slips"]["X"]["shared_with_slips"] == []


def test_thesis_overlap_detected_even_across_events():
    out = slip_exposure_shadow([slip("X", [("NBA","100")], thesis="boston fatigue"),
                               slip("Y", [("NBA","200")], thesis="boston fatigue")])
    assert out["thesis_reuse"]["boston fatigue"] == 2
    assert out["slips"]["X"]["shared_with_slips"] == ["Y"]


def test_repeated_event_within_same_slip_not_counted_twice():
    out = slip_exposure_shadow([slip("X", [("NBA","100"),("NBA","100")]),
                               slip("Y", [("NBA","200")])])
    assert out["event_reuse"] == {}
    assert out["slips"]["X"]["same_event_cross_market_correlation"] is None


@pytest.mark.parametrize("change", [{"priority_score":float("nan")},{"priority_score":1.2}])
def test_invalid_slip_priority_cannot_be_ranked(change):
    x = slip("X", [("NHL","100")]); x.update(change)
    assert slip_exposure_shadow([x])["status"] == "SLIP_INPUTS_INVALID"


def test_holdout_paired_score_and_lower_bound_gap():
    r = evaluate([replay()])
    assert r["status"] == "PAIRED_HOLDOUT_RESEARCH_ONLY"
    assert r["sample_size"] == 1 and r["paired_brier_delta"] < 0
    assert r["challenger"]["lower_bound_empirical_gap"] == pytest.approx(.32)
    assert not r["model_promoted"] and not r["independently_certified"]


def test_holdout_prop_mae_counts_only_when_actual_and_both_means_present():
    r = evaluate([replay(actual_count=9, baseline_projected_mean=12,
                         challenger_projected_mean=10)])
    assert r["baseline"]["prop_mae"] == 3
    assert r["challenger"]["prop_mae"] == 1


@pytest.mark.parametrize("change", [
    {"forecast_at_utc":"2026-11-11T00:00:00Z"},
    {"model_fit_cutoff_utc":"2026-10-10T00:00:00Z"},
    {"calibration_fit_cutoff_utc":"2026-11-10T00:00:00Z"},
    {"source_snapshot_hash":""},
    {"settlement_source_type":"SOCIAL_MEDIA"},
    {"baseline_probability":float("nan")},
    {"challenger_lower_bound":.99},
])
def test_holdout_leakage_and_invalid_probabilities_rejected(change):
    assert evaluate([replay(**change)])["status"] == "HOLDOUT_INPUTS_INVALID"


@pytest.mark.parametrize("change", [
    {"proposition_id": None}, {"proposition_id": ""},
    {"proposition_id": "   "}, {"exact_line": None},
    {"exact_line": float("nan")}, {"exact_line": float("inf")},
    {"exact_line": "0.5"},
])
def test_holdout_requires_exact_proposition_and_finite_line(change):
    assert evaluate([replay(**change)])["status"] == "HOLDOUT_INPUTS_INVALID"


def test_holdout_duplicate_identity_line_rejected():
    assert evaluate([replay(), replay(immutable_forecast_receipt_id="frozen-2")])["status"] == "HOLDOUT_INPUTS_INVALID"


def test_holdout_refuses_overlapping_train_calibration_windows():
    assert chronological_holdout_shadow([replay()],train_end_utc="2026-11-15T00:00:00Z",
                                     calibration_end_utc="2026-11-01T00:00:00Z",
                                     holdout_start_utc="2026-11-01T00:00:00Z",
                                     holdout_end_utc="2026-11-30T00:00:00Z")["status"] == "HOLDOUT_INPUTS_INVALID"


def test_no_execution_or_terminal_redefinition():
    assert CAN_EXECUTE is False and AUTOMATIC_PROMOTION is False
    assert TERMINAL_AUTHORITY == "V17_TERMINAL_REDUCER"
    assert all(not out["production_publication_allowed"] for out in [
        score_prop_projection(prop(), settlement()), conditional_usage_shadow(scenario()),
        revision_regret_shadow(version("original"),version("revised"),
                               outcomes(original=True,revised=True)),
        slip_exposure_shadow([slip("X", [("NBA","1")])]),
        evaluate([replay()])])


def test_prop_distribution_crps_when_frozen_pmf_available():
    # Three discrete outcomes 0,1,2 with y=1: .2^2 + (.5-1)^2 = .29.
    r = score_prop_projection(prop(predictive_count_pmf=[.2,.3,.5]),
                              settlement(actual_count=1))
    assert r["distribution_status"] == "SCORED_DISTRIBUTION"
    assert r["predictive_crps"] == pytest.approx(.29)


def test_prop_distribution_invalid_does_not_manufacture_crps():
    r = score_prop_projection(prop(predictive_count_pmf=[.8,.8]), settlement())
    assert r["distribution_status"] == "INVALID_DISTRIBUTION"
    assert r["predictive_crps"] is None
    assert r["hit"] is True


def test_cross_event_scenario_receipt_cannot_transfer():
    assert conditional_usage_shadow(scenario(team_event_scenario_official_event_id="nfl-other"))["status"] == "SCENARIO_EVIDENCE_INSUFFICIENT"


def test_high_cross_slip_exposure_warns_but_does_not_reject():
    out = slip_exposure_shadow([
        slip("A", [("NHL", "UTA")]), slip("B", [("NHL", "UTA")]),
        slip("C", [("NHL", "UTA")]), slip("D", [("NBA", "BOS")])])
    assert "NHL:UTA" in out["concentration_warnings"]
    assert out["status"] == "EXPOSURE_DIAGNOSTIC_ONLY"
    assert not out["hard_exposure_limit_applied"]


def joint_legs():
    return [dict(sport="NBA", official_event_id="nba-a", leg_id="a",
                 event_start_utc="2026-11-17T00:00:00Z",
                 market_family="MONEYLINE", settlement_basis="FULL_GAME_OT"),
            dict(sport="NHL", official_event_id="nhl-b", leg_id="b",
                 event_start_utc="2026-11-17T02:00:00Z",
                 market_family="SHOTS", settlement_basis="FULL_GAME_OT")]


def joint_bundle(**updates):
    x = dict(scenario_frozen_at_utc="2026-11-16T20:00:00Z",
             joint_fit_cutoff_utc="2026-11-15T20:00:00Z",
             source_snapshot_hash="sha256:joint-snapshot",
             joint_model_artifact_hash="sha256:joint-fit",
             joint_calibration_receipt_id="joint-cal-1",
             joint_scenario_source_verified=True,
             joint_scenarios=[
                 dict(weight=.1, leg_success={"a":True,"b":True}),
                 dict(weight=.4, leg_success={"a":True,"b":False}),
                 dict(weight=.2, leg_success={"a":False,"b":True}),
                 dict(weight=.3, leg_success={"a":False,"b":False}),
             ])
    x.update(updates)
    return x


def test_joint_probability_from_joint_states_not_independent_product():
    from learning_shadow import joint_scenario_slip_shadow
    r = joint_scenario_slip_shadow(joint_legs(), joint_bundle())
    assert r["status"] == "JOINT_SCENARIOS_RESEARCH_ONLY"
    assert r["joint_probability"] == pytest.approx(.1)
    assert r["marginal_probabilities"]["a"] == pytest.approx(.5)
    assert r["marginal_probabilities"]["b"] == pytest.approx(.3)
    assert r["joint_probability"] != pytest.approx(.5*.3)
    assert r["research_edge"] is None and not r["joint_model_certified"]


def test_joint_exact_offer_fee_and_break_even():
    from learning_shadow import joint_scenario_slip_shadow
    offer = dict(gross_multiplier=4.0, fee_rate_on_profit=.05,
                 quoted_at_utc="2026-11-16T19:59:00Z",
                 joint_offer_id="synthetic-priced-1", exact_leg_ids=["a","b"])
    r = joint_scenario_slip_shadow(joint_legs(), joint_bundle(), offer)
    assert r["break_even_probability"] == pytest.approx(1/(1+3*.95))
    assert r["research_edge"] == pytest.approx(.1-1/3.85)
    assert not r["production_publication_allowed"]


def test_joint_rejects_post_event_scenario_and_unauthenticated_fit():
    from learning_shadow import joint_scenario_slip_shadow
    assert joint_scenario_slip_shadow(joint_legs(), joint_bundle(
        scenario_frozen_at_utc="2026-11-17T00:00:00Z"))["joint_probability"] is None
    assert joint_scenario_slip_shadow(joint_legs(), joint_bundle(
        joint_model_artifact_hash=""))["joint_probability"] is None


def test_joint_rejects_incomplete_or_unaligned_leg_outcomes():
    from learning_shadow import joint_scenario_slip_shadow
    bad = joint_bundle(joint_scenarios=[dict(weight=1.0,leg_success={"a":True})])
    assert joint_scenario_slip_shadow(joint_legs(), bad)["status"] == "JOINT_SCENARIOS_UNVERIFIED"


def test_joint_quote_missing_or_mismatch_no_edge():
    from learning_shadow import joint_scenario_slip_shadow
    r = joint_scenario_slip_shadow(joint_legs(), joint_bundle(),
                                   dict(gross_multiplier=4,fee_rate_on_profit=.05,
                                        quoted_at_utc="2026-11-16T20:02:00Z",
                                        joint_offer_id="quote",exact_leg_ids=["a","b"]))
    assert r["joint_probability"] == pytest.approx(.1)
    assert r["break_even_probability"] is None and r["research_edge"] is None


def test_bad_offer_leg_id_type_fails_without_crash():
    from learning_shadow import joint_scenario_slip_shadow
    r = joint_scenario_slip_shadow(joint_legs(), joint_bundle(),
        dict(gross_multiplier=4, fee_rate_on_profit=0,
             quoted_at_utc="2026-11-16T19:00:00Z",
             joint_offer_id="quote", exact_leg_ids=5))
    assert r["joint_probability"] == pytest.approx(.1)
    assert r["break_even_probability"] is None


def test_malformed_slip_leg_returns_typed_failure():
    assert slip_exposure_shadow([dict(slip_id="X",priority_score=.9,legs=[None])])["status"] == "SLIP_INPUTS_INVALID"
