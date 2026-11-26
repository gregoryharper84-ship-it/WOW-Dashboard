"""Authenticated persistence readback is required; echoed ids are not enough."""
from __future__ import annotations

from v17.news_fitted_persistence_readback import (
    missing_news_persistence_readback,
    validate_news_persistence_readback,
)


REQUEST = {
    "sport_key": "americanfootball_nfl",
    "official_event_id": "2026112701",
}
RESPONSE = {
    "immutable_prediction_id": "pred-new",
    "prior_immutable_prediction_id": "pred-old",
    "calibration_receipt_id": "calib-1",
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
        "terminal_readback": {
            "authority": "V17_TERMINAL_REDUCER",
            "status": "HOLD_RESEARCH_RESCORE",
            "can_execute": False,
        },
        "store_attestation": {"source": "TEST_DOUBLE_NOT_PRODUCTION", "read_at_utc": "2026-11-26T00:00:00+00:00"},
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
    assert normalized["terminal_readback"]["authority"] == "V17_TERMINAL_REDUCER"
    assert normalized["can_execute"] is False


def test_validate_rejects_echo_without_attestation_or_prior_preservation():
    _, status, blockers = validate_news_persistence_readback(
        REQUEST, RESPONSE, _store(store_attestation=None),
    )
    assert status == "NEWS_RESCORE_PERSISTENCE_UNVERIFIED"
    assert "STORE_ATTESTATION_MISSING" in blockers

    _, status, blockers = validate_news_persistence_readback(
        REQUEST,
        RESPONSE,
        _store(prior_immutable_prediction_preserved=False),
    )
    assert status == "NEWS_RESCORE_PRIOR_RECEIPT_NOT_PRESERVED"
