from datetime import datetime, timezone

from agent_runtime_v1.contracts import JobStatus, RunCreateRequest, WorkerEnvelope
from agent_runtime_v1.model_bridge import HELD_CODE, score_mlb_event_bridge
from agent_runtime_v1.orchestrator import Orchestrator
from agent_runtime_v1.registry import worker_spec
from agent_runtime_v1.runner import execute_envelope
from agent_runtime_v1.store import MemoryStore


class FakeJobs:
    def __init__(self):
        self.jobs={}; self.calls=0
    def create_job(self,*,run_id,candidate_id,worker_id,worker_version,required,input_hash):
        key=(run_id,candidate_id,worker_id,worker_version,input_hash)
        if key in self.jobs:return self.jobs[key],False
        self.calls+=1
        row={"job_id":f"job-{self.calls}","run_id":run_id,"candidate_id":candidate_id,"worker_id":worker_id,"worker_version":worker_version,"status":"QUEUED","attempt":0,"required":required,"input_hash":input_hash}
        self.jobs[key]=row; return row,True


def _env(worker_id,payload):
    spec=worker_spec(worker_id)
    return WorkerEnvelope(run_id="run-1",job_id=f"job-{worker_id}",candidate_id=None,worker_id=worker_id,worker_version=spec.worker_version,required=True,evidence_snapshot_id=None,as_of=datetime.now(timezone.utc),input_hash="hash",payload=payload,can_execute=False)


def test_start_run_transitions_and_queues_once(monkeypatch):
    sent=[]
    monkeypatch.setattr("agent_runtime_v1.orchestrator.execute_durable.apply_async",lambda *args,**kwargs: sent.append((args,kwargs)))
    store=MemoryStore(); jobs=FakeJobs(); orch=Orchestrator(jobs)
    request=RunCreateRequest(as_of=datetime.now(timezone.utc),user_timezone="America/Chicago",discovery_enabled=False,candidate_inputs=[]).model_dump(mode="json")
    row=store.create_run(idempotency_key="start-once",request=request,governance_version="WOW-v16")
    first=orch.start_run(store=store,run=row,request=request)
    second=orch.start_run(store=store,run=first["run"],request=request)
    assert first["started"] is True
    assert first["run"]["status"]=="DISCOVERY_QUEUED"
    assert second["started"] is False
    assert jobs.calls==1 and len(sent)==1


def test_enabled_discovery_without_provider_does_not_become_zero_candidate_success():
    out=execute_envelope(_env("wow.parallel-discovery-router",{"rows":[],"discovery_enabled":True}))
    assert out.status==JobStatus.BLOCKED
    assert "DISCOVERY_PROVIDER_UNAVAILABLE" in out.blockers


def test_disabled_discovery_with_empty_explicit_board_can_reconcile_zero_rows():
    out=execute_envelope(_env("wow.parallel-discovery-router",{"rows":[],"discovery_enabled":False}))
    assert out.status==JobStatus.SUCCEEDED
    assert out.output["candidate_count"]==0


def test_mlb_held_bridge_proves_model_without_numeric_leak():
    request={
        "research_run_id":"test-run","requested_slate_date":"2026-08-29","requested_timezone":"America/Chicago","scan_stage":"PREGAME",
        "event_key":"MLB:1","official_event_id":"1","event_start_time_utc":"2026-08-30T00:00:00+00:00","sport":"MLB","league":"MLB",
        "market_family":"OUTRIGHT_WINNER","settlement_basis":"FULL_GAME_INCLUDING_EXTRA_INNINGS","home_team":"Home","away_team":"Away","venue":"Park",
        "home_starting_pitcher":"Pitcher H","away_starting_pitcher":"Pitcher A","home_starter_status":"PROBABLE","away_starter_status":"PROBABLE",
        "home_lineup_status":"PROJECTED","away_lineup_status":"PROJECTED","source_snapshot_id":"00000000-0000-0000-0000-000000000001",
    }
    held={"status":"MODEL_SCORED_HELD","code":HELD_CODE,"scoring_evidence_produced":True,"probability_fields_withheld":True,"probability_publishable":False,"can_execute":False,"model_version":"v2d","spec_id":"spec-1"}
    result=score_mlb_event_bridge({"event_request":request},bridge_fn=lambda req:held)
    assert result["code"]==HELD_CODE
    assert result["probability_publishable"] is False
    assert result["probability_fields_withheld"] is True
    assert not any("probability" in key and key not in {"probability_publishable","probability_fields_withheld"} for key in result)


def test_runner_accepts_governed_mlb_held_path_without_available_publication_capability(monkeypatch):
    monkeypatch.setattr("agent_runtime_v1.runner.score_mlb_event_bridge",lambda payload:{"code":HELD_CODE,"probability_publishable":False,"probability_fields_withheld":True,"can_execute":False})
    out=execute_envelope(_env("wow.controlling-model",{"sport":"MLB","market_family":"OUTRIGHT_WINNER","period":"FULL_GAME","event_request":{}}))
    assert out.status==JobStatus.SUCCEEDED
    assert out.ceiling=="MODEL_QUALIFIED_HOLD"
    assert "PROBABILITY_PUBLICATION_HELD" in out.blockers
    assert out.output["probability_publishable"] is False
