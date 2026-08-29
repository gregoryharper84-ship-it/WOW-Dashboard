from __future__ import annotations
import os
from fastapi import APIRouter, Depends, Header, HTTPException
from .contracts import RunCreateRequest, TERMINAL_RUN_STATES, RunStatus
from .store import get_store
from .orchestrator import Orchestrator
from .registry import WORKERS

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

def _terminal_states()->set[str]:
    return {s.value for s in TERMINAL_RUN_STATES}

def _manifest_row(candidate:dict):
    return {
        "candidate_id":str(candidate.get("candidate_id")) if candidate.get("candidate_id") is not None else None,
        "canonical_key":candidate.get("canonical_key"),
        "sport":candidate.get("sport"),
        "league":candidate.get("league"),
        "official_event_id":candidate.get("official_event_id"),
        "participant":candidate.get("participant"),
        "opponent":candidate.get("opponent"),
        "market_family":candidate.get("market_family"),
        "stat_family":candidate.get("stat_family"),
        "period":candidate.get("period"),
        "exact_line":candidate.get("exact_line"),
        "side":candidate.get("side"),
        "settlement_operator":candidate.get("settlement_operator"),
        "controlling_worker_id":candidate.get("controlling_worker_id"),
        "evidence_snapshot_id":str(candidate.get("evidence_snapshot_id")) if candidate.get("evidence_snapshot_id") is not None else None,
        "terminal_label":candidate.get("terminal_label"),
        "terminal_ceiling":candidate.get("terminal_ceiling"),
        "blockers":candidate.get("blockers") or [],
        "can_execute":False,
    }

@router.post("/wow/runs",status_code=202,operation_id="createWowAgentRun")
def create_run(req:RunCreateRequest,idempotency_key:str=Header(...,alias="Idempotency-Key"),_authz=Depends(_auth)):
    store=_store_or_503(); request=req.model_dump(mode="json")
    row=store.create_run(idempotency_key=idempotency_key,request=request,governance_version=GOVERNANCE_VERSION)
    try:
        started=Orchestrator().start_run(store=store,run=row,request=request)
        row=started["run"]
    except Exception as exc:
        latest=store.get_run(str(row["run_id"])) or row
        raise HTTPException(status_code=503,detail={
            "code":"AGENT_RUNTIME_START_FAILED",
            "run_id":str(row["run_id"]),
            "status":latest.get("status"),
            "error":type(exc).__name__,
            "probability_publishable":False,
            "can_execute":False,
        }) from exc
    return {"ok":True,"run_id":str(row["run_id"]),"status":row["status"],"terminal":row["status"] in _terminal_states(),
            "poll_url":f"/wow/runs/{row['run_id']}/manifest","can_execute":False}

@router.get("/wow/runs/{run_id}",operation_id="getWowAgentRun")
def get_run(run_id:str,_authz=Depends(_auth)):
    row=_store_or_503().get_run(run_id)
    if not row: raise HTTPException(404,detail={"code":"RUN_NOT_FOUND","can_execute":False})
    return {"run_id":str(row["run_id"]),"status":row["status"],"stage":row["stage"],"terminal":row["status"] in _terminal_states(),"can_execute":False}

@router.get("/wow/runs/{run_id}/manifest",operation_id="getWowAgentRunManifest")
def manifest(run_id:str,_authz=Depends(_auth)):
    store=_store_or_503(); row=store.get_run(run_id)
    if not row: raise HTTPException(404,detail={"code":"RUN_NOT_FOUND","can_execute":False})
    terminal=row["status"] in _terminal_states()
    rows_in=int(row.get("rows_in") or 0); completed=int(row.get("rows_completed") or 0); held=int(row.get("rows_held") or 0); rejected=int(row.get("rows_rejected") or 0)
    body={"run_id":str(row["run_id"]),"status":row["status"],"terminal":terminal,"stage":row["stage"],"rows_discovered":rows_in,
          "rows_terminal":completed+held+rejected,"rows_pending":max(0,rows_in-completed-held-rejected),"can_execute":False}
    if terminal:
        rows=[_manifest_row(candidate) for candidate in store.list_candidates(run_id)]
        balanced=rows_in==completed+held+rejected
        body["rows"]=rows
        body["reconciliation"]={"rows_in":rows_in,"rows_completed":completed,"rows_held":held,"rows_rejected":rejected,"balanced":balanced}
        if not balanced:
            body["manifest_status"]="RECONCILIATION_FAILED"
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
    if row["status"] in _terminal_states(): return {"run_id":run_id,"status":row["status"],"terminal":True,"can_execute":False}
    try:
        row=store.transition_run(run_id,RunStatus.CANCELED)
    except ValueError as exc:
        raise HTTPException(409,detail={"code":"ILLEGAL_RUN_TRANSITION","error":str(exc),"can_execute":False}) from exc
    return {"run_id":run_id,"status":row["status"],"terminal":True,"can_execute":False}

@router.get("/health/live",include_in_schema=False)
def agent_live():
    return {"ok":True,"service":"wow-agent-runtime","status":"alive","can_execute":False}

def _registry_ready(store)->bool:
    required={
        "wow.parallel-discovery-router","wow.slate-integrity-expert","wow.evidence-hydration","wow.controlling-model",
        "wow.failure-path-framework","wow.dynamic-calibration-expert","wow.exact-line-market-auditor",
        "wow.structure-exposure-governor","wow.final-refresh-governor","wow.terminal-ceiling-reducer",
    }
    code_ready=required==set(WORKERS) and all(WORKERS[key].contract_version=="wow.agent-output.v1" for key in required)
    return code_ready and bool(store.registry_matches(WORKERS))

@router.get("/health/ready",include_in_schema=False)
def agent_ready():
    db=False; queue=False; registry=False; store=None
    try:
        store=get_store(); db=store.get_run("00000000-0000-0000-0000-000000000000") is None
        registry=_registry_ready(store)
    except Exception:
        db=False; registry=False
    try:
        import redis
        url=os.getenv("REDIS_URL")
        if url:
            r=redis.Redis.from_url(url,socket_connect_timeout=1,socket_timeout=1); queue=bool(r.ping())
    except Exception: queue=False
    ok=db and queue and registry
    if not ok: raise HTTPException(503,detail={"ok":False,"database":db,"queue":queue,"registry":registry,"can_execute":False})
    return {"ok":True,"database":db,"queue":queue,"registry":registry,"can_execute":False}
