from datetime import datetime, timedelta, timezone

from v17.game_winner_cash_single_gate import evaluate_game_winner_cash_single


NOW = datetime(2026, 9, 6, 8, 30, tzinfo=timezone.utc)


def _probability(**overrides):
    payload = {
        "candidate_id": "mlb:test-away@test-home",
        "raw_model_probability": 0.70,
        "calibrated_probability": 0.69,
        "calibrated_lower_bound": 0.67,
        "calibrated_upper_bound": 0.72,
        "calibration_health_status": "PASS",
        "market_prior_weight": 0.20,
        "failure_path_status": "PASS",
        "probability_publishable": True,
        "rank_eligible": True,
        "terminal_label": "FINAL_APPROVED",
    }
    payload.update(overrides)
    return payload


def _economics(**overrides):
    recent = (NOW - timedelta(minutes=2)).isoformat()
    payload = {
        "platform": "PRIZEPICKS",
        "market_family": "GAME_WINNER",
        "platform_selection": "Test Home",
        "platform_gross_multiplier": 1.60,
        "platform_capture_timestamp": recent,
        "exact_two_way_market_verified": True,
        "sportsbook_source_count": 3,
        "sportsbook_timestamp": recent,
        "market_no_vig_probability": 0.64,
        "active_safety_buffer": 0.025,
        "market_model_disagreement_status": "PASS_EXPLAINED",
    }
    payload.update(overrides)
    return payload


def _finalized(**overrides):
    payload = {
        "final_refresh_status": "PASS",
        "immutable_prediction_write_status": "WRITTEN",
    }
    payload.update(overrides)
    return payload


def test_positive_lower_bound_edge_can_promote_after_refresh_and_write():
    result = evaluate_game_winner_cash_single(
        _probability(), _economics(), finalization_context=_finalized(), as_of=NOW
    )
    assert result.platform_break_even_probability == 0.625
    assert round(result.lower_bound_edge_after_buffer, 3) == 0.020
    assert result.cash_single_eligible is True
    assert result.economic_gate_status == "PASS"
    assert result.finalization_gate_status == "PASS"
    assert result.terminal_ceiling == "MARKET_VERIFIED_HOLD"
    assert result.can_execute is False


def test_high_probability_does_not_promote_when_lower_bound_misses_price_buffer():
    # Still rank-eligible in the probability lane, but unsafe for the paid single.
    result = evaluate_game_winner_cash_single(
        _probability(calibrated_probability=0.68, calibrated_lower_bound=0.64),
        _economics(),
        finalization_context=_finalized(),
        as_of=NOW,
    )
    assert result.probability_rank_eligible is True
    assert result.sporting_probability_preserved is True
    assert result.cash_single_eligible is False
    assert "LOWER_BOUND_EDGE_DOES_NOT_CLEAR_SAFETY_BUFFER" in result.blockers
    assert result.terminal_ceiling == "REJECT_NO_EDGE"


def test_probability_only_leaderboard_survives_cash_rejection():
    result = evaluate_game_winner_cash_single(
        _probability(calibrated_probability=0.66, calibrated_lower_bound=0.61),
        _economics(platform_gross_multiplier=1.50),
        finalization_context=_finalized(),
        as_of=NOW,
    )
    assert result.probability_rank_eligible is True
    assert result.cash_single_eligible is False
    assert result.sporting_probability_preserved is True


def test_exact_two_way_market_is_mandatory():
    result = evaluate_game_winner_cash_single(
        _probability(),
        _economics(exact_two_way_market_verified=False),
        finalization_context=_finalized(),
        as_of=NOW,
    )
    assert result.cash_single_eligible is False
    assert "EXACT_TWO_WAY_MARKET_NOT_VERIFIED" in result.blockers


def test_stale_platform_or_market_price_blocks_promotion():
    stale = (NOW - timedelta(minutes=11)).isoformat()
    result = evaluate_game_winner_cash_single(
        _probability(),
        _economics(platform_capture_timestamp=stale, sportsbook_timestamp=stale),
        finalization_context=_finalized(),
        as_of=NOW,
    )
    assert result.cash_single_eligible is False
    assert "PRIZEPICKS_PRICE_STALE" in result.blockers
    assert "TWO_WAY_MARKET_STALE" in result.blockers


def test_market_dependent_model_is_not_cash_promoted():
    result = evaluate_game_winner_cash_single(
        _probability(market_prior_weight=0.60),
        _economics(),
        finalization_context=_finalized(),
        as_of=NOW,
    )
    assert result.cash_single_eligible is False
    assert "MARKET_DEPENDENT_MODEL" in result.blockers


def test_model_only_disagreement_requires_explicit_resolution():
    # PP break-even = 62.5%; external fair market is only 60%, while the model
    # lower bound claims enough edge.  The paid lane must not silently trust itself.
    result = evaluate_game_winner_cash_single(
        _probability(calibrated_probability=0.70, calibrated_lower_bound=0.68),
        _economics(market_no_vig_probability=0.60, market_model_disagreement_status="UNRESOLVED"),
        finalization_context=_finalized(),
        as_of=NOW,
    )
    assert result.probability_rank_eligible is True
    assert result.cash_single_eligible is False
    assert "MODEL_ONLY_DISAGREEMENT_UNRESOLVED" in result.blockers
    assert result.terminal_ceiling == "MARKET_VERIFIED_HOLD"


def test_failed_calibration_health_blocks_cash_promotion():
    result = evaluate_game_winner_cash_single(
        _probability(calibration_health_status="FAIL"),
        _economics(),
        finalization_context=_finalized(),
        as_of=NOW,
    )
    assert result.cash_single_eligible is False
    assert "GAME_WINNER_CALIBRATION_HEALTH_NOT_PASS" in result.blockers


def test_final_refresh_is_binding():
    result = evaluate_game_winner_cash_single(
        _probability(),
        _economics(),
        finalization_context=_finalized(final_refresh_status="STALE_PRICE"),
        as_of=NOW,
    )
    assert result.economic_gate_status == "PASS"
    assert result.cash_single_eligible is False
    assert "FINAL_REFRESH_NOT_PASS" in result.blockers


def test_immutable_pregame_write_is_binding():
    result = evaluate_game_winner_cash_single(
        _probability(),
        _economics(),
        finalization_context=_finalized(immutable_prediction_write_status="FAILED"),
        as_of=NOW,
    )
    assert result.economic_gate_status == "PASS"
    assert result.cash_single_eligible is False
    assert "IMMUTABLE_PREGAME_WRITE_NOT_PASS" in result.blockers


def test_realized_result_is_not_an_input_to_pregame_promotion():
    result = evaluate_game_winner_cash_single(
        _probability(official_result="LOSS"),
        _economics(),
        finalization_context=_finalized(),
        as_of=NOW,
    )
    assert result.cash_single_eligible is True
    assert result.can_execute is False
