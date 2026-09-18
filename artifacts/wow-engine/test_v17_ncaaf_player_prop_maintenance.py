from dataclasses import dataclass

import v17.ncaaf_player_prop_maintenance as maintenance


@dataclass
class Snapshot:
    provider: str = "CFBD"
    endpoint: str = "/games/players"
    season: int = 2026
    week: int = 1
    requested_at: str = "2026-09-18T00:00:00+00:00"
    retrieved_at: str = "2026-09-18T00:00:01+00:00"
    request_params: dict = None
    response_rows: list = None
    response_row_count: int = 1
    payload_sha256: str = "a" * 64
    acquisition_status: str = "AVAILABLE"
    blocker_codes: list = None
    can_execute: bool = False

    def __post_init__(self):
        if self.request_params is None:
            self.request_params = {"year": self.season, "week": self.week}
        if self.response_rows is None:
            self.response_rows = []
        if self.blocker_codes is None:
            self.blocker_codes = []


@dataclass
class Observation:
    stat_type: str = "PASS_YARDS"


def test_history_maintenance_preserves_model_unavailable_until_training(monkeypatch):
    monkeypatch.setattr(maintenance.CFBDClient, "from_environment", classmethod(lambda cls: object()))
    monkeypatch.setattr(
        maintenance,
        "hydrate_cfbd_player_stats",
        lambda *args, **kwargs: [Snapshot(season=kwargs["season"], week=1)],
    )
    monkeypatch.setattr(maintenance, "persist_source_snapshots", lambda db, rows: len(rows))
    monkeypatch.setattr(maintenance, "materialize_player_stats", lambda snapshot: [Observation(), Observation("RUSH_YARDS")])
    monkeypatch.setattr(maintenance, "persist_player_stats", lambda db, rows: len(list(rows)))

    result = maintenance.run_ncaaf_player_prop_history_maintenance(object(), seasons=[2025, 2026], weeks=[1])

    assert result["status"] == "PLAYER_PROP_HISTORY_READY"
    assert result["player_stat_candidate_n"] == 4
    assert result["model_capability_status"] == "MODEL_UNAVAILABLE"
    assert result["automatic_certification"] is False
    assert result["automatic_promotion"] is False
    assert result["probability_publishable"] is False
    assert result["can_execute"] is False


def test_history_maintenance_fails_closed_when_cfbd_is_unavailable(monkeypatch):
    def unavailable(cls):
        raise maintenance.CFBDUnavailable("CFBD_API_KEY_MISSING", "missing")

    monkeypatch.setattr(maintenance.CFBDClient, "from_environment", classmethod(unavailable))
    result = maintenance.run_ncaaf_player_prop_history_maintenance(object(), seasons=[2026], weeks=[1])

    assert result["status"] == "BLOCKED"
    assert result["code"] == "CFBD_API_KEY_MISSING"
    assert result["model_capability_status"] == "MODEL_UNAVAILABLE"
    assert result["probability_publishable"] is False
    assert result["can_execute"] is False
