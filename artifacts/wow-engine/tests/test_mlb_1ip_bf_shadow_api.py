from types import SimpleNamespace

from fastapi import Depends, FastAPI
from fastapi.testclient import TestClient

import mlb_1ip_bf_shadow_api as api


class _Rpc:
    def execute(self):
        return SimpleNamespace(data={"forward_sample_complete": False, "graded": 0, "can_execute": False})


class _Db:
    def rpc(self, name, args):
        assert name == "wow_mlb_1ip_bf_shadow_health"
        assert args["p_artifact_checksum"] == api.EXPECTED_ARTIFACT_CHECKSUM
        return _Rpc()


def _auth():
    return True


def test_scheduler_route_rejects_missing_token(monkeypatch):
    monkeypatch.setenv("WOW_MLB_1IP_BF_SHADOW_TOKEN", "secret-value")
    app = FastAPI()
    api.install_mlb_1ip_bf_shadow_routes(
        app,
        auth_dependency=Depends(_auth),
        db_client_fn=lambda: _Db(),
    )
    client = TestClient(app)
    response = client.post(api.RUN_PATH)
    assert response.status_code == 401
    assert response.json()["detail"]["can_execute"] is False


def test_scheduler_route_runs_with_constant_time_token(monkeypatch):
    monkeypatch.setenv("WOW_MLB_1IP_BF_SHADOW_TOKEN", "secret-value")
    monkeypatch.setattr(
        api,
        "run_once",
        lambda client: {
            "status": "FORWARD_SHADOW_ACTIVE",
            "probability_publishable": False,
            "rank_eligible": False,
            "can_execute": False,
        },
    )
    app = FastAPI()
    api.install_mlb_1ip_bf_shadow_routes(
        app,
        auth_dependency=Depends(_auth),
        db_client_fn=lambda: _Db(),
    )
    client = TestClient(app)
    response = client.post(api.RUN_PATH, headers={"X-WOW-BF-Shadow-Token": "secret-value"})
    assert response.status_code == 200
    payload = response.json()
    assert payload["status"] == "FORWARD_SHADOW_ACTIVE"
    assert payload["can_execute"] is False


def test_health_route_never_promotes_from_sample_count_alone(monkeypatch):
    app = FastAPI()
    api.install_mlb_1ip_bf_shadow_routes(
        app,
        auth_dependency=Depends(_auth),
        db_client_fn=lambda: _Db(),
    )
    client = TestClient(app)
    response = client.get(api.HEALTH_PATH)
    assert response.status_code == 200
    payload = response.json()
    assert payload["forward_sample_complete"] is False
    assert payload["certification_ready"] is False
    assert "FORWARD_SAMPLE_INCOMPLETE" in payload["certification_blockers"]
    assert payload["probability_publishable"] is False
    assert payload["rank_eligible"] is False
    assert payload["can_execute"] is False
