from pathlib import Path

import yaml


ROOT = Path(__file__).resolve().parents[2]
EXISTING = ROOT / ".github/workflows/wow-v17-existing-pr-governance-review.yml"
TRUSTED = ROOT / ".github/workflows/wow-v17-trusted-governance-gate.yml"
RELIABILITY = ROOT / ".agents/skills/wow-engineering-reliability/SKILL.md"


def test_existing_pr_governance_overlaps_review_with_ci():
    text = EXISTING.read_text()
    lead = text.index("- name: Engineering Lead certification agent")
    architect = text.index("- name: System Architect agent")
    ci = text.index("- name: Verify exact-head deterministic CI")
    qa = text.index("- name: QA Verification agent")
    assert lead < architect < ci < qa
    assert "ci.json,lead.json" not in text
    assert "ci.json,triage.json" not in text
    assert "Exact-head deterministic CI has already been independently" in text


def test_trust_root_diff_is_data_not_executed_and_requires_architect_pass():
    text = EXISTING.read_text()
    assert "trust_root_changed=false" in text
    assert "trust_root_changed=true" in text
    assert "never execute PR-controlled workflow/action code" in text
    assert "NOT_APPLICABLE is forbidden for trust-root changes" in text
    assert 'steps.target.outputs.trust_root_changed != \'true\'' in text
    assert "EXISTING_PR_TRUST_ROOT_CHANGE_REQUIRES_WORKER_OR_BOOTSTRAP" not in text
    assert "trust_root_changed: ($trust_root_changed == \"true\")" in text


def test_trusted_governance_fails_fast_when_producer_missing():
    text = TRUSTED.read_text()
    assert "GOVERNANCE_PRODUCER_NOT_STARTED" in text
    assert "GOVERNANCE_PRODUCER_FAILED" in text
    assert "GOVERNANCE_PRODUCER_SUCCEEDED_WITHOUT_ARTIFACT" in text
    assert "sleep 5" in text
    assert "sleep 10" not in text
    # 180 * 5 seconds = 15 minutes maximum artifact wait instead of 30.
    assert "for attempt in $(seq 1 180)" in text
    assert "if [ $((attempt % 12)) -eq 0 ]" in text


def test_reliability_contract_freezes_candidate_before_pr():
    text = RELIABILITY.read_text()
    assert "freeze candidate head -> open PR -> protected exact-head matrix" in text
    assert "only defect-driven commits are allowed" in text


def test_governance_workflows_still_parse_as_yaml():
    assert yaml.safe_load(EXISTING.read_text())["name"] == "wow-v17-existing-pr-governance-review"
    assert yaml.safe_load(TRUSTED.read_text())["name"] == "wow-v17-trusted-governance-gate"
