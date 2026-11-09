"""Adjudication of the final provisional-participation recheck.

Only a typed official confirmation may discharge an MLB probable-starter
obligation. These tests pin the fail-closed side of that rule as hard as the
clearing side.
"""
from __future__ import annotations

from datetime import datetime, timedelta, timezone

from v17.player_availability_gate import (
    AVAILABILITY_AVAILABLE,
    AVAILABILITY_UNAVAILABLE,
    PLAYER_AVAILABILITY_RECHECK_REQUIRED,
    PLAYER_RULED_OUT,
)
from v17.provisional_availability_recheck import (
    RECHECK_CONFIRMED,
    RECHECK_IDENTITY_MISMATCH,
    RECHECK_INCONCLUSIVE,
    RECHECK_RULED_OUT,
    RECHECK_WINDOW,
    recheck_is_due,
    resolve_provisional_recheck,
)


def _provisional(**overrides):
    block = {
        "status": "AVAILABLE",
        "basis": "PROBABLE_STARTER",
        "confirmation": "PROBABLE",
        "source": "MLB_STATS_API_SCHEDULE_PROBABLE_PITCHER",
        "captured_at": "2026-11-09T12:00:00+00:00",
        "fresh": True,
        "requires_revalidation": True,
        "provenance": {
            "athlete_id": "660271",
            "event_id": "823001",
            "team": "LAD",
        },
    }
    block.update(overrides)
    return block


def _observed(**overrides):
    block = {
        "status": "AVAILABLE",
        "basis": "CONFIRMED_STARTER",
        "confirmation": "CONFIRMED",
        "source": "MLB_STATS_API_OFFICIAL_STARTER_RECHECK",
        "captured_at": "2026-11-09T21:00:00+00:00",
        "fresh": True,
        "report_complete": True,
        "athlete_id": "660271",
        "event_id": "823001",
        "team": "LAD",
    }
    block.update(overrides)
    return block


def test_confirmed_official_start_discharges_the_obligation():
    checked = datetime(2026, 11, 9, 21, 5, tzinfo=timezone.utc)
    decision, receipt = resolve_provisional_recheck(
        provisional=_provisional(),
        observed=_observed(),
        now=checked,
    )

    assert decision.status == AVAILABILITY_AVAILABLE
    assert decision.basis == "CONFIRMED_STARTER"
    assert decision.requires_revalidation is False
    assert decision.blocker is None
    assert receipt["status"] == RECHECK_CONFIRMED
    assert receipt["checked_at"] == checked.isoformat()
    assert receipt["can_execute"] is False


def test_still_probable_keeps_the_recheck_blocker():
    decision, receipt = resolve_provisional_recheck(
        provisional=_provisional(),
        observed=_observed(basis="PROBABLE_STARTER", confirmation="PROBABLE"),
    )

    assert decision.status != AVAILABILITY_AVAILABLE
    assert decision.blocker == PLAYER_AVAILABILITY_RECHECK_REQUIRED
    assert decision.requires_revalidation is True
    assert receipt["status"] == RECHECK_INCONCLUSIVE


def test_report_marked_fresh_cannot_reuse_old_or_future_capture():
    checked = datetime(2026, 11, 9, 21, 5, tzinfo=timezone.utc)
    for timestamp in (
        "2026-11-09T11:59:00+00:00",
        "2026-11-09T12:00:00+00:00",
        "2026-11-09T22:00:00+00:00",
        "2026-11-09T21:00:00",
        "bad-date",
    ):
        decision, receipt = resolve_provisional_recheck(
            provisional=_provisional(),
            observed=_observed(captured_at=timestamp),
            now=checked,
        )
        assert decision.status != AVAILABILITY_AVAILABLE
        assert decision.blocker == PLAYER_AVAILABILITY_RECHECK_REQUIRED
        assert decision.requires_revalidation is True
        assert receipt["status"] == RECHECK_INCONCLUSIVE
        assert receipt["reason"] == "PROVISIONAL_RECHECK_CAPTURE_INVALID_OR_NOT_NEWER"


def test_report_completeness_is_required_before_positive_clearance():
    checked = datetime(2026, 11, 9, 21, 5, tzinfo=timezone.utc)
    for completeness in (None, False):
        decision, receipt = resolve_provisional_recheck(
            provisional=_provisional(),
            observed=_observed(report_complete=completeness),
            now=checked,
        )
        assert decision.status != AVAILABILITY_AVAILABLE
        assert decision.blocker == PLAYER_AVAILABILITY_RECHECK_REQUIRED
        assert receipt["status"] == RECHECK_INCONCLUSIVE


def test_stale_recheck_evidence_cannot_clear():
    decision, receipt = resolve_provisional_recheck(
        provisional=_provisional(),
        observed=_observed(fresh=False),
    )

    assert decision.status != AVAILABILITY_AVAILABLE
    assert decision.blocker == PLAYER_AVAILABILITY_RECHECK_REQUIRED
    assert receipt["status"] == RECHECK_INCONCLUSIVE


def test_recheck_for_another_event_cannot_clear_this_row():
    decision, receipt = resolve_provisional_recheck(
        provisional=_provisional(),
        observed=_observed(event_id="823999"),
    )

    assert decision.status != AVAILABILITY_AVAILABLE
    assert decision.blocker == PLAYER_AVAILABILITY_RECHECK_REQUIRED
    assert receipt["status"] == RECHECK_IDENTITY_MISMATCH
    assert receipt["mismatched_fields"] == ["event_id"]


def test_rule_out_at_recheck_becomes_a_participation_ruling():
    decision, receipt = resolve_provisional_recheck(
        provisional=_provisional(),
        observed=_observed(status="OUT", designation="OUT"),
    )

    assert decision.status == AVAILABILITY_UNAVAILABLE
    assert decision.blocker == PLAYER_RULED_OUT
    assert receipt["status"] == RECHECK_RULED_OUT


def test_recheck_window_is_pregame_and_short():
    now = datetime(2026, 11, 9, 18, 0, tzinfo=timezone.utc)

    assert recheck_is_due(now + timedelta(hours=5), now) is True
    assert recheck_is_due(now + timedelta(hours=7), now) is False
    assert recheck_is_due(now - timedelta(minutes=1), now) is False
    assert RECHECK_WINDOW == timedelta(hours=6)
