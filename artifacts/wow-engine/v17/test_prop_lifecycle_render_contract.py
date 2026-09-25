from pathlib import Path

import yaml


def test_render_keeps_heavy_prop_evidence_sweep_off_interactive_web_process():
    repo_root = Path(__file__).resolve().parents[3]
    render = yaml.safe_load((repo_root / "render.yaml").read_text())
    services = {item["name"]: item for item in render["services"]}
    service = services["wow-governed-probability-engine"]
    env = {item["key"]: item for item in service["envVars"]}

    # The autonomous producer is forward-cohort evidence only and has repeatedly
    # consumed enough time/resources to destabilize the 512-MB Action web host.
    # Canonical user-board scoring remains installed and hydrates its own rows.
    assert env["WOW_PROP_EVIDENCE_ACQUISITION_ENABLED"]["value"] == "0"
    assert int(env["WOW_PROP_EVIDENCE_ACQUISITION_INTERVAL_SECONDS"]["value"]) >= 300
    assert int(env["WOW_PROP_EVIDENCE_ACQUISITION_FORWARD_DAYS"]["value"]) >= 2
    assert env["WOW_PROP_LIFECYCLE_AUTOPILOT_ENABLED"]["value"] == "0"
    assert int(env["WOW_PROP_LIFECYCLE_AUTOPILOT_INTERVAL_SECONDS"]["value"]) >= 300
    assert int(env["WOW_PROP_LIFECYCLE_AUTOPILOT_MAX_SNAPSHOTS_PER_ROUTE"]["value"]) >= 1
    assert int(env["WOW_PROP_LIFECYCLE_AUTOPILOT_SETTLEMENT_LIMIT"]["value"]) >= 1
    assert env["WOW_CAN_EXECUTE"]["value"] == "false"
    assert env["WOW_DRY_RUN_ONLY"]["value"] == "true"


def test_approval_gated_worker_remains_non_executing_outside_active_blueprint():
    repo_root = Path(__file__).resolve().parents[3]
    active = yaml.safe_load((repo_root / "render.yaml").read_text())
    active_services = {item["name"]: item for item in active["services"]}
    assert "wow-agent-worker" not in active_services
    assert "wow-jobs" not in active_services

    phase2 = yaml.safe_load((repo_root / "render.agent-runtime-phase2.example.yaml").read_text())
    services = {item["name"]: item for item in phase2["services"]}
    worker = services["wow-agent-worker"]
    env = {item["key"]: item for item in worker["envVars"]}
    assert worker["autoDeployTrigger"] == "off"
    assert env["WOW_CAN_EXECUTE"]["value"] == "false"
    assert env["WOW_DRY_RUN_ONLY"]["value"] == "true"


def test_external_lifecycle_wakeup_is_scheduled_and_uses_short_lived_oidc_only():
    repo_root = Path(__file__).resolve().parents[3]
    path = repo_root / ".github" / "workflows" / "wow-v17-prop-lifecycle-autopilot.yml"
    text = path.read_text(encoding="utf-8")

    assert 'cron: "*/15 * * * *"' in text
    assert "id-token: write" in text
    assert "ACTIONS_ID_TOKEN_REQUEST_URL" in text
    assert "ACTIONS_ID_TOKEN_REQUEST_TOKEN" in text
    assert "audience=${WOW_OIDC_AUDIENCE}" in text
    assert '"max_snapshots_per_route":2' in text
    assert '"settlement_limit":20' in text
    assert "not retrying a possibly still-running POST" in text
    assert "for attempt in 1 2" not in text
    assert "--retry-all-errors" not in text
    assert "/v17/prop-lifecycle-autopilot-run" in text
    assert 'WOW_CAN_EXECUTE: "false"' in text
    assert 'WOW_DRY_RUN_ONLY: "true"' in text
    assert "secrets.WOW_ACTION_API_KEY" not in text
    assert "pull_request:" not in text


def test_external_oidc_workflow_is_the_only_lifecycle_scheduler():
    repo_root = Path(__file__).resolve().parents[3]
    render = yaml.safe_load((repo_root / "render.yaml").read_text())
    service = next(item for item in render["services"] if item["name"] == "wow-governed-probability-engine")
    env = {item["key"]: item for item in service["envVars"]}

    assert env["WOW_PROP_LIFECYCLE_AUTOPILOT_ENABLED"]["value"] == "0"
    workflow = (repo_root / ".github" / "workflows" / "wow-v17-prop-lifecycle-autopilot.yml").read_text()
    assert "/v17/prop-lifecycle-autopilot-run" in workflow
