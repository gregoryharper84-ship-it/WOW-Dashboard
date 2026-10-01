from __future__ import annotations

from pathlib import Path

import yaml

from v17.engineering_langgraph_runtime import langgraph_runtime_status


ROOT = Path(__file__).resolve().parents[2]
CELERY = ROOT / "artifacts/wow-engine/agent_runtime/celery_app.py"
RENDER = ROOT / "render.yaml"
REQUIREMENTS = ROOT / "artifacts/wow-engine/requirements.txt"


def _clear_runtime_env(monkeypatch):
    for key in (
        "WOW_ENGINEERING_LANGGRAPH_ENABLED",
        "WOW_ENGINEERING_CHECKPOINT_DB_URI",
        "WOW_CAN_EXECUTE",
        "WOW_DRY_RUN_ONLY",
    ):
        monkeypatch.delenv(key, raising=False)


def test_langgraph_worker_runtime_is_disabled_and_non_executable_by_default(monkeypatch):
    _clear_runtime_env(monkeypatch)
    status = langgraph_runtime_status()

    assert status["status"] == "DISABLED"
    assert status["terminal_status"] == "DEFERRED_WITH_JUSTIFICATION"
    assert status["blocker"] == "LANGGRAPH_ENGINEERING_RUNTIME_NOT_ENABLED"
    assert status["can_execute"] is False
    assert status["probability_authority"] == "NONE"
    assert status["terminal_authority"] == "V17_TERMINAL_REDUCER"
    assert status["checkpoint_uri_configured"] is False


def test_enabled_runtime_without_checkpoint_uri_fails_closed(monkeypatch):
    _clear_runtime_env(monkeypatch)
    monkeypatch.setenv("WOW_ENGINEERING_LANGGRAPH_ENABLED", "1")
    monkeypatch.setenv("WOW_CAN_EXECUTE", "false")
    monkeypatch.setenv("WOW_DRY_RUN_ONLY", "true")

    status = langgraph_runtime_status()

    assert status["status"] == "BLOCKED"
    assert status["terminal_status"] == "BLOCKED_WITH_EXACT_REASON"
    assert status["blocker"] == "WOW_ENGINEERING_CHECKPOINT_DB_URI_MISSING"
    assert status["langgraph_importable"] is True
    assert status["postgres_checkpointer_importable"] is True
    assert status["can_execute"] is False


def test_runtime_rejects_execution_authority_before_backend_checks(monkeypatch):
    _clear_runtime_env(monkeypatch)
    monkeypatch.setenv("WOW_ENGINEERING_LANGGRAPH_ENABLED", "1")
    monkeypatch.setenv("WOW_CAN_EXECUTE", "true")
    monkeypatch.setenv("WOW_DRY_RUN_ONLY", "true")
    monkeypatch.setenv("WOW_ENGINEERING_CHECKPOINT_DB_URI", "postgresql://must-not-be-used")

    status = langgraph_runtime_status()

    assert status["terminal_status"] == "BLOCKED_WITH_EXACT_REASON"
    assert status["blocker"] == "WOW_CAN_EXECUTE_MUST_REMAIN_FALSE"
    assert status["checkpoint_uri_configured"] is False


def test_runtime_rejects_live_mode_before_backend_checks(monkeypatch):
    _clear_runtime_env(monkeypatch)
    monkeypatch.setenv("WOW_ENGINEERING_LANGGRAPH_ENABLED", "1")
    monkeypatch.setenv("WOW_CAN_EXECUTE", "false")
    monkeypatch.setenv("WOW_DRY_RUN_ONLY", "false")

    status = langgraph_runtime_status()

    assert status["terminal_status"] == "BLOCKED_WITH_EXACT_REASON"
    assert status["blocker"] == "WOW_DRY_RUN_ONLY_MUST_REMAIN_TRUE"


def test_checkpoint_secret_is_never_returned(monkeypatch):
    _clear_runtime_env(monkeypatch)
    secret = "postgresql://user:very-secret-password@example.invalid/wow"
    monkeypatch.setenv("WOW_ENGINEERING_LANGGRAPH_ENABLED", "1")
    monkeypatch.setenv("WOW_CAN_EXECUTE", "false")
    monkeypatch.setenv("WOW_DRY_RUN_ONLY", "true")
    monkeypatch.setenv("WOW_ENGINEERING_CHECKPOINT_DB_URI", secret)

    status = langgraph_runtime_status()

    assert status["checkpoint_uri_configured"] is True
    assert secret not in repr(status)
    assert "very-secret-password" not in repr(status)
    assert status["can_execute"] is False


def test_resident_worker_installs_both_auditor_and_langgraph_readiness_hooks():
    text = CELERY.read_text()
    assert "install_celery_worker_hooks()" in text
    assert "install_engineering_langgraph_hooks()" in text
    assert "engineering_langgraph_runtime" in text


def test_render_worker_declares_fail_closed_langgraph_activation_contract():
    manifest = yaml.safe_load(RENDER.read_text())
    worker = next(service for service in manifest["services"] if service.get("name") == "wow-agent-worker")
    env = {item["key"]: item for item in worker["envVars"]}

    assert worker["autoDeployTrigger"] == "off"
    assert env["WOW_ENGINEERING_LANGGRAPH_ENABLED"]["value"] == "0"
    assert env["WOW_ENGINEERING_CHECKPOINT_DB_URI"]["sync"] is False
    assert env["LANGGRAPH_STRICT_MSGPACK"]["value"] == "true"
    assert env["WOW_CAN_EXECUTE"]["value"] == "false"
    assert env["WOW_DRY_RUN_ONLY"]["value"] == "true"


def test_worker_requirements_pin_langgraph_and_postgres_checkpoint_ranges():
    text = REQUIREMENTS.read_text()
    assert "langgraph>=1.2.12,<2.0.0" in text
    assert "langgraph-checkpoint-postgres>=3.1.2,<4.0.0" in text
    assert "opentelemetry-api>=1.45.0,<2.0.0" in text
