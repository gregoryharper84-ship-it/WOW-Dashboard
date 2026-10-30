"""Regression coverage for opt-in isolated Weather collector ownership."""
from __future__ import annotations
import importlib.util
from pathlib import Path

HERE = Path(__file__).resolve().parents[1] / "kalshi_weather_v2"
spec = importlib.util.spec_from_file_location("isolated_capture_wiring", HERE / "isolated_capture_wiring.py")
module = importlib.util.module_from_spec(spec)
spec.loader.exec_module(module)


def test_no_owner_does_not_install_even_if_shared_flag_leaks():
    calls = []

    def installer(*args, **kwargs):
        calls.append((args, kwargs))

    result = module.install_isolated_capture_if_owned(
        object(), db_client_fn=lambda: None, installer=installer,
        env={module.STARTUP_FLAG: "1"},
    )
    assert not calls
    assert result["status"] == "ISOLATED_COLLECTOR_NOT_OWNER"
    assert result["startup_enabled"] is False
    assert result["trigger_installed"] is False
    assert result["can_execute"] is False


def test_explicit_owner_attaches_existing_controller_only_once():
    calls = []
    app = object()
    db = lambda: object()

    def installer(arg, *, db_client_fn):
        calls.append((arg, db_client_fn))

    result = module.install_isolated_capture_if_owned(
        app, db_client_fn=db, installer=installer,
        env={module.OWNER_FLAG: "1", module.STARTUP_FLAG: "0"},
    )
    assert calls == [(app, db)]
    assert result["trigger_installed"] is True
    assert result["startup_enabled"] is False
    assert result["probability_publishable"] is False


def test_scheduler_is_only_requested_by_explicit_second_flag():
    called = []
    result = module.install_isolated_capture_if_owned(
        object(), db_client_fn=lambda: None,
        installer=lambda *args, **kwargs: called.append(1),
        env={module.OWNER_FLAG: "1", module.STARTUP_FLAG: "1"},
    )
    assert called == [1]
    assert result["startup_enabled"] is True
    assert result["status"].endswith("NOT_PRODUCTION_VERIFIED")
    assert result["can_execute"] is False


def test_untrusted_flag_strings_cannot_enable_collector():
    calls = []
    for value in ("true", "yes", "ON", "1 ", "0", ""):
        result = module.install_isolated_capture_if_owned(
            object(), db_client_fn=lambda: None,
            installer=lambda *a, **kw: calls.append(1),
            env={module.OWNER_FLAG: value},
        )
        assert result["trigger_installed"] is False
    assert not calls
