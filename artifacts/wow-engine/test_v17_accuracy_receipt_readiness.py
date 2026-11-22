"""Adversarial offline fixtures for canonical V17 receipt-lookup import readiness."""
from copy import deepcopy

import pytest

from v17.accuracy_receipt_readiness import (
    CAN_EXECUTE, ReceiptReadinessError, inspect_lookup_readiness,
)


def fixture():
    offer = {
        "offer_id": "row-1", "original_label": "Davion Mitchell 6.5 Assists",
        "event_id": "event-1", "entity_id": "canonical-athlete-1",
        "canonical_player_name": "Davion Mitchell",
        "team_id": "MIA", "opponent_id": "BOS", "sport": "NBA",
        "stat": "ASSISTS", "period": "FULL_GAME", "threshold_label": "6.5",
        "direction": "MORE", "offer_type": "SCALAR", "route": "NBA_ASSISTS",
        "event_start_utc": "2026-10-26T23:00:00Z",
    }
    prediction = {
        "prediction_id": "genuine-id-not-proven-by-fixture",
        "event_id": "event-1", "player": "Davion Mitchell",
        "team": "MIA", "opponent": "BOS", "sport": "NBA",
        "stat_type": "ASSISTS", "line": 6.5, "direction": "MORE",
        "event_start_time": "2026-10-26T23:00:00Z",
        "created_at": "2026-10-26T21:00:00Z",
        "locked_at": "2026-10-26T22:00:00Z",
        "model_timestamp": "2026-10-26T21:00:00Z",
        "source_snapshot_id": "source-1",
    }
    match = {
        "can_execute": False, "governed_prediction_table": "wow_predictions",
        "governed_prediction_id": prediction["prediction_id"],
        "is_immutable_pregame": True, "prediction": prediction,
        "calibrated_probability": 0.61, "calibrated_lower_bound": 0.53,
    }
    response = {
        "rows_in": 1, "reconciliation_pass": True, "can_execute": False,
        "rows": [{
            "row_key": "row-1", "status": "MATCHED",
            "code": "IMMUTABLE_PREGAME_PREDICTION_MATCHED",
            "match_count": 1, "matches": [match], "can_execute": False,
        }],
    }
    return [offer], response


def code(offers, response):
    return inspect_lookup_readiness(offers, response)["rows"][0]["code"]


def test_real_current_projection_lacks_period_and_cannot_certify():
    offers, response = fixture()
    report = inspect_lookup_readiness(offers, response)
    assert report["original_offer_count"] == report["reconciled_offer_count"] == 1
    assert report["source_authentication_proven"] is False
    assert report["probability_publishable"] is False
    assert report["can_execute"] is False and CAN_EXECUTE is False
    assert report["rows"][0]["code"] == "PREDICTION_PERIOD_BINDING_MISSING_OR_CONFLICT"


def test_artifact_linkage_missing_no_fabricated_certified_model():
    offers, response = fixture()
    response["rows"][0]["matches"][0]["prediction"]["period"] = "FULL_GAME"
    assert code(offers, response) == "FITTED_SPECIALIST_ARTIFACT_LINKAGE_MISSING"


def test_complete_offline_fixture_cannot_claim_authenticated_receipt():
    offers, response = fixture()
    row = response["rows"][0]["matches"][0]["prediction"]
    row.update(period="FULL_GAME", model_artifact_id="model-1",
               calibrator_id="cal-1", specialist_id="NBA_FITTED_SPECIALIST")
    result = inspect_lookup_readiness(offers, response)
    assert result["source_authentication_proven"] is False
    assert result["rows"][0]["status"] == "HELD"
    assert result["rows"][0]["code"] == "OFFLINE_RECEIPT_FIELDS_RECONCILED_SOURCE_UNAUTHENTICATED"


@pytest.mark.parametrize("field,value", [
    ("event_id", "wrong-event"), ("player", "wrong-player"),
    ("team", "former-team"), ("opponent", "different-opponent"),
    ("stat_type", "REBOUNDS"), ("direction", "LESS"),
])
def test_mismatch_in_immutable_prediction_fails_closed(field, value):
    offers, response = fixture()
    response["rows"][0]["matches"][0]["prediction"][field] = value
    assert code(offers, response) == "PREDICTION_EXACT_IDENTITY_CONFLICT"


def test_current_at_least_threshold_requires_certified_adapter():
    offers, response = fixture()
    offers[0].update(offer_type="AT_LEAST", threshold_label="7+")
    assert code(offers, response) == "AT_LEAST_CERTIFIED_LINE_ADAPTER_REQUIRED"


def test_compound_market_requires_independent_route():
    offers, response = fixture()
    offers[0].update(offer_type="COMPOUND", stat="DOUBLE_DOUBLE",
                     threshold_label="DOUBLE_DOUBLE", route="NBA_DOUBLE_DOUBLE")
    response["rows"][0]["matches"][0]["prediction"]["stat_type"] = "DOUBLE_DOUBLE"
    assert code(offers, response) == "COMPOUND_CERTIFIED_RECEIPT_ADAPTER_REQUIRED"


def test_no_canonical_identity_proof_not_recovered_from_display_label():
    offers, response = fixture()
    del offers[0]["canonical_player_name"]
    assert code(offers, response) == "CANONICAL_PLAYER_LOOKUP_BINDING_MISSING"


def test_unlocked_prediction_not_accepted():
    offers, response = fixture()
    response["rows"][0]["matches"][0]["is_immutable_pregame"] = False
    assert code(offers, response) == "IMMUTABLE_PREGAME_NOT_PROVEN"


def test_mutated_prediction_id_not_accepted():
    offers, response = fixture()
    response["rows"][0]["matches"][0]["prediction"]["prediction_id"] = "another-id"
    assert code(offers, response) == "PREDICTION_IDENTITY_CONFLICT"


def test_batch_partial_lookup_reconciles_every_original_offer():
    offers, response = fixture()
    additional = deepcopy(offers[0])
    additional["offer_id"] = "row-2"
    additional["original_label"] = "Second exact offer"
    offers.append(additional)
    response["rows_in"] = 2
    result = inspect_lookup_readiness(offers, response)
    assert result["reconciled_offer_count"] == 2
    assert result["rows"][1]["code"] == "LOOKUP_ROW_NOT_RETURNED"


def test_lookup_duplicate_row_or_failed_batch_rejected():
    offers, response = fixture()
    response["rows"].append(deepcopy(response["rows"][0]))
    with pytest.raises(ReceiptReadinessError, match="LOOKUP_DUPLICATE_ROW_KEY"):
        inspect_lookup_readiness(offers, response)
    offers, response = fixture()
    response["reconciliation_pass"] = False
    with pytest.raises(ReceiptReadinessError, match="LOOKUP_BATCH_RECONCILIATION_FAILED"):
        inspect_lookup_readiness(offers, response)


def test_nonmatched_backend_code_retained_and_not_reinterpreted():
    offers, response = fixture()
    response["rows"][0].update(
        status="BLOCKED", code="PREDICTION_RECEIPT_IDENTITY_CONFLICT", matches=[], match_count=0,
    )
    assert code(offers, response) == "PREDICTION_RECEIPT_IDENTITY_CONFLICT"


def test_no_execution_fence_is_global_failure():
    offers, response = fixture()
    response["can_execute"] = True
    with pytest.raises(ReceiptReadinessError, match="LOOKUP_EXECUTION_FENCE_NOT_PROVEN"):
        inspect_lookup_readiness(offers, response)
