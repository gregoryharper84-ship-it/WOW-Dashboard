from __future__ import annotations

import json

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
