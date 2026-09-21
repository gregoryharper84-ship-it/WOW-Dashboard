import time
from types import SimpleNamespace

from fastapi import FastAPI
from fastapi.testclient import TestClient

from v17 import interactive_team_event_transport as transport


def test_team_event_transport_reuses_one_bounded_client(monkeypatch):
    created = []
    bounded_client = object()
    monkeypatch.setattr(transport, "_new_bounded_client", lambda: created.append(True) or bounded_client)

    event_api = SimpleNamespace(get_client=lambda: object())
    app = FastAPI()
    assert transport.install_interactive_team_event_transport(app, event_api=event_api) is True
    assert transport.install_interactive_team_event_transport(app, event_api=event_api) is True

    @app.post("/score-team-event")
    def score_team_event():
        return {"same_client": event_api.get_client() is event_api.get_client(), "can_execute": False}

    with TestClient(app) as client:
        first = client.post("/score-team-event")
        second = client.post("/score-team-event")

    assert first.status_code == 200
    assert second.status_code == 200
    assert first.json()["same_client"] is True
    assert len(created) == 1


def test_team_event_transport_timeout_is_not_model_unavailable(monkeypatch):
    monkeypatch.setattr(transport, "_bounded_env_float", lambda *args, **kwargs: 0.05)
    monkeypatch.setattr(transport, "_new_bounded_client", lambda: object())

    event_api = SimpleNamespace(get_client=lambda: object())
    app = FastAPI()
    assert transport.install_interactive_team_event_transport(app, event_api=event_api) is True

    @app.post("/score-team-event")
    def score_team_event():
        time.sleep(0.20)
        return {"code": "SHOULD_NOT_REACH_CALLER", "can_execute": False}

    started = time.monotonic()
    with TestClient(app) as client:
        response = client.post("/score-team-event")
    elapsed = time.monotonic() - started

    assert response.status_code == 504
    detail = response.json()["detail"]
    assert detail["code"] == "ACTION_TRANSPORT_TIMEOUT"
    assert detail["failure_class"] == "ACTION_TRANSPORT_TIMEOUT"
    assert detail["model_capability_status"] == "NOT_YET_DETERMINED"
    assert detail["code"] != "MODEL_UNAVAILABLE"
    assert detail["global_terminal_authority"] == "V17_TERMINAL_REDUCER"
    assert detail["can_execute"] is False
    assert elapsed < 1.0
