import importlib.util
import pathlib
import unittest

SOURCE = pathlib.Path(__file__).resolve().parents[1] / "v17" / "autonomous_closure_decision.py"
spec = importlib.util.spec_from_file_location("autonomous_closure_decision", SOURCE)
module = importlib.util.module_from_spec(spec)
spec.loader.exec_module(module)
H = "a" * 40
M = "b" * 40

def base(**overrides):
    state = dict(authenticated=True, head_sha=H, base="main", repo="o/r",
                 head_repo="o/r", state="open", draft=False,
                 ci_head_sha=H, ci_state="success", certified_sha=H,
                 reviewer_authorized=True, reviewer_independent=True,
                 approved_sha=H)
    state.update(overrides)
    return state

class AutonomousClosureDecisionTests(unittest.TestCase):
    def test_stale_head_fails_closed(self):
        self.assertEqual(module.decide(base(ci_head_sha=M))["reason"], "CI_EVIDENCE_MISSING_OR_STALE")
    def test_failed_ci_no_dispatch(self):
        self.assertEqual(module.decide(base(ci_state="failure"))["reason"], "CI_FAILED_REPAIR_REQUIRED")
    def test_certify_exact_head(self):
        self.assertEqual(module.decide(base(certified_sha=M))["action"], "CERTIFY")
    def test_reviewer_missing_is_typed(self):
        self.assertEqual(module.decide(base(reviewer_authorized=False))["reason"], "REVIEWER_NOT_CONFIGURED")
    def test_no_self_approval(self):
        self.assertEqual(module.decide(base(reviewer_independent=False))["reason"], "REVIEWER_NOT_CONFIGURED")
    def test_approval_missing(self):
        self.assertEqual(module.decide(base(approved_sha=M))["action"], "REVIEW_PENDING")
    def test_closed_without_merge_blocks(self):
        self.assertEqual(module.decide(base(state="closed"))["reason"], "PR_NOT_MERGED")
    def test_squash_merge_provenance_requires_deployment(self):
        x = base(state="closed", merged=True, merged_sha=M)
        self.assertEqual(module.decide(x)["action"], "RELEASE_PENDING")
    def test_qa_must_be_authenticated(self):
        x = base(state="closed", merged=True, merged_sha=M, deployment_authenticated=True, deployed_sha=M)
        self.assertEqual(module.decide(x)["action"], "QA_PENDING")
    def test_reconciliation_required(self):
        x = base(state="closed", merged=True, merged_sha=M, deployment_authenticated=True,
                 deployed_sha=M, qa_authenticated=True, qa_sha=M)
        self.assertEqual(module.decide(x)["reason"], "RECONCILIATION_UNVERIFIED")
    def test_all_evidence_only_yields_close_candidate(self):
        x = base(state="closed", merged=True, merged_sha=M, deployment_authenticated=True,
                 deployed_sha=M, qa_authenticated=True, qa_sha=M, reconciliation_authenticated=True)
        self.assertEqual(module.decide(x)["action"], "CLOSE")
    def test_untrusted_pr_blocks(self):
        self.assertEqual(module.decide(base(head_repo="other/repo"))["reason"], "UNTRUSTED_PR_PROVENANCE")
    def test_input_claims_do_not_authenticate(self):
        self.assertEqual(module.decide(base(authenticated=False))["reason"], "UNAUTHENTICATED_STATE")

if __name__ == "__main__":
    unittest.main()
