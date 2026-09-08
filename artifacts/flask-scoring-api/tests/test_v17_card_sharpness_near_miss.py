from __future__ import annotations

import sys
from pathlib import Path

WOW_ENGINE_ROOT = Path(__file__).resolve().parents[2] / "wow-engine"
if str(WOW_ENGINE_ROOT) not in sys.path:
    sys.path.insert(0, str(WOW_ENGINE_ROOT))

from v17.postmortem_learning_ledger import build_postmortem_outcome
from v17.slip_portfolio_optimizer import canonical_thesis_identity, optimize_portfolio, thesis_identity


def _leg(
    row_id: str,
    *,
    player: str,
    lower: float,
    game: str,
    stat: str = "STRIKEOUTS",
    direction: str = "MORE",
    line: float = 2.5,
    **extra,
):
    row = {
        "row_id": row_id,
        "event_id": game,
        "player": player,
        "prop_type": stat,
        "period": "FULL_GAME",
        "direction": direction,
        "line": line,
        "platform": "PrizePicks",
        "model_probability": min(0.999, lower + 0.04),
        "calibrated_probability": min(0.999, lower + 0.02),
        "calibrated_lower_bound": lower,
    }
    row.update(extra)
    return row


def test_v17_adjacent_thresholds_share_exposure_family_without_probability_mutation():
    a = _leg("a", player="Alexander Zverev", lower=0.68, game="zve-dar", stat="TOTAL_GAMES", direction="LESS", line=32.5)
    b = _leg("b", player="Alexander Zverev", lower=0.69, game="zve-dar", stat="TOTAL_GAMES", direction="LESS", line=33.5)
    assert canonical_thesis_identity(a) != canonical_thesis_identity(b)
    assert thesis_identity(a) == thesis_identity(b)
    result = optimize_portfolio([
        {"card_id": "a", "structure": "FLEX", "legs": [a, _leg("x", player="X", lower=0.72, game="x")]},
        {"card_id": "b", "structure": "FLEX", "legs": [b, _leg("y", player="Y", lower=0.72, game="y")]},
    ])
    assert any(row["removed_row_id"] == "b" for row in result.removals)
    assert result.probability_fields_mutated is False


def test_v17_power_critical_hinge_replaces_with_stronger_independent_row():
    weak = _leg("weak", player="Weak", lower=0.55, game="w")
    strong = _leg("strong", player="Strong", lower=0.85, game="s")
    replacement = _leg("replacement", player="Independent", lower=0.80, game="r")
    result = optimize_portfolio(
        [{"card_id": "power", "structure": "POWER", "legs": [weak, strong]}],
        alternatives=[replacement],
    )
    assert [row["row_id"] for row in result.cards[0]["legs"]] == ["replacement", "strong"]
    assert result.critical_leg_actions[0]["action"] == "REPLACE"
    assert result.probability_fields_mutated is False


def test_v17_power_critical_hinge_shrinks_when_only_filler_exists():
    weak = _leg("weak", player="Weak", lower=0.55, game="w")
    strong = _leg("strong", player="Strong", lower=0.85, game="s")
    filler = _leg("filler", player="Filler", lower=0.50, game="f")
    result = optimize_portfolio(
        [{"card_id": "power", "structure": "POWER", "legs": [weak, strong]}],
        alternatives=[filler],
    )
    assert [row["row_id"] for row in result.cards[0]["legs"]] == ["strong"]
    assert "INSUFFICIENT_LEGS_AFTER_MANDATORY_SHRINK" in result.cards[0]["portfolio_governance"]["blockers"]


def test_v17_zero_event_power_hinge_requires_p0_p1plus_and_tail_path():
    zero = _leg(
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
    result = optimize_portfolio([{"card_id": "power", "structure": "POWER", "legs": [zero, other]}])
    assert [row["row_id"] for row in result.cards[0]["legs"]] == ["other"]
    assert any("POWER_ZERO_EVENT_DISTRIBUTION_UNRESOLVED" in row["reason"] for row in result.removals)


def test_v17_valid_zero_event_distribution_can_survive_structure_gate():
    zero = _leg(
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
    result = optimize_portfolio([{"card_id": "power", "structure": "POWER", "legs": [zero, other]}])
    assert len(result.cards[0]["legs"]) == 2
    assert result.cards[0]["legs"][0]["portfolio_governance"]["zero_event_audit"]["status"] == "PASS"


def test_v17_power_admission_floor_does_not_rewrite_model_qualification_probability():
    marginal = _leg("marginal", player="Marginal", lower=0.61, game="m")
    strong = _leg("strong", player="Strong", lower=0.78, game="s")
    result = optimize_portfolio([
        {
            "card_id": "power",
            "structure": "POWER",
            "power_admission_lower_bound_floor": 0.65,
            "legs": [marginal, strong],
        }
    ])
    assert marginal["calibrated_lower_bound"] == 0.61
    assert any(row["removed_row_id"] == "marginal" for row in result.removals)
    assert result.probability_fields_mutated is False


def test_v17_close_loss_remains_loss_and_records_distance_and_card_damage():
    outcome = build_postmortem_outcome(
        {"prediction_id": "cease-hits", "exact_line": 4.5, "direction": "MORE"},
        official_result="LOSS",
        settlement_source="official_box_score",
        settlement_timestamp="2026-09-07T23:00:00-05:00",
        actual_value=4,
        close_miss_tolerance=0.5,
        cards_killed=1,
        critical_leg_rank=1,
    )
    assert outcome.official_result == "LOSS"
    assert outcome.signed_distance_to_threshold == -0.5
    assert outcome.close_miss_flag is True
    assert outcome.cards_killed == 1


def test_v17_duplicate_close_loss_attributes_multiple_cards_without_regrading():
    outcome = build_postmortem_outcome(
        {"prediction_id": "zverev-tb", "exact_line": 0.5, "direction": "LESS"},
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


def test_v17_moneyline_loss_does_not_get_fake_scalar_distance():
    outcome = build_postmortem_outcome(
        {"prediction_id": "fsu-ml", "side": "FSU"},
        official_result="LOSS",
        settlement_source="official_final_score",
        settlement_timestamp="2026-09-07T23:30:00-05:00",
    )
    assert outcome.official_result == "LOSS"
    assert outcome.signed_distance_to_threshold is None
    assert outcome.close_miss_flag is None
