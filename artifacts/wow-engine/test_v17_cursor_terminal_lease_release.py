"""Offline negative tests for fenced, authentic Cursor terminal lease reconciliation."""
from __future__ import annotations

import copy

import pytest

from v17 import cursor_terminal_lease_release as release
from v17 import cursor_terminal_status_receipt as terminal
from v17 import engineering_remote_fallback as remote


PR_START = "b02caa25f30fcff22ebda631b34d06eb2315671d"


def proof():
    return {
        "status": "CURSOR_TERMINAL_STATUS_VERIFIED",
        "terminal_status": "FINISHED",
        "agent_id": terminal.AGENT_ID,
        "run_id": terminal.RUN_ID,
        "latest_run_id": terminal.RUN_ID,
        "repository": remote.REPOSITORY,
        "pr_number": terminal.PR_NUMBER,
        "head_sha": terminal.EXPECTED_HEAD,
        "lease_released": False,
        "can_execute": False,
        "terminal_authority": "V17_TERMINAL_REDUCER",
    }


def lease(*, hold=True, agent=None, run=None, start=PR_START):
    return remote.receipt(
        "CURSOR_AGENT_ENQUEUED",
        provider="cursor", incident_id="2054", lease_group="PR_2090",
        main_sha="a" * 40, hold=hold, worker_id="github-actions:fixture:1",
        assignment_attempts=1, source_pr=2090, pr_number="2090",
        pr_sha=start, cursor_agent_id=agent or terminal.AGENT_ID,
        cursor_run_id=run or terminal.RUN_ID, cursor_run_status="CREATING",
    )


class FakeGitHub:
    def __init__(self, initial=None):
        self.rows = [
            {"id": 6412045537, "body": remote._body(initial or lease()),
             "user": {"login": "github-actions[bot]"}}
        ]
        self.calls = []
        self.commits = [{"sha": PR_START}, {"sha": terminal.EXPECTED_HEAD}]
        self.mutate_before_check = False
        self.patch_response_wrong = False

    def pages(self, path):
        assert path == remote.LEDGER
        return copy.deepcopy(self.rows)

    def request(self, path, payload=None, method=None):
        self.calls.append((path, payload, method))
        if "/pulls/2090/commits?" in path:
            return copy.deepcopy(self.commits)
        if path.endswith("/issues/comments/6412045537"):
            if method == "PATCH":
                if self.patch_response_wrong:
                    return {"id": 1}
                self.rows[0]["body"] = payload["body"]
                return copy.deepcopy(self.rows[0])
            if self.mutate_before_check:
                changed = copy.deepcopy(lease())
                changed["worker_id"] = "unexpected competing edit"
                self.rows[0]["body"] = remote._body(changed)
                self.mutate_before_check = False
            return copy.deepcopy(self.rows[0])
        raise AssertionError(path)

    @property
    def writes(self):
        return [c for c in self.calls if c[2] == "PATCH"]


def test_terminal_finishes_and_exact_reservation_releases_with_readback():
    api = FakeGitHub()
    receipt = release.release_verified_terminal(api, proof())
    assert receipt["status"] == "RELEASED_AND_READBACK_VERIFIED"
    assert receipt["lease_released"] is True
    assert receipt["can_execute"] is False
    assert receipt["terminal_authority"] == "V17_TERMINAL_REDUCER"
    assert len(api.writes) == 1
    assert remote.reservations(api.rows)[0][1]["hold"] is False


def test_repeated_reconciliation_does_not_write_again():
    api = FakeGitHub()
    release.release_verified_terminal(api, proof())
    next_receipt = release.release_verified_terminal(api, proof())
    assert next_receipt["status"] == "ALREADY_RELEASED"
    assert len(api.writes) == 1


@pytest.mark.parametrize("changed", [
    {"status": "CURSOR_TERMINAL_STATUS_UNVERIFIED"},
    {"terminal_status": "RUNNING"},
    {"terminal_status": "CANCELLED"},
    {"agent_id": "bc-99999999-9999-9999-9999-999999999999"},
    {"latest_run_id": "another-run"},
    {"head_sha": "b" * 40},
    {"lease_released": True},
    {"can_execute": True},
])
def test_any_unverified_or_wrong_proof_cannot_mutate_ledger(changed):
    api = FakeGitHub()
    with pytest.raises(release.ReconciliationBlocked, match="TERMINAL_PROOF_IDENTITY_MISMATCH"):
        release.release_verified_terminal(api, {**proof(), **changed})
    assert not api.writes


@pytest.mark.parametrize("row", [
    lease(agent="bc-99999999-9999-9999-9999-999999999999"),
    lease(run="another-run"),
])
def test_wrong_writer_agent_or_run_cannot_clear_other_lease(row):
    api = FakeGitHub(row)
    with pytest.raises(release.ReconciliationBlocked, match="EXACT_CURSOR_RESERVATION_MISSING_OR_DUPLICATE"):
        release.release_verified_terminal(api, proof())
    assert not api.writes


def test_wrong_original_commit_or_head_cannot_release():
    api = FakeGitHub()
    api.commits = [{"sha": "b" * 40}, {"sha": terminal.EXPECTED_HEAD}]
    with pytest.raises(release.ReconciliationBlocked, match="RESERVED_PR_ANCESTRY_MISMATCH"):
        release.release_verified_terminal(api, proof())
    assert not api.writes


def test_other_held_remote_writer_blocks_release():
    api = FakeGitHub()
    extra = remote.receipt(
        "COPILOT_QUEUED", provider="copilot", incident_id="777",
        main_sha="a" * 40, hold=True,
    )
    api.rows.append({"id": 9001, "body": remote._body(extra),
                     "user": {"login": "github-actions[bot]"}})
    with pytest.raises(release.ReconciliationBlocked, match="OTHER_REMOTE_WRITER_PRESENT"):
        release.release_verified_terminal(api, proof())
    assert not api.writes


def test_malformed_comment_or_untrusted_author_blocks_release():
    api = FakeGitHub()
    api.rows[0]["user"] = {"login": "attacker"}
    with pytest.raises(release.ReconciliationBlocked, match="RESERVATION_WRITER_IDENTITY_UNVERIFIED"):
        release.release_verified_terminal(api, proof())
    assert not api.writes


def test_competing_ledger_update_during_reconciliation_blocks_release():
    api = FakeGitHub()
    api.mutate_before_check = True
    with pytest.raises(release.ReconciliationBlocked, match="RESERVATION_CHANGED_DURING_RECONCILIATION"):
        release.release_verified_terminal(api, proof())
    assert not api.writes


def test_bad_patch_ack_is_not_called_verified():
    api = FakeGitHub()
    api.patch_response_wrong = True
    with pytest.raises(release.ReconciliationBlocked, match="RELEASE_WRITE_ACK_INVALID"):
        release.release_verified_terminal(api, proof())


def test_workflow_keeps_readonly_probe_outside_single_writer_mutex():
    from pathlib import Path

    path = Path(__file__).resolve().parents[2] / ".github/workflows/wow-v17-cursor-terminal-status-receipt.yml"
    s = path.read_text()
    before, after = s.split("  reconcile-terminal-writer:\n", 1)
    assert "contents: read\n  pull-requests: read" in before
    assert "issues: write" not in before
    assert "group: wow-v17-cursor-terminal-status-probe-" in before
    assert "cursor_terminal_status_receipt.py" in before
    assert "needs: terminal-status" in after
    assert "group: wow-v17-engineering-implementation-single-writer" in after
    assert "issues: write" in after
    assert "cursor_terminal_lease_release.py" in after
    assert "cancel-in-progress: false" in after
