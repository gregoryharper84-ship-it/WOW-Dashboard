from datetime import datetime, timedelta, timezone

from fastapi import HTTPException

from v17.daily_snapshot_runtime import DailySnapshotRequest, run_daily_snapshot


FUTURE = (datetime.now(timezone.utc) + timedelta(hours=2)).isoformat()


class Result:
    def __init__(self, data): self.data = data


class Query:
    def __init__(self, data): self.data = data
    def select(self, *_): return self
    def eq(self, *_): return self
    def order(self, *_ , **__): return self
    def limit(self, *_): return self
    def execute(self): return Result(self.data)


class DB:
    def table(self, name):
        if name == "wow_prop_evidence_snapshots":
            return Query([{"source_snapshot_id": "snap-prop", "event_id": "p1", "event_start_time": FUTURE, "sport": "MLB", "player": "P", "stat_type": "strikeouts", "line": 4.5, "hydration_status": "PASS", "blockers": []}])
        return Query([{"official_event_id": "1", "official_date": "2026-09-02", "event_start_time": FUTURE, "home_team": "Home", "away_team": "Away", "venue_name": "Park", "home_probable_pitcher": "H", "away_probable_pitcher": "A", "snapshot_id": "snap-event", "snapshot_timestamp": FUTURE, "feature_hydration_status": "PASS"}])


class Market:
    class ScorePropRequest:
        def __init__(self, **kwargs): self.kwargs = kwargs
    @staticmethod
    def score_prop(*_): raise HTTPException(status_code=422, detail={"code": "PROP_MODEL_NOT_PUBLISHABLE", "probability_publishable": False, "can_execute": False})


class Event: pass


def test_daily_snapshot_returns_one_terminal_receipt_per_selected_row(monkeypatch):
    monkeypatch.setattr("v17.daily_snapshot_runtime.score_team_event_request", lambda *_args, **_kwargs: {"code": "MODEL_UNAVAILABLE", "probability_publishable": False, "can_execute": False})
    result = run_daily_snapshot(DailySnapshotRequest(requested_slate_date="2026-09-02", requested_timezone="America/Chicago"), db=DB(), market_api=Market(), event_api=Event())
    assert result["terminal"] is True
    assert result["run_status"] == "COMPLETED"
    assert len(result["rows"]) == 2
    assert all(row["terminal"] is True and row["can_execute"] is False for row in result["rows"])
    # The PROPS row's score_prop always raises -> HELD; the mocked MONEYLINE
    # row returns normally (no exception) -> COMPLETED. Reconciliation must
    # reflect these real per-row outcomes, not a hardcoded "all completed".
    by_lane = {row["lane"]: row for row in result["rows"]}
    assert by_lane["PROPS"]["row_status"] == "HELD"
    assert by_lane["MONEYLINE"]["row_status"] == "COMPLETED"
    assert result["reconciliation"] == {
        "rows_in": 2, "rows_completed": 1, "rows_held": 1,
        "rows_rejected": 0, "rows_unclassified": 0, "balanced": True,
    }


def test_reconciliation_counts_mixed_completed_and_held_outcomes():
    from v17.daily_snapshot_runtime import _reconcile

    rows = (
        [{"row_status": "COMPLETED"} for _ in range(4)]
        + [{"row_status": "HELD"} for _ in range(2)]
    )
    assert _reconcile(rows) == {
        "rows_in": 6, "rows_completed": 4, "rows_held": 2,
        "rows_rejected": 0, "rows_unclassified": 0, "balanced": True,
    }


def test_reconciliation_all_completed_is_unchanged():
    from v17.daily_snapshot_runtime import _reconcile

    rows = [{"row_status": "COMPLETED"} for _ in range(6)]
    assert _reconcile(rows) == {
        "rows_in": 6, "rows_completed": 6, "rows_held": 0,
        "rows_rejected": 0, "rows_unclassified": 0, "balanced": True,
    }


def test_reconciliation_fails_closed_on_unclassified_row():
    from v17.daily_snapshot_runtime import _reconcile

    rows = [{"row_status": "COMPLETED"}, {"row_status": "SOMETHING_NEW"}]
    result = _reconcile(rows)
    assert result["rows_unclassified"] == 1
    assert result["balanced"] is False


def test_props_row_status_completed_when_any_direction_succeeds():
    from v17.daily_snapshot_runtime import _props_row_status

    outcomes = [
        {"direction": "MORE", "status": "COMPLETED", "payload": {}},
        {"direction": "LESS", "status": "HELD", "payload": {}},
    ]
    assert _props_row_status(outcomes) == "COMPLETED"


def test_props_row_status_held_when_all_directions_held():
    from v17.daily_snapshot_runtime import _props_row_status

    outcomes = [
        {"direction": "MORE", "status": "HELD", "payload": {}},
        {"direction": "LESS", "status": "HELD", "payload": {}},
    ]
    assert _props_row_status(outcomes) == "HELD"


def test_moneyline_row_held_on_exception_not_silently_completed(monkeypatch):
    monkeypatch.setattr(
        "v17.daily_snapshot_runtime.score_team_event_request",
        lambda *_args, **_kwargs: (_ for _ in ()).throw(HTTPException(status_code=409, detail={"code": "MODEL_UNAVAILABLE", "probability_publishable": False, "can_execute": False})),
    )
    result = run_daily_snapshot(
        DailySnapshotRequest(requested_slate_date="2026-09-02", requested_timezone="America/Chicago", lanes=["MONEYLINE"]),
        db=DB(), market_api=Market(), event_api=Event(),
    )
    row = result["rows"][0]
    assert row["row_status"] == "HELD"
    assert result["reconciliation"] == {
        "rows_in": 1, "rows_completed": 0, "rows_held": 1,
        "rows_rejected": 0, "rows_unclassified": 0, "balanced": True,
    }


def test_daily_snapshot_invalid_date_is_terminal_and_non_executable():
    result = run_daily_snapshot(DailySnapshotRequest(requested_slate_date="bad", requested_timezone="UTC"), db=DB(), market_api=Market(), event_api=Event())
    assert result["terminal"] is True
    assert result["run_status"] == "RUN_INVALID_REQUEST"
    assert result["can_execute"] is False
