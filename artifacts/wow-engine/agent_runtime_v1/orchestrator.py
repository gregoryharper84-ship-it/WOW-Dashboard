from __future__ import annotations
import uuid
from datetime import datetime, timezone
from typing import Any
from .contracts import RunStatus, WorkerEnvelope, canonical_hash
from .job_store import JobRepository
from .registry import worker_spec
from .durable_runner import execute_durable

class Orchestrator:
    """Minimal durable scheduler. Run completion remains reducer/reconciliation owned."""
    def __init__(self, jobs:JobRepository|None=None):
        self.jobs=jobs or JobRepository()

    def queue_worker(self, *, run_id:str, candidate_id:str|None, worker_id:str, evidence_snapshot_id:str|None, as_of:datetime, payload:dict[str,Any], required:bool=True)->dict[str,Any]:
        spec=worker_spec(worker_id)
        input_hash=canonical_hash({"evidence_snapshot_id":evidence_snapshot_id,"payload":payload,"worker_version":spec.worker_version})
        job,created=self.jobs.create_job(run_id=run_id,candidate_id=candidate_id,worker_id=worker_id,worker_version=spec.worker_version,required=required,input_hash=input_hash)
        if created:
            env=WorkerEnvelope(run_id=run_id,job_id=str(job["job_id"]),candidate_id=candidate_id,worker_id=worker_id,worker_version=spec.worker_version,required=required,evidence_snapshot_id=evidence_snapshot_id,as_of=as_of,input_hash=input_hash,payload=payload,can_execute=False)
            execute_durable.apply_async(args=[env.model_dump(mode="json")],task_id=str(job["job_id"]),queue="wow-agent")
        return {**job,"created":created,"can_execute":False}
