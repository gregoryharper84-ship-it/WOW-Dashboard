from __future__ import annotations
from datetime import datetime
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
    def get_job(self,job_id:str)->dict[str,Any]|None:
        with self._connect() as conn, conn.cursor() as cur:
            cur.execute("select * from wow.agent_jobs where job_id=%s",(job_id,))
            row=cur.fetchone(); return dict(zip([d.name for d in cur.description],row)) if row else None
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
    def get_run(self,run_id:str)->dict[str,Any]|None:
        with self._connect() as conn, conn.cursor() as cur:
            cur.execute("select * from wow.runs where run_id=%s",(run_id,))
            row=cur.fetchone(); return dict(zip([d.name for d in cur.description],row)) if row else None
    def upsert_candidates(self,run_id:str,candidates:list[dict[str,Any]])->list[dict[str,Any]]:
        rows=[]
        with self._connect() as conn, conn.cursor() as cur:
            for candidate in candidates:
                cid=str(uuid.uuid4())
                canonical=str(candidate.get("canonical_key") or "")
                cur.execute("""insert into wow.run_candidates(
                    candidate_id,run_id,canonical_key,sport,league,official_event_id,participant,opponent,market_family,
                    stat_family,period,exact_line,side,settlement_operator,controlling_worker_id,candidate_payload)
                    values(%s,%s,%s,%s,%s,%s,%s,%s,%s,%s,%s,%s,%s,%s,%s,%s::jsonb)
                    on conflict(run_id,canonical_key) do update set candidate_payload=excluded.candidate_payload
                    returning *""",
                    (cid,run_id,canonical,str(candidate.get("sport") or "").upper(),candidate.get("league"),candidate.get("official_event_id"),
                     candidate.get("participant"),candidate.get("opponent"),str(candidate.get("market_family") or "").upper(),candidate.get("stat_family"),
                     str(candidate.get("period") or "FULL_GAME").upper(),candidate.get("exact_line"),candidate.get("side"),candidate.get("settlement_operator"),
                     candidate.get("controlling_worker_id") or "wow.controlling-model",json.dumps(candidate,default=str)))
                row=cur.fetchone(); rows.append(dict(zip([d.name for d in cur.description],row)))
            cur.execute("update wow.runs set rows_in=(select count(*) from wow.run_candidates where run_id=%s),updated_at=now() where run_id=%s",(run_id,run_id))
        return rows
    def get_candidate(self,candidate_id:str)->dict[str,Any]|None:
        with self._connect() as conn, conn.cursor() as cur:
            cur.execute("select * from wow.run_candidates where candidate_id=%s",(candidate_id,))
            row=cur.fetchone(); return dict(zip([d.name for d in cur.description],row)) if row else None
    def list_candidates(self,run_id:str)->list[dict[str,Any]]:
        with self._connect() as conn, conn.cursor() as cur:
            cur.execute("select * from wow.run_candidates where run_id=%s order by canonical_key,candidate_id",(run_id,))
            cols=[d.name for d in cur.description]; return [dict(zip(cols,row)) for row in cur.fetchall()]
    def list_jobs(self,run_id:str,*,candidate_id:str|None=None,worker_id:str|None=None)->list[dict[str,Any]]:
        sql="select * from wow.agent_jobs where run_id=%s"; args:list[Any]=[run_id]
        if candidate_id is not None: sql+=" and candidate_id=%s"; args.append(candidate_id)
        if worker_id is not None: sql+=" and worker_id=%s"; args.append(worker_id)
        sql+=" order by queued_at,job_id"
        with self._connect() as conn, conn.cursor() as cur:
            cur.execute(sql,args); cols=[d.name for d in cur.description]; return [dict(zip(cols,row)) for row in cur.fetchall()]
    def get_output(self,job_id:str)->dict[str,Any]|None:
        with self._connect() as conn, conn.cursor() as cur:
            cur.execute("select output from wow.agent_outputs where job_id=%s",(job_id,))
            row=cur.fetchone(); return dict(row[0]) if row and isinstance(row[0],dict) else (row[0] if row else None)
    def create_evidence_snapshot(self,*,run_id:str,candidate_id:str,as_of:datetime,event_start:datetime,payload:dict[str,Any],provenance:dict[str,Any],missing:list[str],conflicts:list[str],payload_hash:str)->str:
        evidence_id=str(uuid.uuid4())
        with self._connect() as conn, conn.cursor() as cur:
            cur.execute("""insert into wow.evidence_snapshots(evidence_snapshot_id,run_id,candidate_id,as_of,event_start_utc,payload,provenance,missing_fields,source_conflicts,payload_hash)
                           values(%s,%s,%s,%s,%s,%s::jsonb,%s::jsonb,%s::jsonb,%s::jsonb,%s)
                           on conflict(candidate_id,payload_hash) do update set provenance=excluded.provenance
                           returning evidence_snapshot_id""",
                        (evidence_id,run_id,candidate_id,as_of,event_start,json.dumps(payload,default=str),json.dumps(provenance,default=str),json.dumps(missing),json.dumps(conflicts),payload_hash))
            eid=str(cur.fetchone()[0])
            cur.execute("update wow.run_candidates set evidence_snapshot_id=%s where candidate_id=%s",(eid,candidate_id))
            return eid
    def set_candidate_terminal(self,*,candidate_id:str,label:str,ceiling:str,blockers:list[str],probability_publishable:bool=False)->bool:
        decision_hash=canonical_hash({"candidate_id":candidate_id,"label":label,"ceiling":ceiling,"blockers":sorted(set(blockers)),"probability_publishable":probability_publishable})
        with self._connect() as conn, conn.cursor() as cur:
            cur.execute("select run_id,controlling_worker_id,terminal_label from wow.run_candidates where candidate_id=%s for update",(candidate_id,))
            row=cur.fetchone()
            if not row or row[2] is not None:return False
            run_id,controlling_worker_id,_=row
            clean=sorted(set(blockers))
            cur.execute("update wow.run_candidates set terminal_label=%s,terminal_ceiling=%s,blockers=%s::jsonb where candidate_id=%s and terminal_label is null",(label,ceiling,json.dumps(clean),candidate_id))
            if cur.rowcount!=1:return False
            cur.execute("""insert into wow.terminal_decisions(decision_id,run_id,candidate_id,final_terminal_ceiling,terminal_label,controlling_worker_id,probability_publishable,blockers,reducer_version,decision_hash)
                           values(%s,%s,%s,%s,%s,%s,%s,%s::jsonb,'wow.terminal-ceiling-reducer/1.0.0',%s)
                           on conflict(candidate_id) do nothing""",
                        (str(uuid.uuid4()),run_id,candidate_id,ceiling,label,controlling_worker_id,probability_publishable,json.dumps(clean),decision_hash))
            return True
    def reconcile_run(self,run_id:str)->dict[str,Any]:
        with self._connect() as conn, conn.cursor() as cur:
            cur.execute("""select count(*) as rows_in,
                count(*) filter(where terminal_label='FINAL_APPROVED') as completed,
                count(*) filter(where terminal_label is not null and terminal_label<>'FINAL_APPROVED' and terminal_label not in ('SLATE_PURGE','REJECT_DATA_QUALITY')) as held,
                count(*) filter(where terminal_label in ('SLATE_PURGE','REJECT_DATA_QUALITY')) as rejected,
                count(*) filter(where terminal_label is null) as pending
                from wow.run_candidates where run_id=%s""",(run_id,))
            rows_in,completed,held,rejected,pending=cur.fetchone()
            cur.execute("update wow.runs set rows_in=%s,rows_completed=%s,rows_held=%s,rows_rejected=%s,updated_at=now() where run_id=%s",(rows_in,completed,held,rejected,run_id))
            return {"rows_in":rows_in,"rows_completed":completed,"rows_held":held,"rows_rejected":rejected,"rows_pending":pending,"balanced":rows_in==completed+held+rejected+pending}
