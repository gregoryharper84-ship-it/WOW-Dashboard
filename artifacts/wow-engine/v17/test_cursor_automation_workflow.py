"""Offline tests for the protected dispatcher pilot entrypoint."""
from datetime import datetime, timedelta, timezone
import json
from unittest import TestCase
from unittest.mock import MagicMock, patch

from . import cursor_automation_workflow as entry


class WorkflowTests(TestCase):
    def setUp(self):
        self.now = datetime.now(timezone.utc)
        self.evidence = {
            "receipt_id": "test-usage",
            "automation_id": str(entry._AUTOMATION_ID),
            "model": "Composer 2.5",
            "authenticated_account_readback": True,
            "included_usage_available": True,
            "paid_overages_disabled": True,
            "max_additional_spend_cents": 0,
            "checked_at": self.now.isoformat(),
            "expires_at": (self.now + timedelta(minutes=20)).isoformat(),
        }
        self.env = {
            "CURSOR_VERIFICATION_MODE": "preflight",
            "CURSOR_VERIFICATION_DURABLE_STORE_VERIFIED": "true",
            "CURSOR_VERIFICATION_WEBHOOK_KEY": "fixture",
            "CURSOR_VERIFICATION_REDIS_URL": "rediss://fixture",
        }

    def test_budget_requires_fresh_evidence_for_exact_model_and_automation(self):
        self.assertEqual(entry._budget(json.dumps(self.evidence), self.now)[0], "test-usage")
        for field, value in (
            ("authenticated_account_readback", False),
            ("paid_overages_disabled", False),
            ("expires_at", self.now.isoformat()),
            ("max_additional_spend_cents", 1),
            ("max_additional_spend_cents", False),
            ("automation_id", "wrong"),
            ("model", "Composer 2.5 Fast"),
        ):
            with self.subTest(field=field), self.assertRaisesRegex(entry.PilotHold, "BUDGET_UNVERIFIED"):
                entry._budget(json.dumps({**self.evidence, field: value}), self.now)

    @patch.object(entry, "_admit")
    def test_launch_default_off_and_unattested_store_block_before_admission(self, admit):
        with self.assertRaisesRegex(entry.PilotHold, "LAUNCH_DISABLED"):
            entry.run({**self.env, "CURSOR_VERIFICATION_MODE": "launch"})
        with self.assertRaisesRegex(entry.PilotHold, "DURABILITY_UNVERIFIED"):
            entry.run({**self.env, "CURSOR_VERIFICATION_DURABLE_STORE_VERIFIED": ""})
        admit.assert_not_called()

    @patch.object(entry, "launch_verification_pilot")
    @patch.object(entry, "_redis_client")
    @patch.object(entry, "_admit")
    def test_preflight_reads_slot_without_reserving_or_launching(self, admit, redis, launch):
        admit.return_value = MagicMock(reservation_run_id=42, main_sha="a" * 40,
                                       budget_receipt_id="test-usage")
        store = redis.return_value.__enter__.return_value
        store.exists.return_value = 0
        result = entry.run(self.env)
        self.assertEqual(result["status"], "CURSOR_AUTOMATION_PREFLIGHT_READY")
        store.exists.assert_called_once_with(entry._SLOT_KEY)
        store.set.assert_not_called()
        launch.assert_not_called()

    @patch.object(entry, "api_json")
    def test_agent_inventory_rejects_pagination_active_and_unknown_states(self, request):
        for response in (
            {"items": [], "nextCursor": "page2"},
            {"items": [{"status": "ACTIVE"}]},
            {"items": [{"status": "UNKNOWN"}]},
        ):
            with self.subTest(response=response), self.assertRaises(entry.PilotHold):
                request.return_value = response
                entry._inventory("fixture")
        request.return_value = {"items": [{"status": "IDLE"}, {"status": "ARCHIVED"}]}
        entry._inventory("fixture")

    def test_admission_requires_first_attempt_from_protected_owner_dispatch(self):
        env = {
            "GITHUB_REPOSITORY": entry.REPOSITORY,
            "GITHUB_REF": "refs/heads/main",
            "GITHUB_EVENT_NAME": "workflow_dispatch",
            "GITHUB_ACTOR": entry.REPOSITORY.split("/", 1)[0],
            "GITHUB_RUN_ATTEMPT": "1",
        }
        for key, value in (
            ("GITHUB_REF", "refs/heads/unreviewed"),
            ("GITHUB_EVENT_NAME", "schedule"),
            ("GITHUB_RUN_ATTEMPT", "2"),
            ("GITHUB_ACTOR", "another-user"),
        ):
            with self.subTest(key=key), self.assertRaisesRegex(entry.PilotHold, "CALLER_UNVERIFIED"):
                entry._admit({**env, key: value})
