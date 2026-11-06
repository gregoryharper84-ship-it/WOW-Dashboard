"""Nonproduction acceptance fixtures for #2219. Does not authorize merges/deploys."""
from __future__ import annotations

import copy
from datetime import datetime, timedelta, timezone
from pathlib import Path
import sys

sys.path.insert(0, str(Path(__file__).resolve().parent))
from v17.factory_closed_loop_acceptance import assess_incident, assess_batch

HEAD = "a" * 40
MERGE = "b" * 40
INCIDENT = "WOW-FACTORY-CANARY-1"


def receipts():
    result = []
    start = datetime(2026, 10, 28, 15, 0, tzinfo=timezone.utc)

    def add(event_type, **kwargs):
        i = len(result)
        result.append(dict(
            incident_id=INCIDENT, event_type=event_type,
            observed_at=(start + timedelta(seconds=i * 10)).isoformat(),
            receipt_id=f"receipt-{i}", authenticated=True,
            can_execute=False, **kwargs,
        ))

    add("SUPERVISOR_CYCLE", trigger="SCHEDULED", unattended=True,
        advanced_work=True, cycle_id="cycle-10", cycle_sequence=10)
    add("DETECTED", fingerprint="stable:scorer:type", canonical_incident_id=INCIDENT)
    add("DETECTED", fingerprint="stable:scorer:type", canonical_incident_id=INCIDENT)
    add("TRIAGED", severity="P0", issue_id="2219")
    add("LEASE_ACQUIRED", worker_id="worker-a", epoch=8)
    add("PATCH_PRODUCED", worker_id="worker-a", epoch=8,
        head_sha=HEAD, write_fence_confirmed=True, source_author="builder")
    add("LEASE_RELEASED", worker_id="worker-a", epoch=8, cas_verified=True,
        remote_terminal_verified=True, no_inflight_writes=True)
    add("TESTS_PASSED", head_sha=HEAD, passed=True, test_count=23)
    add("INDEPENDENT_REVIEW_PASSED", head_sha=HEAD, reviewer_principal="reviewer-b",
        source_author="builder", semantic_review=True)
    add("QA_PASSED", app_id=5245832, head_sha=HEAD, conclusion="success")
    add("RELEASE_PASSED", app_id=5245853, head_sha=HEAD, conclusion="success")
    add("MERGED", merged=True, protected_branch="main", source_head_sha=HEAD,
        merge_sha=MERGE)
    add("STAGING_VERIFIED", merge_sha=MERGE, web_sha=MERGE, worker_sha=MERGE,
        web_status="LIVE", worker_status="LIVE", smoke_passed=True)
    add("SUPERVISOR_CYCLE", trigger="EVENT_DRIVEN", unattended=True,
        advanced_work=True, cycle_id="cycle-11", cycle_sequence=11)
    add("PRODUCTION_VERIFIED", merge_sha=MERGE, web_sha=MERGE, worker_sha=MERGE,
        web_status="LIVE", worker_status="LIVE", smoke_passed=True)
    add("NEGATIVE_CANARY_PASSED", merge_sha=MERGE, stale_writer_rejected=True,
        false_green_rejected=True)
    return result


def assert_blocked(data, code, change_class="B"):
    r = assess_incident(data, class_name=change_class, source_attested=True)
    assert r["status"] == "UNVERIFIED"
    assert code in r["reason_codes"]
    assert r["can_execute"] is False


def find(data, typ):
    return next(x for x in data if x["event_type"] == typ)


def test_complete_native_loop_accepted_with_deduplicated_multi_source_incidents():
    r = assess_incident(receipts(), source_attested=True)
    assert r["status"] == "VERIFIED_CLOSED_LOOP"
    assert r["deduplicated_detection_count"] == 1
    assert r["distinct_unattended_progress_cycles"] == 2
    assert r["terminal_authority"] == "V17_TERMINAL_REDUCER"


def test_ci_green_but_no_deployment_is_not_closure():
    data = [r for r in receipts() if r["event_type"] != "PRODUCTION_VERIFIED"]
    assert_blocked(data, "MISSING_PRODUCTION_VERIFIED")


def test_owner_self_review_does_not_count():
    data = receipts()
    find(data, "INDEPENDENT_REVIEW_PASSED")["reviewer_principal"] = "builder"
    assert_blocked(data, "INDEPENDENT_REVIEW_EVIDENCE_MISSING")


def test_class_a_not_forced_into_semantic_reviewer_but_keeps_app_checks():
    data = [r for r in receipts() if r["event_type"] != "INDEPENDENT_REVIEW_PASSED"]
    assert assess_incident(data, class_name="A", source_attested=True)["status"] == "VERIFIED_CLOSED_LOOP"


def test_qa_spoof_app_id_rejected():
    data = receipts()
    find(data, "QA_PASSED")["app_id"] = 0
    assert_blocked(data, "QA_PASSED_EXACT_HEAD_PROVENANCE_INVALID")


def test_release_stale_head_rejected():
    data = receipts()
    find(data, "RELEASE_PASSED")["head_sha"] = "c" * 40
    assert_blocked(data, "RELEASE_PASSED_EXACT_HEAD_PROVENANCE_INVALID")


def test_unfenced_provider_patch_rejected():
    data = receipts()
    find(data, "PATCH_PRODUCED")["write_fence_confirmed"] = False
    assert_blocked(data, "PATCH_WRITER_NOT_FENCED")


def test_wrong_epoch_rejected():
    data = receipts()
    find(data, "PATCH_PRODUCED")["epoch"] = 9
    assert_blocked(data, "PATCH_WRITER_NOT_FENCED")


def test_unverified_remote_terminal_does_not_release_writer():
    data = receipts()
    find(data, "LEASE_RELEASED")["remote_terminal_verified"] = False
    assert_blocked(data, "LEASE_RELEASE_NOT_SAFE")


def test_ambiguous_inflight_write_prevents_release():
    data = receipts()
    find(data, "LEASE_RELEASED")["no_inflight_writes"] = False
    assert_blocked(data, "LEASE_RELEASE_NOT_SAFE")


def test_merge_can_have_different_sha_but_both_services_must_match_merge():
    data = receipts()
    find(data, "PRODUCTION_VERIFIED")["worker_sha"] = HEAD
    assert_blocked(data, "PRODUCTION_VERIFIED_SHA_PARITY_OR_HEALTH_FAILED")


def test_negative_stale_writer_canary_is_mandatory():
    data = receipts()
    find(data, "NEGATIVE_CANARY_PASSED")["stale_writer_rejected"] = False
    assert_blocked(data, "NEGATIVE_CANARY_INVALID")


def test_duplicate_cycle_receipts_are_not_two_unattended_cycles():
    data = receipts()
    cycles = [x for x in data if x["event_type"] == "SUPERVISOR_CYCLE"]
    cycles[1]["cycle_id"] = cycles[0]["cycle_id"]
    cycles[1]["cycle_sequence"] = cycles[0]["cycle_sequence"]
    assert_blocked(data, "TWO_UNATTENDED_PROGRESS_CYCLES_MISSING")


def test_nonconsecutive_cycles_do_not_prove_unattended_continuity():
    data = receipts()
    cycles = [x for x in data if x["event_type"] == "SUPERVISOR_CYCLE"]
    cycles[1]["cycle_sequence"] = cycles[0]["cycle_sequence"] + 2
    assert_blocked(data, "TWO_UNATTENDED_PROGRESS_CYCLES_MISSING")


def test_missing_authenticity_not_auto_corrected():
    data = receipts()
    find(data, "MERGED")["authenticated"] = False
    assert_blocked(data, "UNAUTHENTICATED_RECEIPT")


def test_missing_receipt_is_failure_not_probability_or_model_unavailable():
    data = [x for x in receipts() if x["event_type"] != "PATCH_PRODUCED"]
    assert_blocked(data, "MISSING_PATCH_PRODUCED")


def test_started_event_has_no_betting_execution_authority():
    data = receipts()
    find(data, "TRIAGED")["can_execute"] = True
    assert_blocked(data, "EXECUTION_SAFETY_INVARIANT_FAILED")


def test_no_unknown_incident_id_mixing():
    data = receipts()
    find(data, "QA_PASSED")["incident_id"] = "OTHER-INCIDENT"
    assert_blocked(data, "INCIDENT_IDENTITY_MISMATCH")


def test_no_stage_reordering_to_manufacture_closure():
    data = receipts()
    find(data, "MERGED")["observed_at"] = find(data, "TRIAGED")["observed_at"]
    assert_blocked(data, "RECEIPT_CHRONOLOGY_UNVERIFIED")


def test_batch_reconciles_pass_and_hold_without_hiding_hold():
    good = receipts()
    bad = copy.deepcopy(good)
    bad = [x for x in bad if x["event_type"] != "NEGATIVE_CANARY_PASSED"]
    result = assess_batch({"incidents": [{"class": "B", "receipts": good},
                                         {"class": "B", "receipts": bad}]}, source_attested=True)
    assert result["summary"] == {"UNVERIFIED": 1, "VERIFIED_CLOSED_LOOP": 1}
    assert result["can_execute"] is False


def test_untrusted_json_never_self_attests_to_verified_production():
    data = receipts()
    result = assess_incident(data)
    assert result["status"] == "UNVERIFIED"
    assert "EXTERNAL_SOURCE_ATTESTATION_REQUIRED" in result["reason_codes"]


def test_batch_can_not_be_self_attested_by_untrusted_json():
    result = assess_batch({"source_attested": True, "incidents": [{"class": "B", "receipts": receipts()}]})
    assert result["summary"] == {"UNVERIFIED": 1}