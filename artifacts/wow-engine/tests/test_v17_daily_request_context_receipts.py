"""Regression for the Oct 22 dual Daily-run wrong-date incident.

The provenance receipt is metadata only; it must not change event identity,
sporting probabilities, terminal labels, or row reconciliation.
"""
from copy import deepcopy
from types import SimpleNamespace

from v17.daily_response_contract import DAILY_ROW_DETAIL_TABLE, persist_row_detail
from v17.cross_sport_winner_discovery import _slate_date_matches


class _CaptureDB:
    def __init__(self):
        self.payload = None
        self.table_name = None

    def table(self, name):
        self.table_name = name
        return self

    def upsert(self, payload):
        self.payload = payload
        return self

    def execute(self):
        return SimpleNamespace(data=[])


def test_oct22_evening_events_use_chicago_local_date_not_utc_date():
    # Bears kickoff is still October 22 in Chicago despite UTC Oct 23.
    nfl_start = "2026-10-23T00:15:00Z"
    assert _slate_date_matches(nfl_start, "2026-10-22", "America/Chicago")
    assert not _slate_date_matches(nfl_start, "2026-10-23", "America/Chicago")
    # A UTC October 24 start belongs to the local October 23 slate.
    next_day = "2026-10-24T00:00:00Z"
    assert not _slate_date_matches(next_day, "2026-10-22", "America/Chicago")
    assert _slate_date_matches(next_day, "2026-10-23", "America/Chicago")


def test_daily_receipt_captures_requested_date_timezone_and_caller_without_mutating_rows():
    db = _CaptureDB()
    rows = [
        {"lane": "MONEYLINE", "row_status": "HELD",
         "identity": {"official_event_id": "2026_07_NE_CHI"},
         "result": {"bucket": "EVENT_WRONG_DATE"}, "can_execute": False},
        {"lane": "MONEYLINE", "row_status": "COMPLETED",
         "identity": {"official_event_id": "different"},
         "result": {"bucket": "MODEL_COMPLETED"}, "can_execute": False},
    ]
    original = deepcopy(rows)
    context = {
        "request_trace_id": "trace-a",
        "requested_slate_date": "2026-10-22",
        "requested_timezone": "America/Chicago",
        "caller_class": "INTERNAL_AUTOMATION",
        "accepted_at_utc": "2026-10-22T21:45:00+00:00",
        "can_execute": False,
    }
    receipt = persist_row_detail(
        db, run_id="v17-daily-trace-a", rows=rows, request_context=context
    )
    assert receipt["status"] == "PERSISTED"
    assert receipt["rows_persisted"] == 2
    assert db.table_name == DAILY_ROW_DETAIL_TABLE
    assert rows == original
    assert len(db.payload) == 2
    for i, persisted in enumerate(db.payload):
        assert persisted["run_id"] == "v17-daily-trace-a"
        assert persisted["row_index"] == i
        assert persisted["identity"] == original[i]["identity"]
        assert persisted["row_status"] == original[i]["row_status"]
        assert persisted["detail"]["result"] == original[i]["result"]
        assert persisted["detail"]["daily_request_context"] == context
        assert persisted["can_execute"] is False


def test_legacy_persistence_call_preserves_original_detail_shape():
    db = _CaptureDB()
    row = {"lane": "PROPS", "row_status": "HELD", "identity": {}, "can_execute": False}
    receipt = persist_row_detail(db, run_id="existing-caller", rows=[row])
    assert receipt["status"] == "PERSISTED"
    assert db.payload[0]["detail"] == row
    assert "daily_request_context" not in row


def test_no_rows_does_not_invent_evidence_or_call_database():
    db = _CaptureDB()
    result = persist_row_detail(db, run_id="empty", rows=[], request_context={
        "requested_slate_date": "2026-10-22", "requested_timezone": "America/Chicago"
    })
    assert result["status"] == "NO_ROWS"
    assert result["detail_available"] is False
    assert db.payload is None


def test_same_events_different_slate_requests_have_distinct_immutable_run_contexts():
    """Two Daily calls can see the same event while requesting different local days.

    The ledger must retain requested intent per run rather than attributing a
    wrong-date HOLD to an event or to caller intent without provenance.
    """
    shared = [{"lane": "MONEYLINE", "row_status": "HELD",
               "identity": {"official_event_id": "nba-unique-1",
                            "commence_time_utc": "2026-10-24T01:30:00Z"},
               "result": {"bucket": "EVENT_WRONG_DATE"}, "can_execute": False}]
    original = deepcopy(shared)
    receipts = []
    for run_id, slate_date, caller_class in (
        ("v17-daily-a", "2026-10-22", "INTERNAL_AUTOMATION"),
        ("v17-daily-b", "2026-10-23", "V17_DAILY_ACTION"),
    ):
        db = _CaptureDB()
        context = {"request_trace_id": f"trace-{run_id}",
                   "requested_slate_date": slate_date,
                   "requested_timezone": "America/Chicago",
                   "caller_class": caller_class,
                   "accepted_at_utc": "2026-10-23T20:12:55+00:00",
                   "can_execute": False}
        result = persist_row_detail(db, run_id=run_id, rows=shared,
                                    request_context=context)
        assert result["status"] == "PERSISTED"
        assert result["rows_persisted"] == 1
        receipts.append(db.payload[0])
    assert shared == original
    assert receipts[0]["identity"] == receipts[1]["identity"]
    assert receipts[0]["detail"]["result"] == receipts[1]["detail"]["result"]
    assert receipts[0]["detail"]["daily_request_context"]["requested_slate_date"] == "2026-10-22"
    assert receipts[1]["detail"]["daily_request_context"]["requested_slate_date"] == "2026-10-23"
    assert receipts[0]["detail"]["daily_request_context"]["request_trace_id"] != receipts[1]["detail"]["daily_request_context"]["request_trace_id"]
    assert all(row["can_execute"] is False for row in receipts)
