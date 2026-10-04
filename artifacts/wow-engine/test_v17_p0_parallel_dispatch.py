from __future__ import annotations

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
