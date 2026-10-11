from pathlib import Path

import yaml


ROOT = Path(__file__).resolve().parents[2]


def test_render_autodeploy_containment_preserves_main_ci():
    workflow_text = (ROOT / ".github/workflows/wow-engine-verify.yml").read_text()
    assert "pull_request:\n    branches: [main]" in workflow_text
    assert "push:\n    branches: [main]" in workflow_text

    render = yaml.safe_load((ROOT / "render.yaml").read_text())
    services = {item["name"]: item for item in render["services"]}
    production = services["wow-governed-probability-engine"]

    assert production["autoDeployTrigger"] == "off"
    env = {item["key"]: item for item in production["envVars"]}
    assert env["WOW_CAN_EXECUTE"]["value"] == "false"
    assert env["WOW_DRY_RUN_ONLY"]["value"] == "true"
