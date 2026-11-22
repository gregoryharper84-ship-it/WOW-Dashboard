"""RED→GREEN: Action success requires sealed-destination persistence readback."""
from __future__ import annotations

import pytest

from v17.action_persistence_provenance import (
    PERSISTENCE_CLAIMED_RECEIPT_IDS_MISSING,
    PERSISTENCE_DESTINATION_MISMATCH,
    PERSISTENCE_DESTINATION_UNREADABLE,
    PERSISTENCE_READBACK_MISSING,
    PERSISTENCE_READBACK_VERIFIED,
    claimed_receipt_ids_from_auto_advance,
    verify_action_persistence_provenance,
)
from v17.production_release_admission import COMMITTED_APPLICATION_DATABASE_PROJECT_REF

SEALED = COMMITTED_APPLICATION_DATABASE_PROJECT_REF
SEALED_URL = f"https://{SEALED}.supabase.co"
OTHER_URL = "https://aaaaaaaaaaaaaaaaaaaa.supabase.co"
RECEIPT_ID = "c488d535-8599-4848-9b89-9f971e70a33c"


def _store(*ids: str):
    rows = {
        value: {
            "invocation_id": value,
            "route": "/score-pick-request",
            "http_status": 200,
            "can_execute": False,
        }
        for value in ids
    }

    def readback(receipt_id: str):
        return rows.get(receipt_id)

    return readback


def test_red_missing_readback_is_typed_failure_not_success():
    """Scheduled/Action success without durable row must not pass provenance."""
    result = verify_action_persistence_provenance(
        configured_supabase_url=SEALED_URL,
        claimed_receipt_ids=[RECEIPT_ID],
        readback_by_id=_store(),  # empty store
    )
    assert result["passed"] is False
    assert result["persistence_readback_pass"] is False
    assert result["status"] == "BLOCKED_WITH_EXACT_REASON"
    assert result["code"] == PERSISTENCE_READBACK_MISSING
    assert PERSISTENCE_READBACK_MISSING in result["blockers"]
    assert result["missing_receipt_ids"] == [RECEIPT_ID]
    assert result["can_execute"] is False


def test_red_wrong_project_destination_is_typed_failure():
    result = verify_action_persistence_provenance(
        configured_supabase_url=OTHER_URL,
        claimed_receipt_ids=[RECEIPT_ID],
        readback_by_id=_store(RECEIPT_ID),
    )
    assert result["passed"] is False
    assert result["code"] == PERSISTENCE_DESTINATION_MISMATCH
    assert PERSISTENCE_DESTINATION_MISMATCH in result["blockers"]
    assert result["verified_receipt_ids"] == []
    assert result["can_execute"] is False


def test_red_unreadable_or_empty_claim_cannot_pass():
    unreadable = verify_action_persistence_provenance(
        configured_supabase_url="https://example.invalid",
        claimed_receipt_ids=[RECEIPT_ID],
        readback_by_id=_store(RECEIPT_ID),
    )
    empty_claim = verify_action_persistence_provenance(
        configured_supabase_url=SEALED_URL,
        claimed_receipt_ids=[],
        readback_by_id=_store(RECEIPT_ID),
    )
    assert unreadable["code"] == PERSISTENCE_DESTINATION_UNREADABLE
    assert empty_claim["code"] == PERSISTENCE_CLAIMED_RECEIPT_IDS_MISSING
    assert unreadable["passed"] is False
    assert empty_claim["passed"] is False
    assert unreadable["can_execute"] is empty_claim["can_execute"] is False


def test_green_same_destination_independent_readback_passes_without_execution():
    result = verify_action_persistence_provenance(
        configured_supabase_url=SEALED_URL,
        claimed_receipt_ids=[RECEIPT_ID, RECEIPT_ID],
        readback_by_id=_store(RECEIPT_ID),
    )
    assert result["passed"] is True
    assert result["persistence_readback_pass"] is True
    assert result["status"] == PERSISTENCE_READBACK_VERIFIED
    assert result["code"] == PERSISTENCE_READBACK_VERIFIED
    assert result["blockers"] == []
    assert result["verified_receipt_ids"] == [RECEIPT_ID]
    assert result["observed_project_ref"] == SEALED
    assert result["sealed_project_ref"] == SEALED
    assert result["can_execute"] is False


def test_auto_advance_http_artifact_alone_is_not_persistence_proof():
    """HTTP-complete auto-advance receipt still fails without DB readback."""
    artifact = {
        "status": "AUTO_ADVANCE_COMPLETE",
        "code": "AUTO_ADVANCE_RECONCILED",
        "prop_receipts": [
            {
                "ok": True,
                "http_status": 200,
                "batch_key": "wow-scout-test-1:props:1",
                "request_id": "wow-scout-test-1:props:1",
                "body": {"request_id": "wow-scout-test-1:props:1", "can_execute": False},
                "can_execute": False,
            }
        ],
        "team_event_receipts": [],
        "can_execute": False,
    }
    claimed = claimed_receipt_ids_from_auto_advance(artifact)
    assert claimed == ["wow-scout-test-1:props:1"]
    red = verify_action_persistence_provenance(
        configured_supabase_url=SEALED_URL,
        claimed_receipt_ids=claimed,
        readback_by_id=_store(),
    )
    assert red["code"] == PERSISTENCE_READBACK_MISSING
    green = verify_action_persistence_provenance(
        configured_supabase_url=SEALED_URL,
        claimed_receipt_ids=claimed,
        readback_by_id=_store("wow-scout-test-1:props:1"),
    )
    assert green["code"] == PERSISTENCE_READBACK_VERIFIED
    assert green["can_execute"] is False


@pytest.mark.parametrize(
    "mutate,code",
    [
        (lambda r: r.update(configured_supabase_url=OTHER_URL), PERSISTENCE_DESTINATION_MISMATCH),
        (lambda r: r.update(claimed_receipt_ids=["missing-id"]), PERSISTENCE_READBACK_MISSING),
    ],
)
def test_green_counterexample_regresses_to_typed_red(mutate, code):
    kwargs = {
        "configured_supabase_url": SEALED_URL,
        "claimed_receipt_ids": [RECEIPT_ID],
        "readback_by_id": _store(RECEIPT_ID),
    }
    assert verify_action_persistence_provenance(**kwargs)["passed"] is True
    mutate(kwargs)
    result = verify_action_persistence_provenance(**kwargs)
    assert result["passed"] is False
    assert result["code"] == code
    assert result["can_execute"] is False
