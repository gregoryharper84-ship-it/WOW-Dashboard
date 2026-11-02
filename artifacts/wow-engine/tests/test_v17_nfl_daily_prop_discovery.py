"""NFL + MLB Daily acquisition budget, isolation and source-truth regressions."""
from __future__ import annotations

from v17 import daily_prop_discovery as dispatch
from v17 import nfl_daily_prop_acquisition as nfl_adapter
from v17.daily_snapshot_runtime import _prop_discovery_accounting, _lane_reconciliation, _nfl_fill_response_rows


def _outcome(sport, attempted, *, persisted=0, next_offset=None, blockers=None):
    receipts = [
        {"sport": sport, "source_snapshot_id": f"{sport}-{i}",
         "write_status": "SNAPSHOT_WRITE_SUCCEEDED", "can_execute": False}
        for i in range(persisted)
    ]
    receipts.extend(
        {"sport": sport, "write_status": "PREWRITE_EXCLUDED", "can_execute": False}
        for _ in range(attempted - persisted)
    )
    return {
        "sport": sport, "status": "COMPLETED",
        "attempted": attempted, "hydrated": persisted, "persisted": persisted,
        "snapshot_write_succeeded": persisted, "snapshot_write_failed": 0,
        "explicit_prewrite_exclusions": attempted - persisted,
        "held": attempted - persisted, "receipts": receipts,
        "blockers": blockers or [], "next_offset": next_offset,
        "can_execute": False,
    }


def test_shared_attempt_ceiling_with_mlb_unused_rollover(monkeypatch):
    calls = []
    def nfl(**kwargs):
        calls.append(("NFL", kwargs["max_candidates"], kwargs["candidate_offset"]))
        if kwargs["candidate_offset"] == 0:
            return _outcome("NFL", 3, persisted=2, next_offset=3)
        return _outcome("NFL", kwargs["max_candidates"], persisted=0)
    def mlb(**kwargs):
        calls.append(("MLB", kwargs["max_candidates"], 0))
        return _outcome("MLB", 1, persisted=1)
    monkeypatch.setattr(dispatch, "acquire_nfl", nfl)
    monkeypatch.setattr(dispatch, "acquire_mlb", mlb)
    result = dispatch.acquire_daily_prop_snapshots(
        db=object(), requested_date="2026-11-05",
        requested_timezone="America/Chicago", max_candidates=10,
    )
    assert calls == [("NFL", 5, 0), ("MLB", 7, 0), ("NFL", 6, 3)]
    assert result["attempted"] == 10
    assert result["persisted"] == 3
    assert result["snapshot_write_succeeded"] == 3
    assert result["explicit_prewrite_exclusions"] == 7
    assert result["can_execute"] is False
    assert all(r["can_execute"] is False for r in result["receipts"])


def test_unavailable_nfl_does_not_void_mlb(monkeypatch):
    def broken(**kwargs):
        raise RuntimeError("provider down")
    monkeypatch.setattr(dispatch, "acquire_nfl", broken)
    monkeypatch.setattr(dispatch, "acquire_mlb", lambda **kwargs: _outcome("MLB", 2, persisted=2))
    result = dispatch.acquire_daily_prop_snapshots(
        db=object(), requested_date="2026-11-05",
        requested_timezone="America/Chicago", max_candidates=4,
    )
    assert result["attempted"] == 2
    assert result["persisted"] == 2
    assert result["status"] == "COMPLETED_WITH_ROW_BLOCKERS"
    assert "NFL_DAILY_DISCOVERY_FAILED:RuntimeError" in result["blockers"]


def test_zero_budget_does_not_call_provider(monkeypatch):
    def must_not_run(**kwargs):
        raise AssertionError("provider should not be called")
    monkeypatch.setattr(dispatch, "acquire_nfl", must_not_run)
    monkeypatch.setattr(dispatch, "acquire_mlb", must_not_run)
    result = dispatch.acquire_daily_prop_snapshots(
        db=object(), requested_date="2026-11-05",
        requested_timezone="America/Chicago", max_candidates=0,
    )
    assert result["attempted"] == 0 and result["persisted"] == 0
    assert result["can_execute"] is False


def test_invalid_sport_claim_cannot_exceed_budget(monkeypatch):
    monkeypatch.setattr(dispatch, "acquire_nfl", lambda **kwargs: _outcome("NFL", 99))
    monkeypatch.setattr(dispatch, "acquire_mlb", lambda **kwargs: _outcome("MLB", 0))
    result = dispatch.acquire_daily_prop_snapshots(
        db=object(), requested_date="2026-11-05",
        requested_timezone="America/Chicago", max_candidates=2,
    )
    assert result["attempted"] == 0
    assert result["persisted"] == 0
    assert "PROP_DAILY_DISCOVERY_BUDGET_CONTRACT_INVALID" in result["blockers"]
    assert result["can_execute"] is False


def test_nfl_adapter_zero_budget_preserves_research_only():
    result = nfl_adapter.acquire_daily_nfl_prop_snapshots(
        db=object(), requested_date="2026-11-05",
        requested_timezone="America/Chicago", max_candidates=0,
    )
    assert result["attempted"] == 0
    assert result["can_execute"] is False
    assert result["line_source"] == "PRIOR10_MEDIAN_HALF_POINT_DISCOVERY_ONLY"


def test_accounting_new_vs_reused_is_identity_bound():
    acq = {
        "attempted": 3, "receipts": [
            {"source_snapshot_id": "new", "write_status": "SNAPSHOT_WRITE_SUCCEEDED"},
            {"source_snapshot_id": "not-readback", "write_status": "SNAPSHOT_WRITE_SUCCEEDED"},
            {"write_status": "PREWRITE_EXCLUDED"},
        ],
    }
    summary = _prop_discovery_accounting(
        acquisition=acq,
        canonical_manifest=[{"source_snapshot_id": "new"}, {"source_snapshot_id": "old"}],
    )
    assert summary["new_candidates_acquired"] == 1
    assert summary["reused_snapshot_rows"] == 1
    assert summary["zero_new_candidates_reason"] is None
    assert summary["can_execute"] is False


def test_accounting_distinguishes_not_attempted_vs_all_existing():
    canonical = [{"source_snapshot_id": "existing"}]
    none = _prop_discovery_accounting(acquisition=None, canonical_manifest=canonical)
    reused = _prop_discovery_accounting(
        acquisition={"attempted": 1, "receipts": [{"write_status": "ALREADY_CAPTURED"}]},
        canonical_manifest=canonical,
    )
    assert none["zero_new_candidates_reason"] == "AUTONOMOUS_DISCOVERY_NOT_ATTEMPTED"
    assert reused["zero_new_candidates_reason"] == "ALL_DISCOVERED_CANDIDATES_ALREADY_CAPTURED"
    assert reused["reused_snapshot_rows"] == 1


def test_hydration_only_hold_is_not_a_source_wide_discovery_outage():
    hydration_hold = {
        "status": "COMPLETED_WITH_ROW_BLOCKERS",
        "attempted": 1,
        "persisted": 0,
        "receipts": [{"write_status": "PREWRITE_EXCLUDED"}],
        "blockers": ["PLAYER:RECEIVING_YARDS:PROP_PLAYER_IDENTITY_UNRESOLVED"],
        "per_sport": [
            {"sport": "NFL", "status": "COMPLETED_WITH_ROW_BLOCKERS"},
            {"sport": "MLB", "status": "COMPLETED"},
        ],
        "can_execute": False,
    }
    accounting = _prop_discovery_accounting(
        acquisition=hydration_hold, canonical_manifest=[]
    )
    lane = _lane_reconciliation(
        [], "PROPS", blockers=hydration_hold["blockers"],
        acquisition=hydration_hold, canonical_manifest=[], requested_limit=6,
    )
    assert accounting["zero_new_candidates_reason"] == "NO_CANDIDATE_COULD_BE_HYDRATED"
    assert lane["zero_row_reason"] == "NO_CANONICAL_CANDIDATES"
    assert accounting["can_execute"] is False


def test_provider_outage_stays_distinct_from_successful_empty_slate():
    source_failure = {
        "status": "COMPLETED_WITH_ROW_BLOCKERS",
        "attempted": 0,
        "persisted": 0,
        "receipts": [],
        "blockers": ["NFL_DAILY_DISCOVERY_FAILED:TimeoutError"],
        "per_sport": [
            {"sport": "NFL", "status": "DATA_UNOBTAINABLE"},
            {"sport": "MLB", "status": "COMPLETED"},
        ],
    }
    source = _prop_discovery_accounting(
        acquisition=source_failure, canonical_manifest=[]
    )
    lane = _lane_reconciliation(
        [], "PROPS", blockers=source_failure["blockers"],
        acquisition=source_failure, canonical_manifest=[], requested_limit=6,
    )
    assert source["zero_new_candidates_reason"] == "DISCOVERY_DATA_UNOBTAINABLE"
    assert lane["zero_row_reason"] == "DISCOVERY_DATA_UNOBTAINABLE"
    empty = _prop_discovery_accounting(
        acquisition={"status": "COMPLETED", "attempted": 0, "blockers": []},
        canonical_manifest=[],
    )
    assert empty["zero_new_candidates_reason"] == "NO_CANDIDATES_DISCOVERED"



def test_mlb_scheduled_rotation_reaches_later_pitcher_stat_rows(monkeypatch):
    from datetime import datetime, timedelta, timezone
    from v17 import daily_prop_acquisition as mlb
    future = (datetime.now(timezone.utc) + timedelta(days=1)).isoformat()
    players = [
        {"event_id": "MLB:1", "event_start_time": future, "player": "First", "opponent": "B"},
        {"event_id": "MLB:1", "event_start_time": future, "player": "Second", "opponent": "A"},
    ]
    monkeypatch.setattr(mlb, "_request_schedule", lambda *_a, **_kw: {})
    monkeypatch.setattr(mlb, "_schedule_pitchers", lambda *_a, **_kw: players)

    def held(**_kwargs):
        raise mlb.PropAutoHydrationError("PROP_PLAYER_IDENTITY_UNRESOLVED", "test hold")
    monkeypatch.setattr(mlb, "auto_hydrate_prop_evidence", held)
    def run(slot):
        return mlb.acquire_daily_prop_snapshots(
            db=object(), requested_date="2026-11-05",
            requested_timezone="America/Chicago", max_candidates=2,
            rotating_window_slot=slot,
        )
    first = run(0)
    second = run(1)
    third = run(2)
    assert first["attempted"] == second["attempted"] == third["attempted"] == 2
    assert [r["stat_type"] for r in first["receipts"]] == list(mlb.STAT_TYPES[:2])
    assert [r["stat_type"] for r in second["receipts"]] == list(mlb.STAT_TYPES[2:])
    assert {r["player"] for r in third["receipts"]} == {"Second"}
    assert all(r["can_execute"] is False for r in third["receipts"])


def test_dispatcher_passes_same_rotation_slot_to_both_producers(monkeypatch):
    observed = []
    def nfl(**kwargs):
        observed.append(("NFL", kwargs.get("rotating_window_slot")))
        return _outcome("NFL", 1)
    def mlb(**kwargs):
        observed.append(("MLB", kwargs.get("rotating_window_slot")))
        return _outcome("MLB", 1)
    monkeypatch.setattr(dispatch, "acquire_nfl", nfl)
    monkeypatch.setattr(dispatch, "acquire_mlb", mlb)
    result = dispatch.acquire_daily_prop_snapshots(
        db=object(), requested_date="2026-11-05",
        requested_timezone="America/Chicago", max_candidates=2,
        rotating_window_slot=7,
    )
    assert observed == [("NFL", 7), ("MLB", 7)]
    assert result["attempted"] == 2
    assert result["can_execute"] is False



def test_missing_nfl_only_fill_reserves_budget_for_nfl_not_existing_mlb(monkeypatch):
    calls = []
    def nfl(**kwargs):
        calls.append(("NFL", kwargs["max_candidates"], kwargs["candidate_offset"]))
        start = kwargs["candidate_offset"]
        attempted = min(kwargs["max_candidates"], 6 - start)
        next_offset = start + attempted if start + attempted < 6 else None
        return _outcome("NFL", attempted, persisted=attempted, next_offset=next_offset)
    def forbidden_mlb(**kwargs):
        raise AssertionError("already captured MLB must not be reacquired")
    monkeypatch.setattr(dispatch, "acquire_nfl", nfl)
    monkeypatch.setattr(dispatch, "acquire_mlb", forbidden_mlb)
    result = dispatch.acquire_daily_prop_snapshots(
        db=object(), requested_date="2026-11-05",
        requested_timezone="America/Chicago", max_candidates=4,
        nfl_only=True,
    )
    assert calls == [("NFL", 4, 0)]
    assert result["attempted"] == result["persisted"] == 4
    assert result["candidate_source"] == "NFL_ESPN_PREGAME_ROSTER"
    assert result["can_execute"] is False


def test_nfl_only_fill_continues_in_bounded_windows(monkeypatch):
    called = []
    def nfl(**kwargs):
        start = kwargs["candidate_offset"]
        capacity = kwargs["max_candidates"]
        called.append((start, capacity))
        size = min(capacity, 75 - start)
        return _outcome(
            "NFL", size, persisted=size,
            next_offset=start + size if start + size < 75 else None,
        )
    monkeypatch.setattr(dispatch, "acquire_nfl", nfl)
    monkeypatch.setattr(dispatch, "acquire_mlb",
                        lambda **_: (_ for _ in ()).throw(AssertionError("MLB")))
    result = dispatch.acquire_daily_prop_snapshots(
        db=object(), requested_date="2026-11-05",
        requested_timezone="America/Chicago", max_candidates=120,
        nfl_only=True,
    )
    assert called == [(0, 64), (64, 56)]
    assert result["attempted"] == 75
    assert result["persisted"] == 75
    assert result["attempted"] <= 120
    assert result["can_execute"] is False



def test_missing_nfl_fill_reserves_rows_without_exceeding_daily_page():
    manifest = [
        {"source_snapshot_id": f"mlb-{i}", "sport": "MLB", "event_start_time": f"early-{i}"}
        for i in range(8)
    ] + [
        {"source_snapshot_id": f"nfl-{i}", "sport": "NFL", "event_start_time": f"later-{i}"}
        for i in range(4)
    ]
    picked = _nfl_fill_response_rows(manifest, 6)
    assert len(picked) == 6
    assert [r["source_snapshot_id"] for r in picked[:3]] == [
        "nfl-0", "nfl-1", "nfl-2",
    ]
    assert [r["source_snapshot_id"] for r in picked[3:]] == [
        "mlb-0", "mlb-1", "mlb-2",
    ]
    assert len({r["source_snapshot_id"] for r in picked}) == len(picked)


def test_nfl_fill_small_page_and_mlb_only_compatibility():
    manifest = [
        {"source_snapshot_id": "mlb-first", "sport": "MLB"},
        {"source_snapshot_id": "nfl-later", "sport": "NFL"},
    ]
    assert [r["sport"] for r in _nfl_fill_response_rows(manifest, 1)] == ["NFL"]
    assert _nfl_fill_response_rows(manifest, 0) == []
    mlb_only = [r for r in manifest if r["sport"] == "MLB"]
    assert _nfl_fill_response_rows(mlb_only, 6) == mlb_only



def test_missing_nfl_fill_retains_mlb_when_nfl_events_sort_first():
    # The supplemental NFL family must not displace existing MLB snapshots,
    # even when all of the earliest canonical rows happen to be NFL.
    manifest = [
        {"source_snapshot_id": f"nfl-{i}", "sport": "NFL"}
        for i in range(8)
    ] + [
        {"source_snapshot_id": f"mlb-{i}", "sport": "MLB"}
        for i in range(4)
    ]
    selected = _nfl_fill_response_rows(manifest, 6)
    assert len(selected) == 6
    assert [row["source_snapshot_id"] for row in selected] == [
        "nfl-0", "nfl-1", "nfl-2", "mlb-0", "mlb-1", "mlb-2",
    ]
    assert len({row["source_snapshot_id"] for row in selected}) == 6
    assert _nfl_fill_response_rows(manifest, 1)[0]["sport"] == "NFL"
    assert _nfl_fill_response_rows(manifest, 0) == []


def test_missing_nfl_fill_uses_additional_nfl_only_after_existing_mlb():
    manifest = [
        {"source_snapshot_id": "nfl-0", "sport": "NFL"},
        {"source_snapshot_id": "mlb-0", "sport": "MLB"},
        {"source_snapshot_id": "nfl-1", "sport": "NFL"},
        {"source_snapshot_id": "nfl-2", "sport": "NFL"},
    ]
    selected = _nfl_fill_response_rows(manifest, 4)
    assert [row["source_snapshot_id"] for row in selected] == [
        "nfl-0", "nfl-1", "mlb-0", "nfl-2",
    ]
