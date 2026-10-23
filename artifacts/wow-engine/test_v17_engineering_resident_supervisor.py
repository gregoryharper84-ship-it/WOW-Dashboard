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
        self.open_pr_calls = []
        self.fail_open_pr_page = None
        self.open_pr_pages = open_pr_pages if open_pr_pages is not None else (
            [[open_pr_record(9001, issue_number=1388, body="Incident: #1388")]]
            if open_pr else [[]]
        )

    def get(self, suffix):
        if suffix.startswith("actions/runs?"):
            if self.active and "status=in_progress" in suffix:
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


def test_concurrently_active_engineering_workflows_block_dispatch():
    class Concurrent(FakeClient):
        def get(self, suffix):
            if suffix == "actions/runs?status=in_progress&per_page=100":
                return {"total_count": 2, "workflow_runs": [
                    {"name": "wow-v17-chatgpt-engineering-worker"},
                    {"name": "wow-v17-engineering-provider-dispatcher"},
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
            if suffix.startswith("actions/runs?"):
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
