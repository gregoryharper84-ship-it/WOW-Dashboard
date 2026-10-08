"""Deterministic admission, lease, safety and independent SIRT-handoff tests."""
from __future__ import annotations

from pathlib import Path

import pytest
import yaml

from v17.engineering_resident_supervisor import (
    GitHubTransport,
    active_engineering_workflow,
    dispatch_once,
    next_approved_issue,
    pending_pr_for_issue,
    supervisor_runtime_status,
)

ISSUE = {
    "issue_number": 1388,
    "severity": "P0",
    "priority_rank": 1,
    "execution_lane": "RAPID",
    "rapid_stream": "LLP_RESTORE",
    "lease_group": "P0_LLP_RESTORE",
    "conflict_keys": ["LLP_USER_PATH"],
}
MANIFEST = {
    "restoration": [ISSUE], "acceleration": [],
    "can_execute": False, "terminal_authority": "V17_TERMINAL_REDUCER",
}


class FakeClient:
    def __init__(self, *, active=False, issue_open=True, open_pr=False):
        self.active = active
        self.issue_open = issue_open
        self.open_pr = open_pr
        self.sent = []

    def get(self, suffix):
        if suffix.startswith("actions/runs?"):
            if self.active and "status=in_progress" in suffix:
                return {"total_count": 1, "workflow_runs": [
                    {"name": "wow-v17-chatgpt-engineering-worker"}
                ]}
            return {"total_count": 0, "workflow_runs": []}
        if suffix == "issues/1388":
            return {"state": "open" if self.issue_open else "closed"}
        if suffix.startswith("pulls?"):
            return [{"body": "Incident: 1388"}] if self.open_pr else []
        raise AssertionError(suffix)

    def dispatch(self, issue):
        self.sent.append(issue)


class FakeRedis:
    def __init__(self, *, locked=False, cooling=False):
        self.locked, self.cooling = locked, cooling
        self.values = []

    def set(self, key, value, *, nx=False, ex=0):
        self.values.append((key, value, nx, ex))
        if nx and self.locked:
            return False
        return True

    def exists(self, key):
        return self.cooling


def test_default_disabled_and_does_not_grant_merge_or_execution(monkeypatch):
    for key in ("WOW_ENGINEERING_RESIDENT_DISPATCH_ENABLED", "WOW_ENGINEERING_GITHUB_TOKEN"):
        monkeypatch.delenv(key, raising=False)
    monkeypatch.setenv("WOW_CAN_EXECUTE", "false")
    monkeypatch.setenv("WOW_DRY_RUN_ONLY", "true")
    status = supervisor_runtime_status()
    assert status["status"] == "DISABLED"
    assert status["can_execute"] is False
    assert status["merge_authority"] is False
    assert status["deploy_authority"] is False


def test_enabled_missing_token_or_bad_governance_blocks(monkeypatch):
    monkeypatch.setenv("WOW_ENGINEERING_RESIDENT_DISPATCH_ENABLED", "1")
    monkeypatch.delenv("WOW_ENGINEERING_GITHUB_TOKEN", raising=False)
    assert supervisor_runtime_status()["status"] == "BLOCKED"
    monkeypatch.setenv("WOW_ENGINEERING_GITHUB_TOKEN", "not-a-real-token")
    monkeypatch.setenv("WOW_CAN_EXECUTE", "true")
    assert supervisor_runtime_status()["status"] == "BLOCKED"
    monkeypatch.setenv("WOW_CAN_EXECUTE", "false")
    monkeypatch.setenv("WOW_DRY_RUN_ONLY", "false")
    assert supervisor_runtime_status()["status"] == "BLOCKED"
    monkeypatch.setenv("WOW_DRY_RUN_ONLY", "true")
    monkeypatch.setenv("REDIS_URL", "redis://example.invalid:6379/0")
    assert supervisor_runtime_status()["status"] == "READY"
    monkeypatch.delenv("REDIS_URL")
    assert supervisor_runtime_status()["status"] == "BLOCKED"


def test_atomic_single_supervisor_dispatch_once():
    client, store = FakeClient(), FakeRedis()
    status = dispatch_once(client, store, MANIFEST)
    assert status == "DISPATCHED_TO_PROTECTED_ENGINEERING_WORKFLOW"
    assert client.sent == [ISSUE]
    assert any(value == "1388" and ex == 900 for _, value, _, ex in store.values)


@pytest.mark.parametrize("locked,cooling,active,open_pr,expected", [
    (True, False, False, False, "ANOTHER_RESIDENT_SUPERVISOR_OWNS_LEASE"),
    (False, True, False, False, "RECENT_DISPATCH_COOLDOWN"),
    (False, False, True, False, "EXISTING_ENGINEERING_WORKFLOW_ACTIVE"),
    (False, False, False, True, "AWAITING_EXISTING_PR_REVIEW_OR_REPAIR"),
])
def test_no_duplicate_dispatch_when_busy(locked, cooling, active, open_pr, expected):
    client = FakeClient(active=active, open_pr=open_pr)
    status = dispatch_once(client, FakeRedis(locked=locked, cooling=cooling), MANIFEST)
    assert status == expected
    assert client.sent == []


def test_empty_queue_never_dispatches():
    client = FakeClient(issue_open=False)
    assert dispatch_once(client, FakeRedis(), MANIFEST) == "NO_APPROVED_OPEN_ENGINEERING_TASK"
    assert not client.sent


def test_github_inventory_incomplete_fails_closed():
    class Incomplete(FakeClient):
        def get(self, suffix):
            if suffix.startswith("actions/runs?"):
                return {"total_count": 101, "workflow_runs": []}
            return super().get(suffix)
    with pytest.raises(RuntimeError, match="INCOMPLETE"):
        active_engineering_workflow(Incomplete())


def test_open_pr_identity_is_not_silent_and_blocks_duplicate():
    assert pending_pr_for_issue(FakeClient(open_pr=True), 1388)
    assert not pending_pr_for_issue(FakeClient(open_pr=False), 1388)


def test_issue_selection_requires_valid_manifest():
    assert next_approved_issue(FakeClient(), MANIFEST)["issue_number"] == 1388
    with pytest.raises(ValueError, match="can_execute=false"):
        next_approved_issue(FakeClient(), {**MANIFEST, "can_execute": True})


def test_transport_rejects_unapproved_paths_and_empty_auth():
    with pytest.raises(ValueError, match="MISSING"):
        GitHubTransport("")
    gh = GitHubTransport("dummy")
    with pytest.raises(ValueError, match="UNAPPROVED"):
        gh.call("/repos/different/repo/issues")


def test_sirt_intake_requires_trusted_source_and_is_never_verification():
    root = Path(__file__).resolve().parents[2]
    data = (root / ".github/workflows/wow-v17-engineering-sirt-intake.yml").read_text()
    workflow = yaml.safe_load(data)
    job = workflow["jobs"]["independent-intake"]
    assert "head_branch == 'main'" in job["if"]
    assert "head_repository.full_name == github.repository" in job["if"]
    script = job["steps"][1]["run"]
    assert "SIRT_REVIEW_REQUIRED" in script
    assert "NOT_YET_PERFORMED" in script
    assert "Merge authorization: NONE" in script
    assert "gh issue create" in script
    assert "gh pr merge" not in script
    assert "gh workflow run" not in script
