from __future__ import annotations

from copy import deepcopy

import pytest

from v17.nfl_pickem_full_pool_equity import PoolEquityInputError
from v17.nfl_pickem_projected_pool_equity import evaluate_projected_pool_shadow


LOCK = "2026-10-11T12:00:00Z"


def _inputs(p: float = .51):
    picks = [{
        "status": "PICKEM_READY",
        "source_terminal_label": "MODEL_QUALIFIED_HOLD",
        "official_event_id": "A@H",
        "home_team": "H",
        "away_team": "A",
        "pool_pick": "H",
        "home_probability": p,
        "away_probability": 1 - p,
        "selected_probability": p,
        "calibrated_selection_lower_bound": p - .05,
        "calibrated_selection_upper_bound": p + .05,
        "source_prediction_id": "prediction-1",
        "source_snapshot_id": "snapshot-1",
        "immutable_model_timestamp": "2026-10-10T09:00:00Z",
        "scheduled_start_utc": "2026-10-11T19:00:00Z",
        "controlling_specialist": "wow.nfl-game-win-probability-expert",
        "can_execute": False,
    }]
    forecast = {
        "audience": "OPPONENT_OWNERSHIP_RESEARCH",
        "source_id": "observed-pregame-survey",
        "snapshot_id": "forecast-before-lock",
        "as_of": "2026-10-10T10:00:00Z",
        "available_to_user_at": "2026-10-10T11:00:00Z",
        "home_ownership": {"A@H": 1.0},
    }
    return picks, forecast


def test_two_member_pool_preserves_baseline_with_one_unanimous_opponent():
    picks, forecast = _inputs()
    frozen = deepcopy(picks)
    a = evaluate_projected_pool_shadow(picks, forecast, lock_at=LOCK, pool_size=2, seed=7)
    b = evaluate_projected_pool_shadow(picks, forecast, lock_at=LOCK, pool_size=2, seed=7)
    assert a == b
    assert a["changed_picks"] == []
    assert a["ownership_is_assumption"] is True
    assert a["simulated_opponent_cards_are_actual"] is False
    assert a["production_pick_mutation_allowed"] is False
    assert a["certified_first_place_equity"] is False
    assert a["can_execute"] is False
    assert picks == frozen


def test_multiple_unanimous_opponents_make_small_deviation_worth_shadow_review():
    picks, forecast = _inputs()
    a = evaluate_projected_pool_shadow(picks, forecast, lock_at=LOCK, pool_size=12, seed=2)
    assert a["baseline"]["equal_split_first_share_proxy"] == pytest.approx(1/12)
    assert a["research_challenger"]["equal_split_first_share_proxy"] > a["baseline"]["equal_split_first_share_proxy"]
    assert a["changed_picks"] == [{
        "official_event_id": "A@H",
        "baseline_side": "H",
        "research_challenger_side": "A",
    }]
    assert a["expected_correct_sacrifice"] == pytest.approx(.02)


def test_high_confidence_side_cannot_be_flipped_to_create_differentiation():
    picks, forecast = _inputs(.9)
    a = evaluate_projected_pool_shadow(picks, forecast, lock_at=LOCK, pool_size=12)
    assert a["changed_picks"] == []


def test_missing_snapshot_and_post_lock_availability_block_without_dummy_opponents():
    picks, forecast = _inputs()
    with pytest.raises(PoolEquityInputError, match="PICKEM_OWNERSHIP_FORECAST_REQUIRED"):
        evaluate_projected_pool_shadow(picks, None, lock_at=LOCK, pool_size=12)
    forecast["available_to_user_at"] = "2026-10-11T12:00:01Z"
    with pytest.raises(PoolEquityInputError, match="PICKEM_OWNERSHIP_POST_LOCK_FORECAST"):
        evaluate_projected_pool_shadow(picks, forecast, lock_at=LOCK, pool_size=12)


@pytest.mark.parametrize("field,value,code", [
    ("pool_size", 1, "PICKEM_PROJECTED_POOL_SIZE_INVALID"),
    ("pool_size", True, "PICKEM_PROJECTED_POOL_SIZE_INVALID"),
    ("draws", 10, "PICKEM_PROJECTED_DRAW_COUNT_INVALID"),
    ("draws", 30000, "PICKEM_PROJECTED_DRAW_COUNT_INVALID"),
    ("seed", -1, "PICKEM_PROJECTED_SEED_INVALID"),
    ("max_flips", 17, "PICKEM_EQUITY_MAX_FLIPS_INVALID"),
    ("max_expected_correct_sacrifice", -1.0, "PICKEM_EQUITY_SACRIFICE_LIMIT_INVALID"),
])
def test_bad_research_parameters_fail_closed(field, value, code):
    picks, forecast = _inputs()
    kwargs = {"lock_at": LOCK, "pool_size": 12, field: value}
    with pytest.raises(PoolEquityInputError, match=code):
        evaluate_projected_pool_shadow(picks, forecast, **kwargs)


def test_invalid_source_and_model_status_fail_before_projecting_opponent_choices():
    picks, forecast = _inputs()
    picks[0]["controlling_specialist"] = "public-odds"
    with pytest.raises(PoolEquityInputError, match="PICKEM_EQUITY_SPECIALIST_MISMATCH"):
        evaluate_projected_pool_shadow(picks, forecast, lock_at=LOCK, pool_size=12)
    picks, forecast = _inputs()
    picks[0]["immutable_model_timestamp"] = "2026-10-11T12:00:01Z"
    with pytest.raises(PoolEquityInputError, match="PICKEM_EQUITY_STALE_OR_POST_LOCK_MODEL"):
        evaluate_projected_pool_shadow(picks, forecast, lock_at=LOCK, pool_size=12)


def test_forecast_event_identity_must_match_fitted_model():
    picks, forecast = _inputs()
    forecast["home_ownership"] = {"FAKE": 0.7}
    with pytest.raises(PoolEquityInputError, match="PICKEM_OWNERSHIP_EVENT_SET_MISMATCH"):
        evaluate_projected_pool_shadow(picks, forecast, lock_at=LOCK, pool_size=12)


def test_zero_flip_budget_keeps_probability_intact():
    picks, forecast = _inputs()
    r = evaluate_projected_pool_shadow(picks, forecast, lock_at=LOCK, pool_size=12, max_flips=0)
    assert r["baseline"] == r["research_challenger"]
    assert r["changed_picks"] == []


def test_large_pool_draws_refuse_resource_exhaustion():
    picks, forecast = _inputs()
    with pytest.raises(PoolEquityInputError, match="PICKEM_PROJECTED_RESOURCE_BUDGET_EXCEEDED"):
        evaluate_projected_pool_shadow(
            picks, forecast, lock_at=LOCK, pool_size=1000, draws=20000,
        )
