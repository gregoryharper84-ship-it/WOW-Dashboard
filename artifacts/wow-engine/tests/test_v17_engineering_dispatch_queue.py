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
            {"issue_number": 502, "severity": "P0", "priority_rank": 1, "execution_lane": "RAPID", "rapid_stream": "C", "lease_group": "P0_STREAM_C", "conflict_keys": ["interactive-runtime"]},
            {"issue_number": 960, "severity": "P0", "priority_rank": 2, "execution_lane": "RAPID", "rapid_stream": "B", "lease_group": "P0_STREAM_B", "conflict_keys": ["nfl-full-slate"]},
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
    assert queue["records"][0]["rapid_stream"] == "C"
    assert queue["records"][0]["lease_group"] == "P0_STREAM_C"
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


def test_repo_manifest_prioritizes_current_llp_restoration_incidents():
    path = Path(__file__).parents[1] / "v17" / "engineering_dispatch_manifest.json"
    manifest = json.loads(path.read_text())
    issues = [
        {"number": 1388, "title": "runtime memory stability", "state": "OPEN", "updatedAt": "2026-10-07T02:00:00Z"},
        {"number": 1313, "title": "full slate acceptance", "state": "OPEN", "updatedAt": "2026-10-07T01:30:00Z"},
        {"number": 502, "title": "interactive user journey", "state": "OPEN", "updatedAt": "2026-10-07T01:00:00Z"},
        {"number": 1247, "title": "stale unmanifested issue", "state": "OPEN", "updatedAt": "2026-10-07T02:10:00Z"},
    ]
    queue = build_queue(manifest, issues)
    decision = select_dual_stream_work(queue["records"])
    assert decision.restoration.incident_id == "1388"
    assert decision.acceleration.incident_id is None
    by_id = {row["incident_id"]: row for row in queue["records"]}
    assert by_id["1388"]["severity"] == "P0"
    assert by_id["1388"]["priority_rank"] == 1
    assert by_id["1313"]["severity"] == "P0"
    assert by_id["1313"]["priority_rank"] == 7
    assert "1247" not in by_id
    assert queue["can_execute"] is False
    assert queue["terminal_authority"] == "V17_TERMINAL_REDUCER"


def test_p0_outside_rapid_lane_fails_closed():
    manifest = _manifest()
    manifest["restoration"][0]["execution_lane"] = "STANDARD"
    with pytest.raises(ValueError, match="must use RAPID execution_lane"):
        build_queue(manifest, [])


def test_all_manifested_active_p0_incidents_are_in_rapid_lane():
    path = Path(__file__).parents[1] / "v17" / "engineering_dispatch_manifest.json"
    manifest = json.loads(path.read_text())
    active = {1388, 1313, 502}
    by_id = {int(row["issue_number"]): row for row in manifest["restoration"]}
    assert active <= set(by_id)
    for issue_id in active:
        assert by_id[issue_id]["severity"] == "P0"
        assert by_id[issue_id]["execution_lane"] == "RAPID"
        assert by_id[issue_id]["rapid_stream"] == "C"
        assert by_id[issue_id]["lease_group"] == "P0_RUNTIME"

    issues = [
        {"number": issue_id, "title": f"P0 {issue_id}", "state": "OPEN", "updatedAt": "2026-10-07T02:00:00Z"}
        for issue_id in sorted(active)
    ]
    queue = build_queue(manifest, issues)
    assert queue["rapid_p0_count"] == len(active)
    assert all(row["execution_lane"] == "RAPID" for row in queue["records"])
    decision = select_dual_stream_work(queue["records"])
    assert decision.restoration.incident_id == "1388"
    assert decision.acceleration.incident_id is None


def test_p0_requires_stream_and_lease_metadata():
    manifest = _manifest()
    manifest["restoration"][0].pop("rapid_stream")
    with pytest.raises(ValueError, match="requires rapid_stream and lease_group"):
        build_queue(manifest, [])


def test_active_llp_p0_cases_are_rapid_with_isolated_implementation_leases():
    from v17.p0_parallel_dispatch import select_parallel

    path = Path(__file__).parents[1] / "v17" / "engineering_dispatch_manifest.json"
    manifest = json.loads(path.read_text())
    by_id = {int(row["issue_number"]): row for row in manifest["restoration"]}
    expected = {
        823: ("A", "P0_ACQUISITION"),
        1496: ("B", "P0_MLB_SCORING"),
        1388: ("C", "P0_RUNTIME"),
        1501: ("C", "P0_RUNTIME"),
        1407: ("A", "P0_ACQUISITION"),
    }
    for issue_id, (stream, lease) in expected.items():
        row = by_id[issue_id]
        assert row["severity"] == "P0"
        assert row["execution_lane"] == "RAPID"
        assert (row["rapid_stream"], row["lease_group"]) == (stream, lease)
        assert row["conflict_keys"]

    # A closed historical issue must not consume a writer; one writer per
    # active domain, no repeated incident or overlapping conflict key.
    issues = [
        {"number": issue_id, "title": str(issue_id), "state": "OPEN"}
        for issue_id in expected
    ] + [{"number": 1313, "title": "closed", "state": "CLOSED"}]
    queue = build_queue(manifest, issues)
    selection = select_parallel(queue["records"])
    assert selection["selected_count"] == 3
    assert [row["incident_id"] for row in selection["selected"]] == ["823", "1496", "1388"]
    assert len({row["lease_group"] for row in selection["selected"]}) == 3
    assert len({row["incident_id"] for row in selection["selected"]}) == 3
    keys = [key for row in selection["selected"] for key in row["conflict_keys"]]
    assert len(keys) == len(set(keys))
    assert selection["can_execute"] is False
    assert selection["terminal_authority"] == "V17_TERMINAL_REDUCER"


def test_llp_wow_restoration_surge_all_p0_issues_route_to_rapid_domains():
    path = Path(__file__).parents[1] / "v17" / "engineering_dispatch_manifest.json"
    manifest = json.loads(path.read_text())
    by_id = {r["issue_number"]: r for r in manifest["restoration"]}
    expected = {
        823: "A", 50: "A", 1407: "A",
        1496: "B", 487: "B", 491: "B", 664: "B", 665: "B",
        1388: "C", 1501: "C", 502: "C", 1313: "C",
    }
    for number, stream in expected.items():
        row = by_id[number]
        assert row["severity"] == "P0"
        assert row["execution_lane"] == "RAPID"
        assert row["rapid_stream"] == stream
        assert row["lease_group"]
        assert row["conflict_keys"]
        assert row["can_execute"] is False if "can_execute" in row else manifest["can_execute"] is False
        assert {
            "A": "ACQUISITION_WRITE_SCOPE",
            "B": "FITTED_SCORING_WRITE_SCOPE",
            "C": "RUNTIME_WRITE_SCOPE",
        }[stream] in row["conflict_keys"]
    assert manifest["terminal_authority"] == "V17_TERMINAL_REDUCER"


def test_llp_wow_rapid_surge_reuses_existing_prs_and_selects_three_real_domains():
    from v17.p0_parallel_dispatch import select_parallel

    path = Path(__file__).parents[1] / "v17" / "engineering_dispatch_manifest.json"
    manifest = json.loads(path.read_text())
    available = {823, 50, 1496, 487, 664, 1388, 1501, 502}
    issues = [
        {"number": n, "state": "OPEN", "title": "P0 product repair"}
        for n in available
    ]
    queue = build_queue(manifest, issues)
    open_prs = [
        {"number": 1559, "title": "Claude #823", "body": "Incident: 823"},
        {"number": 1507, "title": "MLB numeric", "body": "Incident: #1496"},
        {"number": 1565, "title": "Prop retry", "body": "Incident: #487"},
        {"number": 1575, "title": "Memory", "body": "Incident: #1388"},
        {"number": 1584, "title": "Latency", "body": "Incident: #1501"},
    ]
    decision = select_parallel(queue["records"], open_prs=open_prs)
    assert [r["incident_id"] for r in decision["selected"]] == ["50", "664", "502"]
    assert [r["rapid_stream"] for r in decision["selected"]] == ["A", "B", "C"]
    assert len({r["lease_group"] for r in decision["selected"]}) == 3
    assert decision["can_execute"] is False
    reasons = {r["incident_id"]: r["reason"] for r in decision["skipped"]}
    for number in (823, 1496, 487, 1388, 1501):
        assert reasons[str(number)] == "EXISTING_OPEN_PR_REQUIRES_REVIEW"


def test_llp_ncaaf_training_and_calibration_cannot_mutate_concurrently():
    from v17.p0_parallel_dispatch import select_parallel

    path = Path(__file__).parents[1] / "v17" / "engineering_dispatch_manifest.json"
    manifest = json.loads(path.read_text())
    issues = [
        {"number": n, "state": "OPEN", "title": "P0 NCAAF repair"}
        for n in (50, 665, 502)
    ]
    decision = select_parallel(build_queue(manifest, issues)["records"])
    assert [r["incident_id"] for r in decision["selected"]] == ["50", "502"]
    block = next(r for r in decision["skipped"] if r["incident_id"] == "665")
    assert block["reason"] == "CONFLICT_KEYS_OVERLAP"
    assert "NCAAF_MODEL_INPUTS" in block["overlap"]
