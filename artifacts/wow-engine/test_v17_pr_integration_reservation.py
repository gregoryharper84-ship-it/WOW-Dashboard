from __future__ import annotations

from datetime import datetime, timedelta, timezone

import pytest

from v17.pr_integration_reservation import (
    DEFAULT_RESERVATION_SECONDS,
    plan_integration,
)

NOW = datetime(2026, 11, 28, 12, 0, tzinfo=timezone.utc)
SHA = "a" * 40
OTHER = "b" * 40


def pull(**overrides):
    record = {
        "number": 2859,
        "head_sha": SHA,
        "state": "open",
        "draft": False,
        "review_state": "APPROVED",
        "checks": {"status": "SUCCESS", "sha": SHA},
        "accepted_head_sha": SHA,
        "behind_by": 0,
        "age_hours": 10.0,
    }
    record.update(overrides)
    return record


def test_ready_pull_owns_one_reservation_without_merge_authority():
    result = plan_integration([pull()], now=NOW)
    assert result["decision"] == "RESERVATION_GRANTED"
    assert result["selected_pull_number"] == 2859
    assert result["requires_refresh"] is False
    assert result["requires_fresh_acceptance"] is False
    assert result["reservation"]["pull_number"] == 2859
    assert result["can_execute"] is False
    assert result["terminal_authority"] == "V17_TERMINAL_REDUCER"


def test_behind_main_reserves_refresh_and_requires_fresh_acceptance():
    result = plan_integration([pull(behind_by=37)], now=NOW)
    assert result["decision"] == "RESERVATION_GRANTED"
    assert result["requires_refresh"] is True
    assert result["requires_fresh_acceptance"] is True
    assert result["pulls"][0]["state"] == "BEHIND_BASE"


def test_green_checks_on_an_older_head_never_select_the_pull():
    stale = pull(checks={"status": "SUCCESS", "sha": OTHER})
    result = plan_integration([stale], now=NOW)
    assert result["decision"] == "NO_ELIGIBLE_PULL"
    assert result["reservation"] is None
    assert result["pulls"][0]["state"] == "CHECKS_NOT_ON_CURRENT_HEAD"
    assert "REQUIRED_CHECKS_NOT_RUN_ON_CURRENT_HEAD" in result["pulls"][0]["blockers"]


def test_oldest_starved_pull_is_selected_before_newer_work():
    old = pull(number=2666, age_hours=500.0, behind_by=12)
    fresh = pull(number=2891, age_hours=4.0)
    result = plan_integration([fresh, old], now=NOW)
    assert result["selected_pull_number"] == 2666
    assert result["reservation"]["reason"] == "MERGE_STARVATION_PREVENTION"
    assert result["waiting"] == [2891]


def test_only_one_pull_holds_the_integration_window():
    first = pull(number=2859)
    second = pull(number=2864, head_sha=OTHER, checks={"status": "SUCCESS", "sha": OTHER},
                  accepted_head_sha=OTHER)
    first_pass = plan_integration([first, second], now=NOW)
    reservation = first_pass["reservation"]
    assert first_pass["selected_pull_number"] == 2859

    second_pass = plan_integration(
        [first, second], now=NOW + timedelta(seconds=30), active_reservation=reservation
    )
    assert second_pass["decision"] == "CONTINUE_ACTIVE_RESERVATION"
    assert second_pass["selected_pull_number"] == 2859
    assert second_pass["reservation"]["expires_at"] == reservation["expires_at"]
    assert second_pass["waiting"] == [2864]


def test_expired_reservation_is_reassigned_to_the_oldest_eligible_pull():
    expired = {
        "pull_number": 2891,
        "head_sha": SHA,
        "expires_at": (NOW - timedelta(minutes=5)).isoformat(),
    }
    result = plan_integration(
        [pull(number=2666, age_hours=300.0), pull(number=2891, age_hours=5.0)],
        now=NOW,
        active_reservation=expired,
    )
    assert result["decision"] == "RELEASE_AND_REASSIGN"
    assert result["selected_pull_number"] == 2666
    assert "INTEGRATION_RESERVATION_EXPIRED" not in result["blockers"]


def test_reservation_for_a_moved_head_is_released_not_honoured():
    reservation = {
        "pull_number": 2859,
        "head_sha": OTHER,
        "expires_at": (NOW + timedelta(minutes=10)).isoformat(),
    }
    result = plan_integration([pull()], now=NOW, active_reservation=reservation)
    assert result["decision"] == "RELEASE_AND_REASSIGN"
    assert result["reservation"]["head_sha"] == SHA


def test_closed_draft_and_unapproved_pulls_are_never_selectable():
    closed = pull(number=1, state="merged")
    draft = pull(number=2, draft=True)
    unapproved = pull(number=3, review_state="CHANGES_REQUESTED")
    result = plan_integration([closed, draft, unapproved], now=NOW)
    assert result["decision"] == "NO_ELIGIBLE_PULL"
    assert result["pulls"][0]["status"] == "CLOSED_OR_MERGED"
    assert 1 not in result["waiting"]


def test_unattested_inventory_fails_closed_instead_of_guessing():
    result = plan_integration([pull(behind_by=None)], now=NOW)
    assert result["decision"] == "BLOCKED_EVIDENCE"
    assert result["reservation"] is None
    assert result["blockers"] == ["PULL_0_BEHIND_BASE_UNATTESTED"]


def test_duplicate_pull_numbers_are_ambiguous_and_blocked():
    result = plan_integration([pull(), pull(number=2859, head_sha=OTHER)], now=NOW)
    assert result["decision"] == "BLOCKED_EVIDENCE"
    assert result["blockers"] == ["DUPLICATE_PULL_NUMBER"]


def test_malformed_reservation_blocks_a_new_reservation():
    result = plan_integration(
        [pull()], now=NOW, active_reservation={"pull_number": 2859, "expires_at": "soon"}
    )
    assert result["decision"] == "BLOCKED_EVIDENCE"
    assert result["blockers"] == ["INTEGRATION_RESERVATION_HEAD_SHA_INVALID"]


def test_starvation_risk_is_visible_for_waiting_work():
    waiting = pull(number=2859, age_hours=120.0, review_state="PENDING")
    selected = pull(number=2900, head_sha=OTHER, age_hours=2.0,
                    checks={"status": "SUCCESS", "sha": OTHER}, accepted_head_sha=OTHER)
    result = plan_integration([selected, waiting], now=NOW)
    assert result["selected_pull_number"] == 2900
    assert result["starvation_risk"] == [2859]


def test_selection_is_deterministic_for_equal_ages():
    a = pull(number=2900, head_sha=OTHER, age_hours=8.0,
             checks={"status": "SUCCESS", "sha": OTHER}, accepted_head_sha=OTHER)
    b = pull(number=2666, head_sha=OTHER, age_hours=8.0,
             checks={"status": "SUCCESS", "sha": OTHER}, accepted_head_sha=OTHER)
    assert plan_integration([a, b], now=NOW)["selected_pull_number"] == 2666
    assert plan_integration([b, a], now=NOW)["selected_pull_number"] == 2666


def test_reservation_window_must_be_bounded():
    with pytest.raises(ValueError, match="RESERVATION_SECONDS_OUT_OF_BOUNDS"):
        plan_integration([pull()], now=NOW, reservation_seconds=10**6)
    result = plan_integration([pull()], now=NOW)
    assert result["reservation"]["expires_at"] == (
        NOW + timedelta(seconds=DEFAULT_RESERVATION_SECONDS)
    ).isoformat().replace("+00:00", "Z")


def test_naive_now_is_rejected():
    with pytest.raises(ValueError, match="NOW_MUST_BE_TIMEZONE_AWARE"):
        plan_integration([pull()], now=datetime(2026, 11, 28, 12, 0))


def test_merge_conflict_is_a_code_repair_not_a_mechanical_refresh():
    conflict = pull(merge_state="DIRTY", behind_by=9, age_hours=200.0)
    result = plan_integration([conflict], now=NOW)
    assert result["decision"] == "RESERVATION_GRANTED"
    assert result["selected_pull_number"] == 2859
    assert result["requires_code_repair"] is True
    assert result["requires_refresh"] is False
    assert result["refresh_owner"] is None
    assert result["pulls"][0]["state"] == "MERGE_CONFLICT_REPAIR_REQUIRED"


def test_failing_required_checks_are_a_code_repair():
    failing = pull(checks={"status": "FAILURE", "sha": SHA}, behind_by=3)
    result = plan_integration([failing], now=NOW)
    assert result["selected_pull_number"] == 2859
    assert result["requires_code_repair"] is True
    assert result["requires_refresh"] is False
    assert result["pulls"][0]["state"] == "REQUIRED_CHECKS_FAILED_REPAIR_REQUIRED"


def test_behind_main_alone_is_owned_by_keep_current_not_a_repair():
    result = plan_integration(
        [pull(behind_by=21, merge_state="BEHIND", author_login="wow-engineering[bot]")],
        now=NOW,
    )
    assert result["requires_refresh"] is True
    assert result["requires_code_repair"] is False
    assert result["refresh_owner"] == "WOW_V17_ENGINEERING_PR_KEEP_CURRENT"
    assert result["refresh_owner_reason"] == "KEEP_CURRENT_SELECTS_THIS_PULL"


def test_ordinary_author_is_never_credited_to_the_bot_only_sweep():
    result = plan_integration(
        [pull(behind_by=21, merge_state="BEHIND", author_login="some-human")], now=NOW
    )
    assert result["decision"] == "RESERVATION_GRANTED"
    assert result["requires_refresh"] is True
    assert result["refresh_owner"] is None
    assert result["refresh_owner_reason"] == "NO_AUTOMATIC_REFRESH_OWNER_FOR_AUTHOR"


def test_unattested_author_cannot_be_credited_to_any_sweep():
    result = plan_integration([pull(behind_by=21, merge_state="BEHIND")], now=NOW)
    assert result["refresh_owner"] is None
    assert result["refresh_owner_reason"] == "AUTHOR_UNATTESTED"


def test_merge_state_not_attested_as_behind_is_not_credited_to_the_sweep():
    result = plan_integration(
        [pull(behind_by=21, author_login="wow-engineering[bot]")], now=NOW
    )
    assert result["refresh_owner"] is None
    assert result["refresh_owner_reason"] == "MERGE_STATE_NOT_ATTESTED_AS_BEHIND"


def test_unrecognised_merge_state_fails_closed():
    result = plan_integration([pull(merge_state="SOMETHING_NEW")], now=NOW)
    assert result["decision"] == "BLOCKED_EVIDENCE"
    assert result["blockers"] == ["PULL_0_MERGE_STATE_UNKNOWN"]
