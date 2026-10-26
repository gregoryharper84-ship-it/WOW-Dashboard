"""NHL official final-score research reconstruction. Not a scoring specialist."""
from copy import deepcopy
import pytest
from v17.nhl_total_goals_score_regimes import (
    CAN_EXECUTE, PROBABILITY_PUBLISHABLE,
    NHLScoringRegimeError, reconcile_official_gamecenter_landing,
)


def goal(home, modifier=None):
    return {"isHome": home, "teamAbbrev": "BOS" if home else "NYR",
            "goalModifier": modifier or "none"}


def period(number, kind, goals):
    return {"periodDescriptor": {"number": number, "periodType": kind}, "goals": goals}


def sample(kind="REG"):
    scoring = [
        period(1, "REG", [goal(True), goal(False)]),
        period(2, "REG", []),
        period(3, "REG", []),
    ]
    if kind == "REG":
        scoring[2]["goals"].append(goal(True, "empty-net"))
    elif kind == "OT":
        scoring.append(period(4, "OT", [goal(True)]))
    else:
        scoring.append(period(4, "OT", []))
        scoring.append(period(5, "SO", [goal(True)]))
    return {
        "id": 2024020022, "season": 20242025, "gameType": 2,
        "gameState": "OFF", "periodDescriptor": {"number": 3 if kind=="REG" else 4 if kind=="OT" else 5,
                                              "periodType": kind},
        "homeTeam": {"abbrev": "BOS", "score": 2},
        "awayTeam": {"abbrev": "NYR", "score": 1},
        "summary": {"scoring": scoring, "shootout": {"events": [
            {"gameWinner": True, "teamAbbrev": {"default": "BOS"}, "result": "goal"}
        ]} if kind == "SO" else {}},
    }


@pytest.mark.parametrize("kind,ot,shootout,empty", [
    ("REG", 0, 0, 1), ("OT", 1, 0, 0), ("SO", 0, 1, 0),
])
def test_period_to_final_score_reconciles_all_outcome_types(kind, ot, shootout, empty):
    x = reconcile_official_gamecenter_landing(sample(kind))
    assert x.regulation_home_goals + x.regulation_away_goals == 3 - ot - shootout
    assert x.actual_overtime_goals == ot
    assert x.shootout_awarded_goal == shootout
    assert x.empty_net_goals == empty
    assert x.official_final_total == 3
    assert x.reconciled_total == 3
    assert len(x.source_payload_sha256) == 64
    assert x.historical_reconstruction
    assert x.can_execute is False and x.probability_publishable is False


def test_shootout_credited_only_as_one_game_total_goal_not_skater_goal():
    x = reconcile_official_gamecenter_landing(sample("SO"))
    assert x.regulation_home_goals == 1 and x.regulation_away_goals == 1
    assert x.actual_overtime_goals == 0
    assert x.shootout_awarded_goal == 1
    assert x.official_final_total == 3


@pytest.mark.parametrize("change", [
    lambda x: x["homeTeam"].__setitem__("score", 4),
    lambda x: x["homeTeam"].__setitem__("abbrev", "NYR"),
    lambda x: x["summary"]["scoring"][0]["goals"][0].__setitem__("teamAbbrev", "NYR"),
    lambda x: x["summary"]["scoring"][0]["goals"][0].pop("isHome"),
    lambda x: x["summary"]["scoring"][2]["goals"].append(goal(False)),
    lambda x: x["summary"].__setitem__("scoring", None),
    lambda x: x.__setitem__("gameState", "LIVE"),
    lambda x: x.__setitem__("gameType", 3),
    lambda x: x["summary"]["scoring"].pop(1),
])
def test_bad_final_score_or_scoring_identity_is_held(change):
    p = sample("REG")
    change(p)
    with pytest.raises(NHLScoringRegimeError) as exc:
        reconcile_official_gamecenter_landing(p)
    assert exc.value.code == "NHL_TOTALS_FINAL_SCORE_REGIME_MISMATCH"


def test_broken_shootout_award_is_held():
    p = sample("SO")
    p["homeTeam"]["score"] = 3
    with pytest.raises(NHLScoringRegimeError):
        reconcile_official_gamecenter_landing(p)


def test_unexpected_shootout_scoring_goal_rejected():
    p=sample("SO")
    p["summary"]["scoring"][-1]["goals"].append(goal(True))
    with pytest.raises(NHLScoringRegimeError):
        reconcile_official_gamecenter_landing(p)


def test_no_probabilities_or_execution_bypass():
    assert not CAN_EXECUTE and not PROBABILITY_PUBLISHABLE



def test_official_nested_abbreviation_format():
    p = sample("SO")
    p["summary"]["scoring"][0]["goals"][0]["teamAbbrev"] = {"default": "BOS"}
    p["summary"]["scoring"][-1]["goals"][0]["teamAbbrev"] = {"default": "BOS"}
    event = reconcile_official_gamecenter_landing(p)
    assert event.shootout_awarded_goal == 1


@pytest.mark.parametrize("mutate", [
    lambda p: p["summary"]["shootout"]["events"].clear(),
    lambda p: p["summary"]["shootout"]["events"].append({"gameWinner": True, "teamAbbrev": {"default": "BOS"}, "result": "goal"}),
    lambda p: p["summary"]["shootout"]["events"][0].__setitem__("result", "save"),
    lambda p: p["summary"]["shootout"]["events"][0].__setitem__("teamAbbrev", {"default": "NYR"}),
    lambda p: p["summary"]["scoring"][-1]["goals"][0].__setitem__("teamAbbrev", {"default": "NYR"}),
    lambda p: p["summary"]["scoring"][-1]["goals"].clear(),
    lambda p: p["summary"]["scoring"].pop(3),
])
def test_shootout_does_not_silently_guess_winning_goal(mutate):
    p = sample("SO")
    mutate(p)
    with pytest.raises(NHLScoringRegimeError) as err:
        reconcile_official_gamecenter_landing(p)
    assert err.value.code == "NHL_TOTALS_FINAL_SCORE_REGIME_MISMATCH"
