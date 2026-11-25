"""Regression checks for the one-shot Cursor Automation transport."""

from concurrent.futures import ThreadPoolExecutor
from dataclasses import replace
from datetime import datetime, timedelta, timezone
import json
from threading import Lock
from unittest import TestCase
from unittest.mock import Mock, patch
from uuid import UUID

from . import cursor_automation_pilot as pilot


_SHA = "a" * 40
_TARGET = UUID("87614e2e-13f3-4df9-927f-a6d37956f963")
_AGENT = "bc-43828280-00de-49c5-b519-2a902d133b2a"


class AtomicTestStore:
    def __init__(self):
        self.lock = Lock()
        self.rows = {}

    def set(self, key, value, *, nx):
        assert nx is True
        with self.lock:
            if key in self.rows:
                return None
            self.rows[key] = value
            return True


class VerificationPilotTests(TestCase):
    def setUp(self):
        self.admission = pilot.VerificationAdmission(
            reservation_run_id=17,
            main_sha=_SHA,
            current_main_sha=_SHA,
            budget_receipt_id="included:17",
            budget_valid_until=datetime.now(timezone.utc) + timedelta(minutes=1),
            included_usage_available=True,
            paid_overages_disabled=True,
            remote_writers_clear=True,
        )
        self.store = AtomicTestStore()

    def launch(self, admission=None):
        return pilot.launch_verification_pilot(
            admission or self.admission, automation_id=_TARGET,
            webhook_key="scoped-test-key", redis_client=self.store,
        )

    @patch.object(pilot, "_post", return_value=_AGENT)
    def test_two_tasks_and_replay_share_one_slot(self, post):
        def attempt(run_id):
            try:
                return self.launch(replace(self.admission, reservation_run_id=run_id))
            except pilot.PilotHold as error:
                return str(error)

        with ThreadPoolExecutor(max_workers=3) as pool:
            results = list(pool.map(attempt, [17, 17, 18]))
        self.assertEqual(results.count("CURSOR_AUTOMATION_SLOT_HELD_OR_ALREADY_USED"), 2)
        receipt = next(result for result in results if isinstance(result, dict))
        self.assertEqual(receipt["agent_id"], _AGENT)
        self.assertFalse(receipt["can_execute"])
        self.assertFalse(receipt["production_verified"])
        post.assert_called_once()
        request = post.call_args.args[0]
        self.assertEqual(request.full_url, pilot._WEBHOOK_ROOT + str(_TARGET))
        self.assertEqual(request.get_header("Authorization"), "Bearer scoped-test-key")
        self.assertEqual(json.loads(request.data)["mode"], "READ_ONLY_SMOKE")
        self.assertNotIn("scoped-test-key", json.dumps(receipt))

    @patch.object(pilot, "_post")
    def test_budget_stale_head_and_writer_conflicts_block_before_reserving(self, post):
        cases = (
            ({"paid_overages_disabled": False}, "BUDGET_UNVERIFIED"),
            ({"included_usage_available": False}, "BUDGET_UNVERIFIED"),
            ({"budget_valid_until": datetime.now(timezone.utc)}, "BUDGET_UNVERIFIED"),
            ({"current_main_sha": "b" * 40}, "ADMISSION_UNVERIFIED"),
            ({"remote_writers_clear": False}, "ADMISSION_UNVERIFIED"),
        )
        for updates, code in cases:
            with self.subTest(updates=updates), self.assertRaisesRegex(pilot.PilotHold, code):
                self.launch(replace(self.admission, **updates))
        self.assertEqual(self.store.rows, {})
        post.assert_not_called()

    @patch.object(pilot, "_post")
    def test_store_failure_blocks_before_post(self, post):
        self.store = Mock()
        self.store.set.side_effect = TimeoutError
        with self.assertRaisesRegex(pilot.PilotHold, "SLOT_UNVERIFIED"):
            self.launch()
        post.assert_not_called()

    @patch.object(pilot, "_post", side_effect=pilot.PilotHold("AMBIGUOUS_POST"))
    def test_ambiguous_post_keeps_slot_and_prevents_retry(self, post):
        with self.assertRaisesRegex(pilot.PilotHold, "AMBIGUOUS_POST"):
            self.launch()
        with self.assertRaisesRegex(pilot.PilotHold, "SLOT_HELD_OR_ALREADY_USED"):
            self.launch()
        post.assert_called_once()

    @patch.object(pilot, "build_opener")
    def test_unverified_cursor_response_is_not_reported_as_run(self, build_opener):
        response = build_opener.return_value.open.return_value.__enter__.return_value
        response.status = 200
        response.read.return_value = b'{"backgroundComposerId":"invalid"}'
        with self.assertRaisesRegex(pilot.PilotHold, "AGENT_ID_UNVERIFIED"):
            self.launch()
        build_opener.return_value.open.assert_called_once()
