import logging

from fastapi import FastAPI
from fastapi.testclient import TestClient

from v17.interactive_latency_telemetry import (
    install_interactive_latency_middleware,
    latency_snapshot,
    reset_latency_samples,
    stage_timer,
    tag_request,
)


def test_interactive_latency_middleware_logs_only_governed_interactive_routes(caplog):
    app = FastAPI()
    install_interactive_latency_middleware(app)
    install_interactive_latency_middleware(app)

    @app.post("/score-prop")
    def score_prop():
        return {"ok": True, "can_execute": False}

    @app.post("/score-pick-request")
    def score_pick_request():
        return {"ok": True, "can_execute": False}

    @app.post("/score-team-event-request")
    def score_team_event_request():
        return {"ok": True, "can_execute": False}

    @app.post("/score-team-event")
    def score_team_event():
        return {"ok": True, "can_execute": False}

    @app.get("/health")
    def health():
        return {"ok": True}

    client = TestClient(app)
    with caplog.at_level(logging.WARNING, logger="wow.v17.interactive_latency"):
        assert client.post("/score-prop").status_code == 200
        assert client.post("/score-pick-request").status_code == 200
        assert client.post("/score-team-event-request").status_code == 200
        assert client.post("/score-team-event").status_code == 200
        assert client.get("/health").status_code == 200

    messages = [record.getMessage() for record in caplog.records]
    prop = [message for message in messages if "route=/score-prop" in message]
    pick = [message for message in messages if "route=/score-pick-request" in message]
    team_request = [message for message in messages if "route=/score-team-event-request" in message]
    team_direct = [message for message in messages if "route=/score-team-event method=" in message]
    assert len(prop) == 1
    assert len(pick) == 1
    assert len(team_request) == 1
    assert len(team_direct) == 1
    governed = [*prop, *pick, *team_request, *team_direct]
    assert all("status_code=200" in message for message in governed)
    assert all("total_ms=" in message for message in governed)
    assert all("can_execute=false" in message for message in governed)
    assert not any("route=/health" in message for message in messages)


def test_interactive_latency_stage_timings_and_percentiles(caplog):
    reset_latency_samples()
    app = FastAPI()
    install_interactive_latency_middleware(app)

    @app.post("/score-pick-request")
    def score_pick_request():
        tag_request(sport="NBA", row_count=3, batch_size=3)
        with stage_timer("hydration"):
            pass
        with stage_timer("fitted_scoring"):
            pass
        return {"ok": True, "can_execute": False}

    client = TestClient(app)
    with caplog.at_level(logging.WARNING, logger="wow.v17.interactive_latency"):
        assert client.post("/score-pick-request", json={"secret": "do-not-log"}).status_code == 200

    message = caplog.records[-1].getMessage()
    assert "sport=NBA row_count=3 batch_size=3" in message
    assert "stage_hydration_ms=" in message and "stage_fitted_scoring_ms=" in message
    assert "can_execute=false" in message
    assert "do-not-log" not in message
    snap = {(r["stage"], r["sport"], r["row_count"], r["batch_size"]): r for r in latency_snapshot()}
    assert ("total", "NBA", 3, 3) in snap and ("hydration", "NBA", 3, 3) in snap
    assert all(r["can_execute"] is False and r["p95_ms"] >= r["p50_ms"] for r in snap.values())


def test_stage_timer_outside_request_is_noop():
    tag_request(sport="NBA", row_count=1)
    with stage_timer("hydration"):
        pass
