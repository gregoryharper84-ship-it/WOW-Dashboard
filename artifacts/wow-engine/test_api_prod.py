from datetime import datetime, timedelta, timezone
from types import SimpleNamespace
import os
import uuid

from fastapi.testclient import TestClient

import api_prod


# Keep the same suite-wide governed-backend test credential used by the
# pre-existing G11 event tests. api._require_action_api_key reads the process
# environment at request time, so competing module-level values make otherwise
# independent tests fail based on pytest collection/import order.
TEST_KEY = "test-g11-action-key"
os.environ["WOW_ACTION_API_KEY"] = TEST_KEY
AUTH = {"Authorization": f"Bearer {TEST_KEY}"}
client = TestClient(api_prod.app)


class _FakeQuery:
    def __init__(self, parent):
        self.parent = parent
        self.capability_key = None

    def select(self, *_args, **_kwargs):
        return self

    def eq(self, key, value):
        if key == "capability_key":
            self.capability_key = value
        return self

    def limit(self, _n):
        return self

    def execute(self):
        row = self.parent.capabilities.get(self.capability_key)
        return SimpleNamespace(data=[row] if row else [])


class _FakeClient:
    def __init__(self, *, prop_status="UNAVAILABLE", evidence=None):
        self.capabilities = {
            "PROP_PROBABILITY": {
                "capability_key": "PROP_PROBABILITY",
                "capability_status": prop_status,
                "evidence": {"reason": "GENERIC_PROP_FITTED_PROVIDER_UNAVAILABLE"},
                "can_execute": False,
                "updated_at": datetime.now(timezone.utc).isoformat(),
            },
            "MLB_EVENT_PROBABILITY": {
                "capability_key": "MLB_EVENT_PROBABILITY",
                "capability_status": "UNAVAILABLE",
                "evidence": {"reason": "TEST_EVENT_HOLD"},
                "can_execute": False,
                "updated_at": datetime.now(timezone.utc).isoformat(),
            },
        }
        self.evidence = evidence or _ready_evidence()
        self.rpc_calls = []

    def table(self, _name):
        return _FakeQuery(self)

    def rpc(self, name, params):
        self.rpc_calls.append((name, params))
        if name == "wow_prop_evidence_snapshot":
            return SimpleNamespace(execute=lambda: SimpleNamespace(data=self.evidence))
        raise AssertionError(f"unexpected RPC {name}")


def _request_payload():
    start = datetime.now(timezone.utc) + timedelta(days=1)
    return {
        "event_id": "WNBA:TEST:1",
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
    return {
        "ok": True,
        "code": "PROP_EVIDENCE_READY",
        "hydration_status": "PASS",
        "source_snapshot_id": str(uuid.uuid4()),
        "game_log": [10, 12, 11, 9, 14, 8, 13, 12, 10, 15],
        "box_score_log": [{"minutes": 34}] * 10,
        "role_status": {"status": "ACTIVE", "role": "STARTER"},
        "role_timestamp": datetime.now(timezone.utc).isoformat(),
        "opportunity_ledger": {"status": "PASS"},
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


def test_llp_identity_is_rejected_from_player_prop_route(monkeypatch):
    fake = _FakeClient()
    monkeypatch.setattr(api_prod, "get_client", lambda: fake)

    response = client.post(
        "/score-prop",
        json=_request_payload(),
        headers={**AUTH, "X-WOW-Model-Identity": "LLP_TEAM_BETTING_MODEL"},
    )
    assert response.status_code == 422
    detail = response.json()["detail"]
    assert detail["code"] == "LLP_PLAYER_PROP_SCOPE_PROHIBITED"
    assert detail["probability_publishable"] is False
    assert detail["can_execute"] is False
    assert fake.rpc_calls == []


def test_prop_lane_unavailable_still_proves_supabase_hydration_before_model_block(monkeypatch):
    fake = _FakeClient(prop_status="UNAVAILABLE")
    monkeypatch.setattr(api_prod, "get_client", lambda: fake)
    monkeypatch.setattr(api_prod.base_api, "_controlling_specialist_provider", lambda _sport, _stat: _specialist())

    response = client.post("/score-prop", json=_request_payload(), headers=AUTH)
    assert response.status_code == 409
    detail = response.json()["detail"]
    assert detail["code"] == "PROP_PROBABILITY_UNAVAILABLE"
    assert detail["evidence_hydration"] == "PASS"
    assert detail["backend_traversal"]["supabase_evidence"] == "PASS"
    assert detail["backend_traversal"]["governed_model"] == "BLOCKED"
    assert detail["backend_traversal"]["prediction_ledger_write"] == "NOT_ATTEMPTED"
    assert detail["probability_publishable"] is False
    assert fake.rpc_calls[0][0] == "wow_prop_evidence_snapshot"


def test_incomplete_prop_evidence_terminates_before_specialist_or_probability(monkeypatch):
    fake = _FakeClient(
        prop_status="UNAVAILABLE",
        evidence={
            "ok": False,
            "code": "RUN_INVALID_ACQUISITION_INCOMPLETE",
            "hydration_status": "INCOMPLETE",
            "blockers": ["WNBA_EVIDENCE:EXACT_L10_INCOMPLETE:n=4<10"],
            "probability_publishable": False,
            "can_execute": False,
        },
    )
    specialist_calls = []
    monkeypatch.setattr(api_prod, "get_client", lambda: fake)
    monkeypatch.setattr(
        api_prod.base_api,
        "_controlling_specialist_provider",
        lambda sport, stat: specialist_calls.append((sport, stat)) or _specialist(),
    )

    response = client.post("/score-prop", json=_request_payload(), headers=AUTH)
    assert response.status_code == 422
    detail = response.json()["detail"]
    assert detail["code"] == "RUN_INVALID_ACQUISITION_INCOMPLETE"
    assert detail["failure_class"] == "RUN_INVALID_ACQUISITION_INCOMPLETE"
    assert detail["probability_publishable"] is False
    assert detail["can_execute"] is False
    assert specialist_calls == []


def test_available_prop_lane_without_real_fitted_provider_still_fails_closed(monkeypatch):
    fake = _FakeClient(prop_status="AVAILABLE")
    monkeypatch.setattr(api_prod, "get_client", lambda: fake)
    monkeypatch.setattr(api_prod.base_api, "_controlling_specialist_provider", lambda _sport, _stat: _specialist())
    monkeypatch.setattr(api_prod.base_api, "_fitted_params_provider", lambda _sport, _stat: None)

    response = client.post("/score-prop", json=_request_payload(), headers=AUTH)
    assert response.status_code == 501
    detail = response.json()["detail"]
    assert detail["code"] == "PROP_FITTED_PROVIDER_UNAVAILABLE"
    assert detail["evidence_hydration"] == "PASS"
    assert detail["probability_publishable"] is False
    assert detail["can_execute"] is False


def test_governance_reports_lane_split(monkeypatch):
    fake = _FakeClient(prop_status="UNAVAILABLE")
    monkeypatch.setattr(api_prod, "get_client", lambda: fake)
    monkeypatch.setattr(api_prod.base_api, "_query_deployment_gate_state", lambda: {
        "governed_probability_capability": "UNAVAILABLE",
        "governed_probability_status": "NOT_PRODUCED",
        "deployment_gates": [{"gate_id": "G11", "status": "FAIL"}],
    })
    monkeypatch.setattr(api_prod.base_api, "_query_calibration_health", lambda: {"status": "BLOCKED"})

    response = client.get("/governance")
    assert response.status_code == 200
    body = response.json()
    assert body["compute_provider"] == "RENDER"
    assert body["database_provider"] == "SUPABASE"
    assert body["lane_capabilities"]["PROP_PROBABILITY"]["status"] == "UNAVAILABLE"
    assert body["routing_contract"]["LLP_TEAM_BETTING_MODEL"].startswith("/score-event")
    assert body["can_execute"] is False
