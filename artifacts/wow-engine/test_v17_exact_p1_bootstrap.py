"""Trust-root bootstrap regression checks. This does NOT self-certify trusted workflows.

Independent bootstrap authority, SIRT and protected exact-head review are
required before these workflow changes can be merged.
"""
from pathlib import Path

import pytest
import yaml


WORKFLOW_DIR = Path(__file__).resolve().parents[2] / ".github" / "workflows"
PRIMARY = "wow-v17-chatgpt-engineering-worker.yml"
FALLBACK = "wow-v17-claude-engineering-worker.yml"
PROVIDER = "wow-v17-engineering-provider-dispatcher.yml"


@pytest.mark.parametrize("workflow", [PRIMARY, FALLBACK])
def test_exact_p1_is_bound_to_approved_standard_global_lease(workflow):
    text = (WORKFLOW_DIR / workflow).read_text()
    assert 'record=$(jq -c --arg id "$TARGET_INCIDENT"' in text
    assert 'TARGET_INCIDENT_NOT_ACTIONABLE' in text
    assert '[ "$severity" = "P1" ] && [ "$lane" = "STANDARD" ]' in text
    assert 'STANDARD_TARGET_MUST_USE_GLOBAL_LEASE' in text
    assert 'lease_group=GLOBAL' in text
    assert 'EXACT_P1_GLOBAL_LEASE' in text
    assert 'TARGET_INCIDENT_LEASE_MISMATCH' in text
    assert 'TARGET_INCIDENT_NOT_SUPPORTED' in text
    assert 'target_hold' in text
    assert 'target_reason' in text
    assert 'V17_TERMINAL_REDUCER' in text
    assert 'can_execute:false' in text


@pytest.mark.parametrize("workflow", [PRIMARY, FALLBACK])
def test_exact_p0_is_still_domain_fenced(workflow):
    text = (WORKFLOW_DIR / workflow).read_text()
    assert '[ "$severity" = "P0" ] && [ "$lane" = "RAPID" ]' in text
    assert 'P0_DOMAIN_LEASE_MISSING' in text
    assert 'DOMAIN_SCOPED_P0_LEASE' in text
    assert 'TARGET_INCIDENT_LEASE_MISMATCH' in text
    assert 'TARGET_INCIDENT_NOT_ACTIONABLE' in text


def test_provider_preserves_numeric_target_and_defers_lease_to_worker():
    text = (WORKFLOW_DIR / PROVIDER).read_text()
    assert 'TARGET_INCIDENT_INVALID' in text
    assert 'TARGET_INCIDENT_REQUIRES_DOMAIN_LEASE' not in text
    assert '[[ "$target_incident" =~ ^[0-9]+$ ]]' in text
    assert "wow-v17-chatgpt-engineering-worker.yml" in text
    assert "wow-v17-claude-engineering-worker.yml" in text


def test_workflow_syntax_and_independent_trust_root_protection():
    for name in (PRIMARY, FALLBACK, PROVIDER):
        yaml.safe_load((WORKFLOW_DIR / name).read_text())
    gate = (WORKFLOW_DIR / "wow-v17-existing-pr-governance-review.yml").read_text()
    for name in (PRIMARY, FALLBACK, PROVIDER):
        assert name in gate
    assert "EXISTING_PR_TRUST_ROOT_CHANGE_REQUIRES_WORKER_OR_BOOTSTRAP" in gate


def test_bootstrap_does_not_authorize_execution_or_merge():
    for name in (PRIMARY, FALLBACK, PROVIDER):
        text = (WORKFLOW_DIR / name).read_text()
        assert "can_execute:true" not in text
        assert "terminal_authority=V17_TERMINAL_REDUCER" not in text or "V17_TERMINAL_REDUCER" in text
    gate = (WORKFLOW_DIR / "wow-v17-trusted-governance-gate.yml").read_text()
    assert "GOVERNANCE_WORKFLOW_IDENTITY_MISMATCH" in gate
