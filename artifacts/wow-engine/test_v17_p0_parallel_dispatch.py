from __future__ import annotations

import pytest

from v17.p0_parallel_dispatch import select_parallel


def row(incident, stream, lease, rank, keys):
    return {
        "incident_id": str(incident),
        "severity": "P0",
        "execution_lane": "RAPID",
        "rapid_stream": stream,
        "lease_group": lease,
        "priority_rank": rank,
        "state": "OPEN",
        "conflict_keys": keys,
        "can_execute": False,
        "terminal_authority": "V17_TERMINAL_REDUCER",
    }


def test_governance_stream_is_prioritized_without_exceeding_global_limit():
    result = select_parallel([
        row(1247, "GOVERNANCE", "P0_GOVERNANCE", 1, ["ENGINEERING_GOVERNANCE", "RELEASE_GOVERNANCE"]),
        row(1237, "A", "P0_STREAM_A", 2, ["SCOUT_PERSISTENCE", "SUPABASE_DATA_PLANE"]),
        row(1189, "B", "P0_STREAM_B", 4, ["ACTION_TRANSPORT", "CUSTOM_GPT_ACTION"]),
        row(502, "C", "P0_STREAM_C", 6, ["WOW_HOST_ORCHESTRATION", "INTERACTIVE_RUNTIME"]),
    ])
    assert [x["incident_id"] for x in result["selected"]] == ["1247", "1237", "1189"]
    assert result["selected_count"] == 3
    assert result["max_parallel_writers"] == 3
    skipped = {x["incident_id"]: x for x in result["skipped"]}
    assert skipped["502"]["reason"] == "MAX_PARALLEL_WRITERS_REACHED"
    assert result["can_execute"] is False
    assert result["terminal_authority"] == "V17_TERMINAL_REDUCER"


def test_selects_three_non_conflicting_streams():
    result = select_parallel([
        row(1237, "A", "P0_STREAM_A", 2, ["SCOUT_PERSISTENCE", "SUPABASE_DATA_PLANE"]),
        row(1189, "B", "P0_STREAM_B", 4, ["ACTION_TRANSPORT", "CUSTOM_GPT_ACTION"]),
        row(502, "C", "P0_STREAM_C", 6, ["WOW_HOST_ORCHESTRATION", "INTERACTIVE_RUNTIME"]),
    ])
    assert [x["incident_id"] for x in result["selected"]] == ["1237", "1189", "502"]
    assert result["selected_count"] == 3
    assert result["max_parallel_writers"] == 3
    assert result["can_execute"] is False
    assert result["terminal_authority"] == "V17_TERMINAL_REDUCER"


def test_stream_c_skips_conflicting_502_and_advances_1127():
    result = select_parallel([
        row(960, "B", "P0_STREAM_B", 5, ["WOW_HOST_ORCHESTRATION", "NFL_FULL_SLATE"]),
        row(502, "C", "P0_STREAM_C", 6, ["WOW_HOST_ORCHESTRATION", "INTERACTIVE_RUNTIME"]),
        row(1127, "C", "P0_STREAM_C", 7, ["SPORT_DISCOVERY", "WNBA_ACQUISITION"]),
    ])
    assert [x["incident_id"] for x in result["selected"]] == ["960", "1127"]
    skipped = {x["incident_id"]: x for x in result["skipped"]}
    assert skipped["502"]["reason"] == "CONFLICT_KEYS_OVERLAP"
    assert skipped["502"]["overlap"] == ["WOW_HOST_ORCHESTRATION"]


def test_only_one_writer_per_lease_group():
    result = select_parallel([
        row(1237, "A", "P0_STREAM_A", 2, ["SCOUT_PERSISTENCE"]),
        row(1250, "B", "P0_STREAM_A", 3, ["SCOUT_HANDOFF"]),
    ])
    assert [x["incident_id"] for x in result["selected"]] == ["1237"]
    assert any(x["reason"] == "LEASE_GROUP_ALREADY_CLAIMED" for x in result["skipped"])


def test_missing_explicit_conflict_metadata_fails_closed():
    result = select_parallel([
        row(1237, "A", "P0_STREAM_A", 2, []),
    ])
    assert result["selected"] == []
    assert result["skipped"][0]["reason"] == "MISSING_EXPLICIT_LEASE_OR_CONFLICT_METADATA"


def test_related_existing_open_pr_is_reviewed_instead_of_dispatched_twice():
    records = [
        row(823, "A", "P0_ACQUISITION", 1, ["ACQUISITION"]),
        row(1407, "A", "P0_ACQUISITION", 2, ["INPLAY_ACTION"]),
        row(1496, "B", "P0_MLB_SCORING", 3, ["MLB_SCORER"]),
        row(1388, "C", "P0_RUNTIME", 4, ["MEMORY_STABILITY"]),
    ]
    result = select_parallel(records, open_prs=[
        {"number": 1301, "title": "ESPN fallback", "body": "Incident: #823"},
        {"number": 1507, "title": "numeric evidence", "body": "Fixes #1496"},
    ])
    assert [x["incident_id"] for x in result["selected"]] == ["1407", "1388"]
    skipped = {x["incident_id"]: x["reason"] for x in result["skipped"]}
    assert skipped["823"] == "EXISTING_OPEN_PR_REQUIRES_REVIEW"
    assert skipped["1496"] == "EXISTING_OPEN_PR_REQUIRES_REVIEW"


def test_active_verified_runtime_worker_allows_disjoint_acquisition_and_scoring():
    records = [
        row(823, "A", "P0_ACQUISITION", 1, ["CANONICAL_EVENT_IDENTITY"]),
        row(1496, "B", "P0_MLB_SCORING", 2, ["MLB_SCORER_INPUTS"]),
        row(1388, "C", "P0_RUNTIME", 3, ["MEMORY_STABILITY"]),
    ]
    active = [{
        "id": 99,
        "path": ".github/workflows/wow-v17-claude-engineering-worker.yml@refs/heads/main",
        "display_title": "wow-v17-claude-engineering-worker lease=P0_RUNTIME incident=1388",
        "head_branch": "main",
    }]
    result = select_parallel(records, active_runs=active)
    assert [x["incident_id"] for x in result["selected"]] == ["823", "1496"]
    assert result["skipped"][0]["reason"] == "ACTIVE_WORKER_LEASE_CONFLICT"


def test_active_cross_stream_conflict_key_blocks_second_writer():
    records = [
        row(823, "A", "P0_ACQUISITION", 1, ["CANONICAL_EVENT_IDENTITY"]),
        row(1496, "B", "P0_MLB_SCORING", 2, ["CANONICAL_EVENT_IDENTITY"]),
    ]
    active = [{
        "id": 101,
        "path": ".github/workflows/wow-v17-chatgpt-engineering-worker.yml",
        "display_title": "wow-v17-chatgpt-engineering-worker lease=P0_ACQUISITION incident=823",
        "head_branch": "main",
    }]
    result = select_parallel(records, active_runs=active)
    assert result["selected"] == []
    assert result["skipped"][1]["reason"] == "ACTIVE_WORKER_CONFLICT_KEYS_OVERLAP"


@pytest.mark.parametrize("title,branch", [
    ("wow-v17-claude-engineering-worker lease=GLOBAL incident=AUTO", "main"),
    ("wow-v17-claude-engineering-worker-lookalike lease=P0_RUNTIME incident=1388", "main"),
    ("wow-v17-claude-engineering-worker lease=P0_RUNTIME incident=1388", "other-branch"),
])
def test_unknown_active_worker_identity_fails_closed(title, branch):
    runs = [{
        "id": 100,
        "path": ".github/workflows/wow-v17-claude-engineering-worker.yml",
        "display_title": title,
        "head_branch": branch,
    }]
    with pytest.raises(ValueError, match="ACTIVE_WORKER_"):
        select_parallel([row(1388, "C", "P0_RUNTIME", 1, ["MEMORY"])], active_runs=runs)


def test_invalid_existing_pr_inventory_fails_closed():
    with pytest.raises(ValueError, match="OPEN_PR_INVENTORY_INVALID"):
        select_parallel([row(823, "A", "P0_ACQUISITION", 1, ["ACQUISITION"])],
                        open_prs=[{"title": "orphan without PR number", "body": "#823"}])
