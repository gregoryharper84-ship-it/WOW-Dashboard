"""Regression: advisory governance lookup must not exhaust the GitHub App API budget.

The gate remains fail closed on no valid exact-head receipt. This file checks
bounded source behavior; it does not grant or fake independent authorization.
"""
from pathlib import Path

ROOT = Path(__file__).resolve().parents[3]
GATE = ROOT / ".github" / "workflows" / "wow-v17-trusted-governance-gate.yml"


def _direct_pr_artifact_lookup() -> str:
    source = GATE.read_text(encoding="utf-8")
    return source.split('artifact_name="wow-v17-engineering-governance-${head_sha}"', 1)[1].split(
        'if [ -z "$artifact_id" ] || [ -z "$run_id" ]; then', 1
    )[0]


def test_direct_pr_artifact_poll_is_bounded_and_sparse():
    section = _direct_pr_artifact_lookup()
    poll_clause = "for _ in $(seq 1 "
    assert poll_clause in section, "direct PR artifact probe must be explicit"
    poll_count = int(section.split(poll_clause, 1)[1].split("); do", 1)[0])
    assert 1 <= poll_count <= 8
    assert "sleep 30" in section
    assert "GOVERNANCE_ARTIFACT_LOOKUP_FAILED" in section
    assert "GOVERNANCE_RECEIPT_MISSING_OR_DUPLICATE" in section


def test_exact_head_and_nonapproval_guards_are_retained():
    source = GATE.read_text(encoding="utf-8")
    assert "GOVERNANCE_RECEIPT_CHECKSUM_MISMATCH" in source
    assert "GOVERNANCE_PR_IDENTITY_MOVED_OR_UNTRUSTED" in source
    assert ".event == \"workflow_run\"" in source
    assert ".status == \"completed\" and .conclusion == \"success\"" in source
