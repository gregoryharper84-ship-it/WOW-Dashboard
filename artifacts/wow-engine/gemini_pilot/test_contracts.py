"""Local unit tests: python -m unittest discover -s . -p test_contracts.py"""
import unittest
from datetime import datetime, timedelta, timezone

from pydantic import ValidationError
from contracts import EvidenceSource, Finding, ModelAssessment, PilotReceipt, PilotTask
from worker import analyze


class ContractTests(unittest.TestCase):
    def setUp(self):
        self.now = datetime(2026, 10, 14, 12, 0, tzinfo=timezone.utc)
        self.source = EvidenceSource(
            source_id="kalshi:rule:1", kind="KALSHI_RULE",
            uri="https://kalshi.com/example", sha256="a" * 64,
            available_at=self.now - timedelta(hours=1), retrieved_at=self.now,
        )
        self.task = PilotTask(
            task_id="pilot-A-001", lane="KALSHI_CONTRACT_AUDIT",
            decision_time=self.now, scope="Audit station and rule identity",
            source_material="Contract source snapshot, not a live market claim",
            evidence=[self.source],
        )

    def test_task_is_always_read_only(self):
        with self.assertRaises(ValidationError):
            PilotTask.model_validate({**self.task.model_dump(), "can_execute": True})

    def test_no_post_decision_evidence(self):
        data = self.task.model_dump()
        data["evidence"][0]["available_at"] = self.now + timedelta(minutes=1)
        with self.assertRaises(ValidationError):
            PilotTask.model_validate(data)

    def test_duplicate_evidence_rejected(self):
        data = self.task.model_dump()
        data["evidence"].append(data["evidence"][0])
        with self.assertRaises(ValidationError):
            PilotTask.model_validate(data)

    def test_unregistered_citation_rejected(self):
        assessment = ModelAssessment(findings=[Finding(
            claim="Contract references another station",
            evidence_ids=["not-supplied"], severity="HIGH",
            recommended_check="Inspect current Kalshi rule snapshot")],
            narrative="An unverified hypothesis")
        with self.assertRaises(ValidationError):
            PilotReceipt(task_id=self.task.task_id, lane=self.task.lane,
                         outcome="CHALLENGER_FINDINGS_UNVERIFIED",
                         source_ids=["kalshi:rule:1"], assessment=assessment)

    def test_missing_model_returns_blocked(self):
        result = analyze(self.task, model="")
        self.assertEqual(result.outcome, "BLOCKED")
        self.assertFalse(result.can_execute)

    def test_no_fake_success_without_assessment(self):
        with self.assertRaises(ValidationError):
            PilotReceipt(task_id=self.task.task_id, lane=self.task.lane,
                         outcome="CHALLENGER_FINDINGS_UNVERIFIED",
                         source_ids=["kalshi:rule:1"])


if __name__ == "__main__":
    unittest.main()
