"""Source identity regression for QA policy receipt verification (issue #1930).

Protects equality of GitHub Actions run.name and the strict, immutable
WORKFLOW_NAME identifier in independent_source_review_receipt.py.

This is a source-level contract test. Production acceptance still requires
new, authenticated QA and Release App receipts from the protected workflow.
"""
from pathlib import Path
import re


ROOT = Path(__file__).resolve().parents[3]
WORKFLOW = ROOT / ".github/workflows/wow-v17-independent-qa-check.yml"
VERIFIER = ROOT / ".github/scripts/independent_source_review_receipt.py"


def test_qa_workflow_uses_static_run_name_matching_verifier():
    yaml = WORKFLOW.read_text(encoding="utf-8")
    source = VERIFIER.read_text(encoding="utf-8")
    assert re.search(r"(?m)^name:\s*wow-v17-independent-qa-check\s*$", yaml)
    assert re.search(r"(?m)^run-name:\s*wow-v17-independent-qa-check\s*$", yaml)
    assert "WORKFLOW_NAME = \"wow-v17-independent-qa-check\"" in source
    assert 'source.get("workflow_name") == run.get("name") == WORKFLOW_NAME' in source
    assert 'run.get("path") == WORKFLOW_PATH' in source


def test_dynamic_run_name_cannot_reintroduce_release_provenance_failure():
    yaml = WORKFLOW.read_text(encoding="utf-8")
    run_names = re.findall(r"(?m)^run-name:\s*([^\n]+)$", yaml)
    assert run_names == ["wow-v17-independent-qa-check"]
    assert "${{" not in run_names[0]
    # This deliberately does NOT relax the verifier into accepting prefixes.
    # New workflow executions after deployment must carry the canonical name.
