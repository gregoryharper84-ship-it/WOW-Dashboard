from __future__ import annotations

import json

import pytest

from v17 import nightly_incident_records as records


def test_checked_in_incident_ledger_paths_validate_and_preserve_v17_invariants() -> None:
    records.validate()

    ledger = json.loads(records.LEDGER.read_text())
    assert ledger["runtime_generation"] == "V17_ACTIVE"
    assert ledger["terminal_authority"] == "V17_TERMINAL_REDUCER"
    assert ledger["can_execute"] is False

    for incident in ledger["records"]:
        pm_path = records.ROOT.parent.parent.parent / incident["postmortem_path"]
        assert pm_path.exists(), incident["postmortem_path"]
        for fix_id in incident.get("engineering_fix_ids", []):
            fix_path = records.ROOT.parent.parent.parent / incident["engineering_fixes"][fix_id]["path"]
            assert fix_path.exists(), incident["engineering_fixes"][fix_id]["path"]


def test_merged_pending_deploy_is_valid_but_not_verified_close() -> None:
    from v17.engineering_agent_team import ACTIVE_RELEASE_STATES, is_actionable

    assert "MERGED_PENDING_DEPLOY" in records.ALLOWED_STATES
    assert "MERGED_PENDING_DEPLOY" in ACTIVE_RELEASE_STATES
    ledger = json.loads(records.LEDGER.read_text(encoding="utf-8"))
    item = next(r for r in ledger["records"] if r["postmortem_id"] == "PM-2026-11-10-001")
    assert item["state"] == "MERGED_PENDING_DEPLOY"
    assert item["engineering_fixes"]["FIX-2026-11-10-001"]["state"] == "MERGED_PENDING_DEPLOY"
    assert item["engineering_fixes"]["FIX-2026-11-10-001"]["merge_commit"] == (
        "6018482f2bedf172b4a7a1014f2bfa1c2db95210"
    )
    assert is_actionable(item)
    assert item.get("reporter_closure", {}).get("status") != "FIXED_VERIFIED"
    assert ledger["can_execute"] is False


def _ledger_with_release_note(note: str) -> dict:
    ledger = json.loads(records.LEDGER.read_text(encoding="utf-8"))
    row = next(r for r in ledger["records"] if r["postmortem_id"] == "PM-2026-11-28-001")
    row["engineering_fixes"]["FIX-2026-11-28-001"]["release_note"] = note
    return ledger


def test_release_note_open_closure_claim_is_detected_and_resolved() -> None:
    stale = "No runtime deployment is involved; Reporter/governance closure remains open."
    assert records.release_note_claims_open_closure(stale)
    assert records.release_note_claims_open_closure("Governance closure is still open.")
    assert not records.release_note_claims_open_closure(None)
    assert not records.release_note_claims_open_closure(
        "Release NOT_APPLICABLE; Reporter closure FIXED_VERIFIED recorded 2026-11-28T04:40:27Z."
    )

    resolved = records.resolve_closure_release_note(stale, "2026-11-28T05:00:00Z")
    assert "remains open" not in resolved.lower()
    assert "Reporter closure FIXED_VERIFIED recorded 2026-11-28T05:00:00Z" in resolved
    assert "No runtime deployment is involved" in resolved


def test_close_verified_resolves_open_closure_release_note(tmp_path, monkeypatch) -> None:
    ledger = _ledger_with_release_note(
        "PR #2904 merged; no runtime deployment is involved; "
        "Reporter/governance closure remains open."
    )
    path = tmp_path / "incident-ledger.json"
    path.write_text(json.dumps(ledger, indent=2) + "\n", encoding="utf-8")
    monkeypatch.setattr(records, "LEDGER", path)

    records.close_verified(
        postmortem_id="PM-2026-11-28-001",
        evidence="Reporter closure receipt recorded while testing the release-note guard.",
    )

    saved = json.loads(path.read_text(encoding="utf-8"))
    row = next(r for r in saved["records"] if r["postmortem_id"] == "PM-2026-11-28-001")
    fix = row["engineering_fixes"]["FIX-2026-11-28-001"]
    assert row["state"] == "VERIFIED_CLOSED"
    assert row["reporter_closure"]["status"] == "FIXED_VERIFIED"
    assert "remains open" not in fix["release_note"].lower()
    assert "FIXED_VERIFIED recorded" in fix["release_note"]
    records.validate()


def test_validate_rejects_open_closure_release_note_on_closed_record(tmp_path, monkeypatch) -> None:
    ledger = _ledger_with_release_note("No runtime deployment; governance closure remains open.")
    path = tmp_path / "incident-ledger.json"
    path.write_text(json.dumps(ledger, indent=2) + "\n", encoding="utf-8")
    monkeypatch.setattr(records, "LEDGER", path)

    with pytest.raises(ValueError, match="still claims the closure remains open"):
        records.validate()


def test_checked_in_records_have_no_open_closure_release_note() -> None:
    ledger = json.loads(records.LEDGER.read_text(encoding="utf-8"))
    for row in ledger["records"]:
        closed = row.get("state") == "VERIFIED_CLOSED" or (
            row.get("reporter_closure") or {}
        ).get("status") == "FIXED_VERIFIED"
        if not closed:
            continue
        for fix_id in row.get("engineering_fix_ids", []):
            note = (row.get("engineering_fixes") or {}).get(fix_id, {}).get("release_note")
            assert not records.release_note_claims_open_closure(note), (row["postmortem_id"], fix_id)

    pending = next(r for r in ledger["records"] if r["postmortem_id"] == "PM-2026-11-10-001")
    pending_note = pending["engineering_fixes"]["FIX-2026-11-10-001"]["release_note"]
    assert pending["state"] == "MERGED_PENDING_DEPLOY"
    assert not records.release_note_claims_open_closure(pending_note)
