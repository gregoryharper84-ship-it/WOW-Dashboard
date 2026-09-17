from pathlib import Path

import yaml


def test_render_enables_continuous_prop_evidence_and_lifecycle_without_execution():
    repo_root = Path(__file__).resolve().parents[3]
    render = yaml.safe_load((repo_root / "render.yaml").read_text())
    services = {item["name"]: item for item in render["services"]}
    service = services["wow-governed-probability-engine"]
    env = {item["key"]: item for item in service["envVars"]}

    assert env["WOW_PROP_EVIDENCE_ACQUISITION_ENABLED"]["value"] == "1"
    assert int(env["WOW_PROP_EVIDENCE_ACQUISITION_INTERVAL_SECONDS"]["value"]) >= 300
    assert int(env["WOW_PROP_EVIDENCE_ACQUISITION_FORWARD_DAYS"]["value"]) >= 2
    assert env["WOW_PROP_LIFECYCLE_AUTOPILOT_ENABLED"]["value"] == "1"
    assert int(env["WOW_PROP_LIFECYCLE_AUTOPILOT_INTERVAL_SECONDS"]["value"]) >= 300
    assert int(env["WOW_PROP_LIFECYCLE_AUTOPILOT_MAX_SNAPSHOTS_PER_ROUTE"]["value"]) >= 1
    assert int(env["WOW_PROP_LIFECYCLE_AUTOPILOT_SETTLEMENT_LIMIT"]["value"]) >= 1
    assert env["WOW_CAN_EXECUTE"]["value"] == "false"
    assert env["WOW_DRY_RUN_ONLY"]["value"] == "true"


def test_worker_remains_non_executing_even_though_web_autopilot_is_enabled():
    repo_root = Path(__file__).resolve().parents[3]
    render = yaml.safe_load((repo_root / "render.yaml").read_text())
    services = {item["name"]: item for item in render["services"]}
    worker = services["wow-agent-worker"]
    env = {item["key"]: item for item in worker["envVars"]}
    assert env["WOW_CAN_EXECUTE"]["value"] == "false"
    assert env["WOW_DRY_RUN_ONLY"]["value"] == "true"
