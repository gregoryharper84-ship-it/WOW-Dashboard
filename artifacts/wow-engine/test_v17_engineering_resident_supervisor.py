"""Deterministic admission, lease, safety and independent SIRT-handoff tests."""
from __future__ import annotations

from pathlib import Path

import pytest
import yaml

from v17.engineering_resident_supervisor import (
    ATTEMPT_KEY_PREFIX,
    GitHubTransport,
    active_engineering_workflow,
    dispatch_once,
    next_approved_issue,
    pending_pr_for_issue,
    persist_resident_heartbeat,
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
SECOND_ISSUE = {
    **ISSUE, "issue_number": 823, "severity": "P1", "priority_rank": 2, "lease_group": "GLOBAL",
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
        if suffix in ("issues/1388", "issues/823"):
            return {"state": "open" if self.issue_open else "closed"}
        if suffix.startswith("pulls?"):
            return [{"body": "Incident: 1388"}] if self.open_pr else []
        raise AssertionError(suffix)

    def dispatch(self, issue):
        self.sent.append(issue)


class FakeRedis:
    def __init__(self, *, locked=False, cooling=False, attempts=0):
        self.locked, self.cooling = locked, cooling
        self.attempts = attempts
        self.attempts_by_key = {}
        self.values = []

    def set(self, key, value, *, nx=False, ex=0):
        self.values.append((key, value, nx, ex))
        if nx and self.locked:
            return False
        return True

    def exists(self, key):
        return self.cooling

    def get(self, key):
        return str(self.attempts_by_key.get(key, self.attempts))

    def incr(self, key):
        value = int(self.get(key)) + 1
        self.attempts_by_key[key] = value
        self.attempts = value
        return value

    def expire(self, key, seconds):
        assert seconds == 86400
        return True


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
    assert supervisor_runtime_status()["status"] == "BLOCKED"  # No durable receipt = no dispatch.
    monkeypatch.setenv("SUPABASE_URL", "https://example.invalid")
    monkeypatch.setenv("SUPABASE_SERVICE_ROLE_KEY", "test-fixture-not-a-secret")
    assert supervisor_runtime_status()["status"] == "CONFIGURED_UNVERIFIED"
    monkeypatch.delenv("SUPABASE_SERVICE_ROLE_KEY")
    monkeypatch.delenv("SUPABASE_SERVICE_KEY", raising=False)
    assert supervisor_runtime_status()["status"] == "BLOCKED"
    monkeypatch.delenv("REDIS_URL")
    assert supervisor_runtime_status()["status"] == "BLOCKED"


def test_atomic_single_supervisor_dispatch_once():
    client, store = FakeClient(), FakeRedis()
    status = dispatch_once(client, store, MANIFEST)
    assert status == "DISPATCHED_TO_PROTECTED_ENGINEERING_WORKFLOW"
    assert client.sent == [ISSUE]
    assert store.attempts == 1
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


def test_repeated_failed_dispatch_is_bounded():
    client = FakeClient()
    store = FakeRedis(attempts=3)
    assert dispatch_once(client, store, MANIFEST) == "DISPATCH_ATTEMPT_CAP_REQUIRES_TRIAGE"
    assert client.sent == []


def test_existing_pr_cannot_starve_next_approved_p0_incident():
    manifest = {**MANIFEST, "restoration": [ISSUE, SECOND_ISSUE]}
    client, store = FakeClient(open_pr=True), FakeRedis()
    assert dispatch_once(client, store, manifest) == "DISPATCHED_TO_PROTECTED_ENGINEERING_WORKFLOW"
    assert client.sent == [SECOND_ISSUE]
    assert store.attempts == 1


def test_retry_cap_cannot_starve_next_approved_p0_incident():
    manifest = {**MANIFEST, "restoration": [ISSUE, SECOND_ISSUE]}
    client, store = FakeClient(), FakeRedis()
    store.attempts_by_key[ATTEMPT_KEY_PREFIX + "1388"] = 3
    assert dispatch_once(client, store, manifest) == "DISPATCHED_TO_PROTECTED_ENGINEERING_WORKFLOW"
    assert client.sent == [SECOND_ISSUE]
    assert store.attempts_by_key[ATTEMPT_KEY_PREFIX + "1388"] == 3


def test_all_pending_prs_hold_without_duplicate_worker():
    manifest = {**MANIFEST, "restoration": [ISSUE]}
    client = FakeClient(open_pr=True)
    assert dispatch_once(client, FakeRedis(), manifest) == "AWAITING_EXISTING_PR_REVIEW_OR_REPAIR"
    assert client.sent == []


def test_p1_dispatch_carries_exact_selected_issue_not_generic_worker(monkeypatch):
    client = GitHubTransport("placeholder")
    calls = []
    monkeypatch.setattr(client, "call", lambda path, payload: calls.append((path, payload)))
    client.dispatch(SECOND_ISSUE)
    assert calls[0][1]["inputs"]["target_incident"] == "823"
    assert calls[0][1]["inputs"]["lease_group"] == "GLOBAL"


def test_p1_cannot_request_separate_mutation_domain(monkeypatch):
    client = GitHubTransport("placeholder")
    monkeypatch.setattr(client, "call", lambda *_: pytest.fail("unsafe API call"))
    with pytest.raises(ValueError, match="STANDARD_TARGET_MUST_USE_GLOBAL_LEASE"):
        client.dispatch({**SECOND_ISSUE, "lease_group": "P1_UNREVIEWED"})


def test_both_workers_validate_exact_p1_route_instead_of_generic_queue():
    root = Path(__file__).resolve().parents[2]
    for workflow_name in (
        "wow-v17-chatgpt-engineering-worker.yml",
        "wow-v17-claude-engineering-worker.yml",
    ):
        content = (root / ".github/workflows" / workflow_name).read_text()
        assert 'TARGET_INCIDENT_NOT_SUPPORTED:' in content
        assert '[ "$severity" = "P1" ] && [ "$lane" = "STANDARD" ]' in content
        assert "lease_group=GLOBAL" in content
        assert 'TARGET_INCIDENT_LEASE_MISMATCH:' in content
    provider = (root / ".github/workflows/wow-v17-engineering-provider-dispatcher.yml").read_text()
    assert 'TARGET_INCIDENT_INVALID' in provider
    assert 'TARGET_INCIDENT_REQUIRES_DOMAIN_LEASE' not in provider


def test_distinct_resident_heartbeat_is_durable_and_not_self_approval():
    class Store:
        def __init__(self):
            self.rows = []
        def table(self, name):
            assert name == "wow_engineering_auditor_runtime"
            return self
        def upsert(self, payload, *, on_conflict):
            assert on_conflict == "auditor_id"
            self.rows.append(payload)
            return self
        def execute(self):
            return {"error": None}

    store = Store()
    persist_resident_heartbeat(
        store, status="RUNNING", outcome="NO_APPROVED_OPEN_ENGINEERING_TASK",
        instance_id="test-instance:1",
    )
    row = store.rows[0]
    assert row["auditor_id"] == "WOW_ENGINEERING_RESIDENT_DISPATCHER"
    assert row["auditor_id"] != "WOW_ENGINEERING_AUDITOR"
    assert row["status"] == "RUNNING"
    assert row["last_error_code"] == "NO_APPROVED_OPEN_ENGINEERING_TASK"
    assert row["can_execute"] is False
    assert row["terminal_authority"] == "V17_TERMINAL_REDUCER"
    assert row["last_heartbeat_at"].endswith("+00:00")
    assert "token" not in repr(row).lower()


@pytest.mark.parametrize("status,outcome", [
    ("PASSED", "CYCLE_STARTED"),
    ("RUNNING", "BAD:UNTRUSTED"),
    ("RUNNING", "X" * 121),
])
def test_heartbeat_receipt_rejects_invalid_status_and_untrusted_outcome(status, outcome):
    class NoWrite:
        def table(self, name):
            raise AssertionError("Should reject before writing")
    with pytest.raises(ValueError, match="RESIDENT_HEARTBEAT"):
        persist_resident_heartbeat(
            NoWrite(), status=status, outcome=outcome, instance_id="test-instance:1",
        )


def test_sirt_resident_monitor_configuration_remains_explicit():
    root = Path(__file__).resolve().parents[2]
    script = (root / "artifacts/wow-engine/v17/sirt_assurance.py").read_text()
    assert "WOW_SIRT_REQUIRE_ENGINEERING_RESIDENT_HEARTBEAT" in script
    assert "dispatcher_runtime = read_auditor_runtime" in script
    assert 'auditor_id=DISPATCHER_ID' in script
