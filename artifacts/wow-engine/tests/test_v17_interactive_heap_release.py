"""Interactive pick wrapper releases heap after the result is built, fail-open (incident 1388)."""
from __future__ import annotations

from types import SimpleNamespace

import pytest
from fastapi import FastAPI

from v17 import interactive_pick_hydration as iph
from v17 import memory_admission


def _install(monkeypatch, endpoint):
    app = FastAPI()
    app.post("/score-pick-request")(endpoint)
    monkeypatch.setattr(iph, "prehydrate_batch", lambda batch, *, market_api: batch)
    monkeypatch.setattr(iph, "_all_rows_started_and_preflight_ready", lambda *a, **k: False)
    assert iph.install_interactive_pick_hydration_wrapper(app, market_api=None)
    route = next(r for r in app.router.routes if getattr(r, "path", None) == "/score-pick-request")
    return route.endpoint


def _batch():
    return SimpleNamespace(response_mode="FULL", rows=[])


def test_release_runs_after_result_and_result_is_unchanged(monkeypatch):
    order = []
    payload = {"p": 0.5, "can_execute": False}

    def score(batch, ident=None):
        order.append("score")
        return payload

    ep = _install(monkeypatch, score)
    monkeypatch.setattr(memory_admission, "release_process_memory", lambda: order.append("release"))
    assert ep(_batch(), None) is payload
    assert order == ["score", "release"]


def test_release_runs_on_exception_and_error_propagates(monkeypatch):
    calls = []

    def boom(batch, ident=None):
        raise ValueError("x")

    ep = _install(monkeypatch, boom)
    monkeypatch.setattr(memory_admission, "release_process_memory", lambda: calls.append(1))
    with pytest.raises(ValueError):
        ep(_batch(), None)
    assert calls == [1]


def test_release_failure_is_fail_open(monkeypatch):
    ep = _install(monkeypatch, lambda batch, ident=None: {"ok": True})

    def bad():
        raise RuntimeError("trim")

    monkeypatch.setattr(memory_admission, "release_process_memory", bad)
    assert ep(_batch(), None) == {"ok": True}
