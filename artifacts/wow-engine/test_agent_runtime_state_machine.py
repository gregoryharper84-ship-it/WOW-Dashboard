import pytest

from agent_runtime.state_machine import (
    IllegalTransitionError,
    JOB_STATES,
    JOB_TERMINAL_STATES,
    RUN_STATES,
    RUN_TERMINAL_STATES,
    assert_job_transition,
    assert_run_transition,
    check_job_transition,
    check_run_transition,
)


def test_run_happy_path_is_fully_walkable():
    path = [
        "CREATED", "VALIDATING_REQUEST", "DISCOVERY_QUEUED", "DISCOVERY_RUNNING",
        "ROUTING", "RESEARCH_QUEUED", "RESEARCH_RUNNING",
        "EVIDENCE_QUEUED", "EVIDENCE_RUNNING", "MODELING_QUEUED",
        "MODELING_RUNNING", "AUDIT_QUEUED", "AUDIT_RUNNING", "FINAL_REFRESH",
        "RECONCILING", "COMPLETED",
    ]
    for current, next_state in zip(path, path[1:]):
        assert_run_transition(current, next_state)  # must not raise


def test_run_cancel_and_fail_reachable_from_every_nonterminal_state():
    for state in RUN_STATES - RUN_TERMINAL_STATES:
        assert check_run_transition(state, "CANCELED").allowed
        assert check_run_transition(state, "FAILED").allowed


def test_run_cannot_skip_stages():
    with pytest.raises(IllegalTransitionError):
        assert_run_transition("CREATED", "COMPLETED")


def test_research_barrier_cannot_be_bypassed():
    with pytest.raises(IllegalTransitionError):
        assert_run_transition("ROUTING", "EVIDENCE_QUEUED")


def test_run_terminal_states_have_no_outbound_transitions():
    for state in RUN_TERMINAL_STATES:
        for other in RUN_STATES:
            assert not check_run_transition(state, other).allowed


def test_run_unknown_state_rejected():
    with pytest.raises(IllegalTransitionError):
        assert_run_transition("NOT_A_REAL_STATE", "FAILED")
    with pytest.raises(IllegalTransitionError):
        assert_run_transition("CREATED", "NOT_A_REAL_STATE")


def test_job_queued_to_running_to_succeeded():
    assert_job_transition("QUEUED", "RUNNING")
    assert_job_transition("RUNNING", "SUCCEEDED")


def test_job_retry_cycle():
    assert_job_transition("RUNNING", "RETRY_PENDING")
    assert_job_transition("RETRY_PENDING", "QUEUED")
    assert_job_transition("RETRY_PENDING", "DEAD_LETTERED")


def test_job_cannot_go_from_succeeded_back_to_running():
    with pytest.raises(IllegalTransitionError):
        assert_job_transition("SUCCEEDED", "RUNNING")


def test_job_terminal_states_have_no_outbound_transitions():
    for state in JOB_TERMINAL_STATES:
        for other in JOB_STATES:
            assert not check_job_transition(state, other).allowed


def test_job_terminal_set_excludes_only_the_three_nonterminal_states():
    assert JOB_TERMINAL_STATES == JOB_STATES - {"QUEUED", "RUNNING", "RETRY_PENDING"}
