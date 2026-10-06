from __future__ import annotations

from datetime import datetime, timedelta, timezone

import pytest

from v17.experiments.mlb_k_feature_local_bound import (
    FeatureLocalPolicy,
    MLBKFeatureLocalError,
    extract_feature_vector,
    feature_local_bound,
    research_receipt,
)


BASE = datetime(2026, 8, 1, 12, tzinfo=timezone.utc)


def _game_log(offset: int = 0):
    return [5 + ((i + offset) % 4) for i in range(10)]


def _box_log(offset: int = 0):
    outs = [18, 18, 21, 17, 19, 18, 20, 18, 21, 16]
    return [{"outs": value + (offset % 2)} for value in outs]


def _row(
    index: int,
    *,
    direction: str = "MORE",
    line: float = 5.5,
    point: float = 0.60,
    outcome=1,
    family: str = "MLB_PITCHER_SO_FAILURE_PATH_NB_V1",
    market_prior_weight: float = 0.0,
    opponent_k_rate: float | None = None,
    event_id: str | None = None,
    prediction_timestamp: str | None = None,
    outcome_available_at: str | None = None,
):
    pred = BASE + timedelta(days=index)
    settled = pred + timedelta(hours=10)
    opponent_context = (
        None
        if opponent_k_rate is None
        else {"k_rate_per_pa": opponent_k_rate}
    )
    return {
        "event_id": event_id or f"MLB:{1000 + index}",
        "lane_key": "MLB_PITCHER_STRIKEOUTS_MORE",
        "direction": direction,
        "model_family": family,
        "calibrated_probability": point,
        "market_prior_weight": market_prior_weight,
        "market_probability_substitution_used": False,
        "prediction_timestamp": prediction_timestamp or pred.isoformat(),
        "outcome_available_at": outcome_available_at or settled.isoformat(),
        "outcome": outcome,
        "void": False,
        "line": line,
        "game_log": _game_log(index % 3),
        "box_score_log": _box_log(index % 2),
        "opponent_context": opponent_context,
    }


def test_extract_feature_vector_matches_model_internal_history_contract():
    vector = extract_feature_vector(
        game_log=[5] * 10,
        box_score_log=[{"outs": 15}] * 5 + [{"outs": 18}] * 5,
        line=6.5,
        opponent_context={"k_rate_per_pa": 0.24},
    )

    assert vector.prior_so_per_out == pytest.approx(50 / 165)
    assert vector.prior_shortened_rate == pytest.approx(0.0)
    assert vector.line == pytest.approx(6.5)
    assert vector.opponent_k_rate_per_pa == pytest.approx(0.24)
    assert vector.prior_start_n == 10


def test_feature_history_must_be_aligned_and_long_enough():
    with pytest.raises(MLBKFeatureLocalError, match="MISALIGNED"):
        extract_feature_vector(
            game_log=[5] * 10,
            box_score_log=[{"outs": 18}] * 9,
            line=5.5,
        )

    with pytest.raises(MLBKFeatureLocalError, match="TOO_SHORT"):
        extract_feature_vector(
            game_log=[5] * 9,
            box_score_log=[{"outs": 18}] * 9,
            line=5.5,
        )


def test_feature_local_bound_is_time_locked_market_independent_and_research_only():
    history = [_row(i, outcome=1 if i < 26 else 0) for i in range(40)]
    # These rows cannot enter the neighborhood.
    history.append(_row(41, market_prior_weight=0.15, outcome=1))
    history.append(
        _row(
            42,
            outcome=1,
            prediction_timestamp="2026-10-10T12:00:00+00:00",
            outcome_available_at="2026-10-10T22:00:00+00:00",
        )
    )

    candidate = _row(
        100,
        point=0.62,
        outcome=1,
        prediction_timestamp="2026-10-01T12:00:00+00:00",
        outcome_available_at="2026-10-01T22:00:00+00:00",
    )
    bound = feature_local_bound(
        candidate=candidate,
        historical_rows=history,
        candidate_as_of="2026-10-01T12:00:00+00:00",
        policy=FeatureLocalPolicy(
            min_effective_n=30,
            max_neighbors=40,
            max_line_distance=0.0,
            max_standardized_distance=3.0,
            bootstrap_draws=2000,
            min_blocks=10,
        ),
        random_seed=7,
    )
    receipt = research_receipt(bound)

    assert bound.sample_n >= 30
    assert bound.block_n >= 10
    assert 0.0 <= bound.composite_lower_bound <= bound.point_probability
    assert bound.composite_lower_bound <= bound.local_wilson_lower
    assert bound.composite_lower_bound <= bound.block_bootstrap_q10_lower

    assert receipt["status"] == "EXPERIMENT_CREATED"
    assert receipt["probability_publishable"] is False
    assert receipt["promotion_authorized"] is False
    assert receipt["rank_eligible"] is False
    assert receipt["can_execute"] is False
    assert receipt["market_probability_substitution_allowed"] is False


def test_exact_line_policy_excludes_other_lines():
    history = [_row(i, line=5.5, outcome=1 if i < 24 else 0) for i in range(35)]
    history.extend(_row(100 + i, line=6.5, outcome=1) for i in range(20))
    candidate = _row(
        200,
        line=5.5,
        point=0.61,
        prediction_timestamp="2026-10-01T12:00:00+00:00",
        outcome_available_at="2026-10-01T22:00:00+00:00",
    )

    bound = feature_local_bound(
        candidate=candidate,
        historical_rows=history,
        candidate_as_of="2026-10-01T12:00:00+00:00",
        policy=FeatureLocalPolicy(
            min_effective_n=30,
            max_neighbors=50,
            max_line_distance=0.0,
            max_standardized_distance=4.0,
            bootstrap_draws=2000,
            min_blocks=10,
        ),
    )

    assert bound.sample_n == 35
    assert bound.max_line_distance_used == pytest.approx(0.0)


def test_opponent_context_policy_is_explicit_and_never_imputed():
    history = [
        _row(i, opponent_k_rate=0.21 + (i % 4) * 0.005)
        for i in range(35)
    ]
    history.extend(_row(100 + i, opponent_k_rate=None) for i in range(20))

    candidate_missing = _row(
        200,
        opponent_k_rate=None,
        prediction_timestamp="2026-10-01T12:00:00+00:00",
        outcome_available_at="2026-10-01T22:00:00+00:00",
    )
    with pytest.raises(
        MLBKFeatureLocalError,
        match="CANDIDATE_OPPONENT_CONTEXT_REQUIRED_BY_POLICY",
    ):
        feature_local_bound(
            candidate=candidate_missing,
            historical_rows=history,
            candidate_as_of="2026-10-01T12:00:00+00:00",
            policy=FeatureLocalPolicy(
                use_opponent_k_rate=True,
                min_effective_n=30,
                max_neighbors=35,
                max_standardized_distance=5.0,
                bootstrap_draws=2000,
            ),
        )

    candidate = _row(
        201,
        opponent_k_rate=0.22,
        prediction_timestamp="2026-10-01T12:00:00+00:00",
        outcome_available_at="2026-10-01T22:00:00+00:00",
    )
    bound = feature_local_bound(
        candidate=candidate,
        historical_rows=history,
        candidate_as_of="2026-10-01T12:00:00+00:00",
        policy=FeatureLocalPolicy(
            use_opponent_k_rate=True,
            min_effective_n=30,
            max_neighbors=35,
            max_standardized_distance=5.0,
            bootstrap_draws=2000,
        ),
    )

    assert bound.sample_n == 35
    assert bound.included_opponent_context_n == 35
    assert "opponent_k_rate_per_pa" in bound.feature_scales


def test_push_is_excluded_to_mirror_current_governed_binary_calibration_contract():
    history = []
    for i in range(40):
        outcome = "PUSH" if i < 10 else ("WIN" if i < 30 else "LOSS")
        history.append(_row(i, outcome=outcome))
    candidate = _row(
        100,
        point=0.60,
        prediction_timestamp="2026-10-01T12:00:00+00:00",
        outcome_available_at="2026-10-01T22:00:00+00:00",
    )

    bound = feature_local_bound(
        candidate=candidate,
        historical_rows=history,
        candidate_as_of="2026-10-01T12:00:00+00:00",
        policy=FeatureLocalPolicy(
            min_effective_n=30,
            max_neighbors=40,
            max_standardized_distance=5.0,
            bootstrap_draws=2000,
        ),
    )
    receipt = research_receipt(bound)

    # The governed Phase-B candidate/health path excludes pushes. Issue #1425
    # separately tests alternative probability targets; this challenger does
    # not silently redefine them.
    assert bound.excluded_push_n == 10
    assert bound.sample_n == 30
    assert bound.local_wilson_lower > 0.50
    assert receipt["binary_push_semantics"] == "CURRENT_GOVERNED_CONTRACT_PUSH_EXCLUDED"
    assert receipt["probability_target_experiment_issue"] == 1425


def test_bootstrap_and_block_support_fail_closed():
    history = [
        _row(i, event_id=f"MLB:{i % 3}")
        for i in range(40)
    ]
    candidate = _row(
        100,
        prediction_timestamp="2026-10-01T12:00:00+00:00",
        outcome_available_at="2026-10-01T22:00:00+00:00",
    )

    with pytest.raises(MLBKFeatureLocalError, match="BLOCK_SUPPORT_INSUFFICIENT"):
        feature_local_bound(
            candidate=candidate,
            historical_rows=history,
            candidate_as_of="2026-10-01T12:00:00+00:00",
            policy=FeatureLocalPolicy(
                min_effective_n=30,
                max_neighbors=40,
                max_standardized_distance=5.0,
                bootstrap_draws=2000,
                min_blocks=10,
            ),
        )

    with pytest.raises(MLBKFeatureLocalError, match="BOOTSTRAP_DRAWS_LT_2000"):
        feature_local_bound(
            candidate=candidate,
            historical_rows=history,
            candidate_as_of="2026-10-01T12:00:00+00:00",
            policy=FeatureLocalPolicy(
                min_effective_n=30,
                max_neighbors=40,
                max_standardized_distance=5.0,
                bootstrap_draws=1999,
                min_blocks=2,
            ),
        )


def test_candidate_market_prior_is_hard_rejected():
    history = [_row(i) for i in range(40)]
    candidate = _row(
        100,
        market_prior_weight=0.01,
        prediction_timestamp="2026-10-01T12:00:00+00:00",
        outcome_available_at="2026-10-01T22:00:00+00:00",
    )
    with pytest.raises(MLBKFeatureLocalError, match="MARKET_PROBABILITY_SUBSTITUTION"):
        feature_local_bound(
            candidate=candidate,
            historical_rows=history,
            candidate_as_of="2026-10-01T12:00:00+00:00",
        )
