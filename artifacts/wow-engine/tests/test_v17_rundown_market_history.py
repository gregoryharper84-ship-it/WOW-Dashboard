from datetime import datetime, timezone
from types import SimpleNamespace

import pytest

from v17 import rundown_market_history as history
from v17 import rundown_market_startup_bootstrap as bootstrap


def _row(*, key, odds, updated, event_start="2026-09-24T18:00:00Z", snapshot_kind="CURRENT"):
    return {
        "observation_key": key,
        "provider": "RUNDOWN",
        "provider_event_id": "evt-1",
        "sport_key": "baseball_mlb",
        "event_start_utc": event_start,
        "market_id": "1",
        "market_name": "moneyline",
        "participant_id": "home",
        "participant_name": "Home",
        "participant_type": "home",
        "selection": "Home",
        "line_id": "main-home",
        "line_value": None,
        "affiliate_id": "3",
        "sportsbook": "Pinnacle",
        "american_odds": odds,
        "decimal_odds": 1.8,
        "price_updated_at": updated,
        "fetched_at": updated,
        "snapshot_kind": snapshot_kind,
        "is_live": False,
        "is_main_line": True,
        "is_available": True,
        "closed_at": None,
        "raw_payload": {"price": odds},
        "prediction_authority": False,
        "can_execute": False,
    }


def test_captured_open_and_close_are_distinct_non_official_references():
    rows = [
        _row(key="first", odds=-130, updated="2026-09-24T12:00:00Z"),
        _row(key="middle", odds=-140, updated="2026-09-24T15:00:00Z"),
        _row(key="last", odds=-150, updated="2026-09-24T17:55:00Z"),
    ]
    refs = history.derive_captured_reference_rows(
        rows,
        now=datetime(2026, 9, 24, 19, 0, tzinfo=timezone.utc),
    )
    assert [row["snapshot_kind"] for row in refs] == ["OPEN", "CLOSE"]
    opening, closing = refs
    assert opening["american_odds"] == -130
    assert closing["american_odds"] == -150
    assert opening["observation_key"] != closing["observation_key"]
    assert opening["raw_payload"]["reference_semantics"] == history.OPEN_SEMANTICS
    assert closing["raw_payload"]["reference_semantics"] == history.CLOSE_SEMANTICS
    assert opening["raw_payload"]["provider_official_open_close"] is False
    assert closing["raw_payload"]["provider_official_open_close"] is False
    assert all(row["prediction_authority"] is False for row in refs)
    assert all(row["can_execute"] is False for row in refs)


def test_close_is_not_materialized_before_event_start():
    refs = history.derive_captured_reference_rows(
        [_row(key="first", odds=-130, updated="2026-09-24T12:00:00Z")],
        now=datetime(2026, 9, 24, 17, 0, tzinfo=timezone.utc),
    )
    assert [row["snapshot_kind"] for row in refs] == ["OPEN"]


def test_existing_reference_rows_make_derivation_repeat_safe():
    current = _row(key="first", odds=-130, updated="2026-09-24T12:00:00Z")
    existing_open = _row(
        key="open-existing",
        odds=-130,
        updated="2026-09-24T12:00:00Z",
        snapshot_kind="OPEN",
    )
    refs = history.derive_captured_reference_rows(
        [current, existing_open],
        now=datetime(2026, 9, 24, 17, 0, tzinfo=timezone.utc),
    )
    assert refs == []


def test_daily_call_budget_blocks_without_provider_request(monkeypatch):
    monkeypatch.setattr(history, "_read_budget", lambda *_a, **_k: {"calls": 12, "datapoints": 100})
    monkeypatch.setattr(history, "max_calls_per_day", lambda: 12)
    called = []
    monkeypatch.setattr(history, "resolve_collection_scope", lambda *_a, **_k: called.append(True))
    result = history.collect_history_once(object(), now=datetime(2026, 9, 24, 15, 0, tzinfo=timezone.utc))
    assert result["status"] == "BUDGET_EXHAUSTED"
    assert result["provider_calls"] == 0
    assert called == []
    assert result["can_execute"] is False


def test_429_failure_creates_24_hour_history_suspension(monkeypatch):
    monkeypatch.setattr(history, "_read_budget", lambda *_a, **_k: {"calls": 0, "datapoints": 0})
    monkeypatch.setattr(
        history,
        "resolve_collection_scope",
        lambda *_a, **_k: {
            "status": "READY",
            "market_ids": ("1",),
            "affiliate_ids": ("3", "19", "23"),
            "book_names": ["Pinnacle", "Draftkings", "Fanduel"],
        },
    )
    monkeypatch.setattr(history, "configured_sports", lambda: ("baseball_mlb",))
    monkeypatch.setattr(
        history.ingestor,
        "collect_snapshot",
        lambda *_a, **_k: {"status": "FAILED", "reason_code": "RUNDOWN_HTTP_429", "datapoints": 0},
    )
    written = []
    monkeypatch.setattr(history, "_write_budget", lambda *_a, **kwargs: written.append(kwargs))
    now = datetime(2026, 9, 24, 15, 0, tzinfo=timezone.utc)
    result = history.collect_history_once(object(), now=now)
    assert result["status"] == "PARTIAL"
    assert result["provider_calls"] == 1
    assert result["suspended_until"] == "2026-09-25T15:00:00Z"
    assert written[0]["reason_code"] == "RUNDOWN_HTTP_429"
    assert written[0]["suspended_until"] == "2026-09-25T15:00:00Z"
    assert result["can_execute"] is False


def test_history_default_scope_is_three_books_and_slow_cadence(monkeypatch):
    for name in (
        "WOW_RUNDOWN_MARKET_HISTORY_BOOKS",
        "WOW_RUNDOWN_MARKET_HISTORY_INTERVAL_SECONDS",
        "WOW_RUNDOWN_MARKET_HISTORY_MAX_CALLS_PER_DAY",
        "WOW_RUNDOWN_MARKET_HISTORY_MAX_DATAPOINTS_PER_DAY",
    ):
        monkeypatch.delenv(name, raising=False)
    assert history.configured_books() == ("Pinnacle", "Draftkings", "Fanduel")
    assert history.interval_seconds() == 7200
    assert history.max_calls_per_day() == 12
    assert history.max_datapoints_per_day() == 2500


def test_startup_installs_history_even_when_one_shot_bootstrap_is_off(monkeypatch):
    monkeypatch.setenv("WOW_RUNDOWN_MARKET_STARTUP_MODE", "OFF")
    monkeypatch.setenv("WOW_RUNDOWN_MARKET_HISTORY_ENABLED", "true")

    handlers = {}

    class FakeApp:
        state = SimpleNamespace()

        def on_event(self, event):
            def register(fn):
                handlers[event] = fn
                return fn
            return register

    app = FakeApp()
    assert bootstrap.install_rundown_market_startup_bootstrap(app, db_client_fn=lambda: object()) is True
    assert "startup" in handlers
    assert app.state.v17_rundown_market_bootstrap_installed is True


def test_market_history_is_never_probability_authority():
    assert history.CAN_EXECUTE is False
    assert "NOT_PROVIDER_OFFICIAL_OPEN" in history.OPEN_SEMANTICS
    assert "NOT_PROVIDER_OFFICIAL_CLOSE" in history.CLOSE_SEMANTICS


def test_history_budget_counts_every_regime_variant_provider_call(monkeypatch):
    monkeypatch.setattr(history, "_read_budget", lambda *_a, **_k: {"calls": 0, "datapoints": 0})
    monkeypatch.setattr(
        history,
        "resolve_collection_scope",
        lambda *_a, **_k: {"status": "READY", "market_ids": ("1",), "affiliate_ids": ("19",), "book_names": ["Pinnacle"]},
    )
    monkeypatch.setattr(history, "configured_sports", lambda: ("baseball_mlb",))
    monkeypatch.setattr(
        history.ingestor,
        "collect_snapshot",
        lambda *_a, **_k: {"status": "COMPLETE", "provider_calls": 3, "datapoints": 12, "rows_written": 0},
    )
    monkeypatch.setattr(history, "materialize_captured_references", lambda *_a, **_k: 0)
    written = []
    monkeypatch.setattr(history, "_write_budget", lambda *_a, **kwargs: written.append(kwargs))
    result = history.collect_history_once(object(), now=datetime(2026, 10, 9, 15, 0, tzinfo=timezone.utc))
    assert result["provider_calls"] == 3
    assert result["can_execute"] is False


def test_paid_provider_history_default_footprint_stays_mlb_only(monkeypatch):
    monkeypatch.delenv("WOW_RUNDOWN_MARKET_HISTORY_SPORT_KEYS", raising=False)
    assert history.configured_sports() == ("baseball_mlb",)


def test_close_references_materialize_even_when_call_budget_is_exhausted(monkeypatch):
    monkeypatch.setattr(history, "_read_budget", lambda *_a, **_k: {"calls": 48, "datapoints": 0})
    monkeypatch.setattr(history, "max_calls_per_day", lambda: 48)
    monkeypatch.setattr(history, "configured_sports", lambda: ("baseball_mlb", "americanfootball_nfl"))
    monkeypatch.setattr(
        history.ingestor, "collect_snapshot",
        lambda *_a, **_k: (_ for _ in ()).throw(AssertionError("provider called")),
    )
    seen = []
    monkeypatch.setattr(
        history, "materialize_captured_references",
        lambda _c, *, sport_key, slate_date, now: seen.append((sport_key, slate_date)) or 2,
    )
    result = history.collect_history_once(object(), now=datetime(2026, 10, 10, 4, 30, tzinfo=timezone.utc))
    assert result["status"] == "BUDGET_EXHAUSTED"
    assert result["reference_rows_written"] == 8
    # 23:30 CDT on 10-09 local: yesterday (10-08) and today (10-09) for each sport.
    assert seen == [
        ("baseball_mlb", "2026-10-08"), ("baseball_mlb", "2026-10-09"),
        ("americanfootball_nfl", "2026-10-08"), ("americanfootball_nfl", "2026-10-09"),
    ]


def test_reference_backlog_failure_for_one_sport_does_not_block_others(monkeypatch):
    monkeypatch.setattr(history, "configured_sports", lambda: ("baseball_mlb", "americanfootball_nfl"))

    def flaky(_c, *, sport_key, slate_date, now):
        if sport_key == "baseball_mlb":
            raise RuntimeError("db down")
        return 1

    monkeypatch.setattr(history, "materialize_captured_references", flaky)
    assert history.materialize_reference_backlog(object(), now=datetime(2026, 10, 9, 15, tzinfo=timezone.utc)) == 2


def test_no_open_or_close_from_quote_first_seen_after_event_start():
    """Even an old provider-updated timestamp is not a historical observation."""
    late = _row(key="late", odds=-170, updated="2026-09-24T17:00:00Z")
    late["fetched_at"] = "2026-09-24T18:05:00Z"
    refs = history.derive_captured_reference_rows(
        [late], now=datetime(2026, 9, 24, 19, 0, tzinfo=timezone.utc)
    )
    assert refs == []


def test_close_uses_last_prestart_observed_not_backdated_late_snapshot():
    early = _row(key="early", odds=-110, updated="2026-09-24T17:30:00Z")
    middle = _row(key="middle", odds=-125, updated="2026-09-24T17:55:00Z")
    late = _row(key="late", odds=-200, updated="2026-09-24T17:40:00Z")
    late["fetched_at"] = "2026-09-24T18:12:00Z"
    refs = history.derive_captured_reference_rows(
        [early, late, middle], now=datetime(2026, 9, 24, 19, 0, tzinfo=timezone.utc)
    )
    assert [r["snapshot_kind"] for r in refs] == ["OPEN", "CLOSE"]
    assert refs[0]["american_odds"] == -110
    assert refs[1]["american_odds"] == -125
    assert refs[1]["fetched_at"] == "2026-09-24T17:55:00Z"


def test_unobserved_quote_and_future_provider_update_are_held():
    unknown = _row(key="unknown", odds=-100, updated="2026-09-24T17:00:00Z")
    unknown["fetched_at"] = None
    future_update = _row(key="future", odds=-150, updated="2026-09-24T18:15:00Z")
    future_update["fetched_at"] = "2026-09-24T17:55:00Z"
    refs = history.derive_captured_reference_rows(
        [unknown, future_update], now=datetime(2026, 9, 24, 19, 0, tzinfo=timezone.utc)
    )
    assert refs == []
