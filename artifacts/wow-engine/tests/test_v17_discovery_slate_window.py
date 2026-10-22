"""Discovery query windows are bound to the requested local slate day.

Production reproduction (2026-10-22 Daily run, 19:16Z / 14:16 CDT): the feed asked
providers for ``now .. now+36h``. For a one-day local slate that pulled roughly 21
hours of tomorrow's games, which ``_purge_bucket`` then (correctly) terminated as
``EVENT_WRONG_DATE`` — 20 of the 40 rows in that run.

This is discovery-window plumbing only. Classification, routing and probabilities
are unchanged, and ``can_execute`` stays false.
"""
from __future__ import annotations

from datetime import datetime, timedelta, timezone

import pytest

from v17 import cross_sport_discovery_feed as discovery_feed
from v17 import cross_sport_resilience_overlay as resilience
from v17 import cross_sport_winner_discovery as discovery
from v17 import quota_aware_degraded_discovery as quota
from v17 import scout_secondary_source as secondary

CHICAGO = "America/Chicago"
# The production run that reproduced the defect.
RUN_START = datetime(2026, 10, 22, 19, 16, 31, tzinfo=timezone.utc)
HORIZON_END = RUN_START + timedelta(hours=36)


def _in_slate(slate_date: str, tz_name: str = CHICAGO):
    token = discovery._SLATE_FETCH_WINDOW.set((slate_date, tz_name))
    return token


@pytest.fixture(autouse=True)
def _clean_context():
    assert discovery._SLATE_FETCH_WINDOW.get() is None
    yield
    assert discovery._SLATE_FETCH_WINDOW.get() is None


# ---------------------------------------------------------------- helper unit


def test_no_slate_context_keeps_the_original_window():
    assert discovery.slate_bounded_window(RUN_START, HORIZON_END) == (RUN_START, HORIZON_END)


def test_same_day_slate_window_ends_at_local_midnight_not_now_plus_36h():
    token = _in_slate("2026-10-22")
    try:
        start, end = discovery.slate_bounded_window(RUN_START, HORIZON_END)
    finally:
        discovery._SLATE_FETCH_WINDOW.reset(token)
    assert start == RUN_START
    # Local midnight Oct 23 CDT (UTC-5) is 05:00Z; upper bound is exclusive.
    assert end == datetime(2026, 10, 23, 4, 59, 59, tzinfo=timezone.utc)
    assert end < HORIZON_END


def test_window_excludes_every_observed_wrong_date_start():
    """The 20 production wrong-date rows started Oct 23 20:00Z .. Oct 24 02:30Z."""
    token = _in_slate("2026-10-22")
    try:
        _, end = discovery.slate_bounded_window(RUN_START, HORIZON_END)
    finally:
        discovery._SLATE_FETCH_WINDOW.reset(token)
    for observed in ("2026-10-23T20:00:00+00:00", "2026-10-24T02:30:00+00:00"):
        assert datetime.fromisoformat(observed) > end


def test_window_still_includes_in_slate_starts():
    token = _in_slate("2026-10-22")
    try:
        start, end = discovery.slate_bounded_window(RUN_START, HORIZON_END)
    finally:
        discovery._SLATE_FETCH_WINDOW.reset(token)
    # Production in-slate rows: NHL 23:00Z, NFL 00:15Z (Oct 23), NHL 02:00Z (Oct 23).
    for observed in ("2026-10-22T23:00:00+00:00", "2026-10-23T00:15:00+00:00", "2026-10-23T02:00:00+00:00"):
        assert start <= datetime.fromisoformat(observed) <= end


def test_later_slate_is_not_truncated_by_the_rolling_horizon():
    short_end = RUN_START + timedelta(hours=12)
    token = _in_slate("2026-10-24")
    try:
        start, end = discovery.slate_bounded_window(RUN_START, short_end)
    finally:
        discovery._SLATE_FETCH_WINDOW.reset(token)
    assert start == datetime(2026, 10, 24, 5, 0, tzinfo=timezone.utc)
    assert end == datetime(2026, 10, 25, 4, 59, 59, tzinfo=timezone.utc)
    assert end > short_end


def test_past_slate_falls_back_to_the_original_window():
    token = _in_slate("2026-10-20")
    try:
        assert discovery.slate_bounded_window(RUN_START, HORIZON_END) == (RUN_START, HORIZON_END)
    finally:
        discovery._SLATE_FETCH_WINDOW.reset(token)


def test_slate_beyond_the_cap_falls_back_to_the_original_window():
    token = _in_slate("2026-12-31")
    try:
        assert discovery.slate_bounded_window(RUN_START, HORIZON_END) == (RUN_START, HORIZON_END)
    finally:
        discovery._SLATE_FETCH_WINDOW.reset(token)


def test_malformed_slate_date_falls_back_to_the_original_window():
    token = _in_slate("not-a-date")
    try:
        assert discovery.slate_bounded_window(RUN_START, HORIZON_END) == (RUN_START, HORIZON_END)
    finally:
        discovery._SLATE_FETCH_WINDOW.reset(token)


def test_unusable_timezone_mirrors_the_classifier_and_uses_utc():
    token = _in_slate("2026-10-22", "Nope/Zone")
    try:
        start, end = discovery.slate_bounded_window(RUN_START, HORIZON_END)
    finally:
        discovery._SLATE_FETCH_WINDOW.reset(token)
    assert start == RUN_START
    assert end == datetime(2026, 10, 22, 23, 59, 59, tzinfo=timezone.utc)


def test_dst_fall_back_day_uses_the_real_local_day_length():
    # 2026-11-01: US clocks fall back; local midnight Nov 2 is CST (UTC-6) = 06:00Z.
    started = datetime(2026, 11, 1, 6, 0, tzinfo=timezone.utc)
    token = _in_slate("2026-11-01")
    try:
        start, end = discovery.slate_bounded_window(started, started + timedelta(hours=36))
    finally:
        discovery._SLATE_FETCH_WINDOW.reset(token)
    # Local midnight Nov 1 is still CDT (05:00Z), earlier than `started`, so `now` wins.
    assert start == started
    assert end == datetime(2026, 11, 2, 5, 59, 59, tzinfo=timezone.utc)


def test_window_is_always_well_ordered():
    for slate in ("2026-10-22", "2026-10-23", "2026-10-26"):
        token = _in_slate(slate)
        try:
            start, end = discovery.slate_bounded_window(RUN_START, HORIZON_END)
        finally:
            discovery._SLATE_FETCH_WINDOW.reset(token)
        assert start < end


# -------------------------------------------------- wrapper + context lifecycle


def test_discover_winner_slate_publishes_then_clears_the_context():
    seen = []

    def fetch(family, target=None):
        seen.append(discovery._SLATE_FETCH_WINDOW.get())
        return []

    discovery.discover_winner_slate(
        requested_slate_date="2026-10-22",
        requested_timezone=CHICAGO,
        fetch_sport_events=fetch,
        supported_sports=("NHL",),
        now=RUN_START,
    )
    assert seen and all(item == ("2026-10-22", CHICAGO) for item in seen)
    assert discovery._SLATE_FETCH_WINDOW.get() is None


def test_context_is_cleared_when_a_feed_raises_unexpectedly():
    def fetch(family, target=None):
        raise RuntimeError("boom")

    try:
        discovery.discover_winner_slate(
            requested_slate_date="2026-10-22",
            requested_timezone=CHICAGO,
            fetch_sport_events=fetch,
            supported_sports=("NHL",),
            now=RUN_START,
        )
    except Exception:  # noqa: BLE001 - discovery may record or re-raise; only cleanup matters
        pass
    assert discovery._SLATE_FETCH_WINDOW.get() is None


# ------------------------------------ real adapters, through a discovery pass


def _capture_secondary(monkeypatch):
    params_seen = []

    def fake(path, params, event_context, *, primary_failure=None):
        params_seen.append(dict(params))
        return secondary.SecondaryResult(True, [], 200)

    monkeypatch.setattr(secondary, "secondary_for_request", fake)
    return params_seen


def test_resilience_schedule_first_queries_the_slate_day_inside_a_discovery_pass(monkeypatch):
    params_seen = _capture_secondary(monkeypatch)
    fetch = resilience._schedule_first_fetch(
        lambda *_a, **_k: [], started=RUN_START, horizon_hours=36
    )
    discovery.discover_winner_slate(
        requested_slate_date="2026-10-22",
        requested_timezone=CHICAGO,
        fetch_sport_events=fetch,
        supported_sports=("NHL",),
        now=RUN_START,
    )
    assert params_seen
    assert all(p["commenceTimeFrom"] == "2026-10-22T19:16:31Z" for p in params_seen)
    assert all(p["commenceTimeTo"] == "2026-10-23T04:59:59Z" for p in params_seen)


def test_resilience_schedule_first_is_unchanged_outside_a_discovery_pass(monkeypatch):
    params_seen = _capture_secondary(monkeypatch)
    fetch = resilience._schedule_first_fetch(
        lambda *_a, **_k: [], started=RUN_START, horizon_hours=36
    )
    fetch("NHL")
    assert params_seen[0]["commenceTimeTo"] == "2026-10-24T07:16:31Z"  # now + 36h, as before


def test_quota_aware_public_fetch_queries_the_slate_day(monkeypatch):
    params_seen = _capture_secondary(monkeypatch)
    token = _in_slate("2026-10-22")
    try:
        quota._public_schedule_fetch("NHL", started=RUN_START, horizon_hours=36)
    finally:
        discovery._SLATE_FETCH_WINDOW.reset(token)
    assert params_seen[0]["commenceTimeFrom"] == "2026-10-22T19:16:31Z"
    assert params_seen[0]["commenceTimeTo"] == "2026-10-23T04:59:59Z"


def test_quota_aware_public_fetch_is_unchanged_outside_a_discovery_pass(monkeypatch):
    params_seen = _capture_secondary(monkeypatch)
    quota._public_schedule_fetch("NHL", started=RUN_START, horizon_hours=36)
    assert params_seen[0]["commenceTimeTo"] == "2026-10-24T07:16:31Z"


def test_odds_proxy_feed_queries_the_slate_day(monkeypatch):
    calls = []

    class _Resp:
        def __init__(self, data):
            self.ok = True
            self.data = data
            self.code = None

    def proxy_get(path, params=None, *args, **kwargs):
        if path.endswith("/sports"):
            return _Resp([{"key": "icehockey_nhl", "active": True}])
        calls.append(dict(params or {}))
        return _Resp([])

    fetch = discovery_feed.odds_proxy_feed(
        proxy_get=proxy_get, now=RUN_START, sport_keys={"NHL": ("icehockey_nhl",)}
    )
    discovery.discover_winner_slate(
        requested_slate_date="2026-10-22",
        requested_timezone=CHICAGO,
        fetch_sport_events=fetch,
        supported_sports=("NHL",),
        now=RUN_START,
    )
    assert calls
    assert calls[0]["commenceTimeTo"] == "2026-10-23T04:59:59Z"


# ------------------------------------------- classification is untouched


def test_wrong_date_classification_is_unchanged():
    """An event outside the slate is still purged; this patch never widens or hides it."""
    assert discovery._slate_date_matches("2026-10-24T01:00Z", "2026-10-22", CHICAGO) is False
    assert discovery._slate_date_matches("2026-10-23T00:15Z", "2026-10-22", CHICAGO) is True
    assert discovery.WRONG_DATE == "EVENT_WRONG_DATE"
