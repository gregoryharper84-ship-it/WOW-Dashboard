"""Deterministic safety/negative regression tests for the proposed V25 roles.

Run: python -m unittest discover -s artifacts/wow-engine -p 'test_v25_team_contract.py'
No network, provider calls, worker dispatch or production state is touched.
"""
from __future__ import annotations

import copy
from datetime import datetime, timedelta, timezone
import importlib.util
from pathlib import Path
import unittest

MODULE = Path(__file__).resolve().parent / "v25" / "organization_contract.py"
spec = importlib.util.spec_from_file_location("wow_v25_organization_contract", MODULE)
assert spec and spec.loader
v25 = importlib.util.module_from_spec(spec)
spec.loader.exec_module(v25)


def handoff():
    now = datetime.now(timezone.utc)
    return {
        "work_item_id": "INC-1841",
        "source_team": "AMRT",
        "current_owner": "model_dependency_investigator",
        "next_owner": "model_restoration_engineer",
        "change_class": "A",
        "evidence_refs": ["receipt://repro-1841"],
        "accepted_by_next_owner": "model_restoration_engineer",
        "lease_id": "canonical-worker-lease-123",
        "lease_epoch": 4,
        "lease_expires_at": (now + timedelta(hours=1)).isoformat(),
        "next_action": "write focused regression for typed failure",
        "blocking_reason": None,
        "updated_at": now.isoformat(),
    }


def closure(sha="a" * 40):
    return {
        "root_cause_evidence": "receipt://root-cause",
        "focused_and_negative_tests": "receipt://focused",
        "regression_and_adjacent_tests": "receipt://regressions",
        "implementer": "engineering-app",
        "independent_review": {"actor": "reviewer-app", "sha": sha, "verified": True},
        "required_approvals": {"codeowner": sha, "security": sha, "qa": sha, "release": sha},
        "merged_sha": sha,
        "target_deploy_sha": sha,
        "live_sha": sha,
        "production_replay": True,
        "persistence_reconciliation": True,
        "independent_qa": {"actor": "qa-app", "sha": sha, "verified": True},
        "security_governance_clear": True,
    }


class V25TeamContractTests(unittest.TestCase):
    def setUp(self):
        self.cfg = v25.load_contract()

    def test_registry_valid_and_no_operative_authority(self):
        self.assertEqual([], v25.validate_contract(self.cfg))
        self.assertEqual("PROPOSED_NOT_DEPLOYED", self.cfg["status"])
        self.assertEqual("V17_ACTIVE", self.cfg["runtime_generation"])
        self.assertEqual("WOW_BETTING_ENGINE", self.cfg["custom_gpt_identity"])
        self.assertFalse(self.cfg["can_execute"])
        self.assertTrue(self.cfg["DRY_RUN_ONLY_NO_LIVE_TRADING_NO_MARKET_ORDERS"])
        self.assertEqual("V17_TERMINAL_REDUCER", self.cfg["terminal_authority"])
        self.assertEqual(27, len(self.cfg["agent_authorities"]))

    def test_one_governed_dispatcher_not_seven_competing_controllers(self):
        self.assertEqual("WOW_ECOSYSTEM_CONDUCTOR", self.cfg["shared_control_plane"])
        self.assertEqual("EXISTING_GOVERNED_WORKER_ONLY", self.cfg["shared_dispatcher"])
        self.assertEqual("EXISTING_CANONICAL_INCIDENT_AND_WORK_LEDGER", self.cfg["shared_queue"])

    def test_amrt_reports_to_production_without_becoming_model_authority(self):
        self.assertEqual("PRODUCTION_ENGINEERING", self.cfg["teams"]["AMRT"]["parent"])
        for role in self.cfg["teams"]["AMRT"]["roles"]:
            self.assertFalse(self.cfg["agent_authorities"][role]["probability_authority"])

    def test_assurance_independent_of_implementation(self):
        self.assertEqual("SYSTEMS_INTELLIGENCE_RELIABILITY",
                         self.cfg["teams"]["SYSTEMS_INTELLIGENCE_RELIABILITY"]["parent"])
        self.assertEqual("INDEPENDENT_VERIFICATION",
                         self.cfg["teams"]["INDEPENDENT_VERIFICATION"]["parent"])

    def test_extra_probability_privilege_is_rejected(self):
        altered = copy.deepcopy(self.cfg)
        altered["agent_authorities"]["model_science_lead"]["probability_authority"] = True
        self.assertTrue(any("forbidden right probability_authority" in e
                            for e in v25.validate_contract(altered)))

    def test_deployment_claim_without_approval_is_rejected(self):
        altered = copy.deepcopy(self.cfg)
        altered["teams"]["AMRT"]["operational_status"] = "LIVE"
        self.assertIn("AMRT: cannot claim deployed status",
                      v25.validate_contract(altered))

    def test_unknown_objective_cannot_get_auto_assigned(self):
        self.assertEqual({"state": "BLOCKED", "reason": "UNREGISTERED_WORK_SCOPE"},
                         v25.route_objective(self.cfg, "BYPASS_RELEASE"))

    def test_model_repair_and_experiment_have_distinct_owners(self):
        self.assertEqual("AMRT", v25.route_objective(self.cfg, "RESTORE_MODEL_CAPABILITY")["team"])
        self.assertEqual("MODEL_EXCELLENCE", v25.route_objective(
            self.cfg, "MODEL_REDESIGN_EXPERIMENT")["team"])

    def test_valid_explicit_handoff_is_only_evidence_not_execution(self):
        self.assertEqual([], v25.assess_handoff(self.cfg, handoff()))

    def test_unacknowledged_handoff_is_rejected(self):
        item = handoff()
        item["accepted_by_next_owner"] = None
        self.assertIn("NEXT_OWNER_NOT_ACKNOWLEDGED", v25.assess_handoff(self.cfg, item))

    def test_expired_lease_requires_reconciliation_not_parallel_takeover(self):
        item = handoff()
        item["lease_expires_at"] = "2026-01-01T00:00:00+00:00"
        self.assertIn("LEASE_EXPIRED_FAIL_CLOSED", v25.assess_handoff(self.cfg, item))

    def test_unknown_next_owner_fails_closed(self):
        item = handoff()
        item["next_owner"] = "unknown-bot"
        self.assertIn("UNKNOWN_ROLE_IDENTITY", v25.assess_handoff(self.cfg, item))

    def test_invalid_lease_epoch_does_not_transfer_writer(self):
        item = handoff()
        item["lease_epoch"] = 0
        self.assertIn("INVALID_LEASE_EPOCH", v25.assess_handoff(self.cfg, item))

    def test_evidence_complete_receipt_is_not_production_release_authority(self):
        self.assertEqual(["PENDING_AUTHORITATIVE_RECEIPT_VERIFICATION"],
                         v25.assess_production_closure(self.cfg, closure()))
        self.assertEqual("PROPOSED_NOT_DEPLOYED", self.cfg["status"])

    def test_missing_production_replay_prevents_closure(self):
        receipt = closure()
        receipt["production_replay"] = False
        self.assertIn("MISSING_PRODUCTION_REPLAY",
                      v25.assess_production_closure(self.cfg, receipt))

    def test_live_sha_drift_prevents_closure(self):
        receipt = closure()
        receipt["live_sha"] = "b" * 40
        self.assertIn("DEPLOYED_SHA_MISMATCH",
                      v25.assess_production_closure(self.cfg, receipt))

    def test_stale_release_approval_prevents_closure(self):
        receipt = closure()
        receipt["required_approvals"]["release"] = "b" * 40
        self.assertIn("APPROVAL_RELEASE_MISSING_OR_STALE",
                      v25.assess_production_closure(self.cfg, receipt))

    def test_implementer_cannot_independently_review(self):
        receipt = closure()
        receipt["independent_review"]["actor"] = "engineering-app"
        self.assertIn("INDEPENDENT_REVIEW_IDENTITY_OR_SHA_UNVERIFIED",
                      v25.assess_production_closure(self.cfg, receipt))

    def test_qa_and_review_must_be_distinct(self):
        receipt = closure()
        receipt["independent_qa"]["actor"] = "reviewer-app"
        self.assertIn("REVIEW_AND_QA_MUST_BE_INDEPENDENT",
                      v25.assess_production_closure(self.cfg, receipt))

    def test_complete_class_c_proposal_is_not_authorized_promotion(self):
        proposal = {key: "receipt://verified" for key in
                    self.cfg["model_promotion_required_fields"]}
        proposal["submitter"] = "science-implementer"
        self.assertEqual(["MODEL_PROMOTION_REQUIRES_INDEPENDENT_AUTHORIZATION"],
                         v25.assess_model_promotion(self.cfg, proposal))

    def test_class_c_cannot_promote_without_challenger(self):
        proposal = {key: "receipt://verified" for key in
                    self.cfg["model_promotion_required_fields"]}
        proposal["challenger"] = ""
        self.assertIn("MISSING_CHALLENGER",
                      v25.assess_model_promotion(self.cfg, proposal))

    def test_class_c_self_review_is_rejected(self):
        proposal = {key: "receipt://verified" for key in
                    self.cfg["model_promotion_required_fields"]}
        proposal["submitter"] = proposal["independent_model_review"]
        self.assertIn("MODEL_SELF_REVIEW_FORBIDDEN",
                      v25.assess_model_promotion(self.cfg, proposal))


if __name__ == "__main__":
    unittest.main()
