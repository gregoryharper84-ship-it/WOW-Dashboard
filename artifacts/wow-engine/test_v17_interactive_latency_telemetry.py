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


def test_stage_timers_labels_percentiles_and_typed_outcomes(caplog):
    import time

    from fastapi import HTTPException

    from v17 import interactive_latency_telemetry as t

    t.reset_latency_samples()
    app = FastAPI()
    t.install_interactive_latency_middleware(app)

    @app.post("/score-pick-request")
    def pick():
        t.annotate_request(sport="MLB", row_count=3, batch_size=3)
        with t.stage_timer("hydration"):
            time.sleep(0.01)
        with t.stage_timer("not_a_stage"):
            pass
        return {"ok": True, "can_execute": False}

    @app.post("/score-team-event-request")
    def team():
        raise HTTPException(status_code=504, detail="x")

    @app.post("/score-prop")
    def prop():
        raise HTTPException(status_code=503, detail="x")

    client = TestClient(app)
    with caplog.at_level(logging.WARNING, logger="wow.v17.interactive_latency"):
        for _ in range(3):
            assert client.post("/score-pick-request").status_code == 200
        assert client.post("/score-team-event-request").status_code == 504
        assert client.post("/score-prop").status_code == 503

    messages = [r.getMessage() for r in caplog.records]
    pick = [m for m in messages if "route=/score-pick-request" in m]
    assert len(pick) == 3
    assert all("outcome=ok" in m and "sport=mlb" in m and "row_count=le4" in m for m in pick)
    assert all("hydration=" in m and "not_a_stage" not in m for m in pick)
    assert all("can_execute=false" in m for m in messages)
    assert any("route=/score-team-event-request" in m and "outcome=timeout" in m for m in messages)
    assert any("route=/score-prop" in m and "outcome=overload" in m for m in messages)
    assert not any("MODEL_UNAVAILABLE" in m for m in messages)

    rows = {(r["route"], r["sport"]): r for r in t.latency_percentiles()}
    row = rows[("/score-pick-request", "mlb")]
    assert row["samples"] == 3 and row["p50_ms"] <= row["p95_ms"]
    assert row["can_execute"] is False


def test_stage_timer_is_noop_outside_request_and_propagates_errors():
    import pytest

    from v17 import interactive_latency_telemetry as t

    with t.stage_timer("hydration"):
        pass
    with pytest.raises(ValueError):
        with t.stage_timer("persistence"):
            raise ValueError("boom")


def test_sample_key_cardinality_is_bounded():
    from v17 import interactive_latency_telemetry as t

    t.reset_latency_samples()
    ctx = {"row_count": "le1", "batch_size": "le1"}
    for i in range(t._MAX_KEYS + 50):
        t._record("/score-prop", {**ctx, "sport": f"s{i}"}, 1.0)
    assert len(t._SAMPLES) <= t._MAX_KEYS + 1
    t.reset_latency_samples()
