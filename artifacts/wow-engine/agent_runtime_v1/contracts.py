from __future__ import annotations
from datetime import datetime
from enum import Enum
from hashlib import sha256
import json
from typing import Any, Literal
from pydantic import BaseModel, ConfigDict, Field, model_validator

class RunStatus(str, Enum):
    CREATED="CREATED"; VALIDATING_REQUEST="VALIDATING_REQUEST"; DISCOVERY_QUEUED="DISCOVERY_QUEUED"
    DISCOVERY_RUNNING="DISCOVERY_RUNNING"; ROUTING="ROUTING"; EVIDENCE_QUEUED="EVIDENCE_QUEUED"
    EVIDENCE_RUNNING="EVIDENCE_RUNNING"; MODELING_QUEUED="MODELING_QUEUED"; MODELING_RUNNING="MODELING_RUNNING"
    AUDIT_QUEUED="AUDIT_QUEUED"; AUDIT_RUNNING="AUDIT_RUNNING"; FINAL_REFRESH="FINAL_REFRESH"
    RECONCILING="RECONCILING"; COMPLETED="COMPLETED"; COMPLETED_WITH_BLOCKERS="COMPLETED_WITH_BLOCKERS"
    FAILED="FAILED"; CANCELED="CANCELED"

class JobStatus(str, Enum):
    QUEUED="QUEUED"; RUNNING="RUNNING"; SUCCEEDED="SUCCEEDED"; BLOCKED="BLOCKED"; REJECTED="REJECTED"
    TIMED_OUT="TIMED_OUT"; RETRY_PENDING="RETRY_PENDING"; DEAD_LETTERED="DEAD_LETTERED"; CANCELED="CANCELED"

TERMINAL_JOB_STATES={JobStatus.SUCCEEDED,JobStatus.BLOCKED,JobStatus.REJECTED,JobStatus.TIMED_OUT,JobStatus.DEAD_LETTERED,JobStatus.CANCELED}
TERMINAL_RUN_STATES={RunStatus.COMPLETED,RunStatus.COMPLETED_WITH_BLOCKERS,RunStatus.FAILED,RunStatus.CANCELED}

class RunCreateRequest(BaseModel):
    model_config=ConfigDict(extra="forbid")
    run_type: Literal["FULL_MODEL"]="FULL_MODEL"
    as_of: datetime
    user_timezone: str
    lanes: list[str]=Field(default_factory=list)
    sports: list[str]=Field(default_factory=list)
    candidate_inputs: list[dict[str,Any]]=Field(default_factory=list)
    discovery_enabled: bool=True
    can_execute: bool=False
    @model_validator(mode="after")
    def execution_prohibited(self):
        if self.can_execute:
            raise ValueError("EXECUTION_PROHIBITED")
        return self

class WorkerEnvelope(BaseModel):
    model_config=ConfigDict(extra="forbid")
    contract_version: Literal["wow.agent-job.v1"]="wow.agent-job.v1"
    run_id: str; job_id: str; candidate_id: str|None=None
    worker_id: str; worker_version: str; required: bool=True
    evidence_snapshot_id: str|None=None; as_of: datetime; input_hash: str
    payload: dict[str,Any]=Field(default_factory=dict)
    can_execute: Literal[False]=False

class WorkerOutput(BaseModel):
    model_config=ConfigDict(extra="forbid")
    contract_version: Literal["wow.agent-output.v1"]="wow.agent-output.v1"
    run_id: str; job_id: str; candidate_id: str|None=None
    worker_id: str; worker_version: str; status: JobStatus
    ceiling: str; blockers: list[str]=Field(default_factory=list)
    evidence_snapshot_id: str|None=None; output: dict[str,Any]=Field(default_factory=dict)
    output_hash: str; started_at: datetime; completed_at: datetime
    can_execute: Literal[False]=False

def canonical_hash(value: Any) -> str:
    raw=json.dumps(value,sort_keys=True,separators=(",",":"),default=str).encode()
    return sha256(raw).hexdigest()
