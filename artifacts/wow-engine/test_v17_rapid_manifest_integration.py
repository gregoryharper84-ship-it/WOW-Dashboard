"""Prevent recurring configuration-to-runtime drift in P0 rapid dispatch.

This runs the *actual* manifest, queue builder, selector and lease parser.
Read-only regression: no writer admission, model probability or betting execution.
"""
from __future__ import annotations

import copy
import json
from pathlib import Path

import pytest

from v17.engineering_dispatch_queue import build_queue
from v17.p0_parallel_dispatch import (
    STREAM_ORDER, TERMINAL_AUTHORITY, active_leases_from_runs, select_parallel,
)

ROOT = Path(__file__).resolve().parents[2]
MANIFEST_PATH = ROOT / "artifacts/wow-engine/v17/engineering_dispatch_manifest.json"


def _rapid_rows(manifest):
    return [
        row for section in ("restoration", "acceleration")
        for row in manifest.get(section, [])
        if row.get("severity") == "P0" and row.get("execution_lane") == "RAPID"
    ]


def _verify_runtime_compatibility(manifest):
    assert manifest["can_execute"] is False
    assert manifest["terminal_authority"] == TERMINAL_AUTHORITY
    rows = _rapid_rows(manifest)
    assert rows, "P0 manifest has no RAPID rows"
    for entry in rows:
        issue = entry["issue_number"]
        stream = entry["rapid_stream"]
        lease = entry["lease_group"]
        assert stream in STREAM_ORDER, (
            f"RAPID_STREAM_UNREGISTERED: issue={issue} stream={stream}; "
            "register in p0_parallel_dispatch.STREAM_ORDER and test leases"
        )
        assert isinstance(entry.get("conflict_keys"), list) and entry["conflict_keys"], issue
        queue = build_queue(
            manifest, [{"number": issue, "title": f"rapid-{issue}", "state": "OPEN"}],
        )
        assert queue["can_execute"] is False
        assert queue["terminal_authority"] == TERMINAL_AUTHORITY
        selected = select_parallel(queue["records"], run_number=1)
        assert selected["selected_count"] == 1, (
            f"RAPID_STREAM_UNSELECTABLE: issue={issue} stream={stream} "
            f"skipped={selected['skipped']}"
        )
        actual = selected["selected"][0]
        assert actual["incident_id"] == str(issue)
        assert actual["lease_group"] == lease
        assert actual["rapid_stream"] == stream
        assert actual["can_execute"] is False
        assert actual["terminal_authority"] == TERMINAL_AUTHORITY

        run = {
            "status": "in_progress",
            "displayTitle": f"worker lease={lease} incident={issue}",
        }
        leases = active_leases_from_runs([run], manifest)
        assert len(leases) == 1
        assert leases[0]["incident_id"] == str(issue)
        assert leases[0]["lease_group"] == lease
        bounded = select_parallel(queue["records"], active_leases=leases)
        assert bounded["selected"] == []
        assert bounded["active_writer_count"] == 1
        assert bounded["can_execute"] is False
    return rows


def test_all_configured_p0_rapid_streams_are_actually_routable():
    _verify_runtime_compatibility(json.loads(MANIFEST_PATH.read_text(encoding="utf-8")))


def test_unregistered_rapid_stream_mutation_is_caught_before_merge():
    manifest = json.loads(MANIFEST_PATH.read_text(encoding="utf-8"))
    broken = copy.deepcopy(manifest)
    broken["restoration"].append({
        "issue_number": 987654321, "severity": "P0", "priority_rank": 8,
        "execution_lane": "RAPID", "rapid_stream": "UNREGISTERED_NEW_SPORT",
        "lease_group": "P0_UNREGISTERED_NEW_SPORT",
        "conflict_keys": ["SYNTHETIC_TEST_ONLY"], "can_execute": False,
    })
    with pytest.raises(AssertionError, match="RAPID_STREAM_UNREGISTERED.*987654321"):
        _verify_runtime_compatibility(broken)


def test_corrupt_manifest_cannot_enable_live_betting_execution():
    manifest = json.loads(MANIFEST_PATH.read_text(encoding="utf-8"))
    for field, value in (
        ("can_execute", True), ("terminal_authority", "OTHER"),
    ):
        with pytest.raises(AssertionError):
            _verify_runtime_compatibility({**manifest, field: value})


def test_guard_is_registered_in_continuous_code_health():
    workflow = (
        ROOT / ".github/workflows/wow-v17-engineering-auditor-code-health.yml"
    ).read_text(encoding="utf-8")
    assert "test_v17_rapid_manifest_integration.py" in workflow
    assert "can_execute: false" in workflow
