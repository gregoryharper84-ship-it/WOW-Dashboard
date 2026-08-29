from __future__ import annotations
from datetime import datetime
from typing import Any
from .contracts import RunStatus, WorkerEnvelope, canonical_hash
from .job_store import JobRepository
from .registry import worker_spec
from .durable_runner import execute_durable

class Orchestrator:
    """Durable WOW scheduler. Queueing is idempotent; execution remains dry-run only."""
    def __init__(self, jobs:JobRepository|None=None):
        self.jobs=jobs or JobRepository()

    def queue_worker(self, *, run_id:str, candidate_id:str|None, worker_id:str, evidence_snapshot_id:str|None, as_of:datetime, payload:dict[str,Any], required:bool=True)->dict[str,Any]:
        spec=worker_spec(worker_id)
        input_hash=canonical_hash({"evidence_snapshot_id":evidence_snapshot_id,"payload":payload,"worker_version":spec.worker_version})
        job,created=self.jobs.create_job(run_id=run_id,candidate_id=candidate_id,worker_id=worker_id,worker_version=spec.worker_version,required=required,input_hash=input_hash)
        if created:
            env=WorkerEnvelope(run_id=run_id,job_id=str(job["job_id"]),candidate_id=candidate_id,worker_id=worker_id,worker_version=spec.worker_version,required=required,evidence_snapshot_id=evidence_snapshot_id,as_of=as_of,input_hash=input_hash,payload=payload,can_execute=False)
            execute_durable.apply_async(
                args=[env.model_dump(mode="json")],
                task_id=str(job["job_id"]),
                queue="wow-agent",
                soft_time_limit=spec.timeout_seconds,
                time_limit=spec.timeout_seconds+5,
            )
        return {**job,"created":created,"can_execute":False}

    def start_run(self, *, store, run:dict[str,Any], request:dict[str,Any])->dict[str,Any]:
        """Start a CREATED run exactly once and enqueue the discovery boundary."""
        current=str(run.get("status"))
        if current != RunStatus.CREATED.value:
            return {"run":run,"job":None,"started":False,"can_execute":False}
        run_id=str(run["run_id"])
        try:
            run=store.transition_run(run_id,RunStatus.VALIDATING_REQUEST,"VALIDATING_REQUEST")
            rows=request.get("candidate_inputs") or []
            discovery_enabled=bool(request.get("discovery_enabled",True))
            job=self.queue_worker(
                run_id=run_id,
                candidate_id=None,
                worker_id="wow.parallel-discovery-router",
                evidence_snapshot_id=None,
                as_of=request["as_of"] if isinstance(request["as_of"],datetime) else datetime.fromisoformat(str(request["as_of"]).replace("Z","+00:00")),
                payload={
                    "rows":rows,
                    "discovery_enabled":discovery_enabled,
                    "lanes":request.get("lanes") or [],
                    "sports":request.get("sports") or [],
                },
                required=True,
            )
            run=store.transition_run(run_id,RunStatus.DISCOVERY_QUEUED,"DISCOVERY_QUEUED")
            return {"run":run,"job":job,"started":True,"can_execute":False}
        except Exception:
            latest=store.get_run(run_id)
            if latest and latest.get("status")==RunStatus.VALIDATING_REQUEST.value:
                try:
                    store.transition_run(run_id,RunStatus.FAILED,"DISCOVERY_QUEUE_FAILED")
                except Exception:
                    pass
            raise
