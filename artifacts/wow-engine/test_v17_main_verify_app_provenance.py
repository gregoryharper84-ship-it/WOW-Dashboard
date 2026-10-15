"""Regression for protected-main CI dispatch provenance.

No GitHub tokens are issued and no workflow requests are sent by this suite.
"""
from __future__ import annotations

import json
import re
import shutil
import subprocess
from pathlib import Path

import pytest

WORKFLOW = Path(__file__).resolve().parents[2] / ".github/workflows/wow-v17-main-verification-reconciler.yml"
SHA = "a" * 40
OTHER = "b" * 40


def _workflow():
    return WORKFLOW.read_text(encoding="utf-8")


def _selector():
    workflow = _workflow()
    found = re.search(
        r"trusted=\$\(jq -r --arg sha \"\$main_sha\" '([^']+)' <<< \"\$history\"\)",
        workflow,
    )
    assert found is not None, "Contract test must extract the actual protected decision"
    return found.group(1)


def _decide(runs, sha=SHA):
    if not shutil.which("jq"):
        pytest.skip("jq is available on protected Ubuntu GitHub Actions runners")
    result = subprocess.run(
        ["jq", "-r", "--arg", "sha", sha, _selector()],
        input=json.dumps({"workflow_runs": runs}),
        text=True,
        capture_output=True,
        check=True,
        timeout=5,
    )
    return result.stdout.strip()


def _run(*, sha=SHA, event="workflow_dispatch", actor="github-actions[bot]",
         status="completed", conclusion="success", created="2026-10-14T23:27:30Z"):
    return {
        "head_sha": sha,
        "event": event,
        "actor": {"login": actor},
        "status": status,
        "conclusion": conclusion,
        "created_at": created,
    }


def test_workflow_is_restricted_to_protected_main_and_read_only_default_token():
    text = _workflow()
    assert "github.ref == 'refs/heads/main'" in text
    assert "github.repository == 'gregoryharper84-ship-it/WOW-Dashboard'" in text
    head = text.split("jobs:", 1)[0]
    assert "contents: read" in head
    assert "actions: read" in head
    assert "actions: write" not in head
    assert "cancel-in-progress: false" in head
    assert 'test "$WOW_CAN_EXECUTE" = false' in text
    assert 'test "$WOW_DRY_RUN_ONLY" = true' in text


def test_dispatch_uses_scoped_engineering_app_not_github_default_token():
    text = _workflow()
    assert "actions/create-github-app-token@fee1f7d63c2ff003460e3d139729b119787bc349" in text
    assert "app-id: ${{ vars.WOW_ENGINEERING_APP_ID }}" in text
    assert "private-key: ${{ secrets.WOW_ENGINEERING_APP_PRIVATE_KEY }}" in text
    assert "permission-actions: write" in text
    assert "permission-contents: read" in text
    assert "GH_TOKEN: ${{ steps.app_token.outputs.token }}" in text
    assert "GH_TOKEN: ${{ github.token }}" in text
    assert "GITHUB_REPOSITORY/branches/main" in text
    assert "MAIN_SUPERSEDED_HOLD" in text
    assert "EXACT_MAIN_TRUSTED_VERIFY_ALREADY_REQUESTED" in text


def test_old_github_token_trigger_does_not_count_as_trusted_chain():
    assert _decide([_run()]) == "MISSING"


def test_engineering_app_trigger_counts_and_prevents_repeat_dispatch():
    assert _decide([_run(actor="wow-engineering[bot]")]) == "completed|success"


def test_trusted_protected_push_verification_counts():
    assert _decide([_run(event="push", actor="github-actions[bot]")]) == "completed|success"


def test_unrelated_actor_or_pull_request_cannot_satisfy_app_provenance():
    assert _decide([_run(actor="unknown-bot[bot]")]) == "MISSING"
    assert _decide([_run(event="pull_request", actor="wow-engineering[bot]")]) == "MISSING"


def test_different_sha_does_not_count_as_this_main():
    assert _decide([_run(sha=OTHER)]) == "MISSING"


def test_failed_trusted_run_is_not_automatically_retried():
    assert _decide([_run(actor="wow-engineering[bot]", conclusion="failure")]) == "completed|failure"


def test_pending_app_run_prevents_duplicate_scheduling():
    assert _decide([_run(actor="wow-engineering[bot]", status="queued", conclusion=None)]) == "queued|"


def test_mixed_history_ignores_old_bot_and_chooses_fresh_trusted_run():
    runs = [
        _run(actor="wow-engineering[bot]", created="2026-10-14T23:10:00Z"),
        _run(actor="github-actions[bot]", created="2026-10-14T23:27:30Z"),
    ]
    assert _decide(runs) == "completed|success"


def test_unverifiable_inventory_must_fail_closed_before_dispatch():
    source = _workflow()
    assert source.count("MAIN_VERIFY_INVENTORY_UNVERIFIABLE") == 2
    assert source.count("(.workflow_runs | type == \"array\" and length < 100)") == 2
    assert "CHECKS_AND_INDEPENDENT_RELEASE_PROVENANCE_STILL_REQUIRED" in source
