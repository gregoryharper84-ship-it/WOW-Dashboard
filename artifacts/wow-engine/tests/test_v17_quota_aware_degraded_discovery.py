from __future__ import annotations

from datetime import datetime, timezone

import pytest

from v17 import cross_sport_discovery_feed as feed
from v17 import cross_sport_winner_discovery as discovery
from v17 import quota_aware_degraded_discovery as quota


class _Result:
    def __init__(self, ok: bool, *, code: str | None = None, data=None):
        self.ok = ok
        self.code = code
        self.data = data


def test_definitive_provider_failure_classification_is_conservative():
    assert quota.classify_provider_failure("RUNDOWN_QUOTA_EXHAUSTED") == quota.QUOTA_EXHAUSTED
    assert quota.classify_provider_failure("ODDS_HTTP_403") == quota.AUTH_FAILURE
    assert quota.classify_provider_failure("CREDENTIAL_UNCONFIGURED") == quota.DISABLED_BY_POLICY
    assert quota.classify_provider_failure("HTTP_429_UNKNOWN") == quota.RATE_LIMITED
    assert quota.classify_provider_failure("HTTP_503") == quota.TEMPORARILY_UNAVAILABLE


def test_public_schedule_success_consumes_zero_paid_odds_calls(monkeypatch):
    paid_factory_calls = []
    paid_http_calls = []

    def fake_paid_factory(*args, proxy_get=None, **kwargs):
        paid_factory_calls.append(1)

        def fetch(_family, _target=None):
            paid_http_calls.append(1)
            result = proxy_get("/odds-api/v4/sports", {"all": "true"})
            if not result.ok:
                raise discovery.DiscoveryFeedError(result.code)
            return result.data or []

        return fetch

    monkeypatch.setattr(
        discovery,
        "_v17_cross_sport_resilience_original_odds_proxy_feed",
        fake_paid_factory,
        raising=False,
    )
    monkeypatch.setattr(
        quota,
        "_public_schedule_fetch",
        lambda family, **kwargs: (
            True,
            [{"id": "espn-1", "home_team": "H", "away_team": "A"}],
            None,
        ),
    )

    context = quota._new_context()
    token = quota._SCAN_CONTEXT.set(context)
    try:
        fetch = quota._quota_aware_odds_proxy_factory(
            proxy_get=lambda *_args, **_kwargs: _Result(True, data=[]),
            now=datetime(2026, 9, 23, 12, 0, tzinfo=timezone.utc),
        )
        rows = fetch("NFL")
    finally:
        quota._SCAN_CONTEXT.reset(token)

    assert paid_factory_calls == [1]
    assert paid_http_calls == []
    assert rows[0]["id"] == "espn-1"
    assert context["paid_provider_calls_attempted"] == 0
    assert context["paid_provider_calls_succeeded"] == 0


def test_rundown_quota_exhaustion_trips_one_scan_circuit(monkeypatch):
    calls = []

    def original_factory(*_args, **_kwargs):
        def fetch(_family, _target=None):
            calls.append(1)
            raise discovery.DiscoveryFeedError("RUNDOWN_QUOTA_EXHAUSTED")

        return fetch

    monkeypatch.setattr(
        feed,
        "_v17_quota_aware_original_rundown_board_feed",
        original_factory,
        raising=False,
    )
    context = quota._new_context()
    token = quota._SCAN_CONTEXT.set(context)
    try:
        fetch = quota._quota_aware_rundown_factory(slate_date="2026-09-23")
        with pytest.raises(discovery.DiscoveryFeedError) as first:
            fetch("MLB")
        with pytest.raises(discovery.DiscoveryFeedError) as second:
            fetch("NFL")
    finally:
        quota._SCAN_CONTEXT.reset(token)

    assert first.value.code == "RUNDOWN_QUOTA_EXHAUSTED"
    assert second.value.code.startswith("RUNDOWN_CIRCUIT_OPEN:")
    assert calls == [1]
    assert context["paid_provider_calls_attempted"] == 1
    assert context["paid_provider_calls_blocked_by_quota_policy"] == 1
    assert context["providers"]["RUNDOWN"]["status"] == quota.QUOTA_EXHAUSTED
    assert context["providers"]["RUNDOWN"]["circuit_open"] is True


def test_ambiguous_rate_limit_does_not_become_quota_circuit(monkeypatch):
    calls = []

    def original_factory(*_args, **_kwargs):
        def fetch(_family, _target=None):
            calls.append(1)
            if len(calls) == 1:
                raise discovery.DiscoveryFeedError("RUNDOWN_HTTP_429_UNKNOWN")
            return []

        return fetch

    monkeypatch.setattr(
        feed,
        "_v17_quota_aware_original_rundown_board_feed",
        original_factory,
        raising=False,
    )
    context = quota._new_context()
    token = quota._SCAN_CONTEXT.set(context)
    try:
        fetch = quota._quota_aware_rundown_factory(slate_date="2026-09-23")
        with pytest.raises(discovery.DiscoveryFeedError):
            fetch("MLB")
        assert fetch("NFL") == []
    finally:
        quota._SCAN_CONTEXT.reset(token)

    assert calls == [1, 1]
    assert context["paid_provider_calls_attempted"] == 2
    assert context["paid_provider_calls_blocked_by_quota_policy"] == 0
    assert context["providers"]["RUNDOWN"]["circuit_open"] is False


def test_public_scoreboard_id_remains_provider_alias_not_official_identity():
    event = discovery.DiscoveredEvent(
        sport="NFL",
        league="NFL",
        sport_key="americanfootball_nfl",
        official_event_id="espn-123",
        home_team="Home",
        away_team="Away",
        commence_time_utc="2026-09-23T23:00:00Z",
        event_status="PREGAME",
        source="DISCOVERY_FEED",
        provider="ESPN_SCOREBOARD",
        raw={"id": "espn-123"},
    )
    out = quota._normalize_public_alias(
        {
            "id": "espn-123",
            "provider_event_id": "123",
            "discovery_provider": "ESPN_SCOREBOARD",
        },
        event,
    )

    assert out.official_event_id is None
    assert out.provider == "ESPN_SCOREBOARD"
    assert out.provider_sport_id is None
    assert out.raw["provider_event_id"] == "123"
    assert out.raw["official_event_id"] is None


def test_coverage_truth_never_calls_partial_board_complete():
    context = quota._new_context()
    complete = {
        "discovery": {
            "acquisition_audit": [
                {"family": "NFL", "request_status": "EVENTS_RETURNED", "coverage_complete": True}
            ],
            "source_blockers": [],
        }
    }
    partial = {
        "discovery": {
            "acquisition_audit": [
                {"family": "SOCCER", "request_status": "PROVIDER_RATE_LIMITED", "coverage_complete": False}
            ],
            "source_blockers": [{"sport": "SOCCER", "status": "PROVIDER_RATE_LIMITED"}],
        }
    }

    assert quota._coverage_status(complete, context) == "PROVEN_FOR_CONFIGURED_DISCOVERY_SOURCES"
    assert quota._coverage_status(partial, context) == "PARTIAL_OR_UNPROVEN"
