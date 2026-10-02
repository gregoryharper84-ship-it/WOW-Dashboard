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


def test_stage_timer_logs_all_stages_without_secrets(caplog):
    from v17.interactive_latency_telemetry import INTERACTIVE_STAGES, InteractiveStageTimer

    ticks = iter(float(i) for i in range(100))
    timer = InteractiveStageTimer("/score-team-event-request", clock=lambda: next(ticks))
    for name in INTERACTIVE_STAGES[:-1]:
        with timer.stage(name):
            pass
    with caplog.at_level(logging.WARNING, logger="wow.v17.interactive_latency"):
        timer.log(sport="nfl", row_count=64, batch_size=64)
    message = caplog.records[-1].getMessage()
    assert all(f"{name}_ms=" in message for name in INTERACTIVE_STAGES)
    assert "can_execute=false" in message
    assert "row_count=le_100" in message


def test_latency_aggregator_p50_p95_and_bounds():
    from v17.interactive_latency_telemetry import LatencyAggregator, MAX_SERIES

    agg = LatencyAggregator()
    assert agg.snapshot() == {}
    agg.record("/r", 5.0)
    assert agg.snapshot()[("/r", "unknown", "unknown", "unknown")]["p95_ms"] == 5.0
    for value in range(1, 101):
        agg.record("/s", float(value), sport="nba", row_count=64, batch_size=64)
    stats = agg.snapshot()[("/s", "nba", "le_100", "le_100")]
    assert (stats["count"], stats["p50_ms"], stats["p95_ms"]) == (100, 50.0, 95.0)
    for i in range(MAX_SERIES + 10):
        agg.record(f"/x{i}", 1.0)
    assert len(agg.snapshot()) <= MAX_SERIES
