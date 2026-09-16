from __future__ import annotations

from v17 import market_evidence_sources as sources
from v17 import sharpapi_prop_compat as compat
from v17.nightly_multiscout import bookmaker_rows, is_prop_market


def _live_shape_rows():
    return [
        {
            "game_id": "nfl-1",
            "league": "nfl",
            "home_team": "Detroit Lions",
            "away_team": "Buffalo Bills",
            "start_time": "2026-09-17T00:15:00Z",
            "bookmaker": "ExampleBook",
            "is_player_prop": True,
            "market_type": "Player Props",
            "stat_category": "Receiving Yards",
            "player_name": "Example Receiver",
            "selection": "Over",
            "line": 49.5,
            "odds": -110,
        }
    ]


def test_live_sharpapi_player_prop_shape_becomes_scout_prop_market():
    events = compat.augment_sharpapi_props([], _live_shape_rows(), sport_key="americanfootball_nfl")
    assert len(events) == 1
    rows = bookmaker_rows(events[0])
    assert len(rows) == 1
    assert rows[0]["market_key"] == "receiving_yards"
    assert is_prop_market(rows[0]["market_key"]) is True
    assert rows[0]["description"] == "Example Receiver"
    assert rows[0]["outcome_name"] == "Over"
    assert rows[0]["point"] == 49.5
    assert rows[0]["price"] == -110


def test_unknown_player_stat_is_explicitly_prop_prefixed():
    row = _live_shape_rows()[0] | {"stat_category": "Completions"}
    events = compat.augment_sharpapi_props([], [row], sport_key="americanfootball_nfl")
    market_key = bookmaker_rows(events[0])[0]["market_key"]
    assert market_key == "player_completions"
    assert is_prop_market(market_key) is True


def test_installed_adapter_preserves_evidence_only_normalization(monkeypatch):
    original = sources.sharpapi_rows_to_odds_api_v4
    monkeypatch.setattr(compat, "_INSTALLED", False)
    monkeypatch.setattr(compat, "_ORIGINAL", None)
    monkeypatch.setattr(sources, "sharpapi_rows_to_odds_api_v4", original)
    compat.install()
    result = sources.normalize_market_payload(
        {"data": _live_shape_rows()},
        provider="SHARPAPI",
        capability="odds",
        sport_key="americanfootball_nfl",
        primary_failure="ODDS_API_FEATURED_ODDS_FALLBACK_ERROR:HTTP_401",
    )
    assert result.ok is True
    assert result.code == "MARKET_EVIDENCE_NORMALISED"
    assert len(result.data) == 1
    assert result.data[0]["_wow_market_evidence"]["prediction_authority"] is False
    rows = bookmaker_rows(result.data[0])
    assert rows[0]["description"] == "Example Receiver"
    assert rows[0]["point"] == 49.5
