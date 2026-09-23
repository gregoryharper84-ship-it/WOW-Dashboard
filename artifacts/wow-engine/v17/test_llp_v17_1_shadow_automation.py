from __future__ import annotations

from pathlib import Path

from fastapi import FastAPI

import v17.core_intelligence_runtime as core_runtime
from v17.llp_v17_1_shadow_internal_routes import (
    AUTOMATIC_PROMOTION_ALLOWED,
    CAN_EXECUTE,
    PRODUCTION_MUTATION_ALLOWED,
    install_llp_v17_1_shadow_internal_routes,
)
from v17.llp_v17_1_shadow_status_route import (
    install_llp_shadow_status_route,
    shadow_automation_status,
)


def _paths(app: FastAPI) -> set[str]:
    return {getattr(route, "path", "") for route in app.routes}


def test_internal_shadow_routes_mount_with_existing_auth_seam():
    app = FastAPI()
    assert install_llp_v17_1_shadow_internal_routes(
        app,
        get_client_fn=lambda: object(),
        existing_auth_dependency=lambda authorization=None: None,
    ) is True
    paths = _paths(app)
    assert "/internal/v17/llp/shadow/capture" in paths
    assert "/internal/v17/llp/shadow/grade" in paths
    assert "/internal/v17/llp/shadow/scorecard" in paths
    assert CAN_EXECUTE is False
    assert AUTOMATIC_PROMOTION_ALLOWED is False
    assert PRODUCTION_MUTATION_ALLOWED is False


def test_core_intelligence_does_not_mount_shadow_automation_by_default(monkeypatch):
    monkeypatch.delenv("WOW_V17_LLP_SHADOW_AUTOMATION_ACTIVE", raising=False)
    seen: list[bool] = []
    monkeypatch.setattr(
        core_runtime,
        "install_llp_v17_1_shadow_internal_routes",
        lambda *args, **kwargs: seen.append(True) or True,
    )
    app = FastAPI()
    core_runtime.install_core_intelligence_routes(
        app,
        auth_dependency=lambda authorization=None: None,
        get_client_fn=lambda: object(),
    )
    assert seen == []


def test_core_intelligence_mounts_shadow_automation_only_when_enabled(monkeypatch):
    monkeypatch.setenv("WOW_V17_LLP_SHADOW_AUTOMATION_ACTIVE", "1")
    seen: list[dict] = []

    def installer(app, *, get_client_fn, existing_auth_dependency):
        seen.append({
            "app": app,
            "client": get_client_fn,
            "auth": existing_auth_dependency,
        })
        return True

    monkeypatch.setattr(core_runtime, "install_llp_v17_1_shadow_internal_routes", installer)
    app = FastAPI()
    auth = lambda authorization=None: None
    client = lambda: object()
    core_runtime.install_core_intelligence_routes(
        app,
        auth_dependency=auth,
        get_client_fn=client,
    )
    assert len(seen) == 1
    assert seen[0]["app"] is app
    assert seen[0]["client"] is client
    assert seen[0]["auth"] is auth


def test_shadow_status_reports_core_intelligence_configured_off(monkeypatch):
    monkeypatch.delenv("WOW_V17_CORE_INTELLIGENCE_ACTIVE", raising=False)
    monkeypatch.setenv("WOW_V17_LLP_SHADOW_AUTOMATION_ACTIVE", "1")
    status = shadow_automation_status(FastAPI())
    assert status["status"] == "INACTIVE_CORE_INTELLIGENCE_DISABLED"
    assert status["routes_expected"] is False
    assert status["routes_present"] is False
    assert status["can_execute"] is False


def test_shadow_status_reports_shadow_automation_configured_off(monkeypatch):
    monkeypatch.setenv("WOW_V17_CORE_INTELLIGENCE_ACTIVE", "1")
    monkeypatch.delenv("WOW_V17_LLP_SHADOW_AUTOMATION_ACTIVE", raising=False)
    status = shadow_automation_status(FastAPI())
    assert status["status"] == "INACTIVE_SHADOW_AUTOMATION_DISABLED"
    assert status["routes_expected"] is False
    assert status["can_execute"] is False


def test_shadow_status_fails_when_configured_active_routes_are_missing(monkeypatch):
    monkeypatch.setenv("WOW_V17_CORE_INTELLIGENCE_ACTIVE", "1")
    monkeypatch.setenv("WOW_V17_LLP_SHADOW_AUTOMATION_ACTIVE", "1")
    status = shadow_automation_status(FastAPI())
    assert status["status"] == "MISCONFIGURED_ROUTE_MISSING"
    assert status["routes_expected"] is True
    assert status["routes_present"] is False
    assert sorted(status["missing_routes"]) == [
        "/internal/v17/llp/shadow/capture",
        "/internal/v17/llp/shadow/grade",
        "/internal/v17/llp/shadow/scorecard",
    ]
    assert status["can_execute"] is False


def test_shadow_status_is_active_only_when_configured_and_routes_exist(monkeypatch):
    monkeypatch.setenv("WOW_V17_CORE_INTELLIGENCE_ACTIVE", "1")
    monkeypatch.setenv("WOW_V17_LLP_SHADOW_AUTOMATION_ACTIVE", "1")
    app = FastAPI()
    install_llp_v17_1_shadow_internal_routes(
        app,
        get_client_fn=lambda: object(),
        existing_auth_dependency=lambda authorization=None: None,
    )
    status = shadow_automation_status(app)
    assert status["status"] == "ACTIVE"
    assert status["routes_expected"] is True
    assert status["routes_present"] is True
    assert status["missing_routes"] == []
    assert status["automatic_promotion_allowed"] is False
    assert status["production_mutation_allowed"] is False
    assert status["can_execute"] is False


def test_status_route_mounts_independently_of_shadow_runtime_flags(monkeypatch):
    monkeypatch.delenv("WOW_V17_CORE_INTELLIGENCE_ACTIVE", raising=False)
    monkeypatch.delenv("WOW_V17_LLP_SHADOW_AUTOMATION_ACTIVE", raising=False)
    app = FastAPI()
    assert install_llp_shadow_status_route(
        app,
        existing_auth_dependency=lambda authorization=None: None,
    ) is True
    assert "/internal/v17/llp/shadow/status" in _paths(app)
    assert "/internal/v17/llp/shadow/capture" not in _paths(app)


def test_scheduled_workflow_is_shadow_only_main_oidc_scoped_and_activation_aware():
    repo_root = Path(__file__).resolve().parents[3]
    workflow = (repo_root / ".github" / "workflows" / "wow-v17-llp-shadow-observer.yml").read_text(
        encoding="utf-8"
    )
    assert 'cron: "17 2,8,14,20 * * *"' in workflow
    assert "id-token: write" in workflow
    assert "github.event_name == 'schedule' || github.event_name == 'workflow_dispatch'" in workflow
    assert 'WOW_CAN_EXECUTE: "false"' in workflow
    assert 'WOW_DRY_RUN_ONLY: "true"' in workflow
    assert 'WOW_AUTOMATIC_PROMOTION: "false"' in workflow
    assert "/internal/v17/llp/shadow/status" in workflow
    assert "/internal/v17/llp/shadow/capture" in workflow
    assert "/internal/v17/llp/shadow/grade" in workflow
    assert "/internal/v17/llp/shadow/scorecard" in workflow
    assert "COMPLETED_SHADOW_AUTOMATION_INACTIVE" in workflow
    assert "MISCONFIGURED_ROUTE_MISSING" not in workflow  # server state, not a client-side guess
    assert 'activation_status == "ACTIVE"' in workflow
    assert '"automatic_promotion_allowed": False' in workflow
    assert '"production_mutation_allowed": False' in workflow
    assert '"can_execute": False' in workflow
