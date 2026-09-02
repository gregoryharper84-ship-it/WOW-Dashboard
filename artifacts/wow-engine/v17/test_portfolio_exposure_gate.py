from v17.portfolio_exposure_gate import (
    evaluate_dependency_correlation_structure,
    evaluate_portfolio_qualification,
    evaluate_session_directional_duplicate_thesis_exposure,
)


def _leg(row_id, *, event_id="evt-1", direction="MORE"):
    return {"row_id": row_id, "event_id": event_id, "direction": direction}


def test_unrelated_single_leg_has_no_dependency_or_exposure():
    legs = [_leg("a", event_id="evt-1")]
    dep = evaluate_dependency_correlation_structure(legs)
    exp = evaluate_session_directional_duplicate_thesis_exposure(legs)
    assert dep["a"]["same_event_dependent"] is False
    assert dep["a"]["co_dependent_row_ids"] == []
    assert exp["a"]["directional_exposure"] is False
    assert exp["a"]["session_event_leg_count"] == 1

    result = evaluate_portfolio_qualification(legs)
    assert result["a"]["downstream_portfolio_evaluation_allowed"] is True
    assert result["a"]["blockers"] == []
    assert result["a"]["portfolio_qualification"] == "QUALIFIED"


def test_unrelated_legs_on_different_events_are_unaffected_by_each_other():
    legs = [_leg("a", event_id="evt-1"), _leg("b", event_id="evt-2")]
    result = evaluate_portfolio_qualification(legs)
    assert result["a"]["downstream_portfolio_evaluation_allowed"] is True
    assert result["b"]["downstream_portfolio_evaluation_allowed"] is True


def test_same_event_correlated_legs_are_not_treated_as_independent():
    # Different directions -> not a directional-exposure case, but they are
    # still structurally dependent (same underlying event).
    legs = [_leg("a", event_id="evt-1", direction="MORE"), _leg("b", event_id="evt-1", direction="LESS")]
    dep = evaluate_dependency_correlation_structure(legs)
    assert dep["a"]["same_event_dependent"] is True
    assert dep["b"]["same_event_dependent"] is True
    assert dep["a"]["co_dependent_row_ids"] == ["b"]
    assert dep["b"]["co_dependent_row_ids"] == ["a"]

    exp = evaluate_session_directional_duplicate_thesis_exposure(legs)
    assert exp["a"]["directional_exposure"] is False  # separate dimension, correctly not triggered

    result = evaluate_portfolio_qualification(legs)
    assert result["a"]["downstream_portfolio_evaluation_allowed"] is False
    assert result["b"]["downstream_portfolio_evaluation_allowed"] is False
    assert "DEPENDENCE_UNQUANTIFIED_SAME_EVENT" in result["a"]["blockers"]
    assert "SESSION_DIRECTIONAL_EXPOSURE" not in result["a"]["blockers"]


def test_directional_and_session_exposure_are_computed_by_the_separate_stage():
    legs = [_leg("a", event_id="evt-1", direction="MORE"), _leg("b", event_id="evt-1", direction="MORE")]
    exp = evaluate_session_directional_duplicate_thesis_exposure(legs)
    assert exp["a"]["directional_exposure"] is True
    assert exp["b"]["directional_exposure"] is True
    assert exp["a"]["session_event_leg_count"] == 2

    result = evaluate_portfolio_qualification(legs)
    assert "SESSION_DIRECTIONAL_EXPOSURE" in result["a"]["blockers"]
    assert "DEPENDENCE_UNQUANTIFIED_SAME_EVENT" in result["a"]["blockers"]  # both stages independently fire
    assert result["a"]["downstream_portfolio_evaluation_allowed"] is False


def test_duplicate_thesis_signal_is_folded_into_the_same_qualification_decision():
    legs = [_leg("a", event_id="evt-3", direction="MORE"), _leg("b", event_id="evt-4", direction="LESS")]
    result = evaluate_portfolio_qualification(legs, duplicate_thesis_flagged={"a": True})
    assert result["a"]["duplicate_thesis_flagged"] is True
    assert "DUPLICATE_THESIS_COMMON_HINGE" in result["a"]["blockers"]
    assert result["a"]["downstream_portfolio_evaluation_allowed"] is False
    # Unrelated leg b is untouched.
    assert result["b"]["downstream_portfolio_evaluation_allowed"] is True
    assert result["b"]["blockers"] == []


def test_no_probability_or_terminal_ceiling_fields_are_ever_produced():
    legs = [_leg("a"), _leg("b")]
    result = evaluate_portfolio_qualification(legs)
    for row in result.values():
        assert "calibrated_probability" not in row
        assert "terminal_label" not in row
        assert "terminal_status" not in row
        assert "rank_eligible" not in row
        assert row["can_execute"] is False
