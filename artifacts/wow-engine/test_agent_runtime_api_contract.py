from datetime import datetime, timezone

from fastapi import FastAPI
from fastapi.testclient import TestClient

import agent_runtime_v1.api as runtime_api
from agent_runtime_v1.contracts import RunCreateRequest
from agent_runtime_v1.store import MemoryStore

TEST_KEY="agent-runtime-test-key"
AUTH={"Authorization":f"Bearer {TEST_KEY}"}


def _client(monkeypatch):
    monkeypatch.setenv("WOW_ACTION_API_KEY",TEST_KEY)
    store=MemoryStore()
    monkeypatch.setattr(runtime_api,"get_store",lambda:store)
    app=FastAPI(); app.include_router(runtime_api.router)
    return TestClient(app),store


def _create(store):
    req=RunCreateRequest(as_of=datetime.now(timezone.utc),user_timezone="America/Chicago").model_dump(mode="json")
    return store.create_run(idempotency_key="api-contract",request=req,governance_version="WOW-v16-CLEAN-CORE")


def test_terminal_manifest_contains_terminal_rows_and_reconciliation(monkeypatch):
    client,store=_client(monkeypatch); run=_create(store); run_id=run["run_id"]
    candidate_id="candidate-1"
    store.candidates[candidate_id]={
        "candidate_id":candidate_id,"run_id":run_id,"canonical_key":"MLB|game|Pitcher A|PLAYER_PROP|K|FULL_GAME|5.5|MORE",
        "sport":"MLB","league":"MLB","official_event_id":"game","participant":"Pitcher A","opponent":"Team B",
        "market_family":"PLAYER_PROP","stat_family":"K","period":"FULL_GAME","exact_line":5.5,"side":"MORE",
        "settlement_operator":">","controlling_worker_id":"wow.mlb-pitcher-k","evidence_snapshot_id":None,
        "terminal_label":"MODEL_UNAVAILABLE","terminal_ceiling":"RESEARCH_INTEREST","blockers":["CERTIFIED_ARTIFACT_UNAVAILABLE"],
    }
    run.update({"status":"COMPLETED_WITH_BLOCKERS","stage":"COMPLETED_WITH_BLOCKERS","rows_in":1,"rows_completed":0,"rows_held":1,"rows_rejected":0})
    response=client.get(f"/wow/runs/{run_id}/manifest",headers=AUTH)
    assert response.status_code==200
    body=response.json()
    assert body["terminal"] is True
    assert body["reconciliation"]=={"rows_in":1,"rows_completed":0,"rows_held":1,"rows_rejected":0,"balanced":True}
    assert len(body["rows"])==1
    assert body["rows"][0]["terminal_label"]=="MODEL_UNAVAILABLE"
    assert body["rows"][0]["blockers"]==["CERTIFIED_ARTIFACT_UNAVAILABLE"]
    assert body["rows"][0]["can_execute"] is False


def test_cancel_created_run_transitions_to_canceled(monkeypatch):
    client,store=_client(monkeypatch); run=_create(store); run_id=run["run_id"]
    response=client.post(f"/wow/runs/{run_id}/cancel",headers=AUTH)
    assert response.status_code==200
    assert response.json()["status"]=="CANCELED"
    assert store.get_run(run_id)["status"]=="CANCELED"


def test_run_routes_require_bearer_auth(monkeypatch):
    client,store=_client(monkeypatch); run=_create(store)
    response=client.get(f"/wow/runs/{run['run_id']}")
    assert response.status_code==401
