from __future__ import annotations

import json
from pathlib import Path

import pytest

from v17.engineering_dispatch_queue import build_queue
from v17.engineering_agent_team import select_dual_stream_work


def _manifest():
    return {
        "can_execute": False,
        "terminal_authority": "V17_TERMINAL_REDUCER",
        "restoration": [
            {"issue_number": 502, "severity": "P0", "priority_rank": 1, "execution_lane": "RAPID", "conflict_keys": ["interactive-runtime"]},
            {"issue_number": 960, "severity": "P0", "priority_rank": 2, "execution_lane": "RAPID", "conflict_keys": ["nfl-full-slate"]},
        ],
        "acceleration": [
            {"issue_number": 1135, "severity": "P1", "priority_rank": 1, "execution_lane": "STANDARD", "conflict_keys": ["failure-capsule"]},
        ],
    }


def test_build_queue_uses_github_open_state_and_preserves_stream_metadata():
    issues = [
        {"number": 502, "title": "restore", "state": "OPEN", "updatedAt": "2026-10-02T00:00:00Z"},
        {"number": 960, "title": "closed", "state": "CLOSED", "updatedAt": "2026-10-02T00:00:00Z"},
        {"number": 1135, "title": "accelerate", "state": "OPEN", "updatedAt": "2026-10-02T00:00:00Z"},
    ]
    queue = build_queue(_manifest(), issues)
    assert [row["incident_id"] for row in queue["records"]] == ["502", "1135"]
    assert queue["records"][0]["work_stream"] == "RESTORATION"
    assert queue["records"][0]["execution_lane"] == "RAPID"
    assert queue["records"][1]["work_stream"] == "ACCELERATION"
    assert queue["records"][1]["conflict_keys"] == ["FAILURE-CAPSULE"]
    assert queue["can_execute"] is False
    assert queue["terminal_authority"] == "V17_TERMINAL_REDUCER"
    assert queue["rapid_p0_count"] == 1


def test_manifest_duplicate_issue_fails_closed():
    manifest = _manifest()
    manifest["acceleration"].append(
        {"issue_number": 502, "severity": "P2", "priority_rank": 2, "execution_lane": "STANDARD", "conflict_keys": ["x"]}
    )
    with pytest.raises(ValueError, match="duplicate issue_number"):
        build_queue(manifest, [])


def test_manifest_requires_explicit_conflict_keys():
    manifest = _manifest()
    manifest["acceleration"][0]["conflict_keys"] = []
    with pytest.raises(ValueError, match="explicit conflict_keys"):
        build_queue(manifest, [])


def test_repo_manifest_is_valid_and_never_grants_execution():
    path = Path(__file__).parents[1] / "v17" / "engineering_dispatch_manifest.json"
    manifest = json.loads(path.read_text())
    queue = build_queue(manifest, [])
    assert queue["records"] == []
    assert queue["can_execute"] is False
    assert queue["terminal_authority"] == "V17_TERMINAL_REDUCER"


def test_repo_manifest_prioritizes_current_p0_governance_and_persistence_incidents():
    path = Path(__file__).parents[1] / "v17" / "engineering_dispatch_manifest.json"
    manifest = json.loads(path.read_text())
    issues = [
        {"number": 1247, "title": "governance", "state": "OPEN", "updatedAt": "2026-10-03T13:05:21Z"},
        {"number": 1237, "title": "persistence", "state": "OPEN", "updatedAt": "2026-10-03T12:00:00Z"},
        {"number": 502, "title": "older restoration", "state": "OPEN", "updatedAt": "2026-10-02T00:00:00Z"},
    ]
    queue = build_queue(manifest, issues)
    decision = select_dual_stream_work(queue["records"])
    assert decision.restoration.incident_id == "1247"
    by_id = {row["incident_id"]: row for row in queue["records"]}
    assert by_id["1247"]["severity"] == "P0"
    assert by_id["1247"]["priority_rank"] == 1
    assert by_id["1237"]["severity"] == "P0"
    assert by_id["1237"]["priority_rank"] == 2
    assert queue["can_execute"] is False
    assert queue["terminal_authority"] == "V17_TERMINAL_REDUCER"


def test_p0_outside_rapid_lane_fails_closed():
    manifest = _manifest()
    manifest["restoration"][0]["execution_lane"] = "STANDARD"
    with pytest.raises(ValueError, match="must use RAPID execution_lane"):
        build_queue(manifest, [])


def test_all_active_p0_incidents_are_in_rapid_lane():
    path = Path(__file__).parents[1] / "v17" / "engineering_dispatch_manifest.json"
    manifest = json.loads(path.read_text())
    active = {1247, 1237, 1250, 1189, 960, 502, 1127}
    by_id = {int(row["issue_number"]): row for row in manifest["restoration"]}
    assert active <= set(by_id)
    for issue_id in active:
        assert by_id[issue_id]["severity"] == "P0"
        assert by_id[issue_id]["execution_lane"] == "RAPID"

    issues = [
        {"number": issue_id, "title": f"P0 {issue_id}", "state": "OPEN", "updatedAt": "2026-10-03T14:00:00Z"}
        for issue_id in sorted(active)
    ]
    queue = build_queue(manifest, issues)
    assert queue["rapid_p0_count"] == len(active)
    assert all(row["execution_lane"] == "RAPID" for row in queue["records"])
    assert select_dual_stream_work(queue["records"]).restoration.incident_id == "1247"
