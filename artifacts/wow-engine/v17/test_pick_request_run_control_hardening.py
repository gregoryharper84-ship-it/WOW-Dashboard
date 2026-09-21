from types import SimpleNamespace

import pytest
from fastapi import HTTPException

from pick_request_runtime_core import PickRequestRow
from v17 import pick_request_run_control as control
from v17 import pick_request_run_control_hardening as hardening
from v17 import pick_request_state_runtime as state


class _Query:
    def __init__(self, db, table):
        self.db = db
        self.table = table
        self.mode = "select"
        self.payload = None
        self.filters = []
        self.limit_n = None

    def select(self, *_args, **_kwargs):
        self.mode = "select"
        return self

    def eq(self, key, value):
        self.filters.append(("eq", key, value))
        return self

    def gte(self, key, value):
        self.filters.append(("gte", key, value))
        return self

    def lt(self, key, value):
        self.filters.append(("lt", key, value))
        return self

    def order(self, *_args, **_kwargs):
        return self

    def limit(self, value):
        self.limit_n = value
        return self

    def upsert(self, payload, **_kwargs):
        self.mode = "upsert"
        self.payload = payload
        return self

    def execute(self):
        rows = self.db.tables.setdefault(self.table, [])
        if self.mode == "select":
            data = []
            for row in rows:
                matched = True
                for op, key, value in self.filters:
                    actual = row.get(key)
                    if op == "eq" and actual != value:
                        matched = False
                    elif op == "gte" and not (actual >= value):
                        matched = False
                    elif op == "lt" and not (actual < value):
                        matched = False
                if matched:
                    data.append(dict(row))
            if self.limit_n is not None:
                data = data[: self.limit_n]
            return SimpleNamespace(data=data)

        payloads = self.payload if isinstance(self.payload, list) else [self.payload]
        for raw in payloads:
            item = dict(raw)
            if self.table == state.RUN_TABLE:
                keys = ("run_id",)
            elif self.table == state.ROW_TABLE:
                keys = ("run_id", "row_key")
            else:
                keys = ("transition_id",)
            prior = next(
                (row for row in rows if all(row.get(key) == item.get(key) for key in keys)),
                None,
            )
            if prior is None:
                rows.append(item)
            else:
                prior.update(item)
        return SimpleNamespace(data=[dict(item) for item in payloads])


class _DB:
    def __init__(self):
        self.tables = {}

    def table(self, name):
        return _Query(self, name)


def _row() -> PickRequestRow:
    return PickRequestRow(
        row_key="PP-013::MORE",
        event_id="401872947",
        event_start_time="2026-09-22T00:15:00+00:00",
        sport="NFL",
        player="Matthew Stafford",
        stat_type="PASS_YARDS",
        line=238.5,
        direction="MORE",
        source_type="NORMALIZED",
        platform="PrizePicks",
    )


def test_receipt_recovery_removes_row_from_retry_set_and_finishes_governance():
    db = _DB()
    run_id = state._run_id("receipt-recovery")
    record = {
        "run_id": run_id,
        "row_key": "PP-013::MORE",
        "event_id": "401872947",
        "event_start_time": "2026-09-22T00:15:00+00:00",
        "sport": "NFL",
        "player": "Matthew Stafford",
        "stat_type": "PASSING_YARDS",
        "exact_line": 238.5,
        "direction": "MORE",
        "source_type": "NORMALIZED",
        "platform": "PRIZEPICKS",
        "current_stage": "MODEL_COMPUTED",
        "stage_seq": state.STAGE_SEQ["MODEL_COMPUTED"],
        "terminal_status": "REJECTED",
        "terminal_code": "NO_LOW_PROBABILITY",
        "model_evaluated": True,
        "probability_publishable": False,
        "rank_eligible": False,
        "prediction_id": None,
        "outcome": {
            "row_key": "PP-013::MORE",
            "terminal_status": "REJECTED",
            "code": "NO_LOW_PROBABILITY",
            "model_evaluated": True,
            "probability_publishable": False,
            "rank_eligible": False,
            "can_execute": False,
        },
        "can_execute": False,
    }
    db.tables[state.ROW_TABLE] = [record]
    db.tables["wow_predictions"] = [
        {
            "prediction_id": "11111111-1111-5111-8111-111111111111",
            "created_at": "2026-09-21T22:00:00+00:00",
            "event_id": "401872947",
            "event_start_time": "2026-09-22T00:15:00+00:00",
            "model_timestamp": "2026-09-21T22:00:00+00:00",
            "locked_at": "2026-09-21T22:01:00+00:00",
            "player": "Matthew Stafford",
            "team": "LAR",
            "opponent": "NYG",
            "sport": "NFL",
            "market_type": "PROP",
            "stat_type": "PASSING_YARDS",
            "line": 238.5,
            "direction": "MORE",
            "source_snapshot_id": "snap-1",
            "raw_model_probability": 0.51,
            "independent_model_probability": 0.51,
            "calibrated_probability": 0.50,
            "calibrated_probability_lower_bound": 0.47,
            "calibrated_probability_upper_bound": 0.53,
            "calibration_status": "PASS",
            "calibration_method": "TEST",
            "calibration_version": "TEST",
            "probability_publishable": False,
            "probability_ceiling": "NO_LOW_PROBABILITY",
            "money_lane_status": "PAYOUT_UNRESOLVED",
            "data_gaps": [],
            "blockers": [],
        }
    ]

    retryable, recovered, blocker = hardening._receipt_preflight_without_rescore(
        db, "receipt-recovery", [record]
    )

    assert blocker is None
    assert recovered == 1
    assert retryable == []
    persisted = db.tables[state.ROW_TABLE][0]
    assert persisted["prediction_id"] == "11111111-1111-5111-8111-111111111111"
    assert persisted["current_stage"] == "GOVERNANCE_AUDITED"
    assert persisted["stage_seq"] == state.STAGE_SEQ["GOVERNANCE_AUDITED"]
    assert persisted["terminal_status"] == "REJECTED"
    assert persisted["can_execute"] is False


def test_closed_run_blocks_manifest_seed_before_any_row_write():
    db = _DB()
    request_id = "closed-board"
    run_id = state._run_id(request_id)
    db.tables[state.RUN_TABLE] = [
        {
            "run_id": run_id,
            "request_id": request_id,
            "run_status": "STOPPED_INFRASTRUCTURE_EXACT_ONCE_PROTECTED",
            "closure_code": "STOPPED_INFRASTRUCTURE_EXACT_ONCE_PROTECTED",
            "closed_at": "2026-09-21T23:00:00+00:00",
            "reopen_allowed": False,
            "can_execute": False,
        }
    ]

    with pytest.raises(HTTPException) as excinfo:
        hardening._closed_safe_seed_manifest(
            db,
            control.ResumablePickRunRequest(request_id=request_id, rows=[_row()]),
        )

    assert excinfo.value.status_code == 409
    assert excinfo.value.detail["code"] == "RUN_CLOSED_REOPEN_REQUIRED"
    assert db.tables.get(state.ROW_TABLE, []) == []
