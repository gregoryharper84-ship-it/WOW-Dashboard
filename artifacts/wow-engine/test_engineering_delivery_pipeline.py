"""Offline tests for fail-closed single-controller resume decisions."""
import importlib.util
from pathlib import Path
import unittest

MODULE = Path(__file__).parent / "v17" / "engineering_delivery_pipeline.py"
spec = importlib.util.spec_from_file_location("engineering_delivery_pipeline", MODULE)
mod = importlib.util.module_from_spec(spec)
spec.loader.exec_module(mod)
SHA = "a" * 40
REPO = "gregoryharper84-ship-it/WOW-Dashboard"


def pr(**overrides):
    v = {"number": 45, "state": "open", "draft": False,
         "head": {"sha": SHA, "repo": {"full_name": REPO}},
         "base": {"ref": "main", "repo": {"full_name": REPO}}}
    v.update(overrides)
    return v


def check(name, app, status="completed", conclusion="success"):
    return {"name": name, "app": {"id": app},
            "status": status, "conclusion": conclusion}


class DeliveryTests(unittest.TestCase):
    def test_no_check_requires_real_review_evidence(self):
        r = mod.reduce_pr(pr(), [], SHA)
        self.assertEqual(r["decision"], "EVIDENCE_PENDING")
        self.assertFalse(r["merge_authorized"])

    def test_fake_qa_app_is_rejected(self):
        r = mod.reduce_pr(pr(), [check(mod.QA_CHECK, 1234)], SHA)
        self.assertEqual(r["decision"], "QA_IDENTITY_INVALID")

    def test_qa_failure_is_not_semantic_pass(self):
        r = mod.reduce_pr(pr(), [check(mod.QA_CHECK, mod.QA_APP_ID, conclusion="failure")], SHA)
        self.assertEqual(r["decision"], "INDEPENDENT_QA_NOT_ACCEPTED")

    def test_pending_qa_is_not_pass(self):
        r = mod.reduce_pr(pr(), [check(mod.QA_CHECK, mod.QA_APP_ID, status="in_progress", conclusion=None)], SHA)
        self.assertEqual(r["decision"], "EVIDENCE_PENDING")

    def test_qa_success_is_not_release_success(self):
        r = mod.reduce_pr(pr(), [check(mod.QA_CHECK, mod.QA_APP_ID)], SHA)
        self.assertEqual(r["decision"], "RELEASE_EVIDENCE_PENDING")

    def test_release_hold_is_not_pass(self):
        r = mod.reduce_pr(pr(), [check(mod.QA_CHECK, mod.QA_APP_ID),
                          check("WOW Release Authority", mod.RELEASE_APP_ID, conclusion="failure")], SHA)
        self.assertEqual(r["decision"], "RELEASE_NOT_ACCEPTED")

    def test_both_apps_success_still_need_owner_review(self):
        r = mod.reduce_pr(pr(), [check(mod.QA_CHECK, mod.QA_APP_ID),
                          check("WOW Release Authority", mod.RELEASE_APP_ID)], SHA)
        self.assertEqual(r["decision"], "REVIEW_STAGE_ACCEPTED")
        self.assertEqual(r["next_action"], "VERIFY_NATIVE_CHECKS_AND_OWNER_APPROVAL")
        self.assertFalse(r["review_approved"])

    def test_stale_sha_rejected(self):
        self.assertEqual(mod.reduce_pr(pr(), [], "b" * 40)["decision"], "SOURCE_HEAD_UNSTABLE")

    def test_foreign_pr_rejected(self):
        x = pr(head={"sha": SHA, "repo": {"full_name": "attacker/repo"}})
        self.assertEqual(mod.reduce_pr(x, [], SHA)["decision"], "SOURCE_HEAD_UNSTABLE")

    def test_draft_pr_rejected(self):
        self.assertEqual(mod.reduce_pr(pr(draft=True), [], SHA)["decision"], "SOURCE_HEAD_UNSTABLE")

    def test_bad_inventory_rejected(self):
        with self.assertRaises(ValueError):
            mod.reduce_pr(pr(), [{}] * 1001, SHA)

    def test_conflicting_security_checks_rejected(self):
        with self.assertRaisesRegex(ValueError, "CONFLICTING_CHECK_EVIDENCE"):
            mod.reduce_pr(pr(), [check(mod.QA_CHECK, mod.QA_APP_ID),
                                check(mod.QA_CHECK, 123)], SHA)

    def test_merge_alone_never_completes(self):
        x = mod.reduce_completion(merge_sha=SHA, live_sha=None,
                                  verified_sha=None, healthy_route=False, persisted_result=False)
        self.assertEqual(x["decision"], "DEPLOY_MISMATCH")

    def test_deploy_alone_never_completes(self):
        x = mod.reduce_completion(merge_sha=SHA, live_sha=SHA,
                                  verified_sha=None, healthy_route=False, persisted_result=False)
        self.assertEqual(x["decision"], "PRODUCTION_EVIDENCE_PENDING")

    def test_persistence_missing_never_completes(self):
        x = mod.reduce_completion(merge_sha=SHA, live_sha=SHA,
                                  verified_sha=SHA, healthy_route=True, persisted_result=False)
        self.assertEqual(x["decision"], "PRODUCTION_EVIDENCE_PENDING")

    def test_verified_full_chain_completes(self):
        x = mod.reduce_completion(merge_sha=SHA, live_sha=SHA,
                                  verified_sha=SHA, healthy_route=True, persisted_result=True)
        self.assertEqual(x["decision"], "COMPLETE")
        self.assertFalse(x["can_execute"])


if __name__ == "__main__":
    unittest.main()
