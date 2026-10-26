"""No-network, no-credential adversarial tests for the shadow-only experiment controller."""
import copy
from datetime import datetime, timedelta, timezone

import pytest
from v17.accuracy_improvement_controller import (
    CAN_EXECUTE, AUTOMATIC_PROMOTION, evaluate_experiment, ExperimentInputError,
)

def case(n=1):
    start = datetime(2026, 10, 1, 18, tzinfo=timezone.utc)
    result = {"experiment_id": "SHADOW_EXPERIMENT", "expected_original_offer_count": n,
              "offers": [], "evidence_by_offer_id": {}}
    for i in range(n):
        st = start + timedelta(days=i)
        oid = "offer-" + str(i)
        threshold = "7+" if i == 0 else "4.5"
        result["offers"].append({
            "offer_id": oid, "original_label": "Player %d %s assists" % (i, threshold),
            "event_id": "event-" + str(i), "entity_id": "player-" + str(i),
            "team_id": "MIA", "opponent_id": "BOS", "period": "FULL_GAME", "stat": "ASSISTS",
            "threshold_label": threshold, "direction": "MORE",
            "offer_type": "AT_LEAST" if i == 0 else "SCALAR", "sport": "NBA",
            "route": "NBA_ASSISTS", "event_start_utc": st.isoformat(),
        })
        models = []
        for name, p in (("champion", .55), ("challenger", .7)):
            models.append({
                "prediction_id": "%s-%d" % (name, i),
                "model_artifact_id": name + "-artifact",
                "calibrator_id": name + "-calibrator",
                "specialist_id": "NBA_" + name + "_FITTED",
                "offer_id": oid, "route": "NBA_ASSISTS", "origin": "FITTED_SPECIALIST",
                "created_at_utc": (st-timedelta(hours=2)).isoformat(),
                "feature_asof_utc": (st-timedelta(hours=3)).isoformat(),
                "calibrated_probability": p, "calibrated_lower_bound": p-.1,
            })
        for model in models:
            for field in ("sport", "event_id", "entity_id", "team_id", "opponent_id",
                          "stat", "period", "threshold_label", "direction", "offer_type"):
                model[field] = result["offers"][-1][field]
            model["source_snapshot_id"] = "source-%d" % i
            model["locked_at_utc"] = (st-timedelta(hours=1)).isoformat()
        result["evidence_by_offer_id"][oid] = {
            "offer_id": oid, "identity_verification": "OFFICIAL_VERIFIED",
            "pregame_eligibility": "CONFIRMED_ELIGIBLE",
            "evidence_asof_utc": (st-timedelta(hours=3)).isoformat(),
            "split": "CALIBRATION" if i < 2 else "HOLDOUT",
            "champion": models[0], "challenger": models[1],
            "settlement": {"source_id": "OFFICIAL_STATS", "verification_id": "verify-%d" % i,
                           "receipt_id": "settle-%d" % i,
                           "settled_at_utc": (st+timedelta(hours=3)).isoformat(),
                           "outcome": 1},
        }
        settlement = result["evidence_by_offer_id"][oid]["settlement"]
        for field in ("offer_id", "event_id", "entity_id", "team_id", "opponent_id",
                      "stat", "period", "threshold_label", "direction", "offer_type"):
            settlement[field] = result["offers"][-1][field]
    return result

def test_board_preserves_literal_at_least_and_dry_run():
    r = evaluate_experiment(case(3))
    assert r["reconciled_offer_count"] == 3
    assert r["paired_research_rows"] == 3
    assert r["rows"][0]["original_label"] == "Player 0 7+ assists"
    assert r["certification_disposition"] == "INSUFFICIENT_UNSEEN_EVENTS"
    assert CAN_EXECUTE is False and AUTOMATIC_PROMOTION is False
    assert r["can_execute"] is False and r["automatic_promotion"] is False

def test_partial_board_preserves_other_rows():
    payload = case(3)
    del payload["evidence_by_offer_id"]["offer-1"]
    r = evaluate_experiment(payload)
    assert r["reconciled_offer_count"] == 3
    assert r["paired_research_rows"] == 2
    assert r["held_codes"]["NOT_SCORED"] == 1

def test_original_board_count_must_match():
    payload = case()
    payload["expected_original_offer_count"] = 27
    with pytest.raises(ExperimentInputError, match="SOURCE_BOARD_COUNT_MISMATCH"):
        evaluate_experiment(payload)

def test_duplicate_offer_rejected():
    payload = case(2)
    payload["offers"][1]["offer_id"] = "offer-0"
    with pytest.raises(ExperimentInputError, match="DUPLICATE_ORIGINAL_OFFER_ID"):
        evaluate_experiment(payload)

def test_wrong_player_identity_held():
    payload = case(3)
    payload["evidence_by_offer_id"]["offer-0"]["identity_verification"] = "UNVERIFIED"
    r = evaluate_experiment(payload)
    assert r["held_codes"]["IDENTITY_NOT_OFFICIALLY_VERIFIED"] == 1
    assert r["paired_research_rows"] == 2

def test_ineligible_player_held():
    payload = case()
    payload["evidence_by_offer_id"]["offer-0"]["pregame_eligibility"] = "OUT"
    assert evaluate_experiment(payload)["rows"][0]["code"] == "PREGAME_ELIGIBILITY_UNVERIFIED"

def test_post_start_model_leakage():
    payload = case()
    payload["evidence_by_offer_id"]["offer-0"]["champion"]["created_at_utc"] = "2026-10-01T20:00:00Z"
    assert evaluate_experiment(payload)["rows"][0]["code"] == "CHAMPION_PREGAME_LEAKAGE"

def test_post_start_source_leakage():
    payload = case()
    payload["evidence_by_offer_id"]["offer-0"]["evidence_asof_utc"] = "2026-10-01T20:00:00Z"
    assert evaluate_experiment(payload)["rows"][0]["code"] == "PREGAME_EVIDENCE_LEAKAGE"

def test_market_implied_not_fitted():
    payload = case()
    payload["evidence_by_offer_id"]["offer-0"]["champion"]["origin"] = "MARKET_IMPLIED"
    assert evaluate_experiment(payload)["rows"][0]["code"] == "CHAMPION_NOT_FITTED_SPECIALIST"

def test_missing_calibration_identifier():
    payload = case()
    payload["evidence_by_offer_id"]["offer-0"]["challenger"]["calibrator_id"] = ""
    assert evaluate_experiment(payload)["rows"][0]["code"] == "CHALLENGER_CALIBRATOR_ID_REQUIRED"

def test_lower_bound_missing_fails_closed():
    payload = case()
    del payload["evidence_by_offer_id"]["offer-0"]["challenger"]["calibrated_lower_bound"]
    assert evaluate_experiment(payload)["rows"][0]["code"] == "CHALLENGER_LOWER_BOUND_INVALID"

def test_invalid_probability_fails_closed():
    payload = case()
    payload["evidence_by_offer_id"]["offer-0"]["champion"]["calibrated_probability"] = 1.01
    assert evaluate_experiment(payload)["rows"][0]["code"] == "CHAMPION_CALIBRATED_PROBABILITY_INVALID"

def test_fake_challenger_artifact_fails_closed():
    payload = case()
    payload["evidence_by_offer_id"]["offer-0"]["challenger"]["model_artifact_id"] = "champion-artifact"
    assert evaluate_experiment(payload)["rows"][0]["code"] == "SAME_ARTIFACT_FAKE_CHALLENGER"

def test_reused_model_receipt_held_for_both_rows():
    payload = case(3)
    payload["evidence_by_offer_id"]["offer-1"]["champion"]["prediction_id"] = "champion-0"
    assert evaluate_experiment(payload)["held_codes"]["REUSED_IMMUTABLE_RECEIPT"] == 2

def test_cross_split_event_leakage():
    payload = case(3)
    payload["offers"][2]["event_id"] = "event-1"
    payload["evidence_by_offer_id"]["offer-2"]["settlement"]["event_id"] = "event-1"
    for model in ("champion", "challenger"):
        payload["evidence_by_offer_id"]["offer-2"][model]["event_id"] = "event-1"
    assert evaluate_experiment(payload)["held_codes"]["CROSS_SPLIT_EVENT_LEAKAGE"] == 2

def test_reverse_chronological_split_leakage():
    payload = case(3)
    payload["evidence_by_offer_id"]["offer-0"]["split"] = "HOLDOUT"
    payload["evidence_by_offer_id"]["offer-2"]["split"] = "CALIBRATION"
    assert evaluate_experiment(payload)["held_codes"]["CHRONOLOGICAL_SPLIT_LEAKAGE"] == 3

def test_double_double_separate_route():
    payload = case()
    offer = payload["offers"][0]
    offer.update(stat="DOUBLE_DOUBLE", threshold_label="DOUBLE_DOUBLE",
                 offer_type="COMPOUND", route="NBA_DOUBLE_DOUBLE")
    for name in ("champion", "challenger"):
        payload["evidence_by_offer_id"]["offer-0"][name]["route"] = "NBA_DOUBLE_DOUBLE"
    for field in ("stat", "threshold_label", "offer_type"):
        payload["evidence_by_offer_id"]["offer-0"]["settlement"][field] = offer[field]
        for model in ("champion", "challenger"):
            payload["evidence_by_offer_id"]["offer-0"][model][field] = offer[field]
    assert evaluate_experiment(payload)["paired_research_rows"] == 1
    offer["route"] = "NBA_POINTS"
    with pytest.raises(ExperimentInputError, match="COMPOUND_REQUIRES_SEPARATE_ROUTE"):
        evaluate_experiment(payload)

def test_at_least_cannot_automatically_convert():
    payload = case()
    payload["offers"][0]["threshold_label"] = "6.5"
    with pytest.raises(ExperimentInputError, match="INVALID_LITERAL_AT_LEAST"):
        evaluate_experiment(payload)

def test_adjacent_line_receipt_mismatch():
    payload = case()
    payload["evidence_by_offer_id"]["offer-0"]["challenger"]["offer_id"] = "adjacent-5.5"
    assert evaluate_experiment(payload)["rows"][0]["code"] == "CHALLENGER_EXACT_OFFER_MISMATCH"

def test_wrong_official_settlement_market_cannot_grade():
    for field, wrong in (("entity_id", "OTHER_PLAYER"), ("event_id", "OTHER_GAME"),
                         ("team_id", "OLD_TEAM"), ("opponent_id", "OTHER_OPPONENT"),
                         ("period", "FIRST_HALF"), ("stat", "POINTS"),
                         ("threshold_label", "8+"), ("direction", "LESS"),
                         ("offer_type", "SCALAR")):
        payload = case()
        payload["evidence_by_offer_id"]["offer-0"]["settlement"][field] = wrong
        assert evaluate_experiment(payload)["rows"][0]["code"] == "SETTLEMENT_EXACT_MARKET_MISMATCH"

def test_cross_route_pooled_brier_cannot_promote():
    payload = case(35)
    payload["offers"][3]["route"] = "NBA_REBOUNDS"
    for model in ("champion", "challenger"):
        payload["evidence_by_offer_id"]["offer-3"][model]["route"] = "NBA_REBOUNDS"
    result = evaluate_experiment(payload)
    assert result["certification_disposition"] == "CROSS_ROUTE_AGGREGATION_NOT_CERTIFIABLE"
    assert len(result["holdout_by_sport_route"]) == 2

def test_missing_settlement():
    payload = case()
    payload["evidence_by_offer_id"]["offer-0"]["settlement"] = None
    assert evaluate_experiment(payload)["rows"][0]["code"] == "OFFICIAL_SETTLEMENT_MISSING"

def test_good_challenger_never_auto_promoted():
    r = evaluate_experiment(case(35))
    assert r["holdout_event_count"] == 33
    assert r["certification_disposition"] == "CHALLENGER_REQUIRES_INDEPENDENT_CLASS_C_CERTIFICATION"
    assert r["probability_publishable"] is False

def test_27_original_offers_eight_failure_receipts_nineteen_unscored():
    payload = case(27)
    for i in range(8):
        payload["evidence_by_offer_id"]["offer-%d" % i].update(
            backend_code="MODEL_UNAVAILABLE", backend_receipt_id="failure-%d" % i)
    for i in range(8, 27):
        del payload["evidence_by_offer_id"]["offer-%d" % i]
    r = evaluate_experiment(payload)
    assert r["original_offer_count"] == r["reconciled_offer_count"] == 27
    assert r["paired_research_rows"] == 0
    assert r["held_codes"] == {"MODEL_UNAVAILABLE": 8, "NOT_SCORED": 19}
    assert r["recovery_experiment_candidates"][0]["category"] == "COVERAGE"
    assert r["recovery_experiment_candidates"][0]["auto_dispatch"] is False

def test_preserves_scorer_failure_not_model_unavailable():
    payload = case()
    payload["evidence_by_offer_id"]["offer-0"].update(
        backend_code="MODEL_SCORER_FAILED", backend_receipt_id="receipt-1")
    assert evaluate_experiment(payload)["rows"][0]["code"] == "MODEL_SCORER_FAILED"
    del payload["evidence_by_offer_id"]["offer-0"]["backend_receipt_id"]
    assert evaluate_experiment(payload)["rows"][0]["code"] == "BACKEND_FAILURE_RECEIPT_REQUIRED"

def test_team_opponent_and_period_mandatory_in_original_market():
    for missing in ("team_id", "opponent_id", "period"):
        payload = case()
        del payload["offers"][0][missing]
        with pytest.raises(ExperimentInputError, match="MISSING_OFFER_" + missing.upper()):
            evaluate_experiment(payload)

def test_champion_model_wrong_event_player_team_or_market_held():
    for field, wrong in (
        ("event_id", "different-event"), ("entity_id", "former-player"),
        ("team_id", "OLD_TEAM"), ("opponent_id", "DIFFERENT_OPPONENT"),
        ("stat", "REBOUNDS"), ("period", "FIRST_HALF"),
        ("threshold_label", "8+"), ("direction", "LESS"),
        ("offer_type", "SCALAR"), ("sport", "NFL"),
    ):
        payload = case()
        payload["evidence_by_offer_id"]["offer-0"]["champion"][field] = wrong
        result = evaluate_experiment(payload)
        assert result["rows"][0]["code"] == "CHAMPION_EXACT_OFFER_MISMATCH"
        assert result["paired_research_rows"] == 0

def test_no_immutable_pregame_lock_or_snapshot_held():
    payload = case()
    del payload["evidence_by_offer_id"]["offer-0"]["champion"]["locked_at_utc"]
    assert evaluate_experiment(payload)["rows"][0]["code"] == "CHAMPION_PREGAME_LOCK_REQUIRED"
    payload = case()
    payload["evidence_by_offer_id"]["offer-0"]["challenger"]["source_snapshot_id"] = ""
    assert evaluate_experiment(payload)["rows"][0]["code"] == "CHALLENGER_SOURCE_SNAPSHOT_REQUIRED"

def test_post_start_locked_receipt_is_not_a_pregame_forecast():
    payload = case()
    payload["evidence_by_offer_id"]["offer-0"]["champion"]["locked_at_utc"] = "2026-10-01T22:00:00Z"
    assert evaluate_experiment(payload)["rows"][0]["code"] == "CHAMPION_PREGAME_LEAKAGE"
