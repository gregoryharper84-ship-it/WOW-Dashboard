from types import SimpleNamespace

from v17 import interactive_team_event_io as io


class _Response:
    def raise_for_status(self):
        return None

    def json(self):
        return {"people": [{"id": 11, "stats": []}, {"id": 22, "stats": []}]}


class _StatsClient:
    def __init__(self):
        self.calls = []

    def get(self, url):
        self.calls.append(url)
        return _Response()


def test_player_stats_fetch_is_cached_within_ttl(monkeypatch):
    fake = _StatsClient()
    monkeypatch.setattr(io, "_STATS_CLIENT", fake)
    monkeypatch.setattr(io, "_stats_cache_ttl_seconds", lambda: 600.0)
    io._STATS_CACHE.clear()

    first = io._bounded_cached_player_stats([22, 11, 22], 2026)
    second = io._bounded_cached_player_stats([11, 22], 2026)

    assert sorted(first) == [11, 22]
    assert sorted(second) == [11, 22]
    assert len(fake.calls) == 1
    assert "personIds=11,22" in fake.calls[0]


def test_event_api_client_is_created_once_and_reused(monkeypatch):
    created = []
    event_api = SimpleNamespace(get_client=lambda: created.append(object()) or created[-1])

    assert io.install_interactive_event_api_client_reuse(event_api) is True
    first = event_api.get_client()
    second = event_api.get_client()

    assert first is second
    assert len(created) == 1
    assert getattr(event_api, io._STATE_KEY_DB) is True


def test_mlb_installer_injects_bounded_stats_fetcher(monkeypatch):
    import mlb_event_specialist_v16 as specialist

    calls = []

    def original(*args, **kwargs):
        calls.append(kwargs)
        return {"ok": True}

    monkeypatch.setattr(specialist, "score_prospective_event", original)
    monkeypatch.delattr(specialist, io._STATE_KEY_MLB, raising=False)

    assert io.install_interactive_mlb_evidence_fetch() is True
    result = specialist.score_prospective_event(req=object(), bridge_payload={}, client=object())

    assert result == {"ok": True}
    assert calls[0]["stats_fetcher"] is io._bounded_cached_player_stats
    assert getattr(specialist, io._STATE_KEY_MLB) is True
