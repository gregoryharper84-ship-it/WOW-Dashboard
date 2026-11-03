from __future__ import annotations

from copy import deepcopy

import pytest

from v17.nfl_pickem_full_pool_equity import (
    PoolEquityInputError,
    evaluate_full_pool_shadow,
)


def _inputs(p: float = .51) -> tuple[list[dict], list[dict]]:
    pick = {
        "status": "PICKEM_READY",
        "source_terminal_label": "MODEL_QUALIFIED_HOLD",
        "official_event_id": "AWAY@HOME",
        "home_team": "HOME",
        "away_team": "AWAY",
        "pool_pick": "HOME",
        "selected_probability": p,
        "home_probability": p,
        "away_probability": 1 - p,
        "calibrated_selection_lower_bound": max(.01, p - .06),
        "calibrated_selection_upper_bound": min(.99, p + .06),
        "source_prediction_id": "pred-1",
        "source_snapshot_id": "snapshot-1",
        "controlling_specialist": "wow.nfl-game-win-probability-expert",
        "immutable_model_timestamp": "2026-10-10T12:00:00Z",
        "scheduled_start_utc": "2026-10-11T19:00:00Z",
        "can_execute": False,
    }
    opponent = {
        "entry_id": "entry-other",
        "receipt_id": "receipt-other",
        "availability_source": "documented-before-lock",
        "audience": "OPPONENT_ENTRIES",
        "submitted_at": "2026-10-10T13:00:00Z",
        "available_to_user_at": "2026-10-10T14:00:00Z",
        "picks": {"AWAY@HOME": "HOME"},
    }
    return [pick], [opponent]


LOCK = "2026-10-11T12:00:00Z"


def test_exact_two_outcome_enumeration_selects_competitive_minor_side_without_model_mutation():
    picks, others = _inputs()
    immutable = deepcopy(picks)
    output = evaluate_full_pool_shadow(picks, others, lock_at=LOCK, pool_size=1 + len(others))
    assert output["scenario_count"] == 2
    assert output["pool_size"] == 2
    assert output["baseline"]["sole_first_probability_proxy"] == pytest.approx(0)
    assert output["baseline"]["equal_split_first_share_proxy"] == pytest.approx(.5)
    assert output["research_challenger"]["sole_first_probability_proxy"] == pytest.approx(0)
    assert output["research_challenger"]["equal_split_first_share_proxy"] == pytest.approx(.5)
    # The baseline splits a tie every time and therefore remains preferable
    # by the equal-split objective when p_home=.51; no blind contrarianism.
    assert output["changed_picks"] == []
    assert picks == immutable
    assert output["certified_first_place_equity"] is False
    assert output["production_pick_mutation_allowed"] is False
    assert output["production_probability_mutation_allowed"] is False
    assert output["automatic_promotion"] is False
    assert output["can_execute"] is False


def test_full_pool_can_disfavor_high_accuracy_baseline_with_three_opponents():
    picks, others = _inputs(.51)
    for i in range(2):
        entry = deepcopy(others[0])
        entry["entry_id"] = f"other-{i}"
        entry["receipt_id"] = f"receipt-{i}"
        others.append(entry)
    output = evaluate_full_pool_shadow(picks, others, lock_at=LOCK, pool_size=1 + len(others))
    assert output["baseline"]["equal_split_first_share_proxy"] == pytest.approx(.25)
    assert output["research_challenger"]["sole_first_probability_proxy"] == pytest.approx(.49)
    assert output["research_challenger"]["equal_split_first_share_proxy"] == pytest.approx(.49)
    assert output["changed_picks"] == [{
        "official_event_id": "AWAY@HOME",
        "baseline_side": "HOME",
        "research_challenger_side": "AWAY",
    }]
    assert output["expected_correct_sacrifice"] == pytest.approx(.02)


def test_high_confidence_baseline_not_flipped_by_extreme_opponent_concentration():
    picks, others = _inputs(.9)
    output = evaluate_full_pool_shadow(picks, others, lock_at=LOCK, pool_size=1 + len(others))
    assert output["changed_picks"] == []
    assert output["expected_correct_sacrifice"] == pytest.approx(0)


def test_missing_or_revealed_after_lock_opponent_entries_fail_closed():
    picks, others = _inputs()
    with pytest.raises(PoolEquityInputError, match="PICKEM_EQUITY_PRELOCK_OPPONENTS_REQUIRED"):
        evaluate_full_pool_shadow(picks, [], lock_at=LOCK, pool_size=1 + len(others))
    others[0]["available_to_user_at"] = "2026-10-11T12:00:01Z"
    with pytest.raises(PoolEquityInputError, match="PICKEM_EQUITY_OPPONENT_NOT_PRELOCK_AVAILABLE"):
        evaluate_full_pool_shadow(picks, others, lock_at=LOCK, pool_size=1 + len(others))


def test_post_kickoff_model_wrong_specialist_and_missing_bounds_fail_closed():
    picks, others = _inputs()
    picks[0]["immutable_model_timestamp"] = "2026-10-11T20:00:00Z"
    with pytest.raises(PoolEquityInputError, match="PICKEM_EQUITY_STALE_OR_POST_LOCK_MODEL"):
        evaluate_full_pool_shadow(picks, others, lock_at=LOCK, pool_size=1 + len(others))
    picks, others = _inputs()
    picks[0]["controlling_specialist"] = "odds-model"
    with pytest.raises(PoolEquityInputError, match="PICKEM_EQUITY_SPECIALIST_MISMATCH"):
        evaluate_full_pool_shadow(picks, others, lock_at=LOCK, pool_size=1 + len(others))
    picks, others = _inputs()
    del picks[0]["calibrated_selection_lower_bound"]
    with pytest.raises(PoolEquityInputError, match="PICKEM_EQUITY_BOUNDS_REQUIRED"):
        evaluate_full_pool_shadow(picks, others, lock_at=LOCK, pool_size=1 + len(others))


def test_probability_and_event_identity_are_not_fabricated():
    picks, others = _inputs()
    picks[0]["away_probability"] = .33
    with pytest.raises(PoolEquityInputError, match="PICKEM_EQUITY_BASELINE_MODEL_CONFLICT"):
        evaluate_full_pool_shadow(picks, others, lock_at=LOCK, pool_size=1 + len(others))
    picks, others = _inputs()
    others[0]["picks"] = {"not-the-game": "HOME"}
    with pytest.raises(PoolEquityInputError, match="PICKEM_EQUITY_OPPONENT_EVENT_SET_MISMATCH"):
        evaluate_full_pool_shadow(picks, others, lock_at=LOCK, pool_size=1 + len(others))


def test_two_game_board_is_deterministic_and_respects_zero_flip_limit():
    picks, others = _inputs()
    p2 = deepcopy(picks[0])
    p2["official_event_id"] = "A2@H2"
    p2["home_team"] = "H2"
    p2["away_team"] = "A2"
    p2["pool_pick"] = "H2"
    p2["source_prediction_id"] = "pred-2"
    p2["source_snapshot_id"] = "snapshot-2"
    others[0]["picks"]["A2@H2"] = "H2"
    picks.append(p2)
    first = evaluate_full_pool_shadow(picks, others, lock_at=LOCK, pool_size=1 + len(others), max_flips=0)
    second = evaluate_full_pool_shadow(picks, others, lock_at=LOCK, pool_size=1 + len(others), max_flips=0)
    assert first == second
    assert first["scenario_count"] == 4
    assert first["changed_picks"] == []
    assert first["research_challenger"] == first["baseline"]


def test_valid_boundary_calibration_interval_can_include_zero_and_one():
    picks, others = _inputs()
    picks[0]["calibrated_selection_lower_bound"] = 0.0
    picks[0]["calibrated_selection_upper_bound"] = 1.0
    out = evaluate_full_pool_shadow(picks, others, lock_at=LOCK, pool_size=1 + len(others))
    assert out["status"] == "RESEARCH_SHADOW_COMPLETE"


def test_missing_opponent_cannot_fake_full_pool_simulation():
    picks, others = _inputs()
    with pytest.raises(PoolEquityInputError, match="PICKEM_EQUITY_POOL_SIZE_MISMATCH"):
        evaluate_full_pool_shadow(picks, others, lock_at=LOCK, pool_size=12)


def test_duplicate_ownership_receipt_and_wrong_audience_blocked():
    picks, others = _inputs()
    copied = deepcopy(others[0])
    copied["entry_id"] = "second-name-same-receipt"
    with pytest.raises(PoolEquityInputError, match="PICKEM_EQUITY_DUPLICATE_OPPONENT_RECEIPT"):
        evaluate_full_pool_shadow(picks, [others[0], copied], lock_at=LOCK, pool_size=3)
    others[0]["audience"] = "ALL_ENTRIES"
    with pytest.raises(PoolEquityInputError, match="PICKEM_EQUITY_OPPONENT_AUDIENCE_INVALID"):
        evaluate_full_pool_shadow(picks, others, lock_at=LOCK, pool_size=2)


def test_full_pool_rejects_claimed_opponent_availability_before_submission():
    picks, opponents = _inputs()
    opponents[0]["available_to_user_at"] = "2026-10-10T12:59:59Z"
    with pytest.raises(PoolEquityInputError, match="PICKEM_EQUITY_OPPONENT_AVAILABILITY_BEFORE_SUBMISSION"):
        evaluate_full_pool_shadow(picks, opponents, lock_at=LOCK, pool_size=2)


def test_research_does_not_modify_live_pickem_objective():
    from v17.nfl_pickem_pool_optimizer import DECISION_OBJECTIVE, CAN_EXECUTE
    assert DECISION_OBJECTIVE == "MAX_EXPECTED_CORRECT"
    assert CAN_EXECUTE is False
