"""FIX-001/FIX-002: production-router failure and Daily store durability regression.

These are repository integration-boundary tests, NOT Render/Supabase acceptance.
No sporting probabilities are calculated or bets executed here.
"""
from __future__ import annotations

from datetime import datetime, timezone

import pytest
from fastapi import HTTPException

from v17 import cross_sport_winner_discovery as discovery
from v17.daily_response_contract import persist_row_detail


def _inventory():
    event = discovery.DiscoveredEvent(
        sport="NFL",
        league="NFL",
        sport_key="americanfootball_nfl",
        official_event_id="event-001",
        home_team="Alpha",
        away_team="Beta",
        commence_time_utc="2030-10-21T20:00:00+00:00",
        event_status="PREGAME",
        source="TEST_FIX001",
    )
    return discovery.DiscoveryInventory(
        requested_slate_date="2030-10-21",
        requested_timezone="UTC",
        events=[event],
    )


def _route(score_row, resolve_model=lambda event: object()):
    inventory = _inventory()
    rows = discovery.route_discovered_slate(
        inventory,
        resolve_model=resolve_model,
        score_row=score_row,
    )
    assert len(rows) == 1
    assert discovery.reconcile(inventory, rows)["row_reconciliation"] == "PASS"
    return rows[0]


def _valid_package():
    return {
        "candidate_id": "candidate-001",
        "calibrated_probability": 0.7,
        "calibrated_lower_bound": 0.63,
        "calibrated_upper_bound": 0.76,
        "immutable_model_timestamp": "2030-10-21T19:00:00+00:00",
        "calibration_method": "ISOTONIC",
        "calibration_version": "v17",
        "source_snapshot_id": "snapshot-001",
        "source_snapshot_timestamp": "2030-10-21T18:59:00+00:00",
        "outcome_space": "TWO_WAY_WINNER",
        "model_version": "fitted-v17",
        "probability_publishable": True,
        "rank_eligible": True,
        "can_execute": False,
    }


def test_scorer_exception_is_row_local_not_capability_absence():
    def fail(*_args):
        raise TimeoutError("test-only timeout")
    row = _route(fail)
    assert row.bucket == discovery.MODEL_SCORER_FAILED
    assert row.detail["error_type"] == "TimeoutError"
    assert row.rank_eligible is False
    assert row.detail["can_execute"] is False


def test_native_typed_exception_survives_router():
    def fail(*_args):
        raise HTTPException(
            status_code=422,
            detail={"code": "MODEL_INPUTS_INSUFFICIENT", "blockers": ["QB_UNRESOLVED"]},
        )
    row = _route(fail)
    assert row.bucket == discovery.MODEL_INPUTS_INSUFFICIENT
    assert row.detail["blockers"] == ["QB_UNRESOLVED"]
    assert row.detail["can_execute"] is False


@pytest.mark.parametrize(
    ("response", "bucket"),
    [
        (None, discovery.MODEL_SCORER_FAILED),
        ({}, discovery.MODEL_SCORER_FAILED),
        ("invalid", discovery.MODEL_OUTPUT_INVALID),
        ({"probability_publishable": True, "rank_eligible": True},
         discovery.MODEL_OUTPUT_INVALID),
    ],
)
def test_invalid_or_missing_completion_fails_closed(response, bucket):
    row = _route(lambda *_: response)
    assert row.bucket == bucket
    assert row.probability_publishable is False
    assert row.rank_eligible is False


def test_resolver_exception_is_not_false_model_unavailable():
    def fail(_event):
        raise ConnectionError("registry offline")
    row = _route(lambda *_: pytest.fail("must not score"), resolve_model=fail)
    assert row.bucket == discovery.OTHER_GOVERNED_HOLD
    assert row.model_status == "MODEL_RESOLUTION_FAILED"
    assert row.bucket != discovery.MODEL_UNAVAILABLE


def test_valid_sporting_package_survives_missing_downstream_market():
    payload = {**_valid_package(), "market_evidence_status": "MARKET_DATA_UNOBTAINABLE"}
    row = _route(lambda *_: payload)
    assert row.bucket == discovery.MODEL_COMPLETED
    assert row.detail["calibrated_lower_bound"] == 0.63
    assert row.detail["market_evidence_status"] == "MARKET_DATA_UNOBTAINABLE"


class _Result:
    def __init__(self, rows):
        self.data = rows


class _Query:
    def __init__(self, db):
        self.db = db
        self.run_id = None
        self.start = 0
        self.end = 9999
        self.inserting = False

    def upsert(self, rows, **_kwargs):
        self.inserting = True
        self.payload = rows
        return self

    def select(self, *_fields):
        return self

    def eq(self, column, value):
        assert column == "run_id"
        self.run_id = value
        return self

    def order(self, *_args, **_kwargs):
        return self

    def range(self, start, end):
        self.start, self.end = start, end
        return self

    def execute(self):
        if self.inserting:
            rows = self.payload
            if self.db.drop_last:
                rows = rows[:-1]
            for row in rows:
                self.db.stored[(row["run_id"], row["row_index"])] = dict(row)
            return _Result([])
        rows = [
            dict(value)
            for (run_id, _), value in sorted(self.db.stored.items())
            if run_id == self.run_id
        ]
        return _Result(rows[self.start:self.end + 1])


class _DB:
    def __init__(self, *, drop_last=False):
        self.stored = {}
        self.drop_last = drop_last

    def table(self, name):
        assert name == "wow_v17_daily_run_row_detail"
        return _Query(self)


def _row(i):
    return {
        "lane": "MONEYLINE",
        "row_status": "HELD",
        "identity": {"official_event_id": f"event-{i}"},
        "result": {"code": "MODEL_SCORER_FAILED", "can_execute": False},
        "can_execute": False,
    }


def test_full_row_set_readback_verifies_success():
    db = _DB()
    result = persist_row_detail(db, run_id="run-a", rows=[_row(i) for i in range(3)])
    assert result["status"] == "PERSISTED"
    assert result["rows_persisted"] == 3
    assert result["detail_available"] is True
    assert all(row["can_execute"] is False for row in db.stored.values())


def test_silent_partial_write_does_not_advertise_complete_detail():
    db = _DB(drop_last=True)
    result = persist_row_detail(db, run_id="run-b", rows=[_row(i) for i in range(3)])
    assert result["status"] == "PERSISTENCE_RECONCILIATION_FAILED"
    assert result["detail_available"] is False
    assert result["rows_persisted"] == 2
    assert "DAILY_ROW_DETAIL_PERSISTED_SET_MISMATCH" in result["blockers"]
    assert result["can_execute"] is False


def test_persistence_readback_ignores_other_runs():
    db = _DB()
    db.stored[("other-run", 0)] = {"run_id": "other-run", "row_index": 0}
    result = persist_row_detail(db, run_id="run-c", rows=[_row(0)])
    assert result["status"] == "PERSISTED"
    assert result["rows_persisted"] == 1
