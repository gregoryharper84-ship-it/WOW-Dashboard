from __future__ import annotations

from datetime import datetime, timezone
from types import SimpleNamespace

from v17 import cross_sport_resilience_overlay as resilience
from v17 import scout_secondary_source as secondary


def test_round_robin_prevents_one_sport_from_consuming_front_of_queue():
    events = [
        SimpleNamespace(sport="MLB", marker="m1"),
        SimpleNamespace(sport="MLB", marker="m2"),
        SimpleNamespace(sport="WNBA", marker="w1"),
        SimpleNamespace(sport="WNBA", marker="w2"),
        SimpleNamespace(sport="MMA", marker="f1"),
    ]

    ordered = resilience._round_robin_events(events)

    assert [item.marker for item in ordered] == ["m1", "w1", "f1", "m2", "w2"]


def test_schedule_first_uses_espn_identity_without_market_fallback(monkeypatch):
    calls = {"espn": 0, "fallback": 0}

    def fake_secondary(path, params, event_context, *, primary_failure=None):
        calls["espn"] += 1
        assert path.endswith("/americanfootball_nfl/events")
        return secondary.SecondaryResult(
            True,
            [
                {
                    "id": "espn-123",
                    "sport_key": "americanfootball_nfl",
                    "commence_time": "2026-09-22T23:00:00Z",
                    "home_team": "Home",
                    "away_team": "Away",
                }
            ],
            200,
        )

    def fallback(_family, _target=None):
        calls["fallback"] += 1
        raise AssertionError("market fallback must not run after ESPN schedule success")

    monkeypatch.setattr(secondary, "secondary_for_request", fake_secondary)
    fetch = resilience._schedule_first_fetch(
        fallback,
        started=datetime(2026, 9, 22, 12, 0, tzinfo=timezone.utc),
        horizon_hours=36,
    )

    rows = fetch("NFL")

    assert calls == {"espn": 1, "fallback": 0}
    assert rows[0]["discovery_provider"] == "ESPN_SCOREBOARD"
    assert rows[0]["prediction_authority"] is False
    assert rows[0]["exact_line_authority"] is False
    assert rows[0]["can_execute"] is False


def test_schedule_first_falls_back_once_when_espn_is_unavailable(monkeypatch):
    calls = {"espn": 0, "fallback": 0}

    def fake_secondary(*_args, **_kwargs):
        calls["espn"] += 1
        return secondary.SecondaryResult(False, status=503, code="ESPN_HTTP_503")

    def fallback(_family, _target=None):
        calls["fallback"] += 1
        return [{"id": "provider-1", "home_team": "H", "away_team": "A"}]

    monkeypatch.setattr(secondary, "secondary_for_request", fake_secondary)
    fetch = resilience._schedule_first_fetch(
        fallback,
        started=datetime(2026, 9, 22, 12, 0, tzinfo=timezone.utc),
        horizon_hours=36,
    )

    rows = fetch("NFL")

    assert rows[0]["id"] == "provider-1"
    assert calls == {"espn": 1, "fallback": 1}


def test_unsupported_family_never_calls_espn(monkeypatch):
    calls = {"espn": 0, "fallback": 0}

    def fake_secondary(*_args, **_kwargs):
        calls["espn"] += 1
        raise AssertionError("ESPN schedule adapter is not configured for soccer")

    def fallback(_family, _target=None):
        calls["fallback"] += 1
        return []

    monkeypatch.setattr(secondary, "secondary_for_request", fake_secondary)
    fetch = resilience._schedule_first_fetch(
        fallback,
        started=datetime(2026, 9, 22, 12, 0, tzinfo=timezone.utc),
        horizon_hours=36,
    )

    assert fetch("SOCCER") == []
    assert calls == {"espn": 0, "fallback": 1}


def test_multi_target_budget_widens_family_without_reintroducing_global_budget(monkeypatch):
    monkeypatch.delenv("WOW_CROSS_SPORT_MULTI_TARGET_BUDGET_SECONDS", raising=False)
    assert resilience.multi_target_budget_seconds(8.0) == 24.0

    monkeypatch.setenv("WOW_CROSS_SPORT_MULTI_TARGET_BUDGET_SECONDS", "12")
    assert resilience.multi_target_budget_seconds(8.0) == 12.0
    assert resilience.multi_target_budget_seconds(30.0) == 30.0


def test_model_invocation_limit_is_operator_bounded(monkeypatch):
    monkeypatch.delenv("WOW_CROSS_SPORT_MAX_MODEL_INVOCATIONS", raising=False)
    assert resilience.model_invocation_limit() == 12

    monkeypatch.setenv("WOW_CROSS_SPORT_MAX_MODEL_INVOCATIONS", "999")
    assert resilience.model_invocation_limit() == 32

    monkeypatch.setenv("WOW_CROSS_SPORT_MAX_MODEL_INVOCATIONS", "0")
    assert resilience.model_invocation_limit() == 1


def test_schedule_first_uses_espn_for_mlb_before_paid_market_provider(monkeypatch):
    calls = {"espn": 0, "fallback": 0}

    def fake_secondary(path, params, event_context, *, primary_failure=None):
        calls["espn"] += 1
        assert path.endswith("/baseball_mlb/events")
        return secondary.SecondaryResult(
            True,
            [{
                "id": "espn-mlb-1",
                "sport_key": "baseball_mlb",
                "commence_time": "2026-09-24T22:05:00Z",
                "home_team": "Phillies",
                "away_team": "Brewers",
            }],
            200,
        )

    def fallback(_family, _target=None):
        calls["fallback"] += 1
        raise AssertionError("paid provider must not be required after MLB schedule success")

    monkeypatch.setattr(secondary, "secondary_for_request", fake_secondary)
    fetch = resilience._schedule_first_fetch(
        fallback,
        started=datetime(2026, 9, 24, 12, 0, tzinfo=timezone.utc),
        horizon_hours=36,
    )

    rows = fetch("MLB")

    assert calls == {"espn": 1, "fallback": 0}
    assert rows[0]["discovery_provider"] == "ESPN_SCOREBOARD"
    assert rows[0]["prediction_authority"] is False
    assert rows[0]["exact_line_authority"] is False
    assert rows[0]["can_execute"] is False
