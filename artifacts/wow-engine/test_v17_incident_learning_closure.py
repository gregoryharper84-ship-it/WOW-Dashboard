"""Regression tests: learning is mandatory at incident closure, never at release."""
from __future__ import annotations

from copy import deepcopy

import pytest

from v17 import nightly_incident_records as records
from v17 import incident_regression_evidence as regression
from v17.nightly_engineering_team_contract import initial_team_fields, transition_record, validate_team_record
from v17.sirt_assurance import failure_families


def _learning() -> dict:
    return {
        "root_cause_code": "RELEASE_STATE_DRIFT",
        "failure_category": "DEPLOYMENT_RUNTIME",
        "why_existing_tests_missed_it": "The prior test checked configuration but not armed PR state.",
        "regression_test_added": "artifacts/wow-engine/test_v17_incident_learning_closure.py::test_learning_is_recorded",
        "regression_test_evidence_ref": "https://github.com/gregoryharper84-ship-it/WOW-Dashboard/actions/runs/123/job/456",
        "regression_test_sha": "a" * 40,
        "architecture_lesson": "Read back effective state after changing global configuration.",
        "preventive_control": "Check per-PR inventory after any auto-merge policy change.",
        "similar_code_paths_reviewed": ["auto-merge state", "release-dispatch state"],
        "recurrence_of": [],
        "recurrence_monitor_owner": "SIRT",
    }


def _ready_for_reporter(severity="P0", opted_in=True) -> dict:
    row = initial_team_fields(domain="deployment runtime", reported_by="nightly_scan")
    row.update({
        "postmortem_id": "PM-2026-10-23-999",
        "severity": severity,
        "state": "DEPLOYED_PENDING_VERIFY",
        "engineering_fix_ids": ["FIX-2026-10-23-999"],
        "engineering_fixes": {"FIX-2026-10-23-999": {"state": "DEPLOYED_PENDING_VERIFY"}},
        "root_cause_status": "CONFIRMED",
        "reproduction_status": "REPRODUCED",
        "acceptance_criteria": ["old failure no longer reproduces"],
    })
    if opted_in:
        row["learning_contract_version"] = "1"
    for stage in ("RESEARCH_TRIAGE", "ENGINEERING", "INDEPENDENT_REVIEW"):
        row = transition_record(row, to_stage=stage, evidence="independent evidence")
    row["review"] = {"status": "PASS", "system_architect_status": "NOT_APPLICABLE"}
    row = transition_record(row, to_stage="QA_VERIFICATION", evidence="review passed")
    row["qa"] = {"status": "PASS"}
    row = transition_record(row, to_stage="RELEASE_OBSERVABILITY", evidence="QA passed")
    row["release"] = {"status": "PRODUCTION_VERIFIED", "production_verified": True,
                      "merge_commit": "a" * 40, "deployed_commit": "a" * 40, "deployment_id": "dep-test"}
    return transition_record(row, to_stage="REPORTER_CLOSURE", evidence="verified release")


def _close(monkeypatch, row, receipt=None):
    saved = []
    monkeypatch.setattr(records, "_load_ledger", lambda: {"records": [deepcopy(row)]})
    monkeypatch.setattr(records, "_save_ledger", lambda ledger: saved.append(deepcopy(ledger)))
    monkeypatch.setattr(records, "verify_authenticated_regression",
                        lambda receipt, *, expected_sha: {"source": "GITHUB_ACTIONS_AUTHENTICATED_JOB_LOG",
                                                           "tested_sha": expected_sha, "result": "PASS"})
    records.close_verified(
        postmortem_id=row["postmortem_id"],
        evidence="verified user-facing acceptance",
        learning_receipt=receipt,
    )
    return saved


def test_p0_missing_learning_does_not_persist_false_closed(monkeypatch):
    row = _ready_for_reporter()
    saved = []
    monkeypatch.setattr(records, "_load_ledger", lambda: {"records": [row]})
    monkeypatch.setattr(records, "_save_ledger", lambda ledger: saved.append(ledger))
    with pytest.raises(ValueError, match="learning receipt"):
        records.close_verified(postmortem_id=row["postmortem_id"], evidence="acceptance")
    assert saved == []


def test_learning_is_recorded(monkeypatch):
    row = _ready_for_reporter(severity="P1")
    saved = _close(monkeypatch, row, _learning())
    assert len(saved) == 1
    closed = saved[0]["records"][0]
    assert closed["state"] == "VERIFIED_CLOSED"
    assert closed["confirmed_root_cause_id"] == "RELEASE_STATE_DRIFT"
    assert closed["learning"]["regression_test_evidence_ref"]
    assert validate_team_record(closed) == []


@pytest.mark.parametrize("bad", [
    {"why_existing_tests_missed_it": ""},
    {"regression_test_added": ""},
    {"regression_test_evidence_ref": ""},
    {"preventive_control": ""},
    {"recurrence_monitor_owner": ""},
    {"root_cause_code": "untyped free-form value"},
    {"similar_code_paths_reviewed": []},
    {"recurrence_of": None},
])
def test_incomplete_or_invalid_receipt_rejected(bad):
    row = _ready_for_reporter()
    row["learning"] = {**_learning(), **bad}
    row["reporter_closure"] = {"status": "FIXED_VERIFIED"}
    row["state"] = "VERIFIED_CLOSED"
    assert any("learning receipt" in error for error in validate_team_record(row))


def test_open_incident_and_release_path_not_gated_on_learning():
    row = _ready_for_reporter()
    assert validate_team_record(row) == []


@pytest.mark.parametrize("severity,opted_in", [("P2", True), ("P1", False)])
def test_p2_and_legacy_incidents_are_not_retroactively_blocked(monkeypatch, severity, opted_in):
    row = _ready_for_reporter(severity=severity, opted_in=opted_in)
    saved = _close(monkeypatch, row)
    assert saved[0]["records"][0]["state"] == "VERIFIED_CLOSED"


def test_sirt_clusters_confirmed_root_causes_from_learning_receipts(monkeypatch):
    row = _ready_for_reporter()
    saved = _close(monkeypatch, row, _learning())
    current = saved[0]["records"][0]
    prior = deepcopy(current)
    prior["postmortem_id"] = "PM-2026-10-22-111"
    families = failure_families([prior, current])["families"]
    assert len(families) == 1
    assert families[0]["recurring"] is True
    assert families[0]["occurrences"] == 2
    assert families[0]["opportunity"] == "PREVENTIVE_ARCHITECTURE_REVIEW"


def test_new_incident_severity_must_be_canonical(tmp_path, monkeypatch):
    ledger = {"records": [], "schema_version": "2.0"}
    monkeypatch.setattr(records, "_load_ledger", lambda: ledger)
    monkeypatch.setattr(records, "_save_ledger", lambda data: None)
    monkeypatch.setattr(records, "POSTMORTEMS", tmp_path)
    monkeypatch.setattr(records, "_relative", lambda path: str(path))
    created = records.create_postmortem(
        title="Learning severity canonicalization",
        severity=" p1 ", domain="deployment runtime", evidence="reproduced",
    )
    assert created.postmortem.exists()
    assert ledger["records"][0]["severity"] == "P1"
    assert ledger["records"][0]["learning_contract_version"] == "1"


@pytest.mark.parametrize("severity", ["", "P9", "HIGH", "P1_INVALID"])
def test_new_incident_rejects_unrecognized_severity_before_ledger_write(severity):
    with pytest.raises(ValueError, match="invalid incident severity"):
        records.create_postmortem(
            title="Invalid severity", severity=severity,
            domain="deployment runtime", evidence="reproduced",
        )


@pytest.mark.parametrize("severity", ["P1 ", "p0", "P9", None])
def test_mutated_severity_cannot_escape_learning_contract(severity):
    row = _ready_for_reporter()
    row["severity"] = severity
    assert "invalid new-incident severity for learning contract" in validate_team_record(row)



def test_direct_verified_closed_creation_is_blocked_before_file_or_ledger_write(tmp_path, monkeypatch):
    ledger = {"records": [], "schema_version": "2.0"}
    saved = []
    monkeypatch.setattr(records, "POSTMORTEMS", tmp_path)
    monkeypatch.setattr(records, "_load_ledger", lambda: ledger)
    monkeypatch.setattr(records, "_save_ledger", lambda data: saved.append(data))
    for severity in ("P0", "P1", " p1 "):
        with pytest.raises(ValueError, match="cannot be created VERIFIED_CLOSED"):
            records.create_postmortem(
                title="Must traverse reporter closure", severity=severity,
                domain="deployment runtime", evidence="reproduced",
                state="VERIFIED_CLOSED",
            )
    assert not list(tmp_path.iterdir())
    assert not ledger["records"] and not saved


@pytest.mark.parametrize("severity", ["P2", "P3"])
def test_lower_severity_creation_preserves_existing_behavior(tmp_path, monkeypatch, severity):
    ledger = {"records": [], "schema_version": "2.0"}
    monkeypatch.setattr(records, "POSTMORTEMS", tmp_path)
    monkeypatch.setattr(records, "_load_ledger", lambda: ledger)
    monkeypatch.setattr(records, "_save_ledger", lambda data: None)
    monkeypatch.setattr(records, "_relative", lambda path: str(path))
    result = records.create_postmortem(
        title="Lower-severity legacy creation", severity=severity,
        domain="deployment runtime", evidence="evidence",
        state="VERIFIED_CLOSED",
    )
    assert result.postmortem.exists()
    assert ledger["records"][0]["state"] == "VERIFIED_CLOSED"


@pytest.mark.parametrize("patch", [
    {"regression_test_added": "not_a_test"},
    {"regression_test_evidence_ref": "NOT_RUN"},
    {"regression_test_evidence_ref": "https://evil.example/actions/runs/123/job/456"},
    {"regression_test_sha": "bad_sha"},
])
def test_closure_rejects_untrusted_evidence_fields(patch):
    row = _ready_for_reporter()
    row["state"] = "VERIFIED_CLOSED"
    row["reporter_closure"] = {"status": "FIXED_VERIFIED"}
    row["learning"] = {**_learning(), **patch}
    assert any("regression" in error for error in validate_team_record(row))


@pytest.mark.parametrize("mutate,expected", [
    ({"conclusion": "failure"}, "failed, mismatched"),
    ({"status": "in_progress"}, "failed, mismatched"),
    ({"head_sha": "b" * 40}, "failed, mismatched"),
    ({"run_id": 999}, "failed, mismatched"),
    ({"workflow_name": "unknown-workflow"}, "failed, mismatched"),
])
def test_authenticated_job_rejects_failure_sha_run_or_untrusted_producer(monkeypatch, mutate, expected):
    monkeypatch.setenv("GH_TOKEN", "dummy-test-token")
    job = {"id": 456, "run_id": 123, "head_sha": "a" * 40,
           "status": "completed", "conclusion": "success", "workflow_name": "wow-engine-verify"}
    job.update(mutate)
    monkeypatch.setattr(regression, "_job_json", lambda job_id, token: job)
    monkeypatch.setattr(regression, "_job_log", lambda job_id, token: (
        "artifacts/wow-engine/test_v17_incident_learning_closure.py::test_learning_is_recorded PASSED\n"
    ))
    with pytest.raises(ValueError, match=expected):
        regression.verify_authenticated_regression(_learning(), expected_sha="a" * 40)


def test_authenticated_job_requires_exact_named_test_pass_in_log(monkeypatch):
    monkeypatch.setenv("GH_TOKEN", "dummy-test-token")
    monkeypatch.setattr(regression, "_job_json", lambda job_id, token: {
        "id": 456, "run_id": 123, "head_sha": "a" * 40,
        "status": "completed", "conclusion": "success", "workflow_name": "wow-engine-verify",
    })
    monkeypatch.setattr(regression, "_job_log", lambda job_id, token: (
        "artifacts/wow-engine/test_v17_incident_learning_closure.py::test_learning_is_recorded FAILED\n"
    ))
    with pytest.raises(ValueError, match="no authenticated PASS log"):
        regression.verify_authenticated_regression(_learning(), expected_sha="a" * 40)


def test_authenticated_regression_proof_binds_job_test_and_exact_release_sha(monkeypatch):
    monkeypatch.setenv("GH_TOKEN", "dummy-test-token")
    monkeypatch.setattr(regression, "_job_json", lambda job_id, token: {
        "id": 456, "run_id": 123, "head_sha": "a" * 40,
        "status": "completed", "conclusion": "success", "workflow_name": "wow-engine-verify",
    })
    monkeypatch.setattr(regression, "_job_log", lambda job_id, token: (
        "artifacts/wow-engine/test_v17_incident_learning_closure.py::test_learning_is_recorded PASSED [100%]\n"
    ))
    proof = regression.verify_authenticated_regression(_learning(), expected_sha="a" * 40)
    assert proof["result"] == "PASS"
    assert proof["tested_sha"] == "a" * 40
    assert proof["job_id"] == 456


def test_timestamped_github_job_log_pass_is_accepted(monkeypatch):
    monkeypatch.setenv("GH_TOKEN", "dummy-test-token")
    monkeypatch.setattr(regression, "_job_json", lambda job_id, token: {
        "id": 456, "run_id": 123, "head_sha": "a" * 40,
        "status": "completed", "conclusion": "success", "workflow_name": "wow-verify",
    })
    monkeypatch.setattr(regression, "_job_log", lambda job_id, token: (
        "2026-10-23T20:01:02.123Z "
        "artifacts/wow-engine/test_v17_incident_learning_closure.py::test_learning_is_recorded PASSED [100%]\n"
    ))
    assert regression.verify_authenticated_regression(
        _learning(), expected_sha="a" * 40
    )["result"] == "PASS"


def test_closure_rejects_missing_authenticated_job_proof_and_does_not_save(monkeypatch):
    row = _ready_for_reporter()
    monkeypatch.setattr(records, "_load_ledger", lambda: {"records": [row]})
    saved = []
    monkeypatch.setattr(records, "_save_ledger", lambda value: saved.append(value))
    def deny(*args, **kwargs):
        raise ValueError("regression evidence GitHub verification unavailable")
    monkeypatch.setattr(records, "verify_authenticated_regression", deny)
    with pytest.raises(ValueError, match="GitHub verification unavailable"):
        records.close_verified(postmortem_id=row["postmortem_id"],
                               evidence="production accepted", learning_receipt=_learning())
    assert not saved


def test_authenticated_regression_sha_rejects_other_release(monkeypatch):
    monkeypatch.setenv("GH_TOKEN", "dummy-test-token")
    with pytest.raises(ValueError, match="does not match verified release"):
        regression.verify_authenticated_regression(_learning(), expected_sha="b" * 40)


def test_missing_github_credential_fails_closed(monkeypatch):
    monkeypatch.delenv("GH_TOKEN", raising=False)
    monkeypatch.delenv("GITHUB_TOKEN", raising=False)
    with pytest.raises(ValueError, match="credential unavailable"):
        regression.verify_authenticated_regression(_learning(), expected_sha="a" * 40)
