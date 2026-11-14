"""Adversarial Reporter handoff tests; no GitHub or Supabase writes."""
from datetime import datetime, timezone, timedelta

from v17.log_incident_intake import SERVICE, classify
from v17.log_reporter_handoff import plan_reporter_handoff

MAIN_SHA = "a" * 40
NOW = datetime(2026, 10, 28, 15, tzinfo=timezone.utc)
RUN = {"repository": "gregoryharper84-ship-it/WOW-Dashboard",
       "workflow": "wow-v17-log-incident-intake.yml",
       "artifact_name": "wow-v17-log-incident-candidates",
       "branch": "main", "head_sha": MAIN_SHA,
       "status": "completed", "conclusion": "success", "run_id": 12345}


def candidate():
    return classify([{"attributes": {
        "service": SERVICE, "status": "error",
        "timestamp": NOW.isoformat(),
        "message": "WOW_V17_NFL_PICKEM_ASYNC_CLAIM_FAILED error=APIError"
    }}], begin=NOW-timedelta(hours=1), end=NOW+timedelta(minutes=1))


def plan(packet=None, *, run=None, known=None, approved=None, states=None, sha=MAIN_SHA):
    return plan_reporter_handoff(
        packet if packet is not None else candidate(),
        provenance=run if run is not None else RUN,
        protected_main_sha=sha, known_fingerprints=known or {},
        approved_issue_ids=approved or frozenset(), issue_states=states or {})


def test_unknown_signal_requires_canonical_reporter_issue_not_writer():
    result = plan()
    assert result["status"] == "REPORTER_PRECHECK"
    assert result["decisions"][0]["state"] == "REPORTER_INTAKE_REQUIRED"
    assert result["decisions"][0]["issue_number"] is None
    assert result["dispatch_authorized"] is False
    assert result["can_execute"] is False


def test_owned_active_issue_requires_review_even_if_manifest_approved():
    fingerprint = candidate()["findings"][0]["fingerprint"]
    result = plan(known={fingerprint: 1816}, approved=frozenset({1816}), states={1816:"open"})
    assert result["decisions"][0]["state"] == "EXISTING_APPROVED_ISSUE_REVIEW"
    assert result["decisions"][0]["issue_number"] == 1816
    assert result["decisions"][0]["dispatch_authorized"] is False


def test_closed_issue_recurrence_and_unadmitted_issue_are_explicit():
    fingerprint = candidate()["findings"][0]["fingerprint"]
    closed = plan(known={fingerprint:1816}, states={1816:"closed"})
    assert closed["decisions"][0]["state"] == "RECURRENCE_REOPEN_REQUIRED"
    not_admitted = plan(known={fingerprint:1816}, states={1816:"open"})
    assert not_admitted["decisions"][0]["state"] == "EXISTING_INCIDENT_NOT_ADMITTED"


def test_main_drift_or_untrusted_source_fail_closed():
    assert plan(sha="b"*40)["reason"] == "SOURCE_PROVENANCE_UNVERIFIED"
    assert plan(run={**RUN, "branch":"feature"})["reason"] == "SOURCE_PROVENANCE_UNVERIFIED"
    assert plan(run={**RUN, "run_id":True})["reason"] == "SOURCE_PROVENANCE_UNVERIFIED"
    assert plan(run={**RUN, "conclusion":"failure"})["reason"] == "SOURCE_PROVENANCE_UNVERIFIED"
    assert plan(run={**RUN, "artifact_name":"provider-route.json"})["reason"] == "SOURCE_PROVENANCE_UNVERIFIED"


def test_forged_candidate_fingerprint_or_failure_held():
    packet=candidate()
    packet["findings"][0]["fingerprint"] = "0"*64
    assert plan(packet)["reason"] == "CANDIDATE_FINGERPRINT_INVALID"
    packet=candidate()
    packet["findings"][0]["typed_failure"] = "WAGER_EXECUTION_AUTHORIZED"
    assert plan(packet)["reason"] == "CANDIDATE_INVALID"
    packet=candidate()
    packet["findings"][0]["count"] = 10000
    assert plan(packet)["reason"] == "CANDIDATE_COUNT_INVALID"


def test_packet_cannot_grant_execution_or_approval():
    packet=candidate()
    packet["can_execute"]=True
    assert plan(packet)["reason"] == "REPORTER_PACKET_INVALID"
    packet=candidate()
    packet["findings"][0]["state"]="FIXED_AND_VERIFIED"
    assert plan(packet)["reason"] == "CANDIDATE_INVALID"
    packet=candidate()
    packet["DRY_RUN_ONLY_NO_LIVE_TRADING_NO_MARKET_ORDERS"]=False
    assert plan(packet)["reason"] == "REPORTER_PACKET_INVALID"


def test_duplicate_fingerprint_and_unsafe_issue_id_rejected():
    packet=candidate()
    packet["findings"].append(dict(packet["findings"][0]))
    assert plan(packet)["reason"] == "DUPLICATED_CANDIDATE"
    fingerprint=candidate()["findings"][0]["fingerprint"]
    assert plan(known={fingerprint:True},states={})["reason"] == "INCIDENT_ID_INVALID"
    assert plan(known={fingerprint:1816},states={})["reason"] == "INCIDENT_STATE_UNVERIFIED"


def test_no_action_has_no_incident_and_never_dispatches():
    packet=classify([],begin=NOW-timedelta(hours=1),end=NOW)
    result=plan(packet)
    assert result["status"] == "NO_ACTION"
    assert result["decisions"] == []
    assert result["dispatch_authorized"] is False


def test_authenticated_worker_fence_signal_is_reporter_candidate_not_lease_release():
    from v17.log_incident_intake import WORKER_SERVICE
    worker = classify([{"attributes": {
        "service": WORKER_SERVICE, "status": "info",
        "timestamp": NOW.isoformat(),
        "message": "WOW_RESIDENT_SUPERVISOR outcome=REMOTE_WRITER_RECONCILIATION_REQUIRED"
    }} for _ in range(4)], begin=NOW-timedelta(hours=1), end=NOW+timedelta(minutes=1))
    result = plan(worker)
    assert result["status"] == "REPORTER_PRECHECK"
    assert result["decisions"][0]["state"] == "REPORTER_INTAKE_REQUIRED"
    assert result["dispatch_authorized"] is False
    worker["findings"][0]["service"] = SERVICE
    assert plan(worker)["reason"] == "WORKER_SOURCE_ROUTE_MISMATCH"
