"""Offline adversarial tests for no-duplicate, fenced autonomous repair admission."""
from dataclasses import replace
from v17.engineering_continuity_controller import Snapshot, Lease, decide, advance

SHA = "a" * 40
HEAD = "b" * 40
BASE = Snapshot(incident=823, main_sha=SHA, incident_open=True,
                verified_writer_available=True, lease_service_ready=True)


class Port:
    def __init__(self, snapshot=BASE):
        self.snapshot, self.events = snapshot, []
        self.lease_ok, self.change_during_claim = True, False
        self.dispatch_fails, self.missing_receipt = False, False
        self.checkpoint_fails = False

    def load(self, incident):
        self.events.append("load")
        return self.snapshot

    def checkpoint(self, incident, code):
        self.events.append(("checkpoint", code))
        if self.checkpoint_fails:
            raise RuntimeError("durable receipt unavailable")
        if code.startswith("INTENT_"):
            self.snapshot = replace(self.snapshot, pending_dispatch=True)

    def claim_dispatch_admission(self, incident, sha):
        self.events.append("claim")
        if self.change_during_claim:
            self.snapshot = replace(self.snapshot, main_sha="c" * 40)
        return Lease(incident, "trusted-app-worker", 4, "opaque-fence")

    def check_fence(self, lease):
        self.events.append("fence")
        return self.lease_ok

    def dispatch(self, incident, action, main_sha, lease, pr_number):
        self.events.append(("dispatch", action, pr_number))
        if self.dispatch_fails:
            raise TimeoutError("ambiguous provider response")
        return "" if self.missing_receipt else "trusted-action-receipt-id"

    def release_dispatch_admission(self, lease):
        self.events.append("release")


def test_new_repair_dispatch_under_fence():
    p = Port()
    assert advance(p, 823).code == "DISPATCH_SUBMITTED_UNVERIFIED"
    assert p.events.count("fence") == 2
    assert ("dispatch", "DISPATCH_NEW_REPAIR", None) in p.events
    assert p.events[-1] == "release"


def test_existing_pr_failures_require_app_authored_same_branch():
    s = replace(BASE, pr_state="DRAFT", pr_number=99, pr_head_sha=HEAD, checks="FAILED")
    assert decide(s).code == "PR_WRITER_PROVENANCE_UNVERIFIED"
    s = replace(s, pr_app_authored=True)
    assert decide(s).code == "REPAIR_SAME_BRANCH"
    p = Port(s)
    assert advance(p, 823).code == "DISPATCH_SUBMITTED_UNVERIFIED"
    assert ("dispatch", "REPAIR_SAME_BRANCH", 99) in p.events


def test_existing_pr_waits_when_no_real_failure():
    for checks, qa in (("PENDING", "PENDING"), ("PASS", "PENDING"), ("PASS", "PASS")):
        s = replace(BASE, pr_state="READY", pr_number=88, pr_head_sha=HEAD,
                    checks=checks, qa=qa, pr_app_authored=True)
        assert decide(s).mutation is False


def test_trust_root_requires_independent_app_reproposal():
    assert decide(replace(BASE, protected_change=True)).code == "TRUST_ROOT_REQUIRES_INDEPENDENT_PROPOSAL"
    s = replace(BASE, pr_state="DRAFT", pr_number=99, pr_head_sha=HEAD,
                checks="FAILED", pr_app_authored=True, protected_change=True)
    assert decide(s).code == "TRUST_ROOT_REPAIR_REQUIRES_APP_REPROPOSAL"


def test_lease_or_provider_missing_never_dispatches():
    for s in (replace(BASE, lease_service_ready=False),
              replace(BASE, verified_writer_available=False),
              replace(BASE, worker_busy=True), replace(BASE, attempts=3)):
        p = Port(s)
        assert advance(p, 823).mutation is False
        assert not any(isinstance(e, tuple) and e[0] == "dispatch" for e in p.events)


def test_fence_lost_before_dispatch():
    p = Port()
    p.lease_ok = False
    assert advance(p, 823).code == "LEASE_LOST_BEFORE_DISPATCH"
    assert "dispatch" not in str(p.events)
    assert p.events[-1] == "release"


def test_main_changed_after_claim_blocks_dispatch():
    p = Port()
    p.change_during_claim = True
    assert advance(p, 823).code == "MAIN_CHANGED_BEFORE_DISPATCH"
    assert "dispatch" not in str(p.events)


def test_checkpoint_missing_blocks_before_lease():
    p = Port()
    p.checkpoint_fails = True
    try:
        advance(p, 823)
    except RuntimeError:
        pass
    else:
        assert False, "must fail closed"
    assert "claim" not in p.events


def test_ambiguous_dispatch_does_not_retry():
    p = Port()
    p.dispatch_fails = True
    assert advance(p, 823).code == "DISPATCH_ACK_AMBIGUOUS_RECONCILE"
    assert sum(isinstance(e, tuple) and e[0] == "dispatch" for e in p.events) == 1
    assert p.events[-1] == "release"


def test_missing_receipt_not_treated_as_accepted():
    p = Port()
    p.missing_receipt = True
    assert advance(p, 823).code == "DISPATCH_RECEIPT_MISSING_RECONCILE"


def test_incomplete_or_contradictory_inputs_hold():
    invalid = [replace(BASE, main_sha="xyz"), replace(BASE, incident=0),
               replace(BASE, attempts=True), replace(BASE, pr_state="MAGIC"),
               replace(BASE, pr_state="READY", pr_number=None),
               replace(BASE, pr_number=23),
               replace(BASE, action_receipt_verified=True)]
    assert all(not decide(x).mutation for x in invalid)


def test_model_delivery_not_inferred_from_merged_or_deploy():
    s = replace(BASE, pr_state="MERGED", pr_number=100, pr_head_sha=HEAD,
                deployed_sha="c" * 40)
    assert decide(s).code == "WAIT_INDEPENDENT_RELEASE"
    s = replace(s, deployed_sha=SHA)
    assert decide(s).code == "VERIFY_ACTUAL_ACTION_READ_ONLY"
    s = replace(s, action_receipt_verified=True)
    assert decide(s).code == "READY_FOR_INDEPENDENT_CLOSURE"
    assert decide(s).terminal is False


def test_pending_dispatch_prevents_duplicate_worker():
    p = Port(replace(BASE, pending_dispatch=True))
    assert advance(p, 823).code == "DISPATCH_PENDING_RECONCILIATION"
    assert "claim" not in p.events


def test_ambiguous_ack_has_persistent_reconciliation_receipt():
    p = Port()
    p.dispatch_fails = True
    advance(p, 823)
    assert ("checkpoint", "DISPATCH_ACK_AMBIGUOUS_RECONCILE") in p.events
