from types import SimpleNamespace

from v17.prediction_receipt_lookup_runtime import (
    PredictionReceiptLookupBatch,
    _run_id,
    lookup_prediction_receipts,
)


class _Query:
    def __init__(self, rows):
        self.rows = list(rows)

    def select(self, _fields):
        return self

    def eq(self, field, value):
        self.rows = [row for row in self.rows if row.get(field) == value]
        return self

    def gte(self, field, value):
        self.rows = [row for row in self.rows if str(row.get(field)) >= str(value)]
        return self

    def lt(self, field, value):
        self.rows = [row for row in self.rows if str(row.get(field)) < str(value)]
        return self

    def order(self, field, desc=False):
        self.rows.sort(key=lambda row: str(row.get(field) or ""), reverse=desc)
        return self

    def limit(self, count):
        self.rows = self.rows[:count]
        return self

    def execute(self):
        return SimpleNamespace(data=self.rows)


class _Db:
    def __init__(self, predictions=None, states=None):
        self.predictions = list(predictions or [])
        self.states = list(states or [])

    def table(self, name):
        if name == "wow_predictions":
            return _Query(self.predictions)
        if name == "wow_pick_request_row_states":
            return _Query(self.states)
        raise AssertionError(name)


def _lookup_row(**overrides):
    base = {
        "row_key": "fried_15.5_more",
        "event_id": "401817034",
        "sport": "MLB",
        "player": "Max Fried",
        "stat_type": "1ST_INNING_PITCHES_THROWN",
        "line": 15.5,
        "direction": "MORE",
    }
    base.update(overrides)
    return base


def _state(request_id, **overrides):
    base = {
        "run_id": _run_id(request_id),
        "row_key": "fried_15.5_more",
        "event_id": "401817034",
        "event_start_time": "2026-09-22T23:05:00+00:00",
        "sport": "MLB",
        "player": "Max Fried",
        "stat_type": "1ST_INNING_PITCHES_THROWN",
        "exact_line": 15.5,
        "direction": "MORE",
        "current_stage": "INGESTED",
        "stage_seq": 0,
        "terminal_status": "PENDING",
        "terminal_code": None,
        "failure_domain": None,
        "durable_status": "INGESTED",
        "model_evaluated": False,
        "probability_publishable": False,
        "rank_eligible": False,
        "prediction_id": None,
        "can_execute": False,
        "updated_at": "2026-09-22T21:38:16+00:00",
    }
    base.update(overrides)
    return base


def test_pending_durable_row_is_explicitly_safe_to_resume_with_same_ids():
    request_id = "PP-20260922-162533-1IP"
    batch = PredictionReceiptLookupBatch.model_validate(
        {"request_id": request_id, "rows": [_lookup_row()]}
    )

    result = lookup_prediction_receipts(
        _Db(states=[_state(request_id)]),
        batch,
    )

    row = result["rows"][0]
    assert result["rows_unresolved"] == 1
    assert result["rows_not_found"] == 0
    assert result["reconciliation_pass"] is True
    assert row["status"] == "UNRESOLVED"
    assert row["code"] == "DURABLE_ROW_PENDING_SAFE_TO_RESUME"
    assert row["retry_allowed"] is True
    assert row["resume"] == {
        "request_id": request_id,
        "row_key": "fried_15.5_more",
        "contract": "REUSE_EXACT_BOARD_REQUEST_ID_AND_ROW_KEY",
        "can_execute": False,
    }
    assert row["matches"] == []
    assert row["can_execute"] is False


def test_model_complete_without_prediction_is_not_fabricated_into_receipt():
    request_id = "PP-20260922-162533-1IP"
    state = _state(
        request_id,
        row_key="wheeler_15.5_more",
        player="Zack Wheeler",
        current_stage="MODEL_COMPUTED",
        stage_seq=3,
        terminal_status="COMPLETED",
        terminal_code="MODEL_QUALIFIED_HOLD",
        durable_status="MODEL_COMPUTED_RESEARCH_ONLY",
        model_evaluated=True,
    )
    batch = PredictionReceiptLookupBatch.model_validate(
        {
            "request_id": request_id,
            "rows": [
                _lookup_row(
                    row_key="wheeler_15.5_more",
                    player="Zack Wheeler",
                )
            ],
        }
    )

    result = lookup_prediction_receipts(_Db(states=[state]), batch)

    row = result["rows"][0]
    assert result["rows_durable_terminal"] == 1
    assert row["status"] == "DURABLE_MODEL_COMPLETE"
    assert row["code"] == "DURABLE_MODEL_COMPLETE_NO_PREDICTION_RECEIPT"
    assert row["retry_allowed"] is False
    assert row["matches"] == []
    assert "calibrated_probability" not in row


def test_durable_prediction_id_missing_from_prediction_ledger_is_integrity_blocker():
    request_id = "PP-20260922-162533-1IP"
    state = _state(
        request_id,
        current_stage="RECEIPT_PERSISTED",
        stage_seq=4,
        terminal_status="COMPLETED",
        model_evaluated=True,
        prediction_id="11111111-1111-1111-1111-111111111111",
    )
    batch = PredictionReceiptLookupBatch.model_validate(
        {"request_id": request_id, "rows": [_lookup_row()]}
    )

    result = lookup_prediction_receipts(_Db(states=[state]), batch)

    row = result["rows"][0]
    assert result["rows_blocked"] == 1
    assert row["status"] == "BLOCKED"
    assert row["code"] == "PREDICTION_RECEIPT_INTEGRITY_MISMATCH"
    assert row["retry_allowed"] is False


def test_durable_state_identity_conflict_is_blocked():
    request_id = "PP-20260922-162533-1IP"
    batch = PredictionReceiptLookupBatch.model_validate(
        {"request_id": request_id, "rows": [_lookup_row(line=16.5)]}
    )

    result = lookup_prediction_receipts(
        _Db(states=[_state(request_id)]),
        batch,
    )

    row = result["rows"][0]
    assert row["status"] == "BLOCKED"
    assert row["code"] == "DURABLE_ROW_IDENTITY_CONFLICT"
    assert row["detail"]["conflicting_fields"] == ["line"]


def test_durable_state_accepts_nfl_board_stat_alias_for_same_canonical_stat():
    request_id = "PP-20260924-NFL-BOARD"
    state = _state(
        request_id,
        row_key="love_pass_yards_more",
        event_id="401999001",
        sport="NFL",
        player="Jordan Love",
        stat_type="PASSING_YARDS",
        exact_line=231.5,
        direction="MORE",
    )
    batch = PredictionReceiptLookupBatch.model_validate(
        {
            "request_id": request_id,
            "rows": [
                _lookup_row(
                    row_key="love_pass_yards_more",
                    event_id="401999001",
                    sport="NFL",
                    player="Jordan Love",
                    stat_type="PASS YARDS",
                    line=231.5,
                    direction="MORE",
                )
            ],
        }
    )

    result = lookup_prediction_receipts(_Db(states=[state]), batch)

    row = result["rows"][0]
    assert row["status"] == "UNRESOLVED"
    assert row["code"] == "DURABLE_ROW_PENDING_SAFE_TO_RESUME"
    assert row["retry_allowed"] is True


def test_durable_state_still_blocks_genuinely_different_nfl_stat():
    request_id = "PP-20260924-NFL-BOARD"
    state = _state(
        request_id,
        row_key="love_stat_conflict",
        event_id="401999001",
        sport="NFL",
        player="Jordan Love",
        stat_type="PASSING_YARDS",
        exact_line=231.5,
        direction="MORE",
    )
    batch = PredictionReceiptLookupBatch.model_validate(
        {
            "request_id": request_id,
            "rows": [
                _lookup_row(
                    row_key="love_stat_conflict",
                    event_id="401999001",
                    sport="NFL",
                    player="Jordan Love",
                    stat_type="RUSH YARDS",
                    line=231.5,
                    direction="MORE",
                )
            ],
        }
    )

    result = lookup_prediction_receipts(_Db(states=[state]), batch)

    row = result["rows"][0]
    assert row["status"] == "BLOCKED"
    assert row["code"] == "DURABLE_ROW_IDENTITY_CONFLICT"
    assert row["detail"]["conflicting_fields"] == ["stat_type"]
