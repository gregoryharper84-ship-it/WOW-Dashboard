"""Fail-closed release event recovery: no deployment permissions or bypass."""
from pathlib import Path

REPO = Path(__file__).resolve().parents[3]
WATCHDOG = REPO / ".github/workflows/wow-v17-main-verification-reconciler.yml"


def test_watchdog_only_dispatches_missing_main_verify():
    src = WATCHDOG.read_text(encoding="utf-8")
    assert 'cron: "19 * * * *"' in src
    assert "workflow_dispatch:" in src
    assert "actions: write" in src
    assert "deployments: write" not in src
    assert "RENDER_API_KEY" not in src
    assert "wow-verify.yml/dispatches" in src
    assert 'head_sha == $sha' in src
    assert '"MISSING"' in src
    assert 'if [ "$verdict" != MISSING ]; then' in src
    assert '[ "$main_sha" = "$current_sha" ]' in src


def test_watchdog_preserves_execution_and_release_boundaries():
    src = WATCHDOG.read_text(encoding="utf-8")
    assert 'WOW_CAN_EXECUTE: "false"' in src
    assert 'WOW_DRY_RUN_ONLY: "true"' in src
    assert "CHECKS_AND_INDEPENDENT_RELEASE_PROVENANCE_STILL_REQUIRED" in src
    assert "render.com" not in src.lower()
