"""
gate_engine/universal_agent/shadow/shadow_pipeline.py
WOW-PATCH-2026-08-10-UNIVERSAL-AGENT-CORE-V1-B3B

MLB Moneyline shadow pipeline — offline, default-off integration connecting
B3A adapter → DeterministicAdapterRunner → B2 orchestrator.

Design
------
The pipeline is DEFAULT OFF. run_shadow_pipeline() returns a DISABLED
ShadowPipelineResult immediately unless _force_enabled=True is passed (for
testing) or the module-level SHADOW_ENABLED flag is set to True by an
authorized caller.

The pipeline:
  1. Validates the input row via MlbMoneylineAdapter (fail-closed:
     AdapterInputError → ADAPTER_ERROR result; never propagated to orchestrator).
  2. Builds the EvidencePacket + six B1 role payloads via the adapter.
  3. Wraps the payloads in DeterministicAdapterRunner (no LLM/API calls).
  4. Runs the B2 orchestrator with the deterministic runner.
  5. Persists to uac_* tables when db_conn is provided (isolated tables only).
  6. Returns a frozen ShadowPipelineResult.

Invariants
----------
- SHADOW_ENABLED = False — default off; never changed here at module load.
- can_execute = False — no wagers, orders, capital, trading, deployment.
- No Anthropic, OpenAI, HTTP, or external API calls at any point.
- No app.py import, no Flask route wiring.
- No production scoring table access (only uac_* tables via audit_store.py).
- AdapterInputError always surfaces as ADAPTER_ERROR, never swallowed.
- Advisory outputs (EvidenceBundle, accepted_findings) are never used to
  modify deterministic decisions; they are read-only advisory evidence only.
- Missing/failed roles are always surfaced explicitly in
  OrchestratorResult.bundle.failed_role_ids and never treated as accepted.
- Contradictions (HIGH severity) always downgrade bundle_status to PARTIAL.
"""
from __future__ import annotations

from dataclasses import dataclass
from typing import Any, Optional

# ── Module-level flags ────────────────────────────────────────────────────────

SHADOW_ENABLED  = False   # default-off; never set True at module load
can_execute     = False
EXECUTION_RULE  = "DRY_RUN_ONLY_NO_LIVE_TRADING_NO_MARKET_ORDERS"
MODULE          = "mlb_moneyline_shadow_pipeline"
VERSION         = "v1.0"


# ── Pipeline status constants ─────────────────────────────────────────────────

class ShadowPipelineStatus:
    """
    Terminal status of one shadow pipeline run.

    COMPLETE      — All six roles ACCEPTED, no HIGH contradictions.
    PARTIAL       — ≥1 role ACCEPTED but not all, or HIGH contradiction found.
    FAILED        — Zero roles ACCEPTED.
    DISABLED      — Pipeline not enabled; no work performed.
    ADAPTER_ERROR — B3A adapter raised AdapterInputError; orchestrator not called.
    """
    COMPLETE      = "COMPLETE"
    PARTIAL       = "PARTIAL"
    FAILED        = "FAILED"
    DISABLED      = "DISABLED"
    ADAPTER_ERROR = "ADAPTER_ERROR"

    @classmethod
    def all_statuses(cls) -> frozenset:
        return frozenset({
            cls.COMPLETE, cls.PARTIAL, cls.FAILED,
            cls.DISABLED, cls.ADAPTER_ERROR,
        })


# ── Result dataclass ──────────────────────────────────────────────────────────

@dataclass(frozen=True)
class ShadowPipelineResult:
    """
    Immutable result of one MLB moneyline shadow pipeline run.

    Fields
    ------
    adapter_result
        MlbMoneylineAdapterResult from B3A. None when pipeline_status is
        DISABLED or ADAPTER_ERROR.
    orchestrator_result
        OrchestratorResult from B2. None when pipeline_status is DISABLED
        or ADAPTER_ERROR.
    shadow_enabled
        True when the pipeline was activated (SHADOW_ENABLED or _force_enabled).
    pipeline_status
        ShadowPipelineStatus.* constant.
    error_code
        Short machine-readable error code when pipeline_status==ADAPTER_ERROR.
        None otherwise.
    error_message
        Human-readable error when pipeline_status==ADAPTER_ERROR. None otherwise.
    run_id
        Caller-supplied run identifier.
    lane
        Always "MLB_MONEYLINE" for B3B.
    """
    adapter_result:      Optional[Any]   # MlbMoneylineAdapterResult | None
    orchestrator_result: Optional[Any]   # OrchestratorResult | None
    shadow_enabled:      bool
    pipeline_status:     str             # ShadowPipelineStatus.*
    error_code:          Optional[str]
    error_message:       Optional[str]
    run_id:              str
    lane:                str

    def is_complete(self) -> bool:
        return self.pipeline_status == ShadowPipelineStatus.COMPLETE

    def is_disabled(self) -> bool:
        return self.pipeline_status == ShadowPipelineStatus.DISABLED

    def is_adapter_error(self) -> bool:
        return self.pipeline_status == ShadowPipelineStatus.ADAPTER_ERROR

    def to_dict(self) -> dict:
        return {
            "pipeline_status":     self.pipeline_status,
            "shadow_enabled":      self.shadow_enabled,
            "run_id":              self.run_id,
            "lane":                self.lane,
            "error_code":          self.error_code,
            "error_message":       self.error_message,
            "adapter_status": (
                self.adapter_result.adapter_status
                if self.adapter_result is not None else None
            ),
            "bundle_status": (
                self.orchestrator_result.bundle.bundle_status
                if self.orchestrator_result is not None else None
            ),
            "accepted_count": (
                self.orchestrator_result.accepted_count()
                if self.orchestrator_result is not None else None
            ),
            "failed_count": (
                self.orchestrator_result.failed_count()
                if self.orchestrator_result is not None else None
            ),
            "contradiction_count": (
                len(self.orchestrator_result.contradictions)
                if self.orchestrator_result is not None else None
            ),
            "persisted": (
                self.orchestrator_result.persisted
                if self.orchestrator_result is not None else None
            ),
        }


# ── ShadowPipeline class ──────────────────────────────────────────────────────

class ShadowPipeline:
    """
    Stateless offline shadow pipeline for one MLB moneyline scoring row.

    Chains: B3A adapter → DeterministicAdapterRunner → B2 orchestrator.

    No live LLM, API, or network calls. No production route wiring.
    Default off — must be explicitly enabled via _force_enabled or by setting
    the module-level SHADOW_ENABLED = True before calling.

    can_execute = False — no wagers, capital, or execution authority.
    """

    can_execute    = False
    EXECUTION_RULE = "DRY_RUN_ONLY_NO_LIVE_TRADING_NO_MARKET_ORDERS"

    def run(
        self,
        row: Any,
        run_id: str,
        *,
        db_conn: Optional[Any] = None,
        snapshot_id: Optional[str] = None,
        _force_enabled: bool = False,
        _registry: Optional[Any] = None,
    ) -> ShadowPipelineResult:
        """
        Run the offline shadow pipeline for one MLB moneyline row.

        Parameters
        ----------
        row
            WOW/LLP MLB moneyline evidence row dict (read-only).
        run_id
            Caller-supplied run identifier.
        db_conn
            Optional psycopg2 connection for uac_* table persistence.
            If None, the orchestrator runs without persisting.
        snapshot_id
            Optional snapshot_id override (for deterministic test assertions).
        _force_enabled
            Testing escape-hatch: bypass the SHADOW_ENABLED flag.
            Never use in production callers.
        _registry
            Optional AgentRegistry override (for test injection).
            If None, a fresh B1 registry is built.

        Returns
        -------
        ShadowPipelineResult (frozen dataclass).
        """
        return run_shadow_pipeline(
            row,
            run_id,
            db_conn=db_conn,
            snapshot_id=snapshot_id,
            _force_enabled=_force_enabled,
            _registry=_registry,
        )


# ── Public function ───────────────────────────────────────────────────────────

def run_shadow_pipeline(
    row: Any,
    run_id: str,
    *,
    db_conn: Optional[Any] = None,
    snapshot_id: Optional[str] = None,
    _force_enabled: bool = False,
    _registry: Optional[Any] = None,
) -> ShadowPipelineResult:
    """
    Run the offline MLB moneyline shadow pipeline.

    Default OFF: returns ShadowPipelineStatus.DISABLED immediately unless
    _force_enabled=True or the module-level SHADOW_ENABLED is True.

    Steps (when enabled)
    --------------------
    1. B3A MlbMoneylineAdapter.adapt(row, run_id, snapshot_id)
       → EvidencePacket + six B1 role payloads.
       AdapterInputError → ADAPTER_ERROR result; orchestrator not called.

    2. DeterministicAdapterRunner wraps adapter payloads; no LLM calls.

    3. B2 run_orchestrator(packet, registry, role_runners, db_conn)
       → OrchestratorResult (with bundle, contradictions, persistence).

    4. Map OrchestratorResult.bundle.bundle_status → ShadowPipelineStatus.

    5. Return frozen ShadowPipelineResult.

    Parameters
    ----------
    row
        WOW/LLP MLB moneyline evidence row dict (read-only).
    run_id
        Caller-supplied run identifier.
    db_conn
        Optional psycopg2 connection for uac_* table persistence.
    snapshot_id
        Optional snapshot_id override.
    _force_enabled
        Bypass SHADOW_ENABLED flag. Testing only; never use in production.
    _registry
        Optional AgentRegistry override. If None, build_b1_registry() is used.

    Returns
    -------
    ShadowPipelineResult (frozen dataclass).

    Raises
    ------
    Nothing — all errors are captured as ADAPTER_ERROR or propagated inside
    OrchestratorResult.  The orchestrator itself never raises on role failure.
    """
    # ── Guard: default-off ────────────────────────────────────────────────────
    enabled = SHADOW_ENABLED or _force_enabled
    if not enabled:
        return ShadowPipelineResult(
            adapter_result=None,
            orchestrator_result=None,
            shadow_enabled=False,
            pipeline_status=ShadowPipelineStatus.DISABLED,
            error_code=None,
            error_message=None,
            run_id=run_id,
            lane="MLB_MONEYLINE",
        )

    # ── Lazy imports (never at module top — guard against circular load) ──────
    from gate_engine.universal_agent.bundle_assembler import BundleStatus
    from gate_engine.universal_agent.lanes.mlb_moneyline.adapter import MlbMoneylineAdapter
    from gate_engine.universal_agent.lanes.mlb_moneyline.validation import AdapterInputError
    from gate_engine.universal_agent.orchestrator import run_orchestrator
    from gate_engine.universal_agent.roles.registry_b1 import build_b1_registry
    from gate_engine.universal_agent.shadow.deterministic_runner import DeterministicAdapterRunner

    # ── Step 1: B3A adapter ───────────────────────────────────────────────────
    adapter = MlbMoneylineAdapter()
    try:
        adapter_result = adapter.adapt(
            row=row,
            run_id=run_id,
            snapshot_id=snapshot_id,
        )
    except AdapterInputError as exc:
        return ShadowPipelineResult(
            adapter_result=None,
            orchestrator_result=None,
            shadow_enabled=True,
            pipeline_status=ShadowPipelineStatus.ADAPTER_ERROR,
            error_code=exc.code,
            error_message=exc.message,
            run_id=run_id,
            lane="MLB_MONEYLINE",
        )

    # ── Step 2: Build deterministic runner from adapter payloads ──────────────
    det_runner = DeterministicAdapterRunner(adapter_result.role_payloads)

    # ── Step 3: Build or use injected registry ────────────────────────────────
    registry = _registry if _registry is not None else build_b1_registry()
    role_runners = det_runner.build_role_runners(registry)

    # ── Step 4: Run B2 orchestrator ───────────────────────────────────────────
    orchestrator_result = run_orchestrator(
        packet=adapter_result.packet,
        registry=registry,
        role_runners=role_runners,
        db_conn=db_conn,
    )

    # ── Step 5: Map bundle_status → ShadowPipelineStatus ─────────────────────
    _bundle_to_pipeline = {
        BundleStatus.COMPLETE: ShadowPipelineStatus.COMPLETE,
        BundleStatus.PARTIAL:  ShadowPipelineStatus.PARTIAL,
        BundleStatus.FAILED:   ShadowPipelineStatus.FAILED,
    }
    bundle_status   = orchestrator_result.bundle.bundle_status
    pipeline_status = _bundle_to_pipeline.get(bundle_status, ShadowPipelineStatus.FAILED)

    return ShadowPipelineResult(
        adapter_result=adapter_result,
        orchestrator_result=orchestrator_result,
        shadow_enabled=True,
        pipeline_status=pipeline_status,
        error_code=None,
        error_message=None,
        run_id=run_id,
        lane="MLB_MONEYLINE",
    )
