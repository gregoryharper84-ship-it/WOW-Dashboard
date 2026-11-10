"""Pure deterministic router tests, no provider call and no DB writes."""
import unittest
from datetime import datetime, timedelta, timezone

from contracts import EvidenceSource, PilotTask
from routing import plan_readonly


class RoutingTests(unittest.TestCase):
    def setUp(self):
        now = datetime(2026, 10, 14, 12, 0, tzinfo=timezone.utc)
        self.task = PilotTask(
            task_id="kalshi-test-a", lane="KALSHI_CONTRACT_AUDIT",
            decision_time=now, scope="Audit specific market rules",
            source_material="Test fixture, not a real market",
            evidence=[EvidenceSource(
                source_id="fixture", kind="KALSHI_RULE",
                uri="https://kalshi.com/test", available_at=now-timedelta(hours=1),
                retrieved_at=now, sha256="b"*64,
            )],
        )

    def test_disabled_by_default(self):
        p = plan_readonly([self.task])
        self.assertEqual(p.blocked, 1)
        self.assertEqual(p.tasks[0].reason, "PILOT_DISABLED")
        self.assertFalse(p.can_execute)

    def test_pure_plan_deterministic(self):
        p1 = plan_readonly([self.task], enabled=True)
        p2 = plan_readonly([self.task], enabled=True)
        self.assertEqual(p1.plan_id, p2.plan_id)
        self.assertEqual(p1.accepted, 1)

    def test_duplicate_task_denied(self):
        with self.assertRaisesRegex(ValueError, "DUPLICATE_TASK_ID"):
            plan_readonly([self.task, self.task], enabled=True)

    def test_missing_contract_source_denied(self):
        task = self.task.model_copy(update={"evidence": [
            self.task.evidence[0].model_copy(update={"kind": "OTHER"})]})
        p = plan_readonly([task], enabled=True)
        self.assertEqual(p.tasks[0].reason, "MISSING_CONTRACT_AUTHORITY")

    def test_too_much_context_denied(self):
        with self.assertRaisesRegex(ValueError, "PILOT_CONTEXT_CAP_EXCEEDED"):
            plan_readonly([self.task], enabled=True, max_source_chars=4)


if __name__ == "__main__":
    unittest.main()
