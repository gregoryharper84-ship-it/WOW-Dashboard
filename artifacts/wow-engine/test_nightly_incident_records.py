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
