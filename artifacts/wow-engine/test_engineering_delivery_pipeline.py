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
    row = {"name": name, "app": {"id": app},
           "status": status, "conclusion": conclusion}
    if name == mod.QA_CHECK and conclusion == "success":
        row["output"] = {"title": "PASS — independent semantic source review"}
    return row


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
        self.assertEqual(r["decision"], "QA_GATE_PENDING")

    def test_release_hold_is_not_pass(self):
        r = mod.reduce_pr(pr(), [check(mod.QA_CHECK, mod.QA_APP_ID),
                          check(mod.INDEPENDENT_QA_CHECK, mod.QA_APP_ID),
                          check(mod.RELEASE_CHECK, mod.RELEASE_APP_ID, conclusion="failure")], SHA)
        self.assertEqual(r["decision"], "RELEASE_NOT_ACCEPTED")

    def test_both_apps_success_still_need_owner_review(self):
        r = mod.reduce_pr(pr(), [check(mod.QA_CHECK, mod.QA_APP_ID),
                          check(mod.INDEPENDENT_QA_CHECK, mod.QA_APP_ID),
                          check(mod.RELEASE_CHECK, mod.RELEASE_APP_ID)], SHA)
        self.assertEqual(r["decision"], "REVIEW_STAGE_ACCEPTED")
        self.assertEqual(r["next_action"], "VERIFY_NATIVE_CHECKS_AND_OWNER_APPROVAL")
        self.assertFalse(r["review_approved"])

    def test_distinct_qa_gate_cannot_be_replaced_by_release_app(self):
        x = mod.reduce_pr(pr(), [check(mod.QA_CHECK, mod.QA_APP_ID),
                                 check(mod.INDEPENDENT_QA_CHECK, mod.RELEASE_APP_ID),
                                 check(mod.RELEASE_CHECK, mod.RELEASE_APP_ID)], SHA)
        self.assertEqual(x["decision"], "QA_GATE_NOT_ACCEPTED")
        self.assertFalse(x["merge_authorized"])

    def test_release_app_name_must_match_exact_required_check(self):
        x = mod.reduce_pr(pr(), [check(mod.QA_CHECK, mod.QA_APP_ID),
                                 check(mod.INDEPENDENT_QA_CHECK, mod.QA_APP_ID),
                                 check("Another Release App Task", mod.RELEASE_APP_ID)], SHA)
        self.assertEqual(x["decision"], "RELEASE_EVIDENCE_PENDING")

    def test_security_success_with_hold_title_is_not_accepted(self):
        x = check(mod.QA_CHECK, mod.QA_APP_ID)
        x["output"] = {"title": "HOLD — independent semantic source review"}
        result = mod.reduce_pr(pr(), [x], SHA)
        self.assertEqual(result["decision"], "INDEPENDENT_QA_NOT_ACCEPTED")

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

    def test_semantic_success_without_real_pass_title_fails_closed(self):
        for output in (None, {}, {"title": None}, {"title": "HOLD"}, {"title": 123}):
            with self.subTest(output=output):
                row = check(mod.QA_CHECK, mod.QA_APP_ID)
                if output is None:
                    row.pop("output", None)
                else:
                    row["output"] = output
                decision = mod.reduce_pr(pr(), [row], SHA)
                self.assertEqual(decision["decision"], "INDEPENDENT_QA_NOT_ACCEPTED")
                self.assertFalse(decision["merge_authorized"])

    def test_malformed_check_inventory_raises_before_filtering(self):
        for data in ([None], ["invalid"], [{"name": "x", "app": []}],
                     [{"name": "x", "output": "bad"}], [{"app": {"id": 5}}]):
            with self.subTest(data=data):
                with self.assertRaisesRegex(ValueError, "CHECK_INVENTORY_INVALID"):
                    mod.reduce_pr(pr(), data, SHA)

    def test_cli_invalid_evidence_writes_typed_no_authority_decision(self):
        import json
        import sys
        import tempfile
        from unittest.mock import patch
        with tempfile.TemporaryDirectory() as temp:
            root = Path(temp)
            (root / "pr.json").write_text(json.dumps(pr()))
            (root / "checks.json").write_text('[null]')
            output = root / "decision.json"
            with patch.object(sys, "argv", ["engineering_delivery_pipeline.py",
                                           "--pr", str(root / "pr.json"),
                                           "--checks", str(root / "checks.json"),
                                           "--head-sha", SHA,
                                           "--output", str(output)]):
                mod.main()
            result = json.loads(output.read_text())
            self.assertEqual(result["decision"], "REVIEW_EVIDENCE_INVALID")
            self.assertFalse(result["merge_authorized"])
            self.assertFalse(result["can_execute"])

    def test_conflicting_security_checks_rejected(self):
        x = mod.reduce_pr(pr(), [check(mod.QA_CHECK, mod.QA_APP_ID),
                                check(mod.QA_CHECK, 123)], SHA)
        self.assertEqual(x["decision"], "QA_IDENTITY_INVALID")

    def test_signed_old_reject_survives_newer_hold(self):
        old = check(mod.QA_CHECK, mod.QA_APP_ID, conclusion="failure")
        old.update({"id": 41, "output": {"title": "REJECT — independent semantic source review",
                                           "summary": "REJECT: semantic finding"}})
        newer = check(mod.QA_CHECK, mod.QA_APP_ID, conclusion="failure")
        newer.update({"id": 42, "output": {"title": "HOLD — independent semantic source review",
                                             "summary": "HOLD: reason PROVIDER_MODEL_UNAVAILABLE"}})
        result = mod.reduce_pr(pr(), [old, newer], SHA)
        self.assertEqual(result["decision"], "SEMANTIC_REJECT_SAME_SHA")
        self.assertEqual(result["reentry_trigger"], "NEW_SOURCE_SHA")

    def test_newer_model_hold_is_diagnostic_not_semantic_reject(self):
        old = check(mod.QA_CHECK, mod.QA_APP_ID, conclusion="failure")
        old.update({"id": 41, "output": {"title": "HOLD — independent semantic source review",
                                           "summary": "reason PROVIDER_TRANSPORT"}})
        newer = check(mod.QA_CHECK, mod.QA_APP_ID, conclusion="failure")
        newer.update({"id": 42, "output": {"title": "HOLD — independent semantic source review",
                                             "summary": "reason PROVIDER_MODEL_UNAVAILABLE"}})
        result = mod.reduce_pr(pr(), [newer, old], SHA)
        self.assertEqual(result["decision"], "REVIEWER_MODEL_REPAIR")
        self.assertEqual(result["reentry_trigger"], "VALID_PROVIDER_PREFLIGHT_AND_NEW_QA_RECEIPT")
        self.assertFalse(result["merge_authorized"])

    def test_bare_404_is_ambiguous_and_never_authorizes_retry(self):
        for reason in ("HTTP_404", "PROVIDER_HTTP_404"):
            x = check(mod.QA_CHECK, mod.QA_APP_ID, conclusion="failure")
            x["output"] = {"title": "HOLD — independent semantic source review",
                           "summary": "reason " + reason}
            with self.subTest(reason=reason):
                result = mod.reduce_pr(pr(), [x], SHA)
                self.assertEqual(result["decision"], "REVIEWER_PROVIDER_DIAGNOSIS")
                self.assertEqual(result["next_action"], "VERIFY_ENDPOINT_MODEL_AND_SCOPE")
                self.assertFalse(result["can_execute"])

    def test_credential_and_quota_errors_need_provider_repair_not_source(self):
        for reason in ("PROVIDER_HTTP_401", "PROVIDER_CREDIT_EXHAUSTED"):
            x = check(mod.QA_CHECK, mod.QA_APP_ID, conclusion="failure")
            x["output"] = {"title": "HOLD — independent semantic source review",
                           "summary": "reason " + reason}
            with self.subTest(reason=reason):
                result = mod.reduce_pr(pr(), [x], SHA)
                self.assertEqual(result["decision"], "REVIEWER_AUTH_OR_QUOTA_REPAIR")
                self.assertNotEqual(result["next_action"], "REPAIR_REJECTED_SOURCE_NEW_SHA")

    def test_transient_does_not_auto_retry(self):
        x = check(mod.QA_CHECK, mod.QA_APP_ID, conclusion="failure")
        x["output"] = {"title": "HOLD — independent semantic source review",
                       "summary": "reason PROVIDER_HTTP_503"}
        result = mod.reduce_pr(pr(), [x], SHA)
        self.assertEqual(result["decision"], "REVIEWER_TRANSIENT_OUTAGE")
        self.assertFalse(result["can_execute"])

    def test_unknown_error_no_fallback(self):
        x = check(mod.QA_CHECK, mod.QA_APP_ID, conclusion="failure")
        x["output"] = {"title": "HOLD — independent semantic source review",
                       "summary": "reason UNKNOWN_PROVIDER_FAILURE"}
        self.assertEqual(mod.reduce_pr(pr(), [x], SHA)["decision"], "INDEPENDENT_QA_NOT_ACCEPTED")

    def test_prior_reject_remains_binding_even_if_later_success(self):
        reject = check(mod.QA_CHECK, mod.QA_APP_ID, conclusion="failure")
        reject.update({"id": 10, "output": {"title": "REJECT — independent semantic source review"}})
        success = check(mod.QA_CHECK, mod.QA_APP_ID)
        success["id"] = 11
        self.assertEqual(mod.reduce_pr(pr(), [success, reject], SHA)["decision"], "SEMANTIC_REJECT_SAME_SHA")

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
