from __future__ import annotations

from datetime import datetime, timedelta, timezone

import pytest

from nfl_event_model_contract import CONTROLLING_SPECIALIST
from v17.nfl_pickem_accuracy_scorecard import grade_week, poisson_binomial


def fixture(n=16):
    kickoff = datetime(2026, 10, 11, 17, tzinfo=timezone.utc)
    games, predictions, settlements = [], [], []
    for i in range(n):
        event = f"official-{i}"
        games.append({"official_event_id": event, "home_team": f"H{i}",
                      "away_team": f"A{i}", "kickoff_utc": kickoff.isoformat(),
                      "status": "SCHEDULED"})
        predictions.append({
            "official_event_id": event, "prediction_id": f"immutable-{i}",
            "controlling_specialist": CONTROLLING_SPECIALIST,
            "model_timestamp": (kickoff - timedelta(hours=2)).isoformat(),
            "home_probability": 0.7, "away_probability": 0.3,
        })
        settlements.append({
            "official_event_id": event, "winner_team": f"H{i}",
            "status": "FINAL", "settled_at": (kickoff + timedelta(hours=4)).isoformat(),
            "settlement_source": "independent-official-results",
        })
    return games, predictions, settlements


@pytest.mark.parametrize("wrong,correct", [(0, 16), (1, 15), (7, 9)])
def test_full_slate_scores(wrong, correct):
    games, forecasts, results = fixture()
    for i in range(wrong):
        results[i]["winner_team"] = f"A{i}"
    report = grade_week(games, forecasts, results)
    assert report["status"] == "COMPLETE"
    assert report["actual_correct"] == correct
    assert report["actual_accuracy"] == correct / 16
    assert report["prediction_coverage"] == 1
    assert report["expected_correct"] == pytest.approx(11.2)
    assert report["brier"] is not None
    assert sum(report["correct_count_distribution_independence_assumption"]) == pytest.approx(1)
    assert report["can_execute"] is False


def test_incomplete_forecast_never_inflates_accuracy():
    games, forecasts, results = fixture()
    forecasts.pop()
    report = grade_week(games, forecasts, results)
    assert report["status"] == "INCOMPLETE"
    assert report["prediction_count"] == 15
    assert report["actual_accuracy"] is None
    assert report["expected_correct"] is None
    assert report["correct_count_distribution_independence_assumption"] is None
    assert report["rows"][-1]["status"] == "PREDICTION_MISSING"


def test_postkickoff_is_blocked():
    games, forecasts, results = fixture(1)
    forecasts[0]["model_timestamp"] = results[0]["settled_at"]
    report = grade_week(games, forecasts, results)
    assert report["status"] == "INCOMPLETE"
    assert report["rows"][0]["status"] == "PREDICTION_NOT_PREGAME"


def test_stale_input_is_blocked():
    games, forecasts, results = fixture(1)
    forecasts[0]["latest_material_update_at"] = games[0]["kickoff_utc"]
    assert grade_week(games, forecasts, results)["rows"][0]["status"] == "PREDICTION_STALE"


def test_independent_result_required():
    games, forecasts, results = fixture(1)
    results[0].pop("settlement_source")
    report = grade_week(games, forecasts, results)
    assert report["status"] == "INCOMPLETE"
    assert report["rows"][0]["settlement_status"] == "SETTLEMENT_PROVENANCE_MISSING"


def test_wrong_specialist_does_not_score():
    games, forecasts, results = fixture(1)
    forecasts[0]["controlling_specialist"] = "MARKET_CONSENSUS"
    assert grade_week(games, forecasts, results)["rows"][0]["status"] == "SPECIALIST_IDENTITY_INVALID"


def test_duplicate_games_are_rejected():
    games, forecasts, results = fixture(1)
    with pytest.raises(ValueError, match="SCHEDULE_DUPLICATE_EVENT"):
        grade_week(games + games, forecasts, results)


def test_no_silent_extra_predictions():
    games, forecasts, results = fixture(1)
    extra = dict(forecasts[0], official_event_id="not-on-slate")
    with pytest.raises(ValueError, match="PREDICTION_NOT_IN_SCHEDULE"):
        grade_week(games, forecasts + [extra], results)


def test_bad_probabilities_not_repaired():
    games, forecasts, results = fixture(1)
    forecasts[0]["away_probability"] = 0.9
    assert grade_week(games, forecasts, results)["rows"][0]["status"] == "PROBABILITIES_NOT_NORMALIZED"


def test_independence_distribution_example():
    assert poisson_binomial([0.5, 0.5]) == pytest.approx([0.25, 0.5, 0.25])
