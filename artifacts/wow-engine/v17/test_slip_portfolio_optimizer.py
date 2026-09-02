from v17.slip_portfolio_optimizer import optimize_portfolio, thesis_identity


def _leg(row_id: str, *, player: str, prob: float, game: str = "game-1", stat: str = "STRIKEOUTS", direction: str = "MORE", line: float = 2.5):
    return {
        "row_id": row_id,
        "event_id": game,
        "player": player,
        "prop_type": stat,
        "direction": direction,
        "line": line,
        "model_probability": prob + 0.01,
        "calibrated_probability": prob,
        "calibrated_lower_bound": prob - 0.02,
    }


def test_normalized_thesis_uses_line_family_not_exact_threshold():
    a = _leg("a", player="Sean Manaea", prob=0.60, line=2.5)
    b = _leg("b", player="Sean Manaea", prob=0.60, line=4.5)
    assert thesis_identity(a) == thesis_identity(b)


def test_borderline_common_hinge_is_replaced_on_second_card_when_stronger_independent_option_exists():
    manaea_flex = _leg("manaea-flex", player="Sean Manaea", prob=0.58)
    manaea_power = _leg("manaea-power", player="Sean Manaea", prob=0.58)
    luzardo = _leg("luzardo", player="Jesus Luzardo", prob=0.69, game="game-2")
    gauff = _leg("gauff", player="Coco Gauff", prob=0.68, game="tennis-1", stat="TOTAL_GAMES")
    cole = _leg("cole", player="Gerrit Cole", prob=0.64, game="game-3")
    grayson = _leg("grayson", player="Grayson Rodriguez", prob=0.66, game="game-4", stat="1IP_PITCHES")
    alternative = _leg("alt", player="Independent Pitcher", prob=0.63, game="game-5")

    cards = [
        {"card_id": "flex", "structure": "FLEX", "legs": [manaea_flex, luzardo, gauff, grayson]},
        {"card_id": "power", "structure": "POWER", "legs": [manaea_power, cole, luzardo.copy()]},
    ]
    # Make the copied Luzardo row a distinct independent direction/market thesis
    # so this fixture isolates Manaea as the duplicated hinge.
    cards[1]["legs"][2]["direction"] = "LESS"

    result = optimize_portfolio(cards, alternatives=[alternative])
    assert result.can_execute is False
    assert result.probability_fields_mutated is False
    assert len(result.replacements) == 1
    assert result.replacements[0]["removed_row_id"] == "manaea-power"
    power_ids = [leg["row_id"] for leg in result.cards[1]["legs"]]
    assert "manaea-power" not in power_ids
    assert "alt" in power_ids


def test_no_superior_replacement_shrinks_card_instead_of_using_filler():
    manaea_a = _leg("manaea-a", player="Sean Manaea", prob=0.58)
    manaea_b = _leg("manaea-b", player="Sean Manaea", prob=0.58)
    strong_a = _leg("strong-a", player="A", prob=0.67, game="game-2")
    strong_b = _leg("strong-b", player="B", prob=0.66, game="game-3")
    # Card B's companion legs are deliberately independent; this fixture is
    # testing only the Manaea common hinge rather than three duplicated theses.
    strong_c = _leg("strong-c", player="C", prob=0.68, game="game-4")
    strong_d = _leg("strong-d", player="D", prob=0.65, game="game-5")
    weak_filler = _leg("filler", player="Filler", prob=0.54, game="game-9")

    result = optimize_portfolio(
        [
            {"card_id": "card-a", "legs": [manaea_a, strong_a, strong_b]},
            {"card_id": "card-b", "legs": [manaea_b, strong_c, strong_d]},
        ],
        alternatives=[weak_filler],
    )
    assert result.replacements == []
    assert len(result.removals) == 1
    assert result.removals[0]["removed_row_id"] == "manaea-b"
    assert len(result.cards[1]["legs"]) == 2
    assert result.cards[1]["portfolio_governance"]["card_shrunk"] is True


def test_duplicate_exposure_changes_structural_score_not_probability_fields():
    first = _leg("first", player="Sean Manaea", prob=0.60)
    second = _leg("second", player="Sean Manaea", prob=0.60)
    mate1 = _leg("mate1", player="A", prob=0.70, game="game-2")
    mate2 = _leg("mate2", player="B", prob=0.69, game="game-3")
    cards = [
        {"card_id": "one", "legs": [first, mate1]},
        {"card_id": "two", "legs": [second, mate2]},
    ]
    result = optimize_portfolio(cards, min_card_legs=2)
    # Cannot shrink a two-leg card below minimum, so the duplicate remains but is
    # explicitly marked unresolved and structurally penalized.
    surviving = result.cards[1]["legs"][0]
    assert surviving["model_probability"] == second["model_probability"]
    assert surviving["calibrated_probability"] == second["calibrated_probability"]
    assert surviving["calibrated_lower_bound"] == second["calibrated_lower_bound"]
    assert surviving["portfolio_governance"]["duplicate_thesis_penalty"] > 0
    assert surviving["portfolio_governance"]["duplicate_thesis_unresolved"] is True
    assert result.probability_fields_mutated is False
    assert result.can_execute is False


def test_separate_cards_are_counted_as_one_portfolio_exposure():
    a = _leg("a", player="Sean Manaea", prob=0.60)
    b = _leg("b", player="Sean Manaea", prob=0.60)
    result = optimize_portfolio([
        {"card_id": "flex", "legs": [a, _leg("x", player="X", prob=0.7, game="x")]},
        {"card_id": "power", "legs": [b, _leg("y", player="Y", prob=0.7, game="y")]},
    ])
    assert result.duplicate_counts[thesis_identity(a)] == 2
