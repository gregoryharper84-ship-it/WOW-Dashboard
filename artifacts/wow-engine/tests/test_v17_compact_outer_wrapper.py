from __future__ import annotations

import pytest
from fastapi import Depends, FastAPI, Header
from fastapi.testclient import TestClient

import pick_request_runtime as pick_runtime
import v17.detailed_evidence_install as subject

@pytest.mark.parametrize("sport", ["MLB", "NFL", "WNBA", "NCAAF"])
def test_detailed_evidence_wrapper_preserves_compact_mode(monkeypatch, sport):
    app = FastAPI()
    captured = {}
    @app.post("/score-pick-request")
    def base_route(batch: pick_runtime.PickRequestBatch, x_wow_model_identity: str | None = Header(default=None, alias="X-WOW-Model-Identity")):
        captured["response_mode"] = batch.response_mode
        return {"ok": True, "response_mode": batch.response_mode, "rows": [{"row_key": batch.rows[0].row_key, "terminal_status": "HELD", "code": "TEST_TYPED_BLOCKER", "model_evaluated": False, "probability_publishable": False, "rank_eligible": False, "can_execute": False}], "can_execute": False}
    monkeypatch.setattr(subject, "_patch_model_features", lambda _market: None)
    monkeypatch.setattr(subject, "_patch_snapshot_payload", lambda: None)
    monkeypatch.setattr(subject, "_patch_team_event_canonicalizer", lambda: None)
    subject.install_v17_detailed_evidence(app, auth_dependency=Depends(lambda: None), market_api=object())
    response = TestClient(app).post("/score-pick-request", json={"request_id": f"compact-{sport.lower()}", "response_mode": "COMPACT", "rows": [{"row_key": "row-1", "event_id": f"{sport}-event-1", "event_start_time": "2099-09-20T20:00:00Z", "sport": sport, "player": "Test Player", "stat_type": "TEST_STAT", "line": 1.5, "direction": "MORE"}]})
    assert response.status_code == 200, response.text
    payload = response.json()
    assert captured["response_mode"] == "COMPACT"
    assert payload["response_mode"] == "COMPACT"
    assert payload["can_execute"] is False
