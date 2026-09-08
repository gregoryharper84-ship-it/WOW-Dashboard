from v17.postmortem_learning_ledger import build_postmortem_outcome
from v17.slip_portfolio_optimizer import (
    canonical_thesis_identity,
    optimize_portfolio,
    thesis_identity,
)


def _leg(
    row_id: str,
    *,
    player: str,
    lower: float,
    game: str,
    stat: str = "STRIKEOUTS",
    direction: str = "MORE",
    line: float = 2.5,
    period: str = "FULL_GAME",
    **extra,
):
    row = {
        "row_id": row_id,
        "event_id": game,
        "player": player,
        "prop_type": stat,
        "period": period,
        "direction": direction,
        "line": line,
        "platform": "PrizePicks",
        "model_probability": min(0.999, lower + 0.04),
        "calibrated_probability": min(0.999, lower + 0.02),
        "calibrated_lower_bound": lower,
    }
    row.update(extra)
    return row


def test_adjacent_lines_are_one_exposure_family_but_not_one_exact_prediction():
    first = _leg("zve-32", player="Alexander Zverev", lower=0.68, game="zve-dar", stat="TOTAL_GAMES", direction="LESS", line=32.5)
    second = _leg("zve-33", player="Alexander Zverev", lower=0.69, game="zve-dar", stat="TOTAL_GAMES", direction="LESS", line=33.5)
    assert canonical_thesis_identity(first) != canonical_thesis_identity(second)
    assert thesis_identity(first) == thesis_identity(second)

    result = optimize_portfolio([
        {"card_id": "a", "structure": "FLEX", "legs": [first, _leg("x", player="X", lower=0.70, game="x")]},
        {"card_id": "b", "structure": "FLEX", "legs": [second, _leg("y", player="Y", lower=0.70, game="y")]},
    ])
    assert any(item["removed_row_id"] == "zve-33" for item in result.removals)
    assert result.probability_fields_mutated is False


def test_power_critical_leg_is_replaced_by_stronger_independent_candidate():
    weak = _leg("weak", player="Weak Hinge", lower=0.55, game="w")
    strong = _leg("strong", player="Strong Hinge", lower=0.85, game="s")
    replacement = _leg("replacement", player="Independent", lower=0.80, game="r")

    result = optimize_portfolio(
        [{"card_id": "power", "structure": "POWER", "legs": [weak, strong]}],
        alternatives=[replacement],
        max_power_critical_failure_share=0.60,
    )
    rows = [leg["row_id"] for leg in result.cards[0]["legs"]]
    assert rows == ["replacement", "strong"]
    assert result.critical_leg_actions[0]["action"] == "REPLACE"
    assert result.probability_fields_mutated is False
    assert result.cards[0]["portfolio_governance"]["portfolio_qualified"] is True


def test_power_critical_leg_shrinks_instead_of_adding_filler():
    weak = _leg("weak", player="Weak Hinge", lower=0.55, game="w")
    strong = _leg("strong", player="Strong Hinge", lower=0.85, game="s")
    filler = _leg("filler", player="Filler", lower=0.50, game="f")

    result = optimize_portfolio(
        [{"card_id": "power", "structure": "POWER", "legs": [weak, strong]}],
        alternatives=[filler],
        max_power_critical_failure_share=0.60,
    )
    assert [leg["row_id"] for leg in result.cards[0]["legs"]] == ["strong"]
    assert result.critical_leg_actions[0]["action"] == "SHRINK"
    assert "INSUFFICIENT_LEGS_AFTER_MANDATORY_SHRINK" in result.cards[0]["portfolio_governance"]["blockers"]
    assert result.probability_fields_mutated is False


def test_zero_event_power_hinge_requires_specialist_event_distribution():
    zero_event = _leg(
        "zverev-tb",
        player="Alexander Zverev",
        lower=0.70,
        game="zve-dar",
        stat="TOTAL_TIE_BREAKS",
        direction="LESS",
        line=0.5,
        discrete_zero_event_required=True,
    )
    other = _leg("other", player="Other", lower=0.70, game="other")
    result = optimize_portfolio([
        {"card_id": "power", "structure": "POWER", "legs": [zero_event, other]}
    ])
    assert [leg["row_id"] for leg in result.cards[0]["legs"]] == ["other"]
    assert any("POWER_ZERO_EVENT_DISTRIBUTION_UNRESOLVED" in item["reason"] for item in result.removals)
    assert result.probability_fields_mutated is False


def test_zero_event_power_hinge_survives_when_distribution_and_tail_path_are_supplied():
    zero_event = _leg(
        "zverev-tb",
        player="Alexander Zverev",
        lower=0.70,
        game="zve-dar",
        stat="TOTAL_TIE_BREAKS",
        direction="LESS",
        line=0.5,
        discrete_zero_event_required=True,
        p_zero_events=0.72,
        p_one_plus_events=0.28,
        zero_event_tail_drivers=("set reaches 6-6", "serve-dominant set path"),
    )
    other = _leg("other", player="Other", lower=0.70, game="other")
    result = optimize_portfolio([
        {"card_id": "power", "structure": "POWER", "legs": [zero_event, other]}
    ])
    assert len(result.cards[0]["legs"]) == 2
    audit = result.cards[0]["legs"][0]["portfolio_governance"]["zero_event_audit"]
    assert audit["status"] == "PASS"
    assert result.cards[0]["portfolio_governance"]["portfolio_qualified"] is True


def test_power_admission_floor_is_separate_from_model_qualification():
    marginal = _leg("marginal", player="Marginal", lower=0.61, game="m")
    strong = _leg("strong", player="Strong", lower=0.78, game="s")
    original_probability = marginal["calibrated_lower_bound"]
    result = optimize_portfolio([
        {
            "card_id": "power",
            "structure": "POWER",
            "power_admission_lower_bound_floor": 0.65,
            "legs": [marginal, strong],
        }
    ])
    assert marginal["calibrated_lower_bound"] == original_probability
    assert any(item["removed_row_id"] == "marginal" for item in result.removals)
    assert result.probability_fields_mutated is False


def test_close_loss_remains_official_loss_while_near_miss_metadata_is_recorded():
    prediction = {
        "prediction_id": "cease-hits-20260907",
        "exact_line": 4.5,
        "direction": "MORE",
    }
    outcome = build_postmortem_outcome(
        prediction,
        official_result="LOSS",
        settlement_source="official_box_score",
        settlement_timestamp="2026-09-07T23:00:00-05:00",
        actual_value=4,
        close_miss_tolerance=0.5,
        duplicate_thesis_count=1,
        cards_killed=1,
        critical_leg_rank=1,
    )
    assert outcome.official_result == "LOSS"
    assert outcome.signed_distance_to_threshold == -0.5
    assert outcome.close_miss_flag is True
    assert outcome.cards_killed == 1


def test_duplicate_near_miss_can_attribute_multiple_cards_without_rewriting_result():
    prediction = {
        "prediction_id": "zverev-tiebreak-20260907",
        "exact_line": 0.5,
        "direction": "LESS",
    }
    outcome = build_postmortem_outcome(
        prediction,
        official_result="LOSS",
        settlement_source="official_match_stats",
        settlement_timestamp="2026-09-07T22:00:00-05:00",
        actual_value=1,
        close_miss_tolerance=0.5,
        duplicate_thesis_count=2,
        cards_killed=2,
        critical_leg_rank=1,
    )
    assert outcome.official_result == "LOSS"
    assert outcome.close_miss_flag is True
    assert outcome.duplicate_thesis_count == 2
    assert outcome.cards_killed == 2


def test_moneyline_loss_has_no_fake_scalar_near_miss_distance():
    prediction = {
        "prediction_id": "fsu-ml-20260907",
        "side": "FSU",
    }
    outcome = build_postmortem_outcome(
        prediction,
        official_result="LOSS",
        settlement_source="official_final_score",
        settlement_timestamp="2026-09-07T23:30:00-05:00",
        actual_value=None,
        duplicate_thesis_count=1,
        cards_killed=1,
    )
    assert outcome.official_result == "LOSS"
    assert outcome.signed_distance_to_threshold is None
    assert outcome.close_miss_flag is None
