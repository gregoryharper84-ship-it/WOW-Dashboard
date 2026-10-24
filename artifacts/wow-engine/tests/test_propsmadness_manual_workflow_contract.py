"""Static regression for opt-in public PropsMadness research, no runtime scrape."""
from pathlib import Path


def test_manual_probe_is_not_scheduled_and_has_no_write_authority():
    workflow = (Path(__file__).resolve().parents[3]
                / ".github/workflows/wow-propsmadness-public-research-once.yml")
    body = workflow.read_text(encoding="utf-8")
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
