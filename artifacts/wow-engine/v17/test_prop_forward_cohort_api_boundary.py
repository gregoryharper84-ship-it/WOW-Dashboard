"""Regressions for the prop forward cohort persistence boundary.

Production emitted ``WOW_PROP_FORWARD_COHORT run_status=FAILED
error_type=APIError`` every 15 minutes from 2026-09-09T18:09Z onward. The
failing call was ``wow_outcomes?prediction_id=in.(...)``: the Supabase edge
rejects the request URI with a plain HTTP 400 once it grows past roughly 630
ids, which supabase-py surfaces only as an untyped ``APIError``.

These tests pin three properties: the ``in_`` filter is chunked so cohort size
can never rebuild that URI, unbounded reads are paged so they cannot silently
truncate at the 1000-row cap, and a boundary failure names the call that failed.
"""
from __future__ import annotations

from datetime import datetime, timezone
from typing import Any

import pytest

# The thesis-dedupe guard monkeypatches these functions into the runtime at
# import time, so the overrides are what production actually executes. Both
# implementations are exercised below; repairing only one leaves production broken.
from v17 import prop_forward_cohort_thesis_dedupe as dedupe
from v17.prop_forward_cohort_runtime import (
    IN_FILTER_CHUNK_SIZE,
    PAGE_SIZE,
    PROVIDER,
    PropForwardCohortBoundaryError,
    _existing_forward_keys,
    _reconcile_rows,
    _settled_prediction_ids,
)
import v17.prop_forward_cohort_runtime as runtime

# Every implementation of the unbounded forward-prediction read that can be
# installed on the runtime. Parametrising over these keeps the override path
# from silently regressing while the base module looks repaired.
FORWARD_PREDICTION_READERS = (
    dedupe.BASE_IMPLEMENTATIONS["_forward_predictions"],
    dedupe._forward_predictions,
)

ELIGIBLE_SNAPSHOT_READERS = (
    dedupe.BASE_IMPLEMENTATIONS["_eligible_snapshots"],
    dedupe._eligible_snapshots,
)

# Measured against the live project: ``prediction_id=in.("<uuid>",...)`` costs
# ~45 URL bytes per id, and the edge starts returning 400 Bad Request at ~28.5 KB.
URL_BYTES_PER_ID = 45
EDGE_URI_REJECT_BYTES = 28_000


class _Query:
    def __init__(self, table: "_Table", rows: list[dict[str, Any]]) -> None:
        self._table = table
        self._rows = rows

    def select(self, *_a, **_k) -> "_Query":
        return self

    def eq(self, *_a, **_k) -> "_Query":
        return self

    def order(self, *_a, **_k) -> "_Query":
        return self

    def limit(self, *_a, **_k) -> "_Query":
        return self

    def gt(self, column: str, value: Any) -> "_Query":
        self._table.client.gt_filters.append((column, value))
        rows = [row for row in self._rows if str(row.get(column) or "") > str(value)]
        return _Query(self._table, rows)

    def in_(self, column: str, values: list[str]) -> "_Query":
        self._table.client.in_filter_sizes.append(len(values))
        rows = [row for row in self._rows if row.get(column) in set(values)]
        return _Query(self._table, rows)

    def range(self, start: int, end: int) -> "_Query":
        return _Query(self._table, self._rows[start:end + 1])

    def execute(self) -> Any:
        if self._table.name in self._table.client.fail_tables:
            raise self._table.client.fail_tables[self._table.name]
        return type("Result", (), {"data": list(self._rows)})()


class _Table:
    def __init__(self, client: "_Client", name: str) -> None:
        self.client = client
        self.name = name

    def _query(self) -> _Query:
        return _Query(self, self.client.tables.get(self.name, []))

    def select(self, *_a, **_k) -> _Query:
        return self._query()


class _Client:
    def __init__(self, tables: dict[str, list[dict[str, Any]]]) -> None:
        self.tables = tables
        self.in_filter_sizes: list[int] = []
        self.gt_filters: list[tuple[str, Any]] = []
        self.fail_tables: dict[str, Exception] = {}

    def table(self, name: str) -> _Table:
        return _Table(self, name)


class _ApiError(Exception):
    """Stands in for postgrest's APIError, which carries no boundary of its own."""


def _prediction_rows(count: int) -> list[dict[str, Any]]:
    return [
        {
            "prediction_id": f"{index:08d}-0000-0000-0000-000000000000",
            "source_snapshot_id": f"snap-{index}",
            "direction": "MORE" if index % 2 == 0 else "LESS",
            "event_start_time": "2026-09-10T00:00:00+00:00",
            "model_timestamp": "2026-09-09T23:00:00+00:00",
            "locked_at": "2026-09-09T23:00:00+00:00",
            "model_provider_identity": PROVIDER,
            "event_id": f"MLB:{index}",
            "player": f"Pitcher {index}",
            "stat_type": "PITCHER_STRIKEOUTS",
            "line": 5.5,
        }
        for index in range(count)
    ]


def test_settled_lookup_never_rebuilds_the_uri_that_the_edge_rejects():
    predictions = _prediction_rows(922)
    client = _Client({"wow_outcomes": []})

    _settled_prediction_ids(client, [row["prediction_id"] for row in predictions])

    assert client.in_filter_sizes, "settled lookup must issue at least one filtered read"
    assert max(client.in_filter_sizes) <= IN_FILTER_CHUNK_SIZE
    worst_case_uri = max(client.in_filter_sizes) * URL_BYTES_PER_ID
    assert worst_case_uri < EDGE_URI_REJECT_BYTES


def test_settled_lookup_returns_every_settled_row_across_chunks():
    predictions = _prediction_rows(500)
    ids = [row["prediction_id"] for row in predictions]
    settled_ids = {ids[0], ids[250], ids[499]}
    outcomes = [
        {
            "prediction_id": prediction_id,
            "actual_stat": 6,
            "settlement_timestamp": "2026-09-10T04:00:00+00:00",
            "void": False,
        }
        for prediction_id in settled_ids
    ]
    client = _Client({"wow_outcomes": outcomes})

    assert _settled_prediction_ids(client, ids) == settled_ids
    assert len(client.in_filter_sizes) > 1, "a 500-id lookup must be chunked"


def test_void_and_ungraded_outcomes_are_still_excluded_after_chunking():
    ids = [row["prediction_id"] for row in _prediction_rows(3)]
    client = _Client({"wow_outcomes": [
        {"prediction_id": ids[0], "actual_stat": 6, "settlement_timestamp": "2026-09-10T04:00:00+00:00", "void": False},
        {"prediction_id": ids[1], "actual_stat": 6, "settlement_timestamp": "2026-09-10T04:00:00+00:00", "void": True},
        {"prediction_id": ids[2], "actual_stat": None, "settlement_timestamp": None, "void": False},
    ]})

    assert _settled_prediction_ids(client, ids) == {ids[0]}


@pytest.mark.parametrize("read_forward_predictions", FORWARD_PREDICTION_READERS)
def test_forward_prediction_reads_page_past_the_thousand_row_cap(read_forward_predictions):
    rows = _prediction_rows(PAGE_SIZE + 137)
    client = _Client({"wow_predictions": rows})

    assert len(read_forward_predictions(client)) == PAGE_SIZE + 137


def test_existing_forward_key_read_pages_past_the_thousand_row_cap():
    client = _Client({"wow_predictions": _prediction_rows(PAGE_SIZE + 137)})

    assert len(_existing_forward_keys(client)) == PAGE_SIZE + 137


@pytest.mark.parametrize("read_forward_predictions", FORWARD_PREDICTION_READERS)
def test_boundary_failure_names_the_failing_call_instead_of_a_bare_api_error(read_forward_predictions):
    client = _Client({"wow_predictions": _prediction_rows(4)})
    client.fail_tables["wow_predictions"] = _ApiError("Bad Request")

    with pytest.raises(PropForwardCohortBoundaryError) as caught:
        read_forward_predictions(client)

    assert caught.value.boundary.startswith("wow_predictions.select_forward")
    assert caught.value.error_type == "_ApiError"


def test_unbalanced_pass_is_not_reported_as_a_completed_run():
    balanced = _reconcile_rows(2, [
        {"status": "CAPTURED_FORWARD"},
        {"status": "CAPTURED_FORWARD"},
        {"status": "SKIPPED_ALREADY_CAPTURED"},
        {"status": "HELD_SCORER"},
    ])
    assert balanced["rows_in"] == 4
    assert balanced["balanced"] is True

    dropped = _reconcile_rows(2, [{"status": "CAPTURED_FORWARD"}])
    assert dropped["balanced"] is False


def _evidence_row(index: int, event_start: str, captured_at: str) -> dict[str, Any]:
    return {
        "source_snapshot_id": f"evidence-{index}",
        "captured_at": captured_at,
        "event_id": f"MLB:{index}",
        "event_start_time": event_start,
        "sport": "MLB",
        "player": f"Pitcher {index}",
        "stat_type": "PITCHER_STRIKEOUTS",
        "line": 5.5,
        "hydration_status": "PASS",
        "blockers": [],
    }


@pytest.mark.parametrize("read_eligible_snapshots", ELIGIBLE_SNAPSHOT_READERS)
def test_settled_history_cannot_crowd_the_future_slate_out_of_the_scan(read_eligible_snapshots):
    now = datetime(2026, 9, 12, 12, 0, tzinfo=timezone.utc)
    history = [
        _evidence_row(index, "2026-09-01T00:00:00+00:00", "2026-08-31T23:00:00+00:00")
        for index in range(5_000)
    ]
    future = _evidence_row(9_001, "2026-09-12T23:10:00+00:00", "2026-09-12T11:00:00+00:00")
    client = _Client({"wow_prop_evidence_snapshots": history + [future]})

    selected = read_eligible_snapshots(client, 10, now=now)

    assert client.gt_filters == [("event_start_time", now.isoformat())]
    assert [row["source_snapshot_id"] for row in selected] == ["evidence-9001"]


@pytest.mark.parametrize("read_eligible_snapshots", ELIGIBLE_SNAPSHOT_READERS)
def test_snapshot_captured_after_first_pitch_is_still_rejected(read_eligible_snapshots):
    now = datetime(2026, 9, 12, 12, 0, tzinfo=timezone.utc)
    late = _evidence_row(1, "2026-09-12T23:10:00+00:00", "2026-09-13T01:00:00+00:00")
    client = _Client({"wow_prop_evidence_snapshots": [late]})

    assert read_eligible_snapshots(client, 10, now=now) == []
