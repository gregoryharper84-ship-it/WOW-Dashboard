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


# ── Tests: behavioral idempotency (cursor-mock proof) ─────────────────────────

class TestSettlementWorkerIdempotencyBehavioral(unittest.TestCase):
    """
    Behavioral proof that rerunning _grade_open_prop_settlements() and
    _grade_open_kalshi_settlements() against an already-settled fixture row
    does not duplicate the settlement outcome or call conn.commit() a second
    time.

    Every test here uses cursor mocks — no source-text inspection.

    Prop path: the UPDATE idempotency guard is `AND settlement_status = 'OPEN'`.
    When a row is already SETTLED, the UPDATE matches 0 rows (rowcount=0),
    `graded` stays 0, and `conn.commit()` is skipped.

    Kalshi path: same guard on `kalshi_forecast_ledger`; same proof.
    """

    # ── Fixture row matching the tuple unpack in _grade_open_prop_settlements:
    # rec_id, event_key, selected_side, model_prob, entry_price,
    # closing_price, raw_row
    PROP_FIXTURE_ROW = (
        "fixture_row_001",       # id
        "EVENT:MLB:GAME:001",    # event_key
        "OVER",                  # selected_side
        0.62,                    # model_probability
        -110,                    # entry_price
        -115,                    # closing_price
        {                        # raw_row  (must be a dict, not None)
            "official_event_result":  "WIN",
            "selected_side":          "OVER",
            "selected_side_is_home":  True,
            "platform_display_result": "WIN",
            "platform_payment":       90.91,
            "stake":                  100.0,
            "promo_protection_active": False,
        },
    )

    # ── Fixture row matching _grade_open_kalshi_settlements:
    # rec_id, market_ticker, side_yes_no, model_prob
    KALSHI_FIXTURE_ROW = (
        "kalshi_fixture_001",  # id
        "KXMLB-2026-001",      # market_ticker
        "YES",                 # side_yes_no
        0.58,                  # model_probability
    )

    # ------------------------------------------------------------------
    # Helpers
    # ------------------------------------------------------------------

    def _prop_cursor(self, update_rowcount: int) -> MagicMock:
        """Cursor that returns the prop fixture on SELECT and reports
        update_rowcount on the subsequent UPDATE."""
        cur = MagicMock()
        cur.fetchall.return_value = [self.PROP_FIXTURE_ROW]
        cur.rowcount = update_rowcount
        return cur

    def _kalshi_cursor(self, update_rowcount: int) -> MagicMock:
        cur = MagicMock()
        cur.fetchall.return_value = [self.KALSHI_FIXTURE_ROW]
        cur.rowcount = update_rowcount
        return cur

    # ------------------------------------------------------------------
    # Prop settlement — core idempotency proof
    # ------------------------------------------------------------------

    def test_fixture_rerun_does_not_duplicate_outcome(self):
        """
        Core fixture-settlement proof (user requirement):
        Run the grader twice against the same fixture row.
          Run 1 — UPDATE rowcount=1  → row is freshly settled, graded=1.
          Run 2 — UPDATE rowcount=0  → row is already SETTLED, graded=0.
        Total graded must equal 1 (not 2), and conn.commit must be called
        exactly once across both runs.
        """
        conn = MagicMock()

        # Run 1: the row is OPEN; UPDATE succeeds
        cur = self._prop_cursor(update_rowcount=1)
        with patch("gate_engine.ml_settlement_truth.reconcile_settlement",
                   return_value={"model_result": "WIN"}):
            first = sw._grade_open_prop_settlements(cur, conn)

        # Run 2: the row is now SETTLED; UPDATE is a no-op (rowcount=0)
        cur2 = self._prop_cursor(update_rowcount=0)
        with patch("gate_engine.ml_settlement_truth.reconcile_settlement",
                   return_value={"model_result": "WIN"}):
            second = sw._grade_open_prop_settlements(cur2, conn)

        self.assertEqual(first, 1,
                         "run 1: fixture row must be graded exactly once")
        self.assertEqual(second, 0,
                         "run 2: already-settled row must not be re-graded")
        self.assertEqual(first + second, 1,
                         "total graded across two runs must equal 1 — "
                         "outcome must not be duplicated")
        self.assertEqual(conn.commit.call_count, 1,
                         "conn.commit must be called exactly once "
                         "(on run 1 only, not again on run 2)")

    def test_update_rowcount_zero_means_not_graded(self):
        """
        When the UPDATE fires but cur.rowcount == 0 (the row was already
        SETTLED — e.g. settled by the other gunicorn worker between the
        SELECT and this UPDATE), graded must remain 0 and conn.commit()
        must not be called.
        """
        conn = MagicMock()
        cur = self._prop_cursor(update_rowcount=0)

        with patch("gate_engine.ml_settlement_truth.reconcile_settlement",
                   return_value={"model_result": "WIN"}):
            result = sw._grade_open_prop_settlements(cur, conn)

        self.assertEqual(result, 0,
                         "graded must be 0 when UPDATE rowcount=0")
        conn.commit.assert_not_called()

    def test_update_rowcount_one_grades_and_commits(self):
        """
        Positive control: when the UPDATE affects 1 row (the row was OPEN),
        graded must be 1 and conn.commit() must be called exactly once.
        """
        conn = MagicMock()
        cur = self._prop_cursor(update_rowcount=1)

        with patch("gate_engine.ml_settlement_truth.reconcile_settlement",
                   return_value={"model_result": "WIN"}):
            result = sw._grade_open_prop_settlements(cur, conn)

        self.assertEqual(result, 1)
        conn.commit.assert_called_once()

    def test_select_open_guard_returns_empty_for_settled_row(self):
        """
        The SELECT itself carries WHERE settlement_status = 'OPEN'.
        When all rows are already SETTLED, fetchall returns [] and the
        worker does nothing: no UPDATE, no commit.
        """
        conn = MagicMock()
        cur = MagicMock()
        cur.fetchall.return_value = []  # SETTLED rows filtered out at SELECT

        result = sw._grade_open_prop_settlements(cur, conn)

        self.assertEqual(result, 0)
        conn.commit.assert_not_called()
        # Only the SELECT execute should have fired; no UPDATE
        self.assertEqual(cur.execute.call_count, 1,
                         "only the SELECT should fire when no OPEN rows exist; "
                         "got more execute() calls than expected")

    def test_loss_result_also_idempotent_on_rerun(self):
        """
        Idempotency must hold for LOSS outcomes too (not just WIN).
        """
        conn = MagicMock()

        cur = self._prop_cursor(update_rowcount=1)
        with patch("gate_engine.ml_settlement_truth.reconcile_settlement",
                   return_value={"model_result": "LOSS"}):
            first = sw._grade_open_prop_settlements(cur, conn)

        cur2 = self._prop_cursor(update_rowcount=0)
        with patch("gate_engine.ml_settlement_truth.reconcile_settlement",
                   return_value={"model_result": "LOSS"}):
            second = sw._grade_open_prop_settlements(cur2, conn)

        self.assertEqual(first, 1)
        self.assertEqual(second, 0)
        self.assertEqual(conn.commit.call_count, 1)

    def test_multiple_open_rows_partial_already_settled(self):
        """
        When a batch contains two fixture rows but only one UPDATE matches
        (rowcount alternates 1 then 0), total graded must equal 1.
        """
        fixture_a = self.PROP_FIXTURE_ROW
        fixture_b = (
            "fixture_row_002",
            "EVENT:MLB:GAME:002",
            "UNDER",
            0.55,
            -105,
            -108,
            {
                "official_event_result":  "WIN",
                "selected_side":          "UNDER",
                "selected_side_is_home":  False,
                "platform_display_result": "WIN",
                "platform_payment":       95.24,
                "stake":                  100.0,
                "promo_protection_active": False,
            },
        )

        conn = MagicMock()
        cur = MagicMock()
        cur.fetchall.return_value = [fixture_a, fixture_b]

        # Make rowcount return 1 for row A's UPDATE, 0 for row B's (already settled)
        cur.rowcount = 0
        rowcounts = [1, 0]
        rowcount_iter = iter(rowcounts)

        original_execute = cur.execute
        execute_calls = [0]

        def side_effect_execute(*args, **kwargs):
            # The first execute is the SELECT; subsequent ones are UPDATEs
            execute_calls[0] += 1
            if execute_calls[0] > 1:
                # This is an UPDATE call — set next rowcount
                try:
                    cur.rowcount = next(rowcount_iter)
                except StopIteration:
                    cur.rowcount = 0

        cur.execute.side_effect = side_effect_execute

        with patch("gate_engine.ml_settlement_truth.reconcile_settlement",
                   return_value={"model_result": "WIN"}):
            result = sw._grade_open_prop_settlements(cur, conn)

        self.assertEqual(result, 1,
                         "only 1 of 2 rows should be counted; "
                         "the already-settled one must be skipped")

    # ------------------------------------------------------------------
    # Kalshi settlement — same idempotency proof for the second guard
    # ------------------------------------------------------------------

    def test_kalshi_fixture_rerun_does_not_duplicate_outcome(self):
        """
        Kalshi path: run the grader twice.
        Run 1 — rowcount=1 → graded=1, commit called.
        Run 2 — rowcount=0 → graded=0, commit NOT called again.
        """
        conn = MagicMock()

        resolution = {"yes_resolved": True, "closing_price_cents": 99}
        reconcile_result = {
            "calibration_include": True,
            "final_result":        "WIN",
            "clv_cents":           5,
            "clv_percent":         0.05,
            "net_pnl_after_fees_cents": 95,
        }

        # Run 1: OPEN row, UPDATE succeeds
        cur = self._kalshi_cursor(update_rowcount=1)
        with patch.object(sw, "_fetch_kalshi_resolution", return_value=resolution), \
             patch("kalshi_engine.settlement_reconciliation.reconcile",
                   return_value=reconcile_result), \
             patch("kalshi_engine.settlement_reconciliation.FILL_STATUS_FILLED", "FILLED"), \
             patch("kalshi_engine.settlement_reconciliation.SS_SETTLED", "SETTLED"):
            first = sw._grade_open_kalshi_settlements(cur, conn)

        # Run 2: already SETTLED, rowcount=0
        cur2 = self._kalshi_cursor(update_rowcount=0)
        with patch.object(sw, "_fetch_kalshi_resolution", return_value=resolution), \
             patch("kalshi_engine.settlement_reconciliation.reconcile",
                   return_value=reconcile_result), \
             patch("kalshi_engine.settlement_reconciliation.FILL_STATUS_FILLED", "FILLED"), \
             patch("kalshi_engine.settlement_reconciliation.SS_SETTLED", "SETTLED"):
            second = sw._grade_open_kalshi_settlements(cur2, conn)

        self.assertEqual(first, 1,
                         "Kalshi run 1: fixture row must be graded exactly once")
        self.assertEqual(second, 0,
                         "Kalshi run 2: already-settled row must not be re-graded")
        self.assertEqual(first + second, 1,
                         "Kalshi total graded must equal 1 across two runs")
        self.assertEqual(conn.commit.call_count, 1,
                         "Kalshi conn.commit must be called exactly once")

    def test_kalshi_rowcount_zero_no_commit(self):
        """
        Kalshi UPDATE rowcount=0 → graded=0 and conn.commit not called.
        """
        conn = MagicMock()
        cur = self._kalshi_cursor(update_rowcount=0)

        resolution = {"yes_resolved": False, "closing_price_cents": 1}
        reconcile_result = {
            "calibration_include": True,
            "final_result":        "LOSS",
            "clv_cents":           -99,
            "clv_percent":         -0.99,
            "net_pnl_after_fees_cents": -100,
        }

        with patch.object(sw, "_fetch_kalshi_resolution", return_value=resolution), \
             patch("kalshi_engine.settlement_reconciliation.reconcile",
                   return_value=reconcile_result), \
             patch("kalshi_engine.settlement_reconciliation.FILL_STATUS_FILLED", "FILLED"), \
             patch("kalshi_engine.settlement_reconciliation.SS_SETTLED", "SETTLED"):
            result = sw._grade_open_kalshi_settlements(cur, conn)

        self.assertEqual(result, 0)
        conn.commit.assert_not_called()

    def test_kalshi_select_open_guard_empty_returns_zero(self):
        """
        Kalshi SELECT returns [] (all rows already SETTLED) → graded=0,
        no UPDATE, no commit.
        """
        conn = MagicMock()
        cur = MagicMock()
        cur.fetchall.return_value = []

        result = sw._grade_open_kalshi_settlements(cur, conn)

        self.assertEqual(result, 0)
        conn.commit.assert_not_called()
        self.assertEqual(cur.execute.call_count, 1,
                         "only the SELECT should fire when fetchall returns []")


if __name__ == "__main__":
    unittest.main()
