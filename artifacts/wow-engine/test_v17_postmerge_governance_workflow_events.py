"""V4 regression: real protected-main CI provenance replaces legacy receipt.

The old source-review producer remains historically testable, but it is not a
postmerge deployment prerequisite. The new observer is read-only and never
self-certifies production or fitted sporting probability.
"""
from __future__ import annotations

import sys
from pathlib import Path

import pytest

ROOT = Path(__file__).resolve().parents[2]
sys.path.insert(0, str(ROOT / "artifacts/wow-engine/v17"))
import lean_production_readback as observer

RELEASE = ROOT / ".github/workflows/wow-v17-release-production-verification-agent.yml"
READBACK = ROOT / ".github/workflows/wow-v17-lean-production-readback.yml"
HEAD = "a" * 40


def authentic(event="push"):
    return {
        "name": observer.SOURCE_WORKFLOW,
        "path": observer.SOURCE_PATH,
        "repository": {"full_name": observer.REPO},
        "head_repository": {"full_name": observer.REPO},
        "head_branch": "main",
        "head_sha": HEAD,
        "status": "completed",
        "conclusion": "success",
        "event": event,
    }


@pytest.mark.parametrize("event", ["push", "workflow_dispatch"])
def test_main_ci_producer_events_accepted_for_observation_only(event):
    observer.validate_source_run(authentic(event), target_sha=HEAD)


@pytest.mark.parametrize("event", [
    "pull_request_target", "pull_request", "schedule",
    "check_suite", "repository_dispatch", "workflow_run", "check_run",
])
def test_untrusted_main_ci_events_rejected(event):
    with pytest.raises(observer.ReadbackHold, match="MAIN_CI_SOURCE_NOT_AUTHENTIC_EXACT_SUCCESS"):
        observer.validate_source_run(authentic(event), target_sha=HEAD)


@pytest.mark.parametrize("branch", ["feature/candidate", "refs/pull/2154/merge", "", "staging"])
def test_source_ci_run_must_be_protected_main(branch):
    data = authentic()
    data["head_branch"] = branch
    with pytest.raises(observer.ReadbackHold, match="MAIN_CI_SOURCE_NOT_AUTHENTIC_EXACT_SUCCESS"):
        observer.validate_source_run(data, target_sha=HEAD)


@pytest.mark.parametrize("field,value", [
    ("name", "wow-v17-independent-source-review"),
    ("path", ".github/workflows/wow-v17-trusted-governance-gate.yml"),
    ("status", "in_progress"),
    ("conclusion", "neutral"),
    ("head_sha", "b" * 40),
])
def test_unrelated_or_stale_workflow_cannot_impersonate_main_ci(field, value):
    data = authentic()
    data[field] = value
    with pytest.raises(observer.ReadbackHold, match="MAIN_CI_SOURCE_NOT_AUTHENTIC_EXACT_SUCCESS"):
        observer.validate_source_run(data, target_sha=HEAD)


def test_repository_identity_and_expected_head_still_bound():
    data = authentic()
    data["head_repository"]["full_name"] = "attacker/repo"
    with pytest.raises(observer.ReadbackHold, match="MAIN_CI_SOURCE_NOT_AUTHENTIC_EXACT_SUCCESS"):
        observer.validate_source_run(data, target_sha=HEAD)
    with pytest.raises(observer.ReadbackHold, match="MAIN_CI_SOURCE_NOT_AUTHENTIC_EXACT_SUCCESS"):
        observer.validate_source_run(authentic(), target_sha="b" * 40)


def test_v4_release_has_five_pinned_checks_and_no_retired_consumer():
    source = RELEASE.read_text(encoding="utf-8")
    for expected in (
        "WOW required-three regression|15368",
        "WOW governed probability backend|15368",
        "WOW additional required regression|15368",
        "WOW Independent QA exact-head|5245832",
        "WOW Release Authority exact-head|5245853",
    ):
        assert expected in source
    assert "verify_existing_pr_governance_receipt.py" not in source
    assert "Verify governance-producing workflow provenance" not in source
    assert "RELEASE_V4_REQUIRED_CHECK_MISSING_FOREIGN_OR_FAILED" in source


def test_main_readback_is_nonblocking_and_not_a_replacement_sirt_certificate():
    source = READBACK.read_text(encoding="utf-8")
    assert "workflows: [wow-verify]" in source
    assert "continue-on-error: true" in source
    assert "Read-only web/worker Render CI-gated SHA observation" in source
    assert "PENDING_SIRT_INDEPENDENT_ACCEPTANCE" in (
        ROOT / "artifacts/wow-engine/v17/lean_production_readback.py"
    ).read_text(encoding="utf-8")
    assert "gh pr merge" not in source
