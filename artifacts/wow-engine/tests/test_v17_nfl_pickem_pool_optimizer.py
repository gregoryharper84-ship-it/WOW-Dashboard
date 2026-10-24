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
    """Production-shaped NFL publication row; no synthetic generic aliases."""
    away_p = 1.0 - home_p
    selected = home if home_p >= away_p else away
    opponent = away if selected == home else home
    selected_p = max(home_p, away_p)
    home_lower = max(0.0, home_p - 0.04)
    home_upper = min(1.0, home_p + 0.04)
    away_lower = max(0.0, away_p - 0.04)
    away_upper = min(1.0, away_p + 0.04)
    selected_lower = home_lower if selected == home else away_lower
    return {
        "event_prediction_id": f"pred-{event_id}",
        "model_version": "NFL_CHAMPION_TEST",
        "model_timestamp": "2026-09-30T12:00:00+00:00",
        "calibration_method": "TEST_CALIBRATOR",
        "calibration_version": "TEST_V1",
        "source_snapshot_id": f"snap-{event_id}",
        "calibrated_home_probability": home_p,
        "calibrated_away_probability": away_p,
        "calibrated_home_lower_bound": home_lower,
        "calibrated_home_upper_bound": home_upper,
        "calibrated_away_lower_bound": away_lower,
        "calibrated_away_upper_bound": away_upper,
        "calibrated_selection_probability": selected_p,
        "rank_calibrated_lower_bound": selected_lower,
        "selected_participant": selected,
        "opponent": opponent,
        "sporting_probability_completed": True,
        "sporting_probability_status": "COMPLETED",
        "probability_fields_withheld": False,
        "model_probability_available": True,
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
            "source_snapshot_id": f"snap-{event_id}",
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
    assert board["source_terminals_preserved"] is True


def test_pick_is_downstream_of_model_and_ignores_market_price_fields():
    base = _row("evt-1", "PIT", "CLE", 0.58)
    noisy = deepcopy(base)
    noisy.update(
        {
            "market_probability": 0.01,
            "sportsbook_implied_probability": 0.99,
            "moneyline": 5000,
            "pool_pick_popularity": 0.01,
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
    assert b["pool_popularity_used"] is False
    assert b["review_overlay_can_change_pool_pick"] is False
    assert b["review_overlay_can_change_probability"] is False


def test_week4_learning_overlay_flags_material_disagreement_without_flipping_pick():
    row = _row("evt-fragility", "JAX", "CIN", 0.61)
    row["model_disagreement"] = 0.08
    out = select_pickem_game(row)
    assert out["status"] == PICKEM_READY
    assert out["pool_pick"] == "CIN"
    assert out["selected_probability"] == 0.61
    assert out["pickem_review_class"] == "MODEL_SIDE_FRAGILITY_REVIEW"
    assert "MATERIAL_MODEL_DISAGREEMENT" in out["pickem_review_reasons"]
    assert out["postmortem_learning_action"] == "DEEP_REVIEW_NO_AUTOMATIC_FLIP"
    assert out["review_overlay_can_change_pool_pick"] is False
    assert out["review_overlay_can_change_probability"] is False


def test_strong_model_side_gets_preserve_guardrail_after_isolated_loss_pattern():
    row = _row("evt-strong", "NE", "BUF", 0.72)
    row["model_disagreement"] = 0.02
    out = select_pickem_game(row)
    assert out["status"] == PICKEM_READY
    assert out["pool_pick"] == "BUF"
    assert out["pickem_review_class"] == "HIGH_CONFIDENCE_HOLD"
    assert out["postmortem_learning_action"] == "PRESERVE_UNLESS_COHORT_EVIDENCE"
    assert "MATERIAL_MODEL_DISAGREEMENT" not in out["pickem_review_reasons"]


def test_toss_up_is_routed_to_review_but_controlling_scorer_choice_is_preserved():
    row = _row("evt-toss-review", "DAL", "HOU", 0.53)
    out = select_pickem_game(row)
    assert out["status"] == PICKEM_READY
    assert out["pool_pick"] == "HOU"
    assert out["pickem_review_class"] == "TOSS_UP_REVIEW"
    assert "TOSS_UP_POINT_PROBABILITY" in out["pickem_review_reasons"]
    assert "NARROW_TWO_SIDED_GAP" in out["pickem_review_reasons"]
    assert out["review_overlay_can_change_pool_pick"] is False


def test_board_summarizes_review_routing_without_affecting_submission_readiness():
    strong = _row("evt-strong-board", "NE", "BUF", 0.72)
    strong["model_disagreement"] = 0.02
    fragile = _row("evt-fragile-board", "JAX", "CIN", 0.61)
    fragile["model_disagreement"] = 0.08
    toss = _row("evt-toss-board", "DAL", "HOU", 0.53)
    board = build_pickem_board([strong, fragile, toss], expected_game_count=3)
    assert board["status"] == PICKEM_BOARD_READY
    assert board["submission_ready"] is True
    assert board["high_confidence_hold_count"] == 1
    assert board["model_side_fragility_review_count"] == 1
    assert board["toss_up_review_count"] == 1


def test_toss_up_preserves_the_controlling_scorer_tie_choice():
    row = _row("evt-tie", "ATL", "NO", 0.50)
    result = select_pickem_game(row)
    assert result["status"] == PICKEM_READY
    assert result["pool_pick"] == "NO"
    assert result["confidence_band"] == "TOSS_UP"
    assert result["probability_gap"] == 0.0


def test_model_qualified_hold_remains_a_required_pick_without_terminal_upgrade():
    row = _row("evt-held", "JAX", "CIN", 0.62)
    row.update(
        {
            "code": "LLP_EVENT_GOVERNANCE_NOT_PROVEN",
            "terminal_label": "MODEL_QUALIFIED_HOLD",
            "probability_publishable": False,
            "rank_eligible": False,
            "blockers": ["LLP_EVENT_DECISION_GOVERNOR_NOT_PROVEN"],
        }
    )
    out = select_pickem_game(row)
    assert out["status"] == PICKEM_READY
    assert out["pool_pick"] == "CIN"
    assert out["source_terminal_label"] == "MODEL_QUALIFIED_HOLD"
    assert out["source_probability_publishable"] is False
    assert out["source_rank_eligible"] is False
    assert out["source_terminal_upgraded"] is False
    assert out["can_execute"] is False


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


def test_malformed_bounds_cannot_become_a_pick():
    malformed = _row("evt-malformed", "NYJ", "CHI", 0.54)
    malformed.pop("rank_calibrated_lower_bound")
    malformed.pop("calibrated_home_lower_bound")
    out = select_pickem_game(malformed)
    assert out["status"] == PICKEM_BLOCKED
    assert out["source_model_status"] == "MODEL_OUTPUT_INVALID"
    assert "PICKEM_CALIBRATED_SELECTION_LOWER_BOUND_REQUIRED" in out["blockers"]


def test_withheld_or_hard_rejected_probability_stays_blocked():
    withheld = _row("evt-withheld", "ARI", "NYG", 0.57)
    withheld["probability_fields_withheld"] = True
    out = select_pickem_game(withheld)
    assert out["status"] == PICKEM_BLOCKED
    assert "PICKEM_PROBABILITY_FIELDS_WITHHELD" in out["blockers"]

    rejected = _row("evt-rejected", "GB", "TB", 0.61)
    rejected["terminal_label"] = "REJECT_DATA_QUALITY"
    out2 = select_pickem_game(rejected)
    assert out2["status"] == PICKEM_BLOCKED
    assert "PICKEM_SOURCE_TERMINAL_NOT_PROBABILITY_BEARING" in out2["blockers"]


def test_stale_model_output_stays_blocked_even_with_numeric_fields_present():
    row = _row("evt-stale", "MIA", "MIN", 0.59)
    row["code"] = "STALE_MODEL_OUTPUT"
    row["blockers"] = ["IMMUTABLE_MODEL_TIMESTAMP_PRECEDES_LATEST_MATERIAL_UPDATE"]
    out = select_pickem_game(row)
    assert out["status"] == PICKEM_BLOCKED
    assert out["source_model_status"] == "STALE_MODEL_OUTPUT"


def test_selected_participant_must_match_the_controlling_model_distribution():
    row = _row("evt-mismatch", "GB", "TB", 0.61)
    row["selected_participant"] = "GB"
    row["opponent"] = "TB"
    row["calibrated_selection_probability"] = 0.39
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
