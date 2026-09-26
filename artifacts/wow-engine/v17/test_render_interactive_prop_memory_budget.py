from pathlib import Path

import yaml


def _service() -> dict:
    repo_root = Path(__file__).resolve().parents[3]
    render = yaml.safe_load((repo_root / "render.yaml").read_text(encoding="utf-8"))
    services = {item["name"]: item for item in render["services"] if item.get("type") == "web"}
    return services["wow-governed-probability-engine"]


def _env(service: dict) -> dict[str, object]:
    return {item["key"]: item.get("value") for item in service["envVars"]}


def test_free_production_web_service_serializes_interactive_prop_work() -> None:
    service = _service()
    env = _env(service)

    assert service["plan"] == "free"
    assert env["WOW_INTERACTIVE_PROP_HYDRATION_WORKERS"] == "1"
    assert env["WOW_INTERACTIVE_PROP_RESEARCH_WORKERS"] == "1"
    assert env["WOW_INTERACTIVE_PROP_SCORE_WORKERS"] == "1"


def test_memory_budget_does_not_weaken_v17_execution_guards() -> None:
    env = _env(_service())

    assert env["WOW_V17_ACTIVE"] == "1"
    assert env["WOW_CAN_EXECUTE"] == "false"
    assert env["WOW_DRY_RUN_ONLY"] == "true"


def test_serial_budget_uses_existing_runtime_controls_only() -> None:
    engine_root = Path(__file__).resolve().parents[1]
    hydration = (engine_root / "v17" / "interactive_pick_hydration.py").read_text(encoding="utf-8")
    scoring = (engine_root / "v17" / "interactive_pick_parallel.py").read_text(encoding="utf-8")

    assert 'WOW_INTERACTIVE_PROP_HYDRATION_WORKERS' in hydration
    assert 'WOW_INTERACTIVE_PROP_RESEARCH_WORKERS' in hydration
    assert 'WOW_INTERACTIVE_PROP_SCORE_WORKERS' in scoring
    assert 'return max(1' in hydration
    assert 'return max(1' in scoring
