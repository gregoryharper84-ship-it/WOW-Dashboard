from agent_runtime_v1.contracts import JobStatus, TERMINAL_JOB_STATES
from agent_runtime_v1.reducer import reconcile


def test_terminal_job_states_are_explicit():
    assert JobStatus.TIMED_OUT in TERMINAL_JOB_STATES
    assert JobStatus.DEAD_LETTERED in TERMINAL_JOB_STATES
    assert JobStatus.RETRY_PENDING not in TERMINAL_JOB_STATES


def test_reconciliation_zero_survivors_is_valid():
    assert reconcile(3, 0, 1, 2)["balanced"] is True
