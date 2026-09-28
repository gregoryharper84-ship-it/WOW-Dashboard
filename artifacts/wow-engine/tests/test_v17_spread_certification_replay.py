from __future__ import annotations

from v17.spread_certification_replay import bind_nflverse_close_proxies


def test_nflverse_close_proxy_binding_uses_exact_game_id_and_reports_coverage():
    evidence, audit = bind_nflverse_close_proxies(
        test_event_ids=["2025_01_DAL_PHI", "2025_01_KC_LAC"],
        source_rows=[
            {
                "game_id": "2025_01_DAL_PHI",
                "home_team": "PHI",
                "away_team": "DAL",
                "spread_line": "7.5",
            },
            {
                "game_id": "2025_01_KC_LAC",
                "home_team": "LAC",
                "away_team": "KC",
                "spread_line": "-2.5",
            },
            {
                "game_id": "2025_01_OTHER_GAME",
                "home_team": "X",
                "away_team": "Y",
                "spread_line": "1.5",
            },
        ],
    )
    assert set(evidence) == {"2025_01_DAL_PHI", "2025_01_KC_LAC"}
    assert evidence["2025_01_DAL_PHI"].home_spread == -7.5
    assert evidence["2025_01_KC_LAC"].home_spread == 2.5
    assert audit["bound_event_n"] == 2
    assert audit["coverage"] == 1.0
    assert audit["blocker_counts"] == {}
    assert audit["live_card_receipt_eligible"] is False
    assert audit["probability_publishable"] is False
    assert audit["can_execute"] is False


def test_nflverse_close_proxy_binding_fails_row_scoped_for_missing_or_bad_line():
    evidence, audit = bind_nflverse_close_proxies(
        test_event_ids=["g1", "g2", "g3"],
        source_rows=[
            {"game_id": "g1", "home_team": "H", "away_team": "A", "spread_line": "3.0"},
            {"game_id": "g2", "home_team": "H", "away_team": "A", "spread_line": ""},
        ],
    )
    assert set(evidence) == {"g1"}
    assert audit["bound_event_n"] == 1
    assert audit["coverage"] == 1 / 3
    assert audit["blocker_counts"] == {
        "SPREAD_HISTORICAL_PROXY_ROW_INCOMPLETE": 1,
        "SPREAD_NFLVERSE_CLOSE_PROXY_EVENT_MISSING": 1,
    }


def test_nflverse_duplicate_event_rows_are_ambiguous_not_silently_selected():
    evidence, audit = bind_nflverse_close_proxies(
        test_event_ids=["g1"],
        source_rows=[
            {"game_id": "g1", "home_team": "H", "away_team": "A", "spread_line": "3.0"},
            {"game_id": "g1", "home_team": "H", "away_team": "A", "spread_line": "3.5"},
        ],
    )
    assert evidence == {}
    assert audit["blocker_counts"] == {"SPREAD_NFLVERSE_CLOSE_PROXY_EVENT_AMBIGUOUS": 1}
