"""Regression: CI must not spawn Flask's startup PostgreSQL warmup daemon.

The production bootstrap remains active. Test the actual extracted module-level
guard without importing the 1.6 MB Flask app and starting unrelated daemons.
"""
from __future__ import annotations

import ast
import os
from pathlib import Path
from types import SimpleNamespace
from unittest.mock import Mock

import pytest


_APP = Path(__file__).resolve().parents[2] / "app.py"


def _startup_guard_code():
    tree = ast.parse(_APP.read_text(encoding="utf-8"), filename=str(_APP))
    nodes = [
        node for node in tree.body
        if isinstance(node, ast.If)
        and "WOW_CI" in ast.unparse(node.test)
        and "startup-warmup" in ast.unparse(node)
        and "_run_startup_warmup" in ast.unparse(node)
    ]
    assert len(nodes) == 1, "Missing/ambiguous WOW_CI startup warmup guard"
    return compile(ast.fix_missing_locations(ast.Module(body=nodes, type_ignores=[])), str(_APP), "exec")


@pytest.mark.parametrize("value", ["1", "true", "YES", " TrUe "])
def test_ci_never_starts_background_warmup(value, monkeypatch):
    monkeypatch.setenv("WOW_CI", value)
    threaded = Mock()
    timings = Mock()
    warmup = Mock()
    exec(_startup_guard_code(), {
        "os": os,
        "_threading": SimpleNamespace(Thread=threaded),
        "_run_startup_warmup": warmup,
        "_bt": timings,
    })
    threaded.assert_not_called()
    # CI must initialize all required schemas before the tests execute.
    warmup.assert_called_once_with()
    timings.assert_called_once_with("startup warmup completed synchronously under CI — app fully ready")


@pytest.mark.parametrize("value", ["", "0", "false"])
def test_normal_startup_still_launches_warmup(value, monkeypatch):
    monkeypatch.setenv("WOW_CI", value)
    threaded = Mock()
    timings = Mock()
    warmup = Mock()
    exec(_startup_guard_code(), {
        "os": os,
        "_threading": SimpleNamespace(Thread=threaded),
        "_run_startup_warmup": warmup,
        "_bt": timings,
    })
    threaded.assert_called_once_with(
        target=warmup, daemon=True, name="startup-warmup"
    )
    threaded.return_value.start.assert_called_once_with()
    timings.assert_called_once_with("startup daemon spawned — app fully ready")
