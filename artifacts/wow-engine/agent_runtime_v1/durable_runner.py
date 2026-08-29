from __future__ import annotations
from .contracts import WorkerEnvelope
from .job_store import JobRepository
from .queue import celery_app
from .runner import execute_envelope

@celery_app.task(bind=True,name="wow.agent_runtime.execute_durable",acks_late=True)
def execute_durable(self,envelope:dict):
    try:
        env=WorkerEnvelope.model_validate(envelope)
    except Exception as exc:
        return {"status":"BLOCKED","error_code":"WORKER_CONTRACT_INVALID","error":type(exc).__name__,"can_execute":False}
    repo=JobRepository()
    if not repo.claim(env.job_id):
        return {"status":"DUPLICATE_DELIVERY_IGNORED","job_id":env.job_id,"can_execute":False}
    out=execute_envelope(env).model_dump(mode="json")
    repo.complete(job_id=env.job_id,output=out)
    return out
