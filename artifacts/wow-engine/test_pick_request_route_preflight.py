from datetime import datetime, timedelta, timezone
import os
import uuid

from fastapi.testclient import TestClient

import api_prod_market


TEST_KEY = "test-p0-pick-request-key"
os.environ["WOW_ACTION_API_KEY"] = TEST_KEY
AUTH = {"Authorization": f"Bearer {TEST_KEY}"}
client = TestClient(api_prod_market.app)


def _request_payload():
    start = datetime.now(timezone.utc) + timedelta(days=1)
    return {
        "event_id": "WNBA:P0:PREFLIGHT:1",
        "event_start_time": start.isoformat(),
        "sport": "WNBA",
        "player": "Test Player",
        "stat_type": "REB",
        "line": 10.5,
        "direction": "MORE",
        "source_snapshot_id": str(uuid.uuid4()),
        "money_lane_status": "PAYOUT_UNRESOLVED",
    }


def _specialist():
    return {
        "sport": "WNBA",
        "canonical_prop_type": "REB",
        "controlling_specialist": "wow.wnba-player-prop-generative-expert",
        "min_event_tree_simulations": 0,
    }


def _install_identity_and_capability(monkeypatch):
    monkeypatch.setattr(
        api_prod_market.prod,
        "_runtime_capability",
        lambda _key: {"capability_status": "AVAILABLE", "evidence": {}, "can_execute": False},
    )
    monkeypatch.setattr(
        api_prod_market.prod,
        "_reject_llp_prop_identity",
        lambda _identity: "WOW_BETTING_ENGINE",
    )
    monkeypatch.setattr(
        api_prod_market.prod.base_api,
        "_controlling_specialist_provider",
        lambda _sport, _stat: _specialist(),
    )


def test_missing_exact_route_terminates_before_evidence_hydration(monkeypatch):
    called = {"evidence": False}
    _install_identity_and_capability(monkeypatch)

    def should_not_hydrate(_req):
        called["evidence"] = True
        raise AssertionError("unsupported exact route must not hydrate evidence")

    monkeypatch.setattr(api_prod_market.prod, "_prop_evidence", should_not_hydrate)
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
    assert body["evidence_hydration"] == "NOT_ATTEMPTED_ROUTE_BLOCKED"
    assert body["backend_traversal"]["supabase_evidence"] == "NOT_ATTEMPTED"
    assert body["backend_traversal"]["governed_model"] == "NOT_INVOKED"
    assert body["specialist_invoked"] is False
    assert body["probability_publishable"] is False
    assert body["can_execute"] is False
    assert called["evidence"] is False


def test_missing_specialist_terminates_before_evidence_hydration(monkeypatch):
    called = {"evidence": False}
    _install_identity_and_capability(monkeypatch)
    monkeypatch.setattr(
        api_prod_market.prod.base_api,
        "_controlling_specialist_provider",
        lambda _sport, _stat: None,
    )

    def should_not_hydrate(_req):
        called["evidence"] = True
        raise AssertionError("missing specialist must not hydrate evidence")

    monkeypatch.setattr(api_prod_market.prod, "_prop_evidence", should_not_hydrate)

    response = client.post("/score-prop", json=_request_payload(), headers=AUTH)

    assert response.status_code == 503
    body = response.json()["detail"]
    assert body["code"] == "SPECIALIST_ROUTING_UNAVAILABLE"
    assert body["evidence_hydration"] == "NOT_ATTEMPTED_ROUTE_BLOCKED"
    assert body["specialist_invoked"] is False
    assert body["probability_publishable"] is False
    assert body["can_execute"] is False
    assert called["evidence"] is False


def test_unavailable_aggregate_capability_terminates_before_hydration(monkeypatch):
    called = {"evidence": False, "route": False}
    _install_identity_and_capability(monkeypatch)
    monkeypatch.setattr(
        api_prod_market.prod,
        "_runtime_capability",
        lambda _key: {"capability_status": "UNAVAILABLE", "evidence": {"reason": "test"}, "can_execute": False},
    )

    def should_not_hydrate(_req):
        called["evidence"] = True
        raise AssertionError("unavailable aggregate capability must not hydrate evidence")

    def should_not_lookup_route(_sport, _stat):
        called["route"] = True
        raise AssertionError("aggregate capability block should stop before exact route lookup")

    monkeypatch.setattr(api_prod_market.prod, "_prop_evidence", should_not_hydrate)
    monkeypatch.setattr(api_prod_market, "_prop_route_artifact", should_not_lookup_route)

    response = client.post("/score-prop", json=_request_payload(), headers=AUTH)

    assert response.status_code == 409
    body = response.json()["detail"]
    assert body["code"] == "PROP_PROBABILITY_UNAVAILABLE"
    assert body["evidence_hydration"] == "NOT_ATTEMPTED_ROUTE_BLOCKED"
    assert body["backend_traversal"]["supabase_evidence"] == "NOT_ATTEMPTED"
    assert body["backend_traversal"]["exact_route_artifact"] == "NOT_ATTEMPTED"
    assert body["probability_publishable"] is False
    assert body["can_execute"] is False
    assert called == {"evidence": False, "route": False}
