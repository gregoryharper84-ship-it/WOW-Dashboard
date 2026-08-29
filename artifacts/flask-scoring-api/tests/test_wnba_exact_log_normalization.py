"""Acceptance tests for WNBA exact-stat game_log normalization."""
from __future__ import annotations


def test_structured_exact_query_rows_normalize_to_numeric_game_log_without_touching_box_score():
    from gate_engine import wnba_generative_gate as gate

    box = [{"minutes": 32, "points": 20, "rebounds": 6, "assists": 4,
            "field_goal_attempts": 15, "usage_rate": .24} for _ in range(10)]
    structured = [
        {"date": f"2026-08-{10+i:02d}", "opponent": "OPP", "stat": 18 + i % 4,
         "line": 18.5, "hit": bool((18 + i % 4) > 18.5)}
        for i in range(10)
    ]
    enr = {"game_log": structured, "box_score_log": box}
    row = {"sport": "WNBA", "stat_key": "PTS"}

    normalized, blockers = gate._normalize_exact_game_log(row, enr)

    assert blockers == []
    assert normalized["game_log"] == [18.0, 19.0, 20.0, 21.0, 18.0, 19.0, 20.0, 21.0, 18.0, 19.0]
    assert normalized["box_score_log"] is box
    assert normalized["game_log"] is not normalized["box_score_log"]
    audit = normalized["game_log_normalization_audit"]
    assert audit["source_type"] == "list[dict]"
    assert audit["target_type"] == "list[number]"
    assert audit["box_score_log_untouched"] is True


def test_composite_exact_log_can_be_derived_from_explicit_components():
    from gate_engine import wnba_generative_gate as gate

    structured = [
        {"points": 20, "rebounds": 7, "assists": 5},
        {"points": 16, "rebounds": 8, "assists": 6},
    ]
    normalized, blockers = gate._normalize_exact_game_log(
        {"sport": "WNBA", "stat_key": "PRA"},
        {"game_log": structured, "box_score_log": list(structured)},
    )
    assert blockers == []
    assert normalized["game_log"] == [32.0, 30.0]


def test_unresolved_structured_rows_fail_closed_instead_of_guessing():
    from gate_engine import wnba_generative_gate as gate

    normalized, blockers = gate._normalize_exact_game_log(
        {"sport": "WNBA", "stat_key": "PTS"},
        {"game_log": [{"date": "2026-08-20", "opponent": "OPP"}], "box_score_log": []},
    )
    assert normalized["game_log"] == []
    assert blockers == [
        "WNBA_ACQUISITION:EXACT_GAME_LOG_NORMALIZATION_PARTIAL:1_ROWS_UNRESOLVED"
    ]
