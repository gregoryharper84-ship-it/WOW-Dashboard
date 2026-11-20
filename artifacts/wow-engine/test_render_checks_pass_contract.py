from pathlib import Path

import yaml


ROOT = Path(__file__).resolve().parents[2]


def test_render_single_writer_matches_required_checks_deploy_contract():
    workflow_text = (ROOT / ".github/workflows/wow-engine-verify.yml").read_text()
    assert "pull_request:\n    branches: [main]" in workflow_text
    assert "push:\n    branches: [main]" in workflow_text

    render = yaml.safe_load((ROOT / "render.yaml").read_text())
    services = {item["name"]: item for item in render["services"]}
    production = services["wow-governed-probability-engine"]
    worker = services["wow-agent-worker"]

    assert production["autoDeployTrigger"] == "off"
    assert worker["autoDeployTrigger"] == "off"
    env = {item["key"]: item for item in production["envVars"]}
    assert env["WOW_CAN_EXECUTE"]["value"] == "false"
    assert env["WOW_DRY_RUN_ONLY"]["value"] == "true"

    deploy = (ROOT / ".github/workflows/wow-v17-required-checks-deploy.yml").read_text()
    assert "Sole authorized production deploy writer" in deploy
    assert "python -m v17.required_checks_deploy" in deploy
