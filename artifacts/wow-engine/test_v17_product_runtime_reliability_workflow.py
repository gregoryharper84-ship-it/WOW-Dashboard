from pathlib import Path

import yaml


ROOT = Path(__file__).resolve().parents[2]
WORKFLOW = ROOT / ".github" / "workflows" / "wow-v17-product-runtime-reliability.yml"


def _text() -> str:
    return WORKFLOW.read_text(encoding="utf-8")


def _yaml():
    return yaml.load(_text(), Loader=yaml.BaseLoader)


def test_product_runtime_watch_runs_every_fifteen_minutes_and_after_deploy():
    data = _yaml()
    assert data["on"]["schedule"] == [{"cron": "*/15 * * * *"}]
    assert data["on"]["workflow_run"]["workflows"] == ["wow-v17-render-production-deploy"]
    assert "workflow_dispatch" in data["on"]


def test_product_runtime_watch_can_route_incidents_but_not_change_code():
    data = _yaml()
    assert data["permissions"] == {"contents": "read", "issues": "write"}
    assert 'WOW_CAN_EXECUTE: "false"' in _text()
    assert 'WOW_DRY_RUN_ONLY: "true"' in _text()
    assert "V17_TERMINAL_REDUCER" in _text()


def test_product_runtime_watch_covers_all_user_facing_surfaces():
    text = _text()
    for surface in (
        "WOW_BETTING_ENGINE",
        "LLP_TEAM_BETTING_ENGINE",
        "KALSHI_WEATHER_MARKET_EXPERT",
    ):
        assert surface in text
    assert "product_runtime_reliability.py" in text
    assert "WOW_PRODUCT_RUNTIME_RELIABILITY" in text


def test_product_runtime_watch_uses_safe_non_probability_probes():
    module = (ROOT / "artifacts" / "wow-engine" / "v17" / "product_runtime_reliability.py").read_text(encoding="utf-8")
    assert 'payload={"lane": "SYNTHETIC_RELIABILITY_PROBE", "ticker": "SYNTHETIC_ONLY"}' in module
    assert 'payload={}' in module
    assert "WEATHER_LANE_RUNTIME_NOT_CERTIFIED" in module
    assert '"probability_publishable") is not False' in module
