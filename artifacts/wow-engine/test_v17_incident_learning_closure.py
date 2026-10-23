"""Regression tests: learning is mandatory at incident closure, never at release."""
from __future__ import annotations

from copy import deepcopy

import pytest

from v17 import nightly_incident_records as records
from v17.nightly_engineering_team_contract import initial_team_fields, transition_record, validate_team_record
from v17.sirt_assurance import failure_families


def _learning() -> dict:
    return {
        "root_cause_code": "RELEASE_STATE_DRIFT",
        "failure_category": "DEPLOYMENT_RUNTIME",
        "why_existing_tests_missed_it": "The prior test checked configuration but not armed PR state.",
        "regression_test_added": "artifacts/wow-engine/test_v17_incident_learning_closure.py::test_learning_is_recorded",
        "regression_test_evidence_ref": "https://github.com/example/example/actions/runs/123",
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
    row["release"] = {"status": "PRODUCTION_VERIFIED", "production_verified": True}
    return transition_record(row, to_stage="REPORTER_CLOSURE", evidence="verified release")


def _close(monkeypatch, row, receipt=None):
    saved = []
    monkeypatch.setattr(records, "_load_ledger", lambda: {"records": [deepcopy(row)]})
    monkeypatch.setattr(records, "_save_ledger", lambda ledger: saved.append(deepcopy(ledger)))
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
