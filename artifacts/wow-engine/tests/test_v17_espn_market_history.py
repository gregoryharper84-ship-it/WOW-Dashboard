from datetime import datetime, timezone
from types import SimpleNamespace

import pytest

from v17 import closing_line_grading as grading
from v17 import espn_market_history as espn_history
from v17 import rundown_market_history as history

FETCHED = datetime(2026, 10, 11, 15, 0, tzinfo=timezone.utc)


def _event(event_id="401872981", *, state="pre", start="2026-10-11T17:00Z", odds=True,
           home="Jacksonville Jaguars", away="Philadelphia Eagles",
           home_close="+105", away_close="-125", home_current=None, away_current=None):
    event = {
        "id": event_id,
        "date": start,
        "status": {"type": {"state": state, "name": "STATUS_SCHEDULED"}},
        "competitions": [{
            "competitors": [
                {"homeAway": "home", "team": {"displayName": home}},
                {"homeAway": "away", "team": {"displayName": away}},
            ],
            "odds": [],
        }],
    }
    if odds:
        event["competitions"][0]["odds"] = [{
            "provider": {"id": "100", "name": "Draft Kings", "priority": 1},
            "moneyline": {
                "home": {"open": {"odds": "+110"}, "close": {"odds": home_close}},
                "away": {"open": {"odds": "-130"}, "close": {"odds": away_close}},
            },
            "homeTeamOdds": {"moneyLine": home_current},
            "awayTeamOdds": {"moneyLine": away_current},
        }]
    return event


def _rows(*events):
    return espn_history.observations_from_scoreboard(
        {"events": list(events)}, sport_key="americanfootball_nfl", fetched_at=FETCHED
    )


def test_pregame_event_yields_two_sided_moneyline_rows_without_authority():
    rows = _rows(_event())
    assert [(r["participant_name"], r["american_odds"]) for r in rows] == [
        ("Jacksonville Jaguars", 105.0), ("Philadelphia Eagles", -125.0),
    ]
    assert {r["affiliate_id"] for r in rows} == {"espn:100"}
    assert {r["sportsbook"] for r in rows} == {"Draft Kings"}
    assert all(r["provider"] == "ESPN" and r["market_id"] == "1" for r in rows)
    assert all(r["snapshot_kind"] == "CURRENT" and r["is_live"] is False for r in rows)
    assert all(r["prediction_authority"] is False and r["can_execute"] is False for r in rows)
    assert rows[0]["decimal_odds"] == pytest.approx(2.05)


@pytest.mark.parametrize(
    "event",
    [
        _event(state="in"),
        _event(state="post"),
        _event(start="2026-10-11T14:59Z"),  # already started by fetch time
        _event(odds=False),
        _event(home_close=None, home_current=None),  # one-sided
        _event(home=""),
    ],
)
def test_live_final_started_missing_or_one_sided_events_are_never_captured(event):
    assert _rows(event) == []


def test_current_moneyline_is_used_when_close_block_absent():
    event = _event(home_close=None, away_close=None, home_current=150, away_current=-170)
    assert [r["american_odds"] for r in _rows(event)] == [150.0, -170.0]


def test_even_price_and_invalid_tokens():
    assert espn_history._american("EVEN") == 100.0
    assert espn_history._american("+150") == 150.0
    assert espn_history._american("-50") is None
    assert espn_history._american("abc") is None
    assert espn_history._american(True) is None


def test_unchanged_price_keeps_identity_so_repeat_polls_do_not_add_rows():
    first = _rows(_event())
    later = espn_history.observations_from_scoreboard(
        {"events": [_event()]}, sport_key="americanfootball_nfl",
        fetched_at=datetime(2026, 10, 11, 16, 30, tzinfo=timezone.utc),
    )
    assert [r["observation_key"] for r in first] == [r["observation_key"] for r in later]
    moved = _rows(_event(away_close="-140"))
    assert first[1]["observation_key"] != moved[1]["observation_key"]


def test_capture_once_is_uncached_and_reports_typed_partial_failures(monkeypatch):
    monkeypatch.setenv("WOW_ESPN_MARKET_HISTORY_SPORT_KEYS", "americanfootball_nfl,baseball_mlb")
    calls = []

    def fetch(url, params):
        calls.append((url.rsplit("/", 2)[-2], params["dates"]))
        if "/baseball/" in url:
            return SimpleNamespace(ok=False, code="ESPN_HTTP_503", data=None)
        return SimpleNamespace(ok=True, data={"events": [_event()]})

    written = []

    class Client:
        def table(self, name):
            return SimpleNamespace(upsert=lambda rows, **k: written.extend(rows) or SimpleNamespace(execute=lambda: None))

    result = espn_history.capture_once(Client(), now=FETCHED, fetch=fetch)
    assert calls == [("nfl", "20261011"), ("nfl", "20261012"), ("mlb", "20261011"), ("mlb", "20261012")]
    assert result["status"] == "PARTIAL"
    assert result["by_sport"]["baseball_mlb"]["failures"] == ["ESPN_HTTP_503", "ESPN_HTTP_503"]
    assert result["can_execute"] is False


def test_unknown_sport_keys_are_ignored(monkeypatch):
    monkeypatch.setenv("WOW_ESPN_MARKET_HISTORY_SPORT_KEYS", "americanfootball_nfl,cricket_xyz")
    assert espn_history.configured_sports() == ("americanfootball_nfl",)


def test_end_to_end_espn_capture_becomes_close_reference_and_grades():
    early = _rows(_event(away_close="-130", home_close="+110"))
    late = espn_history.observations_from_scoreboard(
        {"events": [_event()]}, sport_key="americanfootball_nfl",
        fetched_at=datetime(2026, 10, 11, 16, 45, tzinfo=timezone.utc),
    )
    after_start = datetime(2026, 10, 11, 18, 0, tzinfo=timezone.utc)
    references = history.derive_captured_reference_rows(early + late, now=after_start)
    closes = [r for r in references if r["snapshot_kind"] == "CLOSE"]
    assert sorted(r["american_odds"] for r in closes) == [-125.0, 105.0]  # last pregame capture

    grade = grading.grade_row(
        source=grading.SOURCE_NFL, prediction_id="g1", sport="NFL", official_event_id="2026_06_PHI_JAX",
        selected="Philadelphia Eagles", opponent=None, event_start="2026-10-11T17:00:00Z",
        model_probability=0.62, outcome=1, close_rows=closes,
    )
    assert grade["link_status"] == grading.LINKED
    assert grade["close_probability"] == pytest.approx(grading.no_vig_pair(-125, 105))
    assert grade["close_books"] == ["espn:100"]


def test_cycle_isolates_capture_failure_and_still_grades(monkeypatch):
    def broken():
        raise RuntimeError("db down")

    graded = []
    monkeypatch.setattr(history, "run_closing_line_grading_cycle", lambda fn, log: graded.append(fn) or {"ok": 1})
    out = espn_history.run_cycle(broken)
    assert graded == [broken]
    assert out["can_execute"] is False


def test_installer_respects_kill_switch(monkeypatch):
    monkeypatch.setenv("WOW_ESPN_MARKET_HISTORY_ENABLED", "false")
    app = SimpleNamespace(state=SimpleNamespace())
    assert espn_history.install_espn_market_history(app, db_client_fn=lambda: None) is False


def test_grader_defaults_to_free_espn_close_source(monkeypatch):
    monkeypatch.delenv("WOW_CLOSING_LINE_PROVIDER", raising=False)
    assert grading.close_provider() == "ESPN"
