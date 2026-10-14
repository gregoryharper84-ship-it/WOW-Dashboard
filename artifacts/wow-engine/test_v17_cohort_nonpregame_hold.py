"""Regression: a source-declared week with a postponed or cancelled game is not complete."""
from datetime import datetime, timedelta, timezone
from pathlib import Path
import sys

sys.path.insert(0, str(Path(__file__).resolve().parent))
from v17.cross_sport_acceptance_factory import (
    CohortPolicy, Fixture, Lane, SourceInventory, evaluate_cohort,
)


def _cohort(interrupted_status):
    now = datetime(2026, 10, 14, 12, tzinfo=timezone.utc)
    start = now + timedelta(days=1)
    fixtures = tuple(
        Fixture("NFL", f"game-{n}", start + timedelta(minutes=n), "SCHEDULED")
        for n in range(13)
    ) + (Fixture("NFL", "interrupted", start + timedelta(hours=2), interrupted_status),)
    return evaluate_cohort(
        Lane("NFL", "MONEYLINE", "scorePickRequest", "NFL_GAME_WIN_V17"),
        SourceInventory("source", "a" * 64, now, fixtures),
        CohortPolicy(13, start, start + timedelta(days=1)), now=now,
    )


def test_postponed_fixture_holds_even_when_13_others_eligible():
    result = _cohort("POSTPONED")
    assert result.status == "COHORT_HELD"
    assert result.eligible_count == 13
    assert "ACCEPTANCE_WEEK_NONPREGAME_EVENT" in result.blockers
    assert result.as_dict()["probability_publishable"] is False
    assert result.as_dict()["can_execute"] is False


def test_cancelled_fixture_holds_even_when_13_others_eligible():
    result = _cohort("CANCELLED")
    assert result.status == "COHORT_HELD"
    assert "ACCEPTANCE_WEEK_NONPREGAME_EVENT" in result.blockers


def test_full_fourteen_fixture_week_remains_eligible():
    result = _cohort("SCHEDULED")
    assert result.status == "COHORT_ELIGIBLE_FOR_TEST"
    assert len(result.event_ids) == 14
