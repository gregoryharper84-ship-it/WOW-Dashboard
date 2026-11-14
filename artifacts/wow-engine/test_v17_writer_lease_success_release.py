"""Issue #1805: successful engineering workers leave the GLOBAL lease HELD.

The workflow scripts below are executed read-only. They reproduce the current
success path. The ledger in this file is the reference compare-and-swap
protocol the authorized Engineering App must port into a new final step. It is
not wired into the protected workflows here.

can_execute stays false. This module does not mutate GitHub, leases, or models.
"""
from __future__ import annotations

import threading
from dataclasses import dataclass

import pytest
import yaml

from test_v17_worker_heartbeat_failed_run import WORKFLOWS, _run

HELD = "HELD"
RELEASED_ON_SUCCESS = "RELEASED_ON_SUCCESS"
RELEASED_ON_FAILURE = "RELEASED_ON_FAILURE"
CAN_EXECUTE = False

HEARTBEAT_STEP = "Persist dual-stream heartbeat and receipt"
RECEIPT_STEPS = {
    "openai": (
        "Upload dual-stream dispatch receipt",
        "Record team receipt",
    ),
    "anthropic": (
        "Append incident delivery receipt",
        "Upload dual-stream dispatch receipt",
        "Record team receipt",
    ),
}


@dataclass(frozen=True)
class LeaseClaim:
    lease_group: str
    incident_id: str
    lease_epoch: int
    worker_id: str


@dataclass(frozen=True)
class FinalizerPreconditions:
    job_status: str
    code_write_committed: bool = True
    durable_receipt_persisted: bool = True
    queue_handoff_durable: bool = True
    terminal_steps_complete: bool = True


def _held_record(**overrides):
    record = {
        "lease_group": "GLOBAL",
        "incident_id": "1805",
        "lease_epoch": 4,
        "worker_id": "github-actions:38817114851:1",
        "lease_state": HELD,
        "expires_at": "2026-10-16T01:27:20Z",
        "can_execute": CAN_EXECUTE,
    }
    record.update(overrides)
    return record


class WriterLeaseLedger:
    """In-memory atomic lease record. The workflow must use a durable equivalent.

    compare_and_swap changes state only when group, incident, epoch, worker, and
    expected state all match. A repeated release of the same state is idempotent.
    Any other mismatch leaves the record untouched.
    """

    def __init__(self, record: dict):
        self._record = dict(record)
        self._lock = threading.Lock()
        self.api_error: str | None = None
        self.cas_attempts = 0

    def snapshot(self) -> dict:
        with self._lock:
            return dict(self._record)

    def reassign(self, *, worker_id: str, lease_epoch: int, lease_group: str = "GLOBAL") -> None:
        with self._lock:
            self._record["worker_id"] = worker_id
            self._record["lease_epoch"] = lease_epoch
            self._record["lease_group"] = lease_group
            self._record["lease_state"] = HELD

    def compare_and_swap(
        self,
        claim: LeaseClaim,
        *,
        expected_state: str,
        new_state: str,
    ) -> tuple[bool, str]:
        with self._lock:
            self.cas_attempts += 1
            if self.api_error:
                return False, "LEASE_CAS_API_FAILED"
            record = self._record
            same_owner = (
                record["lease_group"] == claim.lease_group
                and record["incident_id"] == claim.incident_id
                and record["lease_epoch"] == claim.lease_epoch
                and record["worker_id"] == claim.worker_id
            )
            if same_owner and record["lease_state"] == new_state:
                return True, "IDEMPOTENT_ALREADY_RELEASED"
            if not same_owner or record["lease_state"] != expected_state:
                return False, "STALE_OR_FOREIGN_LEASE"
            record["lease_state"] = new_state
            record["can_execute"] = CAN_EXECUTE
            return True, new_state


def _blocked(code: str, state: str) -> dict:
    return {"ok": False, "code": code, "lease_state": state, "can_execute": CAN_EXECUTE}


def finalize_writer_lease(
    ledger: WriterLeaseLedger,
    claim: LeaseClaim,
    preconditions: FinalizerPreconditions,
) -> dict:
    """Release one GLOBAL writer lease after the required terminal steps.

    Success release happens only after code write, durable receipt, queue
    handoff, and terminal steps. A heartbeat is not a release. Failure and
    cancellation release as RELEASED_ON_FAILURE and preserve expires_at.
    """
    current = ledger.snapshot()
    if preconditions.job_status == "success":
        if not preconditions.code_write_committed:
            return _blocked("CODE_WRITE_INCOMPLETE", current["lease_state"])
        if not preconditions.durable_receipt_persisted:
            return _blocked("RECEIPT_NOT_DURABLE", current["lease_state"])
        if not preconditions.queue_handoff_durable:
            return _blocked("QUEUE_HANDOFF_INCOMPLETE", current["lease_state"])
        if not preconditions.terminal_steps_complete:
            return _blocked("TERMINAL_STEPS_INCOMPLETE", current["lease_state"])
        new_state = RELEASED_ON_SUCCESS
    elif preconditions.job_status in {"failure", "cancelled"}:
        new_state = RELEASED_ON_FAILURE
    else:
        return _blocked("JOB_STATUS_UNKNOWN", current["lease_state"])

    ok, code = ledger.compare_and_swap(claim, expected_state=HELD, new_state=new_state)
    released = ledger.snapshot()
    return {
        "ok": ok,
        "code": code,
        "lease_state": released["lease_state"],
        "expires_at": released["expires_at"],
        "worker_id": released["worker_id"],
        "lease_epoch": released["lease_epoch"],
        "lease_group": released["lease_group"],
        "can_execute": CAN_EXECUTE,
    }


def record_progress_heartbeat(ledger: WriterLeaseLedger, *, proposed_state: str) -> dict:
    """A heartbeat may report progress. It cannot release or re-hold a lease."""
    current = ledger.snapshot()
    if proposed_state == RELEASED_ON_SUCCESS:
        code = "HEARTBEAT_IS_NOT_THE_FINALIZER"
    elif current["lease_state"] != HELD and proposed_state == HELD:
        code = "HEARTBEAT_CANNOT_REHOLD"
    elif proposed_state == HELD and current["lease_state"] == HELD:
        return {
            "ok": True,
            "code": "HEARTBEAT_PROGRESS_ONLY",
            "lease_state": HELD,
            "can_execute": CAN_EXECUTE,
        }
    else:
        code = "HEARTBEAT_IS_NOT_THE_FINALIZER"
    return _blocked(code, current["lease_state"])


def _owner() -> LeaseClaim:
    return LeaseClaim("GLOBAL", "1805", 4, "github-actions:38817114851:1")


def _step_names(provider: str) -> list[str]:
    document = yaml.safe_load(WORKFLOWS[provider].read_text())
    names = []
    for job in document["jobs"].values():
        for step in job.get("steps", []):
            if step.get("name"):
                names.append(step["name"])
    return names


@pytest.mark.parametrize("provider", sorted(WORKFLOWS))
def test_reproduced_success_leaves_global_lease_held_before_later_steps(provider, tmp_path):
    names = _step_names(provider)
    heartbeat = names.index(HEARTBEAT_STEP)
    for later in RECEIPT_STEPS[provider]:
        assert names.index(later) > heartbeat
    assert "RELEASED_ON_SUCCESS" not in WORKFLOWS[provider].read_text()
    receipt, body = _run(WORKFLOWS[provider], "success", tmp_path)
    assert receipt["job_status"] == "success"
    assert receipt["lease_group"] == "GLOBAL"
    assert receipt["lease_state"] == HELD
    assert receipt["lease_expires_at"] == "2099-01-01T00:00:00Z"
    assert "- lease_state: HELD" in body
    assert receipt["can_execute"] is False
    assert receipt["terminal_authority"] == "V17_TERMINAL_REDUCER"


def test_success_releases_only_after_receipt_and_queue_handoff():
    ledger = WriterLeaseLedger(_held_record())
    result = finalize_writer_lease(ledger, _owner(), FinalizerPreconditions(job_status="success"))
    assert result["ok"] is True
    assert result["code"] == RELEASED_ON_SUCCESS
    assert result["lease_state"] == RELEASED_ON_SUCCESS
    assert result["expires_at"] == "2026-10-16T01:27:20Z"
    assert result["can_execute"] is False
    assert ledger.cas_attempts == 1


@pytest.mark.parametrize(
    ("field", "code"),
    [
        ("code_write_committed", "CODE_WRITE_INCOMPLETE"),
        ("durable_receipt_persisted", "RECEIPT_NOT_DURABLE"),
        ("queue_handoff_durable", "QUEUE_HANDOFF_INCOMPLETE"),
        ("terminal_steps_complete", "TERMINAL_STEPS_INCOMPLETE"),
    ],
)
def test_incomplete_success_preconditions_do_not_release(field, code):
    ledger = WriterLeaseLedger(_held_record())
    result = finalize_writer_lease(
        ledger,
        _owner(),
        FinalizerPreconditions(job_status="success", **{field: False}),
    )
    assert result == _blocked(code, HELD)
    assert ledger.snapshot()["lease_state"] == HELD
    assert ledger.cas_attempts == 0


def test_stale_previous_owner_cannot_release_newer_lease():
    ledger = WriterLeaseLedger(_held_record())
    ledger.reassign(worker_id="github-actions:newer:1", lease_epoch=5)
    stale = finalize_writer_lease(ledger, _owner(), FinalizerPreconditions(job_status="success"))
    assert stale["ok"] is False
    assert stale["code"] == "STALE_OR_FOREIGN_LEASE"
    assert ledger.snapshot()["lease_state"] == HELD
    assert ledger.snapshot()["lease_epoch"] == 5
    assert ledger.snapshot()["worker_id"] == "github-actions:newer:1"
    current = LeaseClaim("GLOBAL", "1805", 5, "github-actions:newer:1")
    released = finalize_writer_lease(ledger, current, FinalizerPreconditions(job_status="success"))
    assert released["code"] == RELEASED_ON_SUCCESS
    assert ledger.snapshot()["worker_id"] == "github-actions:newer:1"
    late = finalize_writer_lease(ledger, _owner(), FinalizerPreconditions(job_status="success"))
    assert late["ok"] is False
    assert ledger.snapshot()["lease_state"] == RELEASED_ON_SUCCESS
    assert ledger.snapshot()["lease_epoch"] == 5


def test_duplicate_success_finalization_is_idempotent():
    ledger = WriterLeaseLedger(_held_record())
    first = finalize_writer_lease(ledger, _owner(), FinalizerPreconditions(job_status="success"))
    second = finalize_writer_lease(ledger, _owner(), FinalizerPreconditions(job_status="success"))
    assert first["code"] == RELEASED_ON_SUCCESS
    assert second["ok"] is True
    assert second["code"] == "IDEMPOTENT_ALREADY_RELEASED"
    assert ledger.snapshot()["lease_epoch"] == 4
    assert ledger.snapshot()["worker_id"] == _owner().worker_id


@pytest.mark.parametrize("job_status", ["failure", "cancelled"])
def test_failure_and_cancellation_release_without_success_label(job_status):
    ledger = WriterLeaseLedger(_held_record())
    result = finalize_writer_lease(
        ledger,
        _owner(),
        FinalizerPreconditions(
            job_status=job_status,
            code_write_committed=False,
            durable_receipt_persisted=False,
            queue_handoff_durable=False,
            terminal_steps_complete=False,
        ),
    )
    assert result["ok"] is True
    assert result["code"] == RELEASED_ON_FAILURE
    assert result["lease_state"] == RELEASED_ON_FAILURE
    assert result["expires_at"] == "2026-10-16T01:27:20Z"
    assert result["can_execute"] is False


def test_failure_finalizer_cannot_upgrade_a_success_release():
    ledger = WriterLeaseLedger(_held_record())
    finalize_writer_lease(ledger, _owner(), FinalizerPreconditions(job_status="success"))
    downgrade = finalize_writer_lease(
        ledger, _owner(), FinalizerPreconditions(job_status="cancelled")
    )
    assert downgrade["ok"] is False
    assert downgrade["code"] == "STALE_OR_FOREIGN_LEASE"
    assert ledger.snapshot()["lease_state"] == RELEASED_ON_SUCCESS


@pytest.mark.parametrize("api_error", ["403", "quota"])
def test_api_and_quota_failures_leave_the_lease_held(api_error):
    ledger = WriterLeaseLedger(_held_record())
    ledger.api_error = api_error
    result = finalize_writer_lease(ledger, _owner(), FinalizerPreconditions(job_status="success"))
    assert result["ok"] is False
    assert result["code"] == "LEASE_CAS_API_FAILED"
    assert ledger.snapshot()["lease_state"] == HELD
    assert ledger.snapshot()["expires_at"] == "2026-10-16T01:27:20Z"


def test_crash_before_finalizer_leaves_the_lease_held():
    ledger = WriterLeaseLedger(_held_record())
    assert ledger.snapshot()["lease_state"] == HELD
    assert ledger.cas_attempts == 0


def test_crash_after_release_rejects_a_late_held_heartbeat():
    ledger = WriterLeaseLedger(_held_record())
    finalize_writer_lease(ledger, _owner(), FinalizerPreconditions(job_status="success"))
    heartbeat = record_progress_heartbeat(ledger, proposed_state=HELD)
    assert heartbeat["ok"] is False
    assert heartbeat["code"] == "HEARTBEAT_CANNOT_REHOLD"
    assert ledger.snapshot()["lease_state"] == RELEASED_ON_SUCCESS


def test_heartbeat_cannot_release_and_progress_does_not_release():
    ledger = WriterLeaseLedger(_held_record())
    progress = record_progress_heartbeat(ledger, proposed_state=HELD)
    assert progress["code"] == "HEARTBEAT_PROGRESS_ONLY"
    assert ledger.snapshot()["lease_state"] == HELD
    refused = record_progress_heartbeat(ledger, proposed_state=RELEASED_ON_SUCCESS)
    assert refused["code"] == "HEARTBEAT_IS_NOT_THE_FINALIZER"
    assert ledger.snapshot()["lease_state"] == HELD
    assert ledger.cas_attempts == 0


@pytest.mark.parametrize(
    "claim",
    [
        LeaseClaim("DOMAIN", "1805", 4, "github-actions:38817114851:1"),
        LeaseClaim("GLOBAL", "1138", 4, "github-actions:38817114851:1"),
        LeaseClaim("GLOBAL", "1805", 3, "github-actions:38817114851:1"),
        LeaseClaim("GLOBAL", "1805", 4, "github-actions:other:1"),
    ],
)
def test_group_incident_epoch_or_worker_mismatch_does_not_release(claim):
    ledger = WriterLeaseLedger(_held_record())
    result = finalize_writer_lease(ledger, claim, FinalizerPreconditions(job_status="success"))
    assert result["ok"] is False
    assert result["code"] == "STALE_OR_FOREIGN_LEASE"
    assert ledger.snapshot() == _held_record()


def test_concurrent_foreign_writer_cannot_release_the_owner_lease():
    ledger = WriterLeaseLedger(_held_record())
    barrier = threading.Barrier(2)
    outcomes = []

    def release(claim):
        barrier.wait()
        outcomes.append(
            finalize_writer_lease(ledger, claim, FinalizerPreconditions(job_status="success"))
        )

    foreign = LeaseClaim("GLOBAL", "1805", 4, "github-actions:foreign:1")
    threads = [
        threading.Thread(target=release, args=(_owner(),)),
        threading.Thread(target=release, args=(foreign,)),
    ]
    for thread in threads:
        thread.start()
    for thread in threads:
        thread.join()
    assert ledger.snapshot()["lease_state"] == RELEASED_ON_SUCCESS
    assert ledger.snapshot()["worker_id"] == _owner().worker_id
    assert ledger.snapshot()["lease_epoch"] == 4
    by_code = {item["code"] for item in outcomes}
    assert RELEASED_ON_SUCCESS in by_code
    assert "STALE_OR_FOREIGN_LEASE" in by_code
    assert all(item["can_execute"] is False for item in outcomes)


def test_unknown_job_status_does_not_release():
    ledger = WriterLeaseLedger(_held_record())
    result = finalize_writer_lease(ledger, _owner(), FinalizerPreconditions(job_status="unknown"))
    assert result["code"] == "JOB_STATUS_UNKNOWN"
    assert ledger.snapshot()["lease_state"] == HELD
    assert ledger.cas_attempts == 0
