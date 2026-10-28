"""Temporary Cursor quarantine must NOT weaken legacy writer fencing."""
from pathlib import Path

ROOT = Path(__file__).resolve().parents[2]
WORKFLOW = ROOT / ".github/workflows/wow-v17-engineering-provider-dispatcher.yml"
REMOTE = ROOT / "artifacts/wow-engine/v17/engineering_remote_fallback.py"


def test_manual_cursor_is_quarantined_with_typed_survival():
    text = WORKFLOW.read_text()
    a = text.index('elif [ "$force" = "cursor" ]; then')
    b = text.index('elif [ "$force" = "gemini" ]; then', a)
    section = text[a:b]
    assert "action=SURVIVAL" in section
    assert "provider=deterministic" in section
    assert "reason=CURSOR_PROVIDER_QUARANTINED" in section
    assert "independent_fallback_allowed=false" in section
    assert "action=DISPATCH" not in section
    assert "provider=cursor" not in section


def test_new_cursor_launch_and_cursor_api_credentials_not_exposed():
    text = WORKFLOW.read_text()
    assert "cursor_dispatch: ${{ false }}" in text
    for forbidden in ("cursor_cloud_handoff.py", "secrets.CURSOR_API_KEY",
                      "Dispatch scoped Cursor Cloud Agent",
                      "CURSOR_HANDOFF_ENABLED"):
        assert forbidden not in text
    assert "Reconcile or admit separately authorized Copilot fallback" in text


def test_existing_global_remote_hold_still_blocks_other_writer_admission():
    text = WORKFLOW.read_text()
    assert "python artifacts/wow-engine/v17/engineering_remote_fallback.py guard" in text
    for provider in ("openai", "anthropic", "gemini"):
        assert (
            "steps.route.outputs.provider == '" + provider +
            "' && steps.remote_fence.outcome == 'success'"
        ) in text
    remote = REMOTE.read_text()
    assert 'raise ProviderUnavailable("REMOTE_WRITER_RECONCILIATION_REQUIRED")' in remote
    assert "verified_released_ids" in remote


def test_quarantine_does_not_enable_execution_or_order_placement():
    text = WORKFLOW.read_text()
    assert "can_execute: false" in text
    assert "V17_TERMINAL_REDUCER" in text
    assert "CURSOR_PROVIDER_QUARANTINED" in text
    assert "Cursor: temporarily quarantined" in text
    for provider in ("openai", "anthropic", "gemini"):
        assert "provider=" + provider in text
