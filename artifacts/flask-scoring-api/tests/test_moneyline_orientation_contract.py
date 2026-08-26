"""Regression coverage for the canonical binary moneyline orientation contract.

These checks cover representation and handoff only. They intentionally do not
change sport-model weights, calibration, labels, or execution authority.
"""
from __future__ import annotations

import pytest
from datetime import datetime, timedelta, timezone

from gate_engine.moneyline.pipeline import (
    _select_binary_candidate_probability,
    _validate_binary_probability_pair,
    run_moneyline_pipeline,
)
from gate_engine.moneyline.market_snapshot import (
    BookQuote,
    MoneylineMarketSnapshot,
    attach_snapshot_to_enrichment,
)
from gate_engine.daily_orchestrator import operator_daily_status


def _enrichment() -> dict:
    return {
        "home_win_pct": 0.60,
        "away_win_pct": 0.40,
        "home_power": 0.60,
        "away_power": 0.40,
        "game_log": [{"result": "W"}] * 12,
        "lineup_confirmed": True,
        "starter_confirmed": True,
    }


@pytest.mark.parametrize(
    ("away_team", "home_team"),
    [
        ("Cleveland Guardians", "Los Angeles Angels"),
        ("Cincinnati Reds", "San Francisco Giants"),
        ("Chicago Cubs", "Arizona Diamondbacks"),
        ("Colorado Rockies", "Washington Nationals"),
    ],
)
def test_mlb_away_candidate_uses_canonical_away_probability_once(
    away_team: str,
    home_team: str,
) -> None:
    row = {
        "sport": "MLB",
        "team": away_team,
        "opponent": home_team,
        "home_team": home_team,
        "away_team": away_team,
        "market_type": "h2h",
        "event_id": f"{away_team[:3]}-{home_team[:3]}",
        "slate_date": "2026-08-24",
    }
    result = run_moneyline_pipeline(row, _enrichment(), n_sims=100, seed=17)

    pair = result.sport_model["post_sim_home_away_probability"]
    assert result.outputs.independent_probability == round(pair["AWAY"], 4)
    assert abs(pair["HOME"] + pair["AWAY"] - 1.0) <= 0.01
    assert not any("inversion_applied" in note for note in result.sport_model["notes"])
    assert result.can_execute is False


def test_home_candidate_uses_same_canonical_pair_without_second_inversion() -> None:
    row = {
        "sport": "MLB",
        "team": "Los Angeles Angels",
        "opponent": "Cleveland Guardians",
        "home_team": "Los Angeles Angels",
        "away_team": "Cleveland Guardians",
        "market_type": "h2h",
        "event_id": "LAA-CLE",
        "slate_date": "2026-08-24",
    }
    result = run_moneyline_pipeline(row, _enrichment(), n_sims=100, seed=17)
    pair = result.sport_model["post_sim_home_away_probability"]
    assert result.outputs.independent_probability == round(pair["HOME"], 4)
    assert abs(pair["HOME"] + pair["AWAY"] - 1.0) <= 0.01
    assert result.can_execute is False


@pytest.mark.parametrize(
    ("home", "away"),
    [
        (None, 0.4),
        (1.01, -0.01),
        (0.70, 0.20),
    ],
)
def test_incomplete_or_inconsistent_binary_pair_is_rejected(
    home: float | None,
    away: float,
) -> None:
    pair, error = _validate_binary_probability_pair(home, away)
    assert pair is None
    assert error and error.startswith("PROBABILITY_ORIENTATION_CONTRACT_BREACH")


def test_snapshot_requires_reconciled_two_sided_no_vig_pair() -> None:
    snap = MoneylineMarketSnapshot(
        event_id="orientation-test",
        sport="MLB",
        home_team="Los Angeles Angels",
        away_team="Cleveland Guardians",
    )
    snap.books.append(BookQuote(
        bookmaker="book-a",
        team="Los Angeles Angels",
        american_odds=-135,
    ))
    _, blocked_snap, breached = attach_snapshot_to_enrichment(
        {},
        snap,
        row={
            "team": "Los Angeles Angels",
            "opponent": "Cleveland Guardians",
        },
    )
    assert breached is True
    assert blocked_snap.status == "MARKET_PIPELINE_CONTRACT_BREACH"
    assert any(item["reason"] == "ONE_SIDED_MARKET" for item in blocked_snap.dropped)


def test_direct_snapshot_is_rechecked_for_staleness_before_scoring() -> None:
    stale = (datetime.now(timezone.utc) - timedelta(hours=25)).isoformat()
    snap = MoneylineMarketSnapshot(
        event_id="stale-direct",
        sport="MLB",
        home_team="Home",
        away_team="Away",
    )
    snap.books.extend([
        BookQuote("book-a", "Home", -120, retrieved_at=stale),
        BookQuote("book-a", "Away", 100, retrieved_at=stale),
    ])
    _, blocked_snap, breached = attach_snapshot_to_enrichment(
        {}, snap, row={"team": "Home", "opponent": "Away"}
    )
    assert breached is True
    assert blocked_snap.status == "MARKET_PIPELINE_CONTRACT_BREACH"
    assert any(item["reason"] == "STALE_SNAPSHOT_QUOTE" for item in blocked_snap.dropped)


@pytest.mark.parametrize(
    ("raw_status", "kwargs", "expected"),
    [
        ("PARTIAL", {}, "DEGRADED"),
        ("RECONCILIATION_WARNING", {}, "DATA_ERROR"),
        ("FAILED", {"failure_reason": "RUNNER_EXCEPTION_RuntimeError"}, "ENGINE_ERROR"),
        ("COMPLETE", {"counts": {"playable_count": 0}}, "NO_PLAY"),
        ("COMPLETE", {"counts": {"playable_count": 1}}, "COMPLETE"),
    ],
)
def test_operator_daily_status_has_only_terminal_public_vocabulary(
    raw_status: str,
    kwargs: dict,
    expected: str,
) -> None:
    assert operator_daily_status(raw_status, **kwargs) == expected


def test_orientation_selection_reads_pair_without_complementing() -> None:
    class AwayOrientation:
        resolved = True
        is_home = False

    selected, error = _select_binary_candidate_probability(
        {"HOME": 0.63, "AWAY": 0.37},
        orientation=AwayOrientation(),
    )
    assert error is None
    assert selected == 0.37