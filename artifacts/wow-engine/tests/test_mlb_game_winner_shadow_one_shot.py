from __future__ import annotations

import importlib


def test_one_shot_is_disabled_by_default(monkeypatch):
    monkeypatch.delenv("WOW_MLB_GAME_WINNER_SHADOW_EVAL_ON_START", raising=False)
    import v17.mlb_game_winner_shadow_one_shot as module
    module = importlib.reload(module)
    assert module.schedule_if_enabled() is False
    assert module._started is False


def test_one_shot_schedules_only_when_explicitly_enabled(monkeypatch):
    monkeypatch.setenv("WOW_MLB_GAME_WINNER_SHADOW_EVAL_ON_START", "1")
    import v17.mlb_game_winner_shadow_one_shot as module
    module = importlib.reload(module)

    class FakeThread:
        def __init__(self, *, target, name, daemon):
            self.target = target
            self.name = name
            self.daemon = daemon
            self.started = False
        def start(self):
            self.started = True

    created = []
    def factory(**kwargs):
        thread = FakeThread(**kwargs)
        created.append(thread)
        return thread

    monkeypatch.setattr(module.threading, "Thread", factory)
    assert module.schedule_if_enabled() is True
    assert module.schedule_if_enabled() is False
    assert len(created) == 1
    assert created[0].daemon is True
    assert created[0].started is True
