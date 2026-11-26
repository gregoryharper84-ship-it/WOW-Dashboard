"""Authenticated persistence readback is required; echoed ids are not enough."""
from __future__ import annotations

from v17.news_fitted_persistence_readback import (
    missing_news_persistence_readback,
    validate_news_persistence_readback,
)


REQUEST = {
    "sport_key": "americanfootball_nfl",
    "official_event_id": "2026112701",
    "controlling_specialist_route": "LLP_TEAM_BETTING_ENGINE",
    "feature_snapshot_hash": "a" * 64,
    "prior_immutable_prediction_id": "pred-old",
    "prop_target": None,
}
RESPONSE = {
    "immutable_prediction_id": "pred-new",
    "prior_immutable_prediction_id": "pred-old",
    "calibration_receipt_id": "calib-1",
    "model_version": "nfl-fixture-v1",
    "feature_snapshot_hash": "a" * 64,
    "calibrated_probability": 0.57,
    "calibrated_lower_bound": 0.49,
}


def _store(**overrides):
    row = {
        "ok": True,
        "authenticated": True,
        "immutable_prediction_id": "pred-new",
        "prior_immutable_prediction_id": "pred-old",
        "prior_immutable_prediction_preserved": True,
        "calibration_receipt_id": "calib-1",
        "official_event_id": "2026112701",
        "controlling_specialist_route": "LLP_TEAM_BETTING_ENGINE",
        "feature_snapshot_hash": "a" * 64,
        "model_version": "nfl-fixture-v1",
        "calibrated_probability": 0.57,
        "calibrated_lower_bound": 0.49,
        "prop_target": None,
        "terminal_readback": {
            "authority": "V17_TERMINAL_REDUCER",
            "status": "HOLD_RESEARCH_RESCORE",
            "can_execute": False,
            "immutable_prediction_id": "pred-new",
            "calibration_receipt_id": "calib-1",
            "feature_snapshot_hash": "a" * 64,
        },
        "store_attestation": {
            "source": "TEST_DOUBLE_NOT_PRODUCTION",
            "read_at_utc": "2026-11-26T00:00:00+00:00",
        },
        "can_execute": False,
    }
    row.update(overrides)
    return row


def test_default_binding_is_unauthenticated_store_hold():
    out = missing_news_persistence_readback(REQUEST, RESPONSE)
    assert out["ok"] is False
    assert out["authenticated"] is False
    assert "NEWS_PERSISTENCE_STORE_NOT_BOUND" in out["blockers"]


def test_validate_accepts_authenticated_store_preserving_prior():
    normalized, status, blockers = validate_news_persistence_readback(
        REQUEST, RESPONSE, _store(),
    )
    assert status is None
    assert blockers == []
    assert normalized["immutable_prediction_id"] == "pred-new"
    assert normalized["prior_immutable_prediction_id"] == "pred-old"
    assert normalized["prior_immutable_prediction_preserved"] is True
    assert normalized["terminal_readback"]["authority"] == "V17_TERMINAL_REDUCER"
    assert normalized["terminal_readback"]["can_execute"] is False
    assert normalized["can_execute"] is False


def test_omitted_scorer_prior_fails_when_authoritative_prior_exists():
    response = dict(RESPONSE)
    del response["prior_immutable_prediction_id"]
    _, status, blockers = validate_news_persistence_readback(
        REQUEST, response, _store(),
    )
    assert status == "NEWS_RESCORE_PRIOR_RECEIPT_NOT_PRESERVED"
    assert "PRIOR_IMMUTABLE_ID_OMITTED_BY_SCORER" in blockers


def test_missing_store_event_id_fails_closed():
    _, status, blockers = validate_news_persistence_readback(
        REQUEST, RESPONSE, _store(official_event_id=None),
    )
    assert status == "NEWS_RESCORE_PERSISTENCE_MISMATCH"
    assert "STORE_OFFICIAL_EVENT_ID_MISSING" in blockers


def test_terminal_can_execute_must_be_explicit_false():
    for bad in (None, True, "false", 0):
        terminal = {
            "authority": "V17_TERMINAL_REDUCER",
            "status": "HOLD_RESEARCH_RESCORE",
            "can_execute": bad,
        }
        _, status, blockers = validate_news_persistence_readback(
            REQUEST, RESPONSE, _store(terminal_readback=terminal),
        )
        assert status == "NEWS_RESCORE_TERMINAL_READBACK_INVALID"
        assert "TERMINAL_CAN_EXECUTE_NOT_FALSE" in blockers


def test_store_row_must_match_feature_model_calibration_and_terminal_linkage():
    _, status, blockers = validate_news_persistence_readback(
        REQUEST, RESPONSE, _store(feature_snapshot_hash="b" * 64),
    )
    assert status == "NEWS_RESCORE_PERSISTENCE_MISMATCH"
    assert "FEATURE_HASH_STORE_MISMATCH" in blockers

    _, status, blockers = validate_news_persistence_readback(
        REQUEST, RESPONSE, _store(model_version="wrong-model"),
    )
    assert status == "NEWS_RESCORE_PERSISTENCE_MISMATCH"
    assert "MODEL_VERSION_STORE_MISMATCH" in blockers

    _, status, blockers = validate_news_persistence_readback(
        REQUEST, RESPONSE, _store(calibrated_probability=0.61),
    )
    assert status == "NEWS_RESCORE_PERSISTENCE_MISMATCH"
    assert "CALIBRATED_PROBABILITY_STORE_MISMATCH" in blockers

    terminal = {
        "authority": "V17_TERMINAL_REDUCER",
        "status": "HOLD_RESEARCH_RESCORE",
        "can_execute": False,
        "immutable_prediction_id": "pred-other",
        "calibration_receipt_id": "calib-1",
        "feature_snapshot_hash": "a" * 64,
    }
    _, status, blockers = validate_news_persistence_readback(
        REQUEST, RESPONSE, _store(terminal_readback=terminal),
    )
    assert status == "NEWS_RESCORE_PERSISTENCE_MISMATCH"
    assert "TERMINAL_PREDICTION_ID_MISMATCH" in blockers


def test_prior_is_bound_from_store_not_scorer_assertion_alone():
    # Scorer claims a prior that the store does not preserve.
    _, status, blockers = validate_news_persistence_readback(
        REQUEST,
        RESPONSE,
        _store(
            prior_immutable_prediction_id=None,
            prior_immutable_prediction_preserved=False,
        ),
    )
    assert status == "NEWS_RESCORE_PRIOR_RECEIPT_NOT_PRESERVED"
    assert "PRIOR_IMMUTABLE_ID_NOT_IN_STORE" in blockers
