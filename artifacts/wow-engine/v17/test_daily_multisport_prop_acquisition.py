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


def test_refresh_trigger_only_for_selected_stale_captures():
    from datetime import timedelta
    from v17.daily_snapshot_runtime import _prop_manifest_requires_refresh
    fresh = {"captured_at": (NOW - timedelta(minutes=5)).isoformat()}
    old = {"captured_at": (NOW - timedelta(minutes=31)).isoformat()}
    assert _prop_manifest_requires_refresh([old], selected_limit=1, now=NOW)
    assert not _prop_manifest_requires_refresh([fresh], selected_limit=1, now=NOW)
    assert not _prop_manifest_requires_refresh([fresh, old], selected_limit=1, now=NOW)
    assert _prop_manifest_requires_refresh([fresh, old], selected_limit=2, now=NOW)
    assert not _prop_manifest_requires_refresh([old], selected_limit=0, now=NOW)
    assert not _prop_manifest_requires_refresh([], selected_limit=4, now=NOW)
    # Missing capture time remains unverified; it is not a stale *timestamp* trigger.
    assert not _prop_manifest_requires_refresh([{}], selected_limit=1, now=NOW)


def test_nonempty_stale_daily_manifest_requests_new_evidence_and_reports_source_error(monkeypatch):
    from datetime import timedelta
    from v17 import test_active_season_phase_a_daily_chain as fixtures
    from v17 import daily_snapshot_runtime as runtime

    db = fixtures._DB([
        {**fixtures._snapshot("Existing Stale NFL", index=100),
         "captured_at": (fixtures.NOW - timedelta(hours=2)).isoformat()},
    ])
    calls = []

    def acquire(**kwargs):
        calls.append(kwargs)
        return {
            "status": "COMPLETED_WITH_SOURCE_BLOCKERS",
            "attempted": 0, "persisted": 0,
            "snapshot_write_succeeded": 0, "snapshot_write_failed": 0,
            "explicit_prewrite_exclusions": 0, "receipts": [],
            "blockers": ["NFL_PROP_DISCOVERY_FAILED:TimeoutError"],
            "can_execute": False,
        }

    monkeypatch.setattr(runtime, "acquire_daily_prop_snapshots", acquire)
    # Bypass the globally installed Sep-21 wrapper, which intentionally forces
    # acquisition even for fresh manifests; test the Daily core freshness rule.
    request = runtime.DailySnapshotRequest(
        requested_slate_date=fixtures.SLATE, requested_timezone="America/Chicago",
        lanes=["PROPS"],
    )
    result = runtime._run_daily_snapshot_impl(
        request, db=db, market_api=fixtures._Market(), event_api=object(),
    )
    assert len(calls) == 1
    assert calls[0]["include_nfl"] is True
    assert calls[0]["max_candidates"] >= 1
    assert "NFL_PROP_DISCOVERY_FAILED:TimeoutError" in result["blockers"]
    assert result["rows"][0]["identity"]["sport"] == "NFL"
    assert result["rows"][0]["probability_publishable"] is False
    assert result["can_execute"] is False


def test_nonempty_fresh_daily_manifest_does_not_repeat_acquisition(monkeypatch):
    from v17 import test_active_season_phase_a_daily_chain as fixtures
    from v17 import daily_snapshot_runtime as runtime

    db = fixtures._DB([fixtures._snapshot("Current NFL", index=101)])
    monkeypatch.setattr(
        runtime, "acquire_daily_prop_snapshots",
        lambda **_kwargs: (_ for _ in ()).throw(AssertionError("FRESH_MANIFEST_REACQUIRED")),
    )
    request = runtime.DailySnapshotRequest(
        requested_slate_date=fixtures.SLATE, requested_timezone="America/Chicago",
        lanes=["PROPS"],
    )
    result = runtime._run_daily_snapshot_impl(
        request, db=db, market_api=fixtures._Market(), event_api=object(),
    )
    assert len(result["rows"]) == 1
    assert result["can_execute"] is False
