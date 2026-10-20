from concurrent.futures import ThreadPoolExecutor
from pathlib import Path
from threading import Lock

import pytest
import yaml

from v17 import github_release_journal as journal_module
from v17.github_release_journal import (
    AUTHENTIC_QA_APP_ID,
    AUTHENTIC_RELEASE_APP_ID,
    CONTROLLER_FILENAME,
    JournalProtocolError,
    RefJournal,
    ambiguous_render_ack,
    intent_ref,
    inventory_render_writers,
    load_workflow_texts,
    lock_ref,
    observation_decision,
    paginate,
    plan_release_admission,
    render_post_followup,
)


REPO = Path(__file__).resolve().parents[3]
SERVICE = "srv-da7sa9gu01pc73brt80g"
SHA = "b" * 40
OTHER = "c" * 40
THIRD = "d" * 40
REPO_NODE_ID = "R_kgDOtestrepo"
ZERO = "0" * 40


class MemoryGitHub:
    def __init__(self, *, graphql_status=200, forbidden=False, before_update=None):
        self.refs = {}
        self.calls = []
        self.graphql_status = graphql_status
        self.forbidden = forbidden
        self.before_update = before_update
        self.guard = Lock()

    def request(self, method, path, payload):
        with self.guard:
            self.calls.append((method, path, payload))
            if method == "GET" and path.startswith("/git/ref/"):
                name = "refs/" + path.removeprefix("/git/ref/")
                if name not in self.refs:
                    return 404, {"message": "Not Found"}
                return 200, {"object": {"sha": self.refs[name]}}
        raise AssertionError((method, path))

    def graphql(self, query, variables):
        with self.guard:
            self.calls.append(("GRAPHQL", "updateRefs", variables))
            assert "updateRefs" in query
            assert variables["repositoryId"] == REPO_NODE_ID
            if self.forbidden:
                return 200, {
                    "data": {"updateRefs": None},
                    "errors": [{"type": "FORBIDDEN", "message": "Resource not accessible"}],
                }
            if self.graphql_status != 200:
                return self.graphql_status, {"message": "unavailable"}
            if self.before_update:
                self.before_update(self, variables)
            updates = variables["refUpdates"]
            for update in updates:
                if self.refs.get(update["name"], ZERO) != update["beforeOid"]:
                    return 200, {"data": {"updateRefs": None}, "errors": [{"message": "stale beforeOid"}]}
            for update in updates:
                if update["afterOid"] == ZERO:
                    self.refs.pop(update["name"], None)
                else:
                    self.refs[update["name"]] = update["afterOid"]
            return 200, {"data": {"updateRefs": {"clientMutationId": None}}}

    def journal(self):
        return RefJournal(self.request, self.graphql, repository_id=REPO_NODE_ID)


def _checks(head="a" * 40):
    return [
        {
            "id": 1, "name": "WOW Independent QA exact-head", "head_sha": head,
            "app": {"id": AUTHENTIC_QA_APP_ID}, "status": "completed", "conclusion": "success",
        },
        {
            "id": 2, "name": "WOW Release Authority exact-head", "head_sha": head,
            "app": {"id": AUTHENTIC_RELEASE_APP_ID}, "status": "completed", "conclusion": "success",
        },
    ]


def _workflow_texts(*, credential_holders=()):
    text = (
        "jobs:\n"
        "  deploy-production:\n"
        "    environment: wow-release\n"
        "    steps:\n"
    )
    return {
        CONTROLLER_FILENAME: text,
        **{name: "RENDER_API_KEY: ${{ secrets.RENDER_API_KEY }}\n" for name in credential_holders},
    }


def _environment():
    return {
        "name": "wow-release",
        "can_admins_bypass": False,
        "protection_rules": [{"type": "required_reviewers"}, {"type": "branch_policy"}],
        "deployment_branch_policy": {"protected_branches": False, "custom_branch_policies": True},
    }


def _admit(store, **overrides):
    evidence = {
        "environment_document": _environment(),
        "branch_policies": [{"name": "main"}],
        "environment_secret_names": ["RENDER_API_KEY"],
        "secret_list_readable": True,
        "target_sha": SHA,
        "current_main_sha": SHA,
        "qa_app_id": str(AUTHENTIC_QA_APP_ID),
        "release_app_id": str(AUTHENTIC_RELEASE_APP_ID),
        "source_head": "a" * 40,
        "role_checks": _checks(),
        "live_service": {"id": SERVICE, "autoDeploy": "no", "autoDeployTrigger": "off"},
        "workflow_texts": _workflow_texts(),
        "deploys": [],
        "journal": store.journal(),
        "service_id": SERVICE,
        "recheck_main_sha": lambda: SHA,
        "recheck_deploys": lambda: [],
    }
    evidence.update(overrides)
    return plan_release_admission(**evidence)


def test_reservation_uses_atomic_before_oid_for_lock_and_intent_and_reentry_never_reposts():
    store = MemoryGitHub()
    first = store.journal().reserve(SERVICE, SHA)
    again = store.journal().reserve(SERVICE, SHA)

    assert first["action"] == "POST_ONCE"
    assert first["payload"] == {"clearCache": "do_not_clear", "commitId": SHA}
    assert again["action"] == "RECONCILE"
    assert store.refs[lock_ref(SERVICE)] == SHA
    assert store.refs[intent_ref(SERVICE, SHA)] == SHA
    mutations = [call for call in store.calls if call[0] == "GRAPHQL"]
    assert len(mutations) == 1
    assert {
        (update["name"], update["beforeOid"], update["afterOid"])
        for update in mutations[0][2]["refUpdates"]
    } == {
        (lock_ref(SERVICE), ZERO, SHA),
        (intent_ref(SERVICE, SHA), ZERO, SHA),
    }
    assert all(update["force"] is True for update in mutations[0][2]["refUpdates"])
    assert not any(call[0] in {"POST", "DELETE"} for call in store.calls)


def test_concurrent_same_sha_reserves_at_most_one_post():
    store = MemoryGitHub()
    with ThreadPoolExecutor(max_workers=8) as pool:
        decisions = list(pool.map(lambda _index: store.journal().reserve(SERVICE, SHA), range(8)))

    assert sum(decision["action"] == "POST_ONCE" for decision in decisions) == 1
    assert all(decision["action"] in {"POST_ONCE", "RECONCILE"} for decision in decisions)
    assert len([call for call in store.calls if call[0] == "GRAPHQL"]) == 1


def test_graphql_permission_and_api_failures_hold_without_partial_ref_changes():
    denied = MemoryGitHub(forbidden=True)
    decision = denied.journal().reserve(SERVICE, SHA)
    assert decision["action"] == "HOLD"
    assert decision["blockers"] == ["RELEASE_JOURNAL_REF_PERMISSION_DENIED"]
    assert denied.refs == {}

    unavailable = MemoryGitHub(graphql_status=503)
    failed = unavailable.journal().reserve(SERVICE, SHA)
    assert failed["action"] == "HOLD"
    assert failed["blockers"] == ["RELEASE_JOURNAL_CAS_UNAVAILABLE"]
    assert unavailable.refs == {}


def test_atomic_cas_rejects_stale_lock_without_creating_intent():
    def move_lock(store, _variables):
        store.refs[lock_ref(SERVICE)] = THIRD

    store = MemoryGitHub(before_update=move_lock)
    decision = store.journal().reserve(SERVICE, SHA)
    assert decision["action"] == "HOLD"
    assert store.refs[lock_ref(SERVICE)] == THIRD
    assert intent_ref(SERVICE, SHA) not in store.refs


def test_stale_lock_reclaim_requires_its_intent_and_resolved_render_evidence():
    store = MemoryGitHub()
    old = store.journal().reserve(SERVICE, SHA)
    assert old["action"] == "POST_ONCE"
    held = store.journal().reserve(SERVICE, OTHER, holder_resolved=lambda _sha: False)
    assert held["action"] == "HOLD"
    assert store.refs[lock_ref(SERVICE)] == SHA

    reclaimed = store.journal().reserve(
        SERVICE, OTHER, holder_resolved=lambda _sha: True
    )
    assert reclaimed["action"] == "POST_ONCE"
    assert store.refs[lock_ref(SERVICE)] == OTHER
    assert store.refs[intent_ref(SERVICE, SHA)] == SHA
    assert store.refs[intent_ref(SERVICE, OTHER)] == OTHER


def test_reclaim_rejects_lock_replaced_inside_cas_window():
    def replace_lock(store, _variables):
        store.refs[lock_ref(SERVICE)] = THIRD

    store = MemoryGitHub()
    assert store.journal().reserve(SERVICE, SHA)["action"] == "POST_ONCE"
    store.before_update = replace_lock
    result = store.journal().reserve(SERVICE, OTHER, holder_resolved=lambda _sha: True)
    assert result["action"] == "HOLD"
    assert store.refs[lock_ref(SERVICE)] == THIRD
    assert intent_ref(SERVICE, OTHER) not in store.refs


def test_lock_release_requires_matching_intent_and_keeps_it_for_safe_reentry():
    orphan = MemoryGitHub()
    orphan.refs[lock_ref(SERVICE)] = SHA
    assert orphan.journal().release_lock(SERVICE, SHA)["released"] is False
    assert orphan.refs == {lock_ref(SERVICE): SHA}

    store = MemoryGitHub()
    assert store.journal().reserve(SERVICE, SHA)["action"] == "POST_ONCE"
    released = store.journal().release_lock(SERVICE, SHA)
    assert released["released"] is True
    assert lock_ref(SERVICE) not in store.refs
    assert store.refs[intent_ref(SERVICE, SHA)] == SHA
    reentry = store.journal().reserve(SERVICE, SHA)
    assert reentry["action"] == "RECONCILE"
    assert store.refs[lock_ref(SERVICE)] == SHA


def test_ambiguous_render_ack_and_65_minute_build_are_read_only():
    assert ambiguous_render_ack("RENDER_DEPLOY_API_UNREACHABLE")
    assert ambiguous_render_ack("RENDER_DEPLOY_API_HTTP_503")
    assert not ambiguous_render_ack("RENDER_DEPLOY_API_HTTP_422")
    assert render_post_followup(action="POST_ONCE", error_code="RENDER_DEPLOY_API_HTTP_503") == {
        "post": False, "retry": False, "ambiguous": True, "can_execute": False,
    }
    assert observation_decision(
        timed_out=False,
        deploy_status="build_in_progress",
        deploy_sha=SHA,
        target_sha=SHA,
        current_main_sha=SHA,
    )["action"] == "WAIT"
    workflow = (REPO / ".github/workflows/wow-v17-render-production-deploy.yml").read_text()
    assert "timeout-minutes: 120" in workflow
    assert "deploy_deadline = time.monotonic() + 90 * 60" in workflow
    assert 65 * 60 < 90 * 60


def test_moved_main_during_build_cannot_be_reported_live():
    result = observation_decision(
        timed_out=False,
        deploy_status="live",
        deploy_sha=SHA,
        target_sha=SHA,
        current_main_sha=OTHER,
    )
    assert result["action"] == "WAIT"
    assert result["post"] is False


def test_release_admission_holds_for_unattested_credential_scope_without_reserving():
    assert journal_module.RENDER_CREDENTIAL_SCOPE_ATTESTED is False
    store = MemoryGitHub()
    result = _admit(
        store,
        workflow_texts=_workflow_texts(
            credential_holders=("wow-v17-secret-sync.yml", "wow-v17-mcp-remote-acceptance.yml")
        ),
    )
    assert result["action"] == "HOLD"
    assert result["status"] == "RELEASE_RENDER_CREDENTIAL_SCOPE_UNATTESTED"
    assert result["blockers"] == ["RENDER_API_CREDENTIAL_SCOPE_UNATTESTED"]
    assert set(result["credential_holders"]) == {
        "wow-v17-secret-sync.yml", "wow-v17-mcp-remote-acceptance.yml",
    }
    assert store.calls == []
    assert store.refs == {}

    unreadable = MemoryGitHub()
    unknown_scope = _admit(
        unreadable,
        workflow_texts=_workflow_texts(),
        environment_secret_names=None,
        secret_list_readable=False,
    )
    assert unknown_scope["status"] == "RELEASE_RENDER_CREDENTIAL_SCOPE_UNATTESTED"
    assert unknown_scope["environment_credential_scope"] == "UNVERIFIED"
    assert unreadable.calls == []
    assert unreadable.refs == {}


def test_repository_writer_inventory_and_workflow_keep_release_safeguards():
    texts = load_workflow_texts(REPO / ".github/workflows")
    inventory = inventory_render_writers(texts)
    assert inventory["complete"] is True
    assert inventory["foreign_posts"] == []
    # The production deploy credential must not be handed to test/sync workflows.
    # This only removes known repository holders; it does NOT establish provider
    # credential exclusivity or authorize a release.
    assert inventory["credential_holders"] == []
    assert journal_module.RENDER_CREDENTIAL_SCOPE_ATTESTED is False
    sync_text = texts["wow-v17-secret-sync.yml"]
    mcp_text = texts["wow-v17-mcp-remote-acceptance.yml"]
    assert "secrets.WOW_SECRET_SYNC_RENDER_API_KEY" in sync_text
    assert "secrets.WOW_MCP_AUTH_TOKEN" in mcp_text
    assert "secrets.RENDER_API_KEY" not in sync_text
    assert "secrets.RENDER_API_KEY" not in mcp_text
    assert "RENDER_API_KEY" not in mcp_text
    workflow = yaml.safe_load((REPO / ".github/workflows" / CONTROLLER_FILENAME).read_text())
    job = workflow["jobs"]["deploy-production"]
    assert job["environment"] == "wow-release"
    assert workflow["permissions"]["contents"] == "write"
    assert job["env"]["WOW_REPOSITORY_NODE_ID"] == "${{ github.event.repository.node_id }}"
    script = next(step["run"] for step in job["steps"] if "github_graphql" in str(step.get("run") or ""))
    compile(script.split("python - <<'PY'\n", 1)[1].rsplit("\nPY", 1)[0], CONTROLLER_FILENAME, "exec")


def test_full_page_at_pagination_bound_fails_closed():
    assert paginate(lambda page: [page], max_pages=2, page_size=100) == [1]
    with pytest.raises(JournalProtocolError, match="GITHUB_CHECK_PAGE_LIMIT"):
        paginate(lambda _page: [1] * 100, max_pages=2)
