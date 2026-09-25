from __future__ import annotations

from types import SimpleNamespace

import pytest

from v17 import cross_sport_discovery_feed as feed
from v17 import cross_sport_winner_discovery as discovery
from v17 import market_evidence_native_live as live


def _target(*, sport_id=3, league="MLB"):
    return SimpleNamespace(sport_id=sport_id, league=league)


def test_rundown_discovery_snapshot_is_bounded_to_core_main_lines(monkeypatch):
    calls = []

    def fake_snapshot(sport_key, date, **kwargs):
        calls.append((sport_key, date, kwargs))
        return SimpleNamespace(ok=True, data=[{"id": "rundown-1"}], code=None)

    monkeypatch.setattr(live, "get_sport_date_odds_snapshot", fake_snapshot)
    fetch = feed.rundown_board_feed(slate_date="2026-09-25")

    assert fetch("MLB", _target()) == [{"id": "rundown-1"}]
    assert len(calls) == 1
    sport_key, date, kwargs = calls[0]
    assert sport_key == "MLB"
    assert date == "2026-09-25"
    assert kwargs["capability"] == "events"
    assert kwargs["sport_id"] == 3
    assert kwargs["market_ids"] == ("1", "2", "3")
    assert kwargs["affiliate_ids"] == ("3", "19", "23")
    assert kwargs["main_line"] is True
    assert kwargs["hide_closed"] is True


def test_rundown_discovery_filters_are_operator_overridable(monkeypatch):
    monkeypatch.setenv("WOW_RUNDOWN_DISCOVERY_MARKET_IDS", "1, 7")
    monkeypatch.setenv("WOW_RUNDOWN_DISCOVERY_AFFILIATE_IDS", "19")

    assert feed.rundown_discovery_market_ids() == ("1", "7")
    assert feed.rundown_discovery_affiliate_ids() == ("19",)


def test_empty_override_does_not_accidentally_unbound_snapshot(monkeypatch):
    monkeypatch.setenv("WOW_RUNDOWN_DISCOVERY_MARKET_IDS", " , ")
    monkeypatch.setenv("WOW_RUNDOWN_DISCOVERY_AFFILIATE_IDS", "")

    assert feed.rundown_discovery_market_ids() == ("1", "2", "3")
    assert feed.rundown_discovery_affiliate_ids() == ("3", "19", "23")


def test_rundown_discovery_preserves_typed_provider_failure(monkeypatch):
    def fake_snapshot(*_args, **_kwargs):
        return SimpleNamespace(ok=False, data=None, code="RUNDOWN_QUOTA_EXHAUSTED")

    monkeypatch.setattr(live, "get_sport_date_odds_snapshot", fake_snapshot)
    fetch = feed.rundown_board_feed(slate_date="2026-09-25")

    with pytest.raises(discovery.DiscoveryFeedError) as exc:
        fetch("MLB", _target())

    assert exc.value.code == "RUNDOWN_QUOTA_EXHAUSTED"


def test_missing_provider_sport_id_remains_no_configured_feed():
    fetch = feed.rundown_board_feed(slate_date="2026-09-25")

    with pytest.raises(discovery.DiscoveryFeedError) as exc:
        fetch("BOXING", SimpleNamespace(sport_id=None, league="BOXING"))

    assert exc.value.code == "NO_CONFIGURED_DISCOVERY_FEED"
