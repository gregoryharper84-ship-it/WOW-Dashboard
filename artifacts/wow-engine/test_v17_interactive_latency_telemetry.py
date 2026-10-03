import logging

import pytest
from fastapi import FastAPI
from fastapi.testclient import TestClient

from v17.interactive_latency_telemetry import (
    AGGREGATOR,
    LatencyAggregator,
    annotate_request,
    install_interactive_latency_middleware,
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

    messages = [
        record.getMessage()
        for record in caplog.records
        if "WOW_V17_INTERACTIVE_STAGE_TIMING" not in record.getMessage()
    ]
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


def test_stage_records_are_emitted_without_body_content_and_preserve_errors(caplog):
    AGGREGATOR.clear()
    app = FastAPI()
    install_interactive_latency_middleware(app)

    @app.post("/score-pick-request")
    def score_pick_request():
        annotate_request(sport="nba", row_count=3, batch_size=3)
        with stage_timer("hydration"):
            pass
        with stage_timer("fitted_scoring"):
            pass
        return {"ok": True}

    @app.post("/score-prop")
    def boom():
        with pytest.raises(ValueError):
            with stage_timer("persistence"):
                raise ValueError("kept")
        raise RuntimeError("handler")

    client = TestClient(app, raise_server_exceptions=False)
    with caplog.at_level(logging.WARNING, logger="wow.v17.interactive_latency"):
        assert client.post("/score-pick-request", json={"secret": "SENTINEL_BODY"}).status_code == 200
        assert client.post("/score-prop").status_code == 500

    messages = [r.getMessage() for r in caplog.records]
    stage = [m for m in messages if "INTERACTIVE_STAGE_TIMING route=/score-pick-request" in m]
    assert len(stage) == 1
    assert "sport=NBA" in stage[0] and "row_count=<=4" in stage[0]
    assert "hydration:" in stage[0] and "fitted_scoring:" in stage[0]
    assert "can_execute=false" in stage[0]
    assert not any("SENTINEL_BODY" in m for m in messages)
    assert any("INTERACTIVE_LATENCY route=/score-prop" in m and "status_code=500" in m for m in messages)
    assert any(e["stage"] == "total" and e["route"] == "/score-pick-request" for e in AGGREGATOR.summary())


def test_aggregator_percentiles_and_bounds():
    agg = LatencyAggregator(max_keys=2, max_samples=5)
    for i in range(1, 11):
        agg.record("/r", "NBA", 1, 1, "total", float(i))
    agg.record("/r", "NFL", 1, 1, "total", 1.0)
    agg.record("/r", "MLB", 1, 1, "total", 1.0)  # dropped: key cap
    rows = {r["sport"]: r for r in agg.summary()}
    assert set(rows) == {"NBA", "NFL"}
    assert rows["NBA"]["count"] == 5  # sample cap keeps newest 6..10
    assert rows["NBA"]["p50_ms"] == 8.0
    assert rows["NBA"]["p95_ms"] == 10.0
    assert all(r["can_execute"] is False for r in rows.values())
