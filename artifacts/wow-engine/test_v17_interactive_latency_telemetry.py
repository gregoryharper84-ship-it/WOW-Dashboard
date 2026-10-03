import logging

from fastapi import FastAPI
from fastapi.testclient import TestClient

from v17.interactive_latency_telemetry import install_interactive_latency_middleware


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


def test_stage_timer_logs_on_exception_and_records_percentiles(caplog):
    import pytest
    from v17.interactive_latency_telemetry import RECORDER, stage_timer

    RECORDER.clear()
    with caplog.at_level(logging.WARNING, logger="wow.v17.interactive_stage"):
        with pytest.raises(ValueError):
            with stage_timer("/score-pick-request", "persistence"):
                raise ValueError("boom")
    message = caplog.records[-1].getMessage()
    assert "stage=persistence" in message
    assert "stage_ms=" in message
    assert "can_execute=false" in message
    assert RECORDER.percentiles("/score-pick-request|persistence")["count"] == 1


def test_latency_recorder_percentiles_and_bounds():
    from v17.interactive_latency_telemetry import LatencyRecorder

    rec = LatencyRecorder(max_samples=100, max_keys=2)
    for v in range(1, 101):
        rec.record("a", v)
    assert rec.percentiles("a") == {"count": 100, "p50_ms": 50, "p95_ms": 95}
    rec.record("b", 1)
    rec.record("c", 1)  # beyond max_keys: dropped
    assert rec.percentiles("c") is None
    for v in range(1000):
        rec.record("a", v)
    assert rec.percentiles("a")["count"] == 100
