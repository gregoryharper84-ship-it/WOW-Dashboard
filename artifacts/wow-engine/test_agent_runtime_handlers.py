from datetime import datetime, timedelta, timezone

from agent_runtime_v1.contracts import WorkerEnvelope, JobStatus
from agent_runtime_v1.runner import execute_envelope
from agent_runtime_v1.registry import worker_spec


def _env(worker_id,payload,*,candidate_id="candidate-1",evidence_snapshot_id="evidence-1"):
    spec=worker_spec(worker_id)
    return WorkerEnvelope(
        run_id="run-1",job_id=f"job-{worker_id}",candidate_id=candidate_id,
        worker_id=worker_id,worker_version=spec.worker_version,required=True,
        evidence_snapshot_id=evidence_snapshot_id,as_of=datetime.now(timezone.utc),
        input_hash="abc",payload=payload,can_execute=False,
    )


def test_discovery_worker_dedupes_source_families():
    row={"sport":"MLB","official_event_id":"g1","participant":"Pitcher A","market_family":"PLAYER_PROP","stat_family":"K","period":"FULL_GAME","exact_line":5.5,"side":"MORE"}
    a={**row,"sources":[{"url":"https://www.example.com/a"}]}
    b={**row,"sources":[{"url":"https://example.com/b"}]}
    out=execute_envelope(_env("wow.parallel-discovery-router",{"rows":[a,b]}))
    assert out.status==JobStatus.SUCCEEDED
    assert out.output["candidate_count"]==1
    assert out.output["candidates"][0]["source_families"]==["example.com"]


def test_identity_worker_requires_exact_prop_identity():
    candidate={"sport":"MLB","official_event_id":"g1","participant":"Pitcher A","market_family":"PLAYER_PROP","period":"FULL_GAME"}
    out=execute_envelope(_env("wow.slate-integrity-expert",{"candidate":candidate}))
    assert out.status==JobStatus.BLOCKED
    assert "SLATE_IDENTITY_INCOMPLETE" in out.blockers


def test_evidence_worker_keeps_game_and_box_logs_separate():
    now=datetime.now(timezone.utc).isoformat()
    evidence={"candidate_identity":{},"official_event":{},"exact_market_identity":{},"game_log":[4,5,6],"box_score_log":[{"event_id":"1","minutes":31}],"role_status":"CONFIRMED","role_timestamp":now,"source_attempts":[{"source":"official","status":"PASS"}],"source_conflicts":[]}
    out=execute_envelope(_env("wow.evidence-hydration",{"evidence":evidence}))
    assert out.status==JobStatus.SUCCEEDED
    assert out.ceiling=="EVIDENCE_VERIFIED"


def test_controlling_model_does_not_accept_envelope_probability_substitute():
    payload={"capability":{"status":"AVAILABLE","artifact_id":"artifact-1","calibrator_id":"cal-1"},"probability":0.99}
    out=execute_envelope(_env("wow.controlling-model",payload))
    assert out.status==JobStatus.BLOCKED
    assert "CONTROLLING_MODEL_PROVIDER_NOT_WIRED" in out.blockers
    assert out.output["probability_publishable"] is False


def test_failure_path_worker_builds_unconditional_distribution():
    out=execute_envelope(_env("wow.failure-path-framework",{"components":[{"weight":0.8,"pmf":{"5":1.0}},{"weight":0.2,"pmf":{"0":1.0}}]}))
    assert out.status==JobStatus.SUCCEEDED
    assert out.output["unconditional_pmf"]=={0:0.2,5:0.8}


def test_market_worker_preserves_model_market_objective_separation():
    out=execute_envelope(_env("wow.exact-line-market-auditor",{"exact_identity_match":False,"settlement_match":True,"two_way_no_vig_resolved":True,"price_fresh":True}))
    assert out.status==JobStatus.BLOCKED
    assert out.output["blocks_model_probability"] is False
    assert "MARKET_IDENTITY_MISMATCH" in out.blockers


def test_final_refresh_rejects_started_event():
    now=datetime.now(timezone.utc)
    out=execute_envelope(_env("wow.final-refresh-governor",{"now":now.isoformat(),"event_start":(now-timedelta(minutes=1)).isoformat(),"event_status":"STARTED","market_fresh":True,"critical_status_fresh":True}))
    assert out.status==JobStatus.REJECTED
    assert "EVENT_NOT_PREGAME" in out.blockers


def test_terminal_reducer_cannot_upgrade_model_blocker():
    jobs=[
        {"worker_id":"wow.controlling-model","status":"BLOCKED","ceiling":"RESEARCH_INTEREST","blockers":["MODEL_UNAVAILABLE"]},
        {"worker_id":"wow.exact-line-market-auditor","status":"SUCCEEDED","ceiling":"MARKET_VERIFIED_HOLD","blockers":[]},
    ]
    out=execute_envelope(_env("wow.terminal-ceiling-reducer",{"controlling_worker_id":"wow.controlling-model","required_jobs":jobs}))
    assert out.status==JobStatus.SUCCEEDED
    assert out.output["terminal_label"]=="MODEL_UNAVAILABLE"
    assert out.output["probability_publishable"] is False
    assert "MODEL_UNAVAILABLE" in out.output["blockers"]
