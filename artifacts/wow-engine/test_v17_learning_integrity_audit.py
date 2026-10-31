"""Read-only two-sided learning: identity, diagnostics, and no implicit actuation."""
from types import SimpleNamespace

import pytest

from v17.learning_integrity_audit import (
    audit_engineering_incidents,
    compile_engineering_refinements,
    margin_class_diagnostics,
    signal_polarity_diagnostics,
)


def signal(name, bucket, ident, y, p=0.65):
    return SimpleNamespace(
        source_prediction_kind="PROP",
        sport="MLB",
        market_family="PITCHER_STRIKEOUTS",
        stat_type="K",
        specialist_id="MLB_K_FITTED",
        model_family="MLB_K_FITTED",
        signal_namespace="PREGAME_FEATURE",
        signal_name=name,
        signal_bucket=bucket,
        source_prediction_id=ident,
        outcome_target=y,
        model_probability=p,
    )


def test_two_sided_signal_prevalence_from_frozen_pregame_rows():
    rows = [
        signal("WORKLOAD", "HIGH", "p1", 1),
        signal("WORKLOAD", "HIGH", "p2", 0),
        signal("WORKLOAD", "LOW", "p3", 1),
        signal("WORKLOAD", "LOW", "p4", 1),
    ]
    scores = signal_polarity_diagnostics(rows, min_samples=2)
    high = next(s for s in scores if s["signal_bucket"] == "HIGH")
    assert high["wins"] == 1 and high["losses"] == 1
    assert high["p_bucket_given_win"] == pytest.approx(1 / 3)
    assert high["p_bucket_given_loss"] == 1
    assert high["review_ready"] is True
    assert high["serving_cohort_eligible"] is False
    assert high["can_execute"] is False


def test_duplicates_are_not_extra_wins_and_conflicting_buckets_rejected():
    x = signal("TRAVEL", "HIGH", "p1", 1)
    scores = signal_polarity_diagnostics([x, x])
    assert scores[0]["n"] == 1
    with pytest.raises(ValueError, match="SIGNAL_BUCKET_IDENTITY_CONFLICT"):
        signal_polarity_diagnostics([x, signal("TRAVEL", "LOW", "p1", 1)])


def test_nonbinary_outcomes_are_excluded_from_polarity_diagnostics():
    scores = signal_polarity_diagnostics([signal("WORKLOAD", "HIGH", "p1", None)])
    assert scores == []


def test_margin_slices_are_postsettlement_only():
    summary = margin_class_diagnostics([
        {"margin_class": "NEAR_BOUNDARY", "settled_result": "WIN"},
        {"margin_class": "COMFORTABLE_CLEAR", "settled_result": "WIN"},
        {"margin_class": "LARGE_MISS", "settled_result": "LOSS"},
    ])
    assert summary["strata"]["NEAR_BOUNDARY"]["wins"] == 1
    assert summary["strata"]["LARGE_MISS"]["losses"] == 1
    assert summary["causal_attribution"] is False
    assert "NOT_CALIBRATION_COHORT" in summary["interpretation"]


def incident(n, *, cls="CI_PATH_DRIFT", verified=False, scoped=False):
    fix = {
        "state": "VERIFIED_CLOSED" if verified else "OPEN",
        "merge_commit": f"sha-{n}",
        "production_deploy": None,
    }
    if scoped:
        fix["change_scope"] = "DOCS_ONLY"
        fix["qa_status"] = "PASS"
    return {
        "postmortem_id": f"PM-2026-10-31-{n:03d}",
        "domain": "deployment / CI",
        "root_cause_status": "CONFIRMED",
        "learning": {"failure_category": cls} if cls else {},
        "state": "VERIFIED_CLOSED" if verified else "WORKING",
        "engineering_fixes": {f"FIX-{n}": fix},
    }


def test_incident_recurrence_counts_exact_classes_only_and_escalates_third_repeat():
    records = [incident(i) for i in range(1, 5)] + [incident(5, cls=None)]
    audit = audit_engineering_incidents({"records": records})
    group = audit["root_cause_groups"][0]
    assert audit["incident_n"] == 5
    assert audit["explicitly_classified_n"] == 4
    assert group["repeat_n"] == 3
    assert group["recommended_escalation"] == "ESCALATE_P0_REVIEW"
    domain = audit["same_root_cause_recurrence_rate_by_domain"]["DEPLOYMENT / CI"]
    assert domain["recurrence_rate"] == pytest.approx(3 / 4)
    assert domain["unclassified"] == 1


def test_legacy_close_missing_deploy_and_independent_receipt_is_a_gap_not_mutation():
    src = incident(1, verified=True)
    audit = audit_engineering_incidents({"records": [src]})
    gap = audit["verified_closure_evidence_gaps"][0]
    assert "DEPLOYMENT_ID_MISSING" in gap["missing"]
    assert "INDEPENDENT_PRODUCTION_VERIFICATION_MISSING" in gap["missing"]
    assert "PRODUCTION_RECEIPT_MISSING" in gap["missing"]
    assert src["state"] == "VERIFIED_CLOSED"
    assert audit["legacy_missing_provenance_is_not_new_proof_of_failure"] is True


def test_nonproduction_change_uses_qa_instead_of_fake_deployment():
    src = incident(1, verified=True, scoped=True)
    src["qa"] = None
    audit = audit_engineering_incidents({"records": [src]})
    assert audit["closure_evidence_gap_n"] == 0


def test_free_text_refine_items_remain_blocked_not_autonomously_queued():
    item = {
        "domain": "CI",
        "root_cause_key": "PATH_DRIFT",
        "proposed_change": "Pin exact path",
        "evidence_refs": ["PM-2026-10-31-001"],
        "guard_test_path": "tests/test_path_drift.py",
        "preserve": ["existing green paths"],
    }
    proposals = compile_engineering_refinements(
        ["narrow loss", {"proposal": "make all stricter"}, item],
        postmortem_id="PM-2026-10-31-001",
    )
    assert proposals["input_n"] == 3
    assert len(proposals["blocked"]) == 2
    assert len(proposals["proposals"]) == 1
    proposal = proposals["proposals"][0]
    assert "GUARD_FAILS_PRE_FIX" in proposal["recipe"]["required_validation"]
    assert proposal["proposal_namespace"] == "ENGINEERING"
    assert proposal["automatic_promotion"] is False
    assert proposals["automatic_queue_admission"] is False


def test_unstructured_or_mutated_incident_source_fails_closed():
    with pytest.raises(ValueError, match="INCIDENT_LEDGER_DUPLICATE_OR_MISSING_ID"):
        audit_engineering_incidents({"records": [incident(1), incident(1)]})
    with pytest.raises(ValueError, match="INCIDENT_LEDGER_SCHEMA_INVALID"):
        audit_engineering_incidents({"records": "not a list"})
