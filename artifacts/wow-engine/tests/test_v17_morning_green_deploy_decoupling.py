from __future__ import annotations

from pathlib import Path


def test_morning_green_nonterminal_state_does_not_poison_main_checkspass_deploy():
    repo_root = Path(__file__).resolve().parents[3]
    workflow = (
        repo_root / ".github" / "workflows" / "wow-v17-morning-green-continuation.yml"
    ).read_text(encoding="utf-8")

    assert "Preserve nonterminal Morning-Green state without poisoning main deploy checks" in workflow
    assert "durable closure evidence has been persisted" in workflow
    assert "deferred=true" in workflow
    assert "successful workflow termination is forbidden" not in workflow


def test_morning_green_still_blocks_merge_until_required_checks_and_scope_are_ready():
    repo_root = Path(__file__).resolve().parents[3]
    workflow = (
        repo_root / ".github" / "workflows" / "wow-v17-morning-green-continuation.yml"
    ).read_text(encoding="utf-8")

    assert "steps.gates.outputs.ready == 'true' && steps.scope.outcome == 'success'" in workflow
    assert "gh pr merge" in workflow
    assert "can_execute: false" in workflow
