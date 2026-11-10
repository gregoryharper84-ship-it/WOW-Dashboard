from v17.p0_recovery_controller import Blocker, may_release, plan_recovery


def test_review_missing_prepares_parallel_work_without_approval():
    result = plan_recovery("REVIEW_MISSING")
    assert result.blocker is Blocker.REVIEW_MISSING
    assert result.release_hold is True
    assert result.can_execute is False
    assert "independent review" in result.safe_workaround
    assert "parallel" in result.parallel_next_action


def test_provider_failure_never_becomes_pass():
    result = plan_recovery("PROVIDER_FAILURE")
    assert result.release_hold
    assert "HOLD" in result.safe_workaround


def test_unknown_failure_stays_closed():
    assert plan_recovery("NOT_A_REAL_ERROR").blocker is Blocker.UNKNOWN
    assert plan_recovery(None).can_execute is False


def test_release_requires_all_independent_receipts():
    gates = dict(exact_head_ci=True, independent_review=True,
                 owner_authorized=True, calibrated=True,
                 deployed_revision_matches=True, action_verified=True)
    assert may_release(**gates)
    for key in gates:
        failed = dict(gates)
        failed[key] = False
        assert not may_release(**failed), key
