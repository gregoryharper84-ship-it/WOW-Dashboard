from v17.slip_portfolio_optimizer import (
    canonical_thesis_identity,
    component_composite_overlap,
    optimize_portfolio,
    thesis_identity,
)


def _leg(
    row_id: str,
    *,
    player: str,
    prob: float,
    game: str = "game-1",
    stat: str = "STRIKEOUTS",
    direction: str = "MORE",
    line: float = 2.5,
    period: str = "FULL_GAME",
):
    return {
        "row_id": row_id,
        "event_id": game,
        "player": player,
        "prop_type": stat,
        "period": period,
        "direction": direction,
        "line": line,
        "platform": "PrizePicks",
        "model_probability": prob + 0.01,
        "calibrated_probability": prob,
        "calibrated_lower_bound": prob - 0.02,
    }


def test_exact_identity_distinguishes_threshold_while_family_identity_groups_it():
    a = _leg("a", player="Sean Manaea", prob=0.60, line=2.5)
    b = _leg("b", player="Sean Manaea", prob=0.60, line=4.5)
    assert canonical_thesis_identity(a) != canonical_thesis_identity(b)
    assert thesis_identity(a) == thesis_identity(b)


def test_gage_jump_exact_duplicate_across_cards_is_removed_without_probability_haircut():
    a = _leg("gage-a", player="Gage Jump", prob=0.68, game="ath-kc", stat="1IP_PITCHES", line=16.5, period="1IP")
    b = _leg("gage-b", player="Gage Jump", prob=0.68, game="ath-kc", stat="1IP_PITCHES", line=16.5, period="1IP")
    cards = [
        {"card_id": "bonus-flex", "structure": "FLEX", "legs": [a, _leg("x", player="X", prob=0.71, game="x")]},
        {"card_id": "six-flex", "structure": "FLEX", "legs": [b, _leg("y", player="Y", prob=0.70, game="y")]},
    ]
    result = optimize_portfolio(cards)
    assert result.exact_duplicate_counts[canonical_thesis_identity(a)] == 2
    assert [r["removed_row_id"] for r in result.removals] == ["gage-b"]
    assert result.probability_fields_mutated is False
    assert result.cards[1]["portfolio_governance"]["portfolio_qualified"] is False
    assert "INSUFFICIENT_LEGS_AFTER_MANDATORY_SHRINK" in result.cards[1]["portfolio_governance"]["blockers"]


def test_gerrit_cole_exact_duplicate_regression_fixture():
    a = _leg("cole-a", player="Gerrit Cole", prob=0.67, game="nyy-bal", stat="1IP_PITCHES", direction="LESS", line=15.5, period="1IP")
    b = _leg("cole-b", player="Gerrit Cole", prob=0.67, game="nyy-bal", stat="1IP_PITCHES", direction="LESS", line=15.5, period="1IP")
    result = optimize_portfolio([
        {"card_id": "a", "legs": [a, _leg("x", player="X", prob=0.70, game="x"), _leg("z", player="Z", prob=0.69, game="z")]},
        {"card_id": "b", "legs": [b, _leg("y", player="Y", prob=0.70, game="y"), _leg("q", player="Q", prob=0.69, game="q")]},
    ])
    assert any(r["removed_row_id"] == "cole-b" for r in result.removals)
    assert result.probability_fields_mutated is False


def test_caitlin_clark_points_more_and_pra_more_are_component_composite_overlap():
    points = _leg("clark-pts", player="Caitlin Clark", prob=0.66, game="ind-dal", stat="POINTS", direction="MORE", line=17.5)
    pra = _leg("clark-pra", player="Caitlin Clark", prob=0.65, game="ind-dal", stat="PRA", direction="MORE", line=29.5)
    assert component_composite_overlap(points, pra) is True
    result = optimize_portfolio([
        {"card_id": "four", "legs": [points, _leg("x", player="X", prob=0.70, game="x")]},
        {"card_id": "six", "legs": [pra, _leg("y", player="Y", prob=0.70, game="y"), _leg("z", player="Z", prob=0.69, game="z")]},
    ])
    assert ("clark-pts", "clark-pra") in result.component_overlap_pairs
    assert any(r["removed_row_id"] == "clark-pra" for r in result.removals)
    assert result.probability_fields_mutated is False


def test_superior_independent_replacement_beats_common_hinge_and_filler_is_rejected():
    first = _leg("first", player="Sean Manaea", prob=0.58)
    second = _leg("second", player="Sean Manaea", prob=0.58)
    superior = _leg("superior", player="Independent", prob=0.64, game="other")
    filler = _leg("filler", player="Filler", prob=0.54, game="other-2")
    result = optimize_portfolio(
        [
            {"card_id": "one", "legs": [first, _leg("a", player="A", prob=0.70, game="a")]},
            {"card_id": "two", "legs": [second, _leg("b", player="B", prob=0.69, game="b")]},
        ],
        alternatives=[filler, superior],
    )
    assert result.replacements[0]["replacement_row_id"] == "superior"
    assert "filler" not in [leg["row_id"] for leg in result.cards[1]["legs"]]


def test_no_superior_replacement_always_shrinks_even_below_requested_minimum():
    first = _leg("first", player="Sean Manaea", prob=0.60)
    second = _leg("second", player="Sean Manaea", prob=0.60)
    result = optimize_portfolio([
        {"card_id": "one", "legs": [first, _leg("a", player="A", prob=0.70, game="a")]},
        {"card_id": "two", "legs": [second, _leg("b", player="B", prob=0.70, game="b")]},
    ])
    assert len(result.cards[1]["legs"]) == 1
    assert result.cards[1]["portfolio_governance"]["card_shrunk"] is True
    assert result.cards[1]["portfolio_governance"]["portfolio_qualified"] is False


def test_flex_same_event_without_joint_model_is_held_not_assumed_independent():
    a = _leg("a", player="Player A", prob=0.70, game="same-event", stat="POINTS")
    b = _leg("b", player="Player B", prob=0.69, game="same-event", stat="REBOUNDS")
    result = optimize_portfolio([{"card_id": "flex", "structure": "FLEX", "legs": [a, b]}])
    governance = result.cards[0]["portfolio_governance"]
    assert governance["portfolio_qualified"] is False
    assert "PP_CORRELATION_UNRESOLVED" in governance["blockers"]

    resolved = optimize_portfolio([
        {"card_id": "flex", "structure": "FLEX", "joint_probability_status": "PASS", "legs": [a, b]}
    ])
    assert resolved.cards[0]["portfolio_governance"]["portfolio_qualified"] is True


def test_prior_session_ledger_blocks_reusing_exact_thesis_on_later_card():
    prior = _leg("prior", player="Gage Jump", prob=0.68, game="ath-kc", stat="1IP_PITCHES", line=16.5, period="1IP")
    later = _leg("later", player="Gage Jump", prob=0.68, game="ath-kc", stat="1IP_PITCHES", line=16.5, period="1IP")
    result = optimize_portfolio(
        [{"card_id": "later-card", "legs": [later, _leg("x", player="X", prob=0.70, game="x")]}],
        prior_session_legs=[prior],
    )
    assert any(r["removed_row_id"] == "later" for r in result.removals)
    assert result.probability_fields_mutated is False
