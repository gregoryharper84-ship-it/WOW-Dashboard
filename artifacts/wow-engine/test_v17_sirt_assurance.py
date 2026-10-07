"""Class A SIRT evidence-only regression tests."""
from __future__ import annotations

import sys
import unittest
from datetime import datetime, timedelta, timezone
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent))
from v17.sirt_assurance import (  # noqa: E402
    TERMINAL_AUTHORITY,
    check_auditor_heartbeat,
    recurring_incidents,
    verify_product_closure,
)


def accepted_receipt() -> dict:
    return {
        "can_execute": False,
        "terminal_authority": TERMINAL_AUTHORITY,
        "head_sha": "a" * 40,
        "ci_head_sha": "a" * 40,
        "merge_sha": "b" * 40,
        "deployed_sha": "b" * 40,
        "exact_head_ci": "PASS",
        "unit_tests": "PASS",
        "integration_tests": "PASS",
        "regression_tests": "PASS",
        "negative_path_tests": "PASS",
        "review": "PASS",
        "qa_verification": "PASS",
        "production_acceptance": "PASS",
        "independent_verification": "PASS",
        "candidate_reconciliation": "PASS",
        "independent_verifier": "INDEPENDENT_VERIFICATION",
        "ci_url": "https://github.com/example/ci/1",
        "deployment_evidence_url": "https://render.com/example/1",
        "production_acceptance_url": "https://github.com/example/acceptance/1",
        "verification_evidence_url": "https://github.com/example/qa/1",
    }


class ClosureProofTests(unittest.TestCase):
    def test_complete_shape_requires_independent_review_not_self_completion(self):
        result = verify_product_closure(accepted_receipt())
        self.assertEqual(result.status, "EVIDENCE_READY_FOR_INDEPENDENT_REVIEW")
        self.assertFalse(result.can_execute)
        self.assertEqual(result.blockers, ())

    def test_missing_production_or_independent_proof_blocks(self):
        receipt = accepted_receipt()
        receipt["production_acceptance"] = "PENDING"
        receipt["independent_verification"] = None
        receipt["candidate_reconciliation"] = False
        result = verify_product_closure(receipt)
        self.assertEqual(result.status, "BLOCKED_INCOMPLETE_EVIDENCE")
        self.assertIn("PRODUCTION_ACCEPTANCE_UNVERIFIED", result.blockers)
        self.assertIn("INDEPENDENT_VERIFICATION_UNVERIFIED", result.blockers)
        self.assertIn("CANDIDATE_RECONCILIATION_UNVERIFIED", result.blockers)

    def test_ci_old_head_and_deploy_mismatch_fail_closed(self):
        receipt = accepted_receipt()
        receipt["ci_head_sha"] = "c" * 40
        receipt["deployed_sha"] = "d" * 40
        result = verify_product_closure(receipt)
        self.assertIn("EXACT_HEAD_CI_UNVERIFIED", result.blockers)
        self.assertIn("DEPLOYED_IDENTITY_UNVERIFIED", result.blockers)

    def test_deployed_descendant_must_be_explicitly_proven(self):
        receipt = accepted_receipt()
        receipt["deployed_sha"] = "d" * 40
        receipt["deployment_ancestry_verified"] = True
        result = verify_product_closure(receipt)
        self.assertEqual(result.status, "EVIDENCE_READY_FOR_INDEPENDENT_REVIEW")

    def test_truthy_unstructured_claims_are_not_pass(self):
        receipt = accepted_receipt()
        receipt["review"] = "passed"
        receipt["qa_verification"] = 1
        self.assertIn("REVIEW_UNVERIFIED", verify_product_closure(receipt).blockers)
        self.assertIn("QA_VERIFICATION_UNVERIFIED", verify_product_closure(receipt).blockers)

    def test_execution_or_terminal_drift_blocks(self):
        receipt = accepted_receipt()
        receipt["can_execute"] = True
        receipt["terminal_authority"] = "OTHER"
        result = verify_product_closure(receipt)
        self.assertIn("EXECUTION_BOUNDARY_UNPROVEN", result.blockers)
        self.assertIn("TERMINAL_AUTHORITY_DRIFT", result.blockers)


class HeartbeatTests(unittest.TestCase):
    def setUp(self):
        self.now = datetime(2026, 10, 7, 23, 0, tzinfo=timezone.utc)
        self.base = {
            "last_heartbeat_at": (self.now - timedelta(seconds=40)).isoformat(),
            "status": "RUNNING",
            "can_execute": False,
            "terminal_authority": TERMINAL_AUTHORITY,
        }

    def test_fresh_healthy(self):
        self.assertEqual(check_auditor_heartbeat(self.base, now=self.now).status, "HEALTHY_OBSERVED")

    def test_stale_and_missing(self):
        stale = dict(self.base, last_heartbeat_at="2026-10-07T22:40:00Z")
        self.assertIn("AUDITOR_HEARTBEAT_STALE", check_auditor_heartbeat(stale, now=self.now).blockers)
        missing = dict(self.base, last_heartbeat_at=None)
        self.assertIn("AUDITOR_HEARTBEAT_MISSING", check_auditor_heartbeat(missing, now=self.now).blockers)

    def test_a_post_does_not_mask_degraded_runtime(self):
        degraded = dict(self.base, status="DEGRADED")
        self.assertIn("AUDITOR_RUNTIME_NOT_RUNNING", check_auditor_heartbeat(degraded, now=self.now).blockers)

    def test_future_timestamp_and_naive_clock_fail(self):
        future = dict(self.base, last_heartbeat_at="2026-10-07T23:05:00Z")
        self.assertIn("AUDITOR_CLOCK_SKEW", check_auditor_heartbeat(future, now=self.now).blockers)
        with self.assertRaises(ValueError):
            check_auditor_heartbeat(self.base, now=datetime(2026, 10, 7))


class RepeatFailureTests(unittest.TestCase):
    def test_only_confirmed_compatible_causes_are_grouped_and_deduped(self):
        def record(n, *, status="CONFIRMED", cause="OOM", subsystem="DEPLOYMENT_RUNTIME"):
            return {"postmortem_id": f"PM-2026-10-07-{n:03}",
                    "domain": "WOW", "primary_subsystem": subsystem,
                    "root_cause_code": cause, "root_cause_status": status}
        items = [record(1), record(2), record(3), record(3),
                 record(4, status="HYPOTHESIS"), record(5, cause="API_QUOTA")]
        findings = recurring_incidents(items)
        self.assertEqual(len(findings), 1)
        self.assertEqual(findings[0]["occurrences"], 3)
        self.assertEqual(findings[0]["disposition"], "PREVENTION_REVIEW_REQUIRED")
        self.assertFalse(findings[0]["can_execute"])

    def test_unknown_causes_never_group(self):
        records = [{"postmortem_id": str(n), "domain": "WOW",
                    "primary_subsystem": "WORKER", "root_cause_status": "CONFIRMED"}
                   for n in range(5)]
        self.assertEqual(recurring_incidents(records), [])

    def test_grouping_is_stable_regardless_of_input_order(self):
        rows = [{"postmortem_id": str(n), "domain": "WOW",
                 "primary_subsystem": "WORKER", "root_cause_code": "OOM",
                 "root_cause_status": "CONFIRMED"} for n in range(4)]
        self.assertEqual(recurring_incidents(rows), recurring_incidents(list(reversed(rows))))


if __name__ == "__main__":
    unittest.main()
