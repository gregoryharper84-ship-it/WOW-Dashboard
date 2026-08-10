"""
gate_engine/universal_agent/orchestrator.py
WOW-PATCH-2026-08-09-UNIVERSAL-AGENT-CORE-V1 / Phase B2

Universal Agent Core Orchestrator — shared deterministic orchestration layer
connecting B0 infrastructure and B1 six advisory role contracts.

Responsibilities
----------------
1. Accepts ONE immutable EvidencePacket (frozen dataclass).
2. Fans the SAME packet object (same Python identity) to all registered roles
   via dependency-injected RoleRunner callables.
3. Passes every role result through:
     a. B0 UniversalCapabilityBoundary post-hook (recursive governance-key scan)
     b. B1 closed role-specific validator (validate_<role>_output)
4. Detects cross-role contradictions (contradiction_detector.py).
5. Assembles a canonical EvidenceBundle (bundle_assembler.py).
6. Persists all results to isolated uac_* Postgres tables (audit_store.py).
7. Supports deterministic resumability: work units already marked ACCEPTED in
   uac_run_resumability are skipped (SKIPPED_RESUMED) on re-run.

Invariants
----------
- The same EvidencePacket object is passed to EVERY runner (identity guaranteed).
- RUNNER_FAILED / INVALID / GOVERNANCE_REJECTED results are NEVER persisted as
  ACCEPTED and NEVER appear in accepted_role_ids.
- NO_RUNNER is fail-closed: absent runner → the role is failed, never silently
  treated as success.
- Persistence is exclusively to uac_* tables (no other tables touched).
- No app.py import, no Flask route wiring, no live Anthropic/OpenAI/API calls,
  no lane adapter, no sport-specific probability logic, no Weather code,
  no user-facing label changes, no trading/capital/deployment authority.
"""
from __future__ import annotations

import time
from dataclasses import dataclass
from datetime import datetime, timezone
from typing import Any, Optional

from gate_engine.universal_agent.agent_registry import AgentRegistry
from gate_engine.universal_agent.audit_store import (
    UsageStatus,
    ensure_tables,
    is_work_completed,
    mark_work_completed,
    record_agent_result,
    record_budget_event,
    record_evidence_packet,
)
from gate_engine.universal_agent.bundle_assembler import EvidenceBundle, assemble_bundle
from gate_engine.universal_agent.capability_boundary import UniversalCapabilityBoundary
from gate_engine.universal_agent.contradiction_detector import (
    ContradictionRecord,
    detect_contradictions,
)
from gate_engine.universal_agent.evidence_packet import EvidencePacket
from gate_engine.universal_agent.output_contract import OUTPUT_VALID
from gate_engine.universal_agent.role_result import RoleResult
from gate_engine.universal_agent.role_runner import RoleRunnerStatus

# ── B1 role validators ────────────────────────────────────────────────────────
# Imported at module level so lint tools and tests can verify the wiring.
# _ROLE_VALIDATORS maps role_id → validator callable.

from gate_engine.universal_agent.roles.data_slate_integrity import (
    ROLE_ID as _DSI_ID,
    validate_data_slate_integrity_output as _validate_dsi,
)
from gate_engine.universal_agent.roles.news_status import (
    ROLE_ID as _NS_ID,
    validate_news_status_output as _validate_ns,
)
from gate_engine.universal_agent.roles.market_exact_line import (
    ROLE_ID as _MEL_ID,
    validate_market_exact_line_output as _validate_mel,
)
from gate_engine.universal_agent.roles.sport_specialist import (
    ROLE_ID as _SS_ID,
    validate_sport_specialist_output as _validate_ss,
)
from gate_engine.universal_agent.roles.failure_contradiction import (
    ROLE_ID as _FC_ID,
    validate_failure_contradiction_output as _validate_fc,
)
from gate_engine.universal_agent.roles.final_refresh import (
    ROLE_ID as _FR_ID,
    validate_final_refresh_output as _validate_fr,
)

# Canonical pipeline order for all six B1 advisory roles.
B1_ROLE_IDS: tuple = (
    _DSI_ID,  # DATA_SLATE_INTEGRITY
    _NS_ID,   # NEWS_STATUS
    _MEL_ID,  # MARKET_EXACT_LINE
    _SS_ID,   # SPORT_SPECIALIST
    _FC_ID,   # FAILURE_CONTRADICTION
    _FR_ID,   # FINAL_REFRESH
)

_ROLE_VALIDATORS: dict = {
    _DSI_ID: _validate_dsi,
    _NS_ID:  _validate_ns,
    _MEL_ID: _validate_mel,
    _SS_ID:  _validate_ss,
    _FC_ID:  _validate_fc,
    _FR_ID:  _validate_fr,
}


# ── Orchestrator result ───────────────────────────────────────────────────────

@dataclass(frozen=True)
class OrchestratorResult:
    """
    Immutable result of one complete orchestrator run.

    Fields
    ------
    role_results
        Tuple of RoleResult, one per registered agent, in registration order.
    bundle
        The canonical EvidenceBundle assembled from all role results.
    contradictions
        Tuple of ContradictionRecord detected across ACCEPTED roles.
    persisted
        True when db_conn was provided and UAC tables were written.
    run_id
        Echoed from the EvidencePacket.
    snapshot_id
        Echoed from the EvidencePacket.
    completed_at
        ISO-8601 UTC timestamp of orchestrator completion.
    """
    role_results:   tuple
    bundle:         EvidenceBundle
    contradictions: tuple
    persisted:      bool
    run_id:         str
    snapshot_id:    str
    completed_at:   str

    def accepted_count(self) -> int:
        return sum(1 for r in self.role_results if r.accepted)

    def failed_count(self) -> int:
        return sum(1 for r in self.role_results if not r.effectively_accepted)

    def result_for_role(self, role_id: str) -> Optional[RoleResult]:
        for r in self.role_results:
            if r.role_id == role_id:
                return r
        return None

    def result_for_agent(self, agent_id: str) -> Optional[RoleResult]:
        for r in self.role_results:
            if r.agent_id == agent_id:
                return r
        return None


# ── Internal helpers ──────────────────────────────────────────────────────────

def _make_work_unit_id(snapshot_id: str, agent_id: str) -> str:
    """Stable resumability key: '{snapshot_id}:{agent_id}'."""
    return f"{snapshot_id}:{agent_id}"


def _run_one_role(
    *,
    entry: Any,
    packet: EvidencePacket,
    runner: Any,
    boundary: UniversalCapabilityBoundary,
    role_validator: Optional[Any],
) -> RoleResult:
    """
    Execute one role and produce an immutable RoleResult.

    Pipeline
    --------
    1. B0 pre-hook (capability boundary: agent registered + tool allowed +
       governance key scan on empty tool_input dict).
    2. Call runner(entry, packet).
    3. Verify runner returned a dict.
    4. B0 post-hook (governance key scan on raw_output).
    5. B1 role-specific validator.
    6. Return RoleResult with the appropriate RoleRunnerStatus.

    Fail-closed at every step.
    """
    agent_id = entry.agent_id
    role_id  = entry.role
    # Use the first declared capability as the tool name for hook calls.
    caps      = entry.allowed_capabilities
    tool_name = caps[0] if caps else agent_id

    # ── Step 1: B0 pre-hook ───────────────────────────────────────────────────
    pre = boundary.pre_tool_use_hook(agent_id, tool_name, {})
    if pre.blocked:
        return RoleResult(
            agent_id=agent_id,
            role_id=role_id,
            status=RoleRunnerStatus.BOUNDARY_BLOCKED,
            raw_output=None,
            advisory_findings=None,
            violation_code="BOUNDARY_BLOCKED",
            violation_message=pre.message,
            latency_ms=None,
            error_message=pre.message,
        )

    # ── Step 2: Call runner ───────────────────────────────────────────────────
    t0 = time.monotonic()
    try:
        raw_output = runner(entry, packet)
    except Exception as exc:  # noqa: BLE001
        latency_ms = int((time.monotonic() - t0) * 1000)
        return RoleResult(
            agent_id=agent_id,
            role_id=role_id,
            status=RoleRunnerStatus.RUNNER_FAILED,
            raw_output=None,
            advisory_findings=None,
            violation_code="RUNNER_EXCEPTION",
            violation_message=str(exc),
            latency_ms=latency_ms,
            error_message=str(exc),
        )
    latency_ms = int((time.monotonic() - t0) * 1000)

    # ── Step 3: Runner must return a dict ────────────────────────────────────
    if not isinstance(raw_output, dict):
        return RoleResult(
            agent_id=agent_id,
            role_id=role_id,
            status=RoleRunnerStatus.RUNNER_FAILED,
            raw_output=None,
            advisory_findings=None,
            violation_code="RUNNER_NOT_DICT",
            violation_message=(
                f"Runner returned {type(raw_output).__name__}, expected dict"
            ),
            latency_ms=latency_ms,
            error_message=f"Runner returned {type(raw_output).__name__}",
        )

    # ── Step 4: B0 post-hook (governance key scan on output) ─────────────────
    post = boundary.post_tool_use_hook(agent_id, tool_name, raw_output)
    if not post.passed:
        viol_code = (
            post.violation.code
            if post.violation is not None
            else "GOVERNANCE_REJECTED"
        )
        return RoleResult(
            agent_id=agent_id,
            role_id=role_id,
            status=RoleRunnerStatus.GOVERNANCE_REJECTED,
            raw_output=raw_output,
            advisory_findings=None,
            violation_code=viol_code,
            violation_message=post.message,
            latency_ms=latency_ms,
            error_message=None,
        )

    # ── Step 5: B1 role-specific validator ────────────────────────────────────
    if role_validator is None:
        return RoleResult(
            agent_id=agent_id,
            role_id=role_id,
            status=RoleRunnerStatus.INVALID,
            raw_output=raw_output,
            advisory_findings=None,
            violation_code="NO_ROLE_VALIDATOR",
            violation_message=(
                f"No B1 validator registered for role_id={role_id!r}"
            ),
            latency_ms=latency_ms,
            error_message=None,
        )

    validation = role_validator(raw_output)
    if validation is not OUTPUT_VALID:
        return RoleResult(
            agent_id=agent_id,
            role_id=role_id,
            status=RoleRunnerStatus.INVALID,
            raw_output=raw_output,
            advisory_findings=None,
            violation_code=getattr(validation, "code", "INVALID"),
            violation_message=getattr(validation, "message", str(validation)),
            latency_ms=latency_ms,
            error_message=None,
        )

    # ── ACCEPTED ──────────────────────────────────────────────────────────────
    return RoleResult(
        agent_id=agent_id,
        role_id=role_id,
        status=RoleRunnerStatus.ACCEPTED,
        raw_output=raw_output,
        advisory_findings=raw_output.get("advisory_findings"),
        violation_code=None,
        violation_message=None,
        latency_ms=latency_ms,
        error_message=None,
    )


def _persist_one_result(
    db_conn: Any,
    *,
    run_id: str,
    snapshot_id: str,
    result: RoleResult,
) -> None:
    """
    Persist one RoleResult to uac_agent_results and uac_budget_events.
    SKIPPED_RESUMED results are not re-persisted (already in DB).
    """
    if result.status == RoleRunnerStatus.SKIPPED_RESUMED:
        return  # already persisted in a prior run

    # Map to UsageStatus for the budget accounting table
    usage_status = (
        UsageStatus.AVAILABLE
        if result.status == RoleRunnerStatus.ACCEPTED
        else UsageStatus.ERROR
    )

    record_agent_result(
        db_conn,
        run_id=run_id,
        snapshot_id=snapshot_id,
        agent_id=result.agent_id,
        status=result.status,
        output=result.raw_output,
        violation_code=result.violation_code,
        violation_message=result.violation_message,
        model=None,
        input_tokens=None,
        output_tokens=None,
        estimated_cost_usd=None,
        latency_ms=result.latency_ms,
    )
    record_budget_event(
        db_conn,
        run_id=run_id,
        agent_id=result.agent_id,
        event_type="ROLE_EXECUTION",
        usage_status=usage_status,
        input_tokens=None,
        output_tokens=None,
        estimated_cost_usd=None,
        model=None,
        notes=(result.violation_message or result.status)[:500]
              if result.violation_message or result.status else None,
    )


# ── Public entry point ────────────────────────────────────────────────────────

def run_orchestrator(
    packet: EvidencePacket,
    registry: AgentRegistry,
    role_runners: dict,
    db_conn: Optional[Any] = None,
) -> OrchestratorResult:
    """
    Run the B2 orchestrator for one evidence packet.

    Parameters
    ----------
    packet
        Immutable EvidencePacket (frozen dataclass). The SAME Python object is
        passed to every runner — identity is guaranteed, not just equality.
    registry
        AgentRegistry containing the advisory role entries to run.
    role_runners
        dict[agent_id → callable(entry, packet) → dict].
        Any agent_id absent from this dict → NO_RUNNER (fail-closed).
    db_conn
        Optional psycopg2 connection. If None, results are computed but not
        persisted (persisted=False in OrchestratorResult).

    Returns
    -------
    OrchestratorResult (frozen dataclass).

    Raises
    ------
    TypeError
        If packet is not an EvidencePacket instance.
    """
    if not isinstance(packet, EvidencePacket):
        raise TypeError(
            f"packet must be an EvidencePacket, got {type(packet).__name__}"
        )

    # ── DB setup ──────────────────────────────────────────────────────────────
    if db_conn is not None:
        ensure_tables(db_conn)
        record_evidence_packet(
            db_conn,
            snapshot_id=packet.snapshot_id,
            run_id=packet.run_id,
            canonical_event_id=packet.canonical_event_id,
            lane=packet.lane,
            packet_dict=packet.to_dict(),
        )

    # ── Capability boundary from registry ─────────────────────────────────────
    boundary = UniversalCapabilityBoundary.from_registry_entries(
        registry.all_agents()
    )

    # ── Fan out to all registered roles ──────────────────────────────────────
    role_results: list[RoleResult] = []

    for entry in registry.all_agents():
        agent_id = entry.agent_id
        role_id  = entry.role
        work_unit_id = _make_work_unit_id(packet.snapshot_id, agent_id)

        # Resumability: skip already-completed (ACCEPTED) work units
        if db_conn is not None and is_work_completed(
            db_conn, run_id=packet.run_id, work_unit_id=work_unit_id
        ):
            role_results.append(RoleResult(
                agent_id=agent_id,
                role_id=role_id,
                status=RoleRunnerStatus.SKIPPED_RESUMED,
                raw_output=None,
                advisory_findings=None,
                violation_code=None,
                violation_message=None,
                latency_ms=None,
                error_message=None,
            ))
            continue

        # Check runner availability (fail-closed)
        runner = role_runners.get(agent_id)
        if runner is None:
            result = RoleResult(
                agent_id=agent_id,
                role_id=role_id,
                status=RoleRunnerStatus.NO_RUNNER,
                raw_output=None,
                advisory_findings=None,
                violation_code="NO_RUNNER",
                violation_message=f"No runner registered for agent_id={agent_id!r}",
                latency_ms=None,
                error_message=None,
            )
        else:
            role_validator = _ROLE_VALIDATORS.get(role_id)
            result = _run_one_role(
                entry=entry,
                packet=packet,
                runner=runner,
                boundary=boundary,
                role_validator=role_validator,
            )

        role_results.append(result)

        # Persist to UAC tables
        if db_conn is not None:
            _persist_one_result(
                db_conn,
                run_id=packet.run_id,
                snapshot_id=packet.snapshot_id,
                result=result,
            )
            # Mark resumability only for ACCEPTED (failed roles get retried)
            if result.status == RoleRunnerStatus.ACCEPTED:
                mark_work_completed(
                    db_conn,
                    run_id=packet.run_id,
                    work_unit_id=work_unit_id,
                    outcome=RoleRunnerStatus.ACCEPTED,
                )

    # ── Contradiction detection (pure, ACCEPTED only) ─────────────────────────
    results_by_role: dict = {r.role_id: r for r in role_results}
    non_accepted_ids = [
        r.role_id for r in role_results
        if r.status != RoleRunnerStatus.ACCEPTED
    ]
    contradictions = detect_contradictions(
        results_by_role=results_by_role,
        missing_role_ids=non_accepted_ids,
    )

    # ── Bundle assembly ───────────────────────────────────────────────────────
    bundle = assemble_bundle(
        packet=packet,
        role_results=role_results,
        all_expected_role_ids=B1_ROLE_IDS,
        contradictions=contradictions,
    )

    return OrchestratorResult(
        role_results=tuple(role_results),
        bundle=bundle,
        contradictions=contradictions,
        persisted=(db_conn is not None),
        run_id=packet.run_id,
        snapshot_id=packet.snapshot_id,
        completed_at=datetime.now(timezone.utc).isoformat(),
    )
