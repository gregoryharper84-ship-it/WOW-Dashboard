"""Static worker registry for the governed WOW Agent Runtime.

Code is the source of truth; wow_agent_worker_registry (Supabase) mirrors it
for auditability. /health/ready fails closed if the two disagree.
"""
from __future__ import annotations

from dataclasses import dataclass
from typing import Literal

ImplementationType = Literal["DETERMINISTIC", "FITTED_MODEL", "RESEARCH_AGENT"]


@dataclass(frozen=True)
class WorkerSpec:
    worker_id: str
    worker_version: str
    contract_version: str
    implementation_type: ImplementationType
    authority_ceiling: str
    timeout_seconds: int
    max_retries: int
    required_predecessors: tuple[str, ...] = ()
    artifact_required: bool = False


def _research(worker_id: str, predecessors: tuple[str, ...], timeout: int = 30) -> WorkerSpec:
    return WorkerSpec(
        worker_id, "1.0.0", "wow.agent-output.v1", "RESEARCH_AGENT",
        "RESEARCH_INTEREST", timeout, 2, predecessors,
    )


WORKERS: dict[str, WorkerSpec] = {
    "wow.parallel-discovery-router": WorkerSpec(
        "wow.parallel-discovery-router", "1.0.0", "wow.agent-output.v1",
        "RESEARCH_AGENT", "RESEARCH_INTEREST", 30, 2,
    ),
    "wow.global-scout-coordinator": _research(
        "wow.global-scout-coordinator", ("wow.parallel-discovery-router",), 30,
    ),
    "wow.prop-scout-router": _research(
        "wow.prop-scout-router", ("wow.global-scout-coordinator",), 30,
    ),
    "wow.ml-event-scout-router": _research(
        "wow.ml-event-scout-router", ("wow.global-scout-coordinator",), 30,
    ),
    "wow.slate-integrity-expert": WorkerSpec(
        "wow.slate-integrity-expert", "1.0.0", "wow.agent-output.v1",
        "DETERMINISTIC", "IDENTITY_VERIFIED", 20, 1,
        ("wow.global-scout-coordinator",),
    ),
    "wow.source-provenance-researcher": _research(
        "wow.source-provenance-researcher", ("wow.slate-integrity-expert",), 30,
    ),
    "wow.participant-status-researcher": _research(
        "wow.participant-status-researcher", ("wow.slate-integrity-expert",), 30,
    ),
    "wow.history-comparables-researcher": _research(
        "wow.history-comparables-researcher", ("wow.slate-integrity-expert",), 45,
    ),
    "wow.matchup-context-researcher": _research(
        "wow.matchup-context-researcher", ("wow.slate-integrity-expert",), 30,
    ),
    "wow.market-settlement-researcher": _research(
        "wow.market-settlement-researcher", ("wow.slate-integrity-expert",), 30,
    ),
    "wow.research-evidence-reconciler": WorkerSpec(
        "wow.research-evidence-reconciler", "1.0.0", "wow.agent-output.v1",
        "DETERMINISTIC", "RESEARCH_INTEREST", 20, 1,
        (
            "wow.source-provenance-researcher",
            "wow.participant-status-researcher",
            "wow.history-comparables-researcher",
            "wow.matchup-context-researcher",
            "wow.market-settlement-researcher",
        ),
    ),
    "wow.evidence-hydration": WorkerSpec(
        "wow.evidence-hydration", "1.0.0", "wow.agent-output.v1",
        "DETERMINISTIC", "EVIDENCE_VERIFIED", 45, 2,
        ("wow.research-evidence-reconciler",),
    ),
    "wow.controlling-model": WorkerSpec(
        "wow.controlling-model", "1.0.0", "wow.agent-output.v1",
        "FITTED_MODEL", "MODEL_QUALIFIED_HOLD", 60, 1,
        ("wow.evidence-hydration",), artifact_required=True,
    ),
    "wow.failure-path-framework": WorkerSpec(
        "wow.failure-path-framework", "1.0.0", "wow.agent-output.v1",
        "DETERMINISTIC", "MODEL_QUALIFIED_HOLD", 30, 1,
        ("wow.controlling-model",),
    ),
    "wow.dynamic-calibration-expert": WorkerSpec(
        "wow.dynamic-calibration-expert", "1.0.0", "wow.agent-output.v1",
        "DETERMINISTIC", "MODEL_QUALIFIED_HOLD", 30, 1,
        ("wow.failure-path-framework",), artifact_required=True,
    ),
    "wow.exact-line-market-auditor": WorkerSpec(
        "wow.exact-line-market-auditor", "1.0.0", "wow.agent-output.v1",
        "DETERMINISTIC", "MARKET_VERIFIED_HOLD", 30, 2,
        ("wow.dynamic-calibration-expert",),
    ),
    "wow.structure-exposure-governor": WorkerSpec(
        "wow.structure-exposure-governor", "1.0.0", "wow.agent-output.v1",
        "DETERMINISTIC", "STRUCTURE_VERIFIED_HOLD", 20, 1,
        ("wow.exact-line-market-auditor",),
    ),
    "wow.final-refresh-governor": WorkerSpec(
        "wow.final-refresh-governor", "1.0.0", "wow.agent-output.v1",
        "DETERMINISTIC", "FINAL_REFRESH_HOLD", 30, 2,
        ("wow.structure-exposure-governor",),
    ),
    "wow.terminal-ceiling-reducer": WorkerSpec(
        "wow.terminal-ceiling-reducer", "1.0.0", "wow.agent-output.v1",
        "DETERMINISTIC", "FINAL_APPROVED", 15, 0,
        ("wow.final-refresh-governor",),
    ),
}


def worker_spec(worker_id: str) -> WorkerSpec:
    try:
        return WORKERS[worker_id]
    except KeyError as exc:
        raise ValueError(f"UNKNOWN_WORKER: {worker_id}") from exc
