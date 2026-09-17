import logging

from fastapi import FastAPI
from fastapi.testclient import TestClient

from v17.interactive_latency_telemetry import install_interactive_latency_middleware


def test_interactive_latency_middleware_logs_only_governed_interactive_routes(caplog):
    app = FastAPI()
    install_interactive_latency_middleware(app)
    install_interactive_latency_middleware(app)

    @app.post("/score-pick-request")
    def score_pick_request():
        return {"ok": True, "can_execute": False}

    @app.post("/score-team-event-request")
    def score_team_event_request():
        return {"ok": True, "can_execute": False}

    @app.get("/health")
    def health():
        return {"ok": True}

    client = TestClient(app)
    with caplog.at_level(logging.WARNING, logger="wow.v17.interactive_latency"):
        assert client.post("/score-pick-request").status_code == 200
        assert client.post("/score-team-event-request").status_code == 200
        assert client.get("/health").status_code == 200

    messages = [record.getMessage() for record in caplog.records]
    pick = [message for message in messages if "route=/score-pick-request" in message]
    team = [message for message in messages if "route=/score-team-event-request" in message]
    assert len(pick) == 1
    assert len(team) == 1
    assert all("status_code=200" in message for message in [*pick, *team])
    assert all("total_ms=" in message for message in [*pick, *team])
    assert all("can_execute=false" in message for message in [*pick, *team])
    assert not any("route=/health" in message for message in messages)
