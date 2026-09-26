from types import SimpleNamespace

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
        written = []
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
                prior = item
            else:
                prior.update(item)
            written.append(dict(prior))
        return SimpleNamespace(data=written)


class _DB:
    def __init__(self):
        self.tables = {}

    def table(self, name):
        return _Query(self, name)


def _run(request_id: str) -> dict:
    return {
        "run_id": state._run_id(request_id),
        "request_id": request_id,
        "run_status": "RUNNING",
        "total_rows": 0,
        "completed_rows": 0,
        "held_rows": 0,
        "rejected_rows": 0,
        "pending_rows": 0,
        "unresolved_rows": 0,
        "can_execute": False,
    }


def _pending_row(request_id: str, *, row_key: str = "MLBK-001-LESS") -> dict:
    return {
        "run_id": state._run_id(request_id),
        "row_key": row_key,
        "event_id": "PP-20260926-NYM-WSH",
        "event_start_time": "2026-09-26T16:35:00+00:00",
        "sport": "MLB",
        "player": "Connelly Early",
        "stat_type": "PITCHER_STRIKEOUTS",
        "exact_line": 3.0,
        "direction": "LESS",
        "source_type": "NORMALIZED",
        "platform": "PRIZEPICKS",
        "current_stage": "INGESTED",
        "stage_seq": state.STAGE_SEQ["INGESTED"],
        "terminal_status": "PENDING",
        "terminal_code": None,
        "failure_domain": None,
        "durable_status": "INGESTED",
        "model_evaluated": False,
        "probability_publishable": False,
        "rank_eligible": False,
        "prediction_id": None,
        "source_snapshot_id": None,
        "outcome": None,
        "can_execute": False,
    }


def _immutable_prediction() -> dict:
    return {
        "prediction_id": "97968f99-5996-5fd3-b024-924532d9eebc",
        "created_at": "2026-09-26T16:19:05+00:00",
        "event_id": "PP-20260926-NYM-WSH",
        "event_start_time": "2026-09-26T16:35:00+00:00",
        "model_timestamp": "2026-09-26T16:18:58+00:00",
        "locked_at": "2026-09-26T16:19:05+00:00",
        "player": "Connelly Early",
        "team": "NYM",
        "opponent": "WSH",
        "sport": "MLB",
        "market_type": "PROP",
        "stat_type": "PITCHER_STRIKEOUTS",
        "line": 3.0,
        "direction": "LESS",
        "source_snapshot_id": "11111111-1111-5111-8111-111111111111",
        "raw_model_probability": 0.1362,
        "independent_model_probability": 0.1362,
        "calibrated_probability": 0.3960,
        "calibrated_probability_lower_bound": 0.3875,
        "calibrated_probability_upper_bound": 0.4059,
        "calibration_status": "PASS",
        "calibration_method": "TEST",
        "calibration_version": "TEST",
        # Even if the immutable prediction itself was publishable, the recovered
        # row must stay fail-closed because the companion governed outcome is lost.
        "probability_publishable": True,
        "probability_ceiling": "NO_LOW_PROBABILITY",
        "money_lane_status": "PAYOUT_UNRESOLVED",
        "data_gaps": [],
        "blockers": [],
    }


def test_refresh_run_manifest_counts_exposes_seeded_pending_rows():
    request_id = "board-seed-truth"
    db = _DB()
    db.tables[state.RUN_TABLE] = [_run(request_id)]
    first = _pending_row(request_id, row_key="ROW-1")
    second = _pending_row(request_id, row_key="ROW-2")
    db.tables[state.ROW_TABLE] = [first, second]

    hardening._refresh_run_manifest_counts(db, request_id)

    run = db.tables[state.RUN_TABLE][0]
    assert run["total_rows"] == 2
    assert run["pending_rows"] == 2
    assert run["unresolved_rows"] == 2
    assert run["completed_rows"] == 0
    assert run["held_rows"] == 0
    assert run["run_status"] == "RUNNING"
    assert run["can_execute"] is False


def test_immutable_receipt_with_missing_outcome_recovers_to_nonrankable_hold():
    request_id = "receipt-gap"
    db = _DB()
    db.tables[state.RUN_TABLE] = [_run(request_id)]
    record = _pending_row(request_id)
    db.tables[state.ROW_TABLE] = [record]
    db.tables["wow_predictions"] = [_immutable_prediction()]

    retryable, recovered, blocker = hardening._receipt_preflight_without_rescore(
        db,
        request_id,
        [record],
    )

    assert blocker is None
    assert retryable == []
    assert recovered == 1

    persisted = db.tables[state.ROW_TABLE][0]
    assert persisted["prediction_id"] == "97968f99-5996-5fd3-b024-924532d9eebc"
    assert persisted["terminal_status"] == "HELD"
    assert persisted["terminal_code"] == "RUN_RECEIPT_MATCHED_OUTCOME_RECOVERED_HOLD"
    assert persisted["current_stage"] == "RECEIPT_PERSISTED"
    assert persisted["stage_seq"] == state.STAGE_SEQ["RECEIPT_PERSISTED"]
    assert persisted["model_evaluated"] is True
    assert persisted["probability_publishable"] is False
    assert persisted["rank_eligible"] is False
    assert persisted["outcome"]["probability_publishable"] is False
    assert persisted["outcome"]["rank_eligible"] is False
    assert persisted["can_execute"] is False

    run = db.tables[state.RUN_TABLE][0]
    assert run["pending_rows"] == 0
    assert run["unresolved_rows"] == 0
    assert run["held_rows"] == 1
    assert run["run_status"] == "BLOCKED"
    assert run["can_execute"] is False
