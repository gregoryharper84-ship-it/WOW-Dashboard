from datetime import datetime, timedelta, timezone

import pytest

import mlb_1ip_live_acquisition as live


def test_hydrator_passes_target_event_time_to_identity_resolver(monkeypatch):
    seen = {}

    class _Stop(RuntimeError):
        pass

    def resolve(player, *, http_get, event_start=None):
        seen["player"] = player
        seen["event_start"] = event_start
        raise _Stop("stop after identity call")

    monkeypatch.setattr(live, "_resolve_player_id", resolve)
    event_start = datetime.now(timezone.utc) + timedelta(hours=3)

    with pytest.raises(_Stop):
        live.hydrate_mlb_1ip_evidence(
            player="Peyton Tolle",
            event_start_time=event_start.isoformat(),
            http_get=lambda *args, **kwargs: None,
        )

    assert seen["player"] == "Peyton Tolle"
    assert seen["event_start"] == event_start
