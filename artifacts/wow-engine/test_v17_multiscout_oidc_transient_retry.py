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


def test_prop_post_is_not_retried_by_team_event_retry_policy(monkeypatch):
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
