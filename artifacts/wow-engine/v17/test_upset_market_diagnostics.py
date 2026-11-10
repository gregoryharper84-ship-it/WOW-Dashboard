from __future__ import annotations

import pytest

from v17.upset_market_diagnostics import (
    CAN_EXECUTE,
    GRANTS_MODEL_CERTIFICATION,
    GRANTS_RANK_ELIGIBILITY,
    MUTATES_SPORTING_PROBABILITY,
    analyze_upset_market_snapshot,
    no_vig_probabilities,
)


def _outcomes():
    return {
        "FAVORITE": {
            "calibrated_probability": 0.58,
            "calibrated_lower_bound": 0.51,
            "calibrated_upper_bound": 0.65,
        },
        "DOG": {
            "calibrated_probability": 0.42,
            "calibrated_lower_bound": 0.37,
            "calibrated_upper_bound": 0.49,
        },
    }


def _quotes():
    return [
        {
            "book": "Book A", "source_snapshot_id": "a1",
            "official_event_id": "official-match-id",
            "market_type": "FULL_MATCH_TWO_WAY",
            "settlement_basis": "including_overtime_if_applicable",
            "captured_at_utc": "2026-11-10T17:40:00Z",
            "american_odds": {"FAVORITE": -200, "DOG": 175},
        },
        {
            "book": "Book B", "source_snapshot_id": "b1",
            "official_event_id": "official-match-id",
            "market_type": "FULL_MATCH_TWO_WAY",
            "settlement_basis": "including_overtime_if_applicable",
            "captured_at_utc": "2026-11-10T17:45:00Z",
            "american_odds": {"FAVORITE": -185, "DOG": 170},
        },
    ]


def _evaluate(**changes):
    kwargs = {
        "official_event_id": "official-match-id",
        "market_type": "FULL_MATCH_TWO_WAY",
        "settlement_basis": "including_overtime_if_applicable",
        "event_start_time_utc": "2026-11-10T19:00:00Z",
        "decision_time_utc": "2026-11-10T18:00:00Z",
        "governed_outcomes": _outcomes(),
        "governed_receipt_id": "model-receipt-123",
        "book_quotes": _quotes(),
        "method": "power",
    }
    kwargs.update(changes)
    return analyze_upset_market_snapshot(**kwargs)


@pytest.mark.parametrize("method", ["power", "multiplicative"])
def test_no_vig_three_way_sums_to_one_without_renaming_outcomes(method):
    probs = no_vig_probabilities(
        {"HOME": -110, "DRAW": 240, "AWAY": 270}, method=method
    )
    assert set(probs) == {"HOME", "DRAW", "AWAY"}
    assert sum(probs.values()) == pytest.approx(1.0)
    assert all(0 < val < 1 for val in probs.values())


def test_power_and_multiplicative_are_distinct_predeclared_methods():
    prices = {"FAVORITE": -300, "DOG": 245}
    a = no_vig_probabilities(prices, method="power")
    b = no_vig_probabilities(prices, method="multiplicative")
    assert a["DOG"] != pytest.approx(b["DOG"])


def test_research_diagnostics_never_certify_promote_or_rewrite_probability():
    result = _evaluate()
    assert result["status"] == "RESEARCH_ONLY"
    assert result["market_favorite"] == "FAVORITE"
    assert result["no_vig_method"] == "power"
    assert result["no_vig_method_role"] == "PRIMARY_RESEARCH"
    assert result["no_vig_method_contract_version"] == "V17_POWER_PRIMARY_V1"
    assert result["outcomes"]["DOG"]["governed_probability_unchanged"] == 0.42
    assert result["outcomes"]["DOG"]["market_disagreement"] > 0
    assert result["outcomes"]["DOG"]["market_disagreement_reason_code"] == "MODEL_MARKET_DISAGREEMENT_UNVALIDATED"
    assert result["outcomes"]["DOG"]["information_edge_status"] == "REFERENCE_CLASS_UNAVAILABLE_INFORMATION_EDGE_UNPROVEN"
    assert result["outcomes"]["DOG"]["conservative_disagreement"] > 0
    assert result["outcomes"]["DOG"]["reference_class_bias_component"] is None
    assert result["outcomes"]["DOG"]["information_edge_proven"] is False
    assert result["can_execute"] is False
    assert result["rank_eligible"] is False
    assert result["automatic_pick_promotion"] is False
    assert CAN_EXECUTE is False
    assert MUTATES_SPORTING_PROBABILITY is False
    assert GRANTS_MODEL_CERTIFICATION is False
    assert GRANTS_RANK_ELIGIBILITY is False


def test_bias_decomposition_is_only_displayed_with_pregame_reference_receipt():
    result = _evaluate(
        empirical_reference_probs={"FAVORITE": 0.66, "DOG": 0.34},
        reference_class_receipt_id="reference-001",
        reference_class_as_of_utc="2026-11-09T12:00:00Z",
    )
    dog = result["outcomes"]["DOG"]
    assert dog["reference_class_residual_disagreement"] == pytest.approx(0.08)
    assert (
        dog["reference_class_bias_component"]
        + dog["reference_class_residual_disagreement"]
        == pytest.approx(dog["market_disagreement"])
    )
    assert dog["information_edge_proven"] is False
    assert dog["information_edge_status"] == "BIAS_DECOMPOSITION_AVAILABLE_INFORMATION_EDGE_UNPROVEN"


def test_reference_class_without_point_in_time_provenance_fails_closed():
    with pytest.raises(ValueError, match="MARKET_REFERENCE_CLASS_UNVERIFIED"):
        _evaluate(empirical_reference_probs={"FAVORITE": 0.6, "DOG": 0.4})


def test_stale_market_source_is_rejected():
    with pytest.raises(ValueError, match="MARKET_QUOTE_STALE"):
        _evaluate(decision_time_utc="2026-11-10T18:50:00Z")


def test_future_quote_is_rejected():
    quotes = _quotes()
    quotes[0]["captured_at_utc"] = "2026-11-10T19:01:00Z"
    with pytest.raises(ValueError, match="MARKET_QUOTE_FROM_FUTURE"):
        _evaluate(book_quotes=quotes)


def test_market_labels_must_match_governed_outcome_space():
    quotes = _quotes()
    quotes[0]["american_odds"] = {"FAVORITE": -200, "OTHER": 175}
    with pytest.raises(ValueError, match="MARKET_BOOK_OUTCOME_MISMATCH"):
        _evaluate(book_quotes=quotes)


def test_conflicting_books_leave_favorite_identity_unresolved():
    quotes = _quotes()
    quotes[0]["american_odds"] = {"FAVORITE": -150, "DOG": 120}
    quotes[1]["american_odds"] = {"FAVORITE": 125, "DOG": -140}
    result = _evaluate(book_quotes=quotes)
    assert result["market_favorite"] is None
    assert result["market_favorite_verified"] is False
    assert result["book_favorites_agree"] is False


def test_single_book_research_is_explicitly_partial():
    result = _evaluate(book_quotes=_quotes()[:1])
    assert result["status"] == "RESEARCH_ONLY_SINGLE_BOOK"


def test_bogus_governed_probability_distribution_is_rejected():
    invalid = _outcomes()
    invalid["DOG"]["calibrated_probability"] = 0.58
    invalid["DOG"]["calibrated_upper_bound"] = 0.65
    with pytest.raises(ValueError, match="MARKET_MODEL_OUTCOMES_NOT_NORMALIZED"):
        _evaluate(governed_outcomes=invalid)


def test_three_way_market_keeps_draw_distinct():
    result = _evaluate(
        governed_outcomes={
            "HOME": {"calibrated_probability": 0.43, "calibrated_lower_bound": 0.35, "calibrated_upper_bound": 0.50},
            "DRAW": {"calibrated_probability": 0.29, "calibrated_lower_bound": 0.20, "calibrated_upper_bound": 0.40},
            "AWAY": {"calibrated_probability": 0.28, "calibrated_lower_bound": 0.19, "calibrated_upper_bound": 0.39},
        },
        market_type="REGULATION_THREE_WAY",
        settlement_basis="90_minutes_plus_stoppage",
        book_quotes=[
            {
                "book": "A", "source_snapshot_id": "a1",
                "official_event_id": "official-match-id",
                "market_type": "REGULATION_THREE_WAY",
                "settlement_basis": "90_minutes_plus_stoppage", "captured_at_utc": "2026-11-10T17:40:00Z",
                "american_odds": {"HOME": 115, "DRAW": 250, "AWAY": 240},
            },
            {
                "book": "B", "source_snapshot_id": "b1",
                "official_event_id": "official-match-id",
                "market_type": "REGULATION_THREE_WAY",
                "settlement_basis": "90_minutes_plus_stoppage", "captured_at_utc": "2026-11-10T17:45:00Z",
                "american_odds": {"HOME": 110, "DRAW": 255, "AWAY": 245},
            },
        ],
    )
    assert set(result["outcomes"]) == {"HOME", "DRAW", "AWAY"}
    assert result["market_favorite"] == "HOME"


def test_same_teams_but_different_market_settlement_is_rejected():
    quotes = _quotes()
    quotes[1]["settlement_basis"] = "regulation_only"
    with pytest.raises(ValueError, match="MARKET_BOOK_EVENT_OR_SETTLEMENT_MISMATCH"):
        _evaluate(book_quotes=quotes)


def test_other_event_market_quote_does_not_cross_contaminate():
    quotes = _quotes()
    quotes[1]["official_event_id"] = "different-official-event"
    with pytest.raises(ValueError, match="MARKET_BOOK_EVENT_OR_SETTLEMENT_MISMATCH"):
        _evaluate(book_quotes=quotes)


def test_multiplicative_market_baseline_is_sensitivity_not_primary():
    result = _evaluate(method="multiplicative")
    assert result["no_vig_method"] == "multiplicative"
    assert result["no_vig_method_role"] == "SENSITIVITY_ONLY"
    assert result["outcomes"]["DOG"]["information_edge_proven"] is False
    assert result["rank_eligible"] is False
    assert result["can_execute"] is False


def test_no_reference_class_never_calls_disagreement_real_information_edge():
    result = _evaluate()
    dog = result["outcomes"]["DOG"]
    assert dog["market_disagreement"] > 0
    assert dog["information_edge_status"] == "REFERENCE_CLASS_UNAVAILABLE_INFORMATION_EDGE_UNPROVEN"
    assert dog["reference_class_bias_component"] is None
    assert dog["reference_class_residual_disagreement"] is None
    assert dog["automatic_pick_promotion"] is False


def test_books_with_clear_opposite_favorites_cannot_be_overridden_by_average():
    quotes = _quotes()
    # First book strongly prefers FAVORITE, second slightly prefers DOG.
    # The average can still prefer FAVORITE, but that is not a verified
    # cross-book favorite classification.
    quotes[0]["american_odds"] = {"FAVORITE": -280, "DOG": 235}
    quotes[1]["american_odds"] = {"FAVORITE": 115, "DOG": -130}
    result = _evaluate(book_quotes=quotes)
    assert result["outcomes"]["FAVORITE"]["no_vig_market_probability"] > 0.5
    assert result["market_favorite"] is None
    assert result["market_favorite_verified"] is False
    assert result["book_favorites_agree"] is False
    assert result["outcomes"]["DOG"]["is_non_market_favorite"] is False
    assert result["outcomes"]["DOG"]["information_edge_proven"] is False
    assert result["can_execute"] is False


def test_power_de_vig_extreme_prices_require_adaptive_root_above_64():
    """Regress an extreme but solvable root; fixed-64 bisection was wrong."""
    result = no_vig_probabilities({"A": -10000, "B": -11000}, method="power")
    assert result["A"] == pytest.approx(0.4835675804, abs=1e-7)
    assert result["B"] == pytest.approx(0.5164324196, abs=1e-7)
    assert sum(result.values()) == pytest.approx(1.0)


def test_power_de_vig_unbracketed_infinite_like_odds_fails_closed():
    """Reject float-saturated inputs rather than normalizing a bogus root."""
    with pytest.raises(ValueError, match="MARKET_POWER_NO_VIG_NOT_CONVERGED"):
        no_vig_probabilities({"A": -1e20, "B": -1e20}, method="power")


def test_decision_at_kickoff_is_not_pregame():
    """No post-start decision may become a point-in-time research receipt."""
    with pytest.raises(ValueError, match="MARKET_DECISION_NOT_PREGAME"):
        _evaluate(decision_time_utc="2026-11-10T19:00:00Z")


def test_missing_book_snapshot_id_rejected():
    """Every quote needs a concrete source snapshot identifier."""
    quotes = _quotes()
    quotes[0]["source_snapshot_id"] = ""
    with pytest.raises(ValueError, match="MARKET_BOOK_OR_SOURCE_INVALID"):
        _evaluate(book_quotes=quotes)


def test_duplicate_book_not_counted_as_independent_source():
    """Duplicate book names cannot masquerade as two independent books."""
    quotes = _quotes()
    quotes[1]["book"] = "book a"
    with pytest.raises(ValueError, match="MARKET_BOOK_OR_SOURCE_INVALID"):
        _evaluate(book_quotes=quotes)


def test_reference_class_from_after_decision_is_not_time_safe():
    """Reference classes must exist strictly before the prediction decision."""
    with pytest.raises(ValueError, match="MARKET_REFERENCE_CLASS_UNVERIFIED"):
        _evaluate(
            empirical_reference_probs={"FAVORITE": 0.6, "DOG": 0.4},
            reference_class_receipt_id="reference-late",
            reference_class_as_of_utc="2026-11-10T18:00:01Z",
        )


def test_invalid_american_odds_rejected():
    """Do not interpret malformed prices as probability evidence."""
    quotes = _quotes()
    quotes[0]["american_odds"]["DOG"] = -50
    with pytest.raises(ValueError, match="MARKET_ODDS_INVALID"):
        _evaluate(book_quotes=quotes)


def test_underround_book_rejected_by_primary_power_method():
    """An underround requires separate handling; do not silently de-vig it."""
    quotes = _quotes()
    quotes[0]["american_odds"] = {"FAVORITE": 200, "DOG": 200}
    with pytest.raises(ValueError, match="MARKET_UNDERROUND_UNSUPPORTED"):
        _evaluate(book_quotes=quotes)


def test_invalid_governed_bounds_rejected():
    """Never surface a probability package whose point is outside its bounds."""
    values = _outcomes()
    values["DOG"]["calibrated_lower_bound"] = 0.47
    with pytest.raises(ValueError, match="MARKET_MODEL_BOUNDS_INVALID"):
        _evaluate(governed_outcomes=values)


def test_invalid_reference_class_probabilities_rejected():
    """A source receipt does not excuse impossible empirical proportions."""
    with pytest.raises(ValueError, match="MARKET_REFERENCE_CLASS_INVALID"):
        _evaluate(
            empirical_reference_probs={"FAVORITE": 1.2, "DOG": -0.2},
            reference_class_receipt_id="reference-invalid",
            reference_class_as_of_utc="2026-11-09T12:00:00Z",
        )


def test_quote_exactly_maximum_age_is_not_stale():
    """Quote age equal to the stated 60-minute boundary is allowed."""
    quotes = _quotes()
    quotes[0]["captured_at_utc"] = "2026-11-10T17:00:00Z"
    result = _evaluate(book_quotes=quotes, max_quote_age_minutes=60)
    assert result["book_count"] == 2
    assert result["market_favorite_verified"] is True
    assert result["market_favorite_tie_tolerance"] == pytest.approx(0.005)


def test_incomplete_governed_probability_distribution_rejected():
    """Catch missing probability mass in market diagnostics as well as alerts."""
    values = _outcomes()
    values["DOG"]["calibrated_probability"] = 0.22
    values["DOG"]["calibrated_lower_bound"] = 0.15
    with pytest.raises(ValueError, match="MARKET_MODEL_OUTCOMES_NOT_NORMALIZED"):
        _evaluate(governed_outcomes=values)
