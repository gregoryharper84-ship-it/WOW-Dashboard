from pathlib import Path


ROOT = Path(__file__).resolve().parents[3]
WORKFLOW = ROOT / ".github" / "workflows" / "wow-v17-canonical-prop-action.yml"


def test_canonical_prop_action_is_manual_only_and_has_no_stale_fixture_default():
    text = WORKFLOW.read_text(encoding="utf-8")
    assert "if: github.event_name == 'workflow_dispatch' && github.ref == 'refs/heads/main'" in text
    assert "WOW_REQUEST_FILE: ${{ inputs.request_file }}" in text
    assert "2026-09-15-strikeouts-1ip.json" not in text
    assert "default: artifacts/wow-engine/v17/prop_action_requests/" not in text


def test_canonical_prop_action_fails_closed_on_stale_slate_by_default():
    text = WORKFLOW.read_text(encoding="utf-8")
    assert "allow_historical_request" in text
    assert "default: false" in text
    assert "STALE_CANONICAL_PROP_REQUEST_BLOCKED" in text
    assert "CURRENT_SLATE_CANARY" in text
    assert "HISTORICAL_REPRODUCTION" in text
    assert "WOW_SLATE_TIMEZONE: America/New_York" in text


def test_canonical_prop_action_verifies_new_p0_telemetry_regressions():
    text = WORKFLOW.read_text(encoding="utf-8")
    assert "test_v17_action_invocation_telemetry.py" in text
    assert "test_v17_interactive_latency_telemetry.py" in text
    assert "tests/test_v17_live_editor_compat.py" in text


def test_live_compact_canary_uses_per_workflow_run_request_identity():
    text = WORKFLOW.read_text(encoding="utf-8")
    assert "GITHUB_RUN_ID" in text
    assert "GITHUB_RUN_ATTEMPT" in text
    assert 'CANARY_RUN = f"{os.environ[\'GITHUB_RUN_ID\']}-{os.environ[\'GITHUB_RUN_ATTEMPT\']}"' in text
    assert '"request_id": f"v17-live-compact-mlb-four-direction-{CANARY_RUN}"' in text
    assert '"request_id": f"v17-live-compact-cross-sport-{CANARY_RUN}"' in text
    assert '"request_id": "v17-live-compact-mlb-four-direction"' not in text
    assert '"request_id": "v17-live-compact-cross-sport"' not in text
