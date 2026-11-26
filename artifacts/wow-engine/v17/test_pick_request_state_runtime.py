from types import SimpleNamespace

import pytest
from fastapi import HTTPException

from pick_request_runtime_core import PickRequestBatch, PickRequestRow
import v17.pick_request_state_runtime as subject


class _Query:
    def __init__(self, db, table):
        self.db = db
        self.table = table
        self.mode = "select"
        self.payload = None
        self.filters = {}

    def select(self, *_args, **_kwargs):
        self.mode = "select"
        return self

    def eq(self, key, value):
        self.filters[key] = value
        return self

    def upsert(self, payload, **_kwargs):
        self.mode = "upsert"
        self.payload = payload
        return self

    def execute(self):
        if self.db.fail_table == self.table and self.mode == "upsert":
            raise TimeoutError("forced persistence outage")
        rows = self.db.tables.setdefault(self.table, [])
        if self.mode == "select":
            data = [
                dict(row)
                for row in rows
                if all(row.get(key) == value for key, value in self.filters.items())
            ]
            return SimpleNamespace(data=data)
        payloads = self.payload if isinstance(self.payload, list) else [self.payload]
        for payload in payloads:
            item = dict(payload)
            if self.table == subject.RUN_TABLE:
                key_fields = ("run_id",)
            elif self.table == subject.ROW_TABLE:
                key_fields = ("run_id", "row_key")
            else:
                key_fields = ("transition_id",)
            match = next(
                (
                    row
                    for row in rows
                    if all(row.get(field) == item.get(field) for field in key_fields)
                ),
                None,
            )
            if match is None:
                rows.append(item)
            else:
                match.update(item)
        return SimpleNamespace(data=[dict(item) for item in payloads])


class _DB:
    def __init__(self):
        self.tables = {}
        self.fail_table = None

    def table(self, name):
        return _Query(self, name)


def _row(index, *, row_key=None, line=None):
    return PickRequestRow(
        row_key=row_key or f"board-row-{index}",
        event_id=f"NFL:20260921:{index}",
        event_start_time="2026-09-22T00:00:00+00:00",
        sport="NFL",
        player=f"Player {index}",
        stat_type="RECEIVING_YARDS",
        line=float(line if line is not None else 20 + index),
        direction="MORE",
        source_type="PASTED_BOARD",
        platform="PRIZEPICKS",
    )


def _batch(request_id, rows):
    return PickRequestBatch(request_id=request_id, response_mode="FULL", rows=rows)


def _held(row_key, code="MODEL_UNAVAILABLE"):
    return {
        "row_key": row_key,
        "terminal_status": "HELD",
        "code": code,
        "model_evaluated": False,
        "probability_publishable": False,
        "rank_eligible": False,
        "can_execute": False,
    }


def _completed(row_key, prediction_id):
    return {
        "row_key": row_key,
        "terminal_status": "COMPLETED",
        "code": "FULL_MODEL",
        "model_evaluated": True,
        "rank_eligible": True,
        "probability_publishable": True,
        "result": {
            "prediction": {
                "prediction_id": prediction_id,
                "calibrated_probability": 0.66,
                "calibrated_probability_lower_bound": 0.59,
            }
        },
        "can_execute": False,
    }


def test_board_manifest_accumulates_all_54_rows_across_small_action_chunks():
    db = _DB()
    store = subject.PickRequestStateStore(db)
    request_id = "board-54-stable-request"
    all_rows = [_row(i) for i in range(54)]

    last_manifest = None
    for start in range(0, 54, 4):
        chunk = all_rows[start : start + 4]
        ctx = store.begin(_batch(request_id, chunk))
        last_manifest = store.finalize(
            ctx,
            [_held(row.row_key, "MODEL_INPUTS_INSUFFICIENT") for row in chunk],
        )

    assert last_manifest is not None
    assert last_manifest["total_rows"] == 54
    assert last_manifest["held_rows"] == 54
    assert last_manifest["pending_rows"] == 0
    assert last_manifest["unresolved_rows"] == 0
    assert last_manifest["can_execute"] is False
    assert len(db.tables[subject.ROW_TABLE]) == 54


def test_completed_prediction_receipt_is_resumable_without_new_model_work():
    db = _DB()
    store = subject.PickRequestStateStore(db)
    batch = _batch("resume-board", [_row(1)])
    first = store.begin(batch)
    outcome = _completed("board-row-1", "pred-1")
    store.record_outcome(first, "board-row-1", outcome)
    store.finalize(first, [outcome])

    second = store.begin(batch)
    cached = second.reusable_outcome("board-row-1")

    assert cached is not None
    assert cached["resumed_from_durable_receipt"] is True
    assert cached["result"]["prediction"]["prediction_id"] == "pred-1"
    assert second.rows["board-row-1"]["retry_count"] == 1
    assert second.rows["board-row-1"]["stage_seq"] >= subject.STAGE_SEQ["RECEIPT_PERSISTED"]
    assert second.rows["board-row-1"]["can_execute"] is False



def test_retry_of_held_row_uses_fresh_inner_scoring_result_not_prior_blocker():
    db = _DB()
    store = subject.PickRequestStateStore(db)
    batch = _batch("retry-held-then-model-recovers", [_row(3)])
    first = store.begin(batch)
    old = _held("board-row-3", "MODEL_INPUTS_INSUFFICIENT")
    store.finalize(first, [old])

    second = store.begin(batch)
    assert second.reusable_outcome("board-row-3") is None
    fresh = _completed("board-row-3", "pred-recovered-3")
    # Some canonical scoring branches return the fresh result without calling
    # the intermediate durable-state hook. An old held outcome is still loaded.
    assert second.rows["board-row-3"]["outcome"]["code"] == "MODEL_INPUTS_INSUFFICIENT"
    picked = subject._current_full_outcomes(second, {"rows": [fresh]})
    assert picked == [fresh]
    manifest = store.finalize(second, picked)
    assert manifest["completed_rows"] == 1
    assert manifest["held_rows"] == 0

    third = store.begin(batch)
    cached = third.reusable_outcome("board-row-3")
    assert cached is not None
    assert cached["result"]["prediction"]["prediction_id"] == "pred-recovered-3"


def test_resumed_receipt_backed_completed_row_cannot_be_overridden_by_inner_result():
    db = _DB()
    store = subject.PickRequestStateStore(db)
    batch = _batch("resume-immutable-completed", [_row(4)])
    first = store.begin(batch)
    scored = _completed("board-row-4", "pred-immutable-4")
    store.finalize(first, [scored])

    second = store.begin(batch)
    cached = second.reusable_outcome("board-row-4")
    assert cached is not None
    # The canonical path should not score a resumed immutable row at all.
    # Reject unexpected results rather than quietly ignoring a second scorer
    # invocation; the existing receipt remains unchanged in durable state.
    with pytest.raises(HTTPException) as error:
        subject._current_full_outcomes(
            second, {"rows": [_held("board-row-4", "MODEL_UNAVAILABLE")]}
        )
    assert error.value.status_code == 502
    assert error.value.detail["unexpected_resumed_row_keys"] == ["board-row-4"]
    assert second.rows["board-row-4"]["outcome"] == scored
    assert second.reusable_outcome("board-row-4")["result"]["prediction"]["prediction_id"] == "pred-immutable-4"
    assert cached["can_execute"] is False


@pytest.mark.parametrize(
    "returned,expected_duplicates,expected_unknown,expected_malformed",
    [
        (
            [_held("board-row-8", "MODEL_UNAVAILABLE"), _completed("board-row-8", "p-8")],
            ["board-row-8"], [], 0,
        ),
        (
            [_held("board-row-8", "MODEL_UNAVAILABLE"), _completed("foreign-row", "p-9")],
            [], ["foreign-row"], 0,
        ),
        (
            [_held("board-row-8", "MODEL_UNAVAILABLE"), "INVALID_OUTCOME"],
            [], [], 1,
        ),
    ],
)
def test_scorer_response_cannot_hide_duplicate_unknown_or_malformed_rows(
    returned, expected_duplicates, expected_unknown, expected_malformed
):
    db = _DB()
    ctx = subject.PickRequestStateStore(db).begin(
        _batch("reject-corrupt-scorer-outcomes", [_row(8)])
    )
    with pytest.raises(HTTPException) as error:
        subject._current_full_outcomes(ctx, {"rows": returned})
    detail = error.value.detail
    assert error.value.status_code == 502
    assert detail["code"] == "PICK_REQUEST_SCORER_ROW_RECONCILIATION_FAILED"
    assert detail["duplicate_row_keys"] == expected_duplicates
    assert detail["unknown_row_keys"] == expected_unknown
    assert detail["malformed_outcomes"] == expected_malformed
    assert detail["probability_publishable"] is False
    assert detail["rank_eligible"] is False
    assert detail["can_execute"] is False


def test_missing_fresh_row_on_retry_does_not_reuse_stale_held_diagnostic():
    db = _DB()
    store = subject.PickRequestStateStore(db)
    batch = _batch("missed-rescore-row", [_row(21)])
    first = store.begin(batch)
    store.finalize(first, [_held("board-row-21", "MODEL_INPUTS_INSUFFICIENT")])
    second = store.begin(batch)
    assert second.reusable_outcome("board-row-21") is None
    with pytest.raises(HTTPException) as excinfo:
        subject._current_full_outcomes(second, {"rows": []})
    assert excinfo.value.status_code == 502
    assert excinfo.value.detail["missing_row_keys"] == ["board-row-21"]
    assert excinfo.value.detail["probability_publishable"] is False
    assert excinfo.value.detail["can_execute"] is False


def test_explicit_resumed_immutable_receipt_needs_no_fresh_scorer_row():
    db = _DB()
    store = subject.PickRequestStateStore(db)
    batch = _batch("resumed-no-fresh", [_row(22)])
    first = store.begin(batch)
    completed = _completed("board-row-22", "p-22")
    store.finalize(first, [completed])
    second = store.begin(batch)
    assert second.reusable_outcome("board-row-22") is not None
    resumed = subject._current_full_outcomes(second, None)
    assert resumed[0]["result"]["prediction"]["prediction_id"] == "p-22"
    assert resumed[0]["resumed_from_durable_receipt"] is True

    with pytest.raises(HTTPException) as excinfo:
        subject._current_full_outcomes(second, {"rows": [_held("board-row-22")]})
    assert excinfo.value.detail["unexpected_resumed_row_keys"] == ["board-row-22"]


def test_unique_matching_inner_scorer_rows_preserve_source_board_order():
    db = _DB()
    ctx = subject.PickRequestStateStore(db).begin(
        _batch("unique-scorer-results", [_row(15), _row(16)])
    )
    result = subject._current_full_outcomes(
        ctx,
        {"rows": [_held("board-row-16", "MODEL_UNAVAILABLE"), _held("board-row-15", "MODEL_INPUTS_INSUFFICIENT")]},
    )
    assert [item["row_key"] for item in result] == ["board-row-15", "board-row-16"]
    assert [item["code"] for item in result] == ["MODEL_INPUTS_INSUFFICIENT", "MODEL_UNAVAILABLE"]


def test_same_request_and_row_key_cannot_mutate_exact_identity():
    db = _DB()
    store = subject.PickRequestStateStore(db)
    store.begin(_batch("identity-lock", [_row(3, line=23.5)]))

    with pytest.raises(HTTPException) as excinfo:
        store.begin(_batch("identity-lock", [_row(3, line=24.5)]))

    assert excinfo.value.status_code == 409
    assert excinfo.value.detail["code"] == "RUN_ROW_IDENTITY_CONFLICT"
    assert excinfo.value.detail["can_execute"] is False


def test_model_computed_without_prediction_receipt_stays_research_only_and_nonpublishable():
    db = _DB()
    store = subject.PickRequestStateStore(db)
    ctx = store.begin(_batch("research-only", [_row(5)]))
    outcome = {
        "row_key": "board-row-5",
        "terminal_status": "COMPLETED",
        "code": "RESEARCH_MODEL_OUTPUT",
        "model_evaluated": True,
        "probability_publishable": False,
        "rank_eligible": False,
        "result": {
            "research_model_output": {
                "calibrated_probability": 0.61,
                "calibrated_probability_lower_bound": 0.53,
            }
        },
        "can_execute": False,
    }

    store.record_outcome(ctx, "board-row-5", outcome)
    record = ctx.rows["board-row-5"]

    assert record["current_stage"] == "MODEL_COMPUTED"
    assert record["durable_status"] == "MODEL_COMPUTED_RESEARCH_ONLY"
    assert record["probability_publishable"] is False
    assert record["rank_eligible"] is False
    assert ctx.reusable_outcome("board-row-5") is None


def test_state_ledger_failure_is_typed_receipt_service_failure_before_acknowledgement():
    db = _DB()
    store = subject.PickRequestStateStore(db)
    ctx = store.begin(_batch("receipt-outage", [_row(7)]))
    db.fail_table = subject.ROW_TABLE
    outcome = _completed("board-row-7", "pred-7")

    with pytest.raises(subject.PickRequestStatePersistenceError) as excinfo:
        store.record_outcome(ctx, "board-row-7", outcome)

    http_error = subject._persistence_error(excinfo.value, request_id=ctx.request_id)
    assert http_error.status_code == 503
    assert http_error.detail["code"] == "RECEIPT_SERVICE_UNREACHABLE"
    assert http_error.detail["probability_publishable"] is False
    assert http_error.detail["rank_eligible"] is False
    assert http_error.detail["can_execute"] is False


def test_distinct_failure_domain_is_stored_without_renaming_terminal_code():
    db = _DB()
    store = subject.PickRequestStateStore(db)
    ctx = store.begin(_batch("failure-domain", [_row(9)]))
    outcome = _held("board-row-9", "MODEL_SCORER_FAILED")

    store.record_outcome(ctx, "board-row-9", outcome)

    record = ctx.rows["board-row-9"]
    assert record["terminal_code"] == "MODEL_SCORER_FAILED"
    assert record["failure_domain"] == "MODEL_SERVICE_UNREACHABLE"
    assert record["can_execute"] is False


def test_explicit_readiness_records_identity_and_hydration_evidence_without_probability():
    db = _DB()
    store = subject.PickRequestStateStore(db)
    ctx = store.begin(_batch("explicit-readiness", [_row(11)]))

    store.record_readiness(
        ctx,
        "board-row-11",
        identity_evidence={
            "event_id": "NFL:20260921:11",
            "player": "Player 11",
            "identity_binding_status": "PASS",
        },
        hydration_evidence={
            "evidence_version": "V17",
            "game_log_n": 10,
            "box_score_log_n": 10,
            "opportunity_status": "PASS",
        },
        feature_snapshot_id="snapshot-11",
        specialist_id="NFL_RECEIVING_YARDS_V17",
    )

    record = ctx.rows["board-row-11"]
    assert record["current_stage"] == "MODEL_INPUTS_READY"
    assert record["identity_verified_explicit"] is True
    assert record["model_inputs_ready_explicit"] is True
    assert record["feature_snapshot_id"] == "snapshot-11"
    assert record["specialist_id"] == "NFL_RECEIVING_YARDS_V17"
    assert record["can_execute"] is False
    assert "probability" not in record["identity_evidence"]
    assert "probability" not in record["hydration_evidence"]

    transitions = db.tables[subject.TRANSITION_TABLE]
    identity = next(item for item in transitions if item["to_stage"] == "IDENTITY_VERIFIED")
    hydration = next(item for item in transitions if item["to_stage"] == "MODEL_INPUTS_READY")
    assert identity["metadata"]["readiness_evidence_explicit"] is True
    assert hydration["metadata"]["readiness_evidence_explicit"] is True


def test_downstream_stage_gap_fill_is_marked_synthetic_not_explicit_readiness():
    db = _DB()
    store = subject.PickRequestStateStore(db)
    ctx = store.begin(_batch("synthetic-gap-fill", [_row(12)]))

    store.advance(ctx, "board-row-12", "MODEL_COMPUTED")

    record = ctx.rows["board-row-12"]
    assert record["identity_verified_explicit"] is False
    assert record["model_inputs_ready_explicit"] is False
    transitions = db.tables[subject.TRANSITION_TABLE]
    readiness = [
        item for item in transitions
        if item["to_stage"] in subject.READINESS_EVIDENCE_STAGES
    ]
    assert len(readiness) == 2
    assert all(item["metadata"]["readiness_evidence_explicit"] is False for item in readiness)
    assert all(item["metadata"]["synthetic_gap_fill"] is True for item in readiness)
    assert all(item["can_execute"] is False for item in readiness)


def test_record_inputs_ready_persists_normalized_identity_and_hydration_evidence():
    db = _DB()
    store = subject.PickRequestStateStore(db)
    row = _row(13)
    row.evidence = SimpleNamespace(
        role_status={
            "identity_binding_status": "PASS",
            "canonical_event_id": "NFL:20260921:13",
            "verified_canonical_event_id": "NFL:20260921:13",
            "provider_event_ids": {"ESPN": "401000013"},
        },
        opportunity_ledger={"status": "PASS"},
        evidence_version="V17_TEST",
        rate_provenance="TEST_ONLY",
        game_log=list(range(10)),
        box_score_log=[{"n": i} for i in range(10)],
    )
    ctx = store.begin(_batch("record-inputs-ready", [row]))
    token = subject._ACTIVE.set(ctx)
    try:
        subject.record_inputs_ready(
            row,
            {
                "event_id": "NFL:20260921:13",
                "sport": "NFL",
                "player": "Player 13",
                "captured_at": "2026-09-21T12:00:00+00:00",
                "role_timestamp": "2026-09-21T11:55:00+00:00",
                "source_timestamps": {"ESPN": "2026-09-21T11:50:00+00:00"},
            },
        )
    finally:
        subject._ACTIVE.reset(token)

    record = ctx.rows["board-row-13"]
    assert record["identity_verified_explicit"] is True
    assert record["model_inputs_ready_explicit"] is True
    assert record["identity_evidence"]["identity_binding_status"] == "PASS"
    assert record["identity_evidence"]["provider_event_ids"] == {"ESPN": "401000013"}
    assert record["hydration_evidence"]["game_log_n"] == 10
    assert record["hydration_evidence"]["box_score_log_n"] == 10
    assert record["hydration_evidence"]["opportunity_status"] == "PASS"
    assert record["can_execute"] is False
