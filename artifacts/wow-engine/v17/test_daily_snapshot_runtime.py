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
    assert result["reconciliation"]["balanced"] is True
    assert len(result["rows"]) == 2
    assert all(row["terminal"] is True and row["can_execute"] is False for row in result["rows"])


def test_daily_snapshot_invalid_date_is_terminal_and_non_executable():
    result = run_daily_snapshot(DailySnapshotRequest(requested_slate_date="bad", requested_timezone="UTC"), db=DB(), market_api=Market(), event_api=Event())
    assert result["terminal"] is True
    assert result["run_status"] == "RUN_INVALID_REQUEST"
    assert result["can_execute"] is False
