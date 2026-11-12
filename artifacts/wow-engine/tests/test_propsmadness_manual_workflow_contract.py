"""Offline guard: no scheduled PropsMadness collection in the research PR.

The opt-in public-page GitHub workflow was deliberately excluded until
provider source rights are independently established. If an authorized
workflow is introduced later, it must satisfy the same manual/no-write guards.
"""
from pathlib import Path

ROOT = Path(__file__).resolve().parents[3]
MANUAL_WORKFLOW = ROOT / ".github/workflows/wow-propsmadness-public-research-once.yml"
MULTISPORT = ROOT / "artifacts/wow-engine/v17/propsmadness_multisport_public_explore.py"


def test_research_branch_without_vendor_workflow_is_no_collection():
    # The approved early work is parser fixtures, not unattended web requests.
    if not MANUAL_WORKFLOW.exists():
        source = MULTISPORT.read_text(encoding="utf-8")
        assert "personal_noncommercial_use_confirmed" in source
        assert "historical_training_authorized" in source
        assert "can_execute" in source
        assert "403" in source and "429" in source
        return

    # If the independently reviewed workflow ever returns, fail closed on
    # unattended collection, credentials, writes or training.
    body = MANUAL_WORKFLOW.read_text(encoding="utf-8")
    assert "workflow_dispatch:" in body
    assert "confirm_personal_noncommercial_use" in body
    assert "if: inputs.confirm_personal_noncommercial_use == true" in body
    assert "permissions:\n  contents: read" in body
    assert "persist-credentials: false" in body
    assert "fetch_public_one_shot" in body
    assert "historical_training_authorized" in body
    assert "can_execute" in body
    for forbidden in ("  schedule:", "  push:", "  pull_request:", "contents: write",
                      "secrets.", "actions/upload-artifact@", "celery", "Supabase"):
        assert forbidden not in body
