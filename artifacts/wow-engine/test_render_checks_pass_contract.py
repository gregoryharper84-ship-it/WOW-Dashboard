from pathlib import Path

import yaml


ROOT = Path(__file__).resolve().parents[2]


def test_render_production_deploys_only_through_protected_release_controller():
    workflow_text = (ROOT / ".github/workflows/wow-engine-verify.yml").read_text()
    assert "pull_request:\n    branches: [main]" in workflow_text
    assert "push:\n    branches: [main]" in workflow_text

    render = yaml.safe_load((ROOT / "render.yaml").read_text())
    services = {item["name"]: item for item in render["services"]}
    production = services["wow-governed-probability-engine"]

    # Native Render auto-deploy stays off (matches live service settings); production
    # deploys only through the protected wow-release controller, so an unrelated merge
    # or a Blueprint sync cannot deploy production directly.
    assert production["autoDeployTrigger"] == "off"
    assert "autoDeploy" not in production or production["autoDeploy"] is False
    assert all(service.get("autoDeployTrigger") == "off" for service in render["services"] if "autoDeployTrigger" in service)
    controller = yaml.safe_load((ROOT / ".github/workflows/wow-v17-render-production-deploy.yml").read_text())
    assert any(job.get("environment") == "wow-release" for job in controller["jobs"].values())
    env = {item["key"]: item for item in production["envVars"]}
    assert env["WOW_CAN_EXECUTE"]["value"] == "false"
    assert env["WOW_DRY_RUN_ONLY"]["value"] == "true"
