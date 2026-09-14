from __future__ import annotations

import asyncio
from types import SimpleNamespace

import nfl_event_model_startup as runtime


class _FakeApp:
    def __init__(self) -> None:
        self.state = SimpleNamespace()
        self.startup_handlers = []

    def on_event(self, event: str):
        assert event == "startup"

        def register(handler):
            self.startup_handlers.append(handler)
            return handler

        return register


def test_nfl_certification_is_disabled_by_default(monkeypatch):
    monkeypatch.delenv("WOW_NFL_MODEL_CERTIFY_ON_STARTUP", raising=False)
    called = []
    monkeypatch.setattr(runtime, "ensure_champion_model", lambda _db: called.append(True))
    app = _FakeApp()

    runtime.install_nfl_model_startup(app, db_client_fn=lambda: object())
    asyncio.run(app.startup_handlers[0]())

    assert called == []


def test_nfl_certification_remains_explicitly_available(monkeypatch):
    monkeypatch.setenv("WOW_NFL_MODEL_CERTIFY_ON_STARTUP", "1")
    called = []

    async def inline_to_thread(fn, *args, **kwargs):
        return fn(*args, **kwargs)

    monkeypatch.setattr(asyncio, "to_thread", inline_to_thread)
    monkeypatch.setattr(
        runtime,
        "ensure_champion_model",
        lambda _db: called.append(True)
        or {"status": "EXISTING_CHAMPION", "model_artifact_version": "test"},
    )
    app = _FakeApp()

    runtime.install_nfl_model_startup(app, db_client_fn=lambda: object())

    async def run_startup_and_wait():
        await app.startup_handlers[0]()
        await asyncio.gather(*tuple(runtime._BACKGROUND_TASKS))

    asyncio.run(run_startup_and_wait())

    assert called == [True]
