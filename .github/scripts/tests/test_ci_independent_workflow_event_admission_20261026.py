"""Protect genuine independent gate provenance and bounded event admission.

The check_run fast-filter is a cost/rate guard, not an approval. The workflow's
route step must still fetch the real check and verify exact App, PR and SHA.
These tests use workflow source as the executable contract; no GitHub secrets.
"""
from pathlib import Path

import pytest
import yaml

ROOT = Path(__file__).resolve().parents[1]
QA = ROOT / "workflows" / "wow-v17-independent-qa-check.yml"
CERT = ROOT / "workflows" / "wow-v17-existing-pr-governance-review.yml"
SOURCE = ROOT / "scripts" / "independent_source_review_receipt.py"


def workflow(path):
    data = yaml.safe_load(path.read_text(encoding="utf-8"))
    assert isinstance(data, dict)
    return data


def test_qa_run_display_name_is_exactly_pinned_identity():
    qa = workflow(QA)
    assert qa["name"] == "wow-v17-independent-qa-check"
    assert qa["run-name"] == "wow-v17-independent-qa-check"
    assert "\nWORKFLOW_NAME = \"wow-v17-independent-qa-check\"" in SOURCE.read_text(encoding="utf-8")


@pytest.mark.parametrize("event", ["workflow_dispatch", "workflow_run"])
def test_static_qa_run_name_is_event_independent(event):
    run_name = workflow(QA)["run-name"]
    assert event not in run_name
    assert "\u0024{{" not in run_name
    assert run_name == "wow-v17-independent-qa-check"


def test_review_certifier_filters_unrelated_check_run_before_runner():
    data = workflow(CERT)
    gate = data["jobs"]["certify-existing-pr"]["if"]
    for required in (
        "github.repository == 'gregoryharper84-ship-it/WOW-Dashboard'",
        "github.event_name != 'check_run'",
        "github.event.check_run.app.id == 5245853",
        "github.event.check_run.name == 'WOW Release Authority exact-head'",
        "github.event.check_run.status == 'completed'",
        "github.event.check_run.conclusion == 'success'",
    ):
        assert required in gate, f"missing admission guard {required}"
    assert "||" in gate  # existing PR/QA paths still reach the certifier


def test_late_release_reentry_retains_full_live_proof():
    source = CERT.read_text(encoding="utf-8")
    assert "check_run:" in source
    assert 'live_check=$(gh api "/repos/${GITHUB_REPOSITORY}/check-runs/${CHECK_RUN_ID}")' in source
    assert '.app.id == 5245853' in source
    assert "RELEASE_CHECK_PR_TARGET_NOT_UNIQUE" in source
    assert "RELEASE_CHECK_PR_MOVED_OR_NOT_CANONICAL" in source
    assert "cancel-in-progress: false" in source
    assert "SOURCE_QA_RUN_HAD_NO_EXACT_HEAD_TARGET" in source


def test_static_pinning_does_not_weaken_source_review_identity():
    source = SOURCE.read_text(encoding="utf-8")
    assert 'WORKFLOW_NAME = "wow-v17-independent-qa-check"' in source
    assert 'WORKFLOW_PATH = ".github/workflows/wow-v17-independent-qa-check.yml"' in source
    assert 'run.get("path") == WORKFLOW_PATH' in source
    assert "QA_POLICY_RUN_PROVENANCE_MISMATCH" in source
    assert 'source.get("run_id") == str(run_id)' in source
    assert 'source.get("workflow_name") == WORKFLOW_NAME' in source
