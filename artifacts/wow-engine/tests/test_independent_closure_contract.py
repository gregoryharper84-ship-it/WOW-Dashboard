"""Adversarial negative-path tests for independent QA evidence."""
import copy
import importlib.util
from pathlib import Path
import unittest

path = Path(__file__).resolve().parents[1] / "v17" / "independent_closure_contract.py"
spec = importlib.util.spec_from_file_location("independent_closure_contract", path)
mod = importlib.util.module_from_spec(spec)
spec.loader.exec_module(mod)
SHA = "a" * 40

def good():
    return {
        "incident_id":"1544", "work_item_id":"incident-1544",
        "pr_url":"https://github.com/example/repo/pull/1",
        "root_cause":"worker incorrectly declared success",
        "test_evidence":"actions://test/1",
        "production_acceptance_evidence":"runtime://replay/1",
        "persistence_reconciliation_evidence":"supabase://receipt/1",
        "disposition":"FIXED_AND_VERIFIED", "change_class":"A",
        "reviewed_sha":SHA, "tested_sha":SHA, "merged_sha":SHA,
        "deployed_sha":SHA, "qa_verified_sha":SHA,
        "actors":{"ENGINEERING":"engineer-1","REVIEW":"reviewer-2",
                  "RELEASE":"releaser-3","INDEPENDENT_QA":"qa-4"},
        "checks":{k:True for k in ("unit","integration","negative_path","regression",
            "adjacent_lane","independent_review","authorized_merge",
            "production_acceptance","independent_qa","persistence_reconciliation")},
        "invariants":{"custom_gpt_identity":"WOW_BETTING_ENGINE",
                      "runtime_generation":"V17_ACTIVE",
                      "terminal_authority":"V17_TERMINAL_REDUCER",
                      "can_execute":False,
                      "DRY_RUN_ONLY_NO_LIVE_TRADING_NO_MARKET_ORDERS":True},
        "unresolved_p0_p1_regressions":0, "independent_qa_decision":"PASS"
    }

class ClosureContractTests(unittest.TestCase):
    def test_valid_class_a_receipt(self):
        self.assertEqual([], mod.validate_closure(good()))
    def test_self_qa_rejected(self):
        r=good();r["actors"]["INDEPENDENT_QA"]="engineer-1"
        self.assertTrue(any("self-certify" in e for e in mod.validate_closure(r)))
    def test_self_review_rejected(self):
        r=good();r["actors"]["REVIEW"]="engineer-1"
        self.assertTrue(any("review its own" in e for e in mod.validate_closure(r)))
    def test_review_cannot_be_qa(self):
        r=good();r["actors"]["INDEPENDENT_QA"]="reviewer-2"
        self.assertTrue(any("separate principals" in e for e in mod.validate_closure(r)))
    def test_sha_drift_rejected(self):
        r=good();r["deployed_sha"]="b"*40
        self.assertTrue(any("SHAs must match" in e for e in mod.validate_closure(r)))
    def test_missing_deployed_sha_rejected(self):
        r=good();r["deployed_sha"]=None
        self.assertTrue(any("deployed_sha" in e for e in mod.validate_closure(r)))
    def test_false_green_rejected(self):
        r=good();r["checks"]["negative_path"]=False
        self.assertTrue(any("negative_path" in e for e in mod.validate_closure(r)))
    def test_qa_hold_rejected(self):
        r=good();r["independent_qa_decision"]="HOLD"
        self.assertTrue(any("independent_qa_decision" in e for e in mod.validate_closure(r)))
    def test_execution_safety_rejected(self):
        r=good();r["invariants"]["can_execute"]=True
        self.assertTrue(any("can_execute" in e for e in mod.validate_closure(r)))
    def test_class_b_governance_rejected(self):
        r=good();r["change_class"]="B"
        self.assertTrue(any("governed_approval" in e for e in mod.validate_closure(r)))
    def test_class_c_requires_forward_validation(self):
        r=good();r["change_class"]="C";r["checks"]["governed_approval"]=True
        self.assertTrue(any("historical_and_forward_validation" in e for e in mod.validate_closure(r)))
    def test_invalid_receipt_rejected(self):
        self.assertTrue(mod.validate_closure(None))
    def test_open_regression_rejected(self):
        r=good();r["unresolved_p0_p1_regressions"]=1
        self.assertTrue(any("unresolved_p0_p1" in e for e in mod.validate_closure(r)))

if __name__ == "__main__":
    unittest.main()
