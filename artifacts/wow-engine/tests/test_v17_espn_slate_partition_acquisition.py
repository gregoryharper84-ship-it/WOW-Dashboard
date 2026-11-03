"""ESPN scoreboard Eastern-date partitions for a Central evening slate.

The exact UTC acquisition window must not be mistaken for ESPN's
dates=YYYYMMDD partition calendar. Schedule evidence is research-only.
"""
from __future__ import annotations

from datetime import datetime, timezone

import pytest

from v17 import cross_sport_resilience_overlay as resilience
from v17 import cross_sport_winner_discovery as discovery
from v17 import scout_secondary_source as secondary

# 00:05Z is 6:05 PM CST on Nov 2 (not 7:05 PM CST).
RUN_START = datetime(2026, 11, 3, 0, 5, tzinfo=timezone.utc)
SLATE_END = "2026-11-03T05:59:59Z"

EVENTS = {
    "nfl": ("90000001", "2026-11-03T01:15:00Z", "NFL Home", "NFL Away"),
    "nba": ("90000002", "2026-11-03T01:30:00Z", "NBA Home", "NBA Away"),
    "nhl": ("90000003", "2026-11-03T01:00:00Z", "NHL Home", "NHL Away"),
}
SPORT_KEYS = {
    "NFL": "americanfootball_nfl",
    "NBA": "basketball_nba",
    "NHL": "icehockey_nhl",
}


@pytest.fixture(autouse=True)
def _clear_scoreboard_cache():
    secondary._SCOREBOARD_CACHE.clear()
    yield
    secondary._SCOREBOARD_CACHE.clear()


def _event(event_id: str, start: str, home: str, away: str) -> dict:
    return {
        "id": event_id,
        "date": start,
        "competitions": [{
            "competitors": [
                {"homeAway": "home", "team": {"id": f"{event_id}-h", "displayName": home}},
                {"homeAway": "away", "team": {"id": f"{event_id}-a", "displayName": away}},
            ],
        }],
        "season": {"year": 2026, "type": 2},
    }


def _window(start: str = "2026-11-03T00:05:00Z") -> dict:
    return {"commenceTimeFrom": start, "commenceTimeTo": SLATE_END}


def _mock_scoreboards(monkeypatch):
    calls = []

    def fake_http(url, params):
        league = url.rstrip("/").split("/")[-2]
        partition = params["dates"]
        calls.append((league, partition))
        events = [
            _event(*EVENTS[league])
        ] if partition == "20261102" else []
        return secondary.SecondaryResult(True, {"events": events, "total": len(events)}, 200)

    monkeypatch.setattr(secondary, "_http_json", fake_http)
    return calls


def test_eastern_partitions_recover_previous_utc_day_evening():
    # Original UTC-only logic returned ["20261103"], losing three games.
    assert secondary._date_keys(_window()) == ["20261102", "20261103"]
    # Correct clock conversion: 7:05 PM CST = 01:05Z the next day.
    assert secondary._date_keys(_window("2026-11-03T01:05:00Z")) == [
        "20261102", "20261103"
    ]


def test_date_only_lookup_stays_on_explicit_provider_date():
    assert secondary._date_keys({
        "commenceTimeFrom": "20261004",
        "commenceTimeTo": "20261004",
    }) == ["20261004"]
    assert secondary._date_keys({
        "commenceTimeFrom": "2026-10-04",
        "commenceTimeTo": "2026-10-04",
    }) == ["20261004"]


def test_same_eastern_day_uses_one_partition():
    assert secondary._date_keys({
        "commenceTimeFrom": "2026-11-02T17:00:00Z",
        "commenceTimeTo": "2026-11-02T23:00:00Z",
    }) == ["20261102"]


def test_eastern_dst_fall_back_retains_original_utc_day_coverage():
    # Both timestamps are Nov 1 Eastern despite falling on two UTC dates;
    # additive partitioning must preserve the original Nov 2 UTC request.
    assert secondary._date_keys({
        "commenceTimeFrom": "2026-11-01T04:10:00Z",
        "commenceTimeTo": "2026-11-02T02:00:00Z",
    }) == ["20261101", "20261102"]


@pytest.mark.parametrize("family", ["NFL", "NBA", "NHL"])
def test_secondary_events_recovered_for_each_family(monkeypatch, family):
    calls = _mock_scoreboards(monkeypatch)
    result = secondary.secondary_for_request(
        f"/odds-api/v4/sports/{SPORT_KEYS[family]}/events",
        _window(),
        {},
    )
    assert result.ok
    assert len(result.data) == 1
    assert result.data[0]["id"] == f"espn-{EVENTS[family.lower()][0]}"
    assert result.data[0]["_wow_secondary_scoreboard_dates"] == ["20261102"]
    assert calls == [
        (family.lower(), "20261102"),
        (family.lower(), "20261103"),
    ]


def test_exact_window_excludes_started_games_without_losing_later_games(monkeypatch):
    _mock_scoreboards(monkeypatch)
    # At 7:05 PM CST, the synthetic 01:00Z NHL event has already started.
    nhl = secondary.secondary_for_request(
        "/odds-api/v4/sports/icehockey_nhl/events",
        _window("2026-11-03T01:05:00Z"),
        {},
    )
    nfl = secondary.secondary_for_request(
        "/odds-api/v4/sports/americanfootball_nfl/events",
        _window("2026-11-03T01:05:00Z"),
        {},
    )
    assert nhl.ok and nhl.data == []
    assert nfl.ok and len(nfl.data) == 1


def test_exact_window_excludes_next_slate_events(monkeypatch):
    def fake_http(_url, params):
        events = []
        if params["dates"] == "20261102":
            events = [
                _event("too-early", "2026-11-02T23:00:00Z", "Before", "Start"),
                _event("valid", "2026-11-03T01:30:00Z", "In", "Window"),
            ]
        if params["dates"] == "20261103":
            events = [_event("too-late", "2026-11-03T06:00:00Z", "Next", "Day")]
        return secondary.SecondaryResult(True, {"events": events, "total": len(events)}, 200)

    monkeypatch.setattr(secondary, "_http_json", fake_http)
    result = secondary.secondary_for_request(
        "/odds-api/v4/sports/basketball_nba/events", _window(), {}
    )
    assert result.ok
    assert [event["id"] for event in result.data] == ["espn-valid"]


def test_schedule_first_three_families_return_events_in_discovery_context(monkeypatch):
    calls = _mock_scoreboards(monkeypatch)
    fetch = resilience._schedule_first_fetch(
        lambda *_args, **_kwargs: [],
        started=RUN_START,
        horizon_hours=36,
    )
    token = discovery._SLATE_FETCH_WINDOW.set(("2026-11-02", "America/Chicago"))
    try:
        for family in ("NFL", "NBA", "NHL"):
            result = fetch(family)
            assert len(result.rows) == 1
            assert result.primary_path_state == discovery.PATH_SUCCEEDED_WITH_ROWS
            assert result.rows[0]["prediction_authority"] is False
            assert result.rows[0]["can_execute"] is False
    finally:
        discovery._SLATE_FETCH_WINDOW.reset(token)
    assert len(calls) == 6
    assert discovery._SLATE_FETCH_WINDOW.get() is None
