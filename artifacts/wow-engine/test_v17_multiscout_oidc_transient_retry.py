from __future__ import annotations

from v17 import multiscout_auto_advance_oidc as advance_oidc


def _team_payload(sport: str = "MLB", league: str | None = None) -> dict:
    return {
        "rows": [
            {
                "research_run_id": "wow-scout-retry-test",
                "sport": sport,
                "league": league or sport,
                "event_key": f"{sport}:event-1",
                "objective_lane": "OUTRIGHT_WIN_PROBABILITY",
            }
        ]
    }


def _prop_payload(*, request_id: str | None = "wow-scout-retry-test:props:1") -> dict:
    payload = {
        "rows": [
            {
                "row_key": "scout-prop-1",
                "event_id": "event-1",
                "event_start_time": "2026-09-23T19:00:00Z",
                "sport": "MLB",
                "player": "Example Player",
                "stat_type": "PLAYER_STRIKEOUTS",
                "line": 4.5,
                "direction": "MORE",
                "source_type": "AUTONOMOUS_DISCOVERY",
            }
        ]
    }
    if request_id is not None:
        payload["request_id"] = request_id
    return payload


def _transport_timeout() -> dict:
    return {
        "ok": False,
        "http_status": None,
        "body": {"code": "AUTO_ADVANCE_TRANSPORT_FAILURE", "error_type": "TimeoutError"},
        "can_execute": False,
    }


def test_repeat_safe_mlb_batch_retries_transient_502_and_recovers(monkeypatch):
    receipts = [
        {"ok": False, "http_status": 502, "body": {"code": "HTTP_502"}, "can_execute": False},
        {"ok": True, "http_status": 200, "body": {"rows": [{"terminal_status": "HELD"}]}, "can_execute": False},
    ]
    posts = []
    sleeps = []

    def fake_post(origin, path, token, payload, timeout=120):
        posts.append((path, token, payload, timeout))
        return receipts.pop(0)

    monkeypatch.setattr(advance_oidc, "_post_json", fake_post)
    monkeypatch.setattr(advance_oidc.time, "sleep", lambda seconds: sleeps.append(seconds))
    receipt = advance_oidc._refreshing_oidc_post("oidc-token")(
        "https://engine.example",
        "/score-team-event-request",
        "ignored",
        _team_payload(),
    )

    assert receipt["ok"] is True
    assert receipt["http_status"] == 200
    assert receipt["initial_http_status"] == 502
    assert receipt["transient_retry_count"] == 1
    assert receipt["repeat_safe_retry"] is True
    assert receipt["can_execute"] is False
    assert len(posts) == 2
    assert sleeps == [1.0]


def test_repeat_safe_mlb_batch_retries_transport_timeout_and_recovers(monkeypatch):
    receipts = [
        _transport_timeout(),
        {"ok": True, "http_status": 200, "body": {"rows": [{"terminal_status": "HELD"}]}, "can_execute": False},
    ]
    posts = []
    sleeps = []

    def fake_post(origin, path, token, payload, timeout=120):
        posts.append((path, token, payload, timeout))
        return receipts.pop(0)

    monkeypatch.setattr(advance_oidc, "_post_json", fake_post)
    monkeypatch.setattr(advance_oidc.time, "sleep", lambda seconds: sleeps.append(seconds))
    receipt = advance_oidc._refreshing_oidc_post("oidc-token")(
        "https://engine.example",
        "/score-team-event-request",
        "ignored",
        _team_payload(),
    )

    assert receipt["ok"] is True
    assert receipt["http_status"] == 200
    assert receipt["initial_http_status"] is None
    assert receipt["initial_transport_code"] == "AUTO_ADVANCE_TRANSPORT_FAILURE"
    assert receipt["transient_retry_count"] == 1
    assert receipt["repeat_safe_retry"] is True
    assert len(posts) == 2
    assert sleeps == [1.0]


def test_repeat_safe_mlb_batch_retries_at_most_twice_and_stays_fail_closed(monkeypatch):
    posts = []
    sleeps = []

    def fake_post(origin, path, token, payload, timeout=120):
        posts.append((path, token, timeout))
        return {"ok": False, "http_status": 503, "body": {"code": "HTTP_503"}, "can_execute": False}

    monkeypatch.setattr(advance_oidc, "_post_json", fake_post)
    monkeypatch.setattr(advance_oidc.time, "sleep", lambda seconds: sleeps.append(seconds))
    receipt = advance_oidc._refreshing_oidc_post("oidc-token")(
        "https://engine.example",
        "/score-team-event-request",
        "ignored",
        _team_payload(),
    )

    assert receipt["ok"] is False
    assert receipt["http_status"] == 503
    assert receipt["initial_http_status"] == 503
    assert receipt["transient_retry_count"] == 2
    assert receipt["repeat_safe_retry"] is True
    assert receipt["can_execute"] is False
    assert len(posts) == 3
    assert sleeps == [1.0, 3.0]


def test_durable_prop_batch_retries_transport_timeout_with_exact_request_identity(monkeypatch):
    receipts = [
        _transport_timeout(),
        {"ok": True, "http_status": 200, "body": {"rows": [{"row_key": "scout-prop-1", "terminal_status": "HELD"}]}, "can_execute": False},
    ]
    posts = []
    sleeps = []

    def fake_post(origin, path, token, payload, timeout=120):
        posts.append((path, payload.get("request_id"), payload["rows"][0].get("row_key")))
        return receipts.pop(0)

    monkeypatch.setattr(advance_oidc, "_post_json", fake_post)
    monkeypatch.setattr(advance_oidc.time, "sleep", lambda seconds: sleeps.append(seconds))
    receipt = advance_oidc._refreshing_oidc_post("oidc-token")(
        "https://engine.example",
        "/score-pick-request",
        "ignored",
        _prop_payload(),
    )

    assert receipt["ok"] is True
    assert receipt["transient_retry_count"] == 1
    assert receipt["repeat_safe_retry"] is True
    assert posts == [
        ("/score-pick-request", "wow-scout-retry-test:props:1", "scout-prop-1"),
        ("/score-pick-request", "wow-scout-retry-test:props:1", "scout-prop-1"),
    ]
    assert sleeps == [1.0]


def test_prop_without_exact_request_id_does_not_retry_transport_timeout(monkeypatch):
    posts = []
    sleeps = []

    def fake_post(origin, path, token, payload, timeout=120):
        posts.append(path)
        return _transport_timeout()

    monkeypatch.setattr(advance_oidc, "_post_json", fake_post)
    monkeypatch.setattr(advance_oidc.time, "sleep", lambda seconds: sleeps.append(seconds))
    receipt = advance_oidc._refreshing_oidc_post("oidc-token")(
        "https://engine.example",
        "/score-pick-request",
        "ignored",
        _prop_payload(request_id=None),
    )

    assert receipt["ok"] is False
    assert "transient_retry_count" not in receipt
    assert posts == ["/score-pick-request"]
    assert sleeps == []


def test_nfl_immutable_batch_does_not_retry_transient_gateway_failure(monkeypatch):
    posts = []
    sleeps = []

    def fake_post(origin, path, token, payload, timeout=120):
        posts.append((path, token, payload, timeout))
        return {"ok": False, "http_status": 502, "body": {"code": "HTTP_502"}, "can_execute": False}

    monkeypatch.setattr(advance_oidc, "_post_json", fake_post)
    monkeypatch.setattr(advance_oidc.time, "sleep", lambda seconds: sleeps.append(seconds))
    receipt = advance_oidc._refreshing_oidc_post("oidc-token")(
        "https://engine.example",
        "/score-team-event-request",
        "ignored",
        _team_payload("NFL"),
    )

    assert receipt["ok"] is False
    assert receipt["http_status"] == 502
    assert "transient_retry_count" not in receipt
    assert receipt["can_execute"] is False
    assert len(posts) == 1
    assert sleeps == []


def test_nfl_immutable_batch_does_not_retry_transport_timeout(monkeypatch):
    posts = []
    sleeps = []

    def fake_post(origin, path, token, payload, timeout=120):
        posts.append((path, token, payload, timeout))
        return _transport_timeout()

    monkeypatch.setattr(advance_oidc, "_post_json", fake_post)
    monkeypatch.setattr(advance_oidc.time, "sleep", lambda seconds: sleeps.append(seconds))
    receipt = advance_oidc._refreshing_oidc_post("oidc-token")(
        "https://engine.example",
        "/score-team-event-request",
        "ignored",
        _team_payload("NFL"),
    )

    assert receipt["ok"] is False
    assert "transient_retry_count" not in receipt
    assert len(posts) == 1
    assert sleeps == []


def test_non_transient_mlb_failure_is_not_retried(monkeypatch):
    posts = []
    sleeps = []

    def fake_post(origin, path, token, payload, timeout=120):
        posts.append((path, token, payload, timeout))
        return {"ok": False, "http_status": 422, "body": {"code": "TEAM_EVENT_CONTRACT_INVALID"}, "can_execute": False}

    monkeypatch.setattr(advance_oidc, "_post_json", fake_post)
    monkeypatch.setattr(advance_oidc.time, "sleep", lambda seconds: sleeps.append(seconds))
    receipt = advance_oidc._refreshing_oidc_post("oidc-token")(
        "https://engine.example",
        "/score-team-event-request",
        "ignored",
        _team_payload(),
    )

    assert receipt["ok"] is False
    assert receipt["http_status"] == 422
    assert "transient_retry_count" not in receipt
    assert len(posts) == 1
    assert sleeps == []


def test_prop_post_is_not_retried_without_durable_request_identity(monkeypatch):
    posts = []

    def fake_post(origin, path, token, payload, timeout=120):
        posts.append(path)
        return {"ok": False, "http_status": 502, "body": {"code": "HTTP_502"}, "can_execute": False}

    monkeypatch.setattr(advance_oidc, "_post_json", fake_post)
    monkeypatch.setattr(advance_oidc.time, "sleep", lambda _seconds: None)
    receipt = advance_oidc._refreshing_oidc_post("oidc-token")(
        "https://engine.example",
        "/score-pick-request",
        "ignored",
        {"rows": []},
    )

    assert receipt["ok"] is False
    assert posts == ["/score-pick-request"]
    assert receipt["can_execute"] is False
