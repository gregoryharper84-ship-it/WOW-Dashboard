from v17 import pick_request_durable_job_queue as queue
from v17 import pick_request_durable_job_queue_hardening as subject


def test_transient_receipt_failure_moves_job_to_retry_wait_without_scoring(monkeypatch):
    captured = {}

    def fake_retry_or_stop(_db, job, error):
        captured["job"] = dict(job)
        captured["error"] = dict(error)

    monkeypatch.setattr(queue, "_retry_or_stop", fake_retry_or_stop)
    subject._terminal_stop_with_receipt_backoff(
        object(),
        {"job_id": "job-1", "consecutive_failures": 0, "can_execute": False},
        code="PREDICTION_RECEIPT_LOOKUP_FAILED",
        detail={"row_key": "PP-013::MORE"},
    )

    assert captured["error"]["phase"] == "RECEIPT_PREFLIGHT"
    assert captured["error"]["scoring_attempted"] is False
    assert captured["error"]["can_execute"] is False


def test_install_extends_worker_lease_floor_and_preserves_can_execute_false(monkeypatch):
    monkeypatch.setattr(subject, "_INSTALLED", False)
    monkeypatch.setattr(queue, "LEASE_SECONDS", 1800)
    original_terminal = queue._terminal_stop
    subject.install_durable_job_queue_hardening()
    assert queue.LEASE_SECONDS >= 3600
    assert subject.CAN_EXECUTE is False
    queue._terminal_stop = original_terminal
    subject._INSTALLED = False
