"""Contract pins for current documented SharpAPI core market identifiers."""
from __future__ import annotations

from v17 import market_evidence_native_live as live
from v17 import market_evidence_sources as sources


def test_sharpapi_documented_total_points_is_canonical_totals():
    assert sources.canonical_market_key("total_points") == "totals"


def test_sharpapi_total_points_rows_survive_live_normalization():
    rows = [
        {
            "event_id": "nhl-doc-total-1",
            "event_start_time": "2026-09-22T23:00:00Z",
            "home_team": "Dallas Stars",
            "away_team": "Colorado Avalanche",
            "league": "NHL",
            "market_type": "total_points",
            "selection": "Over",
            "selection_type": "over",
            "sportsbook": "fanduel",
            "odds_american": -110,
            "line": 6.5,
            "timestamp": "2026-09-22T18:00:00Z",
        },
        {
            "event_id": "nhl-doc-total-1",
            "event_start_time": "2026-09-22T23:00:00Z",
            "home_team": "Dallas Stars",
            "away_team": "Colorado Avalanche",
            "league": "NHL",
            "market_type": "total_points",
            "selection": "Under",
            "selection_type": "under",
            "sportsbook": "fanduel",
            "odds_american": -110,
            "line": 6.5,
            "timestamp": "2026-09-22T18:00:00Z",
        },
    ]

    events = live.sharpapi_rows_to_odds_api_v4(rows, sport_key="icehockey_nhl")
    assert len(events) == 1
    market = events[0]["bookmakers"][0]["markets"][0]
    assert market["key"] == "totals"
    assert {(row["name"], row["point"]) for row in market["outcomes"]} == {
        ("Over", 6.5),
        ("Under", 6.5),
    }
