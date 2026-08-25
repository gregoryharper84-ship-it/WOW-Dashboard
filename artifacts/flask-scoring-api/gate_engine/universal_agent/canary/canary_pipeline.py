"""
gate_engine/universal_agent/canary/canary_pipeline.py
WOW-PATCH-2026-08-10-UNIVERSAL-AGENT-CORE-V1 / Phase B3C

CanaryPipeline — the ONLY authorized B3C execution path.

AUTHORIZED PATH (one way in, no shortcuts):
  1. Validate row via REAL B3A MlbMoneylineAdapter
  2. Build BudgetState + ClaudeRoleRunner (shared across all 6 roles)
  3. Build B1 registry and role_runners dict (one runner entry per agent)
  4. Call REAL B2 run_orchestrator() — which internally calls:
       - REAL B0 UniversalCapabilityBoundary (pre_hook + post_hook)
       - REAL B1 role-specific validators (validate_*_output)
       - REAL B2 ContradictionDetector + BundleAssembler
  5. Merge call_log with orchestrator role_results for B3C persistence
  6. Persist to b3c_canary_runs table (isolated from uac_* tables)

When UAC_MLB_ML_CLAUDE_SHADOW_ENABLED=False (default): returns DISABLED immediately.
When AdapterInputError is raised: returns ADAPTER_ERROR immediately.

No app.py import. No Flask routes. No Weather/Kalshi imports.
No CAN_EXECUTE, production routing, or weather-lane cross-references.
"""
from __future__ import annotations

import hashlib
import json
from dataclasses import dataclass, field
from datetime import datetime, timezone
from typing import Any, List, Optional

# ── B3A adapter (real, imported — not duplicated) ─────────────────────────────
from gate_engine.universal_agent.lanes.mlb_moneyline.adapter import (
    MlbMoneylineAdapter,
)
from gate_engine.universal_agent.lanes.mlb_moneyline.validation import (
    AdapterInputError,
)

# ── B2 orchestrator (real, imported — not duplicated) ─────────────────────────
from gate_engine.universal_agent.orchestrator import (
    OrchestratorResult,
    run_orchestrator,
)

# ── B1 registry (real, imported — not duplicated) ─────────────────────────────
from gate_engine.universal_agent.roles.registry_b1 import build_b1_registry

# ── B3C canary components ─────────────────────────────────────────────────────
from gate_engine.universal_agent.canary.canary_config import (
    UAC_MLB_ML_CLAUDE_SHADOW_ENABLED,
)
from gate_engine.universal_agent.canary.claude_role_runner import (
    BudgetState,
    CanaryAbortState,
    CanaryCallRecord,
    ClaudeRoleRunner,
)
from gate_engine.universal_agent.canary.canary_store import (
    ensure_canary_tables,
    persist_canary_result,
)

can_execute    = False
advisory_only  = True


# ── Pipeline status ───────────────────────────────────────────────────────────

class CanaryPipelineStatus:
    """Status codes for CanaryPipelineResult."""
    COMPLETE      = "COMPLETE"        # all 6 roles ACCEPTED, orchestrator complete
    PARTIAL       = "PARTIAL"         # some roles failed or contradictions detected
    FAILED        = "FAILED"          # fatal error before/during orchestrator
    DISABLED      = "DISABLED"        # UAC_MLB_ML_CLAUDE_SHADOW_ENABLED=False
    ADAPTER_ERROR = "ADAPTER_ERROR"   # B3A adapter raised AdapterInputError


# ── Pipeline result ───────────────────────────────────────────────────────────

@dataclass(frozen=True)
class CanaryPipelineResult:
    """
    Immutable result of one B3C canary pipeline run.

    Fields
    ------
    canary_run_id       Caller-supplied run identifier.
    pipeline_status     CanaryPipelineStatus constant.
    calls_attempted     How many Claude API calls were attempted.
    calls_successful    How many Claude API calls fully succeeded.
    total_spend_usd     Cumulative cost across all calls in this run.
    call_log            Tuple of CanaryCallRecord (one per attempt).
    adapter_result      MlbMoneylineAdapterResult or None.
    orchestrator_result OrchestratorResult or None (None if pipeline aborted early).
    persisted           True when b3c_canary_runs rows were written.
    error_message       Set on FAILED/ADAPTER_ERROR.
    disabled_reason     Set on DISABLED.
    """
    canary_run_id:       str
    pipeline_status:     str
    calls_attempted:     int
    calls_successful:    int
    total_spend_usd:     float
    call_log:            tuple
    adapter_result:      Any           # MlbMoneylineAdapterResult | None
    orchestrator_result: Any           # OrchestratorResult | None
    persisted:           bool
    error_message:       Optional[str] = None
    disabled_reason:     Optional[str] = None

    def to_dict(self) -> dict:
        return {
            "canary_run_id":    self.canary_run_id,
            "pipeline_status":  self.pipeline_status,
            "calls_attempted":  self.calls_attempted,
            "calls_successful": self.calls_successful,
            "total_spend_usd":  self.total_spend_usd,
            "call_log_count":   len(self.call_log),
            "persisted":        self.persisted,
            "error_message":    self.error_message,
            "disabled_reason":  self.disabled_reason,
        }


# ── Internal helpers ──────────────────────────────────────────────────────────

def _sha256_json(obj: Any) -> Optional[str]:
    """SHA-256 hex of deterministic JSON; returns None on failure."""
    try:
        serialized = json.dumps(obj, sort_keys=True, default=str)
        return hashlib.sha256(serialized.encode()).hexdigest()
    except Exception:  # noqa: BLE001
        return None


def _pipeline_status_from_orchestrator(orch: OrchestratorResult) -> str:
    """Map orchestrator bundle status to CanaryPipelineStatus."""
    from gate_engine.universal_agent.bundle_assembler import BundleStatus
    # EvidenceBundle uses .bundle_status, not .status (field name from dataclass)
    bs = orch.bundle.bundle_status
    if bs == BundleStatus.COMPLETE:
        return CanaryPipelineStatus.COMPLETE
    if bs == BundleStatus.PARTIAL:
        return CanaryPipelineStatus.PARTIAL
    return CanaryPipelineStatus.FAILED


def _persist_all_results(
    conn: Any,
    canary_run_id: str,
    snapshot_id: str,
    call_log: List[CanaryCallRecord],
    orch_result: Optional[OrchestratorResult],
    budget: BudgetState,
) -> None:
    """
    Persist all B3C call records to b3c_canary_runs.
    Merges call_log (model metadata) with orchestrator role_results (schema status).
    """
    ensure_canary_tables(conn)

    cumulative = 0.0
    for rec in call_log:
        if rec.calculated_cost_usd is not None:
            cumulative += rec.calculated_cost_usd

        # Schema status from orchestrator result (if available)
        schema_status: Optional[str] = None
        canonical_hash: Optional[str] = None
        if orch_result is not None:
            role_result = orch_result.result_for_role(rec.role_id)
            if role_result is not None:
                schema_status = role_result.status
                if role_result.advisory_findings:
                    canonical_hash = _sha256_json(role_result.advisory_findings)

        persist_canary_result(
            conn,
            canary_run_id=canary_run_id,
            snapshot_id=snapshot_id,
            role_id=rec.role_id,
            requested_model=rec.requested_model,
            response_model=rec.response_model,
            request_timestamp=rec.request_timestamp,
            completion_timestamp=rec.completion_timestamp,
            latency_ms=rec.latency_ms,
            input_tokens=rec.input_tokens,
            output_tokens=rec.output_tokens,
            cache_read_input_tokens=rec.cache_read_input_tokens,
            cache_creation_input_tokens=rec.cache_creation_input_tokens,
            calculated_cost_usd=rec.calculated_cost_usd,
            cumulative_run_cost_usd=cumulative,
            runner_status=rec.status,
            schema_status=schema_status,
            violation_codes=rec.violation_codes,
            error_classification=rec.error_classification,
            raw_output_hash=rec.raw_output_hash,
            canonical_output_hash=canonical_hash,
        )


# ── Public entry point ────────────────────────────────────────────────────────

def run_canary_pipeline(
    row: Any,
    canary_run_id: str,
    *,
    db_conn: Optional[Any] = None,
    _client: Optional[Any] = None,
    _force_enabled: bool = False,
) -> CanaryPipelineResult:
    """
    Execute the B3C bounded real-Claude canary pipeline.

    Parameters
    ----------
    row             WOW/LLP scoring row dict (MLB moneyline).
    canary_run_id   Caller-supplied run identifier (echoed in all records).
    db_conn         Optional psycopg2 connection for b3c persistence.
    _client         Anthropic client (real or mock). If None, a real client
                    would be needed — but this defaults to None so tests
                    can inject a mock. LIVE CALLS require _client + _force_enabled.
    _force_enabled  Testing escape-hatch to bypass the UAC_MLB_ML_CLAUDE_SHADOW_ENABLED
                    flag. Never use in production.

    Returns
    -------
    CanaryPipelineResult (frozen dataclass). Always returns; never raises to caller.

    Notes
    -----
    - When UAC_MLB_ML_CLAUDE_SHADOW_ENABLED=False and _force_enabled=False:
      returns DISABLED immediately, no API calls made.
    - Actual attempted/completed counts are always the real counts — never fabricated.
    """
    enabled = UAC_MLB_ML_CLAUDE_SHADOW_ENABLED or _force_enabled

    # ── Guard: disabled ───────────────────────────────────────────────────────
    if not enabled:
        return CanaryPipelineResult(
            canary_run_id=canary_run_id,
            pipeline_status=CanaryPipelineStatus.DISABLED,
            calls_attempted=0,
            calls_successful=0,
            total_spend_usd=0.0,
            call_log=(),
            adapter_result=None,
            orchestrator_result=None,
            persisted=False,
            disabled_reason="UAC_MLB_ML_CLAUDE_SHADOW_ENABLED=False",
        )

    # ── Step 1: B3A adapter (real, not duplicated) ────────────────────────────
    adapter = MlbMoneylineAdapter()
    try:
        adapter_result = adapter.adapt(row=row, run_id=canary_run_id)
    except AdapterInputError as exc:
        return CanaryPipelineResult(
            canary_run_id=canary_run_id,
            pipeline_status=CanaryPipelineStatus.ADAPTER_ERROR,
            calls_attempted=0,
            calls_successful=0,
            total_spend_usd=0.0,
            call_log=(),
            adapter_result=None,
            orchestrator_result=None,
            persisted=False,
            error_message=f"AdapterInputError: {exc}",
        )
    except Exception as exc:  # noqa: BLE001
        return CanaryPipelineResult(
            canary_run_id=canary_run_id,
            pipeline_status=CanaryPipelineStatus.FAILED,
            calls_attempted=0,
            calls_successful=0,
            total_spend_usd=0.0,
            call_log=(),
            adapter_result=None,
            orchestrator_result=None,
            persisted=False,
            error_message=f"Adapter unexpected error: {exc}",
        )

    packet = adapter_result.packet

    # ── Step 2: Budget state + abort state + ClaudeRoleRunner (shared) ───────
    # B3C-R1 FIX 1: CanaryAbortState is created ONCE per run and shared across
    # all 6 role dispatches. Any structural failure sets is_aborted=True;
    # subsequent roles check this flag first (step 0) and make ZERO API calls.
    budget      = BudgetState()
    abort_state = CanaryAbortState()
    runner      = ClaudeRoleRunner(client=_client, budget=budget, abort_state=abort_state)

    # ── Step 3: B1 registry + role_runners dict (one runner, 6 entries) ───────
    registry = build_b1_registry()
    role_runners = {
        entry.agent_id: runner
        for entry in registry.all_agents()
    }

    # ── Step 4: Real B2 orchestrator ──────────────────────────────────────────
    # run_orchestrator internally calls:
    #   - REAL B0 UniversalCapabilityBoundary (pre_hook + post_hook)
    #   - REAL B1 role-specific validators (_validate_dsi, _validate_ns, etc.)
    #   - REAL ContradictionDetector + BundleAssembler
    # Tests verify this via unittest.mock.patch + call_count assertions (Step 14D pattern).
    orch_result: Optional[OrchestratorResult] = None
    pipeline_status = CanaryPipelineStatus.FAILED

    try:
        orch_result = run_orchestrator(
            packet,
            registry,
            role_runners,
            db_conn=None,   # UAC tables not written in canary path; B3C has its own table
        )
        pipeline_status = _pipeline_status_from_orchestrator(orch_result)
    except Exception as exc:  # noqa: BLE001
        pipeline_status = CanaryPipelineStatus.FAILED
        # orch_result remains None; call_log still has whatever was recorded

    # ── Step 5: Persist to b3c_canary_runs (isolated from uac_* tables) ───────
    persisted = False
    if db_conn is not None and runner.call_log:
        try:
            _persist_all_results(
                db_conn,
                canary_run_id=canary_run_id,
                snapshot_id=packet.snapshot_id,
                call_log=runner.call_log,
                orch_result=orch_result,
                budget=budget,
            )
            persisted = True
        except Exception:  # noqa: BLE001
            pass  # best-effort persistence; never blocks result

    return CanaryPipelineResult(
        canary_run_id=canary_run_id,
        pipeline_status=pipeline_status,
        calls_attempted=budget.calls_attempted,
        calls_successful=budget.calls_successful,
        total_spend_usd=budget.cumulative_spend_usd,
        call_log=tuple(runner.call_log),
        adapter_result=adapter_result,
        orchestrator_result=orch_result,
        persisted=persisted,
    )


# ── Class interface ───────────────────────────────────────────────────────────

class CanaryPipeline:
    """
    Stateless class wrapper around run_canary_pipeline().
    Useful when callers want an injectable object rather than a bare function.

    can_execute = False — advisory only.
    """
    can_execute    = False
    advisory_only  = True

    def run(
        self,
        row: Any,
        canary_run_id: str,
        *,
        db_conn: Optional[Any] = None,
        _client: Optional[Any] = None,
        _force_enabled: bool = False,
    ) -> CanaryPipelineResult:
        return run_canary_pipeline(
            row,
            canary_run_id,
            db_conn=db_conn,
            _client=_client,
            _force_enabled=_force_enabled,
        )
