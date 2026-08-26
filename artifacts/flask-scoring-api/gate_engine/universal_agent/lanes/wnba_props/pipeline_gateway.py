"""
gate_engine/universal_agent/lanes/wnba_props/pipeline_gateway.py
WOW B4-HARDENING-#193-INTEGRATION

Post-adapter pipeline gateway — the load-bearing connection between
pipeline_state.py and the real B4 WNBA/NBA Props adapter.

This module IS where PipelineStateGuard.can_upgrade() is called for every
row that passes through the B4 pipeline. Before this module existed,
pipeline_state.py had zero callers and the safety property (cannot collapse
a technical failure into a legitimate NO_PLAY) was not active anywhere.

Responsibilities
────────────────
1. Accept a WnbaPropsAdapterResult and wrap it in per-row RowPipelineState.
2. Propagate any adapter-detected failure (TECHNICAL / CONTRACT) into the row
   state, then evaluate the upgrade ceiling via PipelineStateGuard.
3. Allow post-adapter market/money-layer failure injection (test case c):
   the adapter result is preserved while the downstream failure is scoped
   separately.
4. Record legitimate merit-based rejections (FailureKind.LEGITIMATE_OUTCOME)
   without miscategorising them as technical failures.
5. Process batches of rows with strict per-row isolation: a failure on row A
   physically cannot affect row B's RowPipelineState.

Shadow / advisory invariants
─────────────────────────────
can_execute                = False
PRODUCTION_AUTHORITY       = False
USER_OUTPUT_AUTHORITY      = False
NO_AUTO_PROMOTION          = True
CEILING                    = "MODEL_QUALIFIED_HOLD"

No terminal labels, no capital authority, no settlement authority.
No imports from app.py or any production route.
"""
from __future__ import annotations

from dataclasses import dataclass
from typing import Optional

from gate_engine.universal_agent.lanes.wnba_props.adapter import (
    WnbaPropsAdapterResult,
    AdapterStatus,
)
from gate_engine.universal_agent.pipeline_state import (
    FailureKind,
    PipelineLayer,
    UpgradeCeiling,
    UpgradeGuardResult,
    PipelineStateGuard,
    ScopedContractFailure,
    RowPipelineState,
)

# ── Module-level safety constants ─────────────────────────────────────────────
can_execute           = False
PRODUCTION_AUTHORITY  = False
USER_OUTPUT_AUTHORITY = False
NO_AUTO_PROMOTION     = True
CEILING               = "MODEL_QUALIFIED_HOLD"
EXECUTION_RULE        = "DRY_RUN_ONLY_NO_LIVE_TRADING_NO_MARKET_ORDERS"

_GUARD = PipelineStateGuard()   # stateless, thread-safe


# ── Gateway result ────────────────────────────────────────────────────────────

@dataclass
class WnbaPipelineGatewayResult:
    """
    Result of processing one row through the B4 pipeline gateway.

    adapter_result
        The WnbaPropsAdapterResult from the adapter layer. Always present.
        When a market/money failure is injected, the adapter result (including
        packet and role_payloads) remains visible here — it is NOT erased.

    row_state
        The RowPipelineState tracking all layers for this specific row.
        Each row gets its own instance; failures cannot bleed across rows.

    final_ceiling
        The UpgradeGuardResult for the HOLD ceiling (MODEL_QUALIFIED_HOLD).
        This is the load-bearing guard call: if allowed=False, the B4
        pipeline's output reflects that, not a clean COMPLETE/DEGRADED result.

    market_failure
        The market-layer ScopedContractFailure, if one was injected.
        None when the row failed only at the adapter layer or not at all.

    batch_position
        Optional integer position when produced from process_batch().
        Used in batch-level assertions to confirm per-row ordering.
    """
    row_id:          str
    adapter_result:  WnbaPropsAdapterResult
    row_state:       RowPipelineState       # per-row, isolated
    final_ceiling:   UpgradeGuardResult     # load-bearing guard result
    market_failure:  Optional[ScopedContractFailure] = None
    batch_position:  Optional[int] = None

    @property
    def is_technical_failure(self) -> bool:
        return self.adapter_result.adapter_status == AdapterStatus.TECHNICAL_FAILURE

    @property
    def is_contract_failure(self) -> bool:
        return self.adapter_result.adapter_status == AdapterStatus.CONTRACT_FAILURE

    @property
    def has_market_failure(self) -> bool:
        return self.market_failure is not None

    @property
    def upstream_adapter_preserved(self) -> bool:
        """
        True when there is a market-layer failure but the adapter result
        (packet + role_payloads) is intact. The defining property of test
        case (c): downstream failure does NOT erase valid upstream modeling.
        """
        return (
            self.has_market_failure
            and self.adapter_result.packet is not None
            and len(self.adapter_result.role_payloads) > 0
        )

    def ceiling_allows(self, target: str) -> bool:
        return _GUARD.can_upgrade(self.row_state.failure, target).allowed


# ── Gateway ───────────────────────────────────────────────────────────────────

class WnbaPipelineGateway:
    """
    Processes WnbaPropsAdapterResult objects through the pipeline state layer.

    This is the class that makes PipelineStateGuard.can_upgrade() load-bearing
    in the B4 pipeline. Calling process() (or any variant) guarantees that:
    - A TECHNICAL failure produces a gateway result where final_ceiling.allowed
      is False for verified/money/edge ceilings.
    - A CONTRACT failure produces a gateway result that is fail-closed at all
      levels above ADVISORY.
    - A LEGITIMATE_OUTCOME produces a gateway result that passes unconditionally.
    - Row isolation is physically enforced by giving each row its own
      RowPipelineState instance.

    can_execute = False. This class makes no decisions, issues no labels,
    and carries no production authority.
    """

    # ── Single-row processing ─────────────────────────────────────────────────

    def process(
        self,
        adapter_result: WnbaPropsAdapterResult,
        row_id: str,
    ) -> WnbaPipelineGatewayResult:
        """
        Process one adapter result through the pipeline state layer.

        If the adapter already detected a failure (failure_classification is
        not None), that failure is propagated into the row's RowPipelineState
        and PipelineStateGuard.can_upgrade() is evaluated for the HOLD ceiling.

        If the adapter succeeded (COMPLETE or DEGRADED), the row state records
        the adapter layer as complete with no failure.
        """
        state = RowPipelineState(row_id=row_id)

        # Record adapter layer outcome regardless of success or failure
        state.record_layer_complete(
            PipelineLayer.ADAPTER,
            result={
                "adapter_status":      adapter_result.adapter_status,
                "role_payloads_count": len(adapter_result.role_payloads),
                "has_packet":          adapter_result.packet is not None,
                "has_game_script":     adapter_result.game_script_shadow is not None,
            },
        )

        # Propagate any adapter-detected failure into the row state.
        # Re-scope to gateway row_id: the adapter derives its own row_id from
        # event_id+run_id; the gateway caller may use a different row_id string.
        # Re-scoping preserves failure_kind/code/layer/message while binding
        # the failure to THIS gateway row's isolation boundary.
        if adapter_result.failure_classification is not None:
            af = adapter_result.failure_classification
            gateway_failure = _GUARD.scope_failure(
                row_id=row_id,
                failure_kind=af.failure_kind,
                failure_code=af.failure_code,
                failed_at_layer=af.failed_at_layer,
                message=af.message,
                preserved_upstream_result=af.preserved_upstream_result,
            )
            state.record_failure(gateway_failure)

        # Load-bearing call: evaluate the upgrade ceiling for this row
        final_ceiling = state.check_upgrade(UpgradeCeiling.HOLD)

        return WnbaPipelineGatewayResult(
            row_id=row_id,
            adapter_result=adapter_result,
            row_state=state,
            final_ceiling=final_ceiling,
            market_failure=None,
        )

    # ── Market/money-layer failure injection (test case c) ────────────────────

    def process_with_market_failure(
        self,
        adapter_result: WnbaPropsAdapterResult,
        row_id: str,
        market_failure: ScopedContractFailure,
    ) -> WnbaPipelineGatewayResult:
        """
        Process a row that succeeded at the adapter layer but failed
        at the market or money layer (technical failure, not merit).

        The adapter result — including packet, role_payloads, and any
        game_script_shadow — is preserved in the gateway result and
        accessible at result.adapter_result. It is NOT discarded or
        overwritten by the market-layer failure.

        This is the load-bearing proof of test case (c): "valid model result
        followed by a later market/money-layer failure → the completed model
        result is preserved and visible, while the downstream failure is
        scoped separately."

        Raises ValueError if market_failure.row_id != row_id (row isolation
        enforcement from RowPipelineState.record_failure).
        """
        state = RowPipelineState(row_id=row_id)

        # Record adapter layer as complete BEFORE recording the market failure.
        # This snapshot becomes preserved_result_for(PipelineLayer.ADAPTER)
        # and is returned in final_ceiling.preserved_upstream_result when the
        # ceiling is ADVISORY/HOLD.
        state.record_layer_complete(
            PipelineLayer.ADAPTER,
            result={
                "adapter_status":      adapter_result.adapter_status,
                "role_payloads_count": len(adapter_result.role_payloads),
                "has_game_script":     adapter_result.game_script_shadow is not None,
                "packet_run_id":       adapter_result.packet.run_id
                                       if adapter_result.packet is not None else None,
            },
        )

        # Record the market-layer failure. row_state.record_failure() enforces
        # that market_failure.row_id == row_id or raises ValueError.
        state.record_failure(market_failure)

        # Load-bearing guard call after the market failure is recorded
        final_ceiling = state.check_upgrade(UpgradeCeiling.HOLD)

        return WnbaPipelineGatewayResult(
            row_id=row_id,
            adapter_result=adapter_result,  # upstream preserved — NOT erased
            row_state=state,
            final_ceiling=final_ceiling,
            market_failure=market_failure,
        )

    # ── Legitimate rejection (test case d) ───────────────────────────────────

    def record_legitimate_rejection(
        self,
        adapter_result: WnbaPropsAdapterResult,
        row_id: str,
        rejection_code: str = "NO_PLAY",
        message: str = "Row did not qualify based on model evidence",
    ) -> WnbaPipelineGatewayResult:
        """
        Record a merit-based rejection (model ran, produced a legitimate
        no-play result based on real evidence). Uses FailureKind.LEGITIMATE_OUTCOME
        so PipelineStateGuard.can_upgrade() passes unconditionally — a valid
        NO_PLAY is not a technical failure and must not be miscategorised as one.
        """
        state = RowPipelineState(row_id=row_id)
        state.record_layer_complete(
            PipelineLayer.ADAPTER,
            result={"adapter_status": adapter_result.adapter_status},
        )

        # LEGITIMATE_OUTCOME: passes guard unconditionally regardless of ceiling
        failure = _GUARD.scope_failure(
            row_id=row_id,
            failure_kind=FailureKind.LEGITIMATE_OUTCOME,
            failure_code=rejection_code,
            failed_at_layer=PipelineLayer.GOVERNANCE,
            message=message,
        )
        state.record_failure(failure)

        # Guard passes for LEGITIMATE_OUTCOME — reason="LEGITIMATE_OUTCOME"
        final_ceiling = state.check_upgrade(UpgradeCeiling.HOLD)

        return WnbaPipelineGatewayResult(
            row_id=row_id,
            adapter_result=adapter_result,
            row_state=state,
            final_ceiling=final_ceiling,
            market_failure=None,
        )

    # ── Batch processing (test case e) ────────────────────────────────────────

    def process_batch(
        self,
        items: list,   # list of (WnbaPropsAdapterResult, row_id)
    ) -> list:
        """
        Process multiple rows independently through the gateway.
        Each row gets its own RowPipelineState — per-row isolation is physical:
        a failure recorded into row A's RowPipelineState has no reference to
        row B's RowPipelineState and cannot contaminate it.

        Returns list of WnbaPipelineGatewayResult, one per input item,
        in the same order as the input list.
        """
        results = []
        for position, (adapter_result, row_id) in enumerate(items):
            gw = self.process(adapter_result, row_id)
            # Stamp the batch position so tests can verify ordering
            results.append(WnbaPipelineGatewayResult(
                row_id=gw.row_id,
                adapter_result=gw.adapter_result,
                row_state=gw.row_state,
                final_ceiling=gw.final_ceiling,
                market_failure=gw.market_failure,
                batch_position=position,
            ))
        return results

    def make_market_failure(
        self,
        row_id: str,
        failure_code: str = "MARKET_DB_TIMEOUT",
        message: str = "Market gate DB connection timed out",
        preserved_upstream: Optional[dict] = None,
    ) -> ScopedContractFailure:
        """
        Factory helper: build a TECHNICAL market-layer ScopedContractFailure.
        Convenience for callers constructing test case (c) scenarios.
        """
        return _GUARD.scope_failure(
            row_id=row_id,
            failure_kind=FailureKind.TECHNICAL,
            failure_code=failure_code,
            failed_at_layer=PipelineLayer.MARKET,
            message=message,
            preserved_upstream_result=preserved_upstream,
        )
