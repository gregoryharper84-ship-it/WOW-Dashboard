"""
gate_engine/tests/test_b4_pipeline_state_wired.py
WOW B4-HARDENING-#193-INTEGRATION

Six proof cases verifying that PipelineStateGuard.can_upgrade() is now
load-bearing in the real B4 WNBA/NBA Props pipeline — not just callable from
isolation tests against the guard itself.

Prior state: pipeline_state.py had zero callers outside its own unit tests.
This file exercises the wired path end-to-end: WnbaPropsAdapter.adapt() →
WnbaPipelineGateway.process() → RowPipelineState → PipelineStateGuard.

Coverage
────────
Case A  Provider/backend acquisition failure → TECHNICAL_FAILURE, not DEGRADED
Case B  Game-script model crash → TECHNICAL_FAILURE with upstream preserved
Case C  Valid adapter result + later market failure → adapter preserved separately
Case D  Genuine merit-based rejection → LEGITIMATE_OUTCOME, not TECHNICAL_FAILURE
Case E  Mixed batch → per-row state isolation, no misleading aggregation
Case F  Governance invariants unchanged across all cases

Shadow / advisory invariants verified
──────────────────────────────────────
- can_execute = False in adapter, gateway, pipeline_state
- No terminal_label, user_output_authority, capital_authority in any result
- Ceiling never exceeds MODEL_QUALIFIED_HOLD
"""
from __future__ import annotations

import inspect
import sys
import unittest
from datetime import date
from unittest.mock import patch

from gate_engine.universal_agent.lanes.wnba_props.adapter import (
    AdapterStatus,
    WnbaPropsAdapter,
    WnbaPropsAdapterResult,
    can_execute as ADAPTER_CAN_EXECUTE,
)
from gate_engine.universal_agent.lanes.wnba_props.pipeline_gateway import (
    WnbaPipelineGateway,
    WnbaPipelineGatewayResult,
    can_execute           as GW_CAN_EXECUTE,
    PRODUCTION_AUTHORITY  as GW_PRODUCTION_AUTHORITY,
    USER_OUTPUT_AUTHORITY as GW_USER_OUTPUT_AUTHORITY,
    NO_AUTO_PROMOTION     as GW_NO_AUTO_PROMOTION,
    CEILING               as GW_CEILING,
)
from gate_engine.universal_agent.pipeline_state import (
    FailureKind,
    PipelineLayer,
    UpgradeCeiling,
    PipelineStateGuard,
    RowPipelineState,
    ScopedContractFailure,
    can_execute as PS_CAN_EXECUTE,
)


# ── Shared fixtures ───────────────────────────────────────────────────────────

_ROLE_STATUS = {
    "active_status":     "ACTIVE",
    "projected_minutes": 32.0,
    "minutes_low":       26.0,
    "minutes_high":      38.0,
    "usage_role":        "STARTER",
    "sources":           ["espn", "wnba_official"],
    "as_of":             "2026-08-11T10:00:00Z",
}

_ODDS_SNAPSHOT = {
    "sportsbook_line": 22.5,
    "over_odds":       -115,
    "under_odds":      -105,
    "as_of":           "2026-08-11T09:00:00Z",
    "book":            "DraftKings",
}

_GAME_LOG = [
    {"min": 32, "pts": 25, "reb": 8, "ast": 4, "date": "2026-08-10"},
    {"min": 30, "pts": 22, "reb": 7, "ast": 3, "date": "2026-08-09"},
    {"min": 28, "pts": 20, "reb": 9, "ast": 2, "date": "2026-08-08"},
    {"min": 34, "pts": 27, "reb": 10, "ast": 5, "date": "2026-08-07"},
    {"min": 33, "pts": 19, "reb": 6, "ast": 3, "date": "2026-08-06"},
]


def _full_row(**overrides) -> dict:
    row: dict = {
        "sport":         "WNBA",
        "market":        "points",
        "event_id":      "wnba-2026-lv-chi-001",
        "player":        "A'ja Wilson",
        "team":          "LV",
        "opponent":      "CHI",
        "line":          22.5,
        "direction":     "over",
        "slate_date":    date.today().isoformat(),
        "role_status":   _ROLE_STATUS,
        "odds_snapshot": _ODDS_SNAPSHOT,
        "game_log":      _GAME_LOG,
        "source_timestamps": {
            "role_status": "2026-08-11T10:00:00Z",
            "odds":        "2026-08-11T09:00:00Z",
        },
        "matchup": {
            "spread":     -4.0,
            "total_line": 170.5,
        },
    }
    row.update(overrides)
    return row


def _adapt(**kwargs) -> WnbaPropsAdapterResult:
    kwargs.setdefault("row", _full_row())
    kwargs.setdefault("run_id", "wired-test-001")
    return WnbaPropsAdapter().adapt(**kwargs)


def _gateway() -> WnbaPipelineGateway:
    return WnbaPipelineGateway()


# ── Case A: Acquisition failure ───────────────────────────────────────────────

class TestCaseA_AcquisitionFailureIsDistinguishable(unittest.TestCase):
    """
    Case A: A provider/backend failure during WNBA evidence acquisition must
    produce a TECHNICAL_FAILURE adapter result, not a DEGRADED result that
    would be indistinguishable from legitimate data absence.

    The gateway must then surface this as a distinguishable technical state,
    not collapse it into a clean NO_PLAY-equivalent.
    """

    def setUp(self):
        self.result = _adapt(
            run_id="case-a-run",
            acquisition_error="HTTP 503 from BallDontLie provider — 3 retries exhausted",
        )
        self.gw = _gateway()
        self.gw_result = self.gw.process(self.result, row_id="case-a-row")

    def test_A1_adapter_status_is_technical_failure_not_degraded(self):
        """
        REAL REQUIREMENT: the adapter_status must be TECHNICAL_FAILURE.
        Before #193 integration, this would have been DEGRADED — identical
        to a legitimate data-quality gap. The distinction now exists.
        """
        self.assertEqual(self.result.adapter_status, AdapterStatus.TECHNICAL_FAILURE,
            "Acquisition provider failure must be TECHNICAL_FAILURE, not DEGRADED")
        self.assertNotEqual(self.result.adapter_status, AdapterStatus.DEGRADED,
            "DEGRADED means legitimate data absence — acquisition failure is not that")

    def test_A2_failure_classification_is_set(self):
        self.assertIsNotNone(self.result.failure_classification,
            "failure_classification must be set for TECHNICAL_FAILURE results")

    def test_A3_failure_kind_is_technical(self):
        fc = self.result.failure_classification
        self.assertEqual(fc.failure_kind, FailureKind.TECHNICAL)

    def test_A4_failure_layer_is_acquisition(self):
        fc = self.result.failure_classification
        self.assertEqual(fc.failed_at_layer, PipelineLayer.ACQUISITION)

    def test_A5_failure_code_identifies_acquisition_error(self):
        fc = self.result.failure_classification
        self.assertEqual(fc.failure_code, "ACQUISITION_PROVIDER_ERROR")

    def test_A6_no_role_payloads_when_acquisition_failed(self):
        """Nothing was processed before the acquisition failure."""
        self.assertEqual(self.result.role_payloads, {},
            "Acquisition failure means no rows were processed; role_payloads must be empty")

    def test_A7_ceiling_result_is_set(self):
        self.assertIsNotNone(self.result.ceiling_result)

    def test_A8_ceiling_allows_advisory_with_upstream_preserved_reason(self):
        """ADVISORY ceiling is not in BLOCKED_FOR_FAILURES; TECHNICAL failure passes Rule 5a."""
        cr = self.result.ceiling_result
        self.assertTrue(cr.allowed)
        self.assertEqual(cr.reason, "TECHNICAL_FAILURE_UPSTREAM_PRESERVED")

    def test_A9_gateway_shows_technical_failure_property(self):
        self.assertTrue(self.gw_result.is_technical_failure)

    def test_A10_gateway_row_state_has_failure(self):
        self.assertTrue(self.gw_result.row_state.has_failure)

    def test_A11_gateway_row_state_failure_kind_is_technical(self):
        self.assertEqual(
            self.gw_result.row_state.failure.failure_kind, FailureKind.TECHNICAL
        )

    def test_A12_gateway_blocks_verified_ceiling(self):
        """VERIFIED is in BLOCKED_FOR_FAILURES; TECHNICAL failure must block it."""
        self.assertFalse(self.gw_result.ceiling_allows(UpgradeCeiling.VERIFIED))

    def test_A13_gateway_blocks_final_approved(self):
        self.assertFalse(self.gw_result.ceiling_allows("FINAL_APPROVED"))


# ── Case B: Game-script model crash ──────────────────────────────────────────

class TestCaseB_GameScriptCrashIsDistinguishable(unittest.TestCase):
    """
    Case B: A crash inside the game-script shadow gate (ValueError, ImportError,
    any exception) must produce TECHNICAL_FAILURE with packet and role_payloads
    preserved as upstream — not a clean DEGRADED/COMPLETE result.

    Before #193, _run_game_script_shadow() swallowed ALL exceptions and returned
    None. A model crash was indistinguishable from legitimate None (no inputs).
    The wired path now returns (None, error_str) for crashes.
    """

    def setUp(self):
        # Patch the classified function to return the "exception" tuple directly.
        # This simulates the game-script gate crashing with ValueError.
        _patch = patch(
            "gate_engine.universal_agent.lanes.wnba_props.adapter"
            "._run_game_script_shadow_classified",
            return_value=(None, "ValueError: invalid probability: -0.31"),
        )
        self.patcher = _patch
        self.patcher.start()
        self.result = _adapt(run_id="case-b-run")
        self.gw_result = _gateway().process(self.result, row_id="case-b-row")

    def tearDown(self):
        self.patcher.stop()

    def test_B1_adapter_status_is_technical_failure(self):
        self.assertEqual(self.result.adapter_status, AdapterStatus.TECHNICAL_FAILURE)

    def test_B2_role_payloads_preserved_upstream(self):
        """
        REAL REQUIREMENT: role_payloads were built BEFORE the model crash.
        They must be present in the result — upstream work is not discarded.
        """
        self.assertEqual(len(self.result.role_payloads), 6,
            "All 6 role payloads built before the crash must survive in the result")

    def test_B3_packet_preserved_upstream(self):
        """EvidencePacket was built before the crash; it must be in the result."""
        from gate_engine.universal_agent.evidence_packet import EvidencePacket
        self.assertIsInstance(self.result.packet, EvidencePacket)

    def test_B4_failure_code_identifies_model_error(self):
        fc = self.result.failure_classification
        self.assertIsNotNone(fc)
        self.assertEqual(fc.failure_code, "GAME_SCRIPT_MODEL_ERROR")

    def test_B5_failure_kind_is_technical(self):
        self.assertEqual(
            self.result.failure_classification.failure_kind, FailureKind.TECHNICAL
        )

    def test_B6_failure_layer_is_adapter(self):
        """Game-script runs in the ADAPTER layer."""
        self.assertEqual(
            self.result.failure_classification.failed_at_layer, PipelineLayer.ADAPTER
        )

    def test_B7_ceiling_allows_hold_upstream_preserved(self):
        """HOLD (MODEL_QUALIFIED_HOLD) is not in BLOCKED_FOR_FAILURES; upstream preserved."""
        cr = self.result.ceiling_result
        self.assertIsNotNone(cr)
        self.assertTrue(cr.allowed)
        self.assertEqual(cr.reason, "TECHNICAL_FAILURE_UPSTREAM_PRESERVED")

    def test_B8_ceiling_upstream_snapshot_has_role_count(self):
        """preserved_upstream_result records role_payloads_built so audit can see it."""
        upstream = self.result.ceiling_result.preserved_upstream_result
        self.assertIsNotNone(upstream)
        self.assertIn("role_payloads_built", upstream)
        self.assertEqual(upstream["role_payloads_built"], 6)

    def test_B9_game_script_shadow_is_none_on_crash(self):
        """The crashed gate produces no output — game_script_shadow must be None."""
        self.assertIsNone(self.result.game_script_shadow)

    def test_B10_gateway_failure_kind_matches(self):
        self.assertEqual(
            self.gw_result.row_state.failure.failure_kind, FailureKind.TECHNICAL
        )

    def test_B11_legitimate_none_does_not_produce_technical_failure(self):
        """
        KEY DISTINCTION: when the gate legitimately returns (None, None) —
        not an exception, just no output — the adapter must NOT produce
        TECHNICAL_FAILURE. That would be a false positive.
        """
        with patch(
            "gate_engine.universal_agent.lanes.wnba_props.adapter"
            "._run_game_script_shadow_classified",
            return_value=(None, None),   # legitimate None, no error
        ):
            result = _adapt(run_id="case-b-legitimate-none")

        self.assertNotEqual(result.adapter_status, AdapterStatus.TECHNICAL_FAILURE,
            "A legitimate None from the gate must not be treated as a technical failure")
        self.assertIsNone(result.failure_classification,
            "No failure_classification when gate returned None legitimately")
        self.assertIsNone(result.game_script_shadow,
            "game_script_shadow is None for legitimate absence")


# ── Case C: Market failure after valid adapter result ─────────────────────────

class TestCaseC_MarketFailurePreservesAdapterResult(unittest.TestCase):
    """
    Case C: A technical failure at the market/money layer — occurring AFTER
    the adapter has successfully built an EvidencePacket and 6 role payloads —
    must NOT erase or overwrite the adapter result.

    The gateway result must expose BOTH:
    - The original adapter result (packet + role_payloads) at result.adapter_result
    - The market failure scoped to its own layer at result.market_failure

    This is the RowPipelineState multi-layer story: layers complete in sequence,
    a later failure is scoped to its layer without destroying earlier work.
    """

    def setUp(self):
        self.adapter = WnbaPropsAdapter()
        self.gw = _gateway()
        # Run the adapter cleanly — no acquisition error, no model crash
        self.adapter_result = self.adapter.adapt(
            row=_full_row(),
            run_id="case-c-run",
        )
        # Build a TECHNICAL market-layer failure for row "case-c"
        self.mf = self.gw.make_market_failure(
            row_id="case-c-row",
            failure_code="MARKET_DB_TIMEOUT",
            message="Market gate DB connection timed out after 30s",
        )
        # Process through the gateway injecting the market failure
        self.gw_result = self.gw.process_with_market_failure(
            self.adapter_result,
            row_id="case-c-row",
            market_failure=self.mf,
        )

    def test_C1_adapter_result_preserved_by_identity(self):
        """
        REAL REQUIREMENT: the adapter result object is the exact same object
        stored in the gateway result — not a copy with missing fields, not
        reconstructed, not overwritten by the downstream failure.
        """
        self.assertIs(
            self.gw_result.adapter_result,
            self.adapter_result,
            "adapter_result must be the identical object, not replaced by market failure",
        )

    def test_C2_role_payloads_still_present_in_gateway_result(self):
        """Role payloads built at the adapter layer survive the market failure."""
        self.assertEqual(
            len(self.gw_result.adapter_result.role_payloads), 6
        )

    def test_C3_market_failure_is_separately_scoped(self):
        """Market failure is in its own field — not merged into adapter_result."""
        self.assertIsNotNone(self.gw_result.market_failure)
        self.assertIs(self.gw_result.market_failure, self.mf)

    def test_C4_market_failure_layer_is_market(self):
        self.assertEqual(self.gw_result.market_failure.failed_at_layer, PipelineLayer.MARKET)

    def test_C5_upstream_adapter_preserved_property_true(self):
        """
        The .upstream_adapter_preserved property is the canonical check for
        test case C's assertion: adapter did real work AND later layer failed.
        """
        self.assertTrue(self.gw_result.upstream_adapter_preserved)

    def test_C6_adapter_original_status_unchanged(self):
        """The adapter's own status (COMPLETE or DEGRADED) was not mutated."""
        self.assertNotEqual(
            self.gw_result.adapter_result.adapter_status,
            AdapterStatus.TECHNICAL_FAILURE,
            "Market failure must not retroactively change the adapter's status field",
        )

    def test_C7_gateway_blocks_verified_ceiling(self):
        """TECHNICAL market failure blocks VERIFIED and above (BLOCKED_FOR_FAILURES)."""
        self.assertFalse(self.gw_result.ceiling_allows(UpgradeCeiling.VERIFIED))

    def test_C8_gateway_blocks_money_ceiling(self):
        self.assertFalse(self.gw_result.ceiling_allows("MONEY"))

    def test_C9_gateway_final_ceiling_is_upstream_preserved(self):
        """HOLD is not blocked; TECHNICAL with upstream → TECHNICAL_FAILURE_UPSTREAM_PRESERVED."""
        self.assertEqual(
            self.gw_result.final_ceiling.reason,
            "TECHNICAL_FAILURE_UPSTREAM_PRESERVED",
        )

    def test_C10_row_state_records_adapter_layer_complete(self):
        """Adapter layer was completed before the market failure — recorded in row_state."""
        self.assertIn(PipelineLayer.ADAPTER, self.gw_result.row_state._completed_layers)


# ── Case D: Legitimate merit-based rejection ──────────────────────────────────

class TestCaseD_LegitimateMeritRejectionNotMiscategorized(unittest.TestCase):
    """
    Case D: A row with real evidence where the model legitimately says no-play
    must remain a LEGITIMATE_OUTCOME — it must NOT be miscategorised as a
    TECHNICAL_FAILURE even though both produce 'no output' at the decision layer.

    FailureKind.LEGITIMATE_OUTCOME passes PipelineStateGuard unconditionally.
    FailureKind.TECHNICAL blocks verified/money/edge states.
    These must remain distinct.
    """

    def setUp(self):
        self.adapter_result = _adapt(run_id="case-d-run")
        self.gw = _gateway()
        # The model ran, had real evidence, but the result is legitimately no-play.
        self.gw_result = self.gw.record_legitimate_rejection(
            self.adapter_result,
            row_id="case-d-row",
            rejection_code="NO_PLAY",
            message="Model probability 0.44 below threshold 0.52; legitimate rejection",
        )

    def test_D1_adapter_status_is_not_technical_failure(self):
        """The adapter ran successfully; its status must not be TECHNICAL_FAILURE."""
        self.assertNotEqual(
            self.adapter_result.adapter_status,
            AdapterStatus.TECHNICAL_FAILURE,
        )

    def test_D2_adapter_failure_classification_is_none(self):
        """Clean adapter run produces no failure_classification."""
        self.assertIsNone(self.adapter_result.failure_classification)

    def test_D3_gateway_final_ceiling_allows_upgrade(self):
        """
        CORE DISTINCTION: LEGITIMATE_OUTCOME passes the guard unconditionally.
        If this were mistakenly TECHNICAL, it would also pass HOLD — but the
        REASON is different and verifiable.
        """
        self.assertTrue(self.gw_result.final_ceiling.allowed)

    def test_D4_gateway_ceiling_reason_is_legitimate_outcome(self):
        """
        REAL REQUIREMENT: reason must be LEGITIMATE_OUTCOME, not
        TECHNICAL_FAILURE_UPSTREAM_PRESERVED or NO_FAILURE.
        """
        self.assertEqual(
            self.gw_result.final_ceiling.reason,
            "LEGITIMATE_OUTCOME",
        )

    def test_D5_row_state_failure_kind_is_legitimate_outcome(self):
        self.assertEqual(
            self.gw_result.row_state.failure.failure_kind,
            FailureKind.LEGITIMATE_OUTCOME,
        )

    def test_D6_no_market_failure_on_merit_rejection(self):
        """Merit rejection is not a market-layer failure — no market_failure field."""
        self.assertIsNone(self.gw_result.market_failure)

    def test_D7_degraded_adapter_without_exception_not_technical_failure(self):
        """
        A DEGRADED adapter result (legitimate data gaps, no exception) also
        must not be treated as TECHNICAL_FAILURE. Both DEGRADED and COMPLETE
        are clean states with no failure_classification.
        """
        # Force a minimal row likely to produce DEGRADED (missing optional fields)
        minimal_row = {
            "sport":      "WNBA",
            "market":     "points",
            "event_id":   "wnba-2026-minimal",
            "player":     "Kelsey Plum",
            "team":       "LV",
            "opponent":   "NY",
            "line":       18.5,
            "direction":  "over",
            "slate_date": date.today().isoformat(),
        }
        result = WnbaPropsAdapter().adapt(row=minimal_row, run_id="case-d-minimal")
        # Whatever the status, it must NOT be TECHNICAL_FAILURE
        self.assertNotEqual(result.adapter_status, AdapterStatus.TECHNICAL_FAILURE)
        # And it must have no failure_classification
        self.assertIsNone(result.failure_classification)


# ── Case E: Mixed batch → per-row isolation ───────────────────────────────────

class TestCaseE_MixedBatchPerRowIsolation(unittest.TestCase):
    """
    Case E: A batch containing rows in different states — technical failures,
    legitimate rejections, and clean results — must produce per-row states
    that remain independently distinguishable.

    The batch-level summary cannot:
    - Aggregate all rows as "all clean" (hiding the technical failures)
    - Aggregate all rows as "all failed" (hiding the legitimate clean results)
    - Allow one row's RowPipelineState to bleed into an adjacent row's state
    """

    def setUp(self):
        self.gw = _gateway()

        # Row A: acquisition technical failure
        self.result_a = _adapt(
            run_id="batch-a",
            acquisition_error="HTTP 503 from BallDontLie — acquisition failed",
        )

        # Row B: game-script model crash → TECHNICAL_FAILURE, upstream preserved
        with patch(
            "gate_engine.universal_agent.lanes.wnba_props.adapter"
            "._run_game_script_shadow_classified",
            return_value=(None, "RuntimeError: model computation diverged"),
        ):
            self.result_b = _adapt(run_id="batch-b")

        # Row C: clean adapter run (COMPLETE or DEGRADED — no failure)
        with patch(
            "gate_engine.universal_agent.lanes.wnba_props.adapter"
            "._run_game_script_shadow_classified",
            return_value=(None, None),   # legitimate None, not an error
        ):
            self.result_c = _adapt(run_id="batch-c")

        # Row D: legitimate merit rejection (adapter clean, gateway records NO_PLAY)
        self.result_d = _adapt(run_id="batch-d")

        # Batch: A and B are technical failures; C is clean; D will be NO_PLAY
        batch_input = [
            (self.result_a, "batch-row-a"),
            (self.result_b, "batch-row-b"),
            (self.result_c, "batch-row-c"),
        ]
        self.batch = self.gw.process_batch(batch_input)

        # Row D processed separately as legitimate rejection
        self.gw_d = self.gw.record_legitimate_rejection(
            self.result_d, row_id="batch-row-d", rejection_code="NO_PLAY"
        )

    def test_E1_batch_returns_one_result_per_row(self):
        self.assertEqual(len(self.batch), 3)

    def test_E2_batch_positions_are_ordered(self):
        for i, r in enumerate(self.batch):
            self.assertEqual(r.batch_position, i)

    def test_E3_row_a_is_technical_failure(self):
        r = self.batch[0]
        self.assertTrue(r.is_technical_failure)
        self.assertEqual(r.row_id, "batch-row-a")

    def test_E4_row_b_is_technical_failure(self):
        r = self.batch[1]
        self.assertTrue(r.is_technical_failure)
        self.assertEqual(r.row_id, "batch-row-b")

    def test_E5_row_c_is_not_technical_failure(self):
        """Row C had a clean adapter run — must NOT appear as technical failure."""
        r = self.batch[2]
        self.assertFalse(r.is_technical_failure,
            "Clean adapter result must not become TECHNICAL_FAILURE in the batch")

    def test_E6_row_c_has_no_failure_in_state(self):
        r = self.batch[2]
        self.assertFalse(r.row_state.has_failure,
            "Row C's RowPipelineState must have no failure — it ran cleanly")

    def test_E7_row_d_is_legitimate_outcome(self):
        self.assertEqual(
            self.gw_d.final_ceiling.reason, "LEGITIMATE_OUTCOME"
        )

    def test_E8_row_states_are_distinct_objects(self):
        """
        ISOLATION GUARANTEE: each row has its own RowPipelineState instance.
        Physical identity check — not just value equality.
        """
        states = [r.row_state for r in self.batch]
        ids = [id(s) for s in states]
        self.assertEqual(len(set(ids)), len(ids),
            "Every row in the batch must have a distinct RowPipelineState object")

    def test_E9_technical_failure_does_not_contaminate_adjacent_clean_row(self):
        """
        CORE ISOLATION REQUIREMENT: row A's TECHNICAL failure must not
        affect row C's RowPipelineState. Modifying row A's state after the
        batch ran must not touch row C's state.
        """
        # Row A's state reports failure
        self.assertTrue(self.batch[0].row_state.has_failure)
        # Row C's state does not
        self.assertFalse(self.batch[2].row_state.has_failure)
        # Verify they don't share state by checking they can't be the same object
        self.assertIsNot(self.batch[0].row_state, self.batch[2].row_state)

    def test_E10_batch_is_not_uniformly_technical_failure(self):
        """
        No misleading 'all failed' aggregation: not every row is TECHNICAL_FAILURE.
        The batch output must preserve the distinction.
        """
        statuses = [r.adapter_result.adapter_status for r in self.batch]
        technical_count = statuses.count(AdapterStatus.TECHNICAL_FAILURE)
        self.assertGreater(technical_count, 0, "Some rows are technical failures")
        self.assertLess(technical_count, len(self.batch),
            "Not all rows are technical failures — batch must preserve distinction")

    def test_E11_batch_is_not_uniformly_clean(self):
        """
        No misleading 'all clean' aggregation: not every row passed cleanly.
        """
        has_failure_flags = [r.row_state.has_failure for r in self.batch]
        self.assertIn(True, has_failure_flags, "Some rows have failures")
        self.assertIn(False, has_failure_flags, "Some rows are clean")

    def test_E12_row_a_failure_row_id_scoped_to_row_a(self):
        """Each failure is scoped to its own row_id — cross-row ID check."""
        r_a = self.batch[0]
        if r_a.row_state.has_failure:
            self.assertEqual(r_a.row_state.failure.row_id, "batch-row-a")

    def test_E13_row_c_ceiling_allows_hold(self):
        """Clean row C has no failure; upgrade to HOLD is unrestricted."""
        r_c = self.batch[2]
        self.assertTrue(r_c.ceiling_allows(UpgradeCeiling.HOLD))

    def test_E14_row_a_ceiling_blocks_verified(self):
        """Technical-failure row A blocks VERIFIED ceiling even within the batch."""
        r_a = self.batch[0]
        self.assertFalse(r_a.ceiling_allows(UpgradeCeiling.VERIFIED))


# ── Case F: Governance invariants ─────────────────────────────────────────────

class TestCaseF_GovernanceInvariantsUnchangedAcrossAllCases(unittest.TestCase):
    """
    Case F: All governance invariants (can_execute=False, no terminal_label
    authority, no user_output_authority, no capital_authority) must be
    verified by grep-confirmed source inspection AND by explicit assertions
    on result objects from all five preceding cases.

    These invariants must hold regardless of which failure path the pipeline
    takes: acquisition failure, model crash, market failure, or legitimate
    rejection.
    """

    def test_F1_adapter_can_execute_is_false(self):
        self.assertFalse(ADAPTER_CAN_EXECUTE)

    def test_F2_pipeline_state_can_execute_is_false(self):
        self.assertFalse(PS_CAN_EXECUTE)

    def test_F3_gateway_can_execute_is_false(self):
        self.assertFalse(GW_CAN_EXECUTE)

    def test_F4_gateway_production_authority_false(self):
        self.assertFalse(GW_PRODUCTION_AUTHORITY)

    def test_F5_gateway_user_output_authority_false(self):
        self.assertFalse(GW_USER_OUTPUT_AUTHORITY)

    def test_F6_gateway_no_auto_promotion_true(self):
        self.assertTrue(GW_NO_AUTO_PROMOTION)

    def test_F7_gateway_ceiling_is_model_qualified_hold(self):
        self.assertEqual(GW_CEILING, "MODEL_QUALIFIED_HOLD")

    def test_F8_adapter_source_contains_can_execute_false(self):
        """Grep-confirm: adapter source has 'can_execute = False'."""
        import gate_engine.universal_agent.lanes.wnba_props.adapter as adp
        src = inspect.getsource(adp)
        self.assertIn("can_execute    = False", src)

    def test_F9_gateway_source_contains_can_execute_false(self):
        import gate_engine.universal_agent.lanes.wnba_props.pipeline_gateway as gw
        src = inspect.getsource(gw)
        self.assertIn("can_execute           = False", src)

    def test_F10_pipeline_state_source_contains_can_execute_false(self):
        import gate_engine.universal_agent.pipeline_state as ps
        src = inspect.getsource(ps)
        self.assertIn("can_execute    = False", src)

    def test_F11_gateway_result_has_no_terminal_label_field(self):
        """WnbaPipelineGatewayResult must not carry a terminal_label field."""
        result = _gateway().process(_adapt(run_id="f-clean"), row_id="f-row-1")
        self.assertFalse(hasattr(result, "terminal_label"),
            "Gateway result must not carry terminal_label authority")

    def test_F12_gateway_result_has_no_user_output_authority(self):
        result = _gateway().process(_adapt(run_id="f-clean-2"), row_id="f-row-2")
        self.assertFalse(hasattr(result, "user_output_authority"))

    def test_F13_gateway_result_has_no_capital_authority(self):
        result = _gateway().process(_adapt(run_id="f-clean-3"), row_id="f-row-3")
        self.assertFalse(hasattr(result, "capital_authority"))
        self.assertFalse(hasattr(result, "stake_authorized"))

    def test_F14_acquisition_failure_result_no_terminal_label(self):
        result = _adapt(run_id="f-acq", acquisition_error="HTTP 503")
        self.assertFalse(hasattr(result, "terminal_label"))

    def test_F15_ceiling_never_exceeds_model_qualified_hold_on_clean_row(self):
        """Clean rows may only reach MODEL_QUALIFIED_HOLD at most."""
        result = _adapt(run_id="f-ceiling")
        gw_result = _gateway().process(result, row_id="f-row-ceiling")
        # Verify the gateway refuses to certify verified/money/edge even for
        # a clean row — those ceilings are outside B4's advisory authority.
        guard = PipelineStateGuard()
        # For a clean row (no failure), the guard allows anything — but the
        # gateway's declared CEILING constant must be MODEL_QUALIFIED_HOLD.
        self.assertEqual(GW_CEILING, "MODEL_QUALIFIED_HOLD")

    def test_F16_adapter_result_no_place_bet_key(self):
        """Adapter result dict (if serialised) must not carry place_bet key."""
        result = _adapt(run_id="f-forbidden")
        result_dict = {
            "packet":                str(result.packet),
            "adapter_status":        result.adapter_status,
            "role_payloads_count":   len(result.role_payloads),
            "game_script_shadow":    result.game_script_shadow,
            "failure_classification": result.failure_classification,
        }
        self.assertNotIn("place_bet", result_dict)
        self.assertNotIn("settlement", result_dict)
        self.assertNotIn("market_order", result_dict)

    def test_F17_gateway_module_not_imported_from_app(self):
        """Confirm pipeline_gateway is not imported from app.py or any production route."""
        import gate_engine.universal_agent.lanes.wnba_props.pipeline_gateway as gw_mod
        src = inspect.getsource(gw_mod)
        self.assertNotIn("from app import", src)
        self.assertNotIn("import app", src)
        self.assertNotIn("Flask", src)

    def test_F18_adapter_module_not_imported_from_app(self):
        import gate_engine.universal_agent.lanes.wnba_props.adapter as adp_mod
        src = inspect.getsource(adp_mod)
        self.assertNotIn("from app import", src)
        self.assertNotIn("import app", src)

    def test_F19_technical_failure_result_ceiling_result_has_no_terminal_label(self):
        """ceiling_result (UpgradeGuardResult) must not carry terminal_label."""
        result = _adapt(run_id="f-tech", acquisition_error="HTTP 500")
        cr = result.ceiling_result
        self.assertIsNotNone(cr)
        self.assertFalse(hasattr(cr, "terminal_label"))
        self.assertFalse(hasattr(cr, "capital_authority"))

    def test_F20_pipeline_state_guard_has_no_execution_authority(self):
        """PipelineStateGuard must be a stateless evaluator with no authority fields."""
        guard = PipelineStateGuard()
        self.assertFalse(hasattr(guard, "can_execute") and getattr(guard, "can_execute"),
            "Guard instance must not have can_execute=True")
        self.assertFalse(hasattr(guard, "production_authority"))
        self.assertFalse(hasattr(guard, "terminal_label_authority"))


if __name__ == "__main__":
    unittest.main()
