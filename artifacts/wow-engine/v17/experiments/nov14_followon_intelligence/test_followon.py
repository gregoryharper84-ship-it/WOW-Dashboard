"""Synthetic, non-serving regression suite. No claimed historical model lift."""
from copy import deepcopy
from math import isclose

from followon import (
    CAN_EXECUTE, AUTOMATIC_PROMOTION, TERMINAL_AUTHORITY,
    audit_same_event_cross_market, dual_gate_strong_confidence_shadow,
    nba_fatigue_three_point_shadow,
)


def nba(**updates):
    record = {
        "sport": "NBA", "official_event_id": "nba-event-001",
        "event_start_utc": "2026-11-14T23:00:00Z",
        "snapshot_timestamp_utc": "2026-11-14T18:00:00Z",
        "source_snapshot_hash": "sha256:synthetic-frozen",
        "shot_fit_artifact_hash": "sha256:synthetic-priors",
        "projected_pace": 100.0,
        "home_perimeter_defense_index": 0.95,
        "opponent_back_to_back": True,
        "opponent_traveled_since_last_game": True,
        "home_rest_days": 2,
        "away_rest_days": 0,
        "home_three_point_prior": {
            "projected_attempts": 40, "alpha": 15, "beta": 25,
            "frozen_at_utc": "2026-11-14T17:30:00Z",
        },
        "away_three_point_prior": {
            "projected_attempts": 33, "alpha": 12, "beta": 24,
            "frozen_at_utc": "2026-11-14T17:30:00Z",
        },
    }
    record.update(updates)
    return record


def leg(row_id="a", family="MONEYLINE", event="x", sport="NBA", **updates):
    record = {
        "row_id": row_id, "sport": sport, "official_event_id": event,
        "market_family": family, "selected_side": "OVER",
        "exact_line": 220.5,
        "market_model_certified": True,
        "model_market_family": family,
        "market_model_artifact_hash": "sha256:synthetic-market",
        "market_model_receipt_id": "synthetic-receipt",
        "calibration_receipt_id": "synthetic-calibration",
        "market_prediction_timestamp_utc": "2026-11-14T17:00:00Z",
        "event_start_utc": "2026-11-14T23:00:00Z",
        "calibrated_probability": .67,
        "calibrated_lower_bound": .62,
    }
    record.update(updates)
    return record


def model(**updates):
    record = {
        "sport": "NBA", "official_event_id": "x", "selected_participant": "Home",
        "prediction_id": "pred-1", "model_artifact_hash": "sha256:synthetic-model",
        "immutable_prediction_timestamp_utc": "2026-11-14T16:30:00Z",
        "event_start_utc": "2026-11-14T23:00:00Z",
        "calibration_status": "PASS", "probability_publishable": True,
        "rank_eligible": True, "market_prior_weight": .1,
        "calibrated_probability": .81, "calibrated_lower_bound": .77,
    }
    record.update(updates)
    return record


def quote(**updates):
    record = {
        "sport": "NBA", "official_event_id": "x", "selected_participant": "Home",
        "market_family": "MONEYLINE", "quote_source_id": "book",
        "two_way_market_snapshot_id": "snapshot-1",
        "two_way_market_verified": True,
        "quoted_at_utc": "2026-11-14T18:54:00Z",
        "american_odds": -110, "fee_rate_on_profit": .02,
        "market_no_vig_probability": .78,
    }
    record.update(updates)
    return record


def policy(**updates):
    record = {
        "approved_tier_1_floor": .72, "friction_buffer": .025,
        "approved_policy_receipt": "policy-v1",
        "policy_registry_verified": True,
        "max_quote_age_minutes": 10,
    }
    record.update(updates)
    return record


NOW = "2026-11-14T19:00:00Z"


def test_nba_interaction_and_beta_binomial_variance():
    output = nba_fatigue_three_point_shadow(nba())
    assert output["status"] == "FEATURES_ONLY_SHADOW"
    assert isclose(output["fatigue_interaction"], 95)
    assert isclose(output["rest_days_differential_home_minus_away"], 2)
    # n * a*b/(a+b)^2 * (n+a+b)/(1+a+b) for Beta-binomial
    expected = 40 * (15 / 40) * (25 / 40) * (40 + 40) / (1 + 40)
    assert isclose(output["home_three_point_variance"], expected)
    assert output["three_point_margin_variance"] is None
    assert output["adjusted_win_probability"] is None
    assert output["calibrated_lower_bound"] is None


def test_nba_rest_travel_interaction_is_not_unconditional():
    output = nba_fatigue_three_point_shadow(nba(opponent_traveled_since_last_game=False))
    assert output["fatigue_interaction"] == 0
    assert output["home_three_point_variance"] > 0


def test_nba_does_not_mutate_supplied_evidence():
    original = nba()
    snapshot = deepcopy(original)
    nba_fatigue_three_point_shadow(snapshot)
    assert snapshot == original


def test_nba_only_nba_sport_and_immutable_snapshot():
    assert nba_fatigue_three_point_shadow(nba(sport="NHL"))["status"] == "MODEL_INPUTS_INSUFFICIENT"
    assert nba_fatigue_three_point_shadow(nba(source_snapshot_hash=""))["status"] == "SOURCE_PROVENANCE_MISSING"
    assert nba_fatigue_three_point_shadow(
        nba(snapshot_timestamp_utc="2026-11-14T23:00:00Z")
    )["status"] == "PREGAME_CHRONOLOGY_INVALID"


def test_nba_late_shot_priors_hold():
    data = nba()
    data["home_three_point_prior"]["frozen_at_utc"] = "2026-11-14T21:00:00Z"
    assert nba_fatigue_three_point_shadow(data)["status"] == "SHOT_PRIOR_NOT_AVAILABLE_AT_FREEZE"


def test_nba_shot_variance_typed_missing_and_nonfinite_fail():
    for value in (float("nan"), True, None):
        assert nba_fatigue_three_point_shadow(nba(projected_pace=value))["status"] == "MODEL_INPUTS_INSUFFICIENT"


def test_nba_no_assumed_shot_covariance():
    assert nba_fatigue_three_point_shadow(nba())["three_point_margin_sd"] is None
    assert nba_fatigue_three_point_shadow(nba(shot_count_covariance=0))["status"] == "SHOT_COVARIANCE_NOT_VALIDATED"


def test_nba_valid_frozen_covariance_estimate():
    out = nba_fatigue_three_point_shadow(nba(
        shot_count_covariance=0.0, joint_shot_covariance_artifact_hash="sha256:frozen-joint",
    ))
    expected = 9 * (out["home_three_point_variance"] + out["away_three_point_variance"])
    assert isclose(out["three_point_margin_variance"], expected)
    assert isclose(out["three_point_margin_sd"] ** 2, expected)
    assert out["adjusted_win_probability"] is None


def test_nba_invalid_covariance_rejected():
    assert nba_fatigue_three_point_shadow(nba(
        shot_count_covariance=1000000,
        joint_shot_covariance_artifact_hash="sha256:frozen-joint",
    ))["status"] == "SHOT_COVARIANCE_INVALID"


def test_same_event_ml_and_total_held_even_if_receipts_claim_certified():
    legs = [leg("ml", "MONEYLINE"), leg("ou", "TOTAL")]
    before = deepcopy(legs)
    out = audit_same_event_cross_market(legs)
    assert out["status"] == "HELD_FOR_CROSS_MARKET"
    assert all("SAME_EVENT_CROSS_MARKET_DEPENDENCE_UNQUANTIFIED" in row["blockers"]
               for row in out["rows"].values())
    assert out["rows"]["ml"]["same_event_cross_market_correlation"] is None
    assert out["rows"]["ou"]["joint_probability"] is None
    assert before == legs


def test_spread_and_total_cannot_inherit_ml_confidence():
    out = audit_same_event_cross_market([
        leg("ml", "MONEYLINE", event="a"),
        leg("sp", "SPREAD", event="b", market_model_certified=False),
    ])
    assert "SECONDARY_MARKET_SPECIALIST_UNVERIFIED" in out["rows"]["sp"]["blockers"]
    assert out["rows"]["ml"]["blockers"] == []
    assert out["rows"]["ml"]["card_eligible"] is False


def test_wrong_line_and_missing_specialist_hold():
    out = audit_same_event_cross_market([leg("ou", "TOTAL", exact_line=None)])
    assert "SECONDARY_MARKET_EXACT_LINE_MISSING" in out["rows"]["ou"]["blockers"]


def test_unrelated_sports_with_same_raw_event_id_not_joint():
    out = audit_same_event_cross_market([
        leg("nba", "MONEYLINE", event="123", sport="NBA"),
        leg("nhl", "TOTAL", event="123", sport="NHL"),
    ])
    assert out["status"] == "STRUCTURE_ONLY_PASS"
    assert out["rows"]["nba"]["co_exposure_row_ids"] == []
    assert out["rows"]["nhl"]["co_exposure_row_ids"] == []


def test_crossmarket_duplicate_or_missing_identity_hold():
    out = audit_same_event_cross_market([leg("x"), leg("x")])
    assert out["status"] == "ROW_ID_MISSING_OR_DUPLICATE"
    assert not out["card_structurally_qualified"]
    out2 = audit_same_event_cross_market([leg(event="")])
    assert out2["status"] == "HELD_FOR_CROSS_MARKET"


def test_structural_pass_is_not_publication_or_joint_certification():
    out = audit_same_event_cross_market([leg("a", "MONEYLINE", event="A")])
    assert out["status"] == "STRUCTURE_ONLY_PASS"
    assert not out["card_publication_allowed"]
    assert out["production_publication_allowed"] is False


def test_probability_leaderboard_retained_when_quote_missing():
    out = dual_gate_strong_confidence_shadow(model(), None, policy(), as_of_utc=NOW)
    assert out["probability_rank_eligible"]
    assert out["strong_probability_tier_pass"]
    assert out["status"] == "PRICE_MISSING"
    assert not out["value_candidate_pass"] and not out["actionable_card_eligible"]


def test_valid_research_value_gate_is_separate_from_execution():
    out = dual_gate_strong_confidence_shadow(model(), quote(), policy(), as_of_utc=NOW)
    assert out["probability_rank_eligible"]
    assert out["strong_probability_tier_pass"]
    assert out["value_candidate_pass"]
    assert out["lower_bound_friction_adjusted_edge"] > 0
    assert out["market_no_vig_probability"] == .78
    assert out["status"] == "RESEARCH_VALUE_CANDIDATE"
    assert not out["actionable_card_eligible"] and not out["production_publication_allowed"]


def test_negative_ev_heavy_favorite_stays_probability_ranked():
    out = dual_gate_strong_confidence_shadow(
        model(calibrated_probability=.94, calibrated_lower_bound=.88),
        quote(american_odds=-850, market_no_vig_probability=.92),
        policy(), as_of_utc=NOW,
    )
    assert out["probability_rank_eligible"]
    assert out["strong_probability_tier_pass"]
    assert "LOWER_BOUND_EXECUTABLE_EDGE_NON_POSITIVE" in out["blockers"]
    assert not out["value_candidate_pass"]


def test_fee_changes_break_even_and_edge():
    no_fee = dual_gate_strong_confidence_shadow(model(), quote(fee_rate_on_profit=0), policy(), as_of_utc=NOW)
    fee = dual_gate_strong_confidence_shadow(model(), quote(fee_rate_on_profit=.1), policy(), as_of_utc=NOW)
    assert fee["break_even_probability"] > no_fee["break_even_probability"]
    assert fee["lower_bound_friction_adjusted_edge"] < no_fee["lower_bound_friction_adjusted_edge"]


def test_market_prior_dependence_prevents_value_promotion():
    out = dual_gate_strong_confidence_shadow(model(market_prior_weight=.6), quote(), policy(), as_of_utc=NOW)
    assert out["probability_rank_eligible"]
    assert "MARKET_DEPENDENT_MODEL" in out["blockers"]
    assert not out["value_candidate_pass"]


def test_noncertified_model_cannot_be_probability_ranked():
    for key, value in (("calibration_status", "SHADOW"), ("prediction_id", ""),
                       ("rank_eligible", False), ("calibrated_lower_bound", None)):
        out = dual_gate_strong_confidence_shadow(model(**{key: value}), quote(), policy(), as_of_utc=NOW)
        assert not out["probability_rank_eligible"]
        assert not out["value_candidate_pass"]


def test_policy_floor_not_inferred_or_relaxed():
    out = dual_gate_strong_confidence_shadow(model(), quote(), policy(approved_policy_receipt=""), as_of_utc=NOW)
    assert out["status"] == "TIER_POLICY_UNVERIFIED"
    out2 = dual_gate_strong_confidence_shadow(model(), quote(), policy(approved_tier_1_floor=.79), as_of_utc=NOW)
    assert out2["status"] == "BELOW_STRONG_PROBABILITY_FLOOR"
    assert out2["probability_rank_eligible"]


def test_quote_staleness_exact_identity_and_time_fail_closed():
    cases = (
        quote(quoted_at_utc="2026-11-14T18:30:00Z"),
        quote(official_event_id="different"),
        quote(selected_participant="Away"),
        quote(american_odds=0),
        quote(fee_rate_on_profit=float("nan")),
        quote(two_way_market_verified=False),
    )
    for q in cases:
        out = dual_gate_strong_confidence_shadow(model(), q, policy(), as_of_utc=NOW)
        assert out["status"] == "EXACT_PRICE_OR_IDENTITY_UNVERIFIED"
        assert out["probability_rank_eligible"]
        assert not out["value_candidate_pass"]


def test_market_disagreement_held_without_resolution():
    out = dual_gate_strong_confidence_shadow(
        model(), quote(market_no_vig_probability=.48),
        policy(), as_of_utc=NOW,
    )
    assert "MODEL_MARKET_DISAGREEMENT_UNRESOLVED" in out["blockers"]
    assert not out["value_candidate_pass"]


def test_no_post_event_prediction_as_pregame():
    out = dual_gate_strong_confidence_shadow(
        model(immutable_prediction_timestamp_utc="2026-11-15T00:00:00Z"),
        quote(), policy(), as_of_utc=NOW,
    )
    assert out["status"] == "MODEL_INPUTS_INSUFFICIENT"


def test_outputs_cannot_mutate_original_probabilities_or_execute():
    m, q, pol = model(), quote(), policy()
    before = deepcopy((m, q, pol))
    out = dual_gate_strong_confidence_shadow(m, q, pol, as_of_utc=NOW)
    assert (m, q, pol) == before
    assert out["calibrated_probability"] == m["calibrated_probability"]
    assert out["calibrated_lower_bound"] == m["calibrated_lower_bound"]
    assert not out["probability_mutated"]
    assert out["can_execute"] is False
    assert out["automatic_promotion"] is False
    assert CAN_EXECUTE is False and AUTOMATIC_PROMOTION is False
    assert TERMINAL_AUTHORITY == "V17_TERMINAL_REDUCER"
