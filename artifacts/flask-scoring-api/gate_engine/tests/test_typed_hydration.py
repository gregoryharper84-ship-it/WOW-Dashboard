"""
test_typed_hydration.py — WOW-PATCH-2026-08-17-TYPED-HYDRATION-AND-MODEL-READINESS-V1

27 mandatory tests for the typed hydration and model-readiness enforcement layer.

Tests 1-10:  Core gate-behavior and run-controller scenarios.
Tests 11-22: Extended lifecycle, reconciliation, and isolation invariants
             (as specified in the build packet).
Test  23:    Gate-4 market-lane separation — UNAVAILABLE outcome allows
             confidence/model lane while blocking market-edge/money lanes.
Tests 24-27: Module-level invariants.

All tests: can_execute=False unconditional.
"""
from __future__ import annotations

import unittest
from datetime import datetime, timedelta, timezone

from gate_engine.typed_hydration import (
    ALL_GATES,
    GATE_IDENTITY,
    GATE_LEDGER,
    GATE_MARKET,
    GATE_ROLE,
    LABEL_HYDRATION_ABORT,
    LABEL_RUN_INVALID_HYDRATION_RECONCILIATION,
    DataStatus,
    FailureClass,
    LifecycleState,
    MarketGateOutcome,
    ModelStatus,
    advance_lifecycle,
    can_execute,
    reconcile_run,
    run_controller,
    run_hydration_check,
)
from gate_engine.labels import PropLabel


# ---------------------------------------------------------------------------
# Shared fixtures
# ---------------------------------------------------------------------------

def _complete_row(row_id: str = "r1") -> dict:
    return {
        "row_id":     row_id,
        "player":     "Test Player",
        "sport":      "NBA",
        "prop_type":  "points",
        "line":       20.5,
        "side":       "MORE",
        "event_id":   "evt-001",
        "event_date": "2026-08-17",
        "source":     "prizepicks",
        "data_status": "",
    }


def _complete_enrichment(now: datetime | None = None) -> dict:
    if now is None:
        now = datetime.now(timezone.utc)
    # market data fresh within TTL
    return {
        # Gate 1 — identity/status
        "participant_status": "ACTIVE",
        "lineupConfirmed":    True,
        "status_checked_at":  now.isoformat(),
        # Gate 2 — role/opportunity
        "role":                           "starter",
        "projected_minutes_or_workload":  32.0,
        "role_checked_at":               now.isoformat(),
        "role_source":                   "rotowire",
        # Gate 3 — historical ledger
        "l5_values":      [18, 22, 25, 19, 21],
        "l10_values":     [18, 22, 25, 19, 21, 17, 23, 20, 24, 16],
        "l5_line_used":   20.5,
        "l10_median":     20.5,
        "l10_mean":       20.5,
        "role_timestamp": now.isoformat(),
        # Gate 4 — market/settlement
        "market_no_vig_probability": 0.52,
        "data_timestamp":            now.isoformat(),
        "market_checked_at":         now.isoformat(),
        "market_ttl":                900,   # 15 minutes
    }


# ---------------------------------------------------------------------------
# Tests
# ---------------------------------------------------------------------------

class TestTypedHydration(unittest.TestCase):

    # -----------------------------------------------------------------------
    # Test 1 — Complete row reaches MODEL_READY
    # -----------------------------------------------------------------------
    def test_01_complete_row_reaches_model_ready(self):
        """A row with all required data clears all four gates and reaches MODEL_READY."""
        result = run_hydration_check(_complete_row(), _complete_enrichment())

        self.assertEqual(result.lifecycle_state, LifecycleState.MODEL_READY)
        self.assertEqual(result.data_status,     DataStatus.COMPLETE)
        self.assertEqual(result.model_status,    ModelStatus.READY)
        self.assertEqual(result.failure_class,   FailureClass.NONE)
        self.assertEqual(result.gates_passed,    4)
        self.assertEqual(result.gates_failed,    0)
        self.assertEqual(result.missing_fields,  [])
        # terminal_label is cleared at MODEL_READY (set after scoring)
        self.assertEqual(result.terminal_label, "")
        # When Gate 4 is AVAILABLE, both lanes are open
        self.assertEqual(result.market_gate_outcome, MarketGateOutcome.AVAILABLE)
        self.assertTrue(result.market_lane_available)
        self.assertTrue(result.confidence_lane_available)
        # All four gate results must be present and passed
        for gate_id in ALL_GATES:
            self.assertIn(gate_id, result.gate_results)
            self.assertTrue(result.gate_results[gate_id].passed, f"{gate_id} should pass")

    # -----------------------------------------------------------------------
    # Test 2 — Missing identity field → BLOCKED / INPUT_FAILURE
    # -----------------------------------------------------------------------
    def test_02_missing_identity_field_blocked(self):
        """Row missing 'player' field → BLOCKED with INPUT_FAILURE / INCOMPLETE_INPUT."""
        row = _complete_row()
        del row["player"]
        result = run_hydration_check(row, _complete_enrichment())

        self.assertEqual(result.lifecycle_state, LifecycleState.BLOCKED)
        self.assertEqual(result.data_status,     DataStatus.INCOMPLETE_INPUT)
        self.assertEqual(result.failure_class,   FailureClass.INPUT_FAILURE)
        self.assertEqual(result.terminal_label,  PropLabel.DATA_CONTRACT_FAIL.value)
        self.assertIn("player", result.missing_fields)
        self.assertGreater(result.gates_failed, 0)
        # Model was never started
        self.assertEqual(result.model_status, ModelStatus.NOT_STARTED)

    # -----------------------------------------------------------------------
    # Test 3 — Missing role field → BLOCKED (gate 2 fails)
    # -----------------------------------------------------------------------
    def test_03_missing_role_field_blocked(self):
        """Row missing 'role' in enrichment → BLOCKED on Gate 2 (role/opportunity)."""
        enr = _complete_enrichment()
        del enr["role"]
        result = run_hydration_check(_complete_row(), enr)

        self.assertEqual(result.lifecycle_state, LifecycleState.BLOCKED)
        self.assertEqual(result.failure_class,   FailureClass.INPUT_FAILURE)
        self.assertIn("role", result.missing_fields)
        # Gate 2 specifically must fail
        self.assertFalse(result.gate_results[GATE_ROLE].passed)
        self.assertEqual(result.terminal_label, PropLabel.DATA_CONTRACT_FAIL.value)

    # -----------------------------------------------------------------------
    # Test 4 — Missing ledger field → BLOCKED (gate 3 fails)
    # -----------------------------------------------------------------------
    def test_04_missing_ledger_field_blocked(self):
        """Row missing 'l5_values' in enrichment → BLOCKED on Gate 3 (historical ledger)."""
        enr = _complete_enrichment()
        del enr["l5_values"]
        result = run_hydration_check(_complete_row(), enr)

        self.assertEqual(result.lifecycle_state, LifecycleState.BLOCKED)
        self.assertEqual(result.failure_class,   FailureClass.INPUT_FAILURE)
        self.assertIn("l5_values", result.missing_fields)
        self.assertFalse(result.gate_results[GATE_LEDGER].passed)
        self.assertEqual(result.terminal_label, PropLabel.DATA_CONTRACT_FAIL.value)

    # -----------------------------------------------------------------------
    # Test 5 — Missing market field → MODEL_READY with market lane blocked
    # -----------------------------------------------------------------------
    def test_05_missing_market_field_allows_confidence_lane(self):
        """
        Row missing 'market_no_vig_probability' → MODEL_READY with:
          - confidence_lane_available = True   (probability model may run)
          - market_lane_available     = False  (market-edge/money lanes blocked)
          - market_gate_outcome       = UNAVAILABLE
          - terminal_label            = ""     (cleared at MODEL_READY)

        Per the Full Model Gatekeeper contract and reconstructed-confidence
        architecture: absent market evidence lowers the ceiling but does NOT
        prevent the probability model from running.  Only SOURCE_CONFLICT and
        STALE_DATA (BLOCKING outcomes) prevent MODEL_READY.
        """
        enr = _complete_enrichment()
        del enr["market_no_vig_probability"]
        result = run_hydration_check(_complete_row(), enr)

        self.assertEqual(result.lifecycle_state,           LifecycleState.MODEL_READY)
        self.assertEqual(result.market_gate_outcome,       MarketGateOutcome.UNAVAILABLE)
        self.assertFalse(result.market_lane_available)
        self.assertTrue(result.confidence_lane_available)
        self.assertEqual(result.terminal_label,            "")
        # Gate 4 still did not fully pass (it recorded the missing field)
        self.assertFalse(result.gate_results[GATE_MARKET].passed)

    # -----------------------------------------------------------------------
    # Test 6 — SOURCE_CONFLICT → BLOCKED / CONFLICT_FAILURE
    # -----------------------------------------------------------------------
    def test_06_source_conflict_blocked(self):
        """market_no_vig_probability=SOURCE_CONFLICT → BLOCKED with CONFLICT_FAILURE."""
        enr = _complete_enrichment()
        enr["market_no_vig_probability"] = "SOURCE_CONFLICT"
        result = run_hydration_check(_complete_row(), enr)

        self.assertEqual(result.lifecycle_state, LifecycleState.BLOCKED)
        self.assertEqual(result.data_status,     DataStatus.SOURCE_CONFLICT)
        self.assertEqual(result.failure_class,   FailureClass.CONFLICT_FAILURE)
        self.assertEqual(result.terminal_label,  PropLabel.DATA_CONTRACT_FAIL.value)
        self.assertFalse(result.gate_results[GATE_MARKET].passed)

    # -----------------------------------------------------------------------
    # Test 7 — Provider outage → BLOCKED / PROVIDER_FAILURE
    # -----------------------------------------------------------------------
    def test_07_provider_outage_blocked(self):
        """data_status=DATA_PROVIDER_OUTAGE on row → BLOCKED with PROVIDER_FAILURE."""
        row = _complete_row()
        row["data_status"] = "DATA_PROVIDER_OUTAGE"
        result = run_hydration_check(row, _complete_enrichment())

        self.assertEqual(result.lifecycle_state, LifecycleState.BLOCKED)
        self.assertEqual(result.data_status,     DataStatus.DATA_PROVIDER_OUTAGE)
        self.assertEqual(result.failure_class,   FailureClass.PROVIDER_FAILURE)
        self.assertEqual(result.terminal_label,  PropLabel.DATA_CONTRACT_FAIL.value)
        self.assertFalse(result.gate_results[GATE_IDENTITY].passed)

    # -----------------------------------------------------------------------
    # Test 8 — Expired market TTL → BLOCKED / FRESHNESS_FAILURE
    # -----------------------------------------------------------------------
    def test_08_stale_ttl_blocked(self):
        """Market data older than TTL → BLOCKED with STALE_DATA / FRESHNESS_FAILURE."""
        now = datetime.now(timezone.utc)
        enr = _complete_enrichment(now)
        # Set market_checked_at to 30 minutes ago, TTL to 15 minutes
        enr["market_checked_at"] = (now - timedelta(minutes=30)).isoformat()
        enr["market_ttl"]        = 900   # 15 min in seconds
        result = run_hydration_check(_complete_row(), enr, now=now)

        self.assertEqual(result.lifecycle_state, LifecycleState.BLOCKED)
        self.assertEqual(result.data_status,     DataStatus.STALE_DATA)
        self.assertEqual(result.failure_class,   FailureClass.FRESHNESS_FAILURE)
        self.assertEqual(result.terminal_label,  PropLabel.DATA_CONTRACT_FAIL.value)
        self.assertFalse(result.gate_results[GATE_MARKET].passed)
        self.assertIn("Expired TTL", result.gate_results[GATE_MARKET].failure_reason)

    # -----------------------------------------------------------------------
    # Test 9 — Partial failure: one complete + one blocked → DEGRADED
    # -----------------------------------------------------------------------
    def test_09_partial_failure_run_degraded(self):
        """One complete + one blocked row → run_status=DEGRADED, complete row preserved."""
        r_good    = run_hydration_check(_complete_row("good"), _complete_enrichment())
        row_bad   = _complete_row("bad")
        del row_bad["player"]
        r_bad     = run_hydration_check(row_bad, _complete_enrichment())

        ctrl = run_controller([r_good, r_bad])

        self.assertEqual(ctrl.run_status,              "DEGRADED")
        self.assertFalse(ctrl.hard_abort)
        self.assertEqual(ctrl.contract_complete_count, 1)
        self.assertEqual(ctrl.blocked_count,           1)
        self.assertIn("good",  ctrl.model_ready_row_ids)
        self.assertNotIn("good",  ctrl.blocked_row_ids)
        self.assertIn("bad",   ctrl.blocked_row_ids)
        self.assertNotIn("bad",   ctrl.model_ready_row_ids)

    # -----------------------------------------------------------------------
    # Test 10 — Alert fires when contract failure rate > 5%
    # -----------------------------------------------------------------------
    def test_10_alert_fires_at_failure_rate_threshold(self):
        """More than 5% of rows blocked → actual_failure_rate > alert threshold."""
        # 1 blocked in 10 = 10% failure rate
        results = [run_hydration_check(_complete_row(f"r{i}"), _complete_enrichment())
                   for i in range(9)]
        row_bad   = _complete_row("rbad")
        del row_bad["player"]
        results.append(run_hydration_check(row_bad, _complete_enrichment()))

        ctrl = run_controller(results)
        self.assertGreater(ctrl.actual_failure_rate, ctrl.alert_contract_failure_rate)
        # With 10% failure and default systemic_threshold=50%, not a hard abort
        self.assertFalse(ctrl.hard_abort)
        self.assertEqual(ctrl.run_status, "DEGRADED")

    # -----------------------------------------------------------------------
    # Test 11 — Unknown state transition is rejected
    # -----------------------------------------------------------------------
    def test_11_unknown_state_transition_rejected(self):
        """
        Attempting an invalid lifecycle transition raises ValueError.
        BOARD_EXTRACTED → MODEL_READY is not a valid single-step transition.
        """
        with self.assertRaises(ValueError) as ctx:
            from gate_engine.typed_hydration import _validate_transition
            _validate_transition(
                LifecycleState.BOARD_EXTRACTED,
                LifecycleState.MODEL_READY,
            )
        self.assertIn("Invalid lifecycle transition", str(ctx.exception))
        self.assertIn("BOARD_EXTRACTED", str(ctx.exception))
        self.assertIn("MODEL_READY",     str(ctx.exception))

    # -----------------------------------------------------------------------
    # Test 12 — State cannot move backward after scoring
    # -----------------------------------------------------------------------
    def test_12_state_cannot_move_backward_after_scoring(self):
        """
        SCORED is a terminal state.  Any transition out of it (including
        backward to MODEL_READY) must raise ValueError.
        """
        from gate_engine.typed_hydration import _validate_transition
        with self.assertRaises(ValueError):
            _validate_transition(LifecycleState.SCORED, LifecycleState.MODEL_READY)
        with self.assertRaises(ValueError):
            _validate_transition(LifecycleState.SCORED, LifecycleState.SCORING_ATOMIC)
        with self.assertRaises(ValueError):
            _validate_transition(LifecycleState.BLOCKED, LifecycleState.MODEL_READY)
        with self.assertRaises(ValueError):
            _validate_transition(LifecycleState.SCORED, LifecycleState.BLOCKED)

    # -----------------------------------------------------------------------
    # Test 13 — Retry is idempotent (no duplicate exposure or predictions)
    # -----------------------------------------------------------------------
    def test_13_retry_is_idempotent(self):
        """
        Running run_hydration_check twice on the same row produces the same
        result.  There are no side effects (no mutable state in the module).
        """
        row = _complete_row("retry-row")
        enr = _complete_enrichment()

        result_1 = run_hydration_check(row, enr)
        result_2 = run_hydration_check(row, enr)

        self.assertEqual(result_1.lifecycle_state, result_2.lifecycle_state)
        self.assertEqual(result_1.data_status,     result_2.data_status)
        self.assertEqual(result_1.model_status,    result_2.model_status)
        self.assertEqual(result_1.failure_class,   result_2.failure_class)
        self.assertEqual(result_1.gates_passed,    result_2.gates_passed)
        self.assertEqual(result_1.gates_failed,    result_2.gates_failed)
        # Idempotent reconciliation: two identical results in a batch with matching
        # row_ids violates the no-duplicate invariant.
        rec = reconcile_run([result_1, result_2])
        self.assertFalse(rec["valid"], "Duplicate row_ids must fail reconciliation")
        self.assertIn("DEDUP", rec["equations_failed"][0])

    # -----------------------------------------------------------------------
    # Test 14 — One incomplete row does not contaminate complete rows
    # -----------------------------------------------------------------------
    def test_14_one_incomplete_row_does_not_contaminate_complete_rows(self):
        """
        A BLOCKED row in a mixed batch must not affect the data_status,
        model_status, or terminal_label of sibling MODEL_READY rows.
        """
        r_good = run_hydration_check(_complete_row("good"), _complete_enrichment())
        row_bad = _complete_row("bad")
        del row_bad["player"]
        r_bad  = run_hydration_check(row_bad, _complete_enrichment())

        # Complete row remains uncontaminated
        self.assertEqual(r_good.lifecycle_state, LifecycleState.MODEL_READY)
        self.assertEqual(r_good.data_status,     DataStatus.COMPLETE)
        self.assertEqual(r_good.terminal_label,  "")

        # Blocked row is blocked
        self.assertEqual(r_bad.lifecycle_state, LifecycleState.BLOCKED)
        self.assertEqual(r_bad.data_status,     DataStatus.INCOMPLETE_INPUT)
        self.assertEqual(r_bad.terminal_label,  PropLabel.DATA_CONTRACT_FAIL.value)

    # -----------------------------------------------------------------------
    # Test 15 — contract_complete_count=0 aborts the lane
    # -----------------------------------------------------------------------
    def test_15_contract_complete_count_zero_aborts_lane(self):
        """
        All rows blocked → contract_complete_count=0 → hard_abort=True,
        run_status=ABORTED.  Final-card publication is blocked.
        """
        rows = []
        for i in range(3):
            row = _complete_row(f"r{i}")
            del row["player"]
            rows.append(run_hydration_check(row, _complete_enrichment()))

        ctrl = run_controller(rows)

        self.assertTrue(ctrl.hard_abort)
        self.assertEqual(ctrl.run_status,              "ABORTED")
        self.assertEqual(ctrl.contract_complete_count, 0)
        self.assertEqual(ctrl.blocked_count,           3)
        self.assertEqual(ctrl.model_ready_row_ids,     [])
        self.assertIn("contract_complete_count=0", ctrl.abort_reason)

    # -----------------------------------------------------------------------
    # Test 16 — Exact row reconciliation failure returns RUN_INVALID
    # -----------------------------------------------------------------------
    def test_16_reconciliation_failure_returns_run_invalid(self):
        """
        Supplying mismatched scored/model_failed counts causes reconciliation
        to fail, returning run_status='RUN_INVALID'.
        """
        result = run_hydration_check(_complete_row(), _complete_enrichment())
        # rows_model_ready=1, but we claim 0 scored + 0 model_failed → EQ4 mismatch
        rec = reconcile_run(
            [result],
            scored_row_ids=["phantom-row"],      # row that doesn't exist in results
            model_failed_row_ids=["another-phantom"],
        )
        self.assertFalse(rec["valid"])
        self.assertEqual(rec["run_status"], "RUN_INVALID")
        self.assertGreater(len(rec["equations_failed"]), 0)
        # terminal_label is the RUN_INVALID hydration reconciliation label
        self.assertEqual(
            rec["terminal_label"],
            LABEL_RUN_INVALID_HYDRATION_RECONCILIATION,
        )

    # -----------------------------------------------------------------------
    # Test 17 — Atomic failure publishes no probability
    # -----------------------------------------------------------------------
    def test_17_atomic_failure_publishes_no_probability(self):
        """
        A BLOCKED row (atomic calibration/write path never started) has
        model_status=NOT_STARTED and terminal_label=DATA_CONTRACT_FAIL.
        The probability pipeline cannot be reached — no probability is published.
        """
        row = _complete_row()
        del row["event_id"]   # gate 1 fails
        result = run_hydration_check(row, _complete_enrichment())

        self.assertEqual(result.lifecycle_state, LifecycleState.BLOCKED)
        self.assertEqual(result.model_status,    ModelStatus.NOT_STARTED)
        self.assertEqual(result.terminal_label,  PropLabel.DATA_CONTRACT_FAIL.value)
        # lifecycle_state never advanced past BLOCKED, so SCORING_ATOMIC was never entered
        self.assertNotEqual(result.lifecycle_state, LifecycleState.SCORING_ATOMIC)
        self.assertNotEqual(result.lifecycle_state, LifecycleState.SCORED)

    # -----------------------------------------------------------------------
    # Test 18 — Fallback source records both failed and successful attempts
    # -----------------------------------------------------------------------
    def test_18_fallback_source_records_both_attempts(self):
        """
        provider_attempts captures all acquisition attempts (both failed and
        successful) so the audit trail is complete.
        """
        attempts = [
            {"source": "primary_api",  "status": "FAILED",  "error": "timeout"},
            {"source": "fallback_api", "status": "SUCCESS", "latency_ms": 120},
        ]
        result = run_hydration_check(
            _complete_row(),
            _complete_enrichment(),
            provider_attempts=attempts,
            fallback_sources=["fallback_api"],
        )
        # Both attempts preserved regardless of outcome
        self.assertEqual(len(result.provider_attempts), 2)
        self.assertEqual(result.provider_attempts[0]["status"], "FAILED")
        self.assertEqual(result.provider_attempts[1]["status"], "SUCCESS")
        self.assertIn("fallback_api", result.fallback_sources)

    # -----------------------------------------------------------------------
    # Test 19 — Expired TTL cannot be refreshed by reusing the old value
    # -----------------------------------------------------------------------
    def test_19_expired_ttl_cannot_be_refreshed_by_reuse(self):
        """
        Even if the enrichment dict contains market_no_vig_probability (a real
        value), a market_checked_at that is older than market_ttl blocks the row.
        The old value cannot be reused to pass the freshness check.
        """
        now = datetime.now(timezone.utc)
        enr = _complete_enrichment(now)
        # Market data is 2 hours old; TTL is 15 minutes
        enr["market_checked_at"]         = (now - timedelta(hours=2)).isoformat()
        enr["market_ttl"]                = 900
        enr["market_no_vig_probability"] = 0.54   # a real value — still blocked

        result = run_hydration_check(_complete_row(), enr, now=now)

        self.assertEqual(result.lifecycle_state, LifecycleState.BLOCKED)
        self.assertEqual(result.data_status,     DataStatus.STALE_DATA)
        self.assertEqual(result.failure_class,   FailureClass.FRESHNESS_FAILURE)
        self.assertIn("TTL", result.gate_results[GATE_MARKET].failure_reason)

    # -----------------------------------------------------------------------
    # Test 20 — MODEL_REJECTED is impossible unless row previously reached MODEL_READY
    # -----------------------------------------------------------------------
    def test_20_model_rejected_impossible_unless_model_ready(self):
        """
        A BLOCKED row can never advance to SCORING_ATOMIC (the entry point for
        MODEL_REJECTED-equivalent BLOCKED-from-scoring).  Attempting to advance
        a BLOCKED row raises ValueError — fail-closed.
        """
        row = _complete_row()
        del row["player"]
        result = run_hydration_check(row, _complete_enrichment())
        self.assertEqual(result.lifecycle_state, LifecycleState.BLOCKED)

        # Cannot advance BLOCKED → SCORING_ATOMIC
        with self.assertRaises(ValueError):
            advance_lifecycle(result, LifecycleState.SCORING_ATOMIC)

        # Cannot advance BLOCKED → MODEL_READY
        with self.assertRaises(ValueError):
            advance_lifecycle(result, LifecycleState.MODEL_READY)

    # -----------------------------------------------------------------------
    # Test 21 — Blocked rows cannot appear in strongest-lane rankings
    # -----------------------------------------------------------------------
    def test_21_blocked_rows_excluded_from_rankings(self):
        """
        RunController.model_ready_row_ids contains ONLY MODEL_READY rows.
        BLOCKED rows are never included — they cannot appear in lane rankings.
        """
        good_ids  = [f"good-{i}" for i in range(4)]
        blocked_ids = [f"bad-{i}" for i in range(3)]

        results = [
            run_hydration_check(_complete_row(rid), _complete_enrichment())
            for rid in good_ids
        ]
        for rid in blocked_ids:
            row = _complete_row(rid)
            del row["event_id"]
            results.append(run_hydration_check(row, _complete_enrichment()))

        ctrl = run_controller(results)

        self.assertEqual(ctrl.contract_complete_count, 4)
        self.assertEqual(ctrl.blocked_count,           3)
        for rid in good_ids:
            self.assertIn(rid, ctrl.model_ready_row_ids)
        for rid in blocked_ids:
            self.assertNotIn(rid, ctrl.model_ready_row_ids)
            self.assertIn(rid, ctrl.blocked_row_ids)

    # -----------------------------------------------------------------------
    # Test 22 — Blocked rows cannot enter slips or exposure ledgers
    # -----------------------------------------------------------------------
    def test_22_blocked_rows_excluded_from_slips_and_exposure(self):
        """
        blocked_row_ids is structurally separate from model_ready_row_ids.
        No blocked row_id appears in model_ready_row_ids — the two sets are
        disjoint.  Callers must only write model_ready_row_ids to exposure
        ledgers or slip construction.
        """
        results = []
        for i in range(5):
            row = _complete_row(f"row-{i}")
            if i % 2 == 0:
                del row["player"]   # every other row is blocked
            results.append(run_hydration_check(row, _complete_enrichment()))

        ctrl = run_controller(results)

        # Disjoint sets
        ready_set   = set(ctrl.model_ready_row_ids)
        blocked_set = set(ctrl.blocked_row_ids)
        self.assertTrue(ready_set.isdisjoint(blocked_set),
                        "model_ready_row_ids and blocked_row_ids must be disjoint")

        # Together they cover all extracted rows
        self.assertEqual(
            len(ready_set) + len(blocked_set),
            ctrl.rows_extracted,
            "Together model_ready + blocked must account for every extracted row",
        )

        # Only MODEL_READY rows may enter exposure ledgers
        for row_id in ctrl.blocked_row_ids:
            self.assertNotIn(
                row_id,
                ctrl.model_ready_row_ids,
                f"Blocked row {row_id!r} must not appear in model_ready_row_ids",
            )

    # -----------------------------------------------------------------------
    # Test 23 — Gate-4 UNAVAILABLE: confidence lane survives, market lane blocked
    # -----------------------------------------------------------------------
    def test_23_market_unavailable_confidence_lane_survives(self):
        """
        When both market_no_vig_probability AND data_timestamp are absent,
        Gate 4 returns MarketGateOutcome.UNAVAILABLE (not BLOCKING).

        The row must reach MODEL_READY because Gates 1/2/3 all pass and the
        market gate outcome is non-blocking.  Lane availability:
          - confidence_lane_available = True   (probability model may run)
          - market_lane_available     = False  (market-edge / money blocked)

        This is the correct behavior per the Full Model Gatekeeper contract
        and reconstructed-confidence architecture: absent market evidence lowers
        the terminal ceiling; it does not prevent model execution.

        Contrast with SOURCE_CONFLICT and STALE_DATA (both BLOCKING) which
        fully block the row (see tests 6 and 8).
        """
        enr = _complete_enrichment()
        del enr["market_no_vig_probability"]
        del enr["data_timestamp"]
        result = run_hydration_check(_complete_row(), enr)

        # Row reaches MODEL_READY despite market data being absent
        self.assertEqual(result.lifecycle_state,     LifecycleState.MODEL_READY)
        self.assertEqual(result.market_gate_outcome, MarketGateOutcome.UNAVAILABLE)

        # Lane separation: confidence ok, market-edge/money blocked
        self.assertTrue(result.confidence_lane_available,
                        "Confidence/model lane must survive when market data is absent")
        self.assertFalse(result.market_lane_available,
                         "Market-edge/money lane must be blocked when market data is absent")

        # Gate 4 recorded the missing fields but is non-blocking
        self.assertFalse(result.gate_results[GATE_MARKET].passed)
        self.assertIn("market_no_vig_probability", result.missing_fields)

        # terminal_label is cleared at MODEL_READY — ceiling is set by scoring layer
        self.assertEqual(result.terminal_label, "")

        # Gates 1/2/3 must all have passed
        for gate_id in (GATE_IDENTITY, GATE_ROLE, GATE_LEDGER):
            self.assertTrue(
                result.gate_results[gate_id].passed,
                f"{gate_id} must pass for MODEL_READY to be reached",
            )


# ---------------------------------------------------------------------------
# Module-level invariant check
# ---------------------------------------------------------------------------

class TestModuleInvariants(unittest.TestCase):
    """Verify module-level governance invariants are set correctly."""

    def test_can_execute_false(self):
        """can_execute must be False unconditionally."""
        self.assertFalse(can_execute)

    def test_patch_id_present(self):
        from gate_engine.typed_hydration import PATCH_ID, DRY_RUN_ONLY, PRODUCTION_AUTHORITY
        self.assertEqual(
            PATCH_ID,
            "WOW-PATCH-2026-08-17-TYPED-HYDRATION-AND-MODEL-READINESS-V1",
        )
        self.assertIn("DRY_RUN_ONLY", DRY_RUN_ONLY)
        self.assertFalse(PRODUCTION_AUTHORITY)

    def test_governance_patch_registered(self):
        """New patch must appear in the active governance registry."""
        from gate_engine.governance import get_governance_status
        status = get_governance_status()
        ids = status["active_patch_ids"]
        self.assertIn(
            "WOW-PATCH-2026-08-17-TYPED-HYDRATION-AND-MODEL-READINESS-V1",
            ids,
        )
        self.assertEqual(status["patch_count"], 26)

    def test_new_labels_in_module_constants(self):
        """
        New RUN_INVALID label strings are defined as module-level constants in
        typed_hydration.py (labels.py is a protected file; per-patch label
        additions live in the originating module).
        """
        self.assertEqual(
            LABEL_RUN_INVALID_HYDRATION_RECONCILIATION,
            "RUN_INVALID — HYDRATION_RECONCILIATION_FAILURE",
        )
        self.assertEqual(
            LABEL_HYDRATION_ABORT,
            "RUN_INVALID — HYDRATION_ABORTED",
        )


if __name__ == "__main__":
    unittest.main()
