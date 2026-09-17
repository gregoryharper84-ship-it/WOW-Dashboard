from __future__ import annotations

import pytest

from v17.nightly_closure_state import is_terminal, require_terminal, transition_allowed, validate_closure


def test_only_fixed_verified_and_hard_blocked_are_terminal() -> None:
    assert is_terminal("FIXED_VERIFIED") is True
    assert is_terminal("HARD_BLOCKED") is True
    for state in ("CI_WAIT", "CI_REWORK", "DEPLOY_REWORK", "ACCEPTANCE_REWORK"):
        assert is_terminal(state) is False


def test_ci_failed_cannot_be_presented_as_terminal_success() -> None:
    payload = {"incident_id": "R1-447", "risk": "R1", "state": "CI_REWORK", "head_sha": "abc", "can_execute": False}
    assert validate_closure(payload) == []
    with pytest.raises(RuntimeError, match="incident is nonterminal: CI_REWORK"):
        require_terminal(payload)


def test_fixed_verified_requires_merge_and_acceptance() -> None:
    errors = validate_closure({"incident_id": "R1-447", "risk": "R1", "state": "FIXED_VERIFIED", "can_execute": False})
    assert "FIXED_VERIFIED requires merge_sha" in errors
    assert "FIXED_VERIFIED requires acceptance_replay" in errors


def test_hard_blocked_requires_exact_blocker_contract() -> None:
    errors = validate_closure({"incident_id": "R1-447", "risk": "R1", "state": "HARD_BLOCKED", "can_execute": False, "blocker": {"stop_reason": "permission denied"}})
    assert any("HARD_BLOCKED missing blocker fields" in item for item in errors)


def test_rework_loops_are_legal_but_terminal_upgrades_are_not() -> None:
    assert transition_allowed("CI_REWORK", "PATCHING") is True
    assert transition_allowed("ACCEPTANCE_REWORK", "PATCHING") is True
    assert transition_allowed("FIXED_VERIFIED", "PATCHING") is False
    assert transition_allowed("HARD_BLOCKED", "CI_WAIT") is False


def test_can_execute_true_is_rejected() -> None:
    errors = validate_closure({"incident_id": "R1-447", "risk": "R1", "state": "CI_REWORK", "can_execute": True})
    assert "can_execute must remain false" in errors
