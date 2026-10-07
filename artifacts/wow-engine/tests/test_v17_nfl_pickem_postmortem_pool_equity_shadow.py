from __future__ import annotations

import pytest

from v17.nfl_pickem_pool_win_equity_shadow import (
    SHADOW_ELIGIBLE,
    SHADOW_PRESERVE,
    build_pool_win_equity_shadow,
    evaluate_shadow_candidate,
)
from v17.nfl_pickem_postmortem import analyze_settled_pickem_week


EVENTS = (
    "PIT@CLE",
    "IND@WAS",
    "NE@BUF",
    "NYJ@CHI",
    "JAX@CIN",
    "ARI@NYG",
    "LAR@PHI",
    "GB@TB",
    "TEN@BAL",
    "DAL@HOU",
    "MIA@MIN",
    "KC@LV",
    "DEN@SF",
    "LAC@SEA",
    "DET@CAR",
    "ATL@NO",
)

WINNERS = dict(
    zip(
        EVENTS,
        (
            "CLE",
            "IND",
            "NE",
            "CHI",
            "JAX",
            "NYG",
            "LAR",
            "GB",
            "BAL",
            "DAL",
            "MIN",
            "KC",
            "SF",
            "SEA",
            "CAR",
            "ATL",
        ),
    )
)

PICKS = {
    "Jo": dict(zip(EVENTS, ("CLE","IND","BUF","CHI","CIN","ARI","LAR","GB","BAL","DAL","MIN","KC","SF","LAC","DET","NO"))),
    "Tre": dict(zip(EVENTS, ("CLE","WAS","BUF","CHI","JAX","ARI","PHI","GB","BAL","HOU","MIN","KC","DEN","SEA","DET","ATL"))),
    "Matt": dict(zip(EVENTS, ("PIT","IND","BUF","CHI","JAX","ARI","LAR","GB","BAL","HOU","MIN","KC","SF","SEA","DET","ATL"))),
    "Haug": dict(zip(EVENTS, ("PIT","WAS","BUF","CHI","JAX","NYG","LAR","GB","BAL","DAL","MIN","KC","SF","SEA","CAR","NO"))),
    "Drew": dict(zip(EVENTS, ("PIT","IND","BUF","CHI","CIN","ARI","LAR","GB","BAL","HOU","MIN","KC","SF","SEA","DET","NO"))),
    "Jay": dict(zip(EVENTS, ("PIT","IND","BUF","CHI","JAX","NYG","LAR","GB","BAL","DAL","MIN","KC","SF","SEA","DET","ATL"))),
    "Tim": dict(zip(EVENTS, ("CLE","WAS","BUF","CHI","CIN","ARI","LAR","GB","BAL","HOU","MIN","KC","SF","SEA","DET","NO"))),
    "Lew": dict(zip(EVENTS, ("CLE","WAS","BUF","CHI","JAX","ARI","PHI","GB","BAL","DAL","MIN","KC","SF","SEA","CAR","NO"))),
    "GH": dict(zip(EVENTS, ("PIT","IND","BUF","CHI","CIN","ARI","LAR","GB","BAL","HOU","MIN","KC","SF","SEA","DET","NO"))),
    "Ryan": dict(zip(EVENTS, ("PIT","IND","BUF","CHI","CIN","ARI","LAR","GB","BAL","HOU","MIN","KC","SF","LAC","DET","NO"))),
    "El": dict(zip(EVENTS, ("CLE","WAS","BUF","CHI","JAX","ARI","PHI","TB","BAL","DAL","MIN","KC","DEN","SEA","CAR","ATL"))),
    "Pops": dict(zip(EVENTS, ("PIT","WAS","BUF","CHI","CIN","NYG","LAR","GB","BAL","HOU","MIN","KC","SF","SEA","DET","NO"))),
}


def _pick(
    *,
    event_id: str,
    selected: str,
    opponent: str,
    p: float,
    review_class: str,
) -> dict:
    return {
        "status": "PICKEM_READY",
        "official_event_id": event_id,
        "pool_pick": selected,
        "opponent": opponent,
        "selected_probability": p,
        "home_probability": p,
        "away_probability": 1.0 - p,
        "pickem_review_class": review_class,
        "can_execute": False,
    }


def test_final_week4_sheet_regression_reconstructs_gh_outcome_and_consensus_concentration():
    report = analyze_settled_pickem_week(PICKS, WINNERS, user_name="GH")

    assert report["user_wins"] == 9
    assert report["user_losses"] == 7
    assert report["best_wins"] == 13
    assert report["gap_to_best"] == 4
    assert report["wins_rank"] == 7
    assert report["weekly_winners"] == ["Jay"]

    assert report["majority_follow_count"] == 14
    assert report["split_top_count"] == 2
    assert report["minority_pick_count"] == 0
    assert report["all_consensus_or_split_top"] is True
    assert report["unanimous_shared_loss_count"] == 1
    assert report["pool_strategy_shadow_review_earned"] is True
    assert report["production_probability_patch_earned"] is False
    assert report["production_probability_mutation_allowed"] is False
    assert report["can_execute"] is False


def test_week4_winner_gap_is_exactly_four_winner_side_disagreements():
    report = analyze_settled_pickem_week(PICKS, WINNERS, user_name="GH")
    comparison = report["winner_comparisons"][0]

    assert comparison["weekly_winner"] == "Jay"
    assert comparison["disagreement_count"] == 4
    assert comparison["winner_gain_count"] == 4
    assert comparison["user_gain_count"] == 0
    assert comparison["net_disagreement_swing"] == 4
    assert comparison["winner_gain_events"] == [
        "JAX@CIN",
        "ARI@NYG",
        "DAL@HOU",
        "ATL@NO",
    ]


def test_unanimous_buffalo_loss_is_shared_consensus_loss_not_differentiation_failure():
    report = analyze_settled_pickem_week(PICKS, WINNERS, user_name="GH")
    row = next(item for item in report["events"] if item["event_id"] == "NE@BUF")

    assert row["user_pick"] == "BUF"
    assert row["user_correct"] is False
    assert row["unanimous_shared_loss"] is True
    assert row["user_made_minority_pick"] is False


def test_tiebreaker_is_graded_as_realized_error_without_model_attribution():
    report = analyze_settled_pickem_week(
        PICKS,
        WINNERS,
        user_name="GH",
        tiebreaker_predictions={"GH": 44},
        actual_tiebreaker_total=69,
    )
    assert report["tiebreaker"] == {
        "available": True,
        "user_prediction": 44.0,
        "actual_total": 69.0,
        "absolute_error": 25.0,
        "model_attribution_available": False,
    }


def test_high_confidence_consensus_side_is_never_flipped_for_differentiation():
    pick = _pick(
        event_id="NE@BUF",
        selected="BUF",
        opponent="NE",
        p=0.72,
        review_class="HIGH_CONFIDENCE_HOLD",
    )
    out = evaluate_shadow_candidate(
        pick,
        pool_pick_share={"BUF": 1.0, "NE": 0.0},
        pool_size=12,
    )
    assert out["status"] == SHADOW_PRESERVE
    assert out["shadow_pool_pick"] == "BUF"
    assert "HIGH_CONFIDENCE_HOLD_PRESERVE" in out["reasons"]
    assert out["production_pool_pick_unchanged"] is True
    assert out["sporting_probabilities_unchanged"] is True


def test_fragile_overowned_near_tossup_can_become_shadow_differentiation_candidate():
    pick = _pick(
        event_id="ARI@NYG",
        selected="ARI",
        opponent="NYG",
        p=0.53,
        review_class="TOSS_UP_REVIEW",
    )
    out = evaluate_shadow_candidate(
        pick,
        pool_pick_share={"ARI": 0.75, "NYG": 0.25},
        pool_size=12,
    )
    assert out["status"] == SHADOW_ELIGIBLE
    assert out["production_pool_pick"] == "ARI"
    assert out["shadow_pool_pick"] == "NYG"
    assert out["expected_correct_sacrifice_if_switched"] == pytest.approx(0.06)
    assert out["ownership_gap"] == pytest.approx(0.50)
    assert out["production_pool_pick_unchanged"] is True
    assert out["automatic_promotion"] is False
    assert out["can_execute"] is False


def test_even_tossup_has_no_ownership_leverage_and_is_preserved():
    pick = _pick(
        event_id="JAX@CIN",
        selected="CIN",
        opponent="JAX",
        p=0.53,
        review_class="TOSS_UP_REVIEW",
    )
    out = evaluate_shadow_candidate(
        pick,
        pool_pick_share={"CIN": 0.50, "JAX": 0.50},
        pool_size=12,
    )
    assert out["status"] == SHADOW_PRESERVE
    assert out["shadow_pool_pick"] == "CIN"
    assert "INSUFFICIENT_POOL_OWNERSHIP_GAP" in out["reasons"]


def test_probability_sacrifice_gate_blocks_blind_contrarianism():
    pick = _pick(
        event_id="DAL@HOU",
        selected="HOU",
        opponent="DAL",
        p=0.59,
        review_class="MODEL_SIDE_FRAGILITY_REVIEW",
    )
    out = evaluate_shadow_candidate(
        pick,
        pool_pick_share={"HOU": 7 / 12, "DAL": 5 / 12},
        pool_size=12,
    )
    assert out["status"] == SHADOW_PRESERVE
    assert "EXPECTED_CORRECT_SACRIFICE_TOO_LARGE" in out["reasons"]
    assert out["production_pool_pick_unchanged"] is True


def test_shadow_board_never_changes_production_strategy_or_probability():
    picks = [
        _pick(
            event_id="ARI@NYG",
            selected="ARI",
            opponent="NYG",
            p=0.53,
            review_class="TOSS_UP_REVIEW",
        ),
        _pick(
            event_id="NE@BUF",
            selected="BUF",
            opponent="NE",
            p=0.72,
            review_class="HIGH_CONFIDENCE_HOLD",
        ),
    ]
    board = build_pool_win_equity_shadow(
        picks,
        pool_pick_shares={
            "ARI@NYG": {"ARI": 0.75, "NYG": 0.25},
            "NE@BUF": {"BUF": 1.0, "NE": 0.0},
        },
        pool_size=12,
    )
    assert board["candidate_count"] == 1
    assert board["preserve_count"] == 1
    assert board["production_strategy_changed"] is False
    assert board["production_probability_changed"] is False
    assert board["automatic_promotion"] is False
    assert board["can_execute"] is False
