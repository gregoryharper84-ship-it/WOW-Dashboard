"""Regression: trusted V17 governance queries exact artifacts without scanning all history.

This prevents a bounded 2,000-item repository-wide inventory from turning an
otherwise valid, immutable, exact-head release into a bootstrap deadlock.
"""
from pathlib import Path

WORKFLOW = Path(__file__).resolve().parents[2] / "workflows" / "wow-v17-existing-pr-governance-review.yml"


def _source():
    return WORKFLOW.read_text(encoding="utf-8")


def test_exact_head_artifact_inventory_is_name_filtered():
    text = _source()
    assert 'artifact_name="wow-v17-engineering-governance-${HEAD_SHA}"' in text
    assert 'actions/artifacts?name=${artifact_name}&per_page=100&page=1' in text
    assert "EXISTING_PR_RECEIPT_ARTIFACT_DUPLICATE" in text
    assert 'select(.name == $name and .expired == false)' in text
    assert 'actions/artifacts?per_page=100&page=${page}' not in text


def test_source_review_receipt_uniqueness_uses_exact_name_and_authentication():
    text = _source()
    assert 'name=${artifact_name}&per_page=100&page=1' in text
    assert 'all(.artifacts[]; .name == $name)' in text
    assert "SOURCE_REVIEW_ARTIFACT_INVENTORY_NOT_UNIQUE" in text
    assert "verify_unique_artifact_inventory(" in text
    assert "verify_trusted" not in text or "SOURCE_REVIEW_WORKFLOW_RUN_UNTRUSTED_OR_NOT_SUCCESSFUL" in text
    assert "SOURCE_REVIEW_ARTIFACT_NAME_INVALID" in text
