"""Regression coverage for provider-normalized prop identity completeness.

These tests lock the research-only acquisition boundary. A provider prop row may
be preserved only when the exact line and participant identity are explicit;
rows with incomplete identity must fail closed rather than entering Scout as an
ambiguous candidate. Multiple players sharing the same market/direction/line
must remain distinct.
"""
from __future__ import annotations

from v17 import market_evidence_sources as sources


def _row(**overrides):
    row = {
        "sportsbook": "Pinnacle",
        "event_id": "evt-prop-identity",
        "home_team": "Home Team",
        "away_team": "Away Team",
        "market": "player_points",
        "selection": "Over",
        "odds": -110,
        "line": 24.5,
        "player_name": "Player One",
    }
    row.update(overrides)
    return row


def test_sharpapi_prop_requires_exact_line_and_participant_identity():
    assert sources.sharpapi_rows_to_odds_api_v4([_row(line=None)]) == []
    assert sources.sharpapi_rows_to_odds_api_v4([_row(player_name=None)]) == []


def test_sharpapi_complete_prop_preserves_identity_and_exact_line():
    events = sources.sharpapi_rows_to_odds_api_v4([_row()])
    assert len(events) == 1
    market = events[0]["bookmakers"][0]["markets"][0]
    assert market["key"] == "player_points"
    assert market["outcomes"] == [{
        "name": "Over",
        "price": -110,
        "point": 24.5,
        "description": "Player One",
    }]


def test_same_market_direction_and_line_keeps_distinct_players():
    events = sources.sharpapi_rows_to_odds_api_v4([
        _row(player_name="Player One"),
        _row(player_name="Player Two"),
    ])
    outcomes = events[0]["bookmakers"][0]["markets"][0]["outcomes"]
    assert {outcome["description"] for outcome in outcomes} == {"Player One", "Player Two"}
    assert all(outcome["point"] == 24.5 for outcome in outcomes)


def test_odds_api_v4_prop_coercion_rejects_incomplete_identity():
    incomplete = {
        "id": "evt",
        "bookmakers": [{
            "key": "book",
            "markets": [{
                "key": "player_points",
                "outcomes": [{"name": "Over", "price": -110, "point": 24.5}],
            }],
        }],
    }
    assert sources.coerce_odds_api_v4_event(incomplete) is None
