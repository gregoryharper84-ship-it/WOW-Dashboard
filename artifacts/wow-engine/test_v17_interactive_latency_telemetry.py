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


def test_stage_timer_aggregator_and_bounds(caplog):
    from v17.interactive_latency_telemetry import AGGREGATOR, LatencyAggregator, stage_timer

    with stage_timer("hydration"):  # no-op outside a request
        pass

    app = FastAPI()
    install_interactive_latency_middleware(app)

    @app.post("/score-pick-request")
    def score_pick_request():
        with stage_timer("hydration"):
            pass
        with stage_timer("not_a_stage"):
            pass
        return {"ok": True, "can_execute": False}

    with caplog.at_level(logging.WARNING, logger="wow.v17.interactive_latency"):
        TestClient(app).post("/score-pick-request", json={"secret": "body-token"})
    message = caplog.records[-1].getMessage()
    assert "stage_hydration_ms=" in message
    assert "not_a_stage" not in message
    assert "body-token" not in message
    assert "can_execute=false" in message
    assert AGGREGATOR.snapshot()["/score-pick-request"]["count"] >= 1

    agg = LatencyAggregator(max_keys=2, max_samples=5)
    agg.record("a", 1.0)
    agg.record("b", 1.0)
    agg.record("c", 1.0)
    assert set(agg.snapshot()) == {"b", "c"}
    for i in range(1, 11):
        agg.record("b", float(i))
    snap = agg.snapshot()["b"]
    assert snap["count"] == 5.0
    assert snap["p50_ms"] == 8.0
    assert snap["p95_ms"] == 10.0
