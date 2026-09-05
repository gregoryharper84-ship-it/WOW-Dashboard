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
