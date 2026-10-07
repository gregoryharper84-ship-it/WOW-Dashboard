from datetime import datetime, timedelta, timezone

import pytest

from v17.sirt_assurance import assess_sentinel, assess_class_a_closure, failure_families, CLOSURE_EVIDENCE

NOW = datetime(2026, 10, 7, 22, 0, tzinfo=timezone.utc)


def runtime(**kw):
    row = dict(
        auditor_id="WOW_ENGINEERING_AUDITOR",
        status="RUNNING",
        last_heartbeat_at=(NOW - timedelta(seconds=20)).isoformat(),
        terminal_authority="V17_TERMINAL_REDUCER",
        can_execute=False,
    )
    row.update(kw)
    return row


def closure(**kw):
    row = {key: "evidence-id" for key in CLOSURE_EVIDENCE}
    row.update(dict(candidate_head_sha="head", certified_head_sha="head",
                    current_base_sha="base", certified_base_sha="base",
                    merge_commit_sha="merge", deployed_commit_sha="merge",
                    independent_verifier="INDEPENDENT_VERIFICATION",
                    implementation_owner="ENGINEERING",
                    verification_owner="INDEPENDENT_VERIFICATION",
                    production_acceptance_status="PASS",
                    independent_verification_status="PASS",
                    terminal_authority="V17_TERMINAL_REDUCER",
                    can_execute=False))
    row.update(kw)
    return row


def test_healthy_heartbeat_is_not_product_readiness():
    result = assess_sentinel(runtime=runtime(), now=NOW)
    assert result["status"] == "OBSERVED_HEALTHY"
    assert "NOT_PRODUCT_READINESS" in result["scope"]


@pytest.mark.parametrize("bad,code", [
    ({"status": "STOPPED"}, "AUDITOR_NOT_RUNNING"),
    ({"can_execute": True}, "AUTHORITY_UNVERIFIED"),
    ({"terminal_authority": "UNKNOWN"}, "AUTHORITY_UNVERIFIED"),
    ({"last_heartbeat_at": "invalid"}, "HEARTBEAT_UNVERIFIABLE"),
    ({"last_heartbeat_at": (NOW - timedelta(hours=1)).isoformat()}, "HEARTBEAT_STALE"),
])
def test_bad_heartbeat_is_blocked(bad, code):
    result = assess_sentinel(runtime=runtime(**bad), now=NOW)
    assert result["status"] == "BLOCKED"
    assert code in {s["reason"] for s in result["signals"]}


def test_missing_runtime_is_blocked():
    assert assess_sentinel(runtime=None, now=NOW)["status"] == "BLOCKED"


def test_stale_work_handoff_and_probes_are_detected():
    result = assess_sentinel(
        runtime=runtime(), now=NOW,
        work_items=[{"state": "OPEN", "severity": "P0", "fingerprint": "ticket1",
                     "next_audit_at": (NOW - timedelta(minutes=1)).isoformat()}],
        handoffs=[{"work_item_id": "ticket2", "from_owner": "TRIAGE",
                   "to_owner": "ENGINEERING", "status": "PENDING",
                   "created_at": (NOW - timedelta(hours=1)).isoformat()}],
        probes=[{"probe_id": "LLP", "status": "PASS", "observed_at": NOW.isoformat()}],
    )
    assert {x["reason"] for x in result["signals"]} == {
        "WORK_OVERDUE", "HANDOFF_UNACKNOWLEDGED", "PROBE_UNVERIFIABLE"
    }


def test_complete_receipt_is_only_eligible_for_independent_review():
    result = assess_class_a_closure(closure())
    assert result["closure_eligible_for_independent_review"]
    assert result["status"] == "EVIDENCE_COMPLETE_PENDING_TERMINAL_AUTHORITY"


@pytest.mark.parametrize("bad,code", [
    ({"certified_head_sha": "old"}, "CERTIFICATION_HEAD_MISMATCH"),
    ({"certified_base_sha": "old"}, "CERTIFICATION_BASE_STALE"),
    ({"deployed_commit_sha": "unknown"}, "DEPLOYED_ANCESTRY_UNVERIFIED"),
    ({"independent_verifier": "ENGINEERING"}, "INDEPENDENT_VERIFIER_MISSING"),
    ({"verification_owner": "ENGINEERING"}, "VERIFICATION_INDEPENDENCE_UNPROVEN"),
    ({"production_acceptance_status": "FAIL"}, "PRODUCTION_ACCEPTANCE_NOT_PASS"),
    ({"independent_verification_status": "PENDING"}, "INDEPENDENT_VERIFICATION_NOT_PASS"),
    ({"tests_evidence_ref": ""}, "TESTS_EVIDENCE_REF_MISSING"),
    ({"can_execute": True}, "GOVERNANCE_INVARIANT_UNVERIFIED"),
])
def test_closure_must_fail_closed(bad, code):
    result = assess_class_a_closure(closure(**bad))
    assert result["status"] == "CLOSURE_BLOCKED"
    assert code in result["blockers"]


def test_recurrence_requires_confirmed_shared_root_cause():
    records = [
        {"postmortem_id": "PM-1", "root_cause_status": "CONFIRMED",
         "confirmed_root_cause_id": "MEMORY_OVERLAP", "primary_subsystem": "SCOUT"},
        {"postmortem_id": "PM-2", "root_cause_status": "CONFIRMED",
         "confirmed_root_cause_id": "MEMORY_OVERLAP", "primary_subsystem": "LLP"},
        {"postmortem_id": "PM-3", "root_cause_status": "SUSPECTED",
         "confirmed_root_cause_id": "MEMORY_OVERLAP", "primary_subsystem": "PROPS"},
    ]
    result = failure_families(records)
    assert len(result["families"]) == 1
    assert result["families"][0]["occurrences"] == 2
    assert result["families"][0]["opportunity"] == "PREVENTIVE_ARCHITECTURE_REVIEW"
    assert result["unclassified_incident_ids"] == ["PM-3"]


def test_live_critical_audit_findings_are_not_false_green():
    result = assess_sentinel(runtime=runtime(), now=NOW, findings=[
        {"finding_type": "STALE_WORK", "status": "OPEN", "severity": "P0",
         "component": "llp-slate"},
        {"finding_type": "STALE_WORK", "status": "RESOLVED", "severity": "P0",
         "component": "old-resolved"},
    ])
    assert result["status"] == "BLOCKED"
    assert len(result["signals"]) == 1
    assert result["signals"][0]["reason"] == "CRITICAL_FINDING_UNRESOLVED"


def test_symptom_cohorts_are_explicitly_not_root_cause():
    result = failure_families([], [
        {"finding_type": "STALE_WORK", "severity": "P0", "status": "OPEN"},
        {"finding_type": "STALE_WORK", "severity": "P0", "status": "OPEN"},
        {"finding_type": "STALE_WORK", "severity": "P1", "status": "RESOLVED"},
    ])
    assert result["families"] == []
    assert result["open_symptom_cohorts"] == [{
        "finding_type": "STALE_WORK",
        "severity": "P0",
        "open_count": 2,
        "classification": "SYMPTOM_CLUSTER_ROOT_CAUSE_UNPROVEN",
    }]


def test_independent_watchdog_is_separate_from_resident_auditor():
    from pathlib import Path

    workflow = (Path(__file__).resolve().parents[2]
                / ".github/workflows/wow-sirt-independent-reliability-sentinel.yml").read_text()
    assert "schedule:" in workflow
    assert 'cron: "7,22,37,52 * * * *"' in workflow
    assert "sirt_assurance.py sentinel" in workflow
    assert "secrets.SUPABASE_SERVICE_ROLE_KEY" in workflow
    assert "if: always()" in workflow
    assert "actions/upload-artifact@v4" in workflow
    assert "can_execute=true" not in workflow
