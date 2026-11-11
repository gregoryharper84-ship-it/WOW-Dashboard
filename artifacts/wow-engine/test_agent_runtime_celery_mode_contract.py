"""Integration opt-in must publish to the broker; ordinary tests stay isolated."""
import pytest

import importlib.util
from pathlib import Path

spec = importlib.util.spec_from_file_location("engine_test_config", Path(__file__).with_name("conftest.py"))
conftest = importlib.util.module_from_spec(spec)
spec.loader.exec_module(conftest)
from agent_runtime.queue import celery_app


@pytest.mark.parametrize("integration,expected_eager", [(None, True), ("1", False)])
def test_celery_fixture_keeps_real_integration_out_of_eager_mode(monkeypatch, integration, expected_eager):
    if integration is None:
        monkeypatch.delenv("WOW_AGENT_RUNTIME_INTEGRATION", raising=False)
    else:
        monkeypatch.setenv("WOW_AGENT_RUNTIME_INTEGRATION", integration)
    monkeypatch.setitem(celery_app.conf, "task_always_eager", True)
    monkeypatch.setitem(celery_app.conf, "task_eager_propagates", False)
    fixture = conftest._agent_runtime_celery_eager.__wrapped__()
    next(fixture)
    try:
        assert celery_app.conf.task_always_eager is expected_eager
        assert celery_app.conf.task_eager_propagates is expected_eager
    finally:
        with pytest.raises(StopIteration):
            next(fixture)
    assert celery_app.conf.task_always_eager is True
    assert celery_app.conf.task_eager_propagates is False
