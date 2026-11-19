"""Class C evidence bundle is a read-only review preflight, never certification."""
from datetime import datetime, timezone

from v17.class_c_evidence_preflight import REQUIRED_EVIDENCE, build_class_c_evidence_preflight

NOW = datetime(2026, 11, 19, 8, 0, tzinfo=timezone.utc).isoformat()


def candidate():
    return {
        "sport": "NFL", "market_family": "MONEYLINE", "model_family": "NFL_FITTED_V1",
        "model_version": "v17.1", "feature_schema_version": "features-v1",
        "source_contract_version": "sources-v1", "controlling_specialist": "NFL_WINNER_SPECIALIST",
        "artifact_sha256": "a" * 64, "calibrator_sha256": "b" * 64,
        "dataset_sha256": "c" * 64, "policy_sha256": "d" * 64,
        "training_code_sha": "e" * 40, "head_sha": "f" * 40,
        "lifecycle_state": "CANDIDATE", "active": False, "promoted": False,
        "probability_publishable": False, "can_execute": False,
        "automatic_promotion": False, "automatic_certification": False,
    }


def receipts():
    return {category: {
        "receipt_id": category + ":1", "sha256": "0" * 64,
        "subject_artifact_sha256": "a" * 64,
        "head_sha": "f" * 40, "observed_at": NOW,
        "status": "PASS", "independent_verification_status": "PASS",
    } for category in REQUIRED_EVIDENCE}


def test_structural_ready_never_certifies_or_authorizes():
    result = build_class_c_evidence_preflight(candidate(), receipts())
    assert result["status"] == "STRUCTURALLY_READY_FOR_INDEPENDENT_REPLAY"
    assert len(result["bundle_sha256"]) == 64
    assert result["independent_verification_status"] == "REQUIRED"
    assert result["certification_status"] == "NOT_CERTIFIED"
    assert result["model_release_authorized"] is False
    assert result["probability_publishable"] is False
    assert result["can_execute"] is False
    assert all("independent_verification_status" not in v for v in result["manifest"]["receipts"].values())


def test_bundle_hash_is_input_order_independent():
    c, e = candidate(), receipts()
    same = build_class_c_evidence_preflight(dict(reversed(list(c.items()))), dict(reversed(list(e.items()))))
    assert same["bundle_sha256"] == build_class_c_evidence_preflight(c, e)["bundle_sha256"]


def test_calibrator_change_forces_new_bundle_hash():
    c, e = candidate(), receipts()
    old = build_class_c_evidence_preflight(c, e)["bundle_sha256"]
    c["calibrator_sha256"] = "1" * 64
    assert build_class_c_evidence_preflight(c, e)["bundle_sha256"] != old


def test_missing_forward_hold():
    e = receipts()
    del e["prospective_forward"]
    result = build_class_c_evidence_preflight(candidate(), e)
    assert result["status"] == "HOLD"
    assert "CLASS_C_EVIDENCE_MISSING:prospective_forward" in result["blockers"]


def test_substantive_hold_or_reject_cannot_pass():
    for status in ("HOLD", "REJECT"):
        e = receipts()
        e["holdout"]["status"] = status
        assert build_class_c_evidence_preflight(candidate(), e)["status"] == "HOLD"


def test_stale_or_foreign_receipts_hold():
    e = receipts()
    e["calibration"]["subject_artifact_sha256"] = "2" * 64
    e["lower_bound"]["head_sha"] = "3" * 40
    result = build_class_c_evidence_preflight(candidate(), e)
    assert "CLASS_C_RECEIPT_ARTIFACT_MISMATCH:calibration" in result["blockers"]
    assert "CLASS_C_RECEIPT_HEAD_MISMATCH:lower_bound" in result["blockers"]


def test_inertness_and_unrecognized_evidence_rejected():
    c, e = candidate(), receipts()
    c["promoted"] = True
    e["reviewer_approval"] = {"status": "PASS"}
    result = build_class_c_evidence_preflight(c, e)
    assert "CLASS_C_INERTNESS_REQUIRED:promoted" in result["blockers"]
    assert "CLASS_C_EVIDENCE_CATEGORY_UNEXPECTED" in result["blockers"]


def test_invalid_timestamp_never_passes():
    for observed in ("2026-11-19T08:00:00", "3026-11-19T08:00:00Z", "invalid"):
        e = receipts()
        e["source_rights"]["observed_at"] = observed
        result = build_class_c_evidence_preflight(candidate(), e)
        assert "CLASS_C_RECEIPT_TIME_INVALID:source_rights" in result["blockers"]


def test_missing_or_malformed_candidate_holds():
    assert build_class_c_evidence_preflight(None, receipts())["status"] == "HOLD"
    c = candidate()
    c["artifact_sha256"] = "not-a-hash"
    result = build_class_c_evidence_preflight(c, receipts())
    assert "CLASS_C_HASH_INVALID:artifact_sha256" in result["blockers"]


def test_forged_review_fields_never_gain_authority():
    e = receipts()
    e["calibration"]["reviewer_signed_approval"] = "forged"
    result = build_class_c_evidence_preflight(candidate(), e)
    assert result["status"] == "STRUCTURALLY_READY_FOR_INDEPENDENT_REPLAY"
    assert "reviewer_signed_approval" not in str(result["manifest"])
    assert result["automatic_certification"] is False
