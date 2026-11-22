"""Regression: Daily can seed governed NFL props without inventing market offers."""

from datetime import datetime, timezone

from v17 import daily_prop_acquisition as daily
from v17 import nfl_prop_evidence_control_plane as nfl

NOW = datetime(2026, 11, 22, 15, tzinfo=timezone.utc)


def _receipt(snapshot_id, state):
    return {
        "event_id": "2026_11_DAL_PHI",
        "event_start_time": "2026-11-22T20:25:00+00:00",
        "sport": "NFL", "player": "Test Player", "stat_type": "RECEIVING_YARDS",
        "source_snapshot_id": snapshot_id, "write_status": state,
        "can_execute": False,
    }


def _batch(attempted=0, persisted=0, held=0, next_offset=None,
           status="COMPLETED", excluded=0, write_failed=0, receipts=None):
    return {
        "status": status, "attempted": attempted, "hydrated": persisted,
        "persisted": persisted, "held": held, "snapshot_write_succeeded": persisted,
        "snapshot_write_failed": write_failed,
        "explicit_prewrite_exclusions": excluded,
        "receipts": list(receipts or []), "blockers": [], "next_offset": next_offset,
        "can_execute": False,
    }


def test_legacy_direct_acquisition_remains_mlb_only(monkeypatch):
    calls = []
    monkeypatch.setattr(daily, "_acquire_mlb_daily_prop_snapshots",
                        lambda **kwargs: calls.append(kwargs) or _batch())
    monkeypatch.setattr(
        nfl, "acquire_nfl_forward_evidence_batch",
        lambda *_args, **_kwargs: (_ for _ in ()).throw(AssertionError("NFL_NOT_OPTED_IN")),
    )
    result = daily.acquire_daily_prop_snapshots(
        db=object(), requested_date="2026-11-22", requested_timezone="America/Chicago",
        max_candidates=4, now=NOW,
    )
    assert len(calls) == 1
    assert result["can_execute"] is False


def test_nfl_receipts_and_hard_budget_are_reconciled(monkeypatch):
    windows = []

    def acquire(req, **_):
        windows.append((req.candidate_offset, req.max_candidates))
        if req.candidate_offset == 0:
            return _batch(
                attempted=2, persisted=1, held=1, next_offset=2, excluded=1,
                receipts=[
                    _receipt("nfl-snap-1", "SNAPSHOT_WRITE_SUCCEEDED"),
                    _receipt(None, "PREWRITE_EXCLUDED"),
                ],
            )
        return _batch(
            attempted=1, persisted=1, receipts=[_receipt("nfl-snap-2", "SNAPSHOT_WRITE_SUCCEEDED")]
        )

    mlb_budgets = []
    monkeypatch.setattr(nfl, "acquire_nfl_forward_evidence_batch", acquire)
    monkeypatch.setattr(
        daily, "_acquire_mlb_daily_prop_snapshots",
        lambda **kwargs: mlb_budgets.append(kwargs["max_candidates"]) or _batch(),
    )
    result = daily.acquire_daily_prop_snapshots(
        db=object(), requested_date="2026-11-22", requested_timezone="America/Chicago",
        max_candidates=12, include_nfl=True, now=NOW,
    )
    assert windows == [(0, 6), (2, 10)]
    assert mlb_budgets == [10]
    assert result["attempted"] == 3
    assert result["persisted"] == result["snapshot_write_succeeded"] == 2
    assert result["explicit_prewrite_exclusions"] == 1
    assert result["attempted"] == (
        result["snapshot_write_succeeded"] + result["snapshot_write_failed"]
        + result["explicit_prewrite_exclusions"]
    )
    assert len(result["receipts"]) == 3
    assert all(receipt["can_execute"] is False for receipt in result["receipts"])
    assert {item["source_snapshot_id"] for item in result["receipts"]
            if item["write_status"] == "SNAPSHOT_WRITE_SUCCEEDED"} == {
                "nfl-snap-1", "nfl-snap-2",
            }
    assert result["status"] == "COMPLETED_WITH_SOURCE_BLOCKERS"
    assert result["can_execute"] is False


def test_nfl_discovery_failure_preserves_typed_failure_and_mlb_success(monkeypatch):
    monkeypatch.setattr(nfl, "acquire_nfl_forward_evidence_batch",
                        lambda *_args, **_kwargs: {
                            **_batch(status="DATA_UNOBTAINABLE"),
                            "blockers": ["NFL_PROP_DISCOVERY_FAILED:TimeoutError"],
                        })
    monkeypatch.setattr(daily, "_acquire_mlb_daily_prop_snapshots",
                        lambda **_: _batch(
                            attempted=1, persisted=1,
                            receipts=[{"write_status":"SNAPSHOT_WRITE_SUCCEEDED",
                                       "source_snapshot_id":"mlb-one", "can_execute":False}],
                        ))
    result = daily.acquire_daily_prop_snapshots(
        db=object(), requested_date="2026-11-22", requested_timezone="America/Chicago",
        max_candidates=4, include_nfl=True, now=NOW,
    )
    assert result["status"] == "COMPLETED_WITH_SOURCE_BLOCKERS"
    assert result["persisted"] == 1
    assert result["blockers"] == ["NFL_PROP_DISCOVERY_FAILED:TimeoutError"]
    assert result["can_execute"] is False


def test_zero_row_external_failure_remains_data_unobtainable(monkeypatch):
    monkeypatch.setattr(nfl, "acquire_nfl_forward_evidence_batch",
                        lambda *_a, **_kw: _batch(status="DATA_UNOBTAINABLE"))
    monkeypatch.setattr(daily, "_acquire_mlb_daily_prop_snapshots",
                        lambda **_kw: _batch())
    result = daily.acquire_daily_prop_snapshots(
        db=object(), requested_date="2026-11-22", requested_timezone="America/Chicago",
        max_candidates=4, include_nfl=True, now=NOW,
    )
    assert result["status"] == "DATA_UNOBTAINABLE"
    assert result["attempted"] == 0
    assert result["can_execute"] is False


def test_genuine_write_failure_cannot_be_reclassified_as_model_unavailable(monkeypatch):
    monkeypatch.setattr(nfl, "acquire_nfl_forward_evidence_batch",
                        lambda *_a, **_kw: _batch(
                            attempted=1, held=1, excluded=0, write_failed=1,
                            status="RUN_INVALID_PROP_SNAPSHOT_WRITE_FAILURE",
                        ))
    monkeypatch.setattr(daily, "_acquire_mlb_daily_prop_snapshots",
                        lambda **_kw: _batch())
    result = daily.acquire_daily_prop_snapshots(
        db=object(), requested_date="2026-11-22", requested_timezone="America/Chicago",
        max_candidates=4, include_nfl=True, now=NOW,
    )
    assert result["status"] == "RUN_INVALID_PROP_SNAPSHOT_WRITE_FAILURE"
    assert result["snapshot_write_failed"] == 1
    assert result["can_execute"] is False
