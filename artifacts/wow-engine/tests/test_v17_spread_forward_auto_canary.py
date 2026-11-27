from __future__ import annotations

from datetime import datetime, timezone

import pytest

import v17.spread_forward_auto_canary as canary
from v17.spread_margin_challenger import SpreadChallengerUnavailable


# After the Sept. 28 fixture kickoff so the Oct. 4 fixture is the first future game.
NOW = datetime(2026, 9, 29, 6, 0, tzinfo=timezone.utc)

NFL_EVENT = {
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
}


class FakeResponse:
    def __init__(self, status_code: int, payload):
        self.status_code = status_code
        self._payload = payload

    def json(self):
        return self._payload


def _event(*, event_id="401999999", start="2026-09-29T12:00:00Z"):
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


def _ledger_rows(
    *,
    provider_event_id="rd-nfl-7001",
    start="2026-10-04T17:00:00Z",
    home="Dallas Cowboys",
    away="Atlanta Falcons",
    line=-6.5,
    sport_key="americanfootball_nfl",
    quote="2026-10-04T12:00:00Z",
):
    """Two-sided main-line pregame ledger rows as the collector writes them."""
    common = {
        "provider_event_id": provider_event_id,
        "sport_key": sport_key,
        "event_start_utc": start,
        "market_id": "2",
        "market_name": "spreads",
        "sportsbook": "Pinnacle",
        "price_updated_at": quote,
        "fetched_at": quote,
        "snapshot_kind": "CURRENT",
        "is_live": False,
        "is_main_line": True,
        "is_available": True,
    }
    return [
        {**common, "participant_type": "home", "participant_name": home, "selection": home, "line_value": line},
        {**common, "participant_type": "away", "participant_name": away, "selection": away, "line_value": -line},
    ]


class _FakeQuery:
    def __init__(self, data):
        self._data = data
        self.calls: list[tuple[str, tuple]] = []

    def select(self, columns):
        self.calls.append(("select", (columns,)))
        return self

    def eq(self, column, value):
        self.calls.append(("eq", (column, value)))
        return self

    def gte(self, column, value):
        self.calls.append(("gte", (column, value)))
        return self

    def lte(self, column, value):
        self.calls.append(("lte", (column, value)))
        return self

    def execute(self):
        return type("Response", (), {"data": self._data})()


class _FakeClient:
    def __init__(self, data):
        self.query = _FakeQuery(data)
        self.tables: list[str] = []

    def table(self, name):
        self.tables.append(name)
        return self.query


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


def test_read_pregame_spread_observations_queries_governed_ledger_window():
    client = _FakeClient(_ledger_rows())
    rows = canary._read_pregame_spread_observations(
        client, sport="NFL", event_start_time="2026-10-04T17:00:00+00:00"
    )
    assert client.tables == ["wow_market_price_observations"]
    assert [row["participant_type"] for row in rows] == ["home", "away"]
    filters = dict(call for kind, call in client.query.calls if kind in {"eq", "gte", "lte"})
    assert filters["provider"] == "RUNDOWN"
    assert filters["sport_key"] == "americanfootball_nfl"
    assert filters["event_start_utc"]


def test_nfl_auto_canary_uses_ledger_exact_line_not_synthetic_zero(monkeypatch):
    monkeypatch.setattr(canary, "discover_future_nfl_canonical_event", lambda *_a, **_k: dict(NFL_EVENT))
    monkeypatch.setattr(
        canary,
        "_read_pregame_spread_observations",
        lambda *_a, **_k: _ledger_rows(line=-6.5),
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
        "home_spread": -6.5,
    }
    assert result["status"] == "EXPERIMENT_CREATED"
    assert result["probability_publishable"] is False
    assert result["automatic_certification"] is False
    assert result["automatic_promotion"] is False
    assert result["can_execute"] is False
    assert result["global_terminal_reducer"] == "V17_TERMINAL_REDUCER"
    assert result["identity_provider"] == "NFL_CANONICAL_SCHEDULE_SNAPSHOT"
    assert result["identity_acquisition_location"] == "BACKEND_RUNTIME"
    # The receipt's line must be the quoted market line, never a stand-in.
    assert result["exact_signed_spread"] == -6.5
    assert result["exact_line_source"] == "APPEND_ONLY_MARKET_LEDGER"
    assert result["exact_line_sportsbook"] == "Pinnacle"
    assert result["synthetic_line_used"] is False
    assert result["exact_line_evidence"]["home_spread"] == -6.5
    assert result["exact_line_evidence"]["probability_publishable"] is False
    # A persisted line costs no provider call.
    assert result["exact_line_collection"] is None


def test_nfl_auto_canary_collects_slate_snapshot_then_binds_the_quoted_line(monkeypatch):
    monkeypatch.setattr(canary, "discover_future_nfl_canonical_event", lambda *_a, **_k: dict(NFL_EVENT))
    ledger: list[dict] = []
    monkeypatch.setattr(canary, "_read_pregame_spread_observations", lambda *_a, **_k: list(ledger))
    collected = {}

    def collect(db, *, sport, event):
        collected["sport"] = sport
        collected["slate_date"] = canary._slate_date_for(event["event_start_time"], sport=sport)
        collected["db"] = db
        ledger.extend(_ledger_rows(line=-6.5))
        return {
            "status": "COMPLETE",
            "sport": sport,
            "slate_date": collected["slate_date"],
            "rows_written": 12,
            "datapoints": 24,
            "can_execute": False,
        }

    monkeypatch.setattr(canary, "collect_canary_spread_snapshot", collect)
    scored = {}

    def shadow(_db, **kwargs):
        scored.update(kwargs)
        return {"status": "EXPERIMENT_CREATED", "sport": "NFL"}

    monkeypatch.setattr(canary, "run_nfl_forward_shadow", shadow)
    db = object()
    result = canary.run_nfl_spread_auto_canary(db)
    assert collected == {"sport": "NFL", "slate_date": "2026-10-04", "db": db}
    assert scored["home_spread"] == -6.5
    assert result["exact_line_collection"]["status"] == "COMPLETE"
    assert result["exact_line_observed_rows"] == 2
    assert result["synthetic_line_used"] is False


def test_existing_ledger_line_skips_provider_collection(monkeypatch):
    monkeypatch.setattr(canary, "_read_pregame_spread_observations", lambda *_a, **_k: _ledger_rows(line=-6.5))

    def collect(*_args, **_kwargs):  # pragma: no cover - must not run
        raise AssertionError("collection must not run when a line is already persisted")

    monkeypatch.setattr(canary, "collect_canary_spread_snapshot", collect)
    monkeypatch.setattr(canary, "run_nfl_forward_shadow", lambda _db, **_k: {"status": "EXPERIMENT_CREATED"})
    acquisition = canary.resolve_canary_exact_line(object(), sport="NFL", event=dict(NFL_EVENT))
    assert acquisition["collection"] is None
    assert acquisition["evidence"].home_spread == -6.5


def test_collection_that_writes_nothing_still_fails_closed(monkeypatch):
    monkeypatch.setattr(canary, "discover_future_nfl_canonical_event", lambda *_a, **_k: dict(NFL_EVENT))
    monkeypatch.setattr(canary, "_read_pregame_spread_observations", lambda *_a, **_k: [])
    monkeypatch.setattr(
        canary,
        "collect_canary_spread_snapshot",
        lambda *_a, **_k: {"status": "PARTIAL", "rows_written": 0, "can_execute": False},
    )

    def shadow(*_args, **_kwargs):  # pragma: no cover - must never run
        raise AssertionError("shadow must not score without a bound exact line")

    monkeypatch.setattr(canary, "run_nfl_forward_shadow", shadow)
    with pytest.raises(SpreadChallengerUnavailable) as exc:
        canary.run_nfl_spread_auto_canary(object())
    assert exc.value.code == "NFL_SPREAD_CANARY_EXACT_LINE_UNAVAILABLE"
    assert "collection=PARTIAL" in str(exc.value)
    assert "collection_rows=0" in str(exc.value)


def test_collection_failure_is_typed_and_preserves_upstream_reason(monkeypatch):
    monkeypatch.setattr(canary, "discover_future_nfl_canonical_event", lambda *_a, **_k: dict(NFL_EVENT))
    monkeypatch.setattr(canary, "_read_pregame_spread_observations", lambda *_a, **_k: [])

    def collect(*_args, **_kwargs):
        raise canary.SpreadMarketEvidenceError(
            "SPREAD_MARKET_MULTI_BOOK_SCOPE_UNRESOLVED", "resolved 0 books"
        )

    # Exercise the real wrapper so the SpreadMarketEvidenceError -> typed
    # canary blocker conversion is actually covered.
    monkeypatch.setattr(canary.market_evidence, "collect_spread_snapshot", collect)

    def shadow(*_args, **_kwargs):  # pragma: no cover - must never run
        raise AssertionError("shadow must not score after a failed collection")

    monkeypatch.setattr(canary, "run_nfl_forward_shadow", shadow)
    with pytest.raises(SpreadChallengerUnavailable) as exc:
        canary.run_nfl_spread_auto_canary(object())
    assert exc.value.code == "NFL_SPREAD_CANARY_MARKET_COLLECTION_FAILED"
    assert exc.value.code != "MODEL_UNAVAILABLE"
    assert "SPREAD_MARKET_MULTI_BOOK_SCOPE_UNRESOLVED" in str(exc.value)


def test_slate_date_uses_the_us_eastern_calendar():
    assert canary._slate_date_for("2026-10-04T17:00:00+00:00", sport="NFL") == "2026-10-04"
    # A 02:00Z Monday kickoff is still the Sunday slate for the provider.
    assert canary._slate_date_for("2026-10-05T02:00:00+00:00", sport="NFL") == "2026-10-04"
    assert canary._slate_date_for("2026-09-30T23:30:00+00:00", sport="WNBA") == "2026-09-30"


def test_ambiguous_provider_events_fail_closed(monkeypatch):
    duplicate_rows = _ledger_rows(line=-6.5) + _ledger_rows(provider_event_id="rd-nfl-7002", line=-7.0)
    monkeypatch.setattr(canary, "_read_pregame_spread_observations", lambda *_a, **_k: duplicate_rows)
    with pytest.raises(SpreadChallengerUnavailable) as exc:
        canary.resolve_canary_exact_line(
            object(), sport="NFL", event=dict(NFL_EVENT), collect=False
        )
    assert exc.value.code == "NFL_SPREAD_CANARY_EXACT_LINE_UNAVAILABLE"
    assert "SPREAD_EVENT_IDENTITY_AMBIGUOUS" in str(exc.value)


def test_post_start_quote_is_rejected(monkeypatch):
    monkeypatch.setattr(
        canary,
        "_read_pregame_spread_observations",
        lambda *_a, **_k: _ledger_rows(quote="2026-10-04T18:30:00Z"),
    )
    with pytest.raises(SpreadChallengerUnavailable) as exc:
        canary.resolve_canary_exact_line(
            object(), sport="NFL", event=dict(NFL_EVENT), collect=False
        )
    assert exc.value.code == "NFL_SPREAD_CANARY_EXACT_LINE_UNAVAILABLE"


def test_ledger_read_failure_is_typed_not_model_unavailable(monkeypatch):
    def broken(*_args, **_kwargs):
        raise canary.SpreadChallengerUnavailable(
            "NFL_SPREAD_CANARY_EXACT_LINE_LEDGER_UNAVAILABLE",
            "market ledger read failed: TimeoutError",
        )

    monkeypatch.setattr(canary, "discover_future_nfl_canonical_event", lambda *_a, **_k: dict(NFL_EVENT))
    monkeypatch.setattr(canary, "_read_pregame_spread_observations", broken)
    with pytest.raises(SpreadChallengerUnavailable) as exc:
        canary.run_nfl_spread_auto_canary(object())
    assert exc.value.code == "NFL_SPREAD_CANARY_EXACT_LINE_LEDGER_UNAVAILABLE"
    assert exc.value.code != "MODEL_UNAVAILABLE"


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
    monkeypatch.setattr(
        canary,
        "_read_pregame_spread_observations",
        lambda *_a, **_k: _ledger_rows(
            provider_event_id="rd-wnba-9001",
            start="2026-09-30T00:00:00Z",
            home="Minnesota Lynx",
            away="Phoenix Mercury",
            line=-3.0,
            sport_key="basketball_wnba",
            quote="2026-09-29T18:00:00Z",
        ),
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
    assert seen["home_spread"] == -3.0
    assert result["probability_publishable"] is False
    assert result["can_execute"] is False
    assert result["exact_signed_spread"] == -3.0
    assert result["synthetic_line_used"] is False


def test_wnba_missing_line_fails_closed(monkeypatch):
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
    monkeypatch.setattr(canary, "_read_pregame_spread_observations", lambda *_a, **_k: [])
    monkeypatch.setattr(
        canary,
        "collect_canary_spread_snapshot",
        lambda *_a, **_k: {"status": "COMPLETE", "rows_written": 0, "can_execute": False},
    )
    with pytest.raises(SpreadChallengerUnavailable) as exc:
        canary.run_wnba_spread_auto_canary(object())
    assert exc.value.code == "WNBA_SPREAD_CANARY_EXACT_LINE_UNAVAILABLE"


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
