from pathlib import Path

import yaml


ROOT = Path(__file__).resolve().parents[2]
WORKFLOW = ROOT / ".github/workflows/wow-v17-p0-rapid-dispatch.yml"


def test_p0_parallel_rapid_dispatch_contract() -> None:
    text = WORKFLOW.read_text()
    assert 'cron: "*/15 * * * *"' in text
    assert "p0_parallel_dispatch.py" in text
    assert "max_parallel_writers: 3" in text
    assert "implementation_lease_scope: domain-scoped" in text
    assert "for status in queued in_progress waiting requested pending" in text
    assert '--status "$status"' in text
    assert "--limit 1000" in text
    assert "ACTIVE_WRITER_INVENTORY_TRUNCATED" in text
    assert '--active-runs "$RUNNER_TEMP/wow-active-writer-runs.json"' in text
    assert '--run-number "$GITHUB_RUN_NUMBER"' in text
    assert "stream_A: Scout/Data Plane (#1237 -> #1250)" in text
    assert "stream_B: State/Scoring (#1189 -> #960)" in text
    assert "stream_C: Runtime/Ingest (#502 -> #1127)" in text
    assert "status,displayTitle" in text
    assert 'contains(\\\"lease=${lease_group}\\\")' in text
    assert "wow-v17-engineering-provider-dispatcher.yml" in text
    assert "wow-v17-gemini-engineering-worker.yml" in text
    assert "-f force_provider=auto" in text
    assert '-f target_incident="$incident"' in text
    assert '-f lease_group="$lease_group"' in text
    assert 'reason="P0_PARALLEL_RAPID:${stream}:${incident}"' in text
    assert "can_execute: false" in text
    assert "terminal_authority: V17_TERMINAL_REDUCER" in text


def test_p0_parallel_rapid_dispatch_yaml_parses() -> None:
    data = yaml.safe_load(WORKFLOW.read_text())
    assert data["name"] == "wow-v17-p0-rapid-dispatch"
    assert data["permissions"]["actions"] == "write"
    assert data["permissions"]["contents"] == "read"
    assert data["permissions"]["issues"] == "read"


def test_p0_rapid_dispatch_reuses_single_provider_authority() -> None:
    text = WORKFLOW.read_text()
    dispatcher = (
        ROOT / ".github/workflows/wow-v17-engineering-provider-dispatcher.yml"
    ).read_text()
    # The planner cannot infer *all* providers are down from the most recent
    # OpenAI/Claude worker runs: that used to stop Gemini/Cursor arbitration
    # before the authenticated control plane could even inspect its canary.
    assert "Preflight P0 engineering provider availability" not in text
    assert "steps.provider_state.outputs.admit" not in text
    assert "PRIMARY_ACCOUNT_BLOCKED_SECONDARY_DELIVERY_FAILED" not in text
    assert "PROVIDER_ADMISSION_CLEAR" not in text
    assert "WOW_COPILOT_FALLBACK_BUDGET_APPROVED" not in text
    assert "Submit idle sport leases to existing provider authority" in text
    assert "gh workflow run wow-v17-engineering-provider-dispatcher.yml" in text
    assert text.count("gh workflow run ") == 1
    assert "DELEGATED_TO_EXISTING_PROVIDER_DISPATCHER" in text
    assert "provider_execution_verified: false" in text
    assert "dispatched_count_is_only_a_submission_receipt: true" in text
    # This change does not delegate approval to the planner. Provider checks,
    # remote writer fencing and consent remain inside the one control plane.
    assert "gemini_canary_healthy()" in dispatcher
    assert "engineering_remote_fallback.py guard" in dispatcher
    assert "steps.remote_fence.outcome == 'success'" in dispatcher
    assert "CIRCUIT_BREAKER" in dispatcher
    assert "WOW_COPILOT_FALLBACK_ENABLED" in dispatcher


def test_rapid_dispatch_keeps_bounded_nonduplicated_lease_intake() -> None:
    workflow = yaml.safe_load(WORKFLOW.read_text())
    steps = workflow["jobs"]["dispatch"]["steps"]
    submit = next(step for step in steps if step["name"].startswith("Submit idle"))
    assert "if" not in submit
    script = submit["run"]
    assert 'if [ "$active_count" -gt 0 ]; then' in script
    assert '"$GITHUB_REPOSITORY"' in script
    assert 'gh workflow run wow-v17-engineering-provider-dispatcher.yml' in script
    assert '--ref main' in script
    assert "-f force_provider=auto" in script
    assert '-f target_incident="$incident"' in script
    assert '-f lease_group="$lease_group"' in script
    assert "dispatched_count=" in script
    assert "busy_count=" in script
    # A single request per available canonical lease; no direct model calls,
    # alternative credential route, or GitHub bypass identity.
    assert "gemini " not in script
    assert "claude " not in script
    assert "curl " not in script
    assert "git push" not in script


def test_rapid_dispatch_preserves_source_and_terminal_safety() -> None:
    text = WORKFLOW.read_text()
    data = yaml.safe_load(text)
    assert data["concurrency"]["group"] == "wow-v17-p0-rapid-dispatch"
    assert data["permissions"]["contents"] == "read"
    assert data["permissions"]["actions"] == "write"
    assert "can_execute: false" in text
    assert "terminal_authority: V17_TERMINAL_REDUCER" in text
    assert "max_parallel_writers: 3" in text
    assert "steps.provider_state." not in text
