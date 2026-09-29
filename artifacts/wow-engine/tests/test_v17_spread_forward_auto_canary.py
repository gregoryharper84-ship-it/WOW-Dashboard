from __future__ import annotations

from datetime import datetime, timezone

import pytest

import v17.spread_forward_auto_canary as canary
from v17.spread_margin_challenger import SpreadChallengerUnavailable


NOW = datetime(2026, 9, 28, 16, 0, tzinfo=timezone.utc)


class FakeResponse:
    def __init__(self, status_code: int, payload):
        self.status_code = status_code
        self._payload = payload

    def json(self):
        return self._payload


def _event(*, event_id="401999999", start="2026-09-29T00:00:00Z"):
    return {
        "id": event_id,
        "date": start,
        "status": {"type": {"state": "pre", "completed": False}},
        "competitions": [{
            "competitors": [
                {"homeAway": "home", "team": {"id": "6", "displayName": "Dallas Cowboys"}},
                {"homeAway": "away", "team": {"id": "1", "displayName": "Atlanta Falcons"}},
            ]
        }],
    }


def test_discovers_future_nfl_event_from_governed_canonical_schedule(monkeypatch):
    snapshot = {
        "snapshot_id": "schedule-snapshot-1",
        "content_sha256": "abc123",
    }
    rows = [
        {
            "game_id": "2026_03_ATL_DAL",
            "gameday": "2026-10-04",
            "gametime": "13:00",
            "home_team": "DAL",
            "away_team": "ATL",
            "home_score": "",
            "away_score": "",
        },
        {
            "game_id": "2026_02_GB_CHI",
            "gameday": "2026-09-28",
            "gametime": "20:15",
            "home_team": "CHI",
            "away_team": "GB",
            "home_score": "",
            "away_score": "",
        },
    ]
    monkeypatch.setattr(canary, "_load_latest_schedule_snapshot", lambda _db: (snapshot, rows))
    result = canary.discover_future_nfl_canonical_event(object(), now=NOW)
    assert result["sport"] == "NFL"
    assert result["raw_event_id"] == "2026_03_ATL_DAL"
    assert result["home_team"] == "DAL"
    assert result["away_team"] == "ATL"
    assert result["identity_provider"] == "NFL_CANONICAL_SCHEDULE_SNAPSHOT"
    assert result["identity_acquisition_location"] == "BACKEND_RUNTIME"
    assert result["canonical_source_snapshot_id"] == "schedule-snapshot-1"
    assert result["schedule_content_sha256"] == "abc123"
    assert result["can_execute"] is False


def test_nfl_canonical_schedule_skips_scored_rows_and_returns_none_when_no_future(monkeypatch):
    monkeypatch.setattr(
        canary,
        "_load_latest_schedule_snapshot",
        lambda _db: (
            {"snapshot_id": "s1", "content_sha256": "h1"},
            [{
                "game_id": "2026_03_ATL_DAL",
                "gameday": "2026-10-04",
                "gametime": "13:00",
                "home_team": "DAL",
                "away_team": "ATL",
                "home_score": "24",
                "away_score": "17",
            }],
        ),
    )
    assert canary.discover_future_nfl_canonical_event(object(), now=NOW) is None


def test_nfl_canonical_schedule_failure_is_typed(monkeypatch):
    def broken(_db):
        raise RuntimeError("fixture")

    monkeypatch.setattr(canary, "_load_latest_schedule_snapshot", broken)
    with pytest.raises(SpreadChallengerUnavailable) as exc:
        canary.discover_future_nfl_canonical_event(object(), now=NOW)
    assert exc.value.code == "NFL_SPREAD_CANARY_CANONICAL_SCHEDULE_UNAVAILABLE"


def test_valid_empty_wnba_schedule_returns_none():
    result = canary.discover_future_espn_event(
        "WNBA",
        fetcher=lambda *_args, **_kwargs: FakeResponse(200, {"events": []}),
        now=NOW,
        horizon_days=2,
    )
    assert result is None


def test_wnba_source_failure_is_typed():
    with pytest.raises(SpreadChallengerUnavailable) as exc:
        canary.discover_future_espn_event(
            "WNBA",
            fetcher=lambda *_args, **_kwargs: FakeResponse(403, {}),
            now=NOW,
            horizon_days=2,
        )
    assert exc.value.code == "WNBA_SPREAD_CANARY_IDENTITY_SOURCE_UNAVAILABLE"


def test_nfl_auto_canary_invokes_existing_shadow_without_changing_governance(monkeypatch):
    monkeypatch.setattr(
        canary,
        "discover_future_nfl_canonical_event",
        lambda *_args, **_kwargs: {
            "sport": "NFL",
            "raw_event_id": "2026_03_ATL_DAL",
            "event_start_time": "2026-10-04T17:00:00+00:00",
            "home_team": "DAL",
            "away_team": "ATL",
            "identity_provider": "NFL_CANONICAL_SCHEDULE_SNAPSHOT",
            "identity_acquisition_location": "BACKEND_RUNTIME",
            "canonical_source_snapshot_id": "schedule-snapshot-1",
            "schedule_content_sha256": "abc123",
            "can_execute": False,
        },
    )
    seen = {}

    def shadow(db, **kwargs):
        seen["db"] = db
        seen.update(kwargs)
        return {
            "status": "EXPERIMENT_CREATED",
            "code": "NFL_SPREAD_FORWARD_SHADOW_COMPLETE",
            "sport": "NFL",
            "p_cover": 0.51,
            "p_push": 0.0,
            "p_not_cover": 0.49,
        }

    monkeypatch.setattr(canary, "run_nfl_forward_shadow", shadow)
    db = object()
    result = canary.run_nfl_spread_auto_canary(db)
    assert seen == {
        "db": db,
        "event_id": "2026_03_ATL_DAL",
        "event_start_time": "2026-10-04T17:00:00+00:00",
        "home_team": "DAL",
        "away_team": "ATL",
        "home_spread": 0.0,
    }
    assert result["status"] == "EXPERIMENT_CREATED"
    assert result["probability_publishable"] is False
    assert result["automatic_certification"] is False
    assert result["automatic_promotion"] is False
    assert result["can_execute"] is False
    assert result["global_terminal_reducer"] == "V17_TERMINAL_REDUCER"
    assert result["identity_provider"] == "NFL_CANONICAL_SCHEDULE_SNAPSHOT"
    assert result["identity_acquisition_location"] == "BACKEND_RUNTIME"


def test_nfl_no_future_event_is_explicit_defer(monkeypatch):
    monkeypatch.setattr(canary, "discover_future_nfl_canonical_event", lambda *_args, **_kwargs: None)
    result = canary.run_nfl_spread_auto_canary(object())
    assert result == {
        "status": "DEFERRED_WITH_JUSTIFICATION",
        "code": "NFL_SPREAD_CANARY_NO_ELIGIBLE_FUTURE_EVENT",
        "sport": "NFL",
        "identity_provider": "NFL_CANONICAL_SCHEDULE_SNAPSHOT",
        "identity_acquisition_location": "BACKEND_RUNTIME",
        "automatic_certification": False,
        "automatic_promotion": False,
        "probability_publishable": False,
        "global_terminal_reducer": "V17_TERMINAL_REDUCER",
        "dry_run_only_no_live_trading_no_market_orders": True,
        "can_execute": False,
    }


def test_wnba_auto_canary_prefixes_exact_espn_identity(monkeypatch):
    monkeypatch.setattr(
        canary,
        "discover_future_espn_event",
        lambda *_args, **_kwargs: {
            "sport": "WNBA",
            "raw_event_id": "401888888",
            "event_start_time": "2026-09-30T00:00:00+00:00",
            "home_team": "Minnesota Lynx",
            "away_team": "Phoenix Mercury",
            "home_team_id": "8",
            "away_team_id": "11",
            "identity_provider": "ESPN_SCOREBOARD",
            "identity_acquisition_location": "BACKEND_RUNTIME",
            "can_execute": False,
        },
    )
    seen = {}

    def shadow(db, **kwargs):
        seen["db"] = db
        seen.update(kwargs)
        return {
            "status": "EXPERIMENT_CREATED",
            "code": "WNBA_SPREAD_FORWARD_SHADOW_COMPLETE",
            "sport": "WNBA",
            "p_cover": 0.48,
            "p_push": 0.01,
            "p_not_cover": 0.51,
        }

    monkeypatch.setattr(canary, "run_wnba_forward_shadow", shadow)
    db = object()
    result = canary.run_wnba_spread_auto_canary(db)
    assert seen["db"] is db
    assert seen["event_id"] == "espn-401888888"
    assert seen["home_team_id"] == "espn-8"
    assert seen["away_team_id"] == "espn-11"
    assert seen["home_spread"] == 0.0
    assert result["probability_publishable"] is False
    assert result["can_execute"] is False


def test_wnba_no_future_event_is_explicit_defer(monkeypatch):
    monkeypatch.setattr(canary, "discover_future_espn_event", lambda *_args, **_kwargs: None)
    result = canary.run_wnba_spread_auto_canary(object())
    assert result == {
        "status": "DEFERRED_WITH_JUSTIFICATION",
        "code": "WNBA_SPREAD_CANARY_NO_ELIGIBLE_FUTURE_EVENT",
        "sport": "WNBA",
        "identity_provider": "ESPN_SCOREBOARD",
        "identity_acquisition_location": "BACKEND_RUNTIME",
        "automatic_certification": False,
        "automatic_promotion": False,
        "probability_publishable": False,
        "global_terminal_reducer": "V17_TERMINAL_REDUCER",
        "dry_run_only_no_live_trading_no_market_orders": True,
        "can_execute": False,
    }
