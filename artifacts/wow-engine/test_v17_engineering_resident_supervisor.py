"""Deterministic admission, lease, safety and independent SIRT-handoff tests."""
from __future__ import annotations

from pathlib import Path
import json
from datetime import datetime, timedelta, timezone
import io
import zipfile
from urllib.error import HTTPError

import pytest
import yaml

from v17.engineering_resident_supervisor import (
    ATTEMPT_KEY_PREFIX,
    LOCK_KEY,
    PENDING_DISPATCH_KEY,
    GitHubTransport,
    active_engineering_workflow,
    dispatch_once,
    next_approved_issue,
    pending_pr_for_issue,
    persist_resident_heartbeat,
    supervisor_runtime_status,
    resident_activation_blockers,
    resident_cycle_failure_code,
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


def open_pr_record(
    number, *, issue_number=None, title=None, body=None, draft=False,
):
    if body is None and issue_number is not None:
        body = f"Incident: #{issue_number}"
    return {
        "number": number,
        "state": "open",
        "html_url": f"https://github.com/gregoryharper84-ship-it/WOW-Dashboard/pull/{number}",
        "issue_url": f"https://api.github.com/repos/gregoryharper84-ship-it/WOW-Dashboard/issues/{number}",
        "title": title or f"Change {number}",
        "body": body,
        "draft": draft,
    }


class FakeClient:
    def __init__(
        self, *, active=False, issue_open=True, open_pr=False,
        open_pr_pages=None,
    ):
        self.active = active
        self.issue_open = issue_open
        self.open_pr = open_pr
        self.sent = []
        self.provider_state = "PROVIDER_ADMITTED"
        self.remote_clear = True
        self.reconciled_runs = []
        self.open_pr_calls = []
        self.fail_open_pr_page = None
        self.open_pr_pages = open_pr_pages if open_pr_pages is not None else (
            [[open_pr_record(9001, issue_number=1388, body="Incident: #1388")]]
            if open_pr else [[]]
        )

    def get(self, suffix):
        if suffix.startswith("actions/workflows/"):
            assert "/runs?status=" in suffix
            assert suffix.endswith("&per_page=100")
            if (self.active and "status=in_progress" in suffix
                    and "wow-v17-chatgpt-engineering-worker.yml" in suffix):
                return {"total_count": 1, "workflow_runs": [
                    {"name": "wow-v17-chatgpt-engineering-worker"}
                ]}
            return {"total_count": 0, "workflow_runs": []}
        if suffix.startswith("issues/"):
            return {"state": "open" if self.issue_open else "closed"}
        raise AssertionError(suffix)

    def get_open_pull_request_page(self, page):
        self.open_pr_calls.append(page)
        if self.fail_open_pr_page == page:
            raise RuntimeError("SIMULATED_GITHUB_PAGINATION_FAILURE")
        rows = self.open_pr_pages[page - 1] if page <= len(self.open_pr_pages) else []
        return rows, page < len(self.open_pr_pages), len(self.open_pr_pages) > 1

    def dispatch(self, issue, *, dispatch_id=""):
        self.sent.append(issue)

    def provider_admission(self):
        return self.provider_state

    def remote_writer_clear(self):
        return self.remote_clear

    def dispatched_runs(self, claim):
        return self.reconciled_runs


class FakeRedis:
    def __init__(self, *, locked=False, cooling=False, attempts=0):
        self.locked, self.cooling = locked, cooling
        self.attempts = attempts
        self.attempts_by_key = {}
        self.values = []
        self.stored = {}

    def set(self, key, value, *, nx=False, ex=0):
        self.values.append((key, value, nx, ex))
        if nx and self.locked:
            return False
        if nx and key in self.stored:
            return False
        self.stored[key] = value
        return True

    def exists(self, key):
        return self.cooling

    def get(self, key):
        if key.startswith(ATTEMPT_KEY_PREFIX):
            return str(self.attempts_by_key.get(key, self.attempts))
        return self.stored.get(key)

    def eval(self, script, key_count, *args):
        keys, argv = args[:key_count], args[key_count:]
        if "reserve_resident_dispatch" in script:
            if self.get(keys[0]) != argv[0] or self.get(keys[1]):
                return 0
            self.stored[keys[1]] = argv[1]
            return 1
        if "count_accepted_resident_dispatch" in script:
            if (self.get(keys[0]) != argv[0] or int(self.get(keys[1])) != int(argv[1])
                    or int(self.get(keys[1])) >= 3):
                return 0
            self.incr(keys[1])
            self.expire(keys[1], 86400)
            self.stored[keys[0]] = argv[2]
            return 1
        if "clear_reconciled_resident_dispatch" in script:
            if self.get(keys[0]) != argv[0]:
                return 0
            del self.stored[keys[0]]
            return 1
        raise AssertionError(script)

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


@pytest.mark.parametrize("state", [
    "PROVIDER_QUOTA_BLOCKED", "PROVIDER_THROTTLED", "PROVIDER_RECOVERY_UNVERIFIED",
    "PROVIDER_STATUS_UNVERIFIED", None, {}, "unexpected",
])
@pytest.mark.parametrize("attempts", [0, 3])
def test_provider_hold_precedes_dispatch_and_never_consumes_or_resets_cap(state, attempts):
    client, store = FakeClient(), FakeRedis(attempts=attempts)
    client.provider_state = state
    result = dispatch_once(client, store, MANIFEST)
    assert result == (state if state in (
        "PROVIDER_QUOTA_BLOCKED", "PROVIDER_THROTTLED", "PROVIDER_RECOVERY_UNVERIFIED",
    ) else "PROVIDER_STATUS_UNVERIFIED")
    assert not client.sent
    assert store.attempts == attempts
    assert store.attempts_by_key == {}
    assert store.get(PENDING_DISPATCH_KEY) is None


def test_unreadable_provider_status_fails_closed():
    client, store = FakeClient(), FakeRedis()
    def unavailable():
        raise TimeoutError("private upstream response")
    client.provider_admission = unavailable
    assert dispatch_once(client, store, MANIFEST) == "PROVIDER_STATUS_UNVERIFIED"
    assert not client.sent and store.attempts == 0


def test_provider_recovery_dispatches_once_and_preserves_existing_cap():
    client, store = FakeClient(), FakeRedis()
    client.provider_state = "PROVIDER_QUOTA_BLOCKED"
    assert dispatch_once(client, store, MANIFEST) == "PROVIDER_QUOTA_BLOCKED"
    store.stored.pop(LOCK_KEY)
    client.provider_state = "PROVIDER_ADMITTED"
    assert dispatch_once(client, store, MANIFEST) == "DISPATCHED_TO_PROTECTED_ENGINEERING_WORKFLOW"
    assert len(client.sent) == 1 and store.attempts == 1
    assert json.loads(store.get(PENDING_DISPATCH_KEY))["counted"] is True
    store.stored.pop(LOCK_KEY)
    assert dispatch_once(client, store, MANIFEST) == "DISPATCH_RECONCILIATION_REQUIRED"
    assert len(client.sent) == 1 and store.attempts == 1
    exhausted = FakeRedis(attempts=3)
    client.sent.clear()
    assert dispatch_once(client, exhausted, MANIFEST) == "DISPATCH_ATTEMPT_CAP_REQUIRES_TRIAGE"
    assert not client.sent and exhausted.attempts == 3


def test_remote_reservation_blocks_before_dispatch_and_count():
    client, store = FakeClient(), FakeRedis()
    client.remote_clear = False
    assert dispatch_once(client, store, MANIFEST) == "REMOTE_WRITER_RECONCILIATION_REQUIRED"
    assert not client.sent and store.attempts == 0


def test_ambiguous_dispatch_is_reconciled_and_counted_once_without_retry():
    client, store = FakeClient(), FakeRedis()
    def timeout(issue, **kwargs):
        client.sent.append(issue)
        raise TimeoutError("private response")
    client.dispatch = timeout
    assert dispatch_once(client, store, MANIFEST) == "DISPATCH_RECONCILIATION_REQUIRED"
    assert store.attempts == 0
    assert json.loads(store.get(PENDING_DISPATCH_KEY))["counted"] is False
    for runs, expected in (
        ([], "DISPATCH_RECONCILIATION_REQUIRED"),
        ([{"status": "in_progress"}], "EXISTING_ENGINEERING_WORKFLOW_ACTIVE"),
        ([{"status": "completed"}], "DISPATCH_RECONCILED"),
    ):
        store.stored.pop(LOCK_KEY)
        client.reconciled_runs = runs
        assert dispatch_once(client, store, MANIFEST) == expected
    assert len(client.sent) == 1 and store.attempts == 1
    assert store.get(PENDING_DISPATCH_KEY) is None


def test_expired_resident_lease_cannot_send_workflow():
    client, store = FakeClient(), FakeRedis()
    def moved_lease():
        store.stored[LOCK_KEY] = "another-owner"
        return "PROVIDER_ADMITTED"
    client.provider_admission = moved_lease
    assert dispatch_once(client, store, MANIFEST) == "DISPATCH_RECONCILIATION_REQUIRED"
    assert not client.sent and store.attempts == 0


def test_unreadable_remote_reservation_never_dispatches():
    client, store = FakeClient(), FakeRedis()
    def unavailable():
        raise ValueError("private response")
    client.remote_writer_clear = unavailable
    assert dispatch_once(client, store, MANIFEST) == "REMOTE_WRITER_RECONCILIATION_REQUIRED"
    assert not client.sent and store.attempts == 0


def _provider_evidence(monkeypatch, *, action="SURVIVAL", primary="failure",
                       secondary="failure", recovery=False):
    client = GitHubTransport("fixture-token")
    now = datetime.now(timezone.utc)
    stamp = (now - timedelta(minutes=5)).isoformat()
    sha = "a" * 40
    run = {
        "id": 900, "run_attempt": 1, "path": ".github/workflows/wow-v17-engineering-provider-dispatcher.yml",
        "head_branch": "main", "head_sha": sha, "status": "completed",
        "updated_at": stamp,
    }
    route = {
        "action": action, "provider": "deterministic" if action == "SURVIVAL" else "openai",
        "reason": "CIRCUIT_BREAKER:OPENAI_API_QUOTA_EXCEEDED:SECONDARY_UNAVAILABLE_OR_FAILED",
        "can_execute": False, "terminal_authority": "V17_TERMINAL_REDUCER",
    }
    calls = []
    def get(suffix):
        calls.append(suffix)
        if "engineering-provider-dispatcher.yml/runs?" in suffix:
            return {"workflow_runs": [run]}
        if suffix == "actions/runs/900/artifacts?per_page=100":
            return {"total_count": 1, "artifacts": [{
                "id": 77, "name": "wow-v17-engineering-provider-receipt-900-1", "expired": False,
            }]}
        if suffix == "commits/main":
            return {"sha": sha}
        for workflow, state in (("chatgpt", primary), ("claude", secondary)):
            if f"wow-v17-{workflow}-engineering-worker.yml/runs?" in suffix:
                return {"workflow_runs": [{
                    "id": 901 if workflow == "chatgpt" else 902,
                    "path": f".github/workflows/wow-v17-{workflow}-engineering-worker.yml",
                    "head_sha": sha,
                    "head_branch": "main", "status": "completed", "conclusion": state,
                    "updated_at": (now if recovery and state == "success"
                                   else now - timedelta(minutes=10)).isoformat(),
                }]}
        raise AssertionError(suffix)
    monkeypatch.setattr(client, "get", get)
    monkeypatch.setattr(client, "provider_receipt", lambda artifact_id: route)
    return client, run, route, calls


def test_real_transport_persistent_quota_zero_dispatch_and_counter_even_after_old_cap(monkeypatch):
    client, run, route, calls = _provider_evidence(monkeypatch)
    run["updated_at"] = (datetime.now(timezone.utc) - timedelta(days=10)).isoformat()
    monkeypatch.setattr(client, "remote_writer_clear", lambda: True)
    monkeypatch.setattr("v17.engineering_resident_supervisor.active_engineering_workflow", lambda client: False)
    for attempts in (0, 3):
        store = FakeRedis(attempts=attempts)
        assert dispatch_once(client, store, MANIFEST) == "PROVIDER_QUOTA_BLOCKED"
        assert store.attempts == attempts and store.attempts_by_key == {}
    assert not any("dispatches" in path for path in calls)


def test_recent_protected_worker_success_proves_recovery(monkeypatch):
    client, run, route, calls = _provider_evidence(monkeypatch, primary="success", recovery=True)
    assert client.provider_admission() == "PROVIDER_ADMITTED"
    client, run, route, calls = _provider_evidence(monkeypatch, action="DISPATCH")
    assert client.provider_admission() == "PROVIDER_ADMITTED"
    run["head_sha"] = "b" * 40
    assert client.provider_admission() == "PROVIDER_RECOVERY_UNVERIFIED"
    route["can_execute"] = True
    assert client.provider_admission() == "PROVIDER_STATUS_UNVERIFIED"


def test_success_acknowledgment_receipt_matches_exact_parent_worker(monkeypatch):
    client, run, route, _ = _provider_evidence(monkeypatch, primary="success")
    route.update(action="NO_ACTION", provider="none", parent_run_id="901",
                 reason="wow-v17-chatgpt-engineering-worker_SUCCESS")
    assert client.provider_admission() == "PROVIDER_ADMITTED"
    route["parent_run_id"] = "999"
    assert client.provider_admission() == "PROVIDER_RECOVERY_UNVERIFIED"


@pytest.mark.parametrize("bad_run", [
    {"head_branch": "feature"}, {"path": "untrusted.yml"}, {"id": True},
    {"head_sha": "malformed"}, {"status": "queued"},
])
def test_provider_receipt_identity_is_fail_closed(monkeypatch, bad_run):
    client, run, _, _ = _provider_evidence(monkeypatch)
    run.update(bad_run)
    assert client.provider_admission() == "PROVIDER_STATUS_UNVERIFIED"


def test_artifact_redirect_does_not_forward_credentials(monkeypatch):
    client = GitHubTransport("fixture-token")
    raw = io.BytesIO()
    with zipfile.ZipFile(raw, "w") as archive:
        archive.writestr("provider-route.json", '{"can_execute":false}')
    requests = []
    class Response(io.BytesIO):
        pass
    class Opener:
        def open(self, req, timeout):
            requests.append(req)
            if len(requests) == 1:
                raise HTTPError(req.full_url, 302, "", {
                    "Location": "https://fixture.blob.core.windows.net/artifact?signed=fixture",
                }, None)
            return Response(raw.getvalue())
    monkeypatch.setattr("urllib.request.build_opener", lambda handler: Opener())
    assert client.provider_receipt(77) == {"can_execute": False}
    assert requests[0].get_header("Authorization") == "Bearer " + "fixture-token"
    assert requests[1].get_header("Authorization") is None


def test_untrusted_artifact_redirect_is_rejected(monkeypatch):
    client = GitHubTransport("fixture-token")
    class Opener:
        def open(self, req, timeout):
            raise HTTPError(req.full_url, 302, "", {"Location": "https://attacker.invalid/secret"}, None)
    monkeypatch.setattr("urllib.request.build_opener", lambda handler: Opener())
    with pytest.raises(ValueError, match="PROVIDER_STATUS_UNVERIFIED"):
        client.provider_receipt(77)


def test_ambiguous_reconciliation_requires_exact_resident_nonce(monkeypatch):
    client = GitHubTransport("fixture-token")
    claim = {
        "issue_number": 1388, "lease_group": "P0_LLP_RESTORE",
        "dispatch_id": "a" * 32, "created_at": "2026-10-23T16:00:00Z",
    }
    run = {
        "head_branch": "main", "event": "workflow_dispatch",
        "path": ".github/workflows/wow-v17-engineering-provider-dispatcher.yml",
        "created_at": claim["created_at"], "status": "completed",
        "display_title": "WOW V17 provider source=manual lease=P0_LLP_RESTORE incident=1388 resident=" + "b" * 32,
    }
    response = {"total_count": 1, "workflow_runs": [run]}
    monkeypatch.setattr(client, "get", lambda suffix: response)
    assert client.dispatched_runs(claim) == []
    run["display_title"] = run["display_title"].replace("b" * 32, "a" * 32)
    assert client.dispatched_runs(claim) == [run]
    response["total_count"] = True
    with pytest.raises(ValueError, match="DISPATCH_RECONCILIATION_REQUIRED"):
        client.dispatched_runs(claim)


def test_counter_accounting_failure_keeps_pending_hold_without_resend():
    client, store = FakeClient(), FakeRedis()
    original = store.eval
    def lost_reply(script, *args):
        result = original(script, *args)
        if "count_accepted_resident_dispatch" in script:
            raise TimeoutError("reply lost after successful accounting")
        return result
    store.eval = lost_reply
    with pytest.raises(TimeoutError):
        dispatch_once(client, store, MANIFEST)
    assert store.attempts == 1
    store.eval = original
    store.stored.pop(LOCK_KEY)
    client.reconciled_runs = [{"status": "completed"}]
    assert dispatch_once(client, store, MANIFEST) == "DISPATCH_RECONCILED"
    assert store.attempts == 1 and len(client.sent) == 1


def test_malformed_pending_reservation_never_resets_counter():
    client, store = FakeClient(), FakeRedis(attempts=3)
    store.stored[PENDING_DISPATCH_KEY] = '{"can_execute":true}'
    assert dispatch_once(client, store, MANIFEST) == "DISPATCH_RECONCILIATION_REQUIRED"
    assert store.attempts == 3 and not client.sent


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


def test_concurrently_active_engineering_workflows_block_dispatch():
    class Concurrent(FakeClient):
        def get(self, suffix):
            if suffix == "actions/workflows/wow-v17-chatgpt-engineering-worker.yml/runs?status=in_progress&per_page=100":
                return {"total_count": 2, "workflow_runs": [
                    {"name": "wow-v17-chatgpt-engineering-worker"},
                    {"name": "wow-v17-chatgpt-engineering-worker"},
                ]}
            return super().get(suffix)

    client = Concurrent()
    assert dispatch_once(client, FakeRedis(), MANIFEST) == "EXISTING_ENGINEERING_WORKFLOW_ACTIVE"
    assert client.sent == []


def test_empty_queue_never_dispatches():
    client = FakeClient(issue_open=False)
    assert dispatch_once(client, FakeRedis(), MANIFEST) == "NO_APPROVED_OPEN_ENGINEERING_TASK"
    assert not client.sent


def test_github_inventory_incomplete_fails_closed():
    class Incomplete(FakeClient):
        def get(self, suffix):
            if suffix.startswith("actions/workflows/"):
                return {"total_count": 101, "workflow_runs": []}
            return super().get(suffix)
    with pytest.raises(RuntimeError, match="INCOMPLETE"):
        active_engineering_workflow(Incomplete())


def test_open_pr_identity_is_not_silent_and_blocks_duplicate():
    assert pending_pr_for_issue(FakeClient(open_pr=True), 1388)
    assert not pending_pr_for_issue(FakeClient(open_pr=False), 1388)


def _pr_pages(rows):
    pages = [rows[index:index + 100] for index in range(0, len(rows), 100)]
    if not pages:
        return [[]]
    if len(rows) % 100 == 0:
        pages.append([])
    return pages


@pytest.mark.parametrize(
    "count,expected_match,expected_pages",
    [(0, False, 1), (1, False, 1), (100, True, 2), (101, True, 2), (233, True, 3)],
)
def test_open_pr_inventory_is_exhaustive_across_bounded_pages(
    count, expected_match, expected_pages,
):
    rows = [
        open_pr_record(
            2000 + index,
            issue_number=1841 if index == count - 1 and expected_match else None,
        )
        for index in range(count)
    ]
    client = FakeClient(open_pr_pages=_pr_pages(rows))
    assert pending_pr_for_issue(client, 1841) is expected_match
    assert client.open_pr_calls == list(range(1, expected_pages + 1))


@pytest.mark.parametrize("duplicate_page", [2, 3])
def test_duplicate_on_later_page_is_found(duplicate_page):
    rows = [open_pr_record(3000 + index) for index in range(233)]
    rows[(duplicate_page - 1) * 100] = open_pr_record(
        3000 + (duplicate_page - 1) * 100,
        issue_number=1841,
        body="Incident: #1841",
        title="fix(P0): existing repair (#1841)",
        draft=True,
    )
    client = FakeClient(open_pr_pages=_pr_pages(rows))
    assert pending_pr_for_issue(client, 1841)
    assert client.open_pr_calls == [1, 2, 3]


def test_real_draft_pr_1842_is_detected_on_page_three_of_233():
    rows = [open_pr_record(9000 + index) for index in range(233)]
    rows[-1] = open_pr_record(
        1842,
        title="fix(P0): NFL stage-proof contract holds publication when receipts are absent (#1841)",
        body="Incident: #1841",
        draft=True,
    )
    client = FakeClient(open_pr_pages=_pr_pages(rows))
    assert pending_pr_for_issue(client, 1841)
    assert client.open_pr_calls == [1, 2, 3]


def test_open_pr_pagination_failure_replays_from_page_one_and_never_dispatches():
    rows = [open_pr_record(4000 + index) for index in range(101)]
    rows[0] = open_pr_record(4000, issue_number=1388)
    client = FakeClient(open_pr_pages=_pr_pages(rows))
    client.fail_open_pr_page = 2

    with pytest.raises(RuntimeError, match="PAGINATION_FAILURE"):
        dispatch_once(client, FakeRedis(), MANIFEST)
    assert client.sent == []

    client.fail_open_pr_page = None
    assert pending_pr_for_issue(client, 1388)
    assert client.open_pr_calls == [1, 2, 1, 2]


@pytest.mark.parametrize("body,title", [
    ("Related context mentions #1841 only in prose.", "Change to unrelated issue"),
    ("Incident: #11841", "fix(#11841)"),
])
def test_unanchored_or_numeric_prefix_references_do_not_match(body, title):
    client = FakeClient(open_pr_pages=[[
        open_pr_record(5000, issue_number=11841, title=title, body=body),
    ]])
    assert not pending_pr_for_issue(client, 1841)


def test_title_alone_cannot_claim_incident_ownership():
    client = FakeClient(open_pr_pages=[[
        open_pr_record(5001, title="fix(P0): existing repair (#1841)"),
    ]])
    assert not pending_pr_for_issue(client, 1841)


def test_canonical_body_marker_blocks_duplicate_even_with_unrelated_title():
    client = FakeClient(open_pr_pages=[[
        open_pr_record(
            1842, title="new NFL publication repair",
            body="AMRT_CANONICAL_INCIDENT_OWNERSHIP\nIncident: #1841",
            draft=True,
        ),
    ]])
    assert pending_pr_for_issue(client, 1841)


def test_prs_own_issue_url_does_not_alias_incident_number():
    client = FakeClient(open_pr_pages=[[
        open_pr_record(1842, title="Unrelated wording"),
    ]])
    assert not pending_pr_for_issue(client, 1841)


def test_full_page_requires_empty_sentinel_page():
    rows = [open_pr_record(6000 + index) for index in range(100)]
    class NoLink(FakeClient):
        def get_open_pull_request_page(self, page):
            self.open_pr_calls.append(page)
            if page == 1:
                return rows, False, False
            return [], False, False

    client = NoLink(open_pr_pages=[rows, []])
    assert not pending_pr_for_issue(client, 1841)
    assert client.open_pr_calls == [1, 2]


def test_open_pr_inventory_page_limit_is_fail_closed():
    pages = [
        [open_pr_record(8000 + page * 100 + index) for index in range(100)]
        for page in range(11)
    ]
    client = FakeClient(open_pr_pages=pages)
    with pytest.raises(RuntimeError, match="OPEN_PR_INVENTORY_NOT_EXHAUSTIVE"):
        pending_pr_for_issue(client, 1841)
    assert client.open_pr_calls == list(range(1, 11))


def test_exact_page_limit_with_explicit_end_is_complete():
    pages = [
        [open_pr_record(10000 + page * 100 + index) for index in range(100)]
        for page in range(10)
    ]
    client = FakeClient(open_pr_pages=pages)
    assert not pending_pr_for_issue(client, 1841)
    assert client.open_pr_calls == list(range(1, 11))


def test_transport_validates_open_pr_next_link(monkeypatch):
    client = GitHubTransport("placeholder")
    calls = []
    rows = [open_pr_record(7000 + index) for index in range(100)]
    next_link = (
        '<https://api.github.com/repos/gregoryharper84-ship-it/WOW-Dashboard/'
        'pulls?state=open&per_page=100&page=2>; rel="next"'
    )
    monkeypatch.setattr(
        client, "call_with_headers",
        lambda path: (calls.append(path) or rows, {"Link": next_link}),
    )
    result, has_next, has_pagination_link = client.get_open_pull_request_page(1)
    assert len(result) == 100
    assert has_next is True
    assert has_pagination_link is True
    assert calls == [
        "/repos/gregoryharper84-ship-it/WOW-Dashboard/pulls?state=open&per_page=100&page=1",
    ]

    monkeypatch.setattr(
        client, "call_with_headers",
        lambda *_: (rows, {"Link": next_link.replace("page=2", "page=3")}),
    )
    with pytest.raises(RuntimeError, match="OPEN_PR_INVENTORY_INVALID"):
        client.get_open_pull_request_page(1)


def test_malformed_open_pr_page_fails_closed_without_dispatch():
    client = FakeClient(open_pr_pages=[[{}]])
    with pytest.raises(RuntimeError, match="OPEN_PR_INVENTORY_INVALID"):
        dispatch_once(client, FakeRedis(), MANIFEST)
    assert client.sent == []


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


def test_existing_pr_cannot_starve_next_approved_p0_incident(monkeypatch):
    monkeypatch.setenv("WOW_ENGINEERING_EXACT_P1_BOOTSTRAP_CERTIFIED", "1")
    manifest = {**MANIFEST, "restoration": [ISSUE, SECOND_ISSUE]}
    client, store = FakeClient(open_pr=True), FakeRedis()
    assert dispatch_once(client, store, manifest) == "DISPATCHED_TO_PROTECTED_ENGINEERING_WORKFLOW"
    assert client.sent == [SECOND_ISSUE]
    assert store.attempts == 1


def test_existing_pr_does_not_starve_disjoint_p0_incident():
    disjoint_p0 = {
        **ISSUE,
        "issue_number": 1688,
        "priority_rank": 2,
        "lease_group": "P0_AUTONOMY_RESTORE",
        "conflict_keys": ["ENGINEERING_AUTONOMY"],
    }
    manifest = {**MANIFEST, "restoration": [ISSUE, disjoint_p0]}
    client, store = FakeClient(open_pr=True), FakeRedis()
    assert dispatch_once(client, store, manifest) == "DISPATCHED_TO_PROTECTED_ENGINEERING_WORKFLOW"
    assert client.sent == [disjoint_p0]


def test_retry_cap_cannot_starve_next_approved_p0_incident(monkeypatch):
    monkeypatch.setenv("WOW_ENGINEERING_EXACT_P1_BOOTSTRAP_CERTIFIED", "1")
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


def test_p1_remains_held_until_trusted_bootstrap_is_certified(monkeypatch):
    monkeypatch.delenv("WOW_ENGINEERING_EXACT_P1_BOOTSTRAP_CERTIFIED", raising=False)
    manifest = {**MANIFEST, "restoration": [SECOND_ISSUE]}
    client = FakeClient()
    assert dispatch_once(client, FakeRedis(), manifest) == "P1_EXACT_WORKER_BOOTSTRAP_REQUIRED"
    assert not client.sent


def test_untrusted_bootstrap_flag_value_fails_closed(monkeypatch):
    monkeypatch.setenv("WOW_ENGINEERING_EXACT_P1_BOOTSTRAP_CERTIFIED", "yes")
    manifest = {**MANIFEST, "restoration": [SECOND_ISSUE]}
    client = FakeClient()
    assert dispatch_once(client, FakeRedis(), manifest) == "P1_EXACT_WORKER_BOOTSTRAP_REQUIRED"
    assert not client.sent


def test_p0_still_dispatches_without_p1_bootstrap(monkeypatch):
    monkeypatch.delenv("WOW_ENGINEERING_EXACT_P1_BOOTSTRAP_CERTIFIED", raising=False)
    client = FakeClient()
    assert dispatch_once(client, FakeRedis(), MANIFEST) == "DISPATCHED_TO_PROTECTED_ENGINEERING_WORKFLOW"
    assert client.sent == [ISSUE]



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


def test_retiring_resident_cannot_overwrite_new_worker_heartbeat():
    # The Render replacement starts before the old worker has shut down.
    # STOPPED from the retiring instance must be an atomic compare-and-update,
    # never a new upsert of the shared auditor_id.
    class SharedStore:
        def __init__(self):
            self.row = None
            self.pending = None
            self.filters = []
        def table(self, name):
            assert name == "wow_engineering_auditor_runtime"
            return self
        def upsert(self, payload, *, on_conflict):
            assert on_conflict == "auditor_id"
            self.pending = ("upsert", dict(payload))
            self.filters = []
            return self
        def update(self, payload):
            self.pending = ("update", dict(payload))
            self.filters = []
            return self
        def eq(self, key, value):
            self.filters.append((key, value))
            return self
        def execute(self):
            kind, payload = self.pending
            if kind == "upsert" or all(self.row and self.row.get(k) == v for k, v in self.filters):
                self.row = payload
            return {"data": [self.row] if self.row else []}

    db = SharedStore()
    persist_resident_heartbeat(db, status="RUNNING", outcome="CYCLE_STARTED", instance_id="worker-old:7")
    persist_resident_heartbeat(db, status="RUNNING", outcome="CYCLE_STARTED", instance_id="worker-new:8")
    persist_resident_heartbeat(db, status="STOPPED", outcome="WORKER_SHUTDOWN", instance_id="worker-old:7")
    assert db.filters == [
        ("auditor_id", "WOW_ENGINEERING_RESIDENT_DISPATCHER"),
        ("instance_id", "worker-old:7"),
    ]
    assert db.row["status"] == "RUNNING" and db.row["instance_id"] == "worker-new:8"
    persist_resident_heartbeat(db, status="STOPPED", outcome="WORKER_SHUTDOWN", instance_id="worker-new:8")
    assert db.row["status"] == "STOPPED" and db.row["instance_id"] == "worker-new:8"
    assert db.row["can_execute"] is False


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


AMRT_1841 = {
    "issue_number": 1841, "severity": "P0", "priority_rank": 7,
    "execution_lane": "RAPID", "rapid_stream": "LLP_RESTORE",
    "lease_group": "P0_LLP_RESTORE",
    "conflict_keys": ["STAGE_PROOF", "FULL_SLATE_ACCEPTANCE"],
    "amrt_owned": True, "change_class": "B", "can_execute": False,
}


class AMRTClient(FakeClient):
    def __init__(self, *, open_pr=False):
        pages = [[open_pr_record(
            1842,
            title="fix(P0): NFL stage-proof contract holds publication when receipts are absent (#1841)",
            body="Incident: #1841",
            draft=True,
        )]] if open_pr else [[]]
        super().__init__(open_pr=open_pr, open_pr_pages=pages)

    def get(self, suffix):
        if suffix == "issues/1841":
            return {"state": "open"}
        return super().get(suffix)


def test_amrt_1841_is_admitted_by_existing_dispatcher_not_parallel_worker():
    manifest = {**MANIFEST, "restoration": [AMRT_1841]}
    client = AMRTClient(open_pr=False)
    assert dispatch_once(client, FakeRedis(), manifest) == "DISPATCHED_TO_PROTECTED_ENGINEERING_WORKFLOW"
    assert len(client.sent) == 1
    assert client.sent[0]["issue_number"] == 1841
    assert client.sent[0]["lease_group"] == "P0_LLP_RESTORE"


def test_amrt_1841_existing_pr_1842_blocks_duplicate_writer():
    manifest = {**MANIFEST, "restoration": [AMRT_1841]}
    client = AMRTClient(open_pr=True)
    assert dispatch_once(client, FakeRedis(), manifest) == "AWAITING_EXISTING_PR_REVIEW_OR_REPAIR"
    assert not client.sent


def test_amrt_1841_wrong_change_class_blocks_new_source_dispatch():
    manifest = {**MANIFEST, "restoration": [{**AMRT_1841, "change_class": "C"}]}
    client = AMRTClient(open_pr=False)
    assert dispatch_once(client, FakeRedis(), manifest) == "NO_APPROVED_OPEN_ENGINEERING_TASK"
    assert not client.sent


@pytest.mark.parametrize("field,reason", [
    ("enabled", "RESIDENT_FLAG_DISABLED"),
    ("token_configured", "GITHUB_DISPATCH_TOKEN_MISSING"),
    ("redis_configured", "REDIS_LEASE_CONFIG_MISSING"),
    ("durable_heartbeat_configured", "DURABLE_HEARTBEAT_CONFIG_MISSING"),
    ("governance_pass", "DRY_RUN_GOVERNANCE_HOLD"),
])
def test_activation_diagnostic_is_exact_but_never_contains_secret_values(field, reason):
    status = {
        "enabled": True,
        "token_configured": True,
        "redis_configured": True,
        "durable_heartbeat_configured": True,
        "governance_pass": True,
        "can_execute": False,
    }
    assert resident_activation_blockers(status) == ()
    status[field] = False
    result = resident_activation_blockers(status)
    assert result == (reason,)
    assert "SECRET" not in str(result)


def test_activation_diagnostics_include_all_missing_categories_without_credentials():
    status = {"enabled": True, "token_configured": False,
              "redis_configured": False, "durable_heartbeat_configured": True,
              "governance_pass": False}
    assert resident_activation_blockers(status) == (
        "GITHUB_DISPATCH_TOKEN_MISSING",
        "REDIS_LEASE_CONFIG_MISSING",
        "DRY_RUN_GOVERNANCE_HOLD",
    )


def test_actual_env_values_do_not_appear_in_safe_diagnostics(monkeypatch):
    monkeypatch.setenv("WOW_ENGINEERING_RESIDENT_DISPATCH_ENABLED", "1")
    monkeypatch.delenv("WOW_ENGINEERING_GITHUB_TOKEN", raising=False)
    monkeypatch.setenv("REDIS_URL", "redis://token-must-not-appear@host:6379/0")
    monkeypatch.setenv("SUPABASE_URL", "https://private-supabase-url.example")
    monkeypatch.setenv("SUPABASE_SERVICE_KEY", "super-secret-must-not-appear")
    monkeypatch.setenv("WOW_CAN_EXECUTE", "false")
    monkeypatch.setenv("WOW_DRY_RUN_ONLY", "true")
    decision = supervisor_runtime_status()
    blockers = resident_activation_blockers(decision)
    assert blockers == ("GITHUB_DISPATCH_TOKEN_MISSING",)
    assert "secret-must-not-appear" not in str(decision)
    assert "token-must-not-appear" not in str(decision)
    assert "private-supabase-url" not in str(decision)


@pytest.mark.parametrize("code", [
    "ACTIVE_WORKFLOW_INVENTORY_INCOMPLETE",
    "OPEN_PR_INVENTORY_INVALID",
    "OPEN_PR_INVENTORY_INCOMPLETE",
    "OPEN_PR_INVENTORY_NOT_EXHAUSTIVE",
    "OPEN_PR_PAGE_INVALID",
])
def test_resident_cycle_reports_known_fail_closed_reason_without_disabling_hold(code):
    assert resident_cycle_failure_code(RuntimeError(code)) == code
    assert resident_cycle_failure_code(ValueError(code)) == code


@pytest.mark.parametrize("error", [
    RuntimeError("Authorization: Bearer sensitive-credential"),
    RuntimeError("https://api.github.com/private?token=sensitive"),
    ValueError("OPEN_PR_INVENTORY_INVALID plus untrusted text"),
    ConnectionError("secret-network-response"),
])
def test_resident_cycle_redacts_untrusted_error_messages(error):
    result = resident_cycle_failure_code(error)
    assert result == "CYCLE_" + type(error).__name__.upper()
    assert "sensitive" not in result and "secret" not in result
    assert result.isupper() and result.replace("_", "").isalnum()


def test_unrelated_repository_wide_runs_cannot_block_specific_idle_workflows():
    class Narrow(FakeClient):
        def get(self, suffix):
            if suffix.startswith("actions/runs?"):
                raise AssertionError("Unrelated repository-wide CI is not consulted")
            return super().get(suffix)
    client = Narrow()
    assert not active_engineering_workflow(client)
    assert dispatch_once(client, FakeRedis(), MANIFEST) == "DISPATCHED_TO_PROTECTED_ENGINEERING_WORKFLOW"


@pytest.mark.parametrize("count,rows,expected", [
    (15, [], True), (0, [{"id": 1}], True),
    (1, [], True), (0, [], False),
])
def test_live_github_count_skew_is_busy_never_a_dispatch_permission(count, rows, expected):
    class Skewed(FakeClient):
        def get(self, suffix):
            if suffix == "actions/workflows/wow-v17-chatgpt-engineering-worker.yml/runs?status=queued&per_page=100":
                return {"total_count": count, "workflow_runs": rows}
            return super().get(suffix)
    assert active_engineering_workflow(Skewed()) is expected


@pytest.mark.parametrize("bad", [
    None, [], {"total_count": None, "workflow_runs": []},
    {"total_count": True, "workflow_runs": []},
    {"total_count": -1, "workflow_runs": []},
    {"total_count": 0, "workflow_runs": None},
    {"total_count": 0, "workflow_runs": ["invalid-row"]},
])
def test_specific_workflow_inventory_invalid_fails_closed(bad):
    class Invalid(FakeClient):
        def get(self, suffix):
            if suffix.startswith("actions/workflows/"):
                return bad
            return super().get(suffix)
    with pytest.raises(RuntimeError, match="ACTIVE_WORKFLOW_INVENTORY_INVALID"):
        dispatch_once(Invalid(), FakeRedis(), MANIFEST)


def test_github_numeric_repository_link_alias_is_same_exact_repo(monkeypatch):
    client = GitHubTransport("synthetic-test-token")
    link = (
        '<https://api.github.com/repositories/1240256887/'
        'pulls?state=open&per_page=100&page=2>; rel="next", '
        '<https://api.github.com/repositories/1240256887/'
        'pulls?state=open&per_page=100&page=3>; rel="last"'
    )
    monkeypatch.setattr(
        client, "call_with_headers",
        lambda _: ([open_pr_record(9100 + i) for i in range(100)], {"Link": link}),
    )
    rows, has_next, link_present = client.get_open_pull_request_page(1)
    assert len(rows) == 100 and has_next and link_present


@pytest.mark.parametrize("alteration", [
    "https://api.github.com/repositories/1240256888/pulls?state=open&per_page=100&page=2",
    "https://not-github.example/repositories/1240256887/pulls?state=open&per_page=100&page=2",
    "http://api.github.com/repositories/1240256887/pulls?state=open&per_page=100&page=2",
    "https://api.github.com/repositories/1240256887/pulls?state=closed&per_page=100&page=2",
    "https://api.github.com/repositories/1240256887/pulls?state=open&per_page=100&page=3",
    "https://api.github.com/repositories/1240256887/pulls?state=open&per_page=100&page=2&other=1",
    "https://api.github.com/repositories/1240256887/pulls?state=open&per_page=100&page=2#fragment",
    "https://api.github.com/repositories/1240256887/issues?state=open&per_page=100&page=2",
])
def test_numeric_alias_must_not_relax_exact_pagination_identity(alteration, monkeypatch):
    client = GitHubTransport("synthetic-test-token")
    link = f'<{alteration}>; rel="next"'
    monkeypatch.setattr(
        client, "call_with_headers",
        lambda _: ([open_pr_record(9300 + i) for i in range(100)], {"link": link}),
    )
    with pytest.raises(RuntimeError, match="OPEN_PR_INVENTORY_INVALID"):
        client.get_open_pull_request_page(1)


def test_numeric_repository_link_alias_only_for_configured_repository(monkeypatch):
    client = GitHubTransport("synthetic-test-token", repo="different-owner/different-repo")
    link = (
        '<https://api.github.com/repositories/1240256887/'
        'pulls?state=open&per_page=100&page=2>; rel="next"'
    )
    monkeypatch.setattr(client, "call_with_headers", lambda _: ([], {"Link": link}))
    with pytest.raises(RuntimeError, match="OPEN_PR_INVENTORY_INVALID"):
        client.get_open_pull_request_page(1)
