"""Regression: production deploy requires independent exact PR-head role checks.

These fixtures execute the *actual* embedded workflow function rather than
only asserting its text. This is a non-deploying, no-secret test suite.
"""
import ast
from pathlib import Path
import textwrap

import pytest


WORKFLOW = Path(__file__).resolve().parents[3] / ".github/workflows/wow-v17-render-production-deploy.yml"
REPO = "gregoryharper84-ship-it/WOW-Dashboard"
MERGE_SHA = "b" * 40
HEAD_SHA = "a" * 40
QA_APP_ID = "111"
RELEASE_APP_ID = "222"


def _gate():
    text = WORKFLOW.read_text(encoding="utf-8")
    release_step = text.split(
        "      - name: Verify protected-main gates and deploy exact SHA", 1
    )[1].split(
        "      - name: Reconcile exact Render receipt to durable deployment pointer", 1
    )[0]
    script = release_step.split("python - <<'PY'\n", 1)[1].split("\n          PY", 1)[0]
    tree = ast.parse(textwrap.dedent(script))
    func = next(
        n for n in tree.body
        if isinstance(n, ast.FunctionDef) and n.name == "validate_release_provenance"
    )
    namespace = {}
    exec(compile(ast.Module(body=[func], type_ignores=[]), "<workflow>", "exec"), namespace)
    return namespace["validate_release_provenance"]


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
    assert text.index("authorized_pr_head = validate_release_provenance(") < text.index('render_json(\n                  "POST"')
    assert 'WOW_ACTION_API_KEY' not in text
    assert 'WOW_CAN_EXECUTE: "false"' in text
