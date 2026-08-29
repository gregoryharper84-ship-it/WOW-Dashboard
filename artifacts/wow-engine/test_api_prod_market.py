from datetime import datetime, timedelta, timezone
from types import SimpleNamespace
import os
import uuid

from fastapi.testclient import TestClient

import api_prod_market


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


def _bundle():
    return SimpleNamespace(
        cohort=object(),
        pitcher=object(),
        regime_params={},
        resample_fn=lambda *_args, **_kwargs: None,
        n_eff=87.5,
        parent_cohort="WNBA_REB_TEST",
        settled_n_in_cohort=0,
    )


def _row(*, market_available, market_quality):
    return SimpleNamespace(
        probability_publishable=True,
        data_gaps=[],
        effective_sample_size=87.5,
        simulation_draws=50000,
        regime_model_version="WNBA_PROP_TEST_V1",
        calibration_status="PLATT_TIME_SPLIT_V1",
        calibration_version="CAL_TEST_V1",
        bounds_method_version="PREDICTIVE_BOUNDS_V1",
        calibrated_probability_lower_bound=0.57,
        calibrated_probability_upper_bound=0.68,
        model_timestamp=datetime.now(timezone.utc).isoformat(),
        market_prior_available=market_available,
        market_prior_probability=0.55 if market_available else None,
        market_prior_quality=market_quality,
        market_prior_weight=0.0,
        market_prior_weight_source="COLD_START_ZERO_WEIGHT" if market_available else "NO_MARKET_PRIOR",
        reference_market_probability_raw=0.57 if market_available else None,
        reference_market_side="MORE" if market_available else None,
        reference_market_price=-135 if market_available else None,
        money_lane_status="PAYOUT_UNRESOLVED",
    )


def _install_common(monkeypatch, engine_fn):
    monkeypatch.setattr(
        api_prod_market.prod,
        "_runtime_capability",
        lambda _key: {"capability_status": "AVAILABLE", "evidence": {}, "can_execute": False},
    )
    monkeypatch.setattr(api_prod_market.prod, "_prop_evidence", lambda _req: _ready_evidence())
    monkeypatch.setattr(api_prod_market.prod, "_reject_llp_prop_identity", lambda _identity: "WOW_BETTING_ENGINE")
    monkeypatch.setattr(api_prod_market.prod.base_api, "_controlling_specialist_provider", lambda _sport, _stat: _specialist())
    monkeypatch.setattr(api_prod_market.prod.base_api, "_fitted_params_provider", lambda _sport, _stat: _bundle())
    monkeypatch.setattr(api_prod_market.prod.base_api, "score_prop_end_to_end", engine_fn)
    monkeypatch.setattr(
        api_prod_market.prod.base_api,
        "_persist_fn",
        lambda _row: {"prediction_id": str(uuid.uuid4()), "probability_publishable": True},
    )


def test_complete_model_missing_market_keeps_probability_and_holds_market(monkeypatch):
    captured = {}

    def fake_engine(**kwargs):
        captured.update(kwargs)
        return SimpleNamespace(row=_row(market_available=False, market_quality="NO_QUALIFYING_MARKET"), error=None)

    _install_common(monkeypatch, fake_engine)
    response = client.post("/score-prop", json=_request_payload(), headers=AUTH)

    assert response.status_code == 200
    body = response.json()
    assert body["probability_publishable"] is True
    assert body["objective_lanes"]["MODEL"]["status"] == "PASS"
    assert body["objective_lanes"]["MARKET"]["status"] == "HOLD"
    assert body["objective_lanes"]["MARKET"]["quality"] == "NO_QUALIFYING_MARKET"
    assert body["objective_lanes"]["MARKET"]["blocks_model_probability"] is False
    assert body["objective_lanes"]["MONEY"]["status"] == "HOLD"
    assert body["can_execute"] is False
    assert captured["market_side_a"] is None
    assert captured["market_side_b"] is None


def test_complete_model_exact_two_way_market_passes_market_lane(monkeypatch):
    captured = {}

    def fake_engine(**kwargs):
        captured.update(kwargs)
        return SimpleNamespace(row=_row(market_available=True, market_quality="EXACT_TWO_WAY_NO_VIG"), error=None)

    _install_common(monkeypatch, fake_engine)
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
    assert body["objective_lanes"]["MARKET"]["blocks_model_probability"] is False
    assert body["objective_lanes"]["MONEY"]["status"] == "HOLD"
    assert body["can_execute"] is False
    assert captured["market_side_a"].side == "MORE"
    assert captured["market_side_b"].side == "LESS"
    assert captured["market_side_a"].line == 10.5
    assert captured["market_side_b"].event_id == payload["event_id"]
