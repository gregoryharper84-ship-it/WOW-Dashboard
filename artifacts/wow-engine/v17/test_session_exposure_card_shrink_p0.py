from v17.portfolio_exposure_gate import evaluate_portfolio_qualification
from v17.slip_portfolio_optimizer import canonical_thesis_identity, optimize_portfolio


def leg(row_id, player, event, stat, line, direction, lower):
    return {
        "row_id": row_id,
        "event_id": event,
        "player": player,
        "prop_type": stat,
        "period": "1IP" if stat == "1IP_PITCHES" else "FULL_GAME",
        "line": line,
        "direction": direction,
        "platform": "PrizePicks",
        "model_probability": lower + 0.04,
        "calibrated_probability": lower + 0.02,
        "calibrated_lower_bound": lower,
    }


def test_sep6_gage_and_cole_repeats_collapse_to_one_session_thesis_each():
    gage_a = leg("gage-a", "Gage Jump", "ath-kc", "1IP_PITCHES", 16.5, "MORE", 0.64)
    gage_b = leg("gage-b", "Gage Jump", "ath-kc", "1IP_PITCHES", 16.5, "MORE", 0.64)
    cole_a = leg("cole-a", "Gerrit Cole", "nyy-bal", "1IP_PITCHES", 15.5, "LESS", 0.63)
    cole_b = leg("cole-b", "Gerrit Cole", "nyy-bal", "1IP_PITCHES", 15.5, "LESS", 0.63)
    a = leg("ind-a", "Independent A", "a", "POINTS", 10.5, "MORE", 0.70)
    b = leg("ind-b", "Independent B", "b", "POINTS", 10.5, "MORE", 0.69)
    c = leg("ind-c", "Independent C", "c", "POINTS", 10.5, "MORE", 0.68)
    d = leg("ind-d", "Independent D", "d", "POINTS", 10.5, "MORE", 0.67)

    result = optimize_portfolio([
        {"card_id": "card-1", "legs": [gage_a, cole_a, a, b]},
        {"card_id": "card-2", "legs": [gage_b, cole_b, c, d]},
    ])

    assert result.exact_duplicate_counts[canonical_thesis_identity(gage_a)] == 2
    assert result.exact_duplicate_counts[canonical_thesis_identity(cole_a)] == 2
    removed = {item["removed_row_id"] for item in result.removals}
    assert {"gage-b", "cole-b"}.issubset(removed)
    assert result.probability_fields_mutated is False
    assert result.can_execute is False


def test_sep6_clark_points_and_pra_are_overlap_not_independent_diversification():
    points = leg("clark-points", "Caitlin Clark", "ind-dal", "POINTS", 17.5, "MORE", 0.62)
    pra = leg("clark-pra", "Caitlin Clark", "ind-dal", "PRA", 29.5, "MORE", 0.61)
    result = evaluate_portfolio_qualification([points, pra])
    assert result["clark-points"]["component_composite_overlap"] is True
    assert result["clark-pra"]["component_composite_overlap"] is True
    assert "COMPONENT_COMPOSITE_OVERLAP" in result["clark-points"]["blockers"]
    assert result["clark-points"]["downstream_portfolio_evaluation_allowed"] is False


def test_exact_duplicate_does_not_change_any_governed_probability_field():
    first = leg("first", "Gage Jump", "ath-kc", "1IP_PITCHES", 16.5, "MORE", 0.64)
    second = leg("second", "Gage Jump", "ath-kc", "1IP_PITCHES", 16.5, "MORE", 0.64)
    snapshot = (
        first["model_probability"],
        first["calibrated_probability"],
        first["calibrated_lower_bound"],
    )
    result = optimize_portfolio([
        {"card_id": "one", "legs": [first, leg("a", "A", "a", "POINTS", 1.5, "MORE", 0.70)]},
        {"card_id": "two", "legs": [second, leg("b", "B", "b", "POINTS", 1.5, "MORE", 0.70)]},
    ])
    surviving = result.cards[0]["legs"][0]
    assert (
        surviving["model_probability"],
        surviving["calibrated_probability"],
        surviving["calibrated_lower_bound"],
    ) == snapshot
    assert result.probability_fields_mutated is False


def test_unresolved_same_event_flex_is_held_until_joint_treatment_exists():
    a = leg("a", "Player A", "same-game", "POINTS", 10.5, "MORE", 0.67)
    b = leg("b", "Player B", "same-game", "REBOUNDS", 6.5, "MORE", 0.66)
    unresolved = optimize_portfolio([{"card_id": "flex", "structure": "FLEX", "legs": [a, b]}])
    assert unresolved.cards[0]["portfolio_governance"]["portfolio_qualified"] is False
    assert "PP_CORRELATION_UNRESOLVED" in unresolved.cards[0]["portfolio_governance"]["blockers"]

    resolved = optimize_portfolio([
        {"card_id": "flex", "structure": "FLEX", "joint_probability_status": "PASS", "legs": [a, b]}
    ])
    assert resolved.cards[0]["portfolio_governance"]["portfolio_qualified"] is True


def test_no_filler_when_duplicate_removal_drops_below_platform_minimum():
    first = leg("first", "Gage Jump", "ath-kc", "1IP_PITCHES", 16.5, "MORE", 0.64)
    second = leg("second", "Gage Jump", "ath-kc", "1IP_PITCHES", 16.5, "MORE", 0.64)
    weak = leg("weak", "Weak Filler", "weak", "POINTS", 10.5, "MORE", 0.50)
    result = optimize_portfolio([
        {"card_id": "one", "legs": [first, leg("a", "A", "a", "POINTS", 1.5, "MORE", 0.70)]},
        {"card_id": "two", "legs": [second, leg("b", "B", "b", "POINTS", 1.5, "MORE", 0.70)]},
    ], alternatives=[weak])
    assert result.replacements == []
    assert len(result.cards[1]["legs"]) == 1
    assert "INSUFFICIENT_LEGS_AFTER_MANDATORY_SHRINK" in result.cards[1]["portfolio_governance"]["blockers"]
    assert result.cards[1]["portfolio_governance"]["portfolio_qualified"] is False
