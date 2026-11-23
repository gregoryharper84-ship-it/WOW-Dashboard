"""Regression tests: synthetic niche prop examples, not a fitted model or live lines."""
from copy import deepcopy

import pytest

from v17.niche_prop_research import (
    CAN_EXECUTE, STUDY_CATEGORIES, classify_niche_prop,
    summarize_niche_settlements,
)
from v17.slip_research_preflight import audit_research_slips


def example(record_id, sport, stat, period, side, line, outcome, **additional):
    return {
        "record_id": record_id,
        "sport": sport, "stat_type": stat, "market_period": period,
        "side": side, "exact_line": line, "settled_result": outcome,
        "official_event_id": "fixture-event-" + record_id,
        "official_player_id": "fixture-player-" + record_id,
        **additional,
    }


def test_nfl_field_goals_one_combo_one_market_needs_two_distinct_kickers():
    source = example(
        "fg-a", "NFL", "FG Made (Combo)", "1Q", "OVER", 0.5, "WIN",
        combo_participant_ids=["fixture-home-k", "fixture-away-k"],
        combo_team_ids=["HOME", "AWAY"],
        home_team_id="HOME", away_team_id="AWAY",
    )
    frozen = deepcopy(source)
    result = classify_niche_prop(source)
    assert source == frozen
    assert result["category"] == "NFL_1Q_TWO_KICKER_FG_OVER_0_5"
    assert result["identity_gaps"] == []
    assert "two verified kicker" in result["context_to_research"][0]
    assert result["probability"] is None
    assert result["calibrated_lower_bound"] is None
    assert not result["can_execute"] and not CAN_EXECUTE
    broken = deepcopy(source)
    broken["combo_participant_ids"] = ["fixture-home-k", "fixture-home-k"]
    assert "TWO_DISTINCT_OFFICIAL_KICKER_IDS_MISSING" in classify_niche_prop(broken)["identity_gaps"]
    broken["combo_participant_ids"] = ["fixture-home-k"]
    assert "TWO_DISTINCT_OFFICIAL_KICKER_IDS_MISSING" in classify_niche_prop(broken)["identity_gaps"]
    wrong_team = dict(source, combo_team_ids=["HOME", "HOME"])
    assert "COMBO_OPPOSING_TEAM_BINDING_MISSING" in classify_niche_prop(wrong_team)["identity_gaps"]


def test_fgs_other_period_threshold_and_side_do_not_inherit_the_study_category():
    base = example(
        "fg-b", "NFL", "FG_MADE_COMBO", "FIRST_QUARTER",
        "MORE", 0.5, "WIN", combo_participant_ids=["x", "y"],
    )
    for key, value in (("market_period", "FULL_GAME"), ("exact_line", 1.5),
                       ("side", "LESS"), ("sport", "NBA")):
        mutated = dict(base, **{key: value})
        assert classify_niche_prop(mutated)["category"] == "OTHER_MARKET"
    assert classify_niche_prop(dict(base, exact_line=True))["category"] == "OTHER_MARKET"
    assert classify_niche_prop(dict(base, exact_line=float("nan")))["category"] == "OTHER_MARKET"


@pytest.mark.parametrize("stat", ("3PTA", "PERSONAL FOULS", "FTA", "TOV"))
def test_four_distinct_nba_low_volume_under_stats(stat):
    sample = example("nba-" + stat, "NBA", stat, "FULL_GAME", "UNDER", 3.5, "WIN")
    result = classify_niche_prop(sample)
    assert result["category"] == "NBA_LOW_VOLUME_UNDER"
    assert result["identity_gaps"] == []
    assert result["expected_value"] is None
    assert classify_niche_prop(dict(sample, side="OVER"))["category"] == "OTHER_MARKET"


def test_nfl_qb_pass_attempts_and_fantasy_score_are_separate_categories():
    a = example("qb-a", "NFL", "PASS_ATTEMPTS", "Q1", "OVER", 7.5, "WIN")
    b = example("qb-b", "NFL", "FANTASY_SCORE", "Q1", "OVER", 1.9, "LOSS")
    assert classify_niche_prop(a)["category"] == "NFL_1Q_QB_PASS_ATTEMPTS_OVER"
    assert classify_niche_prop(b)["category"] == "NFL_1Q_QB_FANTASY_SCORE_OVER"
    assert "QB_POSITION_UNVERIFIED" in classify_niche_prop(a)["identity_gaps"]
    assert "QB_POSITION_UNVERIFIED" not in classify_niche_prop(
        dict(a, official_player_position="QB"))["identity_gaps"]


def test_non_random_screenshot_shaped_sample_descriptive_only_not_calibration():
    # Synthetic identities, same category counts as the reviewed examples.
    rows = []
    for i in range(7):
        rows.append(example(
            "field-" + str(i), "NFL", "FG_MADE_COMBO", "1Q", "MORE", 0.5, "WIN",
            combo_participant_ids=["home-k-" + str(i), "away-k-" + str(i)],
        ))
    for i, stat in enumerate(("3PTA", "PERSONAL_FOULS", "3PTA", "FTA", "TURNOVERS")):
        rows.append(example("nba-" + str(i), "NBA", stat, "GAME", "LESS", 5, "WIN"))
    rows += [
        example("qb1", "NFL", "FANTASY_SCORE", "Q1", "MORE", 3.7, "WIN"),
        example("qb2", "NFL", "PASS_ATTEMPTS", "Q1", "MORE", 7.5, "WIN"),
        example("qb3", "NFL", "FANTASY_SCORE", "Q1", "MORE", 1.9, "LOSS"),
    ]
    before = deepcopy(rows)
    result = summarize_niche_settlements(rows)
    assert rows == before and len(rows) == 15
    assert sum(x["wins"] for x in result["categories"].values()) == 14
    assert sum(x["losses"] for x in result["categories"].values()) == 1
    assert result["categories"]["NFL_1Q_TWO_KICKER_FG_OVER_0_5"]["wins"] == 7
    assert result["categories"]["NBA_LOW_VOLUME_UNDER"]["wins"] == 5
    assert result["categories"]["NFL_1Q_QB_FANTASY_SCORE_OVER"]["losses"] == 1
    assert result["predictive_hit_probability"] is None
    assert result["certified_joint_probability"] is None
    assert result["estimated_profitability"] is None
    assert result["automatic_promotion"] is False and result["can_execute"] is False
    assert all(not c["pregame_calibration_eligible"] for c in result["categories"].values())
    assert set(result["categories"]).issubset(set(STUDY_CATEGORIES))


def test_duplicate_and_invalid_settlement_never_inflate_sample():
    one = example("same", "NBA", "FTA", "GAME", "LESS", 5, "WIN")
    with pytest.raises(ValueError, match="DUPLICATE"):
        summarize_niche_settlements([one, deepcopy(one)])
    with pytest.raises(ValueError, match="RESULT_INVALID"):
        summarize_niche_settlements([dict(one, settled_result="PENDING")])
    assert summarize_niche_settlements([
        dict(one, settled_result="PUSH"),
    ])["categories"]["NBA_LOW_VOLUME_UNDER"]["observed_hit_rate"] is None


def test_existing_slip_preflight_reports_niche_category_without_approving_entry():
    fg = example(
        "combo", "NFL", "FG_MADE_COMBO", "1Q", "MORE", 0.5, "WIN",
        combo_participant_ids=["k1", "k2"],
    )
    fg.update({"row_id": "combo", "market_family": "PLAYER_PROP"})
    nba = example("nba", "NBA", "FTA", "FULL_GAME", "LESS", 5, "WIN")
    nba.update({"row_id": "nba", "market_family": "PLAYER_PROP"})
    out = audit_research_slips(
        [{"card_id": "research-only", "structure": "FLEX", "legs": [fg, nba]}],
        as_of_utc="2026-11-23T20:00:00Z",
    )
    assert out["status"] == "RESEARCH_HOLD"
    assert out["cards"][0]["niche_category_counts"] == {
        "NFL_1Q_TWO_KICKER_FG_OVER_0_5": 1,
        "NBA_LOW_VOLUME_UNDER": 1,
    }
    combo_blockers = out["cards"][0]["legs"][0]["blockers"]
    assert "NFL_KICKER_COMBO_SPECIALIST_NOT_DECLARED" in combo_blockers
    assert "NFL_KICKER_COMBO_NOT_SINGLE_PLAYER_PROP" in combo_blockers
    assert "NICHE_COMBO_OPPOSING_TEAM_BINDING_MISSING" in combo_blockers
    assert out["cards"][0]["legs"][0]["niche_prop_research"]["category"] == "NFL_1Q_TWO_KICKER_FG_OVER_0_5"
    assert out["joint_probability"] is None and not out["can_execute"]
