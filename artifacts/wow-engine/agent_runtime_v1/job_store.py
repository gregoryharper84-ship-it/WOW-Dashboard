from __future__ import annotations
from datetime import datetime, timezone
import json, os, uuid
from typing import Any
from .contracts import JobStatus, TERMINAL_JOB_STATES, canonical_hash

class JobRepository:
    def __init__(self, dsn:str|None=None):
        self.dsn=dsn or os.getenv("SUPABASE_DB_URL")
        if not self.dsn: raise RuntimeError("AGENT_RUNTIME_DB_UNCONFIGURED")
    def _connect(self):
        import psycopg
        return psycopg.connect(self.dsn)
    def create_job(self, *, run_id:str, candidate_id:str|None, worker_id:str, worker_version:str, required:bool, input_hash:str)->tuple[dict[str,Any],bool]:
        idem=canonical_hash({"run_id":run_id,"candidate_id":candidate_id,"worker_id":worker_id,"worker_version":worker_version,"input_hash":input_hash})
        job_id=str(uuid.uuid4())
        with self._connect() as conn, conn.cursor() as cur:
            cur.execute("""insert into wow.agent_jobs(job_id,run_id,candidate_id,worker_id,worker_version,idempotency_key,status,required,input_hash)
                           values(%s,%s,%s,%s,%s,%s,'QUEUED',%s,%s)
                           on conflict(idempotency_key) do nothing
                           returning job_id,run_id,candidate_id,worker_id,worker_version,status,attempt,required,input_hash""",
                        (job_id,run_id,candidate_id,worker_id,worker_version,idem,required,input_hash))
            row=cur.fetchone()
            if row:
                return dict(zip([d.name for d in cur.description],row)),True
            cur.execute("select job_id,run_id,candidate_id,worker_id,worker_version,status,attempt,required,input_hash from wow.agent_jobs where idempotency_key=%s",(idem,))
            row=cur.fetchone(); return dict(zip([d.name for d in cur.description],row)),False
    def claim(self, job_id:str, *, lease_seconds:int=180)->bool:
        with self._connect() as conn, conn.cursor() as cur:
            cur.execute("""update wow.agent_jobs
                           set status='RUNNING',started_at=coalesce(started_at,now()),heartbeat_at=now(),attempt=attempt+1
                           where job_id=%s and (
                               status in ('QUEUED','RETRY_PENDING')
                               or (status='RUNNING' and coalesce(heartbeat_at,started_at,queued_at) < now()-make_interval(secs => %s))
                           )""",(job_id,lease_seconds))
            return cur.rowcount==1
    def get_status(self,job_id:str)->str|None:
        with self._connect() as conn, conn.cursor() as cur:
            cur.execute("select status from wow.agent_jobs where job_id=%s",(job_id,))
            row=cur.fetchone(); return row[0] if row else None
    def heartbeat(self,job_id:str)->bool:
        with self._connect() as conn, conn.cursor() as cur:
            cur.execute("update wow.agent_jobs set heartbeat_at=now() where job_id=%s and status='RUNNING'",(job_id,))
            return cur.rowcount==1
    def mark_retry_pending(self, *, job_id:str, error_code:str, error_detail:dict[str,Any]|None=None)->bool:
        with self._connect() as conn, conn.cursor() as cur:
            cur.execute("""update wow.agent_jobs set status='RETRY_PENDING',error_code=%s,error_detail_redacted=%s::jsonb,heartbeat_at=now()
                           where job_id=%s and status='RUNNING'""",
                        (error_code,json.dumps(error_detail or {}),job_id))
            return cur.rowcount==1
    def complete(self, *, job_id:str, output:dict[str,Any])->bool:
        status=JobStatus(output["status"])
        if status not in TERMINAL_JOB_STATES: raise ValueError("JOB_COMPLETION_REQUIRES_TERMINAL_STATUS")
        with self._connect() as conn, conn.cursor() as cur:
            cur.execute("select run_id,candidate_id,worker_id,worker_version,status from wow.agent_jobs where job_id=%s for update",(job_id,))
            job=cur.fetchone()
            if not job: raise RuntimeError("JOB_NOT_FOUND")
            run_id,candidate_id,worker_id,worker_version,current_status=job
            if JobStatus(current_status) in TERMINAL_JOB_STATES:
                return False
            cur.execute("""insert into wow.agent_outputs(output_id,job_id,run_id,candidate_id,worker_id,worker_version,evidence_snapshot_id,contract_version,output,output_hash)
                           values(%s,%s,%s,%s,%s,%s,%s,%s,%s::jsonb,%s)
                           on conflict(job_id) do nothing""",
                        (str(uuid.uuid4()),job_id,run_id,candidate_id,worker_id,worker_version,output.get("evidence_snapshot_id"),output.get("contract_version","wow.agent-output.v1"),json.dumps(output,default=str),output["output_hash"]))
            inserted=cur.rowcount==1
            if inserted:
                cur.execute("""update wow.agent_jobs set status=%s,output_hash=%s,ceiling=%s,blockers=%s::jsonb,completed_at=now(),heartbeat_at=now(),error_code=%s
                               where job_id=%s and status in ('RUNNING','RETRY_PENDING')""",
                            (status.value,output["output_hash"],output.get("ceiling"),json.dumps(output.get("blockers") or []),output.get("error_code"),job_id))
                if cur.rowcount!=1: raise RuntimeError("JOB_STATE_COMPARE_AND_SET_FAILED")
            return inserted
    def transition_run(self, run_id:str, expected:str, nxt:str, stage:str)->bool:
        with self._connect() as conn, conn.cursor() as cur:
            cur.execute("update wow.runs set status=%s,stage=%s,updated_at=now() where run_id=%s and status=%s",(nxt,stage,run_id,expected))
            return cur.rowcount==1
