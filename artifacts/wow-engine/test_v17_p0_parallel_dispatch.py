from __future__ import annotations

import json
from pathlib import Path

import pytest

from v17.engineering_dispatch_queue import build_queue
from v17.p0_parallel_dispatch import active_leases_from_runs, select_parallel

ROOT = Path(__file__).resolve().parents[2]
MANIFEST_PATH = ROOT / "artifacts/wow-engine/v17/engineering_dispatch_manifest.json"


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


def test_full_queue_rotates_sports_in_real_bounded_opportunities():
    records = [
        row(1388, "LLP_RESTORE", "P0_LLP_RESTORE", 1, ["LLP_USER_PATH"]),
        row(1688, "AUTONOMY_RESTORE", "P0_AUTONOMY_RESTORE", 2, ["ENGINEERING_AUTONOMY"]),
        row(1247, "GOVERNANCE", "P0_GOVERNANCE", 3, ["ENGINEERING_GOVERNANCE"]),
        row(1950, "A", "P0_NBA_RESTORE", 8, ["NBA_GAME_WIN"]),
        row(1952, "B", "P0_NHL_RESTORE", 8, ["NHL_GAME_WIN"]),
        row(1951, "C", "P0_NCAAF_RESTORE", 8, ["NCAAF_GAME_WIN"]),
    ]
    by_id = {record["incident_id"]: record for record in records}
    admitted: set[str] = set()

    def active_lease(record):
        return {
            "incident_id": record["incident_id"],
            "lease_group": record["lease_group"],
            "conflict_keys": record["conflict_keys"],
        }

    def opportunity(number, active):
        result = select_parallel(records, run_number=number, active_leases=active)
        assert result["active_writer_count"] + result["selected_count"] <= 3
        assert result["max_parallel_writers"] == 3

        leases = {lease["lease_group"] for lease in active}
        incidents = {lease["incident_id"] for lease in active}
        keys = {key for lease in active for key in lease["conflict_keys"]}
        for item in result["selected"]:
            assert item["lease_group"] not in leases
            assert item["incident_id"] not in incidents
            assert keys.isdisjoint(item["conflict_keys"])
            leases.add(item["lease_group"])
            incidents.add(item["incident_id"])
            keys.update(item["conflict_keys"])
            assert item["incident_id"] not in admitted
            admitted.add(item["incident_id"])
        assert result["can_execute"] is False
        assert result["terminal_authority"] == "V17_TERMINAL_REDUCER"
        return result

    first = opportunity(1, [])
    assert [item["incident_id"] for item in first["selected"]] == ["1388", "1688", "1247"]
    active = [active_lease(by_id[item["incident_id"]]) for item in first["selected"]]

    full = opportunity(2, active)
    assert full["selected"] == []
    assert all(item["reason"] == "MAX_PARALLEL_WRITERS_REACHED" for item in full["skipped"])

    by_id["1388"]["state"] = "CLOSED"
    active = [lease for lease in active if lease["incident_id"] != "1388"]
    nba = opportunity(3, active)
    assert [item["incident_id"] for item in nba["selected"]] == ["1950"]
    active.extend(active_lease(item) for item in nba["selected"])

    assert opportunity(4, active)["selected"] == []

    by_id["1247"]["state"] = "CLOSED"
    active = [lease for lease in active if lease["incident_id"] != "1247"]
    nhl = opportunity(5, active)
    assert [item["incident_id"] for item in nhl["selected"]] == ["1952"]
    active.extend(active_lease(item) for item in nhl["selected"])

    assert opportunity(6, active)["selected"] == []

    by_id["1688"]["state"] = "CLOSED"
    active = [lease for lease in active if lease["incident_id"] != "1688"]
    ncaaf = opportunity(7, active)
    assert [item["incident_id"] for item in ncaaf["selected"]] == ["1951"]
    assert {"1950", "1952", "1951"} <= admitted


def test_manifest_routes_all_p0_sports_and_preserves_nfl():
    manifest = json.loads(MANIFEST_PATH.read_text())
    issues = [
        {"number": number, "title": title, "state": "OPEN"}
        for number, title in ((1841, "NFL"), (1950, "NBA"), (1952, "NHL"), (1951, "NCAAF"))
    ]
    queue = build_queue(manifest, issues)
    records = {record["incident_id"]: record for record in queue["records"]}
    expected = {
        "1950": ("A", "P0_NBA_RESTORE", "NBA_GAME_WIN"),
        "1952": ("B", "P0_NHL_RESTORE", "NHL_GAME_WIN"),
        "1951": ("C", "P0_NCAAF_RESTORE", "NCAAF_GAME_WIN"),
    }
    assert set(records) == {"1841", *expected}
    for incident, (stream, lease, key) in expected.items():
        record = records[incident]
        assert (record["severity"], record["execution_lane"], record["rapid_stream"]) == ("P0", "RAPID", stream)
        assert record["lease_group"] == lease
        assert key in record["conflict_keys"]
        assert record["priority_rank"] == 8
        assert record["can_execute"] is False
        assert record["terminal_authority"] == "V17_TERMINAL_REDUCER"
    assert records["1841"]["rapid_stream"] == "LLP_RESTORE"
    sport_records = [records[incident] for incident in expected]
    for index, first in enumerate(sport_records):
        for second in sport_records[index + 1:]:
            assert first["lease_group"] != second["lease_group"]
            assert set(first["conflict_keys"]).isdisjoint(second["conflict_keys"])
    result = select_parallel(queue["records"], run_number=4)
    assert {item["incident_id"] for item in result["selected"]} == set(expected)


def test_closed_issue_wrong_rapid_identity_and_fail_closed_rows_are_not_selected():
    manifest = json.loads(MANIFEST_PATH.read_text())
    queue = build_queue(manifest, [
        {"number": 1950, "title": "NBA", "state": "CLOSED"},
        {"number": 1952, "title": "NHL", "state": "OPEN"},
    ])
    assert [record["incident_id"] for record in queue["records"]] == ["1952"]
    assert [item["incident_id"] for item in select_parallel(queue["records"])["selected"]] == ["1952"]

    wrong_lane = row(1952, "B", "P0_NHL_RESTORE", 8, ["NHL_GAME_WIN"])
    wrong_lane["execution_lane"] = "STANDARD"
    fail_open = row(1951, "C", "P0_NCAAF_RESTORE", 8, ["NCAAF_GAME_WIN"])
    fail_open["can_execute"] = True
    wrong_authority = row(1950, "A", "P0_NBA_RESTORE", 8, ["NBA_GAME_WIN"])
    wrong_authority["terminal_authority"] = "OTHER"
    assert select_parallel([wrong_lane, fail_open, wrong_authority])["selected"] == []


def test_active_lease_inventory_is_bounded_and_rejects_wrong_sport_identity():
    manifest = json.loads(MANIFEST_PATH.read_text())
    active = active_leases_from_runs([
        {"status": "completed", "displayTitle": "worker lease=P0_NBA_RESTORE incident=1950"},
        {"status": "in_progress", "displayTitle": "worker lease=P0_NBA_RESTORE incident=1950"},
        {"status": "queued", "displayTitle": "worker lease=P0_NBA_RESTORE incident=1950"},
    ], manifest)
    assert len(active) == 1
    assert active[0]["incident_id"] == "1950"

    with pytest.raises(ValueError, match="ACTIVE_P0_LEASE_METADATA_INVALID"):
        active_leases_from_runs([
            {"status": "in_progress", "displayTitle": "worker lease=P0_NHL_RESTORE incident=1950"},
        ], manifest)
    with pytest.raises(ValueError, match="ACTIVE_P0_LEASE_IDENTITY_INVALID"):
        active_leases_from_runs([
            {"status": "in_progress", "displayTitle": "worker lease=P0_NBA_RESTORE incident=AUTO"},
        ], manifest)

    wrong_rapid_manifest = json.loads(MANIFEST_PATH.read_text())
    nba_entry = next(entry for entry in wrong_rapid_manifest["restoration"] if entry["issue_number"] == 1950)
    nba_entry["rapid_stream"] = "D"
    with pytest.raises(ValueError, match="ACTIVE_P0_LEASE_METADATA_INVALID"):
        active_leases_from_runs([
            {"status": "in_progress", "displayTitle": "worker lease=P0_NBA_RESTORE incident=1950"},
        ], wrong_rapid_manifest)


def test_three_busy_slots_invalid_lease_and_invalid_run_number_fail_closed():
    records = [
        row(1950, "A", "P0_NBA_RESTORE", 8, ["NBA_GAME_WIN"]),
        row(1952, "B", "P0_NHL_RESTORE", 8, ["NHL_GAME_WIN"]),
        row(1951, "C", "P0_NCAAF_RESTORE", 8, ["NCAAF_GAME_WIN"]),
    ]
    active = [
        {"incident_id": "1388", "lease_group": "P0_LLP_RESTORE", "conflict_keys": ["LLP_USER_PATH"]},
        {"incident_id": "1688", "lease_group": "P0_AUTONOMY_RESTORE", "conflict_keys": ["ENGINEERING_AUTONOMY"]},
        {"incident_id": "1247", "lease_group": "P0_GOVERNANCE", "conflict_keys": ["ENGINEERING_GOVERNANCE"]},
    ]
    result = select_parallel(records, active_leases=active)
    assert result["selected"] == []
    assert result["active_writer_count"] == 3

    invalid = [{**active[0], "lease_group": ""}]
    with pytest.raises(ValueError, match="ACTIVE_P0_LEASE_INVALID"):
        select_parallel(records, active_leases=invalid)
    with pytest.raises(ValueError, match="DISPATCH_RUN_NUMBER_INVALID"):
        select_parallel(records, run_number=0)

    conflict = select_parallel(
        [records[0]],
        active_leases=[
            {"incident_id": "1841", "lease_group": "P0_LLP_RESTORE", "conflict_keys": ["NBA_GAME_WIN"]}
        ],
    )
    assert conflict["selected"] == []
    assert conflict["skipped"][0]["reason"] == "CONFLICT_KEYS_OVERLAP"


def test_soccer_restore_manifest_and_dispatcher_use_identical_rapid_stream():
    from v17.p0_parallel_dispatch import STREAM_ORDER
    manifest = json.loads(MANIFEST_PATH.read_text())
    soccer = [
        record for record in manifest["restoration"]
        if record.get("issue_number") == 2178
    ]
    assert len(soccer) == 1
    record = soccer[0]
    assert record["severity"] == "P0"
    assert record["execution_lane"] == "RAPID"
    assert record["rapid_stream"] == "SOCCER_RESTORE"
    assert record["rapid_stream"] in STREAM_ORDER
    assert record["lease_group"] == "P0_SOCCER_RESTORE"
    assert record["conflict_keys"] == [
        "SOCCER_CANONICAL_EVENTS", "SOCCER_1X2_MODEL",
        "SOCCER_PREGAME_FEATURES", "SOCCER_MODEL_CALIBRATION",
    ]
    assert record["can_execute"] is False


def test_soccer_manifest_queue_selects_one_writer_without_extra_controller():
    manifest = json.loads(MANIFEST_PATH.read_text())
    queue = build_queue(manifest, [{"number": 2178, "title": "P0 soccer",
                                    "state": "OPEN"}])
    matching = [item for item in queue["records"] if item["incident_id"] == "2178"]
    assert len(matching) == 1
    record = matching[0]
    result = select_parallel([record])
    assert len(result["selected"]) == 1
    assert result["selected"][0]["incident_id"] == "2178"
    assert result["selected"][0]["lease_group"] == "P0_SOCCER_RESTORE"
    assert result["selected_count"] == 1
    assert result["max_parallel_writers"] == 3
    assert result["can_execute"] is False
    assert result["terminal_authority"] == "V17_TERMINAL_REDUCER"


def test_soccer_active_lease_blocks_duplicate_and_invalid_identity():
    manifest = json.loads(MANIFEST_PATH.read_text())
    active = active_leases_from_runs([
        {"status": "in_progress",
         "displayTitle": "worker lease=P0_SOCCER_RESTORE incident=2178"},
    ], manifest)
    assert active == [{
        "incident_id": "2178",
        "lease_group": "P0_SOCCER_RESTORE",
        "conflict_keys": [
            "SOCCER_1X2_MODEL", "SOCCER_CANONICAL_EVENTS",
            "SOCCER_MODEL_CALIBRATION", "SOCCER_PREGAME_FEATURES",
        ],
    }]
    queue = build_queue(manifest, [{"number": 2178, "state": "OPEN"}])
    result = select_parallel(queue["records"], active_leases=active)
    assert result["selected"] == []
    assert any(item["reason"] == "DUPLICATE_INCIDENT"
               for item in result["skipped"])
    with pytest.raises(ValueError, match="ACTIVE_P0_LEASE_METADATA_INVALID"):
        active_leases_from_runs([{
            "status": "in_progress",
            "displayTitle": "worker lease=P0_OTHER incident=2178",
        }], manifest)


def test_soccer_remains_bounded_with_other_rapid_writer_leases():
    soccer = row(2178, "SOCCER_RESTORE", "P0_SOCCER_RESTORE", 8,
                 ["SOCCER_1X2_MODEL", "SOCCER_MODEL_CALIBRATION"])
    others = [
        row(1388, "LLP_RESTORE", "P0_LLP_RESTORE", 1, ["LLP_USER_PATH"]),
        row(1688, "AUTONOMY_RESTORE", "P0_AUTONOMY_RESTORE", 2,
            ["ENGINEERING_AUTONOMY"]),
        row(1247, "GOVERNANCE", "P0_GOVERNANCE", 3,
            ["ENGINEERING_GOVERNANCE"]),
    ]
    full = select_parallel(others + [soccer], run_number=1)
    assert len(full["selected"]) == 3
    assert all(item["incident_id"] != "2178" for item in full["selected"])
    active = [{
        "incident_id": item["incident_id"], "lease_group": item["lease_group"],
        "conflict_keys": item["conflict_keys"],
    } for item in full["selected"][:2]]
    partial = select_parallel([soccer], active_leases=active)
    assert [item["incident_id"] for item in partial["selected"]] == ["2178"]
    assert partial["selected_count"] + partial["active_writer_count"] == 3
    conflict = select_parallel([soccer], active_leases=[{
        "incident_id": "1950", "lease_group": "P0_NBA_RESTORE",
        "conflict_keys": ["SOCCER_MODEL_CALIBRATION"],
    }])
    assert conflict["selected"] == []
    assert conflict["skipped"][0]["reason"] == "CONFLICT_KEYS_OVERLAP"
