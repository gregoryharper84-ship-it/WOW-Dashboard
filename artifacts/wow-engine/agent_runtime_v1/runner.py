from __future__ import annotations
from datetime import datetime, timezone
from .contracts import WorkerEnvelope, WorkerOutput, JobStatus, canonical_hash
from .registry import worker_spec
from .queue import celery_app
from .discovery import merge_discovery, canonical_candidate_key
from .evidence import seal_evidence
from .gates import mix_failure_regimes, validate_calibrated, final_refresh
from .reducer import reduce_candidate
from .model_bridge import score_mlb_event_bridge, HELD_CODE, PUBLISHED_CODE

TRANSIENT_CODES={"TRANSPORT_429","TRANSPORT_5XX","DATABASE_TEMPORARY","QUEUE_TEMPORARY"}

def _terminal_output(env:WorkerEnvelope,status:JobStatus,ceiling:str,blockers:list[str],output:dict):
    now=datetime.now(timezone.utc)
    payload={"status":status.value,"ceiling":ceiling,"blockers":blockers,"output":output}
    return WorkerOutput(run_id=env.run_id,job_id=env.job_id,candidate_id=env.candidate_id,worker_id=env.worker_id,
        worker_version=env.worker_version,status=status,ceiling=ceiling,blockers=blockers,evidence_snapshot_id=env.evidence_snapshot_id,
        output=output,output_hash=canonical_hash(payload),started_at=now,completed_at=now,can_execute=False)

def _blocked(env:WorkerEnvelope,*blockers:str,output:dict|None=None):
    return _terminal_output(env,JobStatus.BLOCKED,"RESEARCH_INTEREST",list(blockers),output or {})

def _succeeded(env:WorkerEnvelope,ceiling:str,output:dict,blockers:list[str]|None=None):
    return _terminal_output(env,JobStatus.SUCCEEDED,ceiling,blockers or [],output)

def _aware(value)->datetime:
    if isinstance(value,datetime):
        dt=value
    else:
        dt=datetime.fromisoformat(str(value).replace("Z","+00:00"))
    if dt.tzinfo is None:
        raise ValueError("AWARE_TIMESTAMP_REQUIRED")
    return dt

def _run_discovery(env:WorkerEnvelope):
    rows=env.payload.get("rows")
    if not isinstance(rows,list):
        return _blocked(env,"DISCOVERY_INPUT_MISSING")
    merged=merge_discovery(rows)
    return _succeeded(env,"RESEARCH_INTEREST",{"candidates":merged,"candidate_count":len(merged),"source_family_dedupe":True})

def _run_identity(env:WorkerEnvelope):
    row=env.payload.get("candidate")
    if not isinstance(row,dict):
        return _blocked(env,"CANDIDATE_IDENTITY_MISSING")
    required=["sport","official_event_id","participant","market_family","period"]
    if str(row.get("market_family") or "").upper()=="PLAYER_PROP":
        required += ["stat_family","exact_line","side"]
    missing=[key for key in required if row.get(key) is None or str(row.get(key)).strip()==""]
    if missing:
        return _blocked(env,"SLATE_IDENTITY_INCOMPLETE",output={"missing_fields":missing})
    return _succeeded(env,"IDENTITY_VERIFIED",{"canonical_key":canonical_candidate_key(row),"candidate":row})

def _run_evidence(env:WorkerEnvelope):
    payload=env.payload.get("evidence")
    snapshot_id=env.evidence_snapshot_id or env.payload.get("evidence_snapshot_id")
    if not snapshot_id or not isinstance(payload,dict):
        return _blocked(env,"EVIDENCE_SNAPSHOT_MISSING")
    sealed=seal_evidence(str(snapshot_id),payload)
    blockers=[]
    if sealed.missing_required_fields:
        blockers.append("EVIDENCE_REQUIRED_FIELDS_MISSING")
    if sealed.source_conflicts:
        blockers.append("EVIDENCE_SOURCE_CONFLICT")
    output={"payload_hash":sealed.payload_hash,"missing_required_fields":list(sealed.missing_required_fields),"source_conflicts":list(sealed.source_conflicts),"evidence_snapshot_id":sealed.evidence_snapshot_id}
    if blockers:
        return _blocked(env,*blockers,output=output)
    return _succeeded(env,"EVIDENCE_VERIFIED",output)

def _run_controlling_model(env:WorkerEnvelope):
    cap=env.payload.get("capability")
    if not isinstance(cap,dict) or cap.get("status")!="AVAILABLE" or not cap.get("artifact_id") or not cap.get("calibrator_id"):
        return _blocked(env,"MODEL_UNAVAILABLE",output={"probability_publishable":False})

    sport=str(env.payload.get("sport") or "").upper()
    market_family=str(env.payload.get("market_family") or "").upper()
    period=str(env.payload.get("period") or "").upper()
    if sport=="MLB" and market_family=="OUTRIGHT_WINNER" and period in {"FULL_GAME","FULL_GAME_INCLUDING_EXTRA_INNINGS"}:
        try:
            bridged=score_mlb_event_bridge(env.payload)
        except Exception as exc:
            code=getattr(exc,"code",None) or "EVENT_MODEL_BRIDGE_UNAVAILABLE"
            return _blocked(env,str(code),output={"probability_publishable":False,"error":type(exc).__name__})
        code=bridged.get("code")
        if code==HELD_CODE:
            return _succeeded(env,"MODEL_QUALIFIED_HOLD",bridged,["PROBABILITY_PUBLICATION_HELD"])
        if code==PUBLISHED_CODE and bridged.get("probability_publishable") is True:
            return _succeeded(env,"MODEL_QUALIFIED_HOLD",bridged)
        blockers=list(bridged.get("bridge_blockers") or ["MODEL_UNAVAILABLE"])
        return _blocked(env,*blockers,output=bridged)

    # No qualitative, market, L5/L10, or caller-provided probability fallback.
    return _blocked(env,"CONTROLLING_MODEL_PROVIDER_NOT_WIRED",output={"probability_publishable":False,"artifact_id":cap.get("artifact_id"),"calibrator_id":cap.get("calibrator_id")})

def _run_failure_paths(env:WorkerEnvelope):
    raw=env.payload.get("components")
    if not isinstance(raw,list) or not raw:
        return _blocked(env,"FAILURE_PATH_COMPONENTS_MISSING")
    try:
        components=[]
        for item in raw:
            if not isinstance(item,dict):
                raise ValueError("FAILURE_PATH_COMPONENT_INVALID")
            components.append((float(item["weight"]),{int(k):float(v) for k,v in dict(item["pmf"]).items()}))
        pmf=mix_failure_regimes(components)
    except Exception as exc:
        return _blocked(env,getattr(exc,"args",["FAILURE_PATH_INVALID"])[0] or "FAILURE_PATH_INVALID")
    return _succeeded(env,"MODEL_QUALIFIED_HOLD",{"unconditional_pmf":pmf,"failure_path_applied":True})

def _run_calibration(env:WorkerEnvelope):
    try:
        calibrator_id=env.payload.get("calibrator_id")
        point=env.payload.get("point")
        lower=env.payload.get("lower")
        upper=env.payload.get("upper")
        validate_calibrated(point,lower,upper,calibrator_id)
    except Exception as exc:
        return _blocked(env,getattr(exc,"args",["CALIBRATION_INVALID"])[0] or "CALIBRATION_INVALID")
    return _succeeded(env,"MODEL_QUALIFIED_HOLD",{"calibrator_id":calibrator_id,"calibrated_probability":float(point),"calibrated_probability_lower_bound":float(lower),"calibrated_probability_upper_bound":float(upper),"probability_publishable":True})

def _run_market(env:WorkerEnvelope):
    checks={
        "MARKET_IDENTITY_MISMATCH":env.payload.get("exact_identity_match") is True,
        "SETTLEMENT_IDENTITY_MISMATCH":env.payload.get("settlement_match") is True,
        "TWO_WAY_NO_VIG_UNRESOLVED":env.payload.get("two_way_no_vig_resolved") is True,
        "MARKET_PRICE_STALE":env.payload.get("price_fresh") is True,
    }
    blockers=[code for code,ok in checks.items() if not ok]
    if blockers:
        return _blocked(env,*blockers,output={"market_gate":"HOLD","blocks_model_probability":False})
    return _succeeded(env,"MARKET_VERIFIED_HOLD",{"market_gate":"PASS","blocks_model_probability":False})

def _run_structure(env:WorkerEnvelope):
    checks={
        "DEPENDENCY_CHECK_INCOMPLETE":env.payload.get("dependency_checked") is True,
        "CORRELATION_CHECK_INCOMPLETE":env.payload.get("correlation_checked") is True,
        "DIRECTIONAL_EXPOSURE_CHECK_INCOMPLETE":env.payload.get("directional_exposure_checked") is True,
        "DUPLICATE_THESIS_CHECK_INCOMPLETE":env.payload.get("duplicate_thesis_checked") is True,
        "PORTFOLIO_CHECK_INCOMPLETE":env.payload.get("portfolio_checked") is True,
    }
    blockers=[code for code,ok in checks.items() if not ok]
    if blockers:
        return _blocked(env,*blockers)
    return _succeeded(env,"STRUCTURE_VERIFIED_HOLD",{"structure_gate":"PASS","capital_allocation":False})

def _run_final_refresh(env:WorkerEnvelope):
    try:
        status,blockers=final_refresh(
            now=_aware(env.payload.get("now") or datetime.now(timezone.utc)),
            event_start=_aware(env.payload["event_start"]),
            event_status=str(env.payload.get("event_status") or "UNKNOWN"),
            market_fresh=env.payload.get("market_fresh") is True,
            critical_status_fresh=env.payload.get("critical_status_fresh") is True,
        )
    except Exception as exc:
        return _blocked(env,"FINAL_REFRESH_INPUT_INVALID",output={"error":type(exc).__name__})
    if status!="PASS":
        return _terminal_output(env,JobStatus.REJECTED,"RESEARCH_INTEREST",blockers,{"final_refresh":"REJECT"})
    return _succeeded(env,"FINAL_REFRESH_HOLD",{"final_refresh":"PASS"})

def _run_reducer(env:WorkerEnvelope):
    jobs=env.payload.get("required_jobs")
    if not isinstance(jobs,list):
        return _blocked(env,"REDUCER_INPUT_MISSING")
    try:
        decision=reduce_candidate(controlling_worker_id=env.payload.get("controlling_worker_id"),required_jobs=jobs)
    except Exception as exc:
        return _blocked(env,getattr(exc,"args",["REDUCER_FAILED"])[0] or "REDUCER_FAILED")
    return _succeeded(env,decision.final_terminal_ceiling,{"terminal_label":decision.terminal_label,"final_terminal_ceiling":decision.final_terminal_ceiling,"probability_publishable":decision.probability_publishable,"blockers":list(decision.blockers)},list(decision.blockers))

def execute_envelope(env:WorkerEnvelope)->WorkerOutput:
    spec=worker_spec(env.worker_id)
    if env.worker_version!=spec.worker_version:
        return _blocked(env,"WORKER_VERSION_MISMATCH")
    handler=env.payload.get("_test_handler")
    if handler=="SUCCEED":
        return _succeeded(env,spec.authority_ceiling,{"ok":True})
    if env.worker_id=="wow.parallel-discovery-router": return _run_discovery(env)
    if env.worker_id=="wow.slate-integrity-expert": return _run_identity(env)
    if env.worker_id=="wow.evidence-hydration": return _run_evidence(env)
    if env.worker_id=="wow.controlling-model": return _run_controlling_model(env)
    if env.worker_id=="wow.failure-path-framework": return _run_failure_paths(env)
    if env.worker_id=="wow.dynamic-calibration-expert": return _run_calibration(env)
    if env.worker_id=="wow.exact-line-market-auditor": return _run_market(env)
    if env.worker_id=="wow.structure-exposure-governor": return _run_structure(env)
    if env.worker_id=="wow.final-refresh-governor": return _run_final_refresh(env)
    if env.worker_id=="wow.terminal-ceiling-reducer": return _run_reducer(env)
    return _blocked(env,"WORKER_HANDLER_NOT_WIRED")

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
