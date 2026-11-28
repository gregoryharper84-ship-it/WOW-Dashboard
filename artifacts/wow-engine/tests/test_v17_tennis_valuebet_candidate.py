"""Regression and acceptance checks for tennis candidate-bound source and replay proofs."""
from __future__ import annotations

from datetime import datetime, timedelta, timezone
import pytest

from v17 import tennis_valuebet_candidate as tennis


def test_completed_score_rejects_walkovers_and_retirements():
    """Retirements and walkovers must be excluded from training outcomes."""
    assert tennis._completed_score("6-4 3-1 ret.") is False
    assert tennis._completed_score("W/O") is False
    assert tennis._completed_score("walkover") is False
    assert tennis._completed_score("def.") is False
    assert tennis._completed_score("withdraw") is False
    assert tennis._completed_score("") is False
    assert tennis._completed_score(None) is False


def test_completed_score_accepts_clean_finishes():
    """Standard sets scores are accepted."""
    assert tennis._completed_score("6-4 6-3") is True
    assert tennis._completed_score("7-6(4) 4-6 6-2") is True
    assert tennis._completed_score("6-0 6-0") is True


def test_tennis_candidate_invariants():
    """Universal V17 governance invariants."""
    assert tennis.CAN_EXECUTE is False
    assert tennis.SPORT == "TENNIS"
    assert tennis.MARKET_FAMILY == "OUTRIGHT_WINNER"
    assert tennis.BASE_MODEL_FAMILY == "TENNIS_MAIN_TOUR_MATCH_WIN_LOGIT_V1"
    assert tennis.SOURCE_LICENSE == "CC-BY-4.0"
    assert set(tennis.TOURS) == {"ATP", "WTA"}
    assert set(tennis.SUPPORTED_CATEGORIES) == {"MAIN TOUR", "MASTERS", "GRAND SLAM"}


def test_tennis_player_summary_requires_minimum_prior_matches():
    """Players with fewer than 5 prior matches return None (cold start protection)."""
    history = [
        {
            "start": datetime(2026, 1, i + 1, tzinfo=timezone.utc),
            "surface": "HARD",
            "won": True,
            "event_id": f"match-{i}",
        }
        for i in range(4)
    ]
    target_date = datetime(2026, 1, 10, tzinfo=timezone.utc)
    summary = tennis._player_summary(history, target_date, "HARD")
    assert summary is None


def test_tennis_player_summary_calculates_clean_features_without_leakage():
    """Summary calculates win rate, log volume, and rest days with zero future leakage."""
    history = [
        {
            "start": datetime(2026, 1, i + 1, tzinfo=timezone.utc),
            "surface": "HARD",
            "won": (i % 2 == 0),
            "event_id": f"match-{i}",
        }
        for i in range(10)
    ]
    target_date = datetime(2026, 1, 15, tzinfo=timezone.utc)
    summary = tennis._player_summary(history, target_date, "HARD")
    assert summary is not None
    assert 0.0 <= summary["recent_win_rate"] <= 1.0
    assert 0.0 <= summary["surface_win_rate"] <= 1.0
    assert summary["games_prior_log"] > 0.0
    assert summary["rest_days_capped"] >= 0.0
    assert summary["prior_event_count"] == 10

    # Equal-time and future results cannot enter prior form, workload,
    # last-match rest, or the immutable prior-event provenance digest.
    contaminated = history + [
        {"start": target_date, "surface": "HARD", "won": True, "event_id": "same-time"},
        {"start": target_date + timedelta(days=1), "surface": "HARD", "won": True, "event_id": "future-1"},
        {"start": target_date + timedelta(days=4), "surface": "CLAY", "won": False, "event_id": "future-2"},
    ]
    assert tennis._player_summary(contaminated, target_date, "HARD") == summary


def test_tennis_cold_start_remains_held_with_same_time_and_future_rows():
    """Future and simultaneous observations may not satisfy the five-prior-match floor."""
    cutoff = datetime(2026, 2, 10, tzinfo=timezone.utc)
    prior = [
        {"start": cutoff - timedelta(days=i + 1), "surface": "HARD", "won": True, "event_id": f"prior-{i}"}
        for i in range(4)
    ]
    contaminated = prior + [
        {"start": cutoff, "surface": "HARD", "won": True, "event_id": "same-time"},
        {"start": cutoff + timedelta(days=1), "surface": "HARD", "won": True, "event_id": "future"},
    ]
    assert tennis._player_summary(prior, cutoff, "HARD") is None
    assert tennis._player_summary(contaminated, cutoff, "HARD") is None
