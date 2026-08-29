from __future__ import annotations
from dataclasses import dataclass, field
from datetime import datetime, timezone
import json, os, uuid
from typing import Any
from .contracts import RunStatus, TERMINAL_RUN_STATES, canonical_hash
from .state_machine import assert_run_transition

@dataclass
class MemoryStore:
    runs: dict[str,dict[str,Any]]=field(default_factory=dict)
    jobs: dict[str,dict[str,Any]]=field(default_factory=dict)
    candidates: dict[str,dict[str,Any]]=field(default_factory=dict)
    outputs: dict[str,dict[str,Any]]=field(default_factory=dict)
    audits: list[dict[str,Any]]=field(default_factory=list)
    idem: dict[tuple[str,str],str]=field(default_factory=dict)

    def create_run(self, *, idempotency_key:str, request:dict[str,Any], governance_version:str)->dict[str,Any]:
        request_hash=canonical_hash(request)
        existing=self.idem.get((idempotency_key,request_hash))
        if existing:
            return self.runs[existing]
        run_id=str(uuid.uuid4())
        now=datetime.now(timezone.utc).isoformat()
        row={"run_id":run_id,"idempotency_key":idempotency_key,"request_hash":request_hash,"request_payload":request,
             "run_type":request["run_type"],"requested_as_of":str(request["as_of"]),"user_timezone":request["user_timezone"],
             "status":RunStatus.CREATED.value,"stage":"CREATED","can_execute":False,"dry_run_only":True,
             "governance_version":governance_version,"rows_in":0,"rows_completed":0,"rows_held":0,"rows_rejected":0,
             "created_at":now,"updated_at":now}
        self.runs[run_id]=row; self.idem[(idempotency_key,request_hash)]=run_id
        self.audit(run_id,"RUN_CREATED","wow.agent-runtime",{"request_hash":request_hash})
        return row

    def get_run(self,run_id:str)->dict[str,Any]|None:
        return self.runs.get(run_id)

    def list_candidates(self,run_id:str)->list[dict[str,Any]]:
        rows=[dict(row) for row in self.candidates.values() if str(row.get("run_id"))==str(run_id)]
        return sorted(rows,key=lambda row:(str(row.get("canonical_key") or ""),str(row.get("candidate_id") or "")))

    def transition_run(self,run_id:str,nxt:RunStatus,stage:str|None=None)->dict[str,Any]:
        row=self.runs[run_id]; current=RunStatus(row["status"]); assert_run_transition(current,nxt)
        row["status"]=nxt.value; row["stage"]=stage or nxt.value; row["updated_at"]=datetime.now(timezone.utc).isoformat()
        if nxt in TERMINAL_RUN_STATES:
            row["completed_at"]=row["updated_at"]
        self.audit(run_id,"RUN_TRANSITION","wow.agent-runtime",{"from":current.value,"to":nxt.value})
        return row

    def audit(self,run_id:str,event_type:str,actor:str,detail:dict[str,Any],candidate_id=None,job_id=None)->None:
        self.audits.append({"run_id":run_id,"candidate_id":candidate_id,"job_id":job_id,"event_type":event_type,
                            "actor":actor,"detail_redacted":detail,"created_at":datetime.now(timezone.utc).isoformat()})

    def list_audit(self,run_id:str)->list[dict[str,Any]]:
        return [x for x in self.audits if x["run_id"]==run_id]

class PostgresStore:
    """Direct Postgres repository for the private wow schema."""
    def __init__(self, dsn:str|None=None):
        self.dsn=dsn or os.getenv("SUPABASE_DB_URL")
        if not self.dsn: raise RuntimeError("AGENT_RUNTIME_DB_UNCONFIGURED")

    def _connect(self):
        import psycopg
        return psycopg.connect(self.dsn)

    def create_run(self, *, idempotency_key:str, request:dict[str,Any], governance_version:str)->dict[str,Any]:
        request_hash=canonical_hash(request)
        with self._connect() as conn, conn.cursor() as cur:
            cur.execute("select run_id,status,stage,rows_in,rows_completed,rows_held,rows_rejected,can_execute,dry_run_only from wow.runs where idempotency_key=%s and request_hash=%s",(idempotency_key,request_hash))
            found=cur.fetchone()
            if found:
                cols=[d.name for d in cur.description]; return dict(zip(cols,found))
            run_id=str(uuid.uuid4())
            cur.execute("""insert into wow.runs(run_id,idempotency_key,request_hash,request_payload,run_type,requested_as_of,user_timezone,status,stage,governance_version)
                           values(%s,%s,%s,%s::jsonb,%s,%s,%s,'CREATED','CREATED',%s)
                           returning run_id,status,stage,rows_in,rows_completed,rows_held,rows_rejected,can_execute,dry_run_only""",
                        (run_id,idempotency_key,request_hash,json.dumps(request,default=str),request["run_type"],request["as_of"],request["user_timezone"],governance_version))
            row=cur.fetchone(); cols=[d.name for d in cur.description]
            cur.execute("insert into wow.audit_events(run_id,event_type,actor,detail_redacted) values(%s,'RUN_CREATED','wow.agent-runtime',%s::jsonb)",(run_id,json.dumps({"request_hash":request_hash})))
            return dict(zip(cols,row))

    def get_run(self,run_id:str)->dict[str,Any]|None:
        with self._connect() as conn, conn.cursor() as cur:
            cur.execute("select * from wow.runs where run_id=%s",(run_id,))
            row=cur.fetchone()
            if not row:return None
            return dict(zip([d.name for d in cur.description],row))

    def list_candidates(self,run_id:str)->list[dict[str,Any]]:
        with self._connect() as conn, conn.cursor() as cur:
            cur.execute("""select candidate_id,run_id,canonical_key,sport,league,official_event_id,participant,opponent,
                                  market_family,stat_family,period,exact_line,side,settlement_operator,controlling_worker_id,
                                  evidence_snapshot_id,terminal_label,terminal_ceiling,blockers,created_at
                           from wow.run_candidates where run_id=%s order by canonical_key,candidate_id""",(run_id,))
            cols=[d.name for d in cur.description]
            return [dict(zip(cols,row)) for row in cur.fetchall()]

    def transition_run(self,run_id:str,nxt:RunStatus,stage:str|None=None)->dict[str,Any]:
        with self._connect() as conn, conn.cursor() as cur:
            cur.execute("select status from wow.runs where run_id=%s for update",(run_id,))
            found=cur.fetchone()
            if not found: raise KeyError("RUN_NOT_FOUND")
            current=RunStatus(found[0]); assert_run_transition(current,nxt)
            completed=nxt in TERMINAL_RUN_STATES
            cur.execute("""update wow.runs
                           set status=%s,stage=%s,updated_at=now(),completed_at=case when %s then now() else completed_at end
                           where run_id=%s and status=%s
                           returning *""",
                        (nxt.value,stage or nxt.value,completed,run_id,current.value))
            row=cur.fetchone()
            if not row: raise RuntimeError("RUN_STATE_COMPARE_AND_SET_FAILED")
            cols=[d.name for d in cur.description]
            cur.execute("insert into wow.audit_events(run_id,event_type,actor,detail_redacted) values(%s,'RUN_TRANSITION','wow.agent-runtime',%s::jsonb)",
                        (run_id,json.dumps({"from":current.value,"to":nxt.value})))
            return dict(zip(cols,row))

    def list_audit(self,run_id:str)->list[dict[str,Any]]:
        with self._connect() as conn, conn.cursor() as cur:
            cur.execute("select audit_event_id,event_type,actor,detail_redacted,created_at from wow.audit_events where run_id=%s order by created_at",(run_id,))
            cols=[d.name for d in cur.description]
            return [dict(zip(cols,r)) for r in cur.fetchall()]

_MEMORY=MemoryStore()
def get_store():
    if os.getenv("WOW_AGENT_RUNTIME_STORE","postgres").lower()=="memory":
        return _MEMORY
    return PostgresStore()
