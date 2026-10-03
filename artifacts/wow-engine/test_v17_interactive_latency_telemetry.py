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


def test_stage_timers_and_percentiles_are_bounded_and_do_not_alter_responses(caplog):
    from v17.interactive_latency_telemetry import (
        AGGREGATOR,
        MAX_SAMPLES_PER_KEY,
        note_request_shape,
        row_count_bucket,
        stage_timer,
    )

    AGGREGATOR.reset()
    app = FastAPI()
    install_interactive_latency_middleware(app)

    @app.post("/score-team-event-request")
    def score_team_event_request():
        note_request_shape(3, {"mlb"})
        with stage_timer("hydration"):
            pass
        with stage_timer("fitted_scoring"):
            pass
        with stage_timer("not_a_stage"):
            pass
        return {"ok": True, "can_execute": False}

    client = TestClient(app)
    with caplog.at_level(logging.WARNING, logger="wow.v17.interactive_latency"):
        response = client.post("/score-team-event-request")
    assert response.json() == {"ok": True, "can_execute": False}
    message = next(r.getMessage() for r in caplog.records if "route=/score-team-event-request" in r.getMessage())
    assert "sport=MLB" in message and "row_count_bucket=2-4" in message
    assert "hydration=" in message and "fitted_scoring=" in message and "not_a_stage" not in message
    assert "can_execute=false" in message

    snapshot = AGGREGATOR.snapshot()
    assert len(snapshot) == 1
    assert snapshot[0]["route"] == "/score-team-event-request"
    assert snapshot[0]["sport"] == "MLB" and snapshot[0]["row_count_bucket"] == "2-4"
    assert snapshot[0]["samples"] == 1 and snapshot[0]["can_execute"] is False

    for _ in range(MAX_SAMPLES_PER_KEY + 10):
        AGGREGATOR.record("/score-prop", "NBA", "1", 5.0)
    prop = [s for s in AGGREGATOR.snapshot() if s["route"] == "/score-prop"][0]
    assert prop["samples"] == MAX_SAMPLES_PER_KEY and prop["p50_ms"] == prop["p95_ms"] == 5.0
    assert row_count_bucket(None) == "unknown" and row_count_bucket(75) == "51-100" and row_count_bucket(500) == "100+"
    AGGREGATOR.reset()
