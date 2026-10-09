"""P1 rapid-review admission remains on the governed GLOBAL engineering queue.

A rapid lane accelerates implementation only; no QA, merge, release, or wagering
authority is granted. P0 domain-scoped production recovery retains precedence.
"""
import json
from pathlib import Path

import pytest

from v17.engineering_dispatch_queue import build_queue
from v17.engineering_agent_team import select_dual_stream_work
from v17.p0_parallel_dispatch import select_parallel

MANIFEST = Path(__file__).resolve().parent / "v17" / "engineering_dispatch_manifest.json"


def _manifest():
    return json.loads(MANIFEST.read_text())


def _issues(*numbers):
    return [
        {"number": n, "title": f"Issue {n}", "state": "OPEN",
         "updatedAt": "2026-10-08T23:00:00Z"} for n in numbers
    ]


def test_p1_governance_rapid_is_highest_global_p1_without_displacing_p0():
    manifest = _manifest()
    row = next(x for x in manifest["restoration"] if x["issue_number"] == 1540)
    assert (row["severity"], row["execution_lane"], row["rapid_stream"]) == (
        "P1", "RAPID", "GOVERNANCE"
    )
    assert row["lease_group"] == "GLOBAL"
    assert len(row["conflict_keys"]) >= 3

    queue = build_queue(manifest, _issues(1540, 823, 1388))
    assert queue["rapid_p0_count"] == 1
    assert queue["can_execute"] is False
    assert queue["terminal_authority"] == "V17_TERMINAL_REDUCER"
    # Preserve unchanged P0 domain-scoped dispatch: never recast P1 as P0.
    p0 = select_parallel(queue["records"])
    assert all(x["incident_id"] != "1540" for x in p0["selected"])
    assert any(x["incident_id"] == "1388" for x in queue["records"])
    # GLOBAL worker's existing closure selector explicitly excludes P0 RAPID.
    standard = [x for x in queue["records"]
                if x["severity"] != "P0" or x["execution_lane"] != "RAPID"]
    work = select_dual_stream_work(standard)
    assert work.restoration.incident_id == "1540"
    assert work.restoration.severity == "P1"
    assert work.acceleration.incident_id is None
    assert "CLOSURE_FOCUS_MODE" in work.acceleration_blocked_reason


def test_closed_rapid_governance_issue_is_not_dispatched():
    queue = build_queue(_manifest(), _issues(823, 1388))
    assert not any(x["incident_id"] == "1540" for x in queue["records"])


def test_duplicate_governance_issue_across_work_streams_fails_closed():
    manifest = _manifest()
    manifest["acceleration"].append(dict(
        next(x for x in manifest["restoration"] if x["issue_number"] == 1540)
    ))
    with pytest.raises(ValueError, match="duplicate issue_number"):
        build_queue(manifest, _issues(1540))


def test_rapid_metadata_never_grants_release_or_order_authority():
    queue = build_queue(_manifest(), _issues(1540))
    record = next(x for x in queue["records"] if x["incident_id"] == "1540")
    assert record["can_execute"] is False
    assert record["terminal_authority"] == "V17_TERMINAL_REDUCER"
    assert "release_authorized" not in record
    assert "merge_authorized" not in record
