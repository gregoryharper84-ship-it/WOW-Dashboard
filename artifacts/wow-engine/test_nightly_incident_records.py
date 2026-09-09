from __future__ import annotations

import importlib.util
import json
import sys
from pathlib import Path

import pytest

MODULE_PATH = Path(__file__).parent / "v17" / "nightly_incident_records.py"
spec = importlib.util.spec_from_file_location("nightly_incident_records", MODULE_PATH)
assert spec and spec.loader
nir = importlib.util.module_from_spec(spec)
sys.modules[spec.name] = nir
spec.loader.exec_module(nir)


def _configure_tmp(monkeypatch, tmp_path: Path) -> None:
    root = tmp_path / "artifacts" / "wow-engine" / "v17"
    postmortems = root / "postmortems"
    fixes = root / "engineering-fixes"
    postmortems.mkdir(parents=True)
    fixes.mkdir(parents=True)
    ledger = root / "incident-ledger.json"
    ledger.write_text(
        json.dumps(
            {
                "schema_version": "1.0",
                "runtime_generation": "V17_ACTIVE",
                "description": "test",
                "id_conventions": {
                    "postmortem": "PM-YYYY-MM-DD-NNN",
                    "engineering_fix": "FIX-YYYY-MM-DD-NNN",
                },
                "terminal_authority": "V17_TERMINAL_REDUCER",
                "can_execute": False,
                "records": [],
            }
        )
    )
    monkeypatch.setattr(nir, "ROOT", root)
    monkeypatch.setattr(nir, "POSTMORTEMS", postmortems)
    monkeypatch.setattr(nir, "FIXES", fixes)
    monkeypatch.setattr(nir, "LEDGER", ledger)
    monkeypatch.setattr(nir, "_today", lambda: "2026-09-03")
    monkeypatch.setattr(nir, "_utc_now", lambda: "2026-09-03T23:00:00Z")


def _create_reproduced_incident(*, title: str, protected: bool = False) -> str:
    pm = nir.create_postmortem(
        title=title,
        severity="P1",
        domain="action-contract" if protected else "deployment runtime",
        evidence="Deterministic reproduction.",
    )
    pm_id = pm.postmortem.name.split("__", 1)[0]
    nir.triage_postmortem(
        postmortem_id=pm_id,
        reproduction_status="REPRODUCED",
        root_cause="Deterministic implementation defect.",
        root_cause_confidence="HIGH",
        acceptance_criteria=["Original reproduction passes without weakening V17 invariants."],
        risk_class="R2-restorative" if protected else "R1",
        handoff_evidence="Research/Triage reproduced the defect and defined the acceptance boundary.",
        subsystem="WOW_HOST_ORCHESTRATION" if protected else "DEPLOYMENT_RUNTIME",
        protected_contracts=["action_contract_meaning"] if protected else [],
    )
    return pm_id


def _create_fix(pm_id: str, *, protected: bool = False):
    return nir.create_fix(
        postmortem_id=pm_id,
        title="Repair deterministic defect",
        risk="R2-restorative" if protected else "R1",
        root_cause="Deterministic implementation defect.",
        allowed_files="v17/example.py",
    )


def test_postmortem_requires_triage_before_fix_and_validation(monkeypatch, tmp_path: Path) -> None:
    _configure_tmp(monkeypatch, tmp_path)

    pm = nir.create_postmortem(
        title="Action schema validation failure",
        severity="P1",
        domain="action-contract",
        evidence="Deterministic schema validation error.",
    )
    assert pm.postmortem.name.startswith("PM-2026-09-03-001__")
    assert "can_execute: false" in pm.postmortem.read_text()

    with pytest.raises(ValueError, match="ENGINEERING workflow stage"):
        nir.create_fix(
            postmortem_id="PM-2026-09-03-001",
            title="Repair Action schema contract",
            risk="R2-restorative",
            root_cause="Request schema drift.",
            allowed_files="v17/openapi.wow-betting-engine.v17.yaml",
        )

    nir.triage_postmortem(
        postmortem_id="PM-2026-09-03-001",
        reproduction_status="REPRODUCED",
        root_cause="Request schema drift.",
        root_cause_confidence="HIGH",
        acceptance_criteria=["Canonical Action schema validates and the original reproduction passes."],
        risk_class="R2-restorative",
        handoff_evidence="Research/Triage reproduced schema drift and defined the acceptance boundary.",
        subsystem="WOW_HOST_ORCHESTRATION",
        protected_contracts=["action_contract_meaning"],
    )

    fix = nir.create_fix(
        postmortem_id="PM-2026-09-03-001",
        title="Repair Action schema contract",
        risk="R2-restorative",
        root_cause="Request schema drift.",
        allowed_files="v17/openapi.wow-betting-engine.v17.yaml",
    )
    assert fix.engineering_fix is not None
    assert fix.engineering_fix.name.startswith("FIX-2026-09-03-001__")
    assert "linked_postmortem: PM-2026-09-03-001" in fix.engineering_fix.read_text()

    ledger = json.loads(nir.LEDGER.read_text())
    record = ledger["records"][0]
    assert record["engineering_fix_ids"] == ["FIX-2026-09-03-001"]
    assert record["state"] == "FIX_IN_PROGRESS"
    assert record["workflow_stage"] == "ENGINEERING"
    assert record["root_cause_status"] == "CONFIRMED"
    assert record["acceptance_criteria"]
    assert ledger["can_execute"] is False

    nir.validate()


def test_non_reproduced_incident_never_enters_engineering(monkeypatch, tmp_path: Path) -> None:
    _configure_tmp(monkeypatch, tmp_path)
    pm = nir.create_postmortem(
        title="Unconfirmed symptom",
        severity="P2",
        domain="deployment runtime",
        evidence="Reporter evidence only.",
    )
    pm_id = pm.postmortem.name.split("__", 1)[0]

    nir.triage_postmortem(
        postmortem_id=pm_id,
        reproduction_status="NOT_REPRODUCED",
        root_cause="",
        root_cause_confidence="LOW",
        acceptance_criteria=[],
        risk_class="R1",
        handoff_evidence="Research could not reproduce the symptom; return to Reporter.",
    )

    record = json.loads(nir.LEDGER.read_text())["records"][0]
    assert record["workflow_stage"] == "REPORTER_CLOSURE"
    assert record["root_cause_status"] == "NOT_CONFIRMED"
    assert record["engineering_fix_ids"] == []
    with pytest.raises(ValueError, match="ENGINEERING workflow stage"):
        _create_fix(pm_id)


def test_r3_incident_fails_closed_without_autonomous_fix(monkeypatch, tmp_path: Path) -> None:
    _configure_tmp(monkeypatch, tmp_path)
    pm = nir.create_postmortem(
        title="Credential mutation required",
        severity="P0",
        domain="credential auth",
        evidence="Repair would require secret mutation.",
    )
    pm_id = pm.postmortem.name.split("__", 1)[0]

    nir.triage_postmortem(
        postmortem_id=pm_id,
        reproduction_status="REPRODUCED",
        root_cause="Only available repair requires a prohibited secret mutation.",
        root_cause_confidence="HIGH",
        acceptance_criteria=["Do not mutate credentials automatically."],
        risk_class="R3",
        handoff_evidence="Research proved the hard safety boundary.",
        subsystem="SECURITY_CREDENTIAL",
        protected_contracts=["governance_safety"],
    )

    record = json.loads(nir.LEDGER.read_text())["records"][0]
    assert record["state"] == "BLOCKED_HARD_BOUNDARY"
    assert record["workflow_stage"] == "REPORTER_CLOSURE"
    assert record["engineering_fix_ids"] == []


def test_protected_review_requires_architect_and_direct_bypasses_fail(monkeypatch, tmp_path: Path) -> None:
    _configure_tmp(monkeypatch, tmp_path)
    pm_id = _create_reproduced_incident(title="Protected Action contract defect", protected=True)
    _create_fix(pm_id, protected=True)
    nir.handoff(
        postmortem_id=pm_id,
        to_stage="INDEPENDENT_REVIEW",
        evidence="Engineering patch and regression are ready for independent review.",
    )

    with pytest.raises(ValueError, match="use mark-review"):
        nir.handoff(postmortem_id=pm_id, to_stage="QA_VERIFICATION", evidence="attempted bypass")

    with pytest.raises(ValueError, match="System Architect PASS"):
        nir.mark_review(
            postmortem_id=pm_id,
            status="PASS",
            architect_status="NOT_APPLICABLE",
            evidence="Independent review passed, but architect was omitted.",
        )

    nir.mark_review(
        postmortem_id=pm_id,
        status="PASS",
        architect_status="PASS",
        evidence="Independent and System Architect reviews passed.",
    )
    with pytest.raises(ValueError, match="use mark-qa"):
        nir.handoff(postmortem_id=pm_id, to_stage="RELEASE_OBSERVABILITY", evidence="attempted QA bypass")

    nir.mark_qa(postmortem_id=pm_id, status="PASS", evidence="Original replay and regressions passed.")
    with pytest.raises(ValueError, match="use mark-release"):
        nir.handoff(postmortem_id=pm_id, to_stage="REPORTER_CLOSURE", evidence="attempted release bypass")


def test_fix_ids_increment_across_incidents(monkeypatch, tmp_path: Path) -> None:
    _configure_tmp(monkeypatch, tmp_path)
    first = _create_reproduced_incident(title="First defect")
    second = _create_reproduced_incident(title="Second defect")

    first_fix = _create_fix(first)
    second_fix = _create_fix(second)

    assert first_fix.engineering_fix is not None
    assert second_fix.engineering_fix is not None
    assert first_fix.engineering_fix.name.startswith("FIX-2026-09-03-001__")
    assert second_fix.engineering_fix.name.startswith("FIX-2026-09-03-002__")


def test_validate_rejects_execution_enablement(monkeypatch, tmp_path: Path) -> None:
    _configure_tmp(monkeypatch, tmp_path)
    ledger = json.loads(nir.LEDGER.read_text())
    ledger["can_execute"] = True
    nir.LEDGER.write_text(json.dumps(ledger))

    try:
        nir.validate()
    except ValueError as exc:
        assert "can_execute must be false" in str(exc)
    else:
        raise AssertionError("validate() accepted can_execute=true")
