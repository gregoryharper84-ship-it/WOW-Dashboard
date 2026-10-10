"""Registry infrastructure failures must never be rewritten into MODEL_UNAVAILABLE.

failure_codes.md: MODEL_UNAVAILABLE means the exact fitted specialist/artifact/
adapter is absent for the route. A Supabase registry RPC that throws
(PROP_MODEL_REGISTRY_UNAVAILABLE) or returns an unusable shape
(PROP_MODEL_REGISTRY_INVALID_RESPONSE) proves nothing about capability absence
and must surface under its own typed code on both the batch Pick Request route
and the single /score-prop preflight. Genuine absence (e.g. NCAAF ANYTIME_TD
with no certified artifact) must remain MODEL_UNAVAILABLE.
"""
from __future__ import annotations

from datetime import datetime, timedelta, timezone
import uuid

import pytest
from fastapi import Depends, FastAPI
from fastapi.testclient import TestClient

import api_prod_market
import pick_request_runtime_core as core


INFRA_CODES = ("PROP_MODEL_REGISTRY_UNAVAILABLE", "PROP_MODEL_REGISTRY_INVALID_RESPONSE")


def test_infra_code_sets_are_identical_across_paths():
    assert core.PROP_ROUTE_REGISTRY_INFRA_CODES == api_prod_market.PROP_ROUTE_REGISTRY_INFRA_CODES
    assert "PROP_CERTIFIED_MODEL_ARTIFACT_NOT_FOUND" not in core.PROP_ROUTE_REGISTRY_INFRA_CODES


# ---------------------------------------------------------------- batch route

class _BaseApi:
    @staticmethod
    def _controlling_specialist_provider(_sport, _stat):
        return {"controlling_specialist": "wow.ncaaf-player-prop-expert"}


class _Prod:
    PROP_CAPABILITY_KEY = "PROP"
    base_api = _BaseApi()

    @staticmethod
    def _runtime_capability(_key):
        return {"capability_status": "AVAILABLE"}


def _market(route_payload):
    class _Market:
        prod = _Prod()

        @staticmethod
        def _prop_route_artifact(_sport, _stat):
            return dict(route_payload)

        @staticmethod
        def score_prop(*_a, **_k):
            raise AssertionError("specialist must not be invoked when the route is blocked")

    return _Market()


def _batch_client(route_payload):
    app = FastAPI()
    core.install_pick_request_routes(app, market_api=_market(route_payload), auth_dependency=Depends(lambda: None))
    return TestClient(app)


def _row():
    start = (datetime.now(timezone.utc) + timedelta(hours=2)).isoformat()
    return {
        "row_key": "r1",
        "event_id": "NCAAF:M-OH@MASS:2026-10-10",
        "event_start_time": start,
        "sport": "NCAAF",
        "player": "Test Player",
        "stat_type": "ANYTIME_TD",
        "line": 0.5,
        "direction": "MORE",
        "source_type": "NORMALIZED",
        "platform": "PrizePicks",
    }


def _post_batch(route_payload):
    resp = _batch_client(route_payload).post(
        "/score-pick-request",
        json={"request_id": f"t-{uuid.uuid4()}", "rows": [_row()]},
    )
    assert resp.status_code == 200, resp.text
    body = resp.json()
    assert body["can_execute"] is False
    assert body["rows_in"] == 1
    return body["rows"][0]


@pytest.mark.parametrize("infra_code", INFRA_CODES)
def test_batch_registry_infra_failure_is_typed_not_model_unavailable(infra_code):
    row = _post_batch({"ok": False, "code": infra_code, "can_execute": False})
    assert row["code"] == infra_code
    assert row["code"] != "MODEL_UNAVAILABLE"
    assert row["terminal_status"] == "HELD"
    assert row["terminal_label"] == "REGISTRY_INFRASTRUCTURE_BLOCKED"
    assert row["verdict_class"] == "REGISTRY_INFRASTRUCTURE_BLOCKED"
    assert row["terminal_cause"] == "INFRASTRUCTURE"
    assert row["infrastructure_blocked"] is True
    assert row["model_evaluated"] is False
    assert row["detail"]["terminal_label"] == row["terminal_label"]
    assert row["detail"]["specialist_invoked"] is False
    assert row.get("probability_publishable") in (False, None)


def test_batch_genuinely_absent_artifact_stays_model_unavailable():
    row = _post_batch({"ok": False, "code": "PROP_CERTIFIED_MODEL_ARTIFACT_NOT_FOUND"})
    assert row["code"] == "MODEL_UNAVAILABLE"
    assert row["detail"]["blocker_code"] == "PROP_CERTIFIED_MODEL_ARTIFACT_NOT_FOUND"
    assert row["terminal_status"] == "HELD"
    assert row["terminal_label"] == "MODEL_UNAVAILABLE"
    assert row["verdict_class"] == "CAPABILITY_BLOCKED"


@pytest.mark.parametrize("infra_code", INFRA_CODES)
def test_batch_registry_outage_counts_as_blocked_preflight(infra_code):
    resp = _batch_client({"ok": False, "code": infra_code}).post(
        "/score-pick-request",
        json={"request_id": f"t-{uuid.uuid4()}", "rows": [_row()]},
    )
    assert resp.status_code == 200, resp.text
    body = resp.json()
    assert body["telemetry"]["route_preflight_blocked"] == 1
    assert body["telemetry"]["registry_infrastructure_failures"] == 1
    assert body["infrastructure_blocked_count"] == 1
    assert body["rows"][0]["terminal_cause"] == "INFRASTRUCTURE"


# ---------------------------------------------------------- /score-prop route

TEST_KEY = "test-registry-infra-key"


@pytest.fixture
def auth(monkeypatch):
    monkeypatch.setenv("WOW_ACTION_API_KEY", TEST_KEY)
    monkeypatch.setattr(
        api_prod_market.prod,
        "_runtime_capability",
        lambda _key: {"capability_status": "AVAILABLE", "evidence": {}, "can_execute": False},
    )
    monkeypatch.setattr(api_prod_market.prod, "_reject_llp_prop_identity", lambda _i: "WOW_BETTING_ENGINE")
    monkeypatch.setattr(
        api_prod_market.prod.base_api,
        "_controlling_specialist_provider",
        lambda _s, _t: {"sport": "WNBA", "canonical_prop_type": "REB",
                        "controlling_specialist": "wow.wnba-player-prop-generative-expert"},
    )

    def should_not_hydrate(_req):
        raise AssertionError("blocked route must not hydrate evidence")

    monkeypatch.setattr(api_prod_market.prod, "_prop_evidence", should_not_hydrate)
    return {"Authorization": f"Bearer {TEST_KEY}"}


def _score_prop_payload():
    return {
        "event_id": "WNBA:REGISTRY:INFRA:1",
        "event_start_time": (datetime.now(timezone.utc) + timedelta(days=1)).isoformat(),
        "sport": "WNBA",
        "player": "Test Player",
        "stat_type": "REB",
        "line": 10.5,
        "direction": "MORE",
        "source_snapshot_id": str(uuid.uuid4()),
        "money_lane_status": "PAYOUT_UNRESOLVED",
    }


def test_score_prop_registry_rpc_exception_is_typed_503(monkeypatch, auth):
    class _Boom:
        def rpc(self, *_a, **_k):
            raise RuntimeError("supabase down")

    monkeypatch.setattr(api_prod_market.prod, "get_client", lambda: _Boom())
    resp = TestClient(api_prod_market.app).post("/score-prop", json=_score_prop_payload(), headers=auth)
    assert resp.status_code == 503
    body = resp.json()["detail"]
    assert body["code"] == "PROP_MODEL_REGISTRY_UNAVAILABLE"
    assert body["backend_traversal"]["exact_route_artifact"] == "REGISTRY_FAILED"
    assert body["specialist_invoked"] is False
    assert body["probability_publishable"] is False
    assert body["can_execute"] is False


def test_score_prop_registry_invalid_shape_is_typed_503(monkeypatch, auth):
    monkeypatch.setattr(
        api_prod_market, "_prop_route_artifact",
        lambda _s, _t: {"ok": False, "code": "PROP_MODEL_REGISTRY_INVALID_RESPONSE"},
    )
    resp = TestClient(api_prod_market.app).post("/score-prop", json=_score_prop_payload(), headers=auth)
    assert resp.status_code == 503
    assert resp.json()["detail"]["code"] == "PROP_MODEL_REGISTRY_INVALID_RESPONSE"
