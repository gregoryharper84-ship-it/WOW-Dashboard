"""Restart, concurrent attempt, and safety regressions for durable dispatch."""
from dataclasses import replace
from v17.engineering_continuity_outbox import Proposal, Reservation, dispatch_once

SHA = "a" * 40
HEAD = "b" * 40
P = Proposal(incident=823, main_sha=SHA, action="DISPATCH_NEW_REPAIR")


class SharedAtomicStore:
    """Deterministic stand-in for the REQUIRED real DB-CAS store."""
    def __init__(self):
        self.reservation = None
        self.armed = False
        self.stored = []
        self.fail = False
        self.arm_error = False
        self.mark_error = False
        self.bad_reservation = False

    def reserve(self, p):
        if self.fail:
            raise RuntimeError("database unavailable")
        if self.reservation is not None:
            return "IN_FLIGHT", None
        self.reservation = Reservation(
            p.incident, "durable_intent_token_00000000001", p.main_sha,
            p.action, p.pr_number, p.pr_head_sha, 5
        )
        if self.bad_reservation:
            return "RESERVED", replace(self.reservation, main_sha=HEAD)
        return "RESERVED", self.reservation

    def arm_once(self, r):
        if self.arm_error:
            raise RuntimeError("CAS unavailable")
        if self.armed:
            return False
        self.armed = True
        return True

    def mark_result(self, r, code, run_id):
        if self.mark_error:
            raise RuntimeError("DB write failed")
        self.stored.append((code, run_id))
        return True


class Sender:
    def __init__(self, run_id=None, fails=False):
        self.calls = 0
        self.run_id = run_id
        self.fails = fails

    def __call__(self, p, r):
        self.calls += 1
        if self.fails:
            raise TimeoutError("ambiguous provider status")
        return self.run_id


def cycle(p=P, *, store=None, sender=None, fence=None, main=None, pr_head=None):
    store = store or SharedAtomicStore()
    sender = sender or Sender()
    return dispatch_once(
        p, store, sender,
        writer_fence=fence if fence is not None else lambda r: True,
        current_main=main if main is not None else lambda: SHA,
        current_pr_head=pr_head
    )


def test_ack_without_run_receipt_remains_uncorrelated():
    db = SharedAtomicStore()
    o = cycle(store=db)
    assert (o.code, o.attempted, o.workflow_run_id) == (
        "ATTEMPT_ACK_UNCORRELATED", True, None
    )
    assert db.stored == [("ATTEMPT_ACK_UNCORRELATED", None)]


def test_genuine_run_id_still_unverified():
    o = cycle(sender=Sender(run_id=8819))
    assert o.code == "ATTEMPT_ACK_CORRELATED_UNVERIFIED"
    assert o.workflow_run_id == 8819


def test_restart_duplicate_does_not_dispatch():
    shared = SharedAtomicStore()
    first = Sender()
    assert cycle(store=shared, sender=first).attempted is True
    second = Sender()
    assert cycle(store=shared, sender=second).code == (
        "PRIOR_DISPATCH_REQUIRES_RECONCILIATION"
    )
    assert first.calls == 1 and second.calls == 0


def test_crash_after_arm_before_post_is_not_resubmitted():
    shared = SharedAtomicStore()
    shared.reserve(P)
    assert shared.arm_once(shared.reservation)
    sender = Sender()
    assert cycle(store=shared, sender=sender).code == (
        "PRIOR_DISPATCH_REQUIRES_RECONCILIATION"
    )
    assert sender.calls == 0


def test_timeout_ambiguous_never_retries():
    db = SharedAtomicStore()
    sender = Sender(fails=True)
    assert cycle(store=db, sender=sender).code == "ATTEMPT_AMBIGUOUS"
    assert db.stored == [("ATTEMPT_AMBIGUOUS", None)]
    assert cycle(store=db, sender=sender).code == (
        "PRIOR_DISPATCH_REQUIRES_RECONCILIATION"
    )
    assert sender.calls == 1


def test_store_unavailable_does_not_post():
    db = SharedAtomicStore()
    db.fail = True
    s = Sender()
    assert cycle(store=db, sender=s).code == "OUTBOX_STORE_UNAVAILABLE"
    assert s.calls == 0


def test_stale_main_refuses_before_arming():
    db = SharedAtomicStore()
    s = Sender()
    assert cycle(store=db, sender=s, main=lambda: HEAD).code == (
        "MAIN_CHANGED_BEFORE_DISPATCH"
    )
    assert s.calls == 0 and not db.armed


def test_lease_fence_lost_prevents_post():
    db = SharedAtomicStore()
    s = Sender()
    assert cycle(store=db, sender=s, fence=lambda r: False).code == (
        "WRITER_FENCE_UNVERIFIED"
    )
    assert s.calls == 0 and not db.armed


def test_reservation_mismatch_cannot_rewrite_incident():
    db = SharedAtomicStore()
    db.bad_reservation = True
    s = Sender()
    assert cycle(store=db, sender=s).code == "OUTBOX_RESERVATION_CONTRADICTORY"
    assert s.calls == 0


def test_arm_failure_refuses_post():
    db = SharedAtomicStore()
    db.arm_error = True
    s = Sender()
    assert cycle(store=db, sender=s).code == "OUTBOX_FENCE_OR_ARM_UNKNOWN"
    assert s.calls == 0


def test_post_ack_mark_failure_is_not_retried():
    db = SharedAtomicStore()
    db.mark_error = True
    s = Sender()
    assert cycle(store=db, sender=s).code == (
        "OUTBOX_RESULT_NOT_DURABLE_RECONCILE"
    )
    assert cycle(store=db, sender=s).code == (
        "PRIOR_DISPATCH_REQUIRES_RECONCILIATION"
    )
    assert s.calls == 1


def test_repair_same_branch_requires_exact_pr_identity():
    db = SharedAtomicStore()
    p = Proposal(823, SHA, "REPAIR_SAME_BRANCH", pr_number=77, pr_head_sha=HEAD)
    o = cycle(p, store=db, pr_head=lambda _: HEAD)
    assert o.attempted is True
    assert db.reservation.pr_number == 77
    assert db.reservation.pr_head_sha == HEAD


def test_invalid_or_malformed_proposals_do_not_reserve():
    invalid = [
        replace(P, incident=True), replace(P, incident=-1),
        replace(P, main_sha="not-sha"), replace(P, action="MERGE"),
        replace(P, action="DEPLOY"), replace(P, pr_number=99),
        replace(P, action="REPAIR_SAME_BRANCH"),
        replace(P, action="REPAIR_SAME_BRANCH", pr_number=True, pr_head_sha=HEAD),
        replace(P, action="REPAIR_SAME_BRANCH", pr_number=99, pr_head_sha="bad"),
    ]
    for p in invalid:
        db, s = SharedAtomicStore(), Sender()
        assert cycle(p, store=db, sender=s).code == "PROPOSAL_INVALID"
        assert db.reservation is None and s.calls == 0


def test_boolean_run_id_is_not_valid_workflow_receipt():
    assert cycle(sender=Sender(run_id=True)).code == "ATTEMPT_AMBIGUOUS"

def test_repair_head_must_be_independently_rechecked():
    p = Proposal(823, SHA, "REPAIR_SAME_BRANCH", pr_number=77, pr_head_sha=HEAD)
    assert cycle(p).code == "PR_HEAD_RECHECK_UNAVAILABLE"
    assert cycle(p, pr_head=lambda _: SHA).code == "PR_CHANGED_BEFORE_DISPATCH"
    assert cycle(p, pr_head=lambda _: HEAD).attempted is True


def test_lease_lost_after_atomic_arm_never_calls_provider():
    sequence = iter([True, False])
    db, sender = SharedAtomicStore(), Sender()
    assert cycle(store=db, sender=sender, fence=lambda _: next(sequence)).code == (
        "FENCE_LOST_AFTER_ARM_RECONCILE"
    )
    assert db.armed and sender.calls == 0
    assert cycle(store=db, sender=sender).code == (
        "PRIOR_DISPATCH_REQUIRES_RECONCILIATION"
    )


def test_current_main_changes_after_arm_never_calls_provider():
    main = iter([SHA, HEAD])
    db, sender = SharedAtomicStore(), Sender()
    assert cycle(store=db, sender=sender, main=lambda: next(main)).code == (
        "MAIN_CHANGED_AFTER_ARM_RECONCILE"
    )
    assert db.armed and sender.calls == 0
