"""
gate_engine/tests/test_pipeline_state.py
WOW B4-HARDENING-#193 — Pipeline State Separation + Scoped DATA_CONTRACT_FAIL

Regression tests for gate_engine/universal_agent/pipeline_state.py.

Coverage
--------
TestFailureKindTaxonomy       — constants, ALL set membership
TestPipelineLayerTaxonomy     — constants, ALL set membership
TestUpgradeCeilingTaxonomy    — constants, BLOCKED_FOR_FAILURES entries
TestScopedContractFailure     — immutability, __post_init__ validation,
                                helper properties, row isolation identity
TestUpgradeGuard              — all 5 fail-closed rules (no failure, legitimate
                                outcome, reconstruction, blocked states,
                                technical preserved, contract fail-closed)
TestUpgradeGuardCaseInsensitive — BLOCKED_FOR_FAILURES normalisation
TestRowPipelineState          — layer recording, upstream preservation,
                                row isolation hard error, idempotent failure
TestRowIsolationAcrossRows    — two rows, failure on A cannot propagate to B
TestUpgradeGuardEdgeCases     — boundary conditions, empty preserved_upstream
"""
from __future__ import annotations

import unittest

from gate_engine.universal_agent.pipeline_state import (
    FailureKind,
    PipelineLayer,
    UpgradeCeiling,
    ScopedContractFailure,
    UpgradeGuardResult,
    PipelineStateGuard,
    RowPipelineState,
    can_execute,
    EXECUTION_RULE,
)


# ── Helpers ───────────────────────────────────────────────────────────────────

def _make_guard() -> PipelineStateGuard:
    return PipelineStateGuard()


def _technical_failure(row_id: str = "row-001", **overrides) -> ScopedContractFailure:
    defaults = dict(
        row_id=row_id,
        failure_kind=FailureKind.TECHNICAL,
        failure_code="DB_TIMEOUT",
        failed_at_layer=PipelineLayer.MARKET,
        message="Market gate DB connection timed out",
        reconstruction_attempted=False,
        preserved_upstream_result={"adapter_status": "COMPLETE", "score": 0.72},
    )
    defaults.update(overrides)
    return ScopedContractFailure(**defaults)


def _contract_failure(row_id: str = "row-001", **overrides) -> ScopedContractFailure:
    defaults = dict(
        row_id=row_id,
        failure_kind=FailureKind.CONTRACT,
        failure_code="DATA_CONTRACT_FAIL",
        failed_at_layer=PipelineLayer.ADAPTER,
        message="Required field 'stat_key' missing",
        reconstruction_attempted=False,
        preserved_upstream_result={},
    )
    defaults.update(overrides)
    return ScopedContractFailure(**defaults)


def _outcome_failure(row_id: str = "row-001") -> ScopedContractFailure:
    return ScopedContractFailure(
        row_id=row_id,
        failure_kind=FailureKind.LEGITIMATE_OUTCOME,
        failure_code="NO_PLAY",
        failed_at_layer=PipelineLayer.GOVERNANCE,
        message="Market did not qualify",
        reconstruction_attempted=False,
        preserved_upstream_result={},
    )


# ── Tests: FailureKind ────────────────────────────────────────────────────────

class TestFailureKindTaxonomy(unittest.TestCase):
    def test_constants_exist(self):
        self.assertEqual(FailureKind.TECHNICAL,          "TECHNICAL")
        self.assertEqual(FailureKind.CONTRACT,           "CONTRACT")
        self.assertEqual(FailureKind.LEGITIMATE_OUTCOME, "LEGITIMATE_OUTCOME")

    def test_all_contains_three_entries(self):
        self.assertEqual(len(FailureKind.ALL), 3)

    def test_all_is_frozenset(self):
        self.assertIsInstance(FailureKind.ALL, frozenset)

    def test_all_members(self):
        expected = {
            FailureKind.TECHNICAL,
            FailureKind.CONTRACT,
            FailureKind.LEGITIMATE_OUTCOME,
        }
        self.assertEqual(FailureKind.ALL, expected)


# ── Tests: PipelineLayer ──────────────────────────────────────────────────────

class TestPipelineLayerTaxonomy(unittest.TestCase):
    def test_constants_exist(self):
        layers = [
            PipelineLayer.ACQUISITION, PipelineLayer.ADAPTER,
            PipelineLayer.MARKET, PipelineLayer.MONEY,
            PipelineLayer.SLIP, PipelineLayer.GOVERNANCE,
        ]
        for layer in layers:
            self.assertIsInstance(layer, str)

    def test_all_contains_six_entries(self):
        self.assertEqual(len(PipelineLayer.ALL), 6)

    def test_all_is_frozenset(self):
        self.assertIsInstance(PipelineLayer.ALL, frozenset)

    def test_all_members(self):
        for layer in (
            "ACQUISITION", "ADAPTER", "MARKET", "MONEY", "SLIP", "GOVERNANCE"
        ):
            self.assertIn(layer, PipelineLayer.ALL)


# ── Tests: UpgradeCeiling ─────────────────────────────────────────────────────

class TestUpgradeCeilingTaxonomy(unittest.TestCase):
    def test_blocked_for_failures_is_frozenset(self):
        self.assertIsInstance(UpgradeCeiling.BLOCKED_FOR_FAILURES, frozenset)

    def test_verified_blocked(self):
        self.assertIn("VERIFIED", UpgradeCeiling.BLOCKED_FOR_FAILURES)

    def test_final_approved_blocked(self):
        self.assertIn("FINAL_APPROVED", UpgradeCeiling.BLOCKED_FOR_FAILURES)

    def test_money_blocked(self):
        self.assertIn("MONEY", UpgradeCeiling.BLOCKED_FOR_FAILURES)

    def test_edge_qualified_blocked(self):
        self.assertIn("EDGE_QUALIFIED", UpgradeCeiling.BLOCKED_FOR_FAILURES)

    def test_advisory_not_blocked(self):
        self.assertNotIn("ADVISORY", UpgradeCeiling.BLOCKED_FOR_FAILURES)

    def test_hold_not_blocked(self):
        # MODEL_QUALIFIED_HOLD is the max ceiling for non-failed rows with advisory output
        self.assertNotIn("MODEL_QUALIFIED_HOLD", UpgradeCeiling.BLOCKED_FOR_FAILURES)


# ── Tests: ScopedContractFailure ──────────────────────────────────────────────

class TestScopedContractFailure(unittest.TestCase):
    def test_frozen(self):
        f = _technical_failure()
        with self.assertRaises((AttributeError, TypeError)):
            f.row_id = "other"  # type: ignore[misc]

    def test_is_technical(self):
        f = _technical_failure()
        self.assertTrue(f.is_technical())
        self.assertFalse(f.is_contract())
        self.assertFalse(f.is_legitimate_outcome())

    def test_is_contract(self):
        f = _contract_failure()
        self.assertTrue(f.is_contract())
        self.assertFalse(f.is_technical())

    def test_is_legitimate_outcome(self):
        f = _outcome_failure()
        self.assertTrue(f.is_legitimate_outcome())
        self.assertFalse(f.is_technical())
        self.assertFalse(f.is_contract())

    def test_invalid_failure_kind_raises(self):
        with self.assertRaises(ValueError):
            ScopedContractFailure(
                row_id="r", failure_kind="MADE_UP",
                failure_code="X", failed_at_layer=PipelineLayer.ADAPTER,
                message="bad", reconstruction_attempted=False,
                preserved_upstream_result={},
            )

    def test_invalid_layer_raises(self):
        with self.assertRaises(ValueError):
            ScopedContractFailure(
                row_id="r", failure_kind=FailureKind.TECHNICAL,
                failure_code="X", failed_at_layer="FANTASY_LAYER",
                message="bad", reconstruction_attempted=False,
                preserved_upstream_result={},
            )

    def test_reconstruction_must_be_bool(self):
        with self.assertRaises(TypeError):
            ScopedContractFailure(
                row_id="r", failure_kind=FailureKind.TECHNICAL,
                failure_code="X", failed_at_layer=PipelineLayer.MARKET,
                message="bad", reconstruction_attempted=1,  # type: ignore[arg-type]
                preserved_upstream_result={},
            )

    def test_preserved_upstream_must_be_dict(self):
        with self.assertRaises(TypeError):
            ScopedContractFailure(
                row_id="r", failure_kind=FailureKind.TECHNICAL,
                failure_code="X", failed_at_layer=PipelineLayer.MARKET,
                message="bad", reconstruction_attempted=False,
                preserved_upstream_result=["not", "a", "dict"],  # type: ignore[arg-type]
            )

    def test_preserved_upstream_shallow_copy(self):
        """scope_failure() must not hold a live reference to the caller's dict."""
        guard = _make_guard()
        upstream = {"k": "v"}
        f = guard.scope_failure(
            row_id="r1",
            failure_kind=FailureKind.TECHNICAL,
            failure_code="ERR",
            failed_at_layer=PipelineLayer.MARKET,
            message="msg",
            preserved_upstream_result=upstream,
        )
        upstream["k"] = "mutated"
        self.assertEqual(f.preserved_upstream_result["k"], "v")


# ── Tests: PipelineStateGuard (can_upgrade) ───────────────────────────────────

class TestUpgradeGuard(unittest.TestCase):
    def setUp(self):
        self.guard = _make_guard()

    # Rule 1: no failure → always allowed
    def test_no_failure_allows_advisory(self):
        r = self.guard.can_upgrade(None, UpgradeCeiling.ADVISORY)
        self.assertTrue(r.allowed)
        self.assertEqual(r.reason, "NO_FAILURE")

    def test_no_failure_allows_final_approved(self):
        r = self.guard.can_upgrade(None, UpgradeCeiling.FINAL_APPROVED)
        self.assertTrue(r.allowed)
        self.assertEqual(r.reason, "NO_FAILURE")

    def test_no_failure_no_preserved_upstream(self):
        r = self.guard.can_upgrade(None, UpgradeCeiling.ADVISORY)
        self.assertIsNone(r.preserved_upstream_result)

    # Rule 2: LEGITIMATE_OUTCOME → always allowed
    def test_legitimate_outcome_allows_any_ceiling(self):
        f = _outcome_failure()
        for ceiling in (
            UpgradeCeiling.ADVISORY, UpgradeCeiling.HOLD,
            UpgradeCeiling.VERIFIED, UpgradeCeiling.FINAL_APPROVED,
        ):
            with self.subTest(ceiling=ceiling):
                r = self.guard.can_upgrade(f, ceiling)
                self.assertTrue(r.allowed)
                self.assertEqual(r.reason, "LEGITIMATE_OUTCOME")

    # Rule 3: reconstruction → blocked above ADVISORY
    def test_reconstruction_blocks_hold(self):
        f = _technical_failure(reconstruction_attempted=True)
        r = self.guard.can_upgrade(f, UpgradeCeiling.HOLD)
        self.assertFalse(r.allowed)
        self.assertEqual(r.reason, "RECONSTRUCTION_BLOCKS_UPGRADE")

    def test_reconstruction_blocks_verified(self):
        f = _technical_failure(reconstruction_attempted=True)
        r = self.guard.can_upgrade(f, UpgradeCeiling.VERIFIED)
        self.assertFalse(r.allowed)
        self.assertEqual(r.reason, "RECONSTRUCTION_BLOCKS_UPGRADE")

    def test_reconstruction_allows_advisory(self):
        f = _technical_failure(reconstruction_attempted=True)
        r = self.guard.can_upgrade(f, UpgradeCeiling.ADVISORY)
        # ADVISORY is allowed even with reconstruction
        self.assertTrue(r.allowed)

    # Rule 4: TECHNICAL or CONTRACT + blocked target → denied
    def test_technical_blocks_verified(self):
        f = _technical_failure()
        r = self.guard.can_upgrade(f, UpgradeCeiling.VERIFIED)
        self.assertFalse(r.allowed)
        self.assertIn("TECHNICAL", r.reason)

    def test_technical_blocks_final_approved(self):
        f = _technical_failure()
        r = self.guard.can_upgrade(f, UpgradeCeiling.FINAL_APPROVED)
        self.assertFalse(r.allowed)

    def test_technical_blocks_money(self):
        f = _technical_failure()
        r = self.guard.can_upgrade(f, UpgradeCeiling.MONEY)
        self.assertFalse(r.allowed)

    def test_technical_blocks_edge_qualified(self):
        f = _technical_failure()
        r = self.guard.can_upgrade(f, UpgradeCeiling.EDGE_QUALIFIED)
        self.assertFalse(r.allowed)

    def test_contract_blocks_verified(self):
        f = _contract_failure()
        r = self.guard.can_upgrade(f, UpgradeCeiling.VERIFIED)
        self.assertFalse(r.allowed)

    def test_contract_blocks_final_approved(self):
        f = _contract_failure()
        r = self.guard.can_upgrade(f, UpgradeCeiling.FINAL_APPROVED)
        self.assertFalse(r.allowed)

    # Rule 5: TECHNICAL + non-blocked target → allowed, upstream preserved
    def test_technical_allows_advisory(self):
        f = _technical_failure()
        r = self.guard.can_upgrade(f, UpgradeCeiling.ADVISORY)
        self.assertTrue(r.allowed)
        self.assertEqual(r.reason, "TECHNICAL_FAILURE_UPSTREAM_PRESERVED")

    def test_technical_allows_hold(self):
        f = _technical_failure()
        r = self.guard.can_upgrade(f, UpgradeCeiling.HOLD)
        self.assertTrue(r.allowed)

    def test_technical_allows_watch(self):
        f = _technical_failure()
        r = self.guard.can_upgrade(f, UpgradeCeiling.WATCH)
        self.assertTrue(r.allowed)

    def test_technical_upstream_echoed(self):
        f = _technical_failure(preserved_upstream_result={"adapter_status": "COMPLETE"})
        r = self.guard.can_upgrade(f, UpgradeCeiling.ADVISORY)
        self.assertIsNotNone(r.preserved_upstream_result)
        self.assertEqual(r.preserved_upstream_result["adapter_status"], "COMPLETE")

    def test_technical_upstream_is_copy_not_reference(self):
        upstream = {"k": "original"}
        f = _technical_failure(preserved_upstream_result=upstream)
        r = self.guard.can_upgrade(f, UpgradeCeiling.ADVISORY)
        r.preserved_upstream_result["k"] = "mutated"
        # Original failure record should be unchanged
        self.assertEqual(f.preserved_upstream_result["k"], "original")

    # Rule 6: CONTRACT + non-blocked target → fail-closed
    def test_contract_fail_closed_for_hold(self):
        f = _contract_failure()
        r = self.guard.can_upgrade(f, UpgradeCeiling.HOLD)
        self.assertFalse(r.allowed)
        self.assertEqual(r.reason, "CONTRACT_FAILURE_FAIL_CLOSED")

    def test_contract_fail_closed_for_watch(self):
        f = _contract_failure()
        r = self.guard.can_upgrade(f, UpgradeCeiling.WATCH)
        self.assertFalse(r.allowed)
        self.assertEqual(r.reason, "CONTRACT_FAILURE_FAIL_CLOSED")

    def test_contract_no_preserved_upstream_returned(self):
        f = _contract_failure()
        r = self.guard.can_upgrade(f, UpgradeCeiling.ADVISORY)
        # CONTRACT at ADVISORY: fail-closed; no preserved upstream echoed
        self.assertFalse(r.allowed)
        self.assertIsNone(r.preserved_upstream_result)


# ── Tests: case-insensitive blocked target ────────────────────────────────────

class TestUpgradeGuardCaseInsensitive(unittest.TestCase):
    def setUp(self):
        self.guard = _make_guard()

    def test_lowercase_verified_is_blocked(self):
        f = _technical_failure()
        r = self.guard.can_upgrade(f, "verified")
        self.assertFalse(r.allowed)

    def test_mixedcase_final_approved_is_blocked(self):
        f = _technical_failure()
        r = self.guard.can_upgrade(f, "Final_Approved")
        self.assertFalse(r.allowed)


# ── Tests: scope_failure factory ─────────────────────────────────────────────

class TestScopeFailureFactory(unittest.TestCase):
    def setUp(self):
        self.guard = _make_guard()

    def test_returns_scoped_contract_failure(self):
        f = self.guard.scope_failure(
            row_id="r1",
            failure_kind=FailureKind.TECHNICAL,
            failure_code="TIMEOUT",
            failed_at_layer=PipelineLayer.MARKET,
            message="db timed out",
        )
        self.assertIsInstance(f, ScopedContractFailure)

    def test_defaults(self):
        f = self.guard.scope_failure(
            row_id="r1",
            failure_kind=FailureKind.TECHNICAL,
            failure_code="X",
            failed_at_layer=PipelineLayer.MARKET,
            message="msg",
        )
        self.assertFalse(f.reconstruction_attempted)
        self.assertEqual(f.preserved_upstream_result, {})

    def test_preserved_upstream_shallow_copied(self):
        upstream = {"key": "value"}
        f = self.guard.scope_failure(
            row_id="r1", failure_kind=FailureKind.TECHNICAL,
            failure_code="T", failed_at_layer=PipelineLayer.ADAPTER,
            message="m", preserved_upstream_result=upstream,
        )
        upstream["key"] = "changed"
        self.assertEqual(f.preserved_upstream_result["key"], "value")


# ── Tests: RowPipelineState ───────────────────────────────────────────────────

class TestRowPipelineState(unittest.TestCase):
    def test_initial_state(self):
        state = RowPipelineState(row_id="row-001")
        self.assertFalse(state.has_failure)
        self.assertIsNone(state.failure)
        self.assertEqual(state.completed_layers, ())

    def test_record_layer_complete(self):
        state = RowPipelineState(row_id="row-001")
        state.record_layer_complete(PipelineLayer.ADAPTER, result={"status": "ok"})
        self.assertIn(PipelineLayer.ADAPTER, state.completed_layers)
        self.assertEqual(state.preserved_result_for(PipelineLayer.ADAPTER), {"status": "ok"})

    def test_record_layer_without_result(self):
        state = RowPipelineState(row_id="row-001")
        state.record_layer_complete(PipelineLayer.ACQUISITION)
        self.assertIsNone(state.preserved_result_for(PipelineLayer.ACQUISITION))

    def test_preserved_result_is_copy(self):
        state = RowPipelineState(row_id="row-001")
        result = {"k": "original"}
        state.record_layer_complete(PipelineLayer.ADAPTER, result=result)
        result["k"] = "mutated"
        self.assertEqual(state.preserved_result_for(PipelineLayer.ADAPTER)["k"], "original")

    def test_record_failure(self):
        state = RowPipelineState(row_id="row-001")
        f = _technical_failure(row_id="row-001")
        state.record_failure(f)
        self.assertTrue(state.has_failure)
        self.assertIs(state.failure, f)

    def test_record_failure_idempotent(self):
        """First failure wins; second call is a no-op."""
        state = RowPipelineState(row_id="row-001")
        f1 = _technical_failure(row_id="row-001", failure_code="FIRST")
        f2 = _technical_failure(row_id="row-001", failure_code="SECOND")
        state.record_failure(f1)
        state.record_failure(f2)
        self.assertEqual(state.failure.failure_code, "FIRST")

    def test_wrong_row_id_raises(self):
        """Row isolation: assigning failure from row-A to state for row-B raises."""
        state_b = RowPipelineState(row_id="row-B")
        f_a = _technical_failure(row_id="row-A")
        with self.assertRaises(ValueError) as ctx:
            state_b.record_failure(f_a)
        self.assertIn("isolation", str(ctx.exception))

    def test_check_upgrade_no_failure(self):
        state = RowPipelineState(row_id="row-001")
        r = state.check_upgrade(UpgradeCeiling.FINAL_APPROVED)
        self.assertTrue(r.allowed)

    def test_check_upgrade_technical_advisory(self):
        state = RowPipelineState(row_id="row-001")
        state.record_failure(_technical_failure(row_id="row-001"))
        r = state.check_upgrade(UpgradeCeiling.ADVISORY)
        self.assertTrue(r.allowed)
        self.assertIsNotNone(r.preserved_upstream_result)

    def test_check_upgrade_technical_verified_blocked(self):
        state = RowPipelineState(row_id="row-001")
        state.record_failure(_technical_failure(row_id="row-001"))
        r = state.check_upgrade(UpgradeCeiling.VERIFIED)
        self.assertFalse(r.allowed)

    def test_invalid_layer_raises(self):
        state = RowPipelineState(row_id="row-001")
        with self.assertRaises(ValueError):
            state.record_layer_complete("FANTASY_LAYER")


# ── Tests: row isolation across multiple rows ─────────────────────────────────

class TestRowIsolationAcrossRows(unittest.TestCase):
    """
    Core #193 guarantee: a failure on one row cannot propagate to other rows
    in the same pipeline run.
    """

    def test_failure_on_a_does_not_affect_b(self):
        state_a = RowPipelineState(row_id="row-A")
        state_b = RowPipelineState(row_id="row-B")

        f_a = _technical_failure(row_id="row-A")
        state_a.record_failure(f_a)

        # Row B has no failure
        self.assertFalse(state_b.has_failure)
        r = state_b.check_upgrade(UpgradeCeiling.FINAL_APPROVED)
        self.assertTrue(r.allowed, "Row B should be unrestricted")
        self.assertEqual(r.reason, "NO_FAILURE")

    def test_ten_rows_independent(self):
        states = [RowPipelineState(row_id=f"row-{i:02d}") for i in range(10)]
        # Inject failure into every other row
        guard = _make_guard()
        for i, state in enumerate(states):
            if i % 2 == 0:
                state.record_failure(guard.scope_failure(
                    row_id=state.row_id,
                    failure_kind=FailureKind.TECHNICAL,
                    failure_code="ERR",
                    failed_at_layer=PipelineLayer.MARKET,
                    message="even-row failure",
                ))

        # Odd rows must be unrestricted
        for i, state in enumerate(states):
            if i % 2 == 1:
                r = state.check_upgrade(UpgradeCeiling.FINAL_APPROVED)
                self.assertTrue(r.allowed, f"Row {state.row_id} should be unrestricted")


# ── Tests: edge cases ─────────────────────────────────────────────────────────

class TestUpgradeGuardEdgeCases(unittest.TestCase):
    def setUp(self):
        self.guard = _make_guard()

    def test_empty_target_string_treated_as_unknown(self):
        """An empty target string is not in BLOCKED_FOR_FAILURES; TECHNICAL → allowed."""
        f = _technical_failure()
        r = self.guard.can_upgrade(f, "")
        # "" is not in BLOCKED_FOR_FAILURES; TECHNICAL non-blocked → allowed
        self.assertTrue(r.allowed)

    def test_technical_empty_preserved_upstream(self):
        f = _technical_failure(preserved_upstream_result={})
        r = self.guard.can_upgrade(f, UpgradeCeiling.ADVISORY)
        self.assertTrue(r.allowed)
        self.assertIsNotNone(r.preserved_upstream_result)
        self.assertEqual(r.preserved_upstream_result, {})

    def test_contract_advisory_fail_closed(self):
        """CONTRACT is fail-closed even for ADVISORY, not just for money states."""
        f = _contract_failure()
        r = self.guard.can_upgrade(f, UpgradeCeiling.ADVISORY)
        self.assertFalse(r.allowed)
        self.assertEqual(r.reason, "CONTRACT_FAILURE_FAIL_CLOSED")

    def test_upgrade_guard_result_is_falsy_when_denied(self):
        """UpgradeGuardResult does NOT implement __bool__ — check .allowed explicitly."""
        f = _technical_failure()
        r = self.guard.can_upgrade(f, UpgradeCeiling.VERIFIED)
        # Result is always truthy as a Python object; guard is via .allowed
        self.assertFalse(r.allowed)

    def test_detail_is_non_empty_string(self):
        for failure, ceiling in [
            (None,                UpgradeCeiling.ADVISORY),
            (_technical_failure(), UpgradeCeiling.ADVISORY),
            (_technical_failure(), UpgradeCeiling.VERIFIED),
            (_contract_failure(),  UpgradeCeiling.ADVISORY),
            (_outcome_failure(),   UpgradeCeiling.FINAL_APPROVED),
        ]:
            with self.subTest(failure=failure, ceiling=ceiling):
                r = self.guard.can_upgrade(failure, ceiling)
                self.assertIsInstance(r.detail, str)
                self.assertGreater(len(r.detail), 0)


# ── Tests: module-level safety invariants ────────────────────────────────────

class TestPipelineStateSafetyInvariants(unittest.TestCase):
    def test_can_execute_is_false(self):
        self.assertIs(can_execute, False)

    def test_execution_rule_is_dry_run(self):
        self.assertIn("DRY_RUN", EXECUTION_RULE)
        self.assertIn("NO_LIVE_TRADING", EXECUTION_RULE)


if __name__ == "__main__":
    unittest.main()
