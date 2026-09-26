from types import SimpleNamespace

import v17.ncaaf_prop_history_maintenance as maintenance
from ncaaf_cfbd_client import CFBDUnavailable


class FakeQuery:
    def __init__(self):
        self.mode = None

    def select(self, fields):
        self.mode = "select"
        return self

    def eq(self, field, value):
        return self

    def in_(self, field, values):
        return self

    def execute(self):
        assert self.mode == "select"
        return SimpleNamespace(data=[])


class FakeDB:
    def table(self, name):
        assert name == "wow_ncaaf_source_snapshots"
        return FakeQuery()


class FailingCFBD:
    def player_game_stats(self, **kwargs):
        raise CFBDUnavailable(
            "CFBD_HTTP_ERROR",
            "CFBD returned HTTP 429 for /games/players.",
            http_status=429,
        )


def test_prop_history_propagates_safe_cfbd_http_status(monkeypatch):
    monkeypatch.setattr(maintenance.CFBDClient, "from_environment", lambda: FailingCFBD())

    payload = maintenance.run_ncaaf_prop_history_maintenance(
        FakeDB(), seasons=[2023], weeks=[1]
    )

    assert payload["status"] == "BLOCKED"
    assert payload["code"] == "CFBD_HTTP_ERROR"
    assert payload["blocked_stage"] == "CFBD_PLAYER_HISTORY_ACQUISITION"
    assert payload["detail"]["season"] == 2023
    assert payload["detail"]["provider_error_code"] == "CFBD_HTTP_ERROR"
    assert payload["detail"]["http_status"] == 429
    assert payload["detail"]["provider_status_blocker"] == "CFBD_HTTP_STATUS_429"
    assert "response" not in payload["detail"]
    assert payload["probability_publishable"] is False
    assert payload["can_execute"] is False


def test_prop_history_propagates_status_for_client_bootstrap_failure(monkeypatch):
    def unavailable():
        raise CFBDUnavailable(
            "CFBD_HTTP_ERROR",
            "CFBD returned HTTP 401 for /games/players.",
            http_status=401,
        )

    monkeypatch.setattr(maintenance.CFBDClient, "from_environment", unavailable)
    payload = maintenance.run_ncaaf_prop_history_maintenance(
        FakeDB(), seasons=[2025], weeks=[1]
    )

    assert payload["status"] == "BLOCKED"
    assert payload["detail"]["http_status"] == 401
    assert payload["detail"]["provider_status_blocker"] == "CFBD_HTTP_STATUS_401"
    assert payload["probability_publishable"] is False
    assert payload["can_execute"] is False
