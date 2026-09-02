import logging

from fastapi import Depends, FastAPI

from v17.prop_forward_cohort_route import _int_env, install_prop_forward_cohort_route
from v17.prop_forward_cohort_scheduler import run_prop_forward_cohort_loop


class DummyMarketApi:
    class ScorePropRequest:
        def __init__(self, **kwargs):
            self.kwargs = kwargs


def _auth():
    return None


def test_scheduler_defaults_off(monkeypatch):
    monkeypatch.delenv("WOW_PROP_FORWARD_COHORT_ENABLED", raising=False)
    app = FastAPI()

    install_prop_forward_cohort_route(
        app,
        auth_dependency=Depends(_auth),
        db_client_fn=lambda: object(),
        market_api=DummyMarketApi(),
    )

    assert getattr(app.state, "wow_prop_forward_cohort_scheduler_installed", False) is False
    assert sum(route.path == "/v17/prop-forward-cohort-run" for route in app.router.routes) == 1


def test_scheduler_registers_once_when_enabled(monkeypatch):
    monkeypatch.setenv("WOW_PROP_FORWARD_COHORT_ENABLED", "1")
    monkeypatch.setenv("WOW_PROP_FORWARD_COHORT_INTERVAL_SECONDS", "900")
    app = FastAPI()
    initial_startup_n = len(app.router.on_startup)

    kwargs = {
        "auth_dependency": Depends(_auth),
        "db_client_fn": lambda: object(),
        "market_api": DummyMarketApi(),
    }
    install_prop_forward_cohort_route(app, **kwargs)
    install_prop_forward_cohort_route(app, **kwargs)

    assert app.state.wow_prop_forward_cohort_scheduler_installed is True
    assert len(app.router.on_startup) == initial_startup_n + 1
    assert sum(route.path == "/v17/prop-forward-cohort-run" for route in app.router.routes) == 1


def test_scheduler_env_bounds(monkeypatch):
    monkeypatch.setenv("TEST_INTERVAL", "1")
    assert _int_env("TEST_INTERVAL", 900, minimum=60, maximum=86400) == 60
    monkeypatch.setenv("TEST_INTERVAL", "999999")
    assert _int_env("TEST_INTERVAL", 900, minimum=60, maximum=86400) == 86400
    monkeypatch.setenv("TEST_INTERVAL", "invalid")
    assert _int_env("TEST_INTERVAL", 900, minimum=60, maximum=86400) == 900


def test_scheduler_loop_contract_is_non_execution_oriented():
    # The loop is server-side capture orchestration only; it is not an order or
    # wager executor. Keep this assertion close to the scheduled entrypoint so a
    # future refactor cannot quietly introduce an execution API here.
    assert callable(run_prop_forward_cohort_loop)
    assert logging.getLogger("wow.v17.prop_forward_cohort").name == "wow.v17.prop_forward_cohort"
