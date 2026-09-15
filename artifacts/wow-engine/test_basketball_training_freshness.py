from datetime import date

import pytest

from basketball_training_replay import (
    MAX_TRAINING_CORPUS_AGE_DAYS,
    evaluate_training_freshness,
)


def test_recent_nba_corpus_passes_freshness_gate():
    result = evaluate_training_freshness(
        "NBA",
        "2026-06-20",
        as_of=date(2026, 9, 15),
    )
    assert result["freshness_status"] == "PASS"
    assert result["age_days"] == 87
    assert result["max_age_days"] == MAX_TRAINING_CORPUS_AGE_DAYS
    assert result["can_execute"] is False


def test_current_production_nba_history_shape_is_too_stale_for_2026_certification():
    with pytest.raises(RuntimeError, match="NBA_TRAINING_CORPUS_STALE"):
        evaluate_training_freshness(
            "NBA",
            "2023-04-02",
            as_of=date(2026, 9, 15),
        )


def test_current_production_wnba_history_shape_is_too_stale_for_2026_certification():
    with pytest.raises(RuntimeError, match="WNBA_TRAINING_CORPUS_STALE"):
        evaluate_training_freshness(
            "WNBA",
            "2022-09-18",
            as_of=date(2026, 9, 15),
        )


def test_future_dated_corpus_fails_closed():
    with pytest.raises(RuntimeError, match="TRAINING_CORPUS_FUTURE_DATED"):
        evaluate_training_freshness(
            "NBA",
            "2026-09-16",
            as_of=date(2026, 9, 15),
        )


def test_freshness_gate_does_not_create_capability_for_other_sports():
    with pytest.raises(ValueError, match="unsupported sport"):
        evaluate_training_freshness(
            "NCAAB",
            "2026-03-20",
            as_of=date(2026, 9, 15),
        )
