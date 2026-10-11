"""Regression: unverified repository protection must not enable GitHub auto-merge.

Issue #1663. The audit-only workflow cannot grant release authorization.
"""
from pathlib import Path
import re

import pytest
import yaml


ROOT = Path(__file__).resolve().parents[2]
AUTO = ROOT / ".github/workflows/wow-v17-enable-auto-merge.yml"
EXPERIMENT = ROOT / ".github/workflows/wow-v17-model-improvement-experiment.yml"
HOURLY = ROOT / ".github/workflows/wow-v17-24h-engineering-closure-loop.yml"
SIGNED = ROOT / ".github/workflows/wow-v17-owner-signed-release.yml"


def _shell_lines(document: str) -> str:
    """Ignore comments when scanning for direct shell merge/write commands."""
    return "\n".join(line for line in document.splitlines()
                     if not line.lstrip().startswith("#"))


def test_auto_merge_workflow_does_not_arm_or_merge():
    doc = AUTO.read_text(encoding="utf-8")
    shell = _shell_lines(doc)
    assert "pull_request_target:" in doc
    assert "AUTO_MERGE_HOLD_POLICY_UNVERIFIED" in doc
    assert "gh api" in shell  # still audits current exact PR head
    assert "actual_head" in shell and "EXPECTED_HEAD" in shell
    assert not re.search(r"\bgh\s+pr\s+merge\b", shell)
    assert not re.search(r"\bgh\s+api\s+(?:-X|--method)\s+(?:POST|PUT|PATCH|DELETE)\b", shell)
    assert "actions/checkout" not in shell


def test_auto_merge_workflow_has_no_write_authority():
    doc = AUTO.read_text(encoding="utf-8")
    assert "permissions: {}" in doc
    assert re.search(r"(?m)^\s+contents: read\s*$", doc)
    assert re.search(r"(?m)^\s+pull-requests: read\s*$", doc)
    assert not re.search(r"(?m)^\s+(?:contents|pull-requests): write\s*$", doc)
    assert "audit only; NOT release approval" in doc


def test_owner_signed_separate_recovery_path_remains():
    signed = SIGNED.read_text(encoding="utf-8")
    assert "workflow_dispatch:" in signed
    assert "owner_signature:" in signed
    assert 'namespace "wow-release"' in signed
    assert "Never deploys" in signed


def test_no_secondary_auto_merge_arming_routes():
    for path in (AUTO, EXPERIMENT, HOURLY):
        content = path.read_text(encoding="utf-8")
        active = _shell_lines(content)
        assert not re.search(r"\bgh\s+pr\s+merge\b", active), path
        assert "AUTO_MERGE_HOLD_POLICY_UNVERIFIED" in content, path
    # The hourly loop can still diagnose broken experiment CI and dispatch a
    # repair attempt; removing unsafe release rights must not halt engineering.
    hourly = HOURLY.read_text(encoding="utf-8")
    assert "wow-v17-model-improvement-experiment.yml" in hourly
    assert "Failed experiment CI routed to bounded ChatGPT experiment repair." in hourly


def _executable_workflow_commands(path: Path) -> str:
    """Inspect actual workflow shell steps, including multiline continuations.

    YAML parsing avoids finding text in ordinary workflow documentation. Shell
    continuations are joined before checking for a direct GitHub merge command.
    This is a conservative prohibition, not an authorization mechanism.
    """
    workflow = yaml.safe_load(path.read_text(encoding="utf-8")) or {}
    runs = []
    for job in (workflow.get("jobs") or {}).values():
        for step in job.get("steps") or []:
            script = step.get("run") or ""
            # Join shell's backslash-newline continuations (including spaces).
            script = re.sub(r"\\\r?\n[ \t]*", " ", script)
            script = "\n".join(
                line for line in script.splitlines()
                if not line.lstrip().startswith("#")
            )
            runs.append(script)
    return "\n".join(runs)


def _contains_direct_merge(script: str) -> bool:
    return bool(re.search(r"\bgh\s+pr\s+merge\b", script))


@pytest.mark.parametrize("script", [
    'gh pr merge "$PR" --auto --merge',
    'gh pr merge "$PR" --merge --match-head-commit "$SHA"',
    'gh pr \\\n      merge "$PR" --merge',
    'gh \\\n      pr merge "$PR" --merge',
])
def test_direct_merge_guard_catches_auto_manual_and_multiline(script):
    assert _contains_direct_merge(re.sub(r"\\\r?\n[ \t]*", " ", script))


def test_no_unreviewed_direct_merge_in_any_workflow():
    """Direct PR merge is reserved for separately signed owner-only workflow."""
    workflows = ROOT / ".github/workflows"
    for path in sorted(workflows.glob("*.yml")) + sorted(workflows.glob("*.yaml")):
        if path.name == "wow-v17-owner-signed-release.yml":
            continue
        script = _executable_workflow_commands(path)
        assert not _contains_direct_merge(script), path
        assert "enablePullRequestAutoMerge" not in script, path
