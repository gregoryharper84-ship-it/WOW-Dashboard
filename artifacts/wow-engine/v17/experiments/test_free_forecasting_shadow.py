"""Dependency-free adversarial tests for the non-serving challenger harness."""
from __future__ import annotations

from datetime import datetime, timedelta, timezone
import pytest

from free_forecasting_shadow import (ResearchInputError, chronological_partition,
                                    evaluate_experiment, metric_packet, parse_rows)


def fixture():
    rows = []
    anchor = datetime(2024, 1, 1, tzinfo=timezone.utc)
    for i in range(12):
        event = anchor + timedelta(days=2 * i)
        rows.append({"prediction_id": f"p{i}", "official_event_id": f"e{i}",
                     "sport": "NFL", "market": "GAME_WINNER",
                     "event_start_utc": event.isoformat(),
                     "features_asof_utc": (event - timedelta(hours=1)).isoformat(),
                     "outcome_available_utc": (event + timedelta(hours=3)).isoformat(),
                     "outcome": i % 2, "features": {"prior_strength": (i % 3) - 1.0},
                     "incumbent_probability": 0.55,
                     "incumbent_model_timestamp_utc": (event - timedelta(hours=2)).isoformat()})
    return rows


TRAIN_END = "2024-01-11T00:00:00+00:00"
VAL_END = "2024-01-17T00:00:00+00:00"


def fake_trainer(train, val, hold, **kwargs):
    assert len(train) == 6 and len(val) == 3 and len(hold) == 3
    return [0.45] * len(hold)


def test_chronological_triple_split_and_verifiable_holdout():
    report = evaluate_experiment(fixture(), train_end=TRAIN_END, validation_end=VAL_END,
                                 fit_fn=fake_trainer)
    assert report["cohort"] == {"sport": "NFL", "market": "GAME_WINNER",
                                 "train_n": 6, "validation_n": 3, "holdout_n": 3}
    assert report["comparison_status"] == "PAIRED_HOLDOUT"
    assert report["candidate"]["n"] == 3
    assert report["terminal_authority"] == "V17_TERMINAL_REDUCER"
    assert report["can_execute"] is False
    assert report["automatic_promotion_allowed"] is False
    assert report["production_mutation_allowed"] is False


def test_metric_known_values():
    metrics = metric_packet([0.75, 0.25], [1, 0])
    assert metrics["brier"] == pytest.approx(0.0625)
    assert metrics["log_loss"] == pytest.approx(-__import__("math").log(0.75))


@pytest.mark.parametrize("mutator,expected", [
    (lambda rows: rows[1].update(prediction_id="p0"), "DUPLICATE_PREDICTION_ID"),
    (lambda rows: rows[0].update(features_asof_utc=rows[0]["event_start_utc"]), "PREGAME_OR_SETTLEMENT_TIME_INVALID"),
    (lambda rows: rows[0].update(outcome=3), "INVALID_BINARY_OUTCOME"),
    (lambda rows: rows[0].update(features={"future_score": float("nan")}), "NON_FINITE_FEATURE"),
    (lambda rows: rows[0].update(features={"different": 1.0}), "INCONSISTENT_FEATURE_SCHEMA"),
    (lambda rows: rows[0].update(incumbent_model_timestamp_utc=rows[0]["event_start_utc"]), "INCUMBENT_NOT_PREGAME"),
    (lambda rows: rows[0].update(market="PLAYER_POINTS"), "MIXED_SPECIALIST_OR_MARKET_COHORT"),
])
def test_bad_inputs_fail_closed(mutator, expected):
    rows = fixture()
    mutator(rows)
    with pytest.raises(ResearchInputError, match=expected):
        parse_rows(rows)


def test_training_label_not_known_at_validation_start():
    rows = fixture()
    rows[5]["outcome_available_utc"] = "2024-01-14T00:00:00+00:00"
    with pytest.raises(ResearchInputError, match="TRAIN_LABEL_LEAKAGE"):
        chronological_partition(parse_rows(rows), TRAIN_END, VAL_END)


def test_validation_label_not_known_at_holdout_start():
    rows = fixture()
    rows[8]["outcome_available_utc"] = "2024-01-20T00:00:00+00:00"
    with pytest.raises(ResearchInputError, match="VALIDATION_LABEL_LEAKAGE"):
        chronological_partition(parse_rows(rows), TRAIN_END, VAL_END)


def test_no_incumbent_comparison_without_complete_verified_baseline():
    rows = fixture()
    del rows[11]["incumbent_probability"]
    del rows[11]["incumbent_model_timestamp_utc"]
    report = evaluate_experiment(rows, train_end=TRAIN_END,
                                 validation_end=VAL_END, fit_fn=fake_trainer)
    assert report["comparison_status"] == "NO_VERIFIED_INCUMBENT_BASELINE"
    assert report["incumbent"] is None


def test_invalid_proba_rejected():
    with pytest.raises(ResearchInputError, match="INVALID_PROBABILITY"):
        metric_packet([1.1], [1])


def test_empty_and_tiny_cohort_rejected():
    with pytest.raises(ResearchInputError, match="EMPTY_OR_NONLIST_DATA"):
        parse_rows([])
    with pytest.raises(ResearchInputError, match="INSUFFICIENT_CHRONOLOGICAL_COHORT"):
        chronological_partition(parse_rows(fixture()[:5]), TRAIN_END, VAL_END)
