from datetime import date, datetime, timedelta, timezone
from types import SimpleNamespace

import pytest

from v17 import rundown_market_history as history
from v17 import rundown_timed_capture as timed

UTC = timezone.utc


def _ev(start, state="pre"):
    return {"id": start, "date": start, "status": {"type": {"state": state}}}


def _fetcher(events_by_sport, fail=None, calls=None):
    def fetch(url, params):
        sport = "mlb" if "/baseball/" in url else "nfl"
        if calls is not None:
            calls.append((sport, params["dates"]))
        if fail and sport in fail:
            return SimpleNamespace(ok=False, code="ESPN_HTTP_503", data=None)
        return SimpleNamespace(ok=True, data={"events": events_by_sport.get(sport, [])})
    return fetch


class _Store:
    def __init__(self):
        self.rows = {}

    def table(self, name):
        store = self

        class Q:
            key = None

            def select(self, *_a):
                return self

            def eq(self, _f, v):
                self.key = v
                return self

            def limit(self, *_a):
                return self

            def upsert(self, row, **_k):
                store.rows[row["feed_key"]] = row
                self.key = "__written__"
                return self

            def execute(self):
                row = store.rows.get(self.key)
                return SimpleNamespace(data=[row] if row else [])
        return Q()


@pytest.fixture(autouse=True)
def _env(monkeypatch):
    for name in ("WOW_RUNDOWN_TIMED_MORNING_HOUR", "WOW_RUNDOWN_TIMED_CLOSE_LEAD_MINUTES",
                 "WOW_RUNDOWN_TIMED_WINDOW_MINUTES", "WOW_RUNDOWN_MARKET_HISTORY_SCHEDULE"):
        monkeypatch.delenv(name, raising=False)
    monkeypatch.setenv("WOW_RUNDOWN_MARKET_HISTORY_SPORT_KEYS", "baseball_mlb,americanfootball_nfl")
    monkeypatch.setenv("WOW_RUNDOWN_MARKET_HISTORY_TIMEZONE", "America/Chicago")


# 2026-10-11 local (CDT = UTC-5). NFL: 12:00, 12:00, 15:05, 15:25, 19:20 CT.
NFL = [_ev("2026-10-11T17:00Z"), _ev("2026-10-11T17:00Z"), _ev("2026-10-11T20:05Z"),
       _ev("2026-10-11T20:25Z"), _ev("2026-10-12T00:20Z")]


def test_default_schedule_is_timed():
    assert timed.schedule_mode() == "TIMED"


def test_windows_cluster_starts_within_span():
    starts = sorted({timed._parse(e["date"]) for e in NFL})
    assert [timed._iso(a) for a in timed.windows(starts)] == [
        "2026-10-11T17:00:00Z", "2026-10-11T20:05:00Z", "2026-10-12T00:20:00Z",
    ]


def test_pregame_starts_keep_only_future_pregame_games_on_the_local_day():
    events = {"nfl": NFL + [_ev("2026-10-11T13:00Z", state="post"), _ev("2026-10-12T17:00Z")]}
    now = datetime(2026, 10, 11, 14, tzinfo=UTC)
    starts, code = timed.pregame_starts("americanfootball_nfl", date(2026, 10, 11), now=now, fetch=_fetcher(events))
    assert code is None
    assert len(starts) == 4  # dedup 17:00; excludes final game and next local day
    assert timed._iso(starts[-1]) == "2026-10-12T00:20:00Z"  # 19:20 CT is still Oct 11 locally


def test_morning_not_due_before_hour_then_due_once():
    store = _Store()
    early = datetime(2026, 10, 11, 13, 30, tzinfo=UTC)  # 08:30 CT
    due, _, _ = timed.due_captures(store, now=early, fetch=_fetcher({"nfl": NFL}))
    assert due == {}
    nine = datetime(2026, 10, 11, 14, 5, tzinfo=UTC)  # 09:05 CT
    due, _, day = timed.due_captures(store, now=nine, fetch=_fetcher({"nfl": NFL}))
    assert due == {"americanfootball_nfl": ["americanfootball_nfl|MORNING"]}
    timed.write_done(store, day, {"americanfootball_nfl|MORNING"})
    due, _, _ = timed.due_captures(store, now=nine + timedelta(minutes=10), fetch=_fetcher({"nfl": NFL}))
    assert due == {}


def test_close_window_due_only_within_lead_and_before_start():
    store = _Store()
    timed.write_done(store, date(2026, 10, 11), {"americanfootball_nfl|MORNING"})
    far = datetime(2026, 10, 11, 16, 20, tzinfo=UTC)  # 40 min before 12:00 CT
    assert timed.due_captures(store, now=far, fetch=_fetcher({"nfl": NFL}))[0] == {}
    near = datetime(2026, 10, 11, 16, 40, tzinfo=UTC)  # 20 min before
    due, _, _ = timed.due_captures(store, now=near, fetch=_fetcher({"nfl": NFL}))
    assert due == {"americanfootball_nfl": ["americanfootball_nfl|CLOSE|2026-10-11T17:00:00Z"]}


def test_sport_with_no_games_left_never_triggers_a_paid_call():
    store = _Store()
    nine = datetime(2026, 10, 11, 14, 5, tzinfo=UTC)
    due, failures, _ = timed.due_captures(store, now=nine, fetch=_fetcher({"nfl": NFL, "mlb": []}))
    assert "baseball_mlb" not in due and failures == {}


def test_schedule_failure_is_typed_and_skips_sport():
    due, failures, _ = timed.due_captures(
        _Store(), now=datetime(2026, 10, 11, 14, 5, tzinfo=UTC), fetch=_fetcher({"nfl": NFL}, fail={"mlb"})
    )
    assert failures == {"baseball_mlb": "ESPN_HTTP_503"}
    assert "americanfootball_nfl" in due


def test_cycle_collects_only_due_sports_and_marks_only_completed(monkeypatch):
    store = _Store()
    seen = {}

    def collect(client, *, now, opener=None, sports=None):
        seen["sports"] = list(sports)
        return {"status": "PARTIAL", "provider_calls": 4, "results": [
            {"sport_key": "americanfootball_nfl", "status": "COMPLETE"},
            {"sport_key": "baseball_mlb", "status": "FAILED"},
        ]}

    monkeypatch.setattr(history, "collect_history_once", collect)
    mlb = [_ev("2026-10-11T23:08Z")]
    now = datetime(2026, 10, 11, 14, 5, tzinfo=UTC)
    out = timed.run_timed_cycle(store, now=now, fetch=_fetcher({"nfl": NFL, "mlb": mlb}))
    assert sorted(seen["sports"]) == ["americanfootball_nfl", "baseball_mlb"]
    assert out["windows_completed"] == ["americanfootball_nfl|MORNING"]
    assert timed.read_done(store, date(2026, 10, 11)) == {"americanfootball_nfl|MORNING"}  # MLB retried next tick
    assert out["can_execute"] is False


def test_nothing_due_makes_no_provider_call_but_materializes_closes(monkeypatch):
    monkeypatch.setattr(history, "collect_history_once", lambda *a, **k: pytest.fail("paid call"))
    refs = []
    monkeypatch.setattr(history, "materialize_reference_backlog", lambda c, now: refs.append(now) or 3)
    out = timed.run_timed_cycle(_Store(), now=datetime(2026, 10, 11, 13, tzinfo=UTC), fetch=_fetcher({"nfl": NFL}))
    assert out["status"] == "NOTHING_DUE" and out["provider_calls"] == 0 and out["reference_rows_written"] == 3


def test_history_cycle_sports_filter_limits_paid_calls(monkeypatch):
    monkeypatch.setattr(history, "_read_budget", lambda *_a, **_k: {"calls": 0, "datapoints": 0})
    monkeypatch.setattr(history, "resolve_collection_scope", lambda *_a, **_k: {
        "status": "READY", "market_ids": ("1",), "affiliate_ids": ("3",), "book_names": ["Pinnacle"]})
    called = []
    monkeypatch.setattr(history.ingestor, "collect_snapshot",
                        lambda *_a, sport_key, **_k: called.append(sport_key) or {"status": "COMPLETE", "provider_calls": 2})
    monkeypatch.setattr(history, "materialize_reference_backlog", lambda *a, **k: 0)
    monkeypatch.setattr(history, "_write_budget", lambda *a, **k: None)
    history.collect_history_once(object(), now=datetime(2026, 10, 11, 14, tzinfo=UTC), sports=["americanfootball_nfl"])
    assert called == ["americanfootball_nfl"]
