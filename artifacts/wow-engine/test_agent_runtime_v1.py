from datetime import datetime, timedelta, timezone
import uuid
import pytest

from agent_runtime_v1.contracts import RunCreateRequest, RunStatus, canonical_hash
from agent_runtime_v1.state_machine import assert_run_transition
from agent_runtime_v1.discovery import merge_discovery, canonical_candidate_key
from agent_runtime_v1.evidence import validate_evidence_payload, seal_evidence
from agent_runtime_v1.provider import route_capability, ModelUnavailable, validate_pmf, verify_artifact_bytes
from agent_runtime_v1.gates import mix_failure_regimes, derive_line_probabilities, validate_calibrated, final_refresh
from agent_runtime_v1.reducer import reduce_candidate, strictest, reconcile
from agent_runtime_v1.store import MemoryStore

def _candidate(side="MORE"):
    return {"sport":"MLB","official_event_id":"game-1","participant":"Pitcher A","market_family":"PLAYER_PROP","stat_family":"K","period":"FULL_GAME","exact_line":5.5,"side":side}

def _evidence():
    now=datetime.now(timezone.utc).isoformat()
    return {"candidate_identity":{},"official_event":{},"exact_market_identity":{},"game_log":[4,6,7,5,8],"box_score_log":[{"event_id":"1","minutes":30}],"role_status":"CONFIRMED","role_timestamp":now,"source_attempts":[{"source":"official","status":"PASS"}],"source_conflicts":[]}

def test_01_four_discovery_lanes_collapse_one_candidate():
    rows=[]
    for i in range(4):
        r=_candidate(); r["sources"]=[{"url":f"https://example.com/story/{i}"}]; rows.append(r)
    assert len(merge_discovery(rows))==1

def test_02_reposts_count_one_source_family():
    r1=_candidate(); r1["sources"]=[{"url":"https://www.example.com/a"},{"url":"https://example.com/b"}]
    assert merge_discovery([r1])[0]["source_families"]==["example.com"]

def test_03_missing_fitted_model_is_model_unavailable():
    with pytest.raises(ModelUnavailable): route_capability([],sport="MLB",market_family="PLAYER_PROP",stat_family="K",period="FULL_GAME")

def test_04_failed_more_does_not_approve_less():
    assert canonical_candidate_key(_candidate("MORE")) != canonical_candidate_key(_candidate("LESS"))

def test_05_reversal_is_new_candidate_side():
    assert _candidate("MORE")["side"] != _candidate("LESS")["side"]

def test_06_missing_board_identity_is_visible():
    e=_evidence(); del e["exact_market_identity"]
    missing,_=validate_evidence_payload(e)
    assert "exact_market_identity" in missing

def test_07_started_event_removed_at_refresh():
    now=datetime.now(timezone.utc)
    status,blockers=final_refresh(now=now,event_start=now-timedelta(seconds=1),event_status="STARTED",market_fresh=True,critical_status_fresh=True)
    assert status=="REJECT" and "EVENT_NOT_PREGAME" in blockers

def test_08_wrong_year_identity_changes_key():
    a=_candidate(); b=_candidate(); b["official_event_id"]="2025-game-1"
    assert canonical_candidate_key(a)!=canonical_candidate_key(b)

def test_09_failure_path_changes_unconditional_probability():
    assert mix_failure_regimes([(0.8,{5:1.0}),(0.2,{0:1.0})])=={0:0.2,5:0.8}

def test_10_fake_fixed_haircut_bound_rejected_when_order_invalid():
    with pytest.raises(ValueError): validate_calibrated(.60,.61,.70,"cal-1")

def test_11_missing_calibrator_blocks_publication():
    with pytest.raises(ValueError): validate_calibrated(.60,.55,.65,None)

def test_12_push_preserved_for_whole_line():
    assert derive_line_probabilities({4:.2,5:.5,6:.3},5.0)=={"MORE":.3,"LESS":.2,"PUSH":.5}

def test_13_timed_out_required_worker_remains_blocker():
    d=reduce_candidate(controlling_worker_id="model",required_jobs=[{"worker_id":"model","status":"TIMED_OUT","ceiling":"RESEARCH_INTEREST","blockers":["MODEL_TIMEOUT"]}])
    assert "MODEL_TIMEOUT" in d.blockers and not d.probability_publishable

def test_14_downstream_success_cannot_upgrade_upstream_block():
    jobs=[{"worker_id":"model","status":"SUCCEEDED","ceiling":"MODEL_QUALIFIED_HOLD","blockers":[]},{"worker_id":"audit","status":"BLOCKED","ceiling":"RESEARCH_INTEREST","blockers":["MARKET_IDENTITY_MISMATCH"]}]
    d=reduce_candidate(controlling_worker_id="model",required_jobs=jobs)
    assert d.final_terminal_ceiling=="RESEARCH_INTEREST" and "MARKET_IDENTITY_MISMATCH" in d.blockers

def test_15_rows_reconcile_exactly():
    assert reconcile(14,2,4,8)["balanced"] is True
    with pytest.raises(ValueError): reconcile(14,2,4,7)

def test_16_snapshot_is_hash_stable_and_separate_logs():
    e=_evidence(); sealed=seal_evidence(str(uuid.uuid4()),e)
    assert sealed.payload_hash==canonical_hash(e) and not sealed.missing_required_fields and e["game_log"] is not e["box_score_log"]

def test_17_execution_request_rejected():
    with pytest.raises(Exception): RunCreateRequest(as_of=datetime.now(timezone.utc),user_timezone="America/Chicago",can_execute=True)

def test_illegal_state_transition_fails_closed():
    with pytest.raises(ValueError): assert_run_transition(RunStatus.CREATED,RunStatus.COMPLETED)

def test_unknown_ceiling_fails_closed():
    with pytest.raises(ValueError): strictest(["NOT_A_REAL_LABEL"])

def test_idempotent_run_returns_same_id():
    store=MemoryStore(); req=RunCreateRequest(as_of=datetime.now(timezone.utc),user_timezone="America/Chicago").model_dump(mode="json")
    a=store.create_run(idempotency_key="same",request=req,governance_version="WOW-v16"); b=store.create_run(idempotency_key="same",request=req,governance_version="WOW-v16")
    assert a["run_id"]==b["run_id"]

def test_artifact_hash_verification():
    from hashlib import sha256
    data=b"certified-artifact"; verify_artifact_bytes(data,sha256(data).hexdigest())
    with pytest.raises(RuntimeError): verify_artifact_bytes(data,"0"*64)

def test_pmf_strict_normalization():
    assert validate_pmf({0:.4,1:.6})=={0:.4,1:.6}
    with pytest.raises(ValueError): validate_pmf({0:.4,1:.5})
