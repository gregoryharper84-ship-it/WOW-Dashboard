from types import SimpleNamespace

from v17 import pick_request_durable_job_queue as subject
from v17 import pick_request_run_control as control


class _Query:
    def __init__(self, db, table):
        self.db = db
        self.table = table
        self.mode = "select"
        self.payload = None
        self.filters = {}
        self.limit_n = None

    def select(self, *_args, **_kwargs):
        self.mode = "select"
        return self

    def eq(self, key, value):
        self.filters[key] = value
        return self

    def limit(self, value):
        self.limit_n = value
        return self

    def upsert(self, payload, **_kwargs):
        self.mode = "upsert"
        self.payload = dict(payload)
        return self

    def update(self, payload):
        self.mode = "update"
        self.payload = dict(payload)
        return self

    def execute(self):
        rows = self.db.tables.setdefault(self.table, [])
        if self.mode == "select":
            data = [dict(row) for row in rows if all(row.get(k) == v for k, v in self.filters.items())]
            if self.limit_n is not None:
                data = data[: self.limit_n]
            return SimpleNamespace(data=data)
        if self.mode == "upsert":
            item = dict(self.payload)
            prior = next((row for row in rows if row.get("request_id") == item.get("request_id")), None)
            if prior is None:
                item.setdefault("job_id", "job-1")
                rows.append(item)
                prior = item
            else:
                prior.update(item)
            return SimpleNamespace(data=[dict(prior)])
        matched = [row for row in rows if all(row.get(k) == v for k, v in self.filters.items())]
        for row in matched:
            row.update(self.payload)
        return SimpleNamespace(data=[dict(row) for row in matched])


class _DB:
    def __init__(self):
        self.tables = {}

    def table(self, name):
        return _Query(self, name)


def test_enqueue_returns_without_invoking_scorer(monkeypatch):
    db = _DB()
    scorer_called = {"value": False}

    monkeypatch.setattr(subject.control, "_seed_manifest", lambda _db, _request: None)
    monkeypatch.setattr(
        subject,
        "_ORIGINAL_READ_RUN_STATE",
        lambda _db, request_id, **_kwargs: {
            "request_id": request_id,
            "run": {"run_id": "run-1", "run_status": "RUNNING", "can_execute": False},
            "rows_total": 479,
            "can_execute": False,
        },
    )
    monkeypatch.setattr(subject, "_pending_count", lambda _db, _request_id: 459)

    def scorer(*_args, **_kwargs):
        scorer_called["value"] = True
        raise AssertionError("enqueue must not call the scorer")

    result = subject.enqueue_resumable(
        db,
        control.ResumablePickRunRequest(request_id="board-479", rows=[], batch_size=10),
        score_fn=scorer,
        model_identity=None,
    )

    assert scorer_called["value"] is False
    assert result["rows_manifested"] == 479
    assert result["pending_rows"] == 459
    assert result["job"]["status"] == "QUEUED"
    assert result["next_action"] == "POLL_RUN_STATE"
    assert result["can_execute"] is False


def test_existing_running_job_is_not_requeued(monkeypatch):
    db = _DB()
    db.tables[subject.JOB_TABLE] = [
        {
            "job_id": "job-running",
            "run_id": "run-1",
            "request_id": "board-running",
            "status": "RUNNING",
            "response_mode": "COMPACT",
            "batch_size": 10,
            "consecutive_failures": 0,
            "total_batches_completed": 2,
            "total_rows_attempted": 20,
            "receipt_recovered_count": 1,
            "can_execute": False,
        }
    ]
    monkeypatch.setattr(subject.control, "_seed_manifest", lambda _db, _request: None)
    monkeypatch.setattr(
        subject,
        "_ORIGINAL_READ_RUN_STATE",
        lambda _db, request_id, **_kwargs: {
            "request_id": request_id,
            "run": {"run_id": "run-1", "run_status": "RUNNING", "can_execute": False},
            "rows_total": 100,
            "can_execute": False,
        },
    )
    monkeypatch.setattr(subject, "_pending_count", lambda _db, _request_id: 80)

    result = subject.enqueue_resumable(
        db,
        control.ResumablePickRunRequest(request_id="board-running", rows=[]),
        score_fn=lambda *_args: None,
        model_identity=None,
    )

    assert result["job"]["status"] == "RUNNING"
    assert result["job"]["total_batches_completed"] == 2
    assert result["next_action"] == "POLL_RUN_STATE"


def test_read_run_state_includes_durable_job(monkeypatch):
    db = _DB()
    db.tables[subject.JOB_TABLE] = [
        {
            "job_id": "job-state",
            "run_id": "run-state",
            "request_id": "board-state",
            "status": "RETRY_WAIT",
            "response_mode": "COMPACT",
            "batch_size": 10,
            "consecutive_failures": 1,
            "total_batches_completed": 4,
            "total_rows_attempted": 40,
            "receipt_recovered_count": 2,
            "can_execute": False,
        }
    ]
    monkeypatch.setattr(
        subject,
        "_ORIGINAL_READ_RUN_STATE",
        lambda *_args, **_kwargs: {"request_id": "board-state", "rows_total": 100, "can_execute": False},
    )

    result = subject.read_run_state_with_job(db, "board-state")

    assert result["job"]["status"] == "RETRY_WAIT"
    assert result["job"]["total_rows_attempted"] == 40
    assert result["can_execute"] is False


def test_queue_migration_is_leased_and_fail_closed():
    from pathlib import Path

    sql = (
        Path(__file__).resolve().parents[1]
        / "migrations"
        / "20260921_pick_request_durable_job_queue.sql"
    ).read_text(encoding="utf-8")
    assert "for update of j skip locked" in sql.lower()
    assert "wow_claim_pick_request_job" in sql
    assert "check (can_execute = false)" in sql.lower()
    assert "revoke all on table public.wow_pick_request_jobs from public, anon, authenticated" in sql.lower()
