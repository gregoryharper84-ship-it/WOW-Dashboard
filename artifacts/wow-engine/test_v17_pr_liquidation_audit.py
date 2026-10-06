from __future__ import annotations

from datetime import datetime, timezone

from v17.audit_pr_liquidation import PRState, build_receipt, classify


NOW = datetime(2026, 10, 6, 14, 30, tzinfo=timezone.utc)


def _state(**overrides):
    data = dict(
        number=100,
        title="fix: example",
        author="wow",
        head_ref="fix/example",
        head_sha="a" * 40,
        base_ref="main",
        draft=False,
        updated_at="2026-09-01T00:00:00Z",
        days_since_update=35,
        mergeable=True,
        mergeable_state="clean",
        check_total=5,
        check_success=5,
        check_pending=0,
        check_failed=(),
        governance_failed=(),
        statuses_pending=False,
        statuses_failed=False,
        explicit_supersession_refs=(),
        explicit_supersession_proven=False,
    )
    data.update(overrides)
    return PRState(**data)


def test_age_alone_never_proves_supersession() -> None:
    bucket, reason = classify(_state(days_since_update=90))
    assert bucket == "MERGE_CANDIDATE"
    assert "global mutation/promotion owner" in reason


def test_pending_ci_is_not_merge_ready() -> None:
    bucket, _ = classify(
        _state(
            check_success=4,
            check_pending=1,
            statuses_pending=True,
        )
    )
    assert bucket == "CI_IN_PROGRESS"


def test_failing_ci_requires_repair_not_closure() -> None:
    bucket, reason = classify(
        _state(
            check_success=4,
            check_failed=("unit-tests",),
        )
    )
    assert bucket == "CI_REPAIR_REQUIRED"
    assert "not closure proof" in reason


def test_governance_only_failure_routes_to_recertification() -> None:
    name = "Trusted exact-head engineering governance"
    bucket, _ = classify(
        _state(
            check_success=4,
            check_failed=(name,),
            governance_failed=(name,),
        )
    )
    assert bucket == "GOVERNANCE_RECERTIFY"


def test_explicit_proven_supersession_is_only_auto_close_candidate() -> None:
    bucket, reason = classify(
        _state(
            explicit_supersession_refs=(1428,),
            explicit_supersession_proven=True,
        )
    )
    assert bucket == "PROVEN_SUPERSEDED"
    assert "#1428" in reason


def test_dirty_head_requires_rebase_not_immediate_close() -> None:
    bucket, _ = classify(
        _state(
            mergeable=False,
            mergeable_state="dirty",
        )
    )
    assert bucket == "REBASE_REQUIRED"


def test_no_checks_is_ci_not_triggered() -> None:
    bucket, _ = classify(
        _state(
            check_total=0,
            check_success=0,
        )
    )
    assert bucket == "CI_NOT_TRIGGERED"


def test_receipt_enforces_wip_cap_without_auto_merge_authority() -> None:
    states = [_state(number=i) for i in range(1, 14)]
    receipt = build_receipt(states, 12, NOW)
    assert receipt["total_open_prs_targeting_main"] == 13
    assert receipt["wip_exceeded"] is True
    assert receipt["excess_pr_reduction_target"] == 1
    assert receipt["new_pr_creation_allowed_for_support_lanes"] is False
    assert receipt["can_execute"] is False
    assert receipt["terminal_authority"] == "V17_TERMINAL_REDUCER"
    assert receipt["safe_action_policy"]["MERGE_CANDIDATE"].endswith("no auto-merge.")
