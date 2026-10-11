"""Regression: unverified repository protection must not enable GitHub auto-merge.

Issue #1663. The audit-only workflow cannot grant release authorization.
"""
from pathlib import Path
import re


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
