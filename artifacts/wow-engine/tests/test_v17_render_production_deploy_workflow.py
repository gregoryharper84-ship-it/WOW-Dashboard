from pathlib import Path
DEPLOY_WORKFLOW = Path(__file__).resolve().parents[3] / ".github/workflows/wow-v17-render-production-deploy.yml"

def test_legacy_deployment_is_read_only_and_manual():
    import yaml
    workflow = yaml.safe_load(DEPLOY_WORKFLOW.read_text())
    assert (workflow.get("on") or workflow.get(True)) == {"workflow_dispatch": None}
    assert workflow["permissions"] == {"contents": "read"}
    assert set(workflow["jobs"]) == {"legacy-deployment-disabled"}
    job = workflow["jobs"]["legacy-deployment-disabled"]
    assert job["permissions"] == {"contents": "read"}
    assert "env" not in workflow and "env" not in job


def test_legacy_dispatch_fails_without_side_effects():
    import subprocess
    import yaml
    workflow = yaml.safe_load(DEPLOY_WORKFLOW.read_text())
    steps = workflow["jobs"]["legacy-deployment-disabled"]["steps"]
    assert len(steps) == 1
    assert set(steps[0]) == {"name", "run"}
    # Exact allowlist keeps the executable probe free of deployment side effects.
    assert steps[0]["run"].strip() == 'echo "LEGACY_DEPLOYMENT_DISABLED"\nexit 1'
    result = subprocess.run(["bash", "-c", steps[0]["run"]], capture_output=True, text=True, timeout=5)
    assert result.returncode == 1
    assert result.stdout.strip() == "LEGACY_DEPLOYMENT_DISABLED"


def test_legacy_deployment_cannot_provide_credentials_or_live_receipts():
    text = DEPLOY_WORKFLOW.read_text()
    for forbidden in ("secrets.", "RENDER_API_KEY", "deployments: write", "uses:",
                      "render_deployment_pointer.py", "emit_receipt",
                      "EXACT_SHA_ALREADY_LIVE", "EXACT_SHA_RENDER_DEPLOY_LIVE",
                      "EXACT_DEPLOY_ACCEPTED_FOR_CANARIES"):
        assert forbidden not in text


def test_daily_snapshot_has_deploy_handoff_runway_and_network_retries():
    repo_root = Path(__file__).resolve().parents[3]
    text = (repo_root / ".github" / "workflows" / "wow-v17-daily-snapshot.yml").read_text(
        encoding="utf-8"
    )

    assert "timeout-minutes: 40" in text
    assert "workflow_call:" in text
    assert "post_deploy_acceptance:" in text
    assert "\n  push:\n" not in text
    assert "if: inputs.post_deploy_acceptance == true" in text
    assert "max_attempts = 90" in text
    assert "retry_delay_seconds = 20" in text
    assert "except (urllib.error.URLError, TimeoutError):" in text
    assert 'WOW_CAN_EXECUTE: "false"' in text
    assert 'WOW_DRY_RUN_ONLY: "true"' in text
