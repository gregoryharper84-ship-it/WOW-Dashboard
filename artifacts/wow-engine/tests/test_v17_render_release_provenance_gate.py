"""Regression: production deploy requires independent exact PR-head role checks.

These fixtures execute the *actual* embedded workflow function rather than
only asserting its text. This is a non-deploying, no-secret test suite.
"""
import ast
from pathlib import Path
import re
import textwrap
import urllib.error

import pytest


WORKFLOW = Path(__file__).resolve().parents[3] / ".github/workflows/wow-v17-render-production-deploy.yml"
REPO = "gregoryharper84-ship-it/WOW-Dashboard"
MERGE_SHA = "b" * 40
HEAD_SHA = "a" * 40
QA_APP_ID = "111"
RELEASE_APP_ID = "222"


def _gate():
    text = WORKFLOW.read_text(encoding="utf-8")
    script = text.split("python - <<'PY'\n", 1)[1].split("\n          PY", 1)[0]
    tree = ast.parse(textwrap.dedent(script))
    func = next(
        n for n in tree.body
        if isinstance(n, ast.FunctionDef) and n.name == "validate_release_provenance"
    )
    namespace = {}
    exec(compile(ast.Module(body=[func], type_ignores=[]), "<workflow>", "exec"), namespace)
    return namespace["validate_release_provenance"]


def _embedded_function(name, **overrides):
    text = WORKFLOW.read_text(encoding="utf-8")
    script = text.split("python - <<'PY'\n", 1)[1].split("\n          PY", 1)[0]
    tree = ast.parse(textwrap.dedent(script))
    func = next(n for n in tree.body if isinstance(n, ast.FunctionDef) and n.name == name)
    namespace = {
        "MAX_WORKFLOW_RUN_ATTEMPTS": 3,
        "DEPLOY_WORKFLOW_PATH": ".github/workflows/wow-v17-render-production-deploy.yml",
        "urllib": urllib,
        "re": re,
        "journal_preflight": {"eligible": True},
        "guarded_render_post": lambda _preflight, render_json, path, payload: render_json(
            "POST", path, payload
        ),
    }
    namespace.update(overrides)
    exec(compile(ast.Module(body=[func], type_ignores=[]), "<workflow>", "exec"), namespace)
    return namespace[name]


def _fixture():
    pr = {
        "number": 101, "merged_at": "2026-10-11T21:00:00Z",
        "merge_commit_sha": MERGE_SHA,
        "base": {"ref": "main", "repo": {"full_name": REPO}},
        "head": {"sha": HEAD_SHA, "repo": {"full_name": REPO}},
        "merged": True,
    }
    checks = [
        {
            "id": 10, "name": "WOW Independent QA exact-head", "head_sha": HEAD_SHA,
            "app": {"id": int(QA_APP_ID)}, "status": "completed", "conclusion": "success",
        },
        {
            "id": 11, "name": "WOW Release Authority exact-head", "head_sha": HEAD_SHA,
            "app": {"id": int(RELEASE_APP_ID)}, "status": "completed", "conclusion": "success",
        },
    ]
    return dict(
        target_sha=MERGE_SHA, repository=REPO, pull_requests=[pr],
        merged_pr=pr, check_runs=checks,
        qa_app_id=QA_APP_ID, release_app_id=RELEASE_APP_ID,
    )


def test_workflow_embedded_python_compiles():
    assert _gate().__name__ == "validate_release_provenance"
    assert _embedded_function("validate_deployment_lease_identity").__name__ == "validate_deployment_lease_identity"
    assert _embedded_function("render_deploy_payload").__name__ == "render_deploy_payload"
    assert _embedded_function("is_current_main_sha").__name__ == "is_current_main_sha"
    assert _embedded_function("github_deployment_journal_preflight").__name__ == "github_deployment_journal_preflight"
    assert _embedded_function("guarded_render_post").__name__ == "guarded_render_post"
    assert _embedded_function("validate_secret_sync_release_request").__name__ == "validate_secret_sync_release_request"


def test_secret_sync_request_requires_successful_exact_main_run_and_artifact():
    validate = _embedded_function("validate_secret_sync_release_request")
    run_id, attempt, sha = "81234", "2", "a" * 40
    request = {"run_id": run_id, "run_attempt": attempt, "source_sha": sha}
    source_run = {
        "id": int(run_id),
        "run_attempt": int(attempt),
        "path": ".github/workflows/wow-v17-secret-sync.yml@refs/heads/main",
        "repository": {"full_name": REPO},
        "head_branch": "main",
        "head_sha": sha,
        "status": "completed",
        "conclusion": "success",
        "event": "schedule",
    }
    artifacts = [{
        "total_count": 1,
        "artifacts": [{
            "name": f"wow-v17-secret-env-release-request-{run_id}-{attempt}",
            "expired": False,
        }],
    }]

    result = validate(
        request=request,
        repository=REPO,
        target_sha=sha,
        source_run=source_run,
        artifact_pages=artifacts,
    )

    assert result["request_type"] == "SECRET_ENV_CHANGED_RELEASE_REQUEST"
    assert result["artifact_name"] == artifacts[0]["artifacts"][0]["name"]
    assert result["source_sha"] == sha


@pytest.mark.parametrize(
    "mutate,code",
    [
        (lambda request, run, pages: run.update(status="in_progress"), "SECRET_ENV_RELEASE_REQUEST_RUN_MISMATCH"),
        (lambda request, run, pages: run.update(head_branch="feature"), "SECRET_ENV_RELEASE_REQUEST_RUN_MISMATCH"),
        (lambda request, run, pages: pages[0]["artifacts"][0].update(expired=True), "SECRET_ENV_RELEASE_REQUEST_ARTIFACT_MISSING"),
        (lambda request, run, pages: pages[0].update(total_count=2), "SECRET_ENV_RELEASE_REQUEST_ARTIFACT_INVENTORY_INCOMPLETE"),
    ],
)
def test_secret_sync_request_invalid_source_or_artifact_holds(mutate, code):
    run_id, attempt, sha = "81234", "1", "a" * 40
    request = {"run_id": run_id, "run_attempt": attempt, "source_sha": sha}
    source_run = {
        "id": int(run_id),
        "run_attempt": int(attempt),
        "path": ".github/workflows/wow-v17-secret-sync.yml@refs/heads/main",
        "repository": {"full_name": REPO},
        "head_branch": "main",
        "head_sha": sha,
        "status": "completed",
        "conclusion": "success",
        "event": "workflow_dispatch",
    }
    artifacts = [{
        "total_count": 1,
        "artifacts": [{
            "name": f"wow-v17-secret-env-release-request-{run_id}-{attempt}",
            "expired": False,
        }],
    }]
    mutate(request, source_run, artifacts)

    with pytest.raises(RuntimeError, match=code):
        _embedded_function("validate_secret_sync_release_request")(
            request=request,
            repository=REPO,
            target_sha=sha,
            source_run=source_run,
            artifact_pages=artifacts,
        )


def test_secret_sync_request_can_reconcile_partial_failed_run_with_durable_artifact():
    run_id, attempt, sha = "81234", "2", "a" * 40
    run = {
        "id": int(run_id),
        "run_attempt": int(attempt),
        "path": ".github/workflows/wow-v17-secret-sync.yml@refs/heads/main",
        "repository": {"full_name": REPO},
        "head_branch": "main",
        "head_sha": sha,
        "status": "completed",
        "conclusion": "failure",
        "event": "workflow_dispatch",
    }
    artifacts = [{
        "total_count": 1,
        "artifacts": [{
            "name": f"wow-v17-secret-env-release-request-{run_id}-{attempt}",
            "expired": False,
        }],
    }]

    result = _embedded_function("validate_secret_sync_release_request")(
        request={"run_id": run_id, "run_attempt": attempt, "source_sha": sha},
        repository=REPO,
        target_sha=sha,
        source_run=run,
        artifact_pages=artifacts,
    )

    assert result["request_type"] == "SECRET_ENV_CHANGED_RELEASE_REQUEST"


def test_secret_sync_request_sha_must_match_dispatch_target():
    run_id, attempt, sha = "81234", "1", "a" * 40
    with pytest.raises(RuntimeError, match="SECRET_ENV_RELEASE_REQUEST_SHA_OR_ID_INVALID"):
        _embedded_function("validate_secret_sync_release_request")(
            request={"run_id": run_id, "run_attempt": attempt, "source_sha": sha},
            repository=REPO,
            target_sha="b" * 40,
            source_run={},
            artifact_pages=[],
        )


def test_github_deployment_journal_preflight_never_claims_atomic_create():
    preflight = _embedded_function("github_deployment_journal_preflight")

    result = preflight(
        writer_inventory_complete=True,
        live_auto_deploy_trigger="off",
        git_ref_write_once_protection_verified=False,
        service_lock_release_cas_verified=False,
    )

    assert result["eligible"] is False
    assert result["status"] == "RELEASE_JOURNAL_PREFLIGHT_HOLD_NO_DEPLOY"
    assert "GITHUB_DEPLOYMENT_CREATE_HAS_NO_ATOMIC_IDEMPOTENCY" in result["blockers"]
    assert "GITHUB_GIT_REF_SERVICE_LOCK_RELEASE_CAS_UNAVAILABLE" in result["blockers"]
    assert "GITHUB_GIT_REF_ATOMIC_SERVICE_FENCE_NOT_IMPLEMENTED" in result["blockers"]
    assert "RENDER_NATIVE_AUTO_DEPLOY_WRITER_ENABLED" not in result["blockers"]


@pytest.mark.parametrize(
    "writer_inventory_complete,live_auto_deploy,protection_verified,release_cas_verified,expected_blocker",
    [
        (False, "off", True, True, "RENDER_SERVICE_WRITER_INVENTORY_UNVERIFIED"),
        (True, None, True, True, "RENDER_LIVE_AUTO_DEPLOY_STATE_UNVERIFIED"),
        (True, "checksPass", True, True, "RENDER_NATIVE_AUTO_DEPLOY_WRITER_ENABLED"),
        (True, "off", False, True, "GITHUB_GIT_REF_WRITE_ONCE_PROTECTION_UNVERIFIED"),
        (True, "off", True, False, "GITHUB_GIT_REF_SERVICE_LOCK_RELEASE_CAS_UNAVAILABLE"),
    ],
)
def test_github_deployment_journal_preflight_records_writer_conflicts(
    writer_inventory_complete, live_auto_deploy, protection_verified, release_cas_verified, expected_blocker
):
    result = _embedded_function("github_deployment_journal_preflight")(
        writer_inventory_complete=writer_inventory_complete,
        live_auto_deploy_trigger=live_auto_deploy,
        git_ref_write_once_protection_verified=protection_verified,
        service_lock_release_cas_verified=release_cas_verified,
    )

    assert result["eligible"] is False
    assert expected_blocker in result["blockers"]
    assert "GITHUB_GIT_REF_ATOMIC_SERVICE_FENCE_NOT_IMPLEMENTED" in result["blockers"]


def test_guarded_render_post_rejects_without_calling_render_api():
    guarded_post = _embedded_function("guarded_render_post")
    preflight = _embedded_function("github_deployment_journal_preflight")(
        writer_inventory_complete=False,
        live_auto_deploy_trigger=None,
        git_ref_write_once_protection_verified=False,
        service_lock_release_cas_verified=False,
    )
    render_calls = []

    for path, payload in (
        ("/services/service/deploys", {"commitId": "a" * 40}),
        ("/services/service/rollback", {"deployId": "dep-previous"}),
    ):
        with pytest.raises(RuntimeError, match="RENDER_RELEASE_JOURNAL_PREFLIGHT_HOLD"):
            guarded_post(
                preflight,
                lambda *args: render_calls.append(args),
                path,
                payload,
            )

    assert render_calls == []


def test_exact_same_repo_independent_app_checks_pass():
    assert _gate()(**_fixture()) == HEAD_SHA


@pytest.mark.parametrize("field,value,code", [
    ("qa_app_id", "", "RENDER_RELEASE_APP_IDENTITIES_UNBOUND"),
    ("release_app_id", "invalid", "RENDER_RELEASE_APP_IDENTITIES_UNBOUND"),
    ("release_app_id", QA_APP_ID, "RENDER_RELEASE_APP_IDENTITIES_NOT_DISTINCT"),
])
def test_missing_or_same_identity_fails_closed(field, value, code):
    packet = _fixture()
    packet[field] = value
    with pytest.raises(RuntimeError, match=code):
        _gate()(**packet)


def test_checks_from_foreign_app_do_not_authorize_release():
    packet = _fixture()
    packet["check_runs"][0]["app"]["id"] = 333
    with pytest.raises(RuntimeError, match="RENDER_RELEASE_ROLE_CHECK_MISSING_OR_FOREIGN"):
        _gate()(**packet)


@pytest.mark.parametrize("status,conclusion", [
    ("in_progress", None),
    ("completed", "failure"),
    ("completed", "skipped"),
])
def test_latest_non_success_cannot_be_replaced_by_older_green(status, conclusion):
    packet = _fixture()
    later = dict(packet["check_runs"][0], id=999, status=status, conclusion=conclusion)
    packet["check_runs"].append(later)
    with pytest.raises(RuntimeError, match="RENDER_RELEASE_ROLE_CHECK_NOT_SUCCESS"):
        _gate()(**packet)


def test_stale_qa_head_does_not_authorize_merge():
    packet = _fixture()
    packet["check_runs"][0]["head_sha"] = "c" * 40
    with pytest.raises(RuntimeError, match="RENDER_RELEASE_ROLE_CHECK_MISSING_OR_FOREIGN"):
        _gate()(**packet)


def test_draft_source_pr_cannot_authorize_render_deploy():
    packet = _fixture()
    packet["merged_pr"]["draft"] = True
    with pytest.raises(RuntimeError, match="RENDER_RELEASE_MERGED_PR_NOT_UNIQUE"):
        _gate()(**packet)


def test_deployment_lease_identity_binds_protected_workflow_run():
    run = {
        "id": 98765,
        "path": ".github/workflows/wow-v17-render-production-deploy.yml@refs/heads/main",
        "repository": {"full_name": REPO},
        "event": "workflow_run",
        "head_branch": "main",
        "status": "in_progress",
        "run_attempt": 2,
    }

    identity = _embedded_function("validate_deployment_lease_identity")(
        run, repository=REPO, event="workflow_run", run_id="98765", run_attempt="2"
    )

    assert identity == f"{REPO}:.github/workflows/wow-v17-render-production-deploy.yml:98765:2"


@pytest.mark.parametrize("field,value,code", [
    ("id", 98766, "RENDER_DEPLOY_LEASE_IDENTITY_MISMATCH"),
    ("path", ".github/workflows/other.yml@refs/heads/main", "RENDER_DEPLOY_LEASE_IDENTITY_MISMATCH"),
    ("repository", {"full_name": "attacker/fork"}, "RENDER_DEPLOY_LEASE_IDENTITY_MISMATCH"),
    ("event", "pull_request", "RENDER_DEPLOY_LEASE_IDENTITY_MISMATCH"),
    ("head_branch", "feature", "RENDER_DEPLOY_LEASE_IDENTITY_MISMATCH"),
    ("status", "completed", "RENDER_DEPLOY_LEASE_IDENTITY_MISMATCH"),
    ("run_attempt", 1, "RENDER_DEPLOY_LEASE_IDENTITY_MISMATCH"),
])
def test_foreign_or_stale_deployment_lease_fails_closed(field, value, code):
    run = {
        "id": 98765,
        "path": ".github/workflows/wow-v17-render-production-deploy.yml@refs/heads/main",
        "repository": {"full_name": REPO},
        "event": "workflow_run",
        "head_branch": "main",
        "status": "in_progress",
        "run_attempt": 2,
    }
    run[field] = value
    with pytest.raises(RuntimeError, match=code):
        _embedded_function("validate_deployment_lease_identity")(
            run, repository=REPO, event="workflow_run", run_id="98765", run_attempt="2"
        )


def test_deployment_lease_retries_are_bounded_and_post_pins_exact_sha():
    validate = _embedded_function("validate_deployment_lease_identity")
    run = {
        "id": 98765,
        "path": ".github/workflows/wow-v17-render-production-deploy.yml",
        "repository": {"full_name": REPO},
        "event": "workflow_run",
        "head_branch": "main",
        "status": "in_progress",
        "run_attempt": 4,
    }
    with pytest.raises(RuntimeError, match="RENDER_DEPLOY_ATTEMPT_LIMIT_EXCEEDED"):
        validate(run, repository=REPO, event="workflow_run", run_id="98765", run_attempt="4")

    commit_sha = "b" * 40
    assert _embedded_function("render_deploy_payload")(commit_sha) == {
        "clearCache": "do_not_clear", "commitId": commit_sha,
    }
    with pytest.raises(RuntimeError, match="RENDER_DEPLOY_TARGET_SHA_INVALID"):
        _embedded_function("render_deploy_payload")("stale-or-short-sha")

    current_main_matches = _embedded_function("is_current_main_sha")
    assert current_main_matches(commit_sha, commit_sha)
    assert not current_main_matches(commit_sha, "c" * 40)
    assert not current_main_matches("not-a-sha", "not-a-sha")


@pytest.mark.parametrize("previous_live,mismatch_deploy_id", [
    ({"id": "dep-previous", "commit": {"id": "a" * 40}, "status": "live"}, "dep-mismatch"),
    (None, "dep-mismatch"),
    ({"id": "dep-previous", "commit": {"id": "a" * 40}, "status": "live"}, ""),
])
def test_rollback_without_distinct_authority_holds_without_provider_mutation(
    previous_live, mismatch_deploy_id,
):
    """A mismatched SHA cannot authorize a new rollback, even after lost ACK."""
    mismatch_sha = "b" * 40
    receipts, mutations = [], []

    def forbidden_provider(*args, **kwargs):
        mutations.append((args, kwargs))
        raise AssertionError("unapproved Render API access")

    rollback_function = _embedded_function(
        "rollback_after_sha_mismatch",
        target_sha=mismatch_sha,
        commit_sha=lambda deploy: str((deploy.get("commit") or {}).get("id") or ""),
        render_json=forbidden_provider,
        list_deploys=forbidden_provider,
        guarded_render_post=forbidden_provider,
        emit_receipt=receipts.append,
    )

    with pytest.raises(SystemExit) as result:
        rollback_function(previous_live, mismatch_sha, mismatch_deploy_id)
    assert result.value.code == 0
    assert mutations == []
    assert len(receipts) == 1
    receipt = receipts[0]
    assert receipt["status"] == "DEPLOY_SHA_MISMATCH_HOLD"
    assert receipt["rollback_status"] == "ROLLBACK_AUTHORIZATION_REQUIRED_HOLD"
    assert receipt["rollback_attempts"] == 0
    assert receipt["observed_sha"] == mismatch_sha
    assert receipt["mismatched_deploy_id"] == mismatch_deploy_id
    assert receipt["previous_live_sha"] == (
        str((previous_live or {}).get("commit", {}).get("id") or "")
    )
    assert receipt["can_execute"] is False
    assert "restored_commit_sha" not in receipt
    assert "rollback_deploy_id" not in receipt


def test_render_observation_window_is_monotonic_and_never_reposts_after_timeout():
    text = WORKFLOW.read_text(encoding="utf-8")
    assert "timeout-minutes: 120" in text
    assert "deploy_deadline = time.monotonic() + 90 * 60" in text
    assert "if time.monotonic() >= deploy_deadline:" in text
    assert "DEPLOY_STILL_BUILDING_OBSERVATION_TIMED_OUT" in text
    assert "Do not issue a second" in text
    assert 'f"/services/{service_path}/rollback"' not in text
    assert "rollback_attempts < 3" not in text
    # Preflight must stay closed until a genuine service-wide atomic intent.
    assert "writer_inventory_complete=False" in text
    assert "service_lock_release_cas_verified=False" in text


@pytest.mark.parametrize("failure,expected", [
    ("no_pr", "RENDER_RELEASE_MERGED_PR_NOT_UNIQUE"),
    ("duplicate_pr", "RENDER_RELEASE_MERGED_PR_NOT_UNIQUE"),
    ("not_merged", "RENDER_RELEASE_PR_MERGE_IDENTITY_MISMATCH"),
    ("wrong_base", "RENDER_RELEASE_MERGED_PR_NOT_UNIQUE"),
    ("fork", "RENDER_RELEASE_PR_FORK_UNVERIFIED"),
    ("wrong_sha", "RENDER_RELEASE_MERGED_PR_NOT_UNIQUE"),
])
def test_untrusted_merge_provenance_fails_closed(failure, expected):
    packet = _fixture()
    if failure == "no_pr":
        packet["pull_requests"] = []
    elif failure == "duplicate_pr":
        packet["pull_requests"].append(dict(packet["merged_pr"], number=102))
    elif failure == "not_merged":
        packet["merged_pr"]["merged"] = False
    elif failure == "wrong_base":
        packet["merged_pr"]["base"]["ref"] = "unprotected"
    elif failure == "fork":
        packet["merged_pr"]["head"]["repo"]["full_name"] = "attacker/fork"
    elif failure == "wrong_sha":
        packet["merged_pr"]["merge_commit_sha"] = "d" * 40
    with pytest.raises(RuntimeError, match=expected):
        _gate()(**packet)


def test_live_control_plane_invokes_guard_before_render_deploy_post():
    text = WORKFLOW.read_text(encoding="utf-8")
    assert 'WOW_QA_APP_ID: ${{ vars.WOW_QA_APP_ID }}' in text
    assert 'WOW_RELEASE_APP_ID: ${{ vars.WOW_RELEASE_APP_ID }}' in text
    assert 'validate_release_provenance(' in text
    assert '/pulls?per_page=100' in text
    assert "RENDER_RELEASE_PROVENANCE_VERIFIED" in text
    assert text.index("authorized_pr_head = validate_release_provenance(") < text.index(
        "created = guarded_render_post("
    )
    assert 'WOW_ACTION_API_KEY' not in text
    assert 'WOW_CAN_EXECUTE: "false"' in text
