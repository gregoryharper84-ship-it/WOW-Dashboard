from __future__ import annotations
from billiard.exceptions import SoftTimeLimitExceeded
from .contracts import WorkerEnvelope, JobStatus, TERMINAL_JOB_STATES
from .job_store import JobRepository
from .queue import celery_app
from .registry import worker_spec
from .runner import execute_envelope, _terminal_output, TRANSIENT_CODES

@celery_app.task(bind=True,name="wow.agent_runtime.execute_durable",acks_late=True)
def execute_durable(self,envelope:dict):
    try:
        env=WorkerEnvelope.model_validate(envelope)
    except Exception as exc:
        return {"status":"BLOCKED","error_code":"WORKER_CONTRACT_INVALID","error":type(exc).__name__,"can_execute":False}

    repo=JobRepository()
    try:
        spec=worker_spec(env.worker_id)
        lease_seconds=max(60,spec.timeout_seconds*2+15)
    except Exception:
        spec=None
        lease_seconds=180

    if not repo.claim(env.job_id,lease_seconds=lease_seconds):
        status=repo.get_status(env.job_id)
        if status is None:
            return {"status":"BLOCKED","error_code":"JOB_NOT_FOUND","job_id":env.job_id,"can_execute":False}
        if JobStatus(status) in TERMINAL_JOB_STATES:
            return {"status":"DUPLICATE_DELIVERY_IGNORED","job_id":env.job_id,"terminal_status":status,"can_execute":False}
        if status==JobStatus.RUNNING.value:
            raise self.retry(countdown=5,max_retries=1000)
        raise self.retry(countdown=2,max_retries=1000)

    from .coordinator import Coordinator
    coordinator=Coordinator(repo)
    try:
        coordinator.on_job_started(env.worker_id,env.run_id)
    except Exception:
        # Starting-state races are expected when sibling candidate jobs begin.
        # The state CAS in Coordinator/JobRepository remains authoritative.
        pass

    try:
        if spec is None:
            out=_terminal_output(env,JobStatus.DEAD_LETTERED,"RESEARCH_INTEREST",["UNKNOWN_WORKER"],{"error":"UNKNOWN_WORKER"})
        else:
            out=execute_envelope(env)
    except SoftTimeLimitExceeded:
        out=_terminal_output(env,JobStatus.TIMED_OUT,"RESEARCH_INTEREST",["WORKER_TIMED_OUT"],{})
    except Exception as exc:
        code=getattr(exc,"code",None) or type(exc).__name__
        if spec is not None and code in TRANSIENT_CODES and self.request.retries < spec.max_retries:
            if not repo.mark_retry_pending(job_id=env.job_id,error_code=code,error_detail={"error":type(exc).__name__}):
                out=_terminal_output(env,JobStatus.DEAD_LETTERED,"RESEARCH_INTEREST",["RETRY_STATE_TRANSITION_FAILED"],{"error":type(exc).__name__})
            else:
                raise self.retry(exc=exc,countdown=min(60,2**self.request.retries),max_retries=spec.max_retries)
        else:
            out=_terminal_output(env,JobStatus.DEAD_LETTERED,"RESEARCH_INTEREST",["WORKER_DEAD_LETTERED"],{"error":type(exc).__name__,"code":code})

    payload=out.model_dump(mode="json")
    inserted=repo.complete(job_id=env.job_id,output=payload)
    if inserted:
        try:
            coordinator.on_job_terminal(env,payload)
        except Exception as exc:
            run=repo.get_run(env.run_id) or {}
            current=str(run.get("status") or "")
            if current not in {"COMPLETED","COMPLETED_WITH_BLOCKERS","FAILED","CANCELED","RECONCILING"}:
                try:
                    repo.transition_run(env.run_id,current,"FAILED",f"CONTINUATION_FAILED:{type(exc).__name__}")
                except Exception:
                    pass
            return {**payload,"continuation_status":"FAILED_CLOSED","continuation_error":type(exc).__name__,"can_execute":False}
    return payload
