import logging

from fastapi import FastAPI
from fastapi.testclient import TestClient

from v17.interactive_latency_telemetry import (
    AGGREGATOR,
    LatencyAggregator,
    install_interactive_latency_middleware,
    note_interactive_dimensions,
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


def test_stage_timings_dimensions_and_aggregation_are_non_secret(caplog):
    AGGREGATOR.reset()
    app = FastAPI()
    install_interactive_latency_middleware(app)

    @app.post("/score-pick-request")
    async def score_pick_request(payload: dict):
        note_interactive_dimensions(sport="nba", row_count=3, batch_size=3)
        with stage_timer("hydration"):
            pass
        with stage_timer("fitted_scoring"):
            pass
        with stage_timer("not_a_stage"):
            pass
        return {"ok": True, "can_execute": False}

    client = TestClient(app)
    with caplog.at_level(logging.WARNING, logger="wow.v17.interactive_latency"):
        assert client.post("/score-pick-request", json={"secret": "SENTINEL_SECRET"}).status_code == 200

    message = [r.getMessage() for r in caplog.records if "route=/score-pick-request" in r.getMessage()][0]
    assert "sport=NBA row_count=3 batch_size=3" in message
    assert "hydration_ms=" in message and "fitted_scoring_ms=" in message
    assert "persistence_ms=" not in message and "not_a_stage" not in message
    assert "SENTINEL_SECRET" not in message
    assert "can_execute=false" in message
    rows = AGGREGATOR.summary()
    assert len(rows) == 1 and rows[0]["count"] == 1 and rows[0]["can_execute"] is False


def test_stage_timer_outside_request_is_noop():
    with stage_timer("hydration"):
        pass
    note_interactive_dimensions(sport="NBA", row_count=1)


def test_latency_aggregator_percentiles_and_edges():
    agg = LatencyAggregator()
    assert agg.summary() == []
    agg.record("/r", "NBA", 1, 1, 7.0)
    single = agg.summary()[0]
    assert single["p50_ms"] == single["p95_ms"] == 7.0
    for value in range(1, 101):
        agg.record("/r", "MLB", 10, 5, float(value))
    mlb = [row for row in agg.summary() if row["sport"] == "MLB"][0]
    assert (mlb["count"], mlb["p50_ms"], mlb["p95_ms"]) == (100, 50.0, 95.0)
