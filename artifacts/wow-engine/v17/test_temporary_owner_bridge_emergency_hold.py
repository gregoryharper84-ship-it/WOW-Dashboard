"""Fail-closed containment for the single-identity temporary owner release bridge.

The temporary bridge must not run until a genuinely separate QA identity,
isolated owner release authorization and protected independent bootstrap exist.
"""

from pathlib import Path


def test_unsafe_temporary_release_bridge_is_inert():
    root = Path(__file__).resolve().parents[3]
    workflow = (root / ".github/workflows/wow-v17-temporary-owner-release-bridge.yml").read_text(
        encoding="utf-8"
    )
    header, jobs = workflow.split("\njobs:\n", 1)
    assert "  issue_comment:" not in header
    assert "  workflow_dispatch:" in header
    assert "  owner-bridge:\n" in jobs
    assert "    if: ${{ false }}" in jobs
    assert "\n      OWNER_APPROVAL:" not in jobs
    # The retired job still contains advisory QA and merge steps in source;
    # disabling the whole job is required until they are isolated by principal.
    assert "Independent read-only QA" in jobs
    assert "Merge exact authorized head" in jobs
