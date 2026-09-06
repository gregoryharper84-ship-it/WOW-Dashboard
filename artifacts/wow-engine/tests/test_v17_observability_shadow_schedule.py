from __future__ import annotations

import importlib


def test_observability_return_contract_unchanged_when_shadow_disabled(monkeypatch):
    monkeypatch.delenv("SENTRY_DSN", raising=False)
    monkeypatch.delenv("WOW_MLB_GAME_WINNER_SHADOW_EVAL_ON_START", raising=False)
    import v17_observability as module
    module = importlib.reload(module)
    assert module.initialize_observability() == {
        "status": "DISABLED_NOT_CONFIGURED",
        "provider": "SENTRY",
        "can_execute": False,
    }
