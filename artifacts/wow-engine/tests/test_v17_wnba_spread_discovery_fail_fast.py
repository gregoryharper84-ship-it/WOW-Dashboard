from __future__ import annotations

from datetime import datetime, timezone

import pytest

import v17.spread_forward_auto_canary as canary
from v17.spread_margin_challenger import SpreadChallengerUnavailable

NOW = datetime(2026, 9, 30, 12, 0, tzinfo=timezone.utc)


def test_espn_transport_failure_fails_over_after_one_date_attempt():
    calls = []

    def failing_fetcher(*args, **kwargs):
        calls.append((args, kwargs))
        raise TimeoutError("source unavailable")

    with pytest.raises(SpreadChallengerUnavailable) as exc:
        canary.discover_future_espn_event(
            "WNBA",
            fetcher=failing_fetcher,
            now=NOW,
            horizon_days=15,
        )

    assert exc.value.code == "WNBA_SPREAD_CANARY_IDENTITY_SOURCE_UNAVAILABLE"
    assert len(calls) == 1


def test_wnba_stats_source_failure_stops_after_one_date_attempt(monkeypatch):
    import v17.wnba_prop_evidence_control_plane as control

    calls = []

    def failing_scoreboard(requested_date, *, http_get):
        calls.append((requested_date, http_get))
        raise RuntimeError("stats unavailable")

    monkeypatch.setattr(control, "_scoreboard_schedule_for_date", failing_scoreboard)

    with pytest.raises(SpreadChallengerUnavailable) as exc:
        canary.discover_future_wnba_stats_event(
            fetcher=object(),
            now=NOW,
            horizon_days=15,
        )

    assert exc.value.code == "WNBA_SPREAD_CANARY_IDENTITY_SOURCE_UNAVAILABLE"
    assert len(calls) == 1


def test_valid_empty_espn_days_still_scan_the_requested_horizon():
    calls = []

    class Response:
        status_code = 200

        @staticmethod
        def json():
            return {"events": []}

    def fetcher(*args, **kwargs):
        calls.append((args, kwargs))
        return Response()

    assert canary.discover_future_espn_event(
        "WNBA",
        fetcher=fetcher,
        now=NOW,
        horizon_days=3,
    ) is None
    assert len(calls) == 3
