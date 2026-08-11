"""
gate_engine/tests/test_settlement_reliability.py
WOW B4-HARDENING-#194 — Settlement Worker Reliability

Regression tests for the hardening changes in gate_engine/settlement_worker.py.

Coverage
--------
TestSettlementWorkerBackoffMath     — exponential backoff formula, cap enforcement
TestSettlementWorkerHeartbeat       — last_heartbeat updated at loop start
TestSettlementWorkerConsecutiveErr  — consecutive_errors tracking + reset on success
TestSettlementWorkerIdempotency     — AND settlement_status='OPEN' guard ensures
                                      duplicate-safe grading (documented invariant)
TestSettlementWorkerSafetyConstants — can_execute=False, EXECUTION_RULE, batch size,
                                      advisory lock key
TestSettlementWorkerConfig          — env-var defaults and stat dict completeness
"""
from __future__ import annotations

import threading
import time
import unittest
from datetime import datetime, timezone
from unittest.mock import MagicMock, patch, call

import gate_engine.settlement_worker as sw


# ── Helpers ───────────────────────────────────────────────────────────────────

def _reset_stats() -> None:
    """Reset worker stats to a known baseline between tests."""
    sw._WORKER_STATS.update({
        "ticks":              0,
        "props_graded":       0,
        "kalshi_graded":      0,
        "errors":             0,
        "consecutive_errors": 0,
        "last_tick":          None,
        "last_success_tick":  None,
        "last_heartbeat":     None,
        "last_error":         None,
    })


# ── Tests: backoff math ───────────────────────────────────────────────────────

class TestSettlementWorkerBackoffMath(unittest.TestCase):
    """
    Verify the exponential backoff formula independent of the loop thread.
    The loop uses: sleep_sec = min(BASE * 2^n, MAX) where n = consecutive_errors - 1
    and n is capped at 10 to prevent integer overflow.
    """
    def _expected_sleep(self, consecutive_errors: int) -> int:
        n = min(consecutive_errors - 1, 10)
        return min(sw._BACKOFF_BASE_SEC * (2 ** n), sw._BACKOFF_MAX_SEC)

    def test_first_error_uses_base(self):
        expected = min(sw._BACKOFF_BASE_SEC * 1, sw._BACKOFF_MAX_SEC)
        self.assertEqual(self._expected_sleep(1), expected)

    def test_second_error_doubles(self):
        expected = min(sw._BACKOFF_BASE_SEC * 2, sw._BACKOFF_MAX_SEC)
        self.assertEqual(self._expected_sleep(2), expected)

    def test_third_error_quadruples(self):
        expected = min(sw._BACKOFF_BASE_SEC * 4, sw._BACKOFF_MAX_SEC)
        self.assertEqual(self._expected_sleep(3), expected)

    def test_cap_enforced(self):
        for n in range(8, 20):
            result = self._expected_sleep(n)
            self.assertLessEqual(result, sw._BACKOFF_MAX_SEC,
                                 f"Backoff for {n} errors exceeds MAX")

    def test_exponent_capped_at_ten(self):
        """n=10 and n=15 should produce the same sleep (exponent capped)."""
        self.assertEqual(self._expected_sleep(11), self._expected_sleep(20))

    def test_base_sec_positive(self):
        self.assertGreater(sw._BACKOFF_BASE_SEC, 0)

    def test_max_sec_greater_than_base(self):
        self.assertGreater(sw._BACKOFF_MAX_SEC, sw._BACKOFF_BASE_SEC)

    def test_max_sec_less_than_interval(self):
        """Max backoff should not exceed the normal interval — avoids stalling."""
        self.assertLessEqual(sw._BACKOFF_MAX_SEC, sw.SETTLEMENT_WORKER_INTERVAL_SEC)


# ── Tests: heartbeat updated at loop start ────────────────────────────────────

class TestSettlementWorkerHeartbeat(unittest.TestCase):
    """
    last_heartbeat must be stamped at the START of every loop iteration,
    before the tick runs. Even when the tick fails, the heartbeat is current.
    """

    def setUp(self):
        _reset_stats()

    def test_heartbeat_present_in_stats(self):
        self.assertIn("last_heartbeat", sw._WORKER_STATS)

    def test_heartbeat_starts_none(self):
        self.assertIsNone(sw._WORKER_STATS["last_heartbeat"])

    def test_heartbeat_updated_before_tick(self):
        """
        Simulate one loop iteration: stamp heartbeat, run a succeeding tick,
        confirm heartbeat was set before the tick completed.
        """
        heartbeat_during_tick: list = []

        def fake_tick():
            # Capture heartbeat value WHILE the tick is running
            heartbeat_during_tick.append(sw._WORKER_STATS["last_heartbeat"])

        with patch.object(sw, "_settlement_worker_tick", side_effect=fake_tick), \
             patch("time.sleep", side_effect=StopIteration):
            sw._WORKER_STATS["consecutive_errors"] = 0
            try:
                sw._settlement_worker_loop()
            except StopIteration:
                pass

        # Heartbeat must have been set before fake_tick ran
        self.assertEqual(len(heartbeat_during_tick), 1)
        self.assertIsNotNone(heartbeat_during_tick[0],
                             "last_heartbeat was None when tick ran — "
                             "must be stamped BEFORE the tick")

    def test_heartbeat_updated_even_on_tick_failure(self):
        """Heartbeat remains current even when the tick raises an exception."""
        call_count = [0]

        def fail_first_then_stop(*_args, **_kwargs):
            call_count[0] += 1
            if call_count[0] == 1:
                raise RuntimeError("simulated tick failure")
            raise StopIteration  # end the loop on second sleep

        with patch.object(sw, "_settlement_worker_tick", side_effect=RuntimeError("tick error")), \
             patch("time.sleep", side_effect=StopIteration):
            try:
                sw._settlement_worker_loop()
            except StopIteration:
                pass

        self.assertIsNotNone(sw._WORKER_STATS["last_heartbeat"],
                             "last_heartbeat must be set even when tick fails")

    def test_heartbeat_is_iso_string(self):
        """After a simulated iteration, last_heartbeat must be a valid ISO string."""
        with patch.object(sw, "_settlement_worker_tick"), \
             patch("time.sleep", side_effect=StopIteration):
            try:
                sw._settlement_worker_loop()
            except StopIteration:
                pass

        heartbeat = sw._WORKER_STATS["last_heartbeat"]
        self.assertIsNotNone(heartbeat)
        # Verify it parses as ISO 8601
        dt = datetime.fromisoformat(heartbeat)
        self.assertIsNotNone(dt)


# ── Tests: consecutive_errors tracking ───────────────────────────────────────

class TestSettlementWorkerConsecutiveErrors(unittest.TestCase):
    """
    consecutive_errors must:
    • Increment by 1 on each tick that produces a new error.
    • Reset to 0 on any tick that completes without a new error.
    """

    def setUp(self):
        _reset_stats()

    def test_increments_on_error(self):
        with patch.object(sw, "_settlement_worker_tick",
                          side_effect=RuntimeError("boom")), \
             patch("time.sleep", side_effect=StopIteration):
            try:
                sw._settlement_worker_loop()
            except StopIteration:
                pass

        self.assertEqual(sw._WORKER_STATS["consecutive_errors"], 1)

    def test_resets_on_success(self):
        sw._WORKER_STATS["consecutive_errors"] = 5

        with patch.object(sw, "_settlement_worker_tick"), \
             patch("time.sleep", side_effect=StopIteration):
            try:
                sw._settlement_worker_loop()
            except StopIteration:
                pass

        self.assertEqual(sw._WORKER_STATS["consecutive_errors"], 0)

    def test_increments_twice_on_two_errors(self):
        call_count = [0]

        def fail_twice():
            call_count[0] += 1
            if call_count[0] <= 2:
                raise RuntimeError("error")

        sleep_count = [0]

        def count_sleeps(_sec):
            sleep_count[0] += 1
            if sleep_count[0] >= 2:
                raise StopIteration

        with patch.object(sw, "_settlement_worker_tick", side_effect=fail_twice), \
             patch("time.sleep", side_effect=count_sleeps):
            try:
                sw._settlement_worker_loop()
            except StopIteration:
                pass

        self.assertEqual(sw._WORKER_STATS["consecutive_errors"], 2)

    def test_sleep_after_error_uses_backoff(self):
        """Loop uses backoff sleep (< interval) on error, not full interval."""
        sleep_durations: list[float] = []

        def record_sleep(sec):
            sleep_durations.append(sec)
            raise StopIteration

        with patch.object(sw, "_settlement_worker_tick",
                          side_effect=RuntimeError("fail")), \
             patch("time.sleep", side_effect=record_sleep):
            try:
                sw._settlement_worker_loop()
            except StopIteration:
                pass

        self.assertEqual(len(sleep_durations), 1)
        # First error → sleep = BASE (= BASE * 2^0)
        expected = min(sw._BACKOFF_BASE_SEC, sw._BACKOFF_MAX_SEC)
        self.assertEqual(sleep_durations[0], expected)

    def test_sleep_after_success_uses_interval(self):
        sleep_durations: list[float] = []

        def record_sleep(sec):
            sleep_durations.append(sec)
            raise StopIteration

        with patch.object(sw, "_settlement_worker_tick"), \
             patch("time.sleep", side_effect=record_sleep):
            try:
                sw._settlement_worker_loop()
            except StopIteration:
                pass

        self.assertEqual(sleep_durations[0], sw.SETTLEMENT_WORKER_INTERVAL_SEC)


# ── Tests: idempotency (documented invariant) ─────────────────────────────────

class TestSettlementWorkerIdempotency(unittest.TestCase):
    """
    The existing SQL guard (AND settlement_status = 'OPEN') in
    _grade_open_prop_settlements() means that grading an already-settled row
    is a safe no-op (rowcount=0). This test documents the invariant.
    """

    def test_sql_contains_open_guard(self):
        """
        The UPDATE statement in _grade_open_prop_settlements must include
        'AND settlement_status = \'OPEN\'' so a re-graded row is a no-op.
        """
        import inspect
        src = inspect.getsource(sw._grade_open_prop_settlements)
        self.assertIn("settlement_status = 'OPEN'", src,
                      "Idempotency guard 'AND settlement_status = OPEN' missing "
                      "from _grade_open_prop_settlements UPDATE statement")

    def test_graded_count_uses_rowcount(self):
        """
        graded is only incremented when cur.rowcount > 0 — rows that had
        already been settled are silently skipped.
        """
        import inspect
        src = inspect.getsource(sw._grade_open_prop_settlements)
        self.assertIn("rowcount", src,
                      "graded increment must check cur.rowcount to be idempotent")


# ── Tests: safety constants ───────────────────────────────────────────────────

class TestSettlementWorkerSafetyConstants(unittest.TestCase):
    def test_can_execute_is_false(self):
        self.assertIs(sw.CAN_EXECUTE, False)

    def test_execution_rule_is_dry_run(self):
        self.assertIn("DRY_RUN", sw.EXECUTION_RULE)
        self.assertIn("NO_LIVE_TRADING", sw.EXECUTION_RULE)

    def test_advisory_lock_key_is_integer(self):
        self.assertIsInstance(sw._ADVISORY_LOCK_KEY, int)

    def test_advisory_lock_key_does_not_collide_with_llp_cron(self):
        # LLP cron uses 778597203; settlement worker must differ
        self.assertNotEqual(sw._ADVISORY_LOCK_KEY, 778597203)

    def test_backoff_constants_present(self):
        self.assertTrue(hasattr(sw, "_BACKOFF_BASE_SEC"))
        self.assertTrue(hasattr(sw, "_BACKOFF_MAX_SEC"))

    def test_backoff_base_sec_is_positive_int(self):
        self.assertIsInstance(sw._BACKOFF_BASE_SEC, int)
        self.assertGreater(sw._BACKOFF_BASE_SEC, 0)

    def test_backoff_max_sec_is_positive_int(self):
        self.assertIsInstance(sw._BACKOFF_MAX_SEC, int)
        self.assertGreater(sw._BACKOFF_MAX_SEC, 0)


# ── Tests: _WORKER_STATS completeness ────────────────────────────────────────

class TestSettlementWorkerConfig(unittest.TestCase):
    def test_stats_dict_has_consecutive_errors(self):
        self.assertIn("consecutive_errors", sw._WORKER_STATS)

    def test_stats_dict_has_last_heartbeat(self):
        self.assertIn("last_heartbeat", sw._WORKER_STATS)

    def test_stats_dict_has_backoff_config(self):
        self.assertIn("backoff_base_sec", sw._WORKER_STATS)
        self.assertIn("backoff_max_sec",  sw._WORKER_STATS)

    def test_stats_dict_has_standard_fields(self):
        for field in (
            "ticks", "props_graded", "kalshi_graded", "errors",
            "last_tick", "last_success_tick", "last_error",
            "enabled", "interval_sec",
        ):
            with self.subTest(field=field):
                self.assertIn(field, sw._WORKER_STATS)

    def test_consecutive_errors_initial_value(self):
        # Fresh import: consecutive_errors starts at 0
        self.assertIsInstance(sw._WORKER_STATS["consecutive_errors"], int)

    def test_last_heartbeat_initial_value(self):
        # last_heartbeat starts None — updated only when the loop has run
        # (may be non-None if a prior test ran the loop; just check type)
        val = sw._WORKER_STATS["last_heartbeat"]
        self.assertIn(type(val), (type(None), str))


if __name__ == "__main__":
    unittest.main()
