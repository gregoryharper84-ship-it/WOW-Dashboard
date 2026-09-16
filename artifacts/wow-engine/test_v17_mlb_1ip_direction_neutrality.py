from copy import deepcopy

from v17.mlb_1ip_direction_neutrality import build_direction_neutral_1ip_audit


def _line(*, row_key="1IP-01", directions=("MORE", "LESS")):
    return {
        "row_key": row_key,
        "player": "Pitcher A",
        "team": "AAA",
        "opponent": "BBB",
        "stat_type": "1ST_INNING_PITCHES_THROWN",
        "line": 15.5,
        "available_directions": list(directions),
    }


def _row(row_key, lower, *, evaluated=True, rank_eligible=False, publishable=False):
    return {
        "row_key": row_key,
        "model_evaluated": evaluated,
        "calibrated_lower_bound": lower,
        "terminal_status": "HELD",
        "terminal_label": "MODEL_QUALIFIED_HOLD",
        "rank_eligible": rank_eligible,
        "probability_publishable": publishable,
    }


def test_less_can_win_direction_neutral_comparison():
    audit = build_direction_neutral_1ip_audit(
        [_line()],
        [
            _row("1IP-01-MORE", 0.54),
            _row("1IP-01-LESS", 0.63),
        ],
    )
    comparison = audit["comparisons"][0]
    assert comparison["preferred_direction"] == "LESS"
    assert comparison["preferred_calibrated_lower_bound"] == 0.63
    assert audit["less_preferred"] == 1
    assert audit["more_preferred"] == 0


def test_more_can_win_without_default_preference():
    audit = build_direction_neutral_1ip_audit(
        [_line()],
        [
            _row("1IP-01-MORE", 0.66),
            _row("1IP-01-LESS", 0.52),
        ],
    )
    assert audit["comparisons"][0]["preferred_direction"] == "MORE"
    assert audit["more_preferred"] == 1
    assert audit["less_preferred"] == 0


def test_bidirectional_offer_requires_both_scored_sides_for_comparison():
    audit = build_direction_neutral_1ip_audit(
        [_line()],
        [_row("1IP-01-MORE", 0.66)],
    )
    comparison = audit["comparisons"][0]
    assert comparison["comparison_status"] == "BIDIRECTIONAL_INCOMPLETE"
    assert comparison["preferred_direction"] is None
    assert audit["bidirectional_expected"] == 1
    assert audit["bidirectional_complete"] == 0
    assert audit["incomplete_pairs"] == 1


def test_unsupported_or_rejected_pair_does_not_manufacture_selection():
    audit = build_direction_neutral_1ip_audit(
        [_line()],
        [
            {
                "row_key": "1IP-01-MORE",
                "model_evaluated": False,
                "calibrated_lower_bound": None,
                "terminal_status": "REJECTED",
                "code": "REJECT_OOD",
            },
            {
                "row_key": "1IP-01-LESS",
                "model_evaluated": False,
                "calibrated_lower_bound": None,
                "terminal_status": "REJECTED",
                "code": "REJECT_OOD",
            },
        ],
    )
    assert audit["comparisons"][0]["preferred_direction"] is None
    assert audit["comparison_winners_by_lower_bound"] == []


def test_single_direction_offer_is_not_synthesized_into_two_sides():
    audit = build_direction_neutral_1ip_audit(
        [_line(directions=("LESS",))],
        [_row("1IP-01-LESS", 0.61)],
    )
    comparison = audit["comparisons"][0]
    assert comparison["comparison_status"] == "SINGLE_DIRECTION_OFFER_COMPLETE"
    assert comparison["preferred_direction"] == "LESS"
    assert audit["single_direction_offers"] == 1


def test_audit_does_not_mutate_model_probabilities_or_rank_flags():
    rows = [
        _row("1IP-01-MORE", 0.56, rank_eligible=False, publishable=False),
        _row("1IP-01-LESS", 0.64, rank_eligible=False, publishable=False),
    ]
    before = deepcopy(rows)
    audit = build_direction_neutral_1ip_audit([_line()], rows)
    assert rows == before
    winner = audit["comparison_winners_by_lower_bound"][0]
    assert winner["direction"] == "LESS"
    assert winner["rank_eligible"] is False
    assert winner["probability_publishable"] is False
    assert winner["comparison_only"] is True


def test_non_1ip_rows_are_ignored():
    source = _line()
    source["stat_type"] = "PITCHER_STRIKEOUTS"
    audit = build_direction_neutral_1ip_audit([source], [])
    assert audit["source_1ip_lines"] == 0
    assert audit["comparisons"] == []


def test_live_host_instruction_requires_direction_neutral_1ip():
    from pathlib import Path

    text = Path(__file__).with_name("WOW_V17_CUSTOM_GPT_INSTRUCTIONS.txt").read_text(encoding="utf-8")
    assert "When MORE+LESS are offered, score both" in text
    assert "No default MORE or manual LESS-only haircut/ceiling" in text
    assert "a missing side makes the pair incomplete" in text
    assert len(text) <= 8000
