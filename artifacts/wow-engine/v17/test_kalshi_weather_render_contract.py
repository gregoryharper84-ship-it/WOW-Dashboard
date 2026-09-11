from pathlib import Path

import yaml


def test_canonical_render_service_activates_v17_kalshi_weather_fail_closed_lane():
    repo_root = Path(__file__).resolve().parents[3]
    render = yaml.safe_load((repo_root / "render.yaml").read_text())
    services = {item["name"]: item for item in render["services"]}
    service = services["wow-governed-probability-engine"]
    assert service["rootDir"] == "artifacts/wow-engine"
    assert service["startCommand"] == "uvicorn api_ncaaf_acceptance:app --host 0.0.0.0 --port $PORT"
    env = {item["key"]: item for item in service["envVars"]}
    assert env["WOW_V17_ACTIVE"]["value"] == "1"
    assert env["WOW_KALSHI_WEATHER_V2_ACTIVE"]["value"] == "1"
    assert env["WOW_CAN_EXECUTE"]["value"] == "false"
    assert env["WOW_DRY_RUN_ONLY"]["value"] == "true"
