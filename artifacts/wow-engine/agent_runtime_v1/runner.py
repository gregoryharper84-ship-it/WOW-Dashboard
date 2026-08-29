from __future__ import annotations
from datetime import datetime, timezone
from .contracts import WorkerEnvelope, WorkerOutput, JobStatus, canonical_hash
from .registry import worker_spec
from .queue import celery_app

TRANSIENT_CODES={"TRANSPORT_429","TRANSPORT_5XX","DATABASE_TEMPORARY","QUEUE_TEMPORARY"}

def _terminal_output(env:WorkerEnvelope,status:JobStatus,ceiling:str,blockers:list[str],output:dict):
    now=datetime.now(timezone.utc)
    payload={"status":status.value,"ceiling":ceiling,"blockers":blockers,"output":output}
    return WorkerOutput(run_id=env.run_id,job_id=env.job_id,candidate_id=env.candidate_id,worker_id=env.worker_id,
        worker_version=env.worker_version,status=status,ceiling=ceiling,blockers=blockers,evidence_snapshot_id=env.evidence_snapshot_id,
        output=output,output_hash=canonical_hash(payload),started_at=now,completed_at=now,can_execute=False)

def execute_envelope(env:WorkerEnvelope)->WorkerOutput:
    spec=worker_spec(env.worker_id)
    if env.worker_version!=spec.worker_version:
        return _terminal_output(env,JobStatus.BLOCKED,"RESEARCH_INTEREST",["WORKER_VERSION_MISMATCH"],{})
    if spec.implementation_type=="FITTED_MODEL":
        cap=env.payload.get("capability")
        if not cap or cap.get("status")!="AVAILABLE" or not cap.get("artifact_id"):
            return _terminal_output(env,JobStatus.BLOCKED,"RESEARCH_INTEREST",["MODEL_UNAVAILABLE"],{"probability_publishable":False})
    handler=env.payload.get("_test_handler")
    if handler=="SUCCEED":
        return _terminal_output(env,JobStatus.SUCCEEDED,spec.authority_ceiling,[],{"ok":True})
    return _terminal_output(env,JobStatus.BLOCKED,"RESEARCH_INTEREST",["WORKER_HANDLER_NOT_WIRED"],{})

@celery_app.task(bind=True,name="wow.agent_runtime.execute",acks_late=True)
def execute_job(self,envelope:dict):
    try:
        env=WorkerEnvelope.model_validate(envelope)
    except Exception as exc:
        return {"status":"BLOCKED","error_code":"WORKER_CONTRACT_INVALID","error":type(exc).__name__,"can_execute":False}
    spec=worker_spec(env.worker_id)
    try:
        out=execute_envelope(env)
        return out.model_dump(mode="json")
    except Exception as exc:
        code=getattr(exc,"code",None)
        if code in TRANSIENT_CODES and self.request.retries < spec.max_retries:
            raise self.retry(exc=exc,countdown=min(60,2**self.request.retries))
        return _terminal_output(env,JobStatus.DEAD_LETTERED,"RESEARCH_INTEREST",["WORKER_DEAD_LETTERED"],{"error":type(exc).__name__}).model_dump(mode="json")
