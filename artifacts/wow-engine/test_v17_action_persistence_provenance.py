"""RED→GREEN: Action success requires sealed-destination persistence readback."""
from __future__ import annotations

import pytest

from v17.action_persistence_provenance import (
    PERSISTENCE_CLAIMED_RECEIPT_IDS_MISSING,
    PERSISTENCE_DESTINATION_MISMATCH,
    PERSISTENCE_DESTINATION_UNREADABLE,
    PERSISTENCE_READBACK_MISSING,
    PERSISTENCE_READBACK_VERIFIED,
    claimed_receipt_ids_from_async_summary,
    claimed_receipt_ids_from_auto_advance,
    readback_durable_receipt_row,
    require_persistence_readback,
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


def test_require_claimed_ids_false_allows_empty_claims_after_destination_check():
    allowed = verify_action_persistence_provenance(
        configured_supabase_url=SEALED_URL,
        claimed_receipt_ids=[],
        readback_by_id=_store(),
        require_claimed_ids=False,
    )
    assert allowed["passed"] is True
    assert allowed["code"] == PERSISTENCE_READBACK_VERIFIED
    assert allowed["verified_receipt_ids"] == []
    assert allowed["can_execute"] is False

    still_blocked = verify_action_persistence_provenance(
        configured_supabase_url=OTHER_URL,
        claimed_receipt_ids=[],
        readback_by_id=_store(),
        require_claimed_ids=False,
    )
    assert still_blocked["passed"] is False
    assert still_blocked["code"] == PERSISTENCE_DESTINATION_MISMATCH
    assert still_blocked["can_execute"] is False


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


def test_synthetic_batch_key_is_not_a_durable_receipt_claim():
    """Local ordering labels must not poison sealed-destination readback."""
    artifact = {
        "prop_receipts": [],
        "team_event_receipts": [
            {
                "ok": True,
                "http_status": 200,
                "batch_key": "team_events:1",
                "body": {
                    "request_id": "wow-scout-test-1:team_events:1",
                    "can_execute": False,
                },
                "can_execute": False,
            }
        ],
    }
    claimed = claimed_receipt_ids_from_auto_advance(artifact)
    assert claimed == ["wow-scout-test-1:team_events:1"]
    assert "team_events:1" not in claimed

    synthetic_only = claimed_receipt_ids_from_auto_advance(
        {
            "prop_receipts": [],
            "team_event_receipts": [
                {
                    "ok": True,
                    "batch_key": "team_events:1",
                    "body": {"can_execute": False},
                    "can_execute": False,
                }
            ],
        }
    )
    assert synthetic_only == []
    blocked = verify_action_persistence_provenance(
        configured_supabase_url=SEALED_URL,
        claimed_receipt_ids=synthetic_only,
        readback_by_id=_store("team_events:1"),
    )
    assert blocked["code"] == PERSISTENCE_CLAIMED_RECEIPT_IDS_MISSING
    assert blocked["can_execute"] is False


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


def test_async_summary_claims_source_run_and_job_request_ids():
    claimed = claimed_receipt_ids_from_async_summary(
        {
            "source_run_id": "wow-scout-run-1",
            "jobs": [
                {"request_id": "research-1:scout:abc", "can_execute": False},
                {"request_id": "research-1:scout:abc", "can_execute": False},
                {"request_payload": {"request_id": "payload-only"}, "can_execute": False},
            ],
        }
    )
    assert claimed == [
        "wow-scout-run-1",
        "research-1:scout:abc",
        "payload-only",
    ]


class _FakeQuery:
    def __init__(self, rows):
        self._rows = rows

    def select(self, *_args, **_kwargs):
        return self

    def eq(self, column, value):
        self._rows = [row for row in self._rows if row.get(column) == value]
        return self

    def limit(self, *_args, **_kwargs):
        return self

    def execute(self):
        return type("Result", (), {"data": list(self._rows)})()


class _FakeDB:
    def __init__(self, tables):
        self._tables = tables

    def table(self, name):
        return _FakeQuery(list(self._tables.get(name) or []))


def test_readback_durable_receipt_row_accepts_request_id_and_source_run():
    db = _FakeDB(
        {
            "wow_action_invocation_receipts": [
                {"invocation_id": "inv-1", "request_id": "req-header-1", "route": "/score-prop"}
            ],
            "wow_pick_request_runs": [],
            "wow_scout_handoff_jobs": [
                {
                    "job_id": "job-1",
                    "source_run_id": "wow-scout-run-1",
                    "request_id": "research-1:scout:abc",
                    "target_route": "/score-pick-request",
                    "current_state": "MODEL_EVALUATED",
                    "terminal": True,
                }
            ],
        }
    )
    assert readback_durable_receipt_row(db, "req-header-1")["invocation_id"] == "inv-1"
    assert readback_durable_receipt_row(db, "research-1:scout:abc")["job_id"] == "job-1"
    assert readback_durable_receipt_row(db, "wow-scout-run-1")["source_run_id"] == "wow-scout-run-1"
    assert readback_durable_receipt_row(db, "missing") is None


def test_require_persistence_readback_env_gate(monkeypatch):
    monkeypatch.delenv("WOW_REQUIRE_PERSISTENCE_READBACK", raising=False)
    assert require_persistence_readback() is False
    monkeypatch.setenv("WOW_REQUIRE_PERSISTENCE_READBACK", "true")
    assert require_persistence_readback() is True
