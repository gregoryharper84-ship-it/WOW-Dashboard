"""
test_odds_api_failover.py — Regression tests for the get_h2h_odds()
TheRundown failover and _normalize_rundown_to_h2h_events() normalizer.

Run:
  cd artifacts/flask-scoring-api
  python -m pytest services/tests/test_odds_api_failover.py -v
"""
import os
import sys
from datetime import datetime, timedelta, timezone
from unittest import mock

import pytest

sys.path.insert(0, os.path.join(os.path.dirname(__file__), "..", ".."))

from services import odds_api
from services.odds_api import (
    _normalize_rundown_to_h2h_events,
    _RUNDOWN_FAILOVER_STATUSES,
    _SPORT_KEY_TO_NAME,
)


# ---------------------------------------------------------------------------
# Helpers
# ---------------------------------------------------------------------------

def _rundown_event(home="Seattle Storm", away="Las Vegas Aces",
                   home_price=-150, away_price=130,
                   date_updated=None, affiliates=None,
                   event_date="2026-07-25T20:00:00Z"):
    """Build a minimal TheRundown event dict."""
    if date_updated is None:
        date_updated = (
            datetime.now(timezone.utc) - timedelta(minutes=5)
        ).isoformat().replace("+00:00", "Z")
    if affiliates is None:
        affiliates = ["1"]
    lines = {}
    for aff in affiliates:
        lines[aff] = {
            "moneyline": {
                "moneyline_home": home_price,
                "moneyline_away": away_price,
                "date_updated":   date_updated,
            }
        }
    return {
        "event_date":       event_date,
        "teams_normalized": [{"name": home}, {"name": away}],
        "lines":            lines,
    }


def _odds_api_event(home="Seattle Storm", away="Las Vegas Aces"):
    """Build a minimal Odds API h2h event dict (primary shape)."""
    ts = (datetime.now(timezone.utc) - timedelta(minutes=5)).isoformat()
    return {
        "home_team": home,
        "away_team": away,
        "commence_time": "2026-07-25T20:00:00Z",
        "bookmakers": [{
            "key":         "draftkings",
            "last_update": ts,
            "markets": [{
                "key":         "h2h",
                "last_update": ts,
                "outcomes": [
                    {"name": home, "price": -150},
                    {"name": away, "price": 130},
                ],
            }],
        }],
    }


# ---------------------------------------------------------------------------
# _normalize_rundown_to_h2h_events — normalizer unit tests
# ---------------------------------------------------------------------------

class TestNormalizeRundownToH2hEvents:
    def test_happy_path_single_affiliate(self):
        ev = _rundown_event(affiliates=["7"])
        result = _normalize_rundown_to_h2h_events([ev])
        assert len(result) == 1
        out = result[0]
        assert out["home_team"] == "Seattle Storm"
        assert out["away_team"] == "Las Vegas Aces"
        assert len(out["bookmakers"]) == 1
        bm = out["bookmakers"][0]
        assert bm["key"] == "rundown:7"
        assert len(bm["markets"]) == 1
        market = bm["markets"][0]
        assert market["key"] == "h2h"
        assert {"name": "Seattle Storm", "price": -150} in market["outcomes"]
        assert {"name": "Las Vegas Aces", "price": 130} in market["outcomes"]

    def test_multiple_affiliates_all_included(self):
        ev = _rundown_event(affiliates=["1", "2", "5"])
        result = _normalize_rundown_to_h2h_events([ev])
        assert len(result) == 1
        assert len(result[0]["bookmakers"]) == 3
        keys = {bm["key"] for bm in result[0]["bookmakers"]}
        assert keys == {"rundown:1", "rundown:2", "rundown:5"}

    def test_affiliate_missing_home_price_skipped(self):
        ev = _rundown_event(affiliates=["1"])
        ev["lines"]["1"]["moneyline"]["moneyline_home"] = None
        result = _normalize_rundown_to_h2h_events([ev])
        # Event has no valid affiliates → dropped entirely
        assert result == []

    def test_affiliate_missing_away_price_skipped(self):
        ev = _rundown_event(affiliates=["1"])
        ev["lines"]["1"]["moneyline"]["moneyline_away"] = None
        result = _normalize_rundown_to_h2h_events([ev])
        assert result == []

    def test_mixed_affiliates_partial_valid(self):
        ev = _rundown_event(affiliates=["1", "2"])
        # Make affiliate 2 incomplete
        ev["lines"]["2"]["moneyline"]["moneyline_home"] = None
        result = _normalize_rundown_to_h2h_events([ev])
        # Event survives with one valid affiliate
        assert len(result) == 1
        assert len(result[0]["bookmakers"]) == 1
        assert result[0]["bookmakers"][0]["key"] == "rundown:1"

    def test_event_with_fewer_than_two_teams_skipped(self):
        ev = _rundown_event()
        ev["teams_normalized"] = [{"name": "Seattle Storm"}]
        result = _normalize_rundown_to_h2h_events([ev])
        assert result == []

    def test_event_with_empty_teams_skipped(self):
        ev = _rundown_event()
        ev["teams_normalized"] = []
        result = _normalize_rundown_to_h2h_events([ev])
        assert result == []

    def test_blank_team_name_skipped(self):
        ev = _rundown_event()
        ev["teams_normalized"][0]["name"] = ""
        result = _normalize_rundown_to_h2h_events([ev])
        assert result == []

    def test_empty_input(self):
        assert _normalize_rundown_to_h2h_events([]) == []
        assert _normalize_rundown_to_h2h_events(None) == []

    def test_commence_time_carried_through(self):
        ev = _rundown_event(event_date="2026-08-01T19:05:00Z")
        result = _normalize_rundown_to_h2h_events([ev])
        assert result[0]["commence_time"] == "2026-08-01T19:05:00Z"

    def test_last_update_matches_date_updated(self):
        ts = "2026-07-25T18:00:00Z"
        ev = _rundown_event(date_updated=ts)
        result = _normalize_rundown_to_h2h_events([ev])
        bm = result[0]["bookmakers"][0]
        assert bm["last_update"] == ts
        assert bm["markets"][0]["last_update"] == ts

    def test_multiple_events_normalized(self):
        ev1 = _rundown_event(home="Team A", away="Team B")
        ev2 = _rundown_event(home="Team C", away="Team D")
        result = _normalize_rundown_to_h2h_events([ev1, ev2])
        assert len(result) == 2
        names = {(r["home_team"], r["away_team"]) for r in result}
        assert ("Team A", "Team B") in names
        assert ("Team C", "Team D") in names


# ---------------------------------------------------------------------------
# _SPORT_KEY_TO_NAME — reverse map correctness
# ---------------------------------------------------------------------------

class TestSportKeyToName:
    def test_round_trip_all_sport_keys(self):
        for name, key in odds_api.SPORT_KEYS.items():
            assert _SPORT_KEY_TO_NAME[key] == name

    def test_wnba_maps_correctly(self):
        assert _SPORT_KEY_TO_NAME["basketball_wnba"] == "WNBA"

    def test_mlb_maps_correctly(self):
        assert _SPORT_KEY_TO_NAME["baseball_mlb"] == "MLB"


# ---------------------------------------------------------------------------
# get_h2h_odds() — failover integration tests
# ---------------------------------------------------------------------------

class TestGetH2hOddsFailover:

    def _mock_get(self, status, data=None):
        """Patch odds_api._get to return (data, status)."""
        return mock.patch.object(odds_api, "_get", return_value=(data, status))

    def _mock_rundown(self, events, status="AVAILABLE"):
        """Patch services.rundown.get_events_for_sport."""
        import services.rundown as _rd
        return mock.patch.object(_rd, "get_events_for_sport",
                                 return_value=(events, status))

    def test_primary_success_returns_directly(self):
        """Primary Odds API success: data returned, TheRundown never called."""
        primary_data = [_odds_api_event()]
        with self._mock_get("AVAILABLE (remaining=500)", primary_data), \
             mock.patch("services.odds_api._normalize_rundown_to_h2h_events") as mock_norm:
            result, status = odds_api.get_h2h_odds("basketball_wnba")
        assert result == primary_data
        assert "AVAILABLE" in status
        mock_norm.assert_not_called()

    def test_failover_triggered_on_quota_exhausted(self):
        """429 → TheRundown failover is attempted."""
        rd_event = _rundown_event()
        with self._mock_get("FAILED: quota exhausted"), \
             self._mock_rundown([rd_event]):
            result, status = odds_api.get_h2h_odds("basketball_wnba")
        assert len(result) == 1
        assert result[0]["home_team"] == "Seattle Storm"
        assert status.startswith("FALLBACK_RUNDOWN:")

    def test_failover_triggered_on_invalid_key(self):
        """401 → TheRundown failover is attempted."""
        rd_event = _rundown_event(home="Dodgers", away="Yankees")
        with self._mock_get("FAILED: invalid ODDS_API_KEY"), \
             self._mock_rundown([rd_event]):
            result, status = odds_api.get_h2h_odds("baseball_mlb")
        assert len(result) == 1
        assert status.startswith("FALLBACK_RUNDOWN:")

    def test_failover_NOT_triggered_on_timeout(self):
        """Transient errors (timeout) do NOT attempt TheRundown fallback."""
        import services.rundown as _rd
        with self._mock_get("FAILED: timeout"), \
             mock.patch.object(_rd, "get_events_for_sport") as mock_rd:
            result, status = odds_api.get_h2h_odds("basketball_wnba")
        mock_rd.assert_not_called()
        assert result == []
        assert status == "FAILED: timeout"

    def test_failover_NOT_triggered_on_http_500(self):
        """5xx errors do NOT attempt TheRundown fallback."""
        import services.rundown as _rd
        with self._mock_get("FAILED: HTTP 500"), \
             mock.patch.object(_rd, "get_events_for_sport") as mock_rd:
            result, status = odds_api.get_h2h_odds("baseball_mlb")
        mock_rd.assert_not_called()
        assert result == []

    def test_failover_NOT_triggered_on_not_called(self):
        """NOT_CALLED (no key) does NOT attempt TheRundown fallback."""
        import services.rundown as _rd
        with self._mock_get("NOT_CALLED: ODDS_API_KEY not set"), \
             mock.patch.object(_rd, "get_events_for_sport") as mock_rd:
            result, status = odds_api.get_h2h_odds("basketball_wnba")
        mock_rd.assert_not_called()
        assert result == []

    def test_failover_returns_empty_when_rundown_has_no_events(self):
        """Failover attempted but TheRundown yields no usable events."""
        with self._mock_get("FAILED: quota exhausted"), \
             self._mock_rundown([], "AVAILABLE"):
            result, status = odds_api.get_h2h_odds("basketball_wnba")
        assert result == []
        assert "FALLBACK_RUNDOWN" in status
        assert "0 events" in status
        assert "primary=" in status

    def test_failover_returns_empty_when_rundown_itself_fails(self):
        """Failover attempted but TheRundown call fails."""
        with self._mock_get("FAILED: quota exhausted"), \
             self._mock_rundown([], "FAILED: rate limit exceeded"):
            result, status = odds_api.get_h2h_odds("basketball_wnba")
        assert result == []
        assert "FALLBACK_RUNDOWN" in status

    def test_failover_not_attempted_for_unknown_sport_key(self):
        """Unknown sport_key has no reverse-map entry — failover skipped."""
        import services.rundown as _rd
        with self._mock_get("FAILED: quota exhausted"), \
             mock.patch.object(_rd, "get_events_for_sport") as mock_rd:
            result, status = odds_api.get_h2h_odds("unknown_sport_xyz")
        mock_rd.assert_not_called()
        assert result == []

    def test_status_contains_event_count_on_success(self):
        """Successful failover status string reports the number of events."""
        rd_events = [_rundown_event(home="A", away="B"),
                     _rundown_event(home="C", away="D")]
        with self._mock_get("FAILED: quota exhausted"), \
             self._mock_rundown(rd_events):
            result, status = odds_api.get_h2h_odds("basketball_wnba")
        assert len(result) == 2
        assert "2 events" in status

    def test_failover_shape_parseable_by_books_from_odds_api_event(self):
        """
        End-to-end: normalized RunDown event is correctly parsed by
        consensus_odds._books_from_odds_api_event — the primary consumer.
        """
        from kalshi_engine.llp_bridge.consensus_odds import _books_from_odds_api_event

        rd_events = [_rundown_event(
            home="Seattle Storm", away="Las Vegas Aces",
            home_price=-160, away_price=140, affiliates=["1", "3"]
        )]
        normalized = _normalize_rundown_to_h2h_events(rd_events)
        assert len(normalized) == 1

        books = _books_from_odds_api_event(
            normalized[0], "Seattle Storm", "Las Vegas Aces", "Seattle Storm"
        )
        assert len(books) == 2  # one per affiliate
        for bk in books:
            assert bk["source"] == "the_odds_api"
            assert 0 < bk["target_fair_probability"] < 1
            assert bk["bookmaker"].startswith("rundown:")


# ---------------------------------------------------------------------------
# _RUNDOWN_FAILOVER_STATUSES — completeness check
# ---------------------------------------------------------------------------

class TestRundownFailoverStatuses:
    def test_quota_exhausted_in_set(self):
        assert "FAILED: quota exhausted" in _RUNDOWN_FAILOVER_STATUSES

    def test_invalid_key_in_set(self):
        assert "FAILED: invalid ODDS_API_KEY" in _RUNDOWN_FAILOVER_STATUSES

    def test_timeout_not_in_set(self):
        assert "FAILED: timeout" not in _RUNDOWN_FAILOVER_STATUSES

    def test_http500_not_in_set(self):
        assert "FAILED: HTTP 500" not in _RUNDOWN_FAILOVER_STATUSES

    def test_not_called_not_in_set(self):
        assert "NOT_CALLED: ODDS_API_KEY not set" not in _RUNDOWN_FAILOVER_STATUSES
