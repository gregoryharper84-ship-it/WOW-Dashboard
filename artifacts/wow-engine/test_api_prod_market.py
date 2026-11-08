from datetime import datetime, timedelta, timezone
from types import SimpleNamespace
import logging
import os
import uuid

import pytest
from fastapi.testclient import TestClient

import api_prod_market
from pick_request_runtime_core import PROP_STAT_ALIASES
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
    # Force the governed writer's WNBA alias path regardless of which test
    # modules happened to import the runtime alias registration first.
    """Prove exact two-way quotes pass the market lane without changing model probability."""
    monkeypatch.setitem(PROP_STAT_ALIASES, ("WNBA", "REB"), "REBOUNDS")
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
    assert captured["request"].stat_type == "REBOUNDS"
    assert captured["market_side_a"].stat == "REBOUNDS"
    assert captured["market_side_b"].stat == "REBOUNDS"
    assert captured["market_side_a"].side == "MORE"
    assert captured["market_side_b"].side == "LESS"
    assert captured["market_side_a"].line == 10.5
    assert captured["market_side_b"].event_id == payload["event_id"]


def test_mismatched_quote_identities_hold_market_but_preserve_model_probability(monkeypatch):
    """Reject both quote sides when either side's event or participant differs."""
    # Pin the writer alias path so this test does not depend on module order.
    monkeypatch.setitem(PROP_STAT_ALIASES, ("WNBA", "REB"), "REBOUNDS")
    captured = {}

    def fake_score(**kwargs):
        captured.update(kwargs)
        return _result(market_available=False, market_quality="NO_QUALIFYING_MARKET")

    _install_common(monkeypatch, fake_score)
    payload = _request_payload()
    now = datetime.now(timezone.utc).isoformat()
    common = {
        "line": 10.5,
        "settlement_basis": "FULL_GAME_PLAYER_STAT",
        "retrieved_at": now,
        "stat": "REB",
        "period": "FULL_GAME",
    }
    payload["market_side_a"] = {
        **common,
        "side": "MORE",
        "american_odds": -135,
        "participant": "Test Player",
        "event_id": payload["event_id"],
    }
    payload["market_side_b"] = {
        **common,
        "side": "LESS",
        "american_odds": 105,
        "participant": "Other Player",
        "event_id": "WNBA:TEST:MARKET:2",
    }

    response = client.post("/score-prop", json=payload, headers=AUTH)

    assert response.status_code == 200
    body = response.json()
    assert body["probability_publishable"] is True
    assert body["objective_lanes"]["MODEL"]["status"] == "PASS"
    assert body["objective_lanes"]["MARKET"]["status"] == "HOLD"
    assert body["objective_lanes"]["MARKET"]["candidate_audit_status"] == "HOLD"
    assert body["objective_lanes"]["MARKET"]["candidate_audit_blocker"] == (
        "WOW_HOLD_MARKET_IDENTITY_MISMATCH"
    )
    assert body["objective_lanes"]["MARKET"]["blocks_model_probability"] is False
    assert body["can_execute"] is False
    assert captured["market_side_a"] is None
    assert captured["market_side_b"] is None
    assert captured["request"].stat_type == "REBOUNDS"


def test_python_verification_conflict_holds_economics_without_erasing_model_probability(monkeypatch):
    def fake_score(**_kwargs):
        return _result(market_available=True, market_quality="EXACT_TWO_WAY_NO_VIG")

    _install_common(monkeypatch, fake_score)
    monkeypatch.setattr(api_prod_market, "wolfram_audit_enabled", lambda: True)
    monkeypatch.setattr(
        api_prod_market,
        "persist_wolfram_audit",
        lambda _client, prediction_id, audit: {
            "arithmetic_audit_id": str(uuid.uuid4()),
            "prediction_id": prediction_id,
            "audit_payload_hash": "a" * 64,
        },
    )
    monkeypatch.setattr(
        api_prod_market,
        "audit_wolfram_claims",
        lambda _claims, required: {
            "verdict": "COMPUTATION_VERIFICATION_CONFLICT",
            "provider": "PYTHON_PRIMARY",
            "audit_required": required,
            "receipts": [],
            "blocks_model_probability": False,
            "can_execute": False,
        },
    )
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
    assert body["objective_lanes"]["MARKET"]["status"] == "HOLD"
    assert body["objective_lanes"]["MONEY"]["status"] == "HOLD"
    assert body["objective_lanes"]["MONEY"]["money_lane_status"] == "COMPUTATION_VERIFICATION_CONFLICT"
    assert body["objective_lanes"]["ARITHMETIC_AUDIT"]["verdict"] == "COMPUTATION_VERIFICATION_CONFLICT"
    assert body["objective_lanes"]["ARITHMETIC_AUDIT"]["provider"] == "PYTHON_PRIMARY"
    assert body["objective_lanes"]["ARITHMETIC_AUDIT"]["blocks_model_probability"] is False
    assert body["backend_traversal"]["wolfram_arithmetic_audit"] == "COMPUTATION_VERIFICATION_CONFLICT"
    assert body["backend_traversal"]["wolfram_audit_ledger_write"] == "PASS"
    assert body["can_execute"] is False


def test_python_pass_allows_existing_market_lane_semantics(monkeypatch):
    row = _row(market_available=True, market_quality="EXACT_TWO_WAY_NO_VIG")
    market_lane = {"status": "PASS", "blocks_model_probability": False}
    settlement_lane = {"status": "HOLD", "blocks_model_probability": False}
    money_lane = {"status": "HOLD", "money_lane_status": "PAYOUT_UNRESOLVED", "blocks_model_probability": False}
    market_audit = SimpleNamespace(side_a=None, side_b=None)
    monkeypatch.setattr(api_prod_market, "wolfram_audit_enabled", lambda: True)
    monkeypatch.setattr(api_prod_market.prod, "get_client", lambda: object())
    monkeypatch.setattr(
        api_prod_market,
        "persist_wolfram_audit",
        lambda _client, prediction_id, audit: {
            "arithmetic_audit_id": str(uuid.uuid4()),
            "prediction_id": prediction_id,
            "audit_payload_hash": "b" * 64,
        },
    )
    monkeypatch.setattr(
        api_prod_market,
        "audit_wolfram_claims",
        lambda claims, required: {
            "verdict": "PASS",
            "provider": "PYTHON_PRIMARY",
            "audit_required": required,
            "receipts": [{"claim_id": claim["claim_id"], "verdict": "PASS"} for claim in claims],
            "blocks_model_probability": False,
            "can_execute": False,
        },
    )

    market, settlement, money, audit = api_prod_market._apply_wolfram_arithmetic_gate(
        row=row,
        direction="MORE",
        market_audit=market_audit,
        market_lane=market_lane,
        settlement_lane=settlement_lane,
        money_lane=money_lane,
        prediction_id=str(uuid.uuid4()),
    )

    assert audit["verdict"] == "PASS"
    assert market["status"] == "PASS"
    assert market["arithmetic_verified"] is True
    assert settlement["status"] == "HOLD"
    assert money["status"] == "HOLD"


def test_python_ledger_failure_holds_economics_but_preserves_provider_verdict(monkeypatch):
    row = _row(market_available=True, market_quality="EXACT_TWO_WAY_NO_VIG")
    monkeypatch.setattr(api_prod_market, "wolfram_audit_enabled", lambda: True)
    monkeypatch.setattr(
        api_prod_market,
        "audit_wolfram_claims",
        lambda _claims, required: {
            "verdict": "PASS",
            "provider": "PYTHON_PRIMARY",
            "audit_required": required,
            "receipts": [],
            "blocks_model_probability": False,
            "can_execute": False,
        },
    )
    monkeypatch.setattr(
        api_prod_market,
        "persist_wolfram_audit",
        lambda *_args, **_kwargs: (_ for _ in ()).throw(RuntimeError("database unavailable")),
    )

    market, _settlement, money, audit = api_prod_market._apply_wolfram_arithmetic_gate(
        row=row,
        direction="MORE",
        market_audit=SimpleNamespace(side_a=None, side_b=None),
        market_lane={"status": "PASS", "blocks_model_probability": False},
        settlement_lane={"status": "PASS", "blocks_model_probability": False},
        money_lane={"status": "PASS", "money_lane_status": "RESOLVED", "blocks_model_probability": False},
        prediction_id=str(uuid.uuid4()),
    )

    assert audit["verdict"] == "PYTHON_ARITHMETIC_AUDIT_LEDGER_WRITE_UNPROVEN"
    assert audit["provider_verdict"] == "PASS"
    assert audit["ledger_write"] == "UNPROVEN"
    assert market["status"] == "HOLD"
    assert money["status"] == "HOLD"
    assert audit["blocks_model_probability"] is False


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


def test_model_features_passes_through_opponent_context_verbatim():
    evidence = {
        "game_log": [1, 2],
        "box_score_log": [{"outs": 3}],
        "role_status": {"status": "ACTIVE"},
        "role_timestamp": "2026-08-29T00:00:00+00:00",
        "opportunity_ledger": {"status": "PASS"},
        "source_timestamps": {"caller": "2026-08-29T00:00:00+00:00"},
        "evidence_version": "PROP_EVIDENCE_V1",
        "rate_provenance": "TEST",
        "captured_at": "2026-08-29T00:00:00+00:00",
        "opponent_context": {"k_rate_per_pa": 0.15},
    }
    features = api_prod_market._model_features(evidence)
    assert features["opponent_context"] == {"k_rate_per_pa": 0.15}


def test_model_features_opponent_context_defaults_to_none_when_absent():
    features = api_prod_market._model_features({"game_log": [], "box_score_log": []})
    assert features["opponent_context"] is None


def test_discrete_model_evidence_surfaces_failure_path_evidence():
    distribution = SimpleNamespace(
        coverage=CoverageDecision(in_distribution=True, ood_score=0.1, coverage_failures=()),
        failure_path_evidence={"tags": ["STRIKEOUT_RATE_SUPPRESSION"], "opponent_factor": 0.8},
    )
    result = SimpleNamespace(
        row=SimpleNamespace(model_family="MLB_PITCHER_SO_FAILURE_PATH_NB_V1", probability_publishable=True),
        inference=SimpleNamespace(
            artifact=SimpleNamespace(training_rows=4489),
            distribution=distribution,
        ),
    )
    evidence = api_prod_market._discrete_model_evidence(result)
    assert evidence["failure_path_evidence"] == {"tags": ["STRIKEOUT_RATE_SUPPRESSION"], "opponent_factor": 0.8}
    assert evidence["can_execute"] is False


def test_discrete_model_evidence_defaults_failure_path_evidence_to_empty():
    distribution = SimpleNamespace(
        coverage=CoverageDecision(in_distribution=True, ood_score=0.1, coverage_failures=()),
    )
    result = SimpleNamespace(
        row=SimpleNamespace(model_family="WNBA_PROP_POISSON_LOGGLM_V1", probability_publishable=True),
        inference=SimpleNamespace(
            artifact=SimpleNamespace(training_rows=1000),
            distribution=distribution,
        ),
    )
    evidence = api_prod_market._discrete_model_evidence(result)
    assert evidence["failure_path_evidence"] == {}


def test_score_stage_timer_preserves_exception_and_redacts_payload(caplog):
    secret = "DO_NOT_LOG_PLAYER_OR_MARKET_PAYLOAD"
    with pytest.raises(RuntimeError, match=secret):
        with api_prod_market._score_stage_timer(
            "evidence_revalidation",
            sport="NFL",
            stat_type="RUSHING_YARDS",
            direction="MORE",
        ):
            raise RuntimeError(secret)

    log_text = "\n".join(record.getMessage() for record in caplog.records)
    assert "WOW_V17_PROP_SCORE_STAGE" in log_text
    assert "sport=NFL" in log_text
    assert "stat_type=RUSHING_YARDS" in log_text
    assert "direction=MORE" in log_text
    assert "stage=evidence_revalidation" in log_text
    assert "status=FAILED" in log_text
    assert "can_execute=false" in log_text
    assert secret not in log_text


def test_primary_evidence_http_exception_is_logged_and_preserved(monkeypatch, caplog):
    """A primary evidence HTTP failure is logged with correlation and re-raised unchanged."""
    _install_common(monkeypatch, lambda **_: None)

    for status_code, code in (
        (422, "PROP_PLAYER_IDENTITY_REQUIRED"),
        (503, "PROP_EVIDENCE_BRIDGE_UNAVAILABLE"),
    ):
        def raise_primary(
            _req, *_args, _status=status_code, _code=code, **_kwargs
        ):
            raise api_prod_market.HTTPException(
                status_code=_status,
                detail={
                    "code": _code,
                    "probability_publishable": False,
                    "can_execute": False,
                },
            )

        monkeypatch.setattr(api_prod_market, "repair_prop_evidence", raise_primary)
        caplog.clear()
        with caplog.at_level(logging.WARNING, logger="wow.v17.prop_evidence.readback"):
            response = client.post("/score-prop", json=_request_payload(), headers=AUTH)

        assert response.status_code == status_code
        assert response.json()["detail"]["code"] == code
        assert "WOW_V17_PROP_EVIDENCE_FAILURE" in caplog.text
        assert f"code={code}" in caplog.text
        assert "can_execute=false" in caplog.text
        assert "Test Player" not in caplog.text


def test_score_stage_timer_success_is_timing_only(caplog):
    with api_prod_market._score_stage_timer(
        "prediction_persistence",
        sport="NFL",
        stat_type="RUSHING_YARDS",
        direction="LESS",
    ):
        prediction_id = "private-prediction-id"
    assert prediction_id

    log_text = "\n".join(record.getMessage() for record in caplog.records)
    assert "stage=prediction_persistence" in log_text
    assert "status=PASS" in log_text
    assert "private-prediction-id" not in log_text


# --- /v17/prop-evidence-readback HTTP acceptance -------------------------

READBACK_ID = "aaaaaaaa-aaaa-4aaa-8aaa-aaaaaaaaaaaa"
READBACK_EVENT = "MLB:2026-11-09:NYY-BOS"
READBACK_PLAYER = "Leaky Player"


class _ReadbackResponse:
    def __init__(self, data):
        self.data = data

    def execute(self):
        return self


class _ReadbackQuery:
    def __init__(self, rows):
        self.rows = rows
        self.filters = []

    def select(self, *_args, **_kwargs):
        return self

    def eq(self, key, value):
        self.filters.append((key, value))
        return self

    def order(self, *_args, **_kwargs):
        return self

    def limit(self, count):
        self.limit_count = count
        return self

    def execute(self):
        rows = [dict(row) for row in self.rows]
        for key, value in self.filters:
            rows = [row for row in rows if row.get(key) == value]
        return _ReadbackResponse(rows)


class _ReadbackTable:
    def __init__(self, rows):
        self.rows = rows

    def select(self, *_args, **_kwargs):
        return _ReadbackQuery(self.rows)


class _ReadbackClient:
    def __init__(self):
        self.rpc_calls = []
        self.table_calls = []
        self.rows = [
            {
                "source_snapshot_id": READBACK_ID,
                "captured_at": "2026-11-08T07:54:00+00:00",
                "event_id": READBACK_EVENT,
                "sport": "MLB",
                "player": READBACK_PLAYER,
                "stat_type": "PITCHER_STRIKEOUTS",
                "line": 5.5,
                "hydration_status": "PASS",
                "blockers": [],
                "game_log": [1.0, 2.0, 3.0],
                "box_score_log": [{"secret-box": "DO_NOT_RETURN"}],
                "role_status": {"status": "ACTIVE"},
                "role_timestamp": "2026-11-08T06:00:00+00:00",
            }
        ]

    def rpc(self, name, params):
        self.rpc_calls.append((name, params))
        return _ReadbackResponse({"ok": True, "code": "PROP_EVIDENCE_READY"})

    def table(self, name):
        self.table_calls.append(name)
        if name == "wow_prop_evidence_snapshots":
            return _ReadbackTable(self.rows)
        if name == "wow_action_invocation_receipts":
            return _ReadbackTable([])
        raise AssertionError(f"unexpected table {name}")


def _readback_payload():
    return {
        "source_snapshot_id": READBACK_ID,
        "event_id": READBACK_EVENT,
        "sport": "MLB",
        "player": READBACK_PLAYER,
        "stat_type": "PITCHER_STRIKEOUTS",
        "line": 5.5,
        "request_id": "req-readback-test",
    }


def test_readback_endpoint_requires_authentication():
    """Reject missing credentials before any evidence lookup."""
    response = client.post("/v17/prop-evidence-readback", json=_readback_payload())
    assert response.status_code == 401
    assert READBACK_PLAYER not in response.text
    assert "can_execute" not in response.text


def test_readback_endpoint_rejects_empty_and_partial_identity():
    """Reject empty and partial identities with a validation error."""
    empty = client.post("/v17/prop-evidence-readback", json={}, headers=AUTH)
    partial = client.post(
        "/v17/prop-evidence-readback",
        json={"event_id": READBACK_EVENT},
        headers=AUTH,
    )
    assert empty.status_code == 422
    assert partial.status_code == 422


def test_readback_endpoint_sanitizes_database_unavailability(monkeypatch):
    """Return a sanitized 503 when the database client is unavailable."""
    def unavailable():
        raise RuntimeError("postgres://reader:super-secret-password@db.internal:5432/wow")

    monkeypatch.setattr(api_prod_market.prod, "get_client", unavailable)
    response = client.post(
        "/v17/prop-evidence-readback",
        json=_readback_payload(),
        headers=AUTH,
    )
    assert response.status_code == 503
    body = response.json()
    assert body["detail"]["code"] == "PROP_EVIDENCE_READBACK_UNAVAILABLE"
    assert body["detail"]["probability_publishable"] is False
    assert body["detail"]["can_execute"] is False
    assert "super-secret-password" not in response.text
    assert "postgres://" not in response.text
    assert "Traceback" not in response.text


def test_readback_endpoint_sanitizes_unexpected_diagnosis_failure(monkeypatch):
    """Return a sanitized 503 when diagnosis raises unexpectedly."""
    monkeypatch.setattr(api_prod_market.prod, "get_client", lambda: object())

    def broken(*_args, **_kwargs):
        raise RuntimeError("stack-secret-should-not-escape")

    monkeypatch.setattr(api_prod_market, "diagnose_prop_evidence", broken)
    response = client.post(
        "/v17/prop-evidence-readback",
        json=_readback_payload(),
        headers=AUTH,
    )
    assert response.status_code == 503
    assert response.json()["detail"]["code"] == "PROP_EVIDENCE_READBACK_UNAVAILABLE"
    assert "stack-secret-should-not-escape" not in response.text
    assert "Traceback" not in response.text


def test_readback_endpoint_is_read_only_and_does_not_leak_evidence(monkeypatch):
    """Return a bounded read-only result without evidence leakage."""
    fake_client = _ReadbackClient()
    monkeypatch.setattr(api_prod_market.prod, "get_client", lambda: fake_client)

    def forbidden(*_args, **_kwargs):
        raise AssertionError("readback must not score, repair or mutate")

    monkeypatch.setattr(api_prod_market, "score_discrete_prop_end_to_end", forbidden)
    monkeypatch.setattr(api_prod_market, "repair_prop_evidence", forbidden)

    response = client.post(
        "/v17/prop-evidence-readback",
        json=_readback_payload(),
        headers=AUTH,
    )
    assert response.status_code == 200, response.text
    body = response.json()
    assert body["operation"] == "PROP_EVIDENCE_READBACK"
    assert body["read_only"] is True
    assert body["can_execute"] is False
    assert body["diagnosis"] == "EVIDENCE_READY"
    assert body["snapshot_row"]["present"] is True
    assert body["snapshot_row"]["game_log_count"] == 3
    assert fake_client.rpc_calls[0][0] == "wow_prop_evidence_snapshot"
    assert set(fake_client.table_calls) <= {
        "wow_prop_evidence_snapshots",
        "wow_action_invocation_receipts",
    }

    text = response.text
    assert READBACK_PLAYER not in text
    assert "DO_NOT_RETURN" not in text
    assert "secret-box" not in text
    assert "1.0, 2.0, 3.0" not in text
    assert "probability_more" not in text
    assert "probability_less" not in text
    assert "calibrated_probability" not in text
