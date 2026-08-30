from datetime import datetime, timedelta, timezone
from types import SimpleNamespace
import os
import uuid

from fastapi.testclient import TestClient

import api_prod_market
from prop_distribution_contract import CoverageDecision


TEST_KEY = "test-g11-action-key"
os.environ["WOW_ACTION_API_KEY"] = TEST_KEY
AUTH = {"Authorization": f"Bearer {TEST_KEY}"}
client = TestClient(api_prod_market.app)


def _request_payload():
    start = datetime.now(timezone.utc) + timedelta(days=1)
    return {
        "event_id": "WNBA:TEST:MARKET:1",
        "event_start_time": start.isoformat(),
        "sport": "WNBA",
        "player": "Test Player",
        "stat_type": "REB",
        "line": 10.5,
        "direction": "MORE",
        "source_snapshot_id": str(uuid.uuid4()),
        "money_lane_status": "PAYOUT_UNRESOLVED",
    }


def _ready_evidence():
    now = datetime.now(timezone.utc).isoformat()
    return {
        "ok": True,
        "code": "PROP_EVIDENCE_READY",
        "hydration_status": "PASS",
        "source_snapshot_id": str(uuid.uuid4()),
        "captured_at": now,
        "player": "Test Player",
        "game_log": [10, 12, 11, 9, 14, 8, 13, 12, 10, 15],
        "box_score_log": [{"minutes": 34}] * 10,
        "role_status": {"status": "ACTIVE", "role": "STARTER"},
        "role_timestamp": now,
        "opportunity_ledger": {"status": "PASS"},
        "source_timestamps": {"box_score_log": now},
        "evidence_version": "PROP_EVIDENCE_V1",
        "rate_provenance": "BOX_SCORE_L10_MINUTES_WEIGHTED_PER_MINUTE_V1",
        "probability_publishable": False,
        "can_execute": False,
    }


def _specialist():
    return {
        "sport": "WNBA",
        "canonical_prop_type": "REB",
        "controlling_specialist": "wow.wnba-player-prop-generative-expert",
        "min_event_tree_simulations": 0,
    }


def _ready_route_artifact():
    return {
        "ok": True,
        "code": "PROP_CERTIFIED_MODEL_ARTIFACT_READY",
        "sport": "WNBA",
        "stat_type": "REB",
        "feature_schema_version": "PROP_FEATURES_V1",
        "model_family": "TEST_DISCRETE_V1",
        "model_artifact_version": "WNBA_REB_MODEL_V1",
        "lifecycle_state": "PROSPECTIVE_CERTIFIED",
        "probability_publishable": False,
        "can_execute": False,
    }


def _row(*, market_available, market_quality):
    return SimpleNamespace(
        probability_publishable=True,
        data_gaps=[],
        effective_sample_size=8.5,
        calibration_status="PLATT_TIME_SPLIT_V1",
        calibration_method="PLATT_TIME_SPLIT_V1",
        calibration_version="WNBA_REB_CAL_V1",
        bounds_method_version="PREDICTIVE_BOUNDS_V1",
        calibrated_probability_lower_bound=0.57,
        calibrated_probability_upper_bound=0.68,
        model_timestamp=datetime.now(timezone.utc).isoformat(),
        market_prior_available=market_available,
        market_prior_probability=0.55 if market_available else None,
        market_prior_quality=market_quality,
        market_prior_weight=0.0,
        market_prior_weight_source="OBJECTIVE_SEPARATED_ZERO_WEIGHT" if market_available else "NO_MARKET_PRIOR",
        reference_market_probability_raw=0.57 if market_available else None,
        reference_market_side="MORE" if market_available else None,
        reference_market_price=-135 if market_available else None,
        money_lane_status="PAYOUT_UNRESOLVED",
        model_provider_identity="WOW_PROP_FITTED_MODEL_V1",
        model_family="TEST_DISCRETE_V1",
        model_artifact_version="WNBA_REB_MODEL_V1",
        model_artifact_checksum="c" * 64,
        model_bundle_fingerprint="f" * 64,
        model_artifact_lifecycle_state="PROSPECTIVE_CERTIFIED",
        feature_schema_version="PROP_FEATURES_V1",
        feature_transform_version="PROP_TRANSFORM_V1",
        specialist_version="wow.wnba-player-prop-generative-expert@1",
        certification_id="CERT-TEST",
        distribution_type="DISCRETE_PMF",
        probability_more=0.62,
        probability_less=0.38,
        push_probability=0.0,
    )


def _result(*, market_available, market_quality):
    row = _row(market_available=market_available, market_quality=market_quality)
    artifact = SimpleNamespace(training_rows=1200)
    distribution = SimpleNamespace(coverage=CoverageDecision(True, 0.1, ()))
    return SimpleNamespace(
        row=row,
        inference=SimpleNamespace(artifact=artifact, distribution=distribution),
    )


def _install_common(monkeypatch, score_fn):
    monkeypatch.setattr(
        api_prod_market.prod,
        "_runtime_capability",
        lambda _key: {"capability_status": "AVAILABLE", "evidence": {}, "can_execute": False},
    )
    monkeypatch.setattr(api_prod_market.prod, "_prop_evidence", lambda _req: _ready_evidence())
    monkeypatch.setattr(api_prod_market.prod, "_reject_llp_prop_identity", lambda _identity: "WOW_BETTING_ENGINE")
    monkeypatch.setattr(api_prod_market.prod.base_api, "_controlling_specialist_provider", lambda _sport, _stat: _specialist())
    monkeypatch.setattr(api_prod_market, "_prop_route_artifact", lambda _sport, _stat: _ready_route_artifact())
    monkeypatch.setattr(api_prod_market.prod, "get_client", lambda: object())
    monkeypatch.setattr(api_prod_market, "score_discrete_prop_end_to_end", score_fn)
    monkeypatch.setattr(
        api_prod_market.prod.base_api,
        "_persist_fn",
        lambda _row: {"prediction_id": str(uuid.uuid4()), "probability_publishable": True},
    )


def test_complete_model_missing_market_keeps_probability_and_holds_market(monkeypatch):
    captured = {}

    def fake_score(**kwargs):
        captured.update(kwargs)
        return _result(market_available=False, market_quality="NO_QUALIFYING_MARKET")

    _install_common(monkeypatch, fake_score)
    response = client.post("/score-prop", json=_request_payload(), headers=AUTH)

    assert response.status_code == 200
    body = response.json()
    assert body["probability_publishable"] is True
    assert body["objective_lanes"]["MODEL"]["status"] == "PASS"
    assert body["objective_lanes"]["MARKET"]["status"] == "HOLD"
    assert body["objective_lanes"]["MARKET"]["quality"] == "NO_QUALIFYING_MARKET"
    assert body["objective_lanes"]["MARKET"]["blocks_model_probability"] is False
    assert body["objective_lanes"]["MONEY"]["status"] == "HOLD"
    assert body["model_path"].startswith("WOW_PROP_FITTED_MODEL_V1")
    assert body["model_evidence"]["distribution_type"] == "DISCRETE_PMF"
    assert body["backend_traversal"]["exact_route_artifact"] == "PASS"
    assert body["can_execute"] is False
    assert captured["market_side_a"] is None
    assert captured["market_side_b"] is None
    assert captured["request"].feature_schema_version == "PROP_FEATURES_V1"
    assert captured["request"].market_identity_id.startswith("wow-market:")


def test_complete_model_exact_two_way_market_passes_market_lane(monkeypatch):
    captured = {}

    def fake_score(**kwargs):
        captured.update(kwargs)
        return _result(market_available=True, market_quality="EXACT_TWO_WAY_NO_VIG")

    _install_common(monkeypatch, fake_score)
    payload = _request_payload()
    now = datetime.now(timezone.utc).isoformat()
    common = {
        "line": 10.5,
        "settlement_basis": "FULL_GAME_PLAYER_STAT",
        "retrieved_at": now,
        "participant": "Test Player",
        "stat": "REB",
        "period": "FULL_GAME",
        "event_id": payload["event_id"],
    }
    payload["market_side_a"] = {**common, "side": "MORE", "american_odds": -135}
    payload["market_side_b"] = {**common, "side": "LESS", "american_odds": 105}

    response = client.post("/score-prop", json=payload, headers=AUTH)

    assert response.status_code == 200
    body = response.json()
    assert body["probability_publishable"] is True
    assert body["objective_lanes"]["MODEL"]["status"] == "PASS"
    assert body["objective_lanes"]["MARKET"]["status"] == "PASS"
    assert body["objective_lanes"]["MARKET"]["quality"] == "EXACT_TWO_WAY_NO_VIG"
    assert body["objective_lanes"]["MARKET"]["market_prior_weight"] == 0.0
    assert body["objective_lanes"]["MARKET"]["blocks_model_probability"] is False
    assert body["objective_lanes"]["MONEY"]["status"] == "HOLD"
    assert body["can_execute"] is False
    assert captured["market_side_a"].side == "MORE"
    assert captured["market_side_b"].side == "LESS"
    assert captured["market_side_a"].line == 10.5
    assert captured["market_side_b"].event_id == payload["event_id"]


def test_runtime_has_no_legacy_fitted_params_fallback(monkeypatch):
    def blocked_score(**_kwargs):
        from prop_discrete_engine import PropCalibrationUnavailable
        raise PropCalibrationUnavailable(
            "PROP_CALIBRATOR_ADAPTER_UNAVAILABLE",
            "reviewed calibrator adapter missing",
        )

    _install_common(monkeypatch, blocked_score)
    response = client.post("/score-prop", json=_request_payload(), headers=AUTH)
    assert response.status_code == 409
    body = response.json()["detail"]
    assert body["code"] == "PROP_CALIBRATOR_ADAPTER_UNAVAILABLE"
    assert "RAW_DISCRETE_DISTRIBUTION" in body["model_path"]
    assert body["probability_publishable"] is False
    assert body["can_execute"] is False


def test_missing_exact_route_artifact_blocks_before_model_invocation(monkeypatch):
    called = {"score": False}

    def should_not_score(**_kwargs):
        called["score"] = True
        raise AssertionError("model must not run without exact certified route")

    _install_common(monkeypatch, should_not_score)
    monkeypatch.setattr(
        api_prod_market,
        "_prop_route_artifact",
        lambda _sport, _stat: {
            "ok": False,
            "code": "PROP_CERTIFIED_MODEL_ARTIFACT_NOT_FOUND",
            "probability_publishable": False,
            "can_execute": False,
        },
    )

    response = client.post("/score-prop", json=_request_payload(), headers=AUTH)

    assert response.status_code == 409
    body = response.json()["detail"]
    assert body["code"] == "MODEL_UNAVAILABLE"
    assert body["blocker_code"] == "PROP_CERTIFIED_MODEL_ARTIFACT_NOT_FOUND"
    assert body["aggregate_prop_capability_status"] == "AVAILABLE"
    assert body["requested_route"] == {
        "sport": "WNBA",
        "stat_type": "REB",
        "feature_schema_version": "PROP_FEATURES_V1",
    }
    assert body["backend_traversal"]["exact_route_artifact"] == "BLOCKED"
    assert body["backend_traversal"]["governed_model"] == "NOT_INVOKED"
    assert body["probability_publishable"] is False
    assert body["can_execute"] is False
    assert called["score"] is False
