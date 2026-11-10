"""Regression for issue #1794: CI must not import-start a SQL daemon.

Isolates the real module-level startup statement via AST so this test can
execute both modes without importing Flask or spawning DB/network workers.
Production warmup remains present and launches normally.
"""
from __future__ import annotations

import ast
from pathlib import Path
from types import SimpleNamespace

import pytest

APP_FILE = Path(__file__).resolve().parents[2] / "app.py"


def _startup_guard():
    content = APP_FILE.read_text(encoding="utf-8")
    tree = ast.parse(content, filename=str(APP_FILE))
    matches = [
        statement for statement in tree.body
        if isinstance(statement, ast.If)
        and "_threading.Thread(target=_run_startup_warmup" in (
            ast.get_source_segment(content, statement) or ""
        )
    ]
    assert len(matches) == 1, "exactly one module-level startup guard required"
    guard = matches[0]
    assert "WOW_CI" in ast.unparse(guard.test)
    assert guard.orelse, "a typed CI skip path is required"
    assert "def _run_startup_warmup" in content
    assert "_s2_ensure_tables()" in content, "production SQL warmup must remain"
    return compile(ast.Module(body=[guard], type_ignores=[]), str(APP_FILE), "exec")


@pytest.mark.parametrize("ci_value", ["1", "true", "yes", " TRUE ", "YeS"])
def test_ci_import_does_not_spawn_database_warmup(ci_value):
    events = []

    class StubThread:
        def __init__(self, *, target, daemon, name):
            events.append(("init", target.__name__, daemon, name))

        def start(self):
            events.append(("start",))

    def _run_startup_warmup():
        raise AssertionError("startup warmup must never be called in this test")

    context = {
        "os": SimpleNamespace(environ={"WOW_CI": ci_value}),
        "_threading": SimpleNamespace(Thread=StubThread),
        "_run_startup_warmup": _run_startup_warmup,
        "_bt": lambda message: events.append(("log", message)),
    }
    exec(_startup_guard(), context, context)
    assert events == [("log", "startup daemon disabled under CI; explicit test setup owns DB")]


@pytest.mark.parametrize("ci_value", ["", "0", "false", "off"])
def test_production_import_still_spawns_one_warmup_thread(ci_value):
    events = []

    def _run_startup_warmup():
        raise AssertionError("only thread startup should occur in the test")

    class StubThread:
        def __init__(self, *, target, daemon, name):
            assert target is _run_startup_warmup
            assert daemon is True
            assert name == "startup-warmup"
            events.append("constructed")

        def start(self):
            events.append("started")

    context = {
        "os": SimpleNamespace(environ={"WOW_CI": ci_value}),
        "_threading": SimpleNamespace(Thread=StubThread),
        "_run_startup_warmup": _run_startup_warmup,
        "_bt": lambda message: events.append(message),
    }
    exec(_startup_guard(), context, context)
    assert events == [
        "constructed",
        "started",
        "startup daemon spawned — app fully ready",
    ]
