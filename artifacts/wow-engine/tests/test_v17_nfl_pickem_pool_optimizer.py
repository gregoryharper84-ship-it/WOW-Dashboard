from __future__ import annotations

from copy import deepcopy

import pytest

from v17.nfl_pickem_pool_optimizer import (
    DECISION_OBJECTIVE,
    PICKEM_BOARD_INCOMPLETE,
    PICKEM_BOARD_READY,
    PICKEM_BLOCKED,
    PICKEM_READY,
    build_pickem_board,
    select_pickem_game,
    tiebreaker_capability,
)

MATCHUPS = [
    ("PIT", "CLE"),
    ("IND", "WAS"),
    ("NE", "BUF"),
    ("NYJ", "CHI"),
    ("JAX", "CIN"),
    ("ARI", "NYG"),
    ("LA", "PHI"),
    ("GB", "TB"),
    ("TEN", "BAL"),
    ("DAL", "HOU"),
    ("MIA", "MIN"),
    ("KC", "LV"),
    ("DEN", "SF"),
    ("LAC", "SEA"),
    ("DET", "CAR"),
    ("ATL", "NO"),
]


def _row(event_id: str, away: str, home: str, home_p: float) -> dict:
    away_p = 1.0 - home_p
    selected = home if home_p >= away_p else away
    opponent = away if selected == home else home
    selected_p = max(home_p, away_p)
    lower = max(0.0, selected_p - 0.04)
    upper = min(1.0, selected_p + 0.04)
    return {
        "prediction_id": f"pred-{event_id}",
        "candidate_id": f"cand-{event_id}",
        "model_version": "NFL_CHAMPION_TEST",
        "immutable_model_timestamp": "2026-09-30T12:00:00+00:00",
        "calibration_method": "TEST_CALIBRATOR",
        "calibration_version": "TEST_V1",
        "source_snapshot_id": f"snap-{event_id}",
        "source_snapshot_timestamp": "2026-09-30T11:59:00+00:00",
        "outcome_space": "NFL_FULL_GAME_WINNER",
        "calibrated_probability": selected_p,
        "calibrated_lower_bound": lower,
        "calibrated_upper_bound": upper,
        "calibrated_home_probability": home_p,
        "calibrated_away_probability": away_p,
        "calibrated_selection_probability": selected_p,
        "selected_participant": selected,
        "opponent": opponent,
        "sporting_probability_completed": True,
        "sporting_probability_status": "COMPLETED",
        "probability_fields_withheld": False,
        "probability_publishable": True,
        "rank_eligible": True,
        "terminal_label": "FINAL_APPROVED",
        "global_terminal_authority": "V17_TERMINAL_REDUCER",
        "code": "GOVERNED_PROBABILITY_PUBLISHED",
        "can_execute": False,
        "candidate_envelope": {
            "official_event_id": event_id,
            "sport": "NFL",
            "league": "NFL",
            "home_team": home,
            "away_team": away,
        },
    }


def test_week4_acceptance_16_games_in_exactly_16_required_picks_out():
    rows = [
        _row(f"2026-W4-{i:02d}", away, home, 0.51 + (i % 8) * 0.025)
        for i, (away, home) in enumerate(MATCHUPS, 1)
    ]
    board = build_pickem_board(rows, expected_game_count=16)
    assert board["status"] == PICKEM_BOARD_READY
    assert board["submission_ready"] is True
    assert board["ready_pick_count"] == 16
    assert board["blocked_event_count"] == 0
    assert len({pick["official_event_id"] for pick in board["picks"]}) == 16
    assert all(
        pick["pool_pick"] in {pick["home_team"], pick["away_team"]}
        for pick in board["picks"]
    )
    assert all(pick["can_execute"] is False for pick in board["picks"])


def test_pick_is_downstream_of_model_and_ignores_market_price_fields():
    base = _row("evt-1", "PIT", "CLE", 0.58)
    noisy = deepcopy(base)
    noisy.update(
        {
            "market_probability": 0.01,
            "sportsbook_implied_probability": 0.99,
            "moneyline": 5000,
        }
    )
    a = select_pickem_game(base)
    b = select_pickem_game(noisy)
    assert a["status"] == PICKEM_READY
    assert b["status"] == PICKEM_READY
    assert a["pool_pick"] == b["pool_pick"] == "CLE"
    assert a["selected_probability"] == b["selected_probability"] == 0.58
    assert b["market_probability_used"] is False
    assert b["sportsbook_price_used"] is False


def test_toss_up_preserves_the_controlling_scorer_tie_choice():
    row = _row("evt-tie", "ATL", "NO", 0.50)
    result = select_pickem_game(row)
    assert result["status"] == PICKEM_READY
    assert result["pool_pick"] == "NO"
    assert result["confidence_band"] == "TOSS_UP"
    assert result["probability_gap"] == 0.0


def test_typed_scorer_failure_is_preserved_not_collapsed_to_model_unavailable():
    row = {
        "code": "MODEL_SCORER_FAILED",
        "blockers": ["TEAM_EVENT_SCORER_TIMEOUT_OR_TRANSPORT_FAILURE"],
        "sporting_probability_completed": False,
        "candidate_envelope": {
            "official_event_id": "evt-fail",
            "sport": "NFL",
            "league": "NFL",
            "home_team": "BUF",
            "away_team": "NE",
        },
    }
    result = select_pickem_game(row)
    assert result["status"] == PICKEM_BLOCKED
    assert result["source_model_status"] == "MODEL_SCORER_FAILED"
    assert "TEAM_EVENT_SCORER_TIMEOUT_OR_TRANSPORT_FAILURE" in result["blockers"]


def test_malformed_or_nonfinal_governed_output_cannot_become_a_pick():
    malformed = _row("evt-malformed", "NYJ", "CHI", 0.54)
    malformed.pop("calibrated_lower_bound")
    out = select_pickem_game(malformed)
    assert out["status"] == PICKEM_BLOCKED
    assert out["source_model_status"] == "MODEL_OUTPUT_INVALID"

    held = _row("evt-held", "JAX", "CIN", 0.62)
    held["probability_publishable"] = False
    held["rank_eligible"] = False
    held["terminal_label"] = "MODEL_QUALIFIED_HOLD"
    out2 = select_pickem_game(held)
    assert out2["status"] == PICKEM_BLOCKED
    assert out2["source_model_status"] == "PICKEM_GOVERNANCE_NOT_FINAL"


def test_selected_participant_must_match_the_controlling_model_distribution():
    row = _row("evt-mismatch", "GB", "TB", 0.61)
    row["selected_participant"] = "GB"
    row["opponent"] = "TB"
    row["calibrated_selection_probability"] = 0.39
    row["calibrated_probability"] = 0.39
    row["calibrated_lower_bound"] = 0.35
    row["calibrated_upper_bound"] = 0.43
    out = select_pickem_game(row)
    assert out["status"] == PICKEM_BLOCKED
    assert "PICKEM_SELECTION_MODEL_OUTPUT_MISMATCH" in out["blockers"]


def test_duplicate_event_rows_are_blocked_and_board_is_incomplete():
    row = _row("evt-dup", "TEN", "BAL", 0.66)
    board = build_pickem_board([row, deepcopy(row)], expected_game_count=1)
    assert board["status"] == PICKEM_BOARD_INCOMPLETE
    assert board["ready_pick_count"] == 0
    assert board["blocked_event_count"] == 1
    assert "PICKEM_DUPLICATE_GOVERNED_EVENT_ROW" in board["blocked"][0]["blockers"]


def test_non_max_expected_correct_strategy_is_not_silently_implemented():
    with pytest.raises(ValueError, match="PICKEM_STRATEGY_MODE_UNSUPPORTED"):
        build_pickem_board(
            [_row("evt-1", "DEN", "SF", 0.60)],
            strategy_mode="POOL_WIN_EQUITY",
        )
    assert DECISION_OBJECTIVE == "MAX_EXPECTED_CORRECT"


def test_tiebreaker_fails_closed_without_certified_total_points_model():
    status = tiebreaker_capability()
    assert status == {
        "status": "UNAVAILABLE",
        "blocker": "NFL_CERTIFIED_FULL_GAME_TOTAL_MODEL_NOT_REGISTERED",
        "moneyline_probability_reuse_allowed": False,
        "sportsbook_total_substitution_allowed": False,
        "can_execute": False,
    }
