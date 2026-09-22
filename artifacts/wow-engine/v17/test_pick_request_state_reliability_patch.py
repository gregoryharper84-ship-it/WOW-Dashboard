from v17.pick_request_state_reliability_patch import (
    _manifest_counts,
    _receipt_stage_allowed,
)


def test_manifest_counts_preserve_ingested_pending_rows_before_finalize():
    rows = (
        [{"terminal_status": "COMPLETED", "model_evaluated": True, "stage_seq": 5}] * 6
        + [{"terminal_status": "HELD", "model_evaluated": False, "stage_seq": 0}] * 4
        + [{"terminal_status": "REJECTED", "model_evaluated": False, "stage_seq": 0}] * 6
        + [{"terminal_status": "PENDING", "model_evaluated": False, "stage_seq": 0}] * 2
    )

    counts = _manifest_counts(rows)

    assert counts == {
        "total_rows": 18,
        "completed_rows": 6,
        "held_rows": 4,
        "rejected_rows": 6,
        "pending_rows": 2,
        "unresolved_rows": 2,
    }


def test_governance_audit_cannot_imply_receipt_without_prediction_id():
    record = {
        "model_evaluated": True,
        "stage_seq": 3,
        "prediction_id": None,
    }

    assert _receipt_stage_allowed(record, "MODEL_COMPUTED") is True
    assert _receipt_stage_allowed(record, "RECEIPT_PERSISTED") is False
    assert _receipt_stage_allowed(record, "GOVERNANCE_AUDITED") is False
    assert _receipt_stage_allowed(record, "PUBLICATION_AUTHORIZED") is False


def test_receipt_backed_row_can_advance_to_governance_audit():
    record = {
        "model_evaluated": True,
        "stage_seq": 3,
        "prediction_id": "11111111-1111-1111-1111-111111111111",
    }

    assert _receipt_stage_allowed(record, "RECEIPT_PERSISTED") is True
    assert _receipt_stage_allowed(record, "GOVERNANCE_AUDITED") is True
