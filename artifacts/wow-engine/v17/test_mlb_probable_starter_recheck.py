"""Official-source recheck of an MLB probable starter.

The schedule's ``probablePitcher`` is the provisional datum; the official
boxscore is the confirmation surface. These tests use injected payloads and
never touch the network.
"""
from __future__ import annotations

from datetime import datetime, timedelta, timezone

import prop_auto_hydration as mlb

EVENT_START = (datetime.now(timezone.utc) + timedelta(hours=2)).isoformat()
NOW = datetime.now(timezone.utc)


def _box(home_first=660271, away_first=111111, **overrides):
    teams = {
        "home": {
            "battingOrder": list(range(1, 10)),
            "pitchers": [home_first, 222222],
        },
        "away": {
            "battingOrder": list(range(11, 20)),
            "pitchers": [away_first, 333333],
        },
    }
    teams.update(overrides)
    return {"teams": teams}


def test_posted_lineups_with_the_player_first_confirm_the_start(monkeypatch):
    monkeypatch.setattr(mlb, "_request_json", lambda *a, **k: _box())

    confirmed, detail = mlb._confirmed_starting_pitcher(
        823001, side="home", player_id=660271, http_get=lambda *a, **k: None
    )

    assert confirmed is True
    assert detail["lineups_posted"] is True
    assert detail["target_first_pitcher"] == 660271


def test_unposted_lineups_do_not_confirm_even_when_listed_first(monkeypatch):
    box = _box()
    box["teams"]["away"]["battingOrder"] = []
    monkeypatch.setattr(mlb, "_request_json", lambda *a, **k: box)

    confirmed, detail = mlb._confirmed_starting_pitcher(
        823001, side="home", player_id=660271, http_get=lambda *a, **k: None
    )

    assert confirmed is False
    assert detail["lineups_posted"] is False


def test_a_different_first_pitcher_is_not_confirmation(monkeypatch):
    monkeypatch.setattr(mlb, "_request_json", lambda *a, **k: _box(home_first=444444))

    confirmed, _detail = mlb._confirmed_starting_pitcher(
        823001, side="home", player_id=660271, http_get=lambda *a, **k: None
    )

    assert confirmed is False


def _patch_schedule(monkeypatch, *, confirmed: bool):
    monkeypatch.setattr(mlb, "_resolve_player_id", lambda *a, **k: (660271, "Test Pitcher"))
    monkeypatch.setattr(
        mlb,
        "_schedule_context",
        lambda *a, **k: {
            "official_game_pk": 823001,
            "side": "home",
            "team": "LAD",
            "official_game_date": EVENT_START,
            "schedule_status": "Scheduled",
        },
    )
    monkeypatch.setattr(
        mlb,
        "_confirmed_starting_pitcher",
        lambda *a, **k: (
            confirmed,
            {"lineups_posted": confirmed, "sides": {}, "target_first_pitcher": 660271},
        ),
    )


def test_recheck_returns_a_confirmed_starter_block(monkeypatch):
    _patch_schedule(monkeypatch, confirmed=True)

    block = mlb.recheck_mlb_probable_starter(
        player="Test Pitcher", event_start_time=EVENT_START, now=NOW
    )

    assert block["basis"] == "CONFIRMED_STARTER"
    assert block["confirmation"] == "CONFIRMED"
    assert block["evidence_status"] == "RETRIEVED"
    assert block["fresh"] is True
    assert block["athlete_id"] == "660271"
    assert block["event_id"] == "823001"
    assert block["recheck_status"] == "CONFIRMED_STARTER"
    assert block["can_execute"] is False


def test_recheck_stays_provisional_when_not_confirmed(monkeypatch):
    _patch_schedule(monkeypatch, confirmed=False)

    block = mlb.recheck_mlb_probable_starter(
        player="Test Pitcher", event_start_time=EVENT_START, now=NOW
    )

    assert block["basis"] == "PROBABLE_STARTER"
    assert block["recheck_status"] == "LINEUPS_NOT_POSTED"
    assert block["can_execute"] is False


def test_unresolved_official_listing_is_a_retrieval_failure(monkeypatch):
    monkeypatch.setattr(mlb, "_resolve_player_id", lambda *a, **k: (660271, "Test Pitcher"))

    def unresolved(*_a, **_k):
        raise mlb.PropAutoHydrationError("MLB_STARTER_STATUS_UNRESOLVED", "not listed")

    monkeypatch.setattr(mlb, "_schedule_context", unresolved)

    block = mlb.recheck_mlb_probable_starter(
        player="Test Pitcher", event_start_time=EVENT_START, now=NOW
    )

    assert block["evidence_status"] == "RETRIEVAL_FAILED"
    assert block["recheck_status"] == "OFFICIAL_LISTING_UNRESOLVED"
    assert block["can_execute"] is False
