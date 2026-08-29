from __future__ import annotations
import os
from fastapi import APIRouter, Depends, Header, HTTPException
from .contracts import RunCreateRequest, TERMINAL_RUN_STATES, RunStatus
from .store import get_store

router=APIRouter()
GOVERNANCE_VERSION=os.getenv("WOW_GOVERNANCE_VERSION","WOW-v16-CLEAN-CORE")

def _require(authorization:str|None):
    import api_prod as prod
    return prod._require_action_api_key(authorization)

def _auth(authorization=Header(default=None,alias="Authorization")):
    return _require(authorization)

def _store_or_503():
    try:return get_store()
    except Exception as exc:
        raise HTTPException(status_code=503,detail={"code":"AGENT_RUNTIME_STORE_UNAVAILABLE","error":type(exc).__name__,"can_execute":False})

@router.post("/wow/runs",status_code=202,operation_id="createWowAgentRun")
def create_run(req:RunCreateRequest,idempotency_key:str=Header(...,alias="Idempotency-Key"),_authz=Depends(_auth)):
    store=_store_or_503()
    row=store.create_run(idempotency_key=idempotency_key,request=req.model_dump(mode="json"),governance_version=GOVERNANCE_VERSION)
    return {"ok":True,"run_id":str(row["run_id"]),"status":row["status"],"terminal":row["status"] in {s.value for s in TERMINAL_RUN_STATES},
            "poll_url":f"/wow/runs/{row['run_id']}/manifest","can_execute":False}

@router.get("/wow/runs/{run_id}",operation_id="getWowAgentRun")
def get_run(run_id:str,_authz=Depends(_auth)):
    row=_store_or_503().get_run(run_id)
    if not row: raise HTTPException(404,detail={"code":"RUN_NOT_FOUND","can_execute":False})
    return {"run_id":str(row["run_id"]),"status":row["status"],"stage":row["stage"],"terminal":row["status"] in {s.value for s in TERMINAL_RUN_STATES},"can_execute":False}

@router.get("/wow/runs/{run_id}/manifest",operation_id="getWowAgentRunManifest")
def manifest(run_id:str,_authz=Depends(_auth)):
    row=_store_or_503().get_run(run_id)
    if not row: raise HTTPException(404,detail={"code":"RUN_NOT_FOUND","can_execute":False})
    terminal=row["status"] in {s.value for s in TERMINAL_RUN_STATES}
    rows_in=int(row.get("rows_in") or 0); completed=int(row.get("rows_completed") or 0); held=int(row.get("rows_held") or 0); rejected=int(row.get("rows_rejected") or 0)
    body={"run_id":str(row["run_id"]),"status":row["status"],"terminal":terminal,"stage":row["stage"],"rows_discovered":rows_in,
          "rows_terminal":completed+held+rejected,"rows_pending":max(0,rows_in-completed-held-rejected),"can_execute":False}
    if terminal:
        body["reconciliation"]={"rows_in":rows_in,"rows_completed":completed,"rows_held":held,"rows_rejected":rejected,"balanced":rows_in==completed+held+rejected}
    return body

@router.get("/wow/runs/{run_id}/audit",operation_id="getWowAgentRunAudit")
def audit(run_id:str,_authz=Depends(_auth)):
    store=_store_or_503()
    if not store.get_run(run_id): raise HTTPException(404,detail={"code":"RUN_NOT_FOUND","can_execute":False})
    return {"run_id":run_id,"events":store.list_audit(run_id),"can_execute":False}

@router.post("/wow/runs/{run_id}/cancel",operation_id="cancelWowAgentRun")
def cancel(run_id:str,_authz=Depends(_auth)):
    store=_store_or_503(); row=store.get_run(run_id)
    if not row: raise HTTPException(404,detail={"code":"RUN_NOT_FOUND","can_execute":False})
    if row["status"] in {s.value for s in TERMINAL_RUN_STATES}: return {"run_id":run_id,"status":row["status"],"terminal":True,"can_execute":False}
    if not hasattr(store,"transition_run"): raise HTTPException(409,detail={"code":"CANCEL_REQUIRES_ORCHESTRATOR","can_execute":False})
    row=store.transition_run(run_id,RunStatus.CANCELED)
    return {"run_id":run_id,"status":row["status"],"terminal":True,"can_execute":False}

@router.get("/health/live",include_in_schema=False)
def agent_live():
    return {"ok":True,"service":"wow-agent-runtime","status":"alive","can_execute":False}

@router.get("/health/ready",include_in_schema=False)
def agent_ready():
    db=False; queue=False; registry=True
    try: db=get_store() is not None
    except Exception: db=False
    try:
        import redis
        url=os.getenv("REDIS_URL")
        if url:
            r=redis.Redis.from_url(url,socket_connect_timeout=1,socket_timeout=1); queue=bool(r.ping())
    except Exception: queue=False
    ok=db and queue and registry
    if not ok: raise HTTPException(503,detail={"ok":False,"database":db,"queue":queue,"registry":registry,"can_execute":False})
    return {"ok":True,"database":db,"queue":queue,"registry":registry,"can_execute":False}
