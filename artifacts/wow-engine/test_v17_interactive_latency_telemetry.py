import logging

from fastapi import FastAPI
from fastapi.testclient import TestClient

from v17.interactive_latency_telemetry import (
    AGGREGATOR,
    LatencyAggregator,
    install_interactive_latency_middleware,
    set_request_dimensions,
    stage_timer,
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


def test_stage_timings_dimensions_and_aggregate_are_recorded(caplog):
    AGGREGATOR.clear()
    app = FastAPI()
    install_interactive_latency_middleware(app)

    @app.post("/score-team-event-request")
    def team():
        set_request_dimensions(sport="NBA", row_count=64, batch_size=8)
        with stage_timer("hydration"):
            pass
        with stage_timer("fitted_scoring"):
            pass
        return {"ok": True, "can_execute": False}

    with caplog.at_level(logging.WARNING, logger="wow.v17.interactive_latency"):
        assert TestClient(app).post("/score-team-event-request").status_code == 200
    message = caplog.records[-1].getMessage()
    assert "sport=NBA row_count=64 batch_size=8" in message
    assert "hydration=" in message and "fitted_scoring=" in message
    assert "can_execute=false" in message
    snap = AGGREGATOR.snapshot()
    assert len(snap) == 1 and snap[0]["row_count"] == 64 and snap[0]["can_execute"] is False


def test_aggregator_percentiles_and_bounds():
    agg = LatencyAggregator(max_samples=100, max_keys=2)
    for v in range(1, 101):
        agg.record("/r", "NBA", 1, 1, float(v))
    row = agg.snapshot()[0]
    assert row["p50_ms"] == 50.0 and row["p95_ms"] == 95.0
    agg.record("/r", "NFL", 1, 1, 1.0)
    agg.record("/r", "MLB", 1, 1, 1.0)
    assert len(agg.snapshot()) == 2


def test_stage_helpers_are_noops_outside_a_request():
    set_request_dimensions(sport="NBA", row_count=1)
    with stage_timer("discovery"):
        pass
