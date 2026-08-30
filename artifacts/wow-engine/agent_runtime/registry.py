"""Static worker registry (packet section 6) — the canonical WOW Agent
Runtime V1 worker set, one per pipeline stage, in dependency order.

Code is the source of truth; wow_agent_worker_registry (Supabase) mirrors it
for auditability. /health/ready fails closed if the two disagree — see
repository.registry_matches(). Adopted from PR #33
(feature/wow-agent-runtime-v1) during the convergence pass: Phase 1 had a
worker_registry table but no code-side registry and no parity check.
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


WORKERS: dict[str, WorkerSpec] = {
    "wow.parallel-discovery-router": WorkerSpec(
        "wow.parallel-discovery-router", "1.0.0", "wow.agent-output.v1",
        "RESEARCH_AGENT", "RESEARCH_INTEREST", 30, 2,
    ),
    "wow.slate-integrity-expert": WorkerSpec(
        "wow.slate-integrity-expert", "1.0.0", "wow.agent-output.v1",
        "DETERMINISTIC", "IDENTITY_VERIFIED", 20, 1,
        ("wow.parallel-discovery-router",),
    ),
    "wow.evidence-hydration": WorkerSpec(
        "wow.evidence-hydration", "1.0.0", "wow.agent-output.v1",
        "DETERMINISTIC", "EVIDENCE_VERIFIED", 45, 2,
        ("wow.slate-integrity-expert",),
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
