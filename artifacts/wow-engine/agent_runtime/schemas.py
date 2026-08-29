"""Pydantic request/response contracts (packet section 9) and the canonical
worker envelope (packet section 10), scoped to what Phase 1 needs: run
creation, polling, and an in-process fake-worker envelope for the Phase 1
exit-criterion test. Real worker envelopes for queued/remote execution are a
Phase 2 concern (Celery task signature, not this module).
"""
from __future__ import annotations

from typing import Any, Optional

from pydantic import BaseModel, ConfigDict, Field


class RunCreateRequest(BaseModel):
    model_config = ConfigDict(extra="forbid")

    run_type: str
    as_of: str
    user_timezone: str
    lanes: list[str] = Field(default_factory=list)
    sports: list[str] = Field(default_factory=list)
    candidate_inputs: list[dict[str, Any]] = Field(default_factory=list)
    discovery_enabled: bool = True
    # Accepted only so it can be explicitly rejected with 422
    # EXECUTION_PROHIBITED rather than silently ignored (packet section 9).
    can_execute: bool = False


class RunCreateResponse(BaseModel):
    ok: bool = True
    run_id: str
    status: str
    terminal: bool
    poll_url: str
    reused: bool = False
    can_execute: bool = False


class JobCounts(BaseModel):
    queued: int = 0
    running: int = 0
    blocked: int = 0
    timed_out: int = 0


class NonterminalManifestResponse(BaseModel):
    run_id: str
    status: str
    terminal: bool = False
    stage: str
    rows_discovered: int = 0
    rows_terminal: int = 0
    rows_pending: int = 0
    jobs: JobCounts = Field(default_factory=JobCounts)
    can_execute: bool = False


class ReconciliationBlock(BaseModel):
    rows_in: int
    rows_completed: int
    rows_held: int
    rows_rejected: int
    balanced: bool


class TerminalManifestResponse(BaseModel):
    run_id: str
    status: str
    terminal: bool = True
    reconciliation: ReconciliationBlock
    candidates: list[dict[str, Any]] = Field(default_factory=list)
    can_execute: bool = False


class WorkerJobEnvelope(BaseModel):
    """wow.agent-job.v1 (packet section 10)."""
    model_config = ConfigDict(extra="forbid")

    contract_version: str = "wow.agent-job.v1"
    run_id: str
    job_id: str
    candidate_id: Optional[str] = None
    worker_id: str
    worker_version: str
    required: bool
    evidence_snapshot_id: Optional[str] = None
    as_of: str
    input_hash: str
    payload: dict[str, Any] = Field(default_factory=dict)


class WorkerOutputEnvelope(BaseModel):
    """wow.agent-output.v1 (packet section 10). A Pydantic validation failure
    on this model must terminate the job as BLOCKED with
    WORKER_CONTRACT_INVALID — never be silently coerced (packet section 10)."""
    model_config = ConfigDict(extra="forbid")

    contract_version: str = "wow.agent-output.v1"
    run_id: str
    job_id: str
    candidate_id: Optional[str] = None
    worker_id: str
    worker_version: str
    status: str
    ceiling: Optional[str] = None
    blockers: list[str] = Field(default_factory=list)
    evidence_snapshot_id: Optional[str] = None
    output: dict[str, Any] = Field(default_factory=dict)
    output_hash: str
    can_execute: bool = False
