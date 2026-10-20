"""#1887: auto-merge must be armed by the wow-engineering App, never github.token.

A github.token-armed merge is executed by github-actions[bot], and GitHub does not
start push workflows for it, so main loses exact-SHA CI and the release handoff.
"""
from __future__ import annotations

from pathlib import Path

import yaml

ROOT = Path(__file__).resolve().parents[2]
WORKFLOW = ROOT / ".github/workflows/wow-v17-enable-auto-merge.yml"


def _load():
    text = WORKFLOW.read_text(encoding="utf-8")
    return text, yaml.safe_load(text)


def _job():
    _, workflow = _load()
    return workflow["jobs"]["arm"]


def _step(name):
    return next(step for step in _job()["steps"] if step.get("name") == name)


def test_merge_is_armed_only_with_the_engineering_app_token():
    text, _ = _load()
    merge = _step("Enable auto-merge")
    assert merge["env"]["GH_TOKEN"] == "${{ steps.eng_token.outputs.token }}"
    assert "${{ github.token }}" not in text
    assert "GITHUB_TOKEN" not in text
    mint = _step("Mint wow-engineering App token")
    assert mint["uses"] == "./.github/actions/wow-engineering-token"
    assert mint["with"]["app-private-key"] == "${{ secrets.WOW_ENGINEERING_APP_PRIVATE_KEY }}"
    assert _job()["environment"] == "wow-engineering"


def test_missing_app_credential_fails_closed_before_arming():
    steps = [step.get("name") for step in _job()["steps"]]
    assert steps.index("Require Engineering App identity") < steps.index("Enable auto-merge")
    guard = _step("Require Engineering App identity")
    assert guard["env"]["KEY_PRESENT"] == "${{ steps.eng_token.outputs.key-present }}"
    assert 'if [ "${KEY_PRESENT:-false}" != "true" ]' in guard["run"]
    assert "exit 1" in guard["run"]
    assert "AUTO_MERGE_APP_IDENTITY_MISSING" in guard["run"]


def test_pr_code_is_never_checked_out_and_job_token_is_read_only():
    _, workflow = _load()
    assert workflow["permissions"] == {}
    assert _job()["permissions"] == {"contents": "read"}
    checkouts = [step for step in _job()["steps"] if str(step.get("uses", "")).startswith("actions/checkout@")]
    assert len(checkouts) == 1
    assert checkouts[0]["with"] == {"ref": "main", "fetch-depth": 1, "persist-credentials": False}


def test_trigger_scope_and_merge_semantics_are_unchanged():
    _, workflow = _load()
    trigger = workflow.get("on", workflow.get(True))
    assert set(trigger) == {"pull_request_target"}
    assert trigger["pull_request_target"]["branches"] == ["main"]
    condition = _job()["if"]
    assert "github.event.pull_request.head.repo.full_name == github.repository" in condition
    assert "github.event.pull_request.draft == false" in condition
    run = _step("Enable auto-merge")["run"]
    assert 'gh pr merge "$PR" --repo "$REPO" --auto --merge' in run
    for forbidden in ("--admin", "--squash", "--rebase", "workflow run", "gh api", "dispatches"):
        assert forbidden not in run
    assert workflow["concurrency"]["cancel-in-progress"] is True


def test_no_competing_post_merge_dispatch_is_reintroduced():
    text, _ = _load()
    for forbidden in ("workflow_dispatch", "repository_dispatch", "dispatch-protected-deploy", "RENDER_API_KEY", "render"):
        assert forbidden not in text.split("jobs:", 1)[1]
