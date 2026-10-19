"""Exactly-once GitHub ref journal for the production Render controller.

These tests drive the admission planner with a fake ref store. They do not
call Render or GitHub and they do not authorize a deployment.
"""

from __future__ import annotations

import os
import re
from pathlib import Path

import pytest
import yaml

from v17.github_release_journal import (
    AUTHENTIC_QA_APP_ID,
    AUTHENTIC_RELEASE_APP_ID,
    CONTROLLER_FILENAME,
    DEPLOYMENT_API_CAS_BLOCKER,
    QA_CHECK_NAME,
    RELEASE_CHECK_NAME,
    REQUIRED_ENVIRONMENT,
    JournalProtocolError,
    RefJournal,
    ambiguous_render_ack,
    deployments_api_is_not_a_cas,
    inventory_render_writers,
    load_workflow_texts,
    protected_release_job_environment,
    observation_decision,
    paginate,
    plan_release_admission,
    render_post_followup,
    workflow_posts_render_deploy,
)


REPO = Path(__file__).resolve().parents[3]
SERVICE = "srv-da7sa9gu01pc73brt80g"
SHA = "b" * 40
OTHER = "c" * 40
HEAD = "a" * 40
STALE_PREFIX = "dfe47f42"


class MemoryGitHub:
    def __init__(self, *, create_status: int = 201):
        self.refs: dict[str, str] = {}
        self.calls: list[tuple] = []
        self.create_status = create_status

    def request(self, method, path, payload):
        self.calls.append((method, path, None if payload is None else dict(payload)))
        if method == "GET" and path.startswith("/git/ref/"):
            full = "refs/" + path[len("/git/ref/"):]
            sha = self.refs.get(full)
            if sha is None:
                return 404, {"message": "Not Found"}
            return 200, {"object": {"sha": sha}}
        if method == "POST" and path == "/git/refs":
            if self.create_status == 403:
                return 403, {"message": "Resource not accessible by integration"}
            ref = payload["ref"]
            if ref in self.refs:
                return 422, {"message": "Reference already exists"}
            self.refs[ref] = payload["sha"]
            return self.create_status, {"ref": ref, "object": {"sha": payload["sha"]}}
        if method == "DELETE" and path.startswith("/git/refs/"):
            full = "refs/" + path[len("/git/refs/"):]
            if full not in self.refs:
                return 404, {"message": "Not Found"}
            del self.refs[full]
            return 204, None
        raise AssertionError((method, path))


def _checks(head=HEAD, qa_app=AUTHENTIC_QA_APP_ID, release_app=AUTHENTIC_RELEASE_APP_ID, conclusion="success"):
    return [
        {
            "id": 10,
            "name": QA_CHECK_NAME,
            "head_sha": head,
            "app": {"id": qa_app},
            "status": "completed",
            "conclusion": conclusion,
        },
        {
            "id": 11,
            "name": RELEASE_CHECK_NAME,
            "head_sha": head,
            "app": {"id": release_app},
            "status": "completed",
            "conclusion": "success",
        },
    ]


def _texts(foreign_post: bool = False, *, bind_environment: bool = True):
    environment_line = "    environment: wow-release\n" if bind_environment else ""
    texts = {
        CONTROLLER_FILENAME: (
            "name: wow-v17-render-production-deploy\n"
            "jobs:\n"
            "  deploy-production:\n"
            + environment_line
            + "    steps:\n"
            + '      - run: render_json("POST", f"/services/{service}/deploys", payload)\n'
        ),
        "wow-v17-secret-sync.yml": "RENDER_API_KEY: ${{ secrets.RENDER_API_KEY }}\n",
    }
    if foreign_post:
        texts["other.yml"] = 'requests.post("https://api.render.com/v1/services/x/deploys")\n'
    return texts


def _protected_environment():
    return {
        "name": "wow-release",
        "can_admins_bypass": False,
        "protection_rules": [{"type": "required_reviewers"}, {"type": "branch_policy"}],
        "deployment_branch_policy": {
            "protected_branches": False,
            "custom_branch_policies": True,
        },
    }


def _plan(store: MemoryGitHub, **overrides):
    evidence = dict(
        environment_document=_protected_environment(),
        branch_policies=[{"name": "main"}],
        environment_secret_names=["RENDER_API_KEY"],
        secret_list_readable=True,
        target_sha=SHA,
        current_main_sha=SHA,
        qa_app_id=str(AUTHENTIC_QA_APP_ID),
        release_app_id=str(AUTHENTIC_RELEASE_APP_ID),
        source_head=HEAD,
        role_checks=_checks(),
        live_service={"id": SERVICE, "autoDeploy": "no", "autoDeployTrigger": "off"},
        workflow_texts=_texts(),
        deploys=[],
        journal=RefJournal(store.request),
        service_id=SERVICE,
        recheck_main_sha=lambda: SHA,
        recheck_deploys=lambda: [],
    )
    evidence.update(overrides)
    return plan_release_admission(**evidence)


def _render_posts(decision) -> list:
    follow = render_post_followup(action=decision["action"], error_code=None)
    if follow["post"]:
        return [decision["payload"]]
    return []


def test_real_controller_uses_github_job_binding_not_nonexistent_runner_environment():
    texts = load_workflow_texts(REPO / ".github/workflows")
    controller = texts[CONTROLLER_FILENAME]
    assert protected_release_job_environment(texts) == REQUIRED_ENVIRONMENT
    parsed = yaml.safe_load(controller)
    assert parsed["jobs"]["deploy-production"]["environment"] == REQUIRED_ENVIRONMENT
    assert "ref: ${{ github.sha }}" in controller
    assert "GITHUB_ENVIRONMENT" not in controller
    assert "protected_job_environment = protected_release_job_environment(release_workflow_texts)" in controller
    assert "environment_document=environment_document" in controller
    assert "secret_list_readable=secret_list_readable" in controller
    assert "RELEASE_PROTECTED_JOB_BINDING_MISSING" in controller
    assert "/environments/wow-release" in controller


@pytest.mark.parametrize(
    "workflow",
    [
        "jobs:\n  deploy-production:\n    runs-on: ubuntu-latest\n",
        "jobs:\n  deploy-production:\n    environment: production\n",
        "jobs:\n  different-job:\n    environment: wow-release\n",
        "jobs:\n  deploy-production:\n    environment: wow-release\n    environment: wow-release\n",
        "jobs:\n  deploy-production:\n    environment:\n      name: wow-release\n",
        "jobs:\n  deploy-production:\n    steps:\n      - run: echo wow-release\n",
        "not-jobs:\n  deploy-production:\n    environment: wow-release\n",
    ],
)
def test_missing_wrong_duplicated_or_spoofed_job_binding_fails_closed(workflow):
    assert protected_release_job_environment({CONTROLLER_FILENAME: workflow}) == ""


def test_realistic_protected_job_environment_is_accepted_without_runner_variable():
    text = (
        "name: governed\n"
        "jobs:\n"
        "  prepare:\n    runs-on: ubuntu-latest\n"
        "  deploy-production:\n    name: release\n"
        "    environment: wow-release\n    runs-on: ubuntu-latest\n"
        "    steps:\n      - run: echo okay\n"
        "  audit:\n    runs-on: ubuntu-latest\n"
    )
    assert protected_release_job_environment({CONTROLLER_FILENAME: text}) == REQUIRED_ENVIRONMENT


def test_environment_api_authorization_is_reachable_without_a_runner_environment(monkeypatch):
    """A missing runner environment variable is not evidence. API evidence can pass."""
    monkeypatch.delenv("GITHUB_ENVIRONMENT", raising=False)
    assert "GITHUB_ENVIRONMENT" not in os.environ
    store = MemoryGitHub()

    admitted = _plan(store, secret_list_readable=False, environment_secret_names=None)

    assert admitted["action"] == "POST_ONCE"
    assert admitted["environment_credential_scope"] == "UNVERIFIED_API_FORBIDDEN"
    assert admitted["environment_reviewers_configured"] is True
    assert admitted["can_execute"] is False
    workflow = (REPO / ".github/workflows/wow-v17-render-production-deploy.yml").read_text(encoding="utf-8")
    assert "GITHUB_ENVIRONMENT" not in workflow
    assert "/environments/wow-release" in workflow
    real_texts = load_workflow_texts(REPO / ".github" / "workflows")
    assert protected_release_job_environment(real_texts) == REQUIRED_ENVIRONMENT
    unbound = _plan(MemoryGitHub(), workflow_texts=_texts(bind_environment=False))
    assert unbound["blockers"] == ["RELEASE_ENVIRONMENT_JOB_BINDING_MISSING"]
    assert unbound["status"] == "RELEASE_ENVIRONMENT_RESTRICTION_FAILED"


def test_missing_environment_protection_blocks_without_a_ref_create():
    live_shaped = dict(_protected_environment())
    live_shaped["protection_rules"] = [{"type": "branch_policy"}]
    recorded = _plan(MemoryGitHub(), environment_document=live_shaped)
    assert recorded["action"] == "POST_ONCE"
    assert recorded["environment_reviewers_configured"] is False

    open_environment = dict(_protected_environment())
    open_environment["deployment_branch_policy"] = None
    unrestricted_store = MemoryGitHub()
    unrestricted = _plan(unrestricted_store, environment_document=open_environment)
    assert unrestricted["blockers"] == ["RELEASE_ENVIRONMENT_BRANCH_POLICY_UNRESTRICTED"]
    assert unrestricted_store.calls == []

    excludes_store = MemoryGitHub()
    excludes = _plan(excludes_store, branch_policies=[{"name": "release/*"}])
    assert excludes["blockers"] == ["RELEASE_ENVIRONMENT_BRANCH_POLICY_EXCLUDES_MAIN"]
    assert excludes_store.calls == []

    bypass = dict(_protected_environment())
    bypass["can_admins_bypass"] = True
    bypass_store = MemoryGitHub()
    admin = _plan(bypass_store, environment_document=bypass)
    assert admin["blockers"] == ["RELEASE_ENVIRONMENT_ADMIN_BYPASS_ENABLED"]
    assert bypass_store.calls == []

    unscoped_store = MemoryGitHub()
    unscoped = _plan(
        unscoped_store,
        secret_list_readable=True,
        environment_secret_names=["SOME_OTHER_SECRET"],
    )
    assert unscoped["status"] == "RELEASE_ENVIRONMENT_CREDENTIAL_UNSCOPED"
    assert unscoped["environment_credential_scope"] == "MISSING"
    assert unscoped_store.calls == []


def test_deployments_api_is_rejected_as_the_journal_cas():
    with pytest.raises(JournalProtocolError, match=DEPLOYMENT_API_CAS_BLOCKER):
        deployments_api_is_not_a_cas("github_deployments")
    deployments_api_is_not_a_cas("git_ref")


def test_green_path_reserves_one_intent_and_one_pinned_post():
    store = MemoryGitHub()
    decision = _plan(store)

    assert decision["action"] == "POST_ONCE"
    assert decision["eligible"] is True
    assert decision["can_execute"] is False
    assert decision["payload"] == {"clearCache": "do_not_clear", "commitId": SHA}
    assert decision["environment_reviewers_configured"] is True
    assert decision["environment_credential_scope"] == "ENVIRONMENT_SECRET"
    assert _render_posts(decision) == [decision["payload"]]
    assert decision["credential_holders"] == ["wow-v17-secret-sync.yml"]
    assert store.refs[f"refs/heads/wow-release/intent/{SERVICE}/{SHA}"] == SHA
    assert store.refs[f"refs/heads/wow-release/lock/{SERVICE}"] == SHA

    again = _plan(store)
    assert again["action"] == "RECONCILE"
    assert again["eligible"] is False
    assert _render_posts(again) == []
    creates = [call for call in store.calls if call[0] == "POST"]
    assert len(creates) == 2


def test_missing_wrong_and_stale_app_checks_do_not_reserve_or_post():
    missing = _plan(MemoryGitHub(), role_checks=[])
    assert missing["action"] == "HOLD"
    assert missing["status"] == "RELEASE_AUTHORIZATION_HOLD_NO_DEPLOY"
    assert "MISSING_OR_FOREIGN" in missing["blockers"][0]

    wrong = _plan(MemoryGitHub(), role_checks=_checks(qa_app=333))
    assert wrong["action"] == "HOLD"
    assert "MISSING_OR_FOREIGN" in wrong["blockers"][0]

    stale = _plan(MemoryGitHub(), role_checks=_checks(head="d" * 40))
    assert stale["action"] == "HOLD"
    assert "MISSING_OR_FOREIGN" in stale["blockers"][0]

    unbound = _plan(MemoryGitHub(), qa_app_id="111", release_app_id="222")
    assert unbound["blockers"] == ["RENDER_RELEASE_APP_IDENTITIES_UNBOUND"]

    for store in (MemoryGitHub(),):
        assert _plan(store, role_checks=[])
        assert not any(call[0] == "POST" for call in store.calls)


def test_missing_or_stale_sha_never_reserves_a_journal():
    store = MemoryGitHub()
    short = _plan(store, target_sha=STALE_PREFIX, current_main_sha=STALE_PREFIX)
    assert short["action"] == "HOLD"
    assert short["status"] == "STALE_SHA_NO_DEPLOY"
    assert short["blockers"] == ["RENDER_DEPLOY_TARGET_SHA_INVALID"]

    moved = _plan(store, current_main_sha=OTHER)
    assert moved["status"] == "STALE_SHA_NO_DEPLOY"
    assert store.calls == []
    assert _render_posts(short) == []
    assert _render_posts(moved) == []


def test_concurrent_same_sha_posts_once_and_other_sha_holds_the_lock():
    store = MemoryGitHub()
    first = RefJournal(store.request).reserve(SERVICE, SHA, consume_only=False)
    second = RefJournal(store.request).reserve(SERVICE, SHA, consume_only=False)
    other = RefJournal(store.request).reserve(SERVICE, OTHER, consume_only=False)

    assert first["action"] == "POST_ONCE"
    assert second["action"] == "RECONCILE"
    assert other["action"] == "HOLD"
    assert other["status"] == "RELEASE_JOURNAL_SERVICE_LOCK_HELD"
    assert _render_posts(first) == [first["payload"]]
    assert _render_posts(second) == []
    assert _render_posts(other) == []
    assert not any(payload and payload.get("sha") == OTHER for _method, _path, payload in store.calls)


def test_native_writer_environment_and_foreign_post_hold_before_any_ref_create():
    native = MemoryGitHub()
    decision = _plan(
        native,
        live_service={"id": SERVICE, "autoDeploy": "yes", "autoDeployTrigger": "checksPass"},
    )
    assert decision["status"] == "RELEASE_NATIVE_WRITER_ENABLED"
    assert decision["blockers"] == ["RENDER_NATIVE_AUTO_DEPLOY_WRITER_ENABLED"]
    assert native.calls == []

    unverified = _plan(MemoryGitHub(), live_service=None)
    assert unverified["status"] == "RELEASE_JOURNAL_PREFLIGHT_HOLD_NO_DEPLOY"
    assert unverified["live_render_auto_deploy_trigger"] == "UNVERIFIED"

    wrong_service = _plan(
        MemoryGitHub(),
        live_service={"id": "srv-someoneelse", "autoDeploy": "no", "autoDeployTrigger": "off"},
    )
    assert wrong_service["blockers"] == ["RELEASE_SERVICE_IDENTITY_UNVERIFIED"]

    environment = MemoryGitHub()
    blocked = _plan(environment, environment_document=None, branch_policies=[])
    assert blocked["status"] == "RELEASE_ENVIRONMENT_RESTRICTION_FAILED"
    assert environment.calls == []

    foreign = MemoryGitHub()
    inventory_hold = _plan(foreign, workflow_texts=_texts(foreign_post=True))
    assert inventory_hold["status"] == "RELEASE_JOURNAL_PREFLIGHT_HOLD_NO_DEPLOY"
    assert inventory_hold["blockers"] == ["RENDER_FOREIGN_DEPLOY_WRITER"]
    assert foreign.calls == []


def test_other_sha_in_progress_holds_without_consuming_the_intent():
    store = MemoryGitHub()
    decision = _plan(
        store,
        deploys=[{"id": "dep-other", "status": "build_in_progress", "commit": {"id": OTHER}}],
        recheck_deploys=lambda: [{"id": "dep-other", "status": "build_in_progress", "commit": {"id": OTHER}}],
    )
    assert decision["status"] == "RELEASE_OTHER_SHA_DEPLOY_ACTIVE"
    assert store.calls == []
    assert _render_posts(decision) == []


def test_delayed_visibility_and_ambiguous_ack_do_not_post_twice():
    store = MemoryGitHub()
    visible = {"items": []}

    first = _plan(store, recheck_deploys=lambda: list(visible["items"]))
    assert first["action"] == "POST_ONCE"
    lost = render_post_followup(action=first["action"], error_code="RENDER_DEPLOY_API_UNREACHABLE")
    assert lost == {"post": False, "retry": False, "ambiguous": True, "can_execute": False}
    assert ambiguous_render_ack("RENDER_DEPLOY_API_HTTP_503") is True
    assert ambiguous_render_ack("RENDER_DEPLOY_API_HTTP_401") is False
    rejected = render_post_followup(action="POST_ONCE", error_code="RENDER_DEPLOY_API_HTTP_422")
    assert rejected["retry"] is False
    assert rejected["ambiguous"] is False

    visible["items"] = [{"id": "dep-delayed", "status": "build_in_progress", "commit": {"id": SHA}}]
    second = _plan(store, deploys=list(visible["items"]), recheck_deploys=lambda: list(visible["items"]))
    assert second["action"] == "RECONCILE"
    assert second["deploy_id"] == "dep-delayed"
    assert second["retry"] is False
    assert _render_posts(second) == []


def test_main_moving_after_intent_reservation_does_not_post(monkeypatch):
    store = MemoryGitHub()
    decision = _plan(store, recheck_main_sha=lambda: OTHER, recheck_deploys=lambda: [])

    assert decision["action"] == "HOLD"
    assert decision["status"] == "STALE_SHA_NO_DEPLOY"
    assert _render_posts(decision) == []
    assert store.refs[f"refs/heads/wow-release/intent/{SERVICE}/{SHA}"] == SHA
    assert f"refs/heads/wow-release/lock/{SERVICE}" not in store.refs

    later = _plan(store, recheck_main_sha=lambda: SHA)
    assert later["action"] == "RECONCILE"
    assert _render_posts(later) == []


def test_permission_denied_ref_create_holds_and_stops():
    store = MemoryGitHub(create_status=403)
    decision = _plan(store)
    assert decision["action"] == "HOLD"
    assert decision["blockers"] == ["RELEASE_JOURNAL_REF_PERMISSION_DENIED"]
    assert _render_posts(decision) == []
    assert sum(1 for method, _path, _payload in store.calls if method == "POST") == 1


def test_observation_timeout_is_read_only():
    decision = observation_decision(
        timed_out=True,
        deploy_status="build_in_progress",
        deploy_sha=SHA,
        target_sha=SHA,
        current_main_sha=SHA,
    )
    assert decision["action"] == "TIMEOUT_HOLD"
    assert decision["status"] == "DEPLOY_STILL_BUILDING_OBSERVATION_TIMED_OUT"
    assert decision["post"] is False
    assert decision["can_execute"] is False

    live = observation_decision(
        timed_out=False,
        deploy_status="live",
        deploy_sha=SHA,
        target_sha=SHA,
        current_main_sha=OTHER,
    )
    assert live["action"] == "WAIT"
    assert live["post"] is False


def test_lock_release_deletes_only_our_lock_and_keeps_the_intent():
    store = MemoryGitHub()
    journal = RefJournal(store.request)
    assert journal.reserve(SERVICE, SHA, consume_only=False)["action"] == "POST_ONCE"
    released = journal.release_lock(SERVICE, SHA)
    assert released["released"] is True
    intent = f"refs/heads/wow-release/intent/{SERVICE}/{SHA}"
    assert store.refs[intent] == SHA
    assert f"refs/heads/wow-release/lock/{SERVICE}" not in store.refs
    assert not any(call[1].endswith(f"intent/{SERVICE}/{SHA}") and call[0] == "DELETE" for call in store.calls)

    store.refs[f"refs/heads/wow-release/lock/{SERVICE}"] = OTHER
    refused = journal.release_lock(SERVICE, SHA)
    assert refused["released"] is False
    assert refused["reason"] == "LOCK_NOT_OURS"
    assert store.refs[f"refs/heads/wow-release/lock/{SERVICE}"] == OTHER

    orphan = MemoryGitHub()
    orphan.refs[f"refs/heads/wow-release/lock/{SERVICE}"] = SHA
    assert RefJournal(orphan.request).release_lock(SERVICE, SHA)["released"] is True
    assert orphan.refs == {}


def test_check_pagination_fails_closed_when_a_full_page_remains():
    def fetch(page):
        if page < 3:
            return [page] * 100
        return [3]

    assert len(paginate(fetch)) == 201

    with pytest.raises(JournalProtocolError, match="GITHUB_CHECK_PAGE_LIMIT"):
        paginate(lambda _page: [1] * 100, max_pages=2)


def test_repository_workflows_do_not_add_a_second_deploy_writer_or_push_the_journal_branch():
    texts = load_workflow_texts(REPO / ".github" / "workflows")
    inventory = inventory_render_writers(texts)
    assert inventory["complete"] is True
    assert inventory["foreign_posts"] == []
    assert "wow-v17-secret-sync.yml" in inventory["credential_holders"]
    assert "wow-v17-mcp-remote-acceptance.yml" in inventory["credential_holders"]
    assert workflow_posts_render_deploy(texts[CONTROLLER_FILENAME]) is True

    branch = f"wow-release/intent/{SERVICE}/{SHA}"

    def matches(pattern: str, name: str) -> bool:
        pieces = []
        index = 0
        while index < len(pattern):
            if pattern.startswith("**", index):
                pieces.append(".*")
                index += 2
            elif pattern[index] == "*":
                pieces.append("[^/]*")
                index += 1
            elif pattern[index] == "?":
                pieces.append("[^/]")
                index += 1
            else:
                pieces.append(re.escape(pattern[index]))
                index += 1
        return re.fullmatch("".join(pieces), name) is not None

    for path in sorted((REPO / ".github" / "workflows").glob("*.yml")):
        document = yaml.safe_load(path.read_text(encoding="utf-8"))
        trigger = document.get("on", document.get(True))
        if isinstance(trigger, list):
            assert "push" not in trigger and "create" not in trigger, path.name
            continue
        if not isinstance(trigger, dict):
            continue
        assert "create" not in trigger, path.name
        push = trigger.get("push")
        if not isinstance(push, dict):
            continue
        branches = push.get("branches")
        assert branches, path.name
        if isinstance(branches, str):
            branches = [branches]
        assert not any(matches(str(pattern), branch) for pattern in branches), path.name
