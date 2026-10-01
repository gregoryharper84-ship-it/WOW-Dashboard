from __future__ import annotations

import importlib
import sys
from pathlib import Path
from types import SimpleNamespace


def _reload_module():
    import v17.posthog_observability as module

    return importlib.reload(module)


def test_posthog_observability_is_inert_without_project_key(monkeypatch):
    monkeypatch.delenv("POSTHOG_PROJECT_API_KEY", raising=False)
    module = _reload_module()

    assert module.initialize_posthog_observability() == {
        "status": "DISABLED_NOT_CONFIGURED",
        "provider": "POSTHOG",
        "can_execute": False,
    }


def test_posthog_observability_enables_safe_exception_autocapture(monkeypatch):
    class FakePosthog:
        instances = []

        def __init__(self, **kwargs):
            self.kwargs = kwargs
            self.captures = []
            type(self).instances.append(self)

        def capture(self, event, **kwargs):
            self.captures.append((event, kwargs))
            return "event-id"

    monkeypatch.setenv("POSTHOG_PROJECT_API_KEY", "test-project-token")
    monkeypatch.setenv("POSTHOG_HOST", "https://us.i.posthog.com")
    monkeypatch.setenv("WOW_ENVIRONMENT", "test")
    monkeypatch.setenv("WOW_RELEASE_SHA", "deadbeef")
    monkeypatch.setitem(sys.modules, "posthog", SimpleNamespace(Posthog=FakePosthog))
    module = _reload_module()

    status = module.initialize_posthog_observability()

    assert status == {
        "status": "ENABLED",
        "provider": "POSTHOG",
        "host": "https://us.i.posthog.com",
        "exception_autocapture": True,
        "capture_exception_code_variables": False,
        "can_execute": False,
    }
    client = FakePosthog.instances[-1]
    assert client.kwargs["project_api_key"] == "test-project-token"
    assert client.kwargs["enable_exception_autocapture"] is True
    assert client.kwargs["capture_exception_code_variables"] is False
    assert client.captures == [
        (
            "wow observability started",
            {
                "distinct_id": "wow-governed-probability-engine",
                "properties": {
                    "runtime_generation": "V17_ACTIVE",
                    "terminal_authority": "V17_TERMINAL_REDUCER",
                    "can_execute": False,
                    "environment": "test",
                    "release": "deadbeef",
                },
            },
        )
    ]


def test_posthog_incident_capture_uses_allowlisted_failure_fingerprint(monkeypatch):
    class FakePosthog:
        instances = []

        def __init__(self, **kwargs):
            self.captures = []
            type(self).instances.append(self)

        def capture(self, event, **kwargs):
            self.captures.append((event, kwargs))
            return "event-id"

    monkeypatch.setenv("POSTHOG_PROJECT_API_KEY", "test-project-token")
    monkeypatch.setenv("WOW_ENVIRONMENT", "test")
    monkeypatch.setenv("WOW_RELEASE_SHA", "deadbeef")
    monkeypatch.setitem(sys.modules, "posthog", SimpleNamespace(Posthog=FakePosthog))
    module = _reload_module()
    module.initialize_posthog_observability()

    result = module.capture_wow_engineering_incident(
        route="/score-team-event",
        sport="NFL",
        market_family="OUTRIGHT_WINNER",
        scorer_stage="MODEL_INVOKE",
        terminal_code="NFL_FITTED_SCORER_FAILED",
        exception_type="RemoteProtocolError",
        exception_message="authorization=Bearer super-secret-value",
        specialist="wow.nfl-game-win-probability-expert",
        model_version="NFL_EVENT_PREGAME_PRIOR_V1",
        artifact_id="artifact-123",
        run_id="run-123",
        deploy_id="dep-123",
        source_provider="NFLVERSE",
    )

    assert result["status"] == "CAPTURED"
    assert result["can_execute"] is False
    client = FakePosthog.instances[-1]
    event, payload = client.captures[-1]
    assert event == "wow engineering incident"
    properties = payload["properties"]
    assert properties["wow_failure_fingerprint"] == result["fingerprint"]
    assert properties["sport"] == "NFL"
    assert properties["scorer_stage"] == "MODEL_INVOKE"
    assert properties["terminal_code"] == "NFL_FITTED_SCORER_FAILED"
    assert properties["terminal_authority"] == "V17_TERMINAL_REDUCER"
    assert properties["can_execute"] is False
    assert "exception_message" not in properties
    assert "super-secret-value" not in repr(properties)


def test_posthog_incident_capture_is_inert_when_telemetry_disabled(monkeypatch):
    monkeypatch.delenv("POSTHOG_PROJECT_API_KEY", raising=False)
    module = _reload_module()

    result = module.capture_wow_engineering_incident(
        route="/score-team-event",
        sport="WNBA",
        market_family="SPREAD",
        scorer_stage="DISCOVERY",
        terminal_code="WNBA_SPREAD_CANARY_IDENTITY_SOURCE_UNAVAILABLE",
    )

    assert result["status"] == "NOT_CAPTURED_TELEMETRY_DISABLED"
    assert result["fingerprint"]
    assert result["can_execute"] is False


def test_posthog_observability_initializes_once_per_process(monkeypatch):
    class FakePosthog:
        instances = []

        def __init__(self, **kwargs):
            self.captures = []
            type(self).instances.append(self)

        def capture(self, event, **kwargs):
            self.captures.append((event, kwargs))

    monkeypatch.setenv("POSTHOG_PROJECT_API_KEY", "test-project-token")
    monkeypatch.setitem(sys.modules, "posthog", SimpleNamespace(Posthog=FakePosthog))
    module = _reload_module()

    first = module.initialize_posthog_observability()
    second = module.initialize_posthog_observability()

    assert first == second
    assert len(FakePosthog.instances) == 1
    assert len(FakePosthog.instances[0].captures) == 1


def test_posthog_observability_fails_open(monkeypatch):
    class BrokenPosthog:
        def __init__(self, **kwargs):
            raise RuntimeError("telemetry unavailable")

    monkeypatch.setenv("POSTHOG_PROJECT_API_KEY", "test-project-token")
    monkeypatch.setitem(sys.modules, "posthog", SimpleNamespace(Posthog=BrokenPosthog))
    module = _reload_module()

    assert module.initialize_posthog_observability() == {
        "status": "INITIALIZATION_FAILED",
        "provider": "POSTHOG",
        "error_type": "RuntimeError",
        "can_execute": False,
    }


def test_v17_observability_calls_posthog_without_changing_sentry_contract():
    source = Path("v17_observability.py").read_text(encoding="utf-8")

    assert "initialize_posthog_observability()" in source
    assert '"provider": "SENTRY"' in source
    assert '"can_execute": False' in source


def test_posthog_dependency_is_importable():
    assert importlib.import_module("posthog")
