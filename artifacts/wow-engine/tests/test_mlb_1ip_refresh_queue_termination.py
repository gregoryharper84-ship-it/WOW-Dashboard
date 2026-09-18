"""Reliability regression tests for the MLB 1IP refresh-queue worker.

Covers the two gaps that left rows permanently non-terminal:
(a) rows whose event_start_time had passed were filtered out of the pending
    query, so EXPIRED_PREGAME_WINDOW never fired for them;
(b) a runtime/provider failure went straight to FAILED with next_refresh_at
    NULL, so nothing retried the row and nothing alerted on it.

can_execute and probability_publishable stay false on every path here.
"""
from datetime import datetime, timedelta, timezone
from types import SimpleNamespace

import mlb_1ip_final_refresh_job as job
from prop_auto_hydration import PropAutoHydrationError


NOW = datetime(2026, 9, 18, 18, 0, tzinfo=timezone.utc)


class _Query:
    def __init__(self, table, *, rows=None, count=None):
        self.table = table
        self._rows = rows if rows is not None else []
        self._count = count
        self._update = None
        self._eq = {}

    def select(self, *args, **kwargs):
        self.table.selects.append((args, kwargs))
        return self

    def update(self, payload):
        self._update = payload
        return self

    def eq(self, column, value):
        self._eq[column] = value
        return self

    def or_(self, expression):
        self.table.or_filters.append(expression)
        return self

    def order(self, *_args, **_kwargs):
        return self

    def limit(self, *_args, **_kwargs):
        return self

    def execute(self):
        if self._update is not None:
            self.table.updates.append((self._eq.get("queue_id"), dict(self._update)))
            return SimpleNamespace(data=[], count=None)
        if self._eq.get("status") == "FAILED":
            return SimpleNamespace(data=list(self.table.failed_rows), count=len(self.table.failed_rows))
        return SimpleNamespace(data=list(self._rows), count=self._count)


class _Table:
    def __init__(self, rows, failed_rows=()):
        self.rows = rows
        self.failed_rows = list(failed_rows)
        self.updates = []
        self.selects = []
        self.or_filters = []

    def query(self):
        return _Query(self, rows=self.rows)


class _Client:
    def __init__(self, rows, *, failed_rows=(), artifact=None, artifact_error=None):
        self.table_state = _Table(rows, failed_rows=failed_rows)
        self.artifact = artifact
        self.artifact_error = artifact_error
        self.rpc_calls = 0

    def table(self, name):
        assert name == "wow_mlb_1ip_refresh_queue"
        return self.table_state.query()

    def rpc(self, _name, _params):
        self.rpc_calls += 1
        if self.artifact_error is not None:
            raise self.artifact_error
        return SimpleNamespace(execute=lambda: SimpleNamespace(data=self.artifact))


def _row(**overrides):
    row = {
        "queue_id": "queue-1",
        "row_key": "row-1",
        "event_id": "MLB:1",
        "player": "Test Pitcher",
        "starter_name_at_capture": "Test Pitcher",
        "event_start_time": (NOW + timedelta(hours=3)).isoformat(),
        "line": 15.5,
        "direction": "MORE",
        "money_lane_status": "PAYOUT_UNRESOLVED",
        "status": "WAITING_FOR_OFFICIAL_LINEUP",
        "next_refresh_at": (NOW - timedelta(minutes=5)).isoformat(),
        "refresh_attempts": 0,
    }
    row.update(overrides)
    return row


def _never_called(**_kwargs):
    raise AssertionError("hydrator must not run for a past-event row")


def _raising(**_kwargs):
    raise PropAutoHydrationError("MLB_GAME_LOG_INVALID", "inningsPitched missing")


def test_past_event_row_terminates_as_expired_pregame_window():
    """(a) A row whose event has concluded reaches a terminal state."""
    stale = _row(
        queue_id="stale-1",
        event_start_time=(NOW - timedelta(days=14)).isoformat(),
        next_refresh_at=(NOW - timedelta(days=14)).isoformat(),
        refresh_attempts=10,
    )
    client = _Client([stale])

    counters = job.run_once(client=client, now=NOW, hydrator=_never_called)

    assert counters["expired"] == 1
    assert counters["expired_stale"] == 1
    queue_id, update = client.table_state.updates[0]
    assert queue_id == "stale-1"
    assert update["status"] == "EXPIRED_PREGAME_WINDOW"
    assert update["next_refresh_at"] is None
    assert update["refresh_attempts"] == 11
    assert update["probability_publishable"] is False
    assert update["can_execute"] is False
    # A past-event row must never be scored, so no artifact is resolved for it.
    assert client.rpc_calls == 1


def test_pending_query_honours_next_refresh_at_backoff():
    client = _Client([_row()])
    job.run_once(client=client, now=NOW, hydrator=lambda **_kwargs: {"official_lineup_status": "PROJECTED", "starter_name": "Test Pitcher"})
    assert client.table_state.or_filters == [
        f"next_refresh_at.is.null,next_refresh_at.lte.{job._pg_timestamp(NOW)}"
    ]


def test_runtime_error_retries_with_backoff_while_pregame_window_open():
    """(b) A provider failure is retried instead of parked forever."""
    client = _Client([_row(refresh_attempts=1)])

    counters = job.run_once(client=client, now=NOW, hydrator=_raising)

    assert counters["retry_scheduled"] == 1
    assert counters["failed"] == 0
    assert counters["dead_lettered"] == 0
    _, update = client.table_state.updates[0]
    assert update["status"] == "WAITING_FOR_OFFICIAL_LINEUP"
    # attempts==2 -> 60 * 2**1 seconds of backoff.
    assert update["next_refresh_at"] == (NOW + timedelta(seconds=120)).isoformat()
    # The typed hydration code is preserved, never collapsed.
    assert update["last_error_code"] == "REFRESH_RUNTIME_ERROR:PropAutoHydrationError:MLB_GAME_LOG_INVALID"
    assert update["can_execute"] is False


def test_runtime_error_dead_letters_once_retry_budget_is_spent(caplog):
    client = _Client([_row(refresh_attempts=job.MAX_REFRESH_ATTEMPTS)])

    with caplog.at_level("ERROR"):
        counters = job.run_once(client=client, now=NOW, hydrator=_raising)

    assert counters["dead_lettered"] == 1
    assert counters["failed"] == 1
    _, update = client.table_state.updates[0]
    assert update["status"] == "FAILED"
    assert update["next_refresh_at"] is None
    assert update["last_error_code"].startswith("REFRESH_DEAD_LETTER:REFRESH_RUNTIME_ERROR:")
    assert "WOW_MLB_1IP_REFRESH_DEAD_LETTER" in caplog.text
    assert update["can_execute"] is False


def test_failure_after_first_pitch_dead_letters_instead_of_retrying(caplog):
    """A closed pregame window can never be retried, whatever the budget."""
    base_update = {}
    row = _row(event_start_time=(NOW - timedelta(minutes=1)).isoformat(), refresh_attempts=0)

    with caplog.at_level("ERROR"):
        retried = job._apply_failure(
            base_update, row=row, attempts=1, ts=NOW, code="REFRESH_RUNTIME_ERROR:RuntimeError"
        )

    assert retried is False
    assert base_update["status"] == "FAILED"
    assert base_update["next_refresh_at"] is None
    assert base_update["last_error_code"].startswith("REFRESH_DEAD_LETTER:")
    assert "WOW_MLB_1IP_REFRESH_DEAD_LETTER" in caplog.text


def test_artifact_resolution_failure_retries_then_dead_letters():
    ready = {"official_lineup_status": "CONFIRMED", "starter_name": "Test Pitcher"}
    retryable = _Client([_row(refresh_attempts=0)], artifact_error=RuntimeError("registry down"))
    counters = job.run_once(client=retryable, now=NOW, hydrator=lambda **_kwargs: ready)
    assert counters["retry_scheduled"] == 1
    _, update = retryable.table_state.updates[0]
    assert update["status"] == "WAITING_FOR_OFFICIAL_LINEUP"
    assert update["last_error_code"].startswith("REFRESH_ARTIFACT_RESOLUTION_ERROR:")

    spent = _Client(
        [_row(refresh_attempts=job.MAX_REFRESH_ATTEMPTS)],
        artifact_error=RuntimeError("registry down"),
    )
    counters = job.run_once(client=spent, now=NOW, hydrator=lambda **_kwargs: ready)
    assert counters["dead_lettered"] == 1
    _, update = spent.table_state.updates[0]
    assert update["status"] == "FAILED"
    assert update["last_error_code"].startswith("REFRESH_DEAD_LETTER:REFRESH_ARTIFACT_RESOLUTION_ERROR:")


def test_existing_failed_backlog_is_alerted_every_pass(caplog):
    parked = {
        "queue_id": "parked-1",
        "event_id": "MLB:823576",
        "player": "Robert Stock",
        "last_error_code": "REFRESH_RUNTIME_ERROR:PropAutoHydrationError",
    }
    client = _Client([], failed_rows=[parked])

    with caplog.at_level("ERROR"):
        counters = job.run_once(client=client, now=NOW)

    assert counters["dead_letter_backlog"] == 1
    assert "WOW_MLB_1IP_REFRESH_DEAD_LETTER_BACKLOG" in caplog.text
    assert "parked-1" in caplog.text


def test_backoff_is_bounded():
    assert job._retry_backoff_seconds(1) == job.RETRY_BACKOFF_BASE_SECONDS
    assert job._retry_backoff_seconds(3) == 240
    assert job._retry_backoff_seconds(50) == job.RETRY_BACKOFF_MAX_SECONDS
    assert job.CAN_EXECUTE is False
