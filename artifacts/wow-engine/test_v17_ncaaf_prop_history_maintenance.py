from types import SimpleNamespace

import v17.ncaaf_prop_history_maintenance as maintenance
from ncaaf_cfbd_client import CFBDResponse, CFBDUnavailable


PLAYER_GAME = {
    "id": 9001,
    "teams": [
        {
            "team": "Alpha",
            "conference": "SEC",
            "homeAway": "home",
            "points": 35,
            "categories": [
                {
                    "name": "passing",
                    "types": [
                        {"name": "YDS", "athletes": [{"id": "qb-1", "name": "Quarter Back", "stat": "301"}]},
                        {"name": "C/ATT", "athletes": [{"id": "qb-1", "name": "Quarter Back", "stat": "24/33"}]},
                    ],
                },
                {
                    "name": "receiving",
                    "types": [
                        {"name": "REC", "athletes": [{"id": "wr-1", "name": "Wide Receiver", "stat": "8"}]},
                        {"name": "YDS", "athletes": [{"id": "wr-1", "name": "Wide Receiver", "stat": "119"}]},
                    ],
                },
            ],
        }
    ],
}


class FakeCFBD:
    def __init__(self):
        self.weeks = []

    def player_game_stats(self, *, year, week=None, team=None, conference=None, classification=None, season_type=None, category=None):
        self.weeks.append((year, week))
        return CFBDResponse(
            endpoint="/games/players",
            params={
                "year": year,
                "week": week,
                "classification": classification,
                "seasonType": season_type,
            },
            rows=[PLAYER_GAME],
        )


class FakeQuery:
    def __init__(self, db):
        self.db = db
        self.mode = None
        self.rows = []

    def select(self, fields):
        self.mode = "select"
        return self

    def eq(self, field, value):
        return self

    def in_(self, field, values):
        return self

    def upsert(self, rows, on_conflict=None):
        self.mode = "upsert"
        self.rows = list(rows)
        return self

    def execute(self):
        if self.mode == "select":
            return SimpleNamespace(data=list(self.db.available_rows))
        if self.mode == "upsert":
            self.db.persisted.extend(self.rows)
            return SimpleNamespace(data=[{} for _ in self.rows])
        raise AssertionError("query mode was not selected")


class FakeDB:
    def __init__(self, available_rows=None):
        self.available_rows = list(available_rows or [])
        self.persisted = []

    def table(self, name):
        assert name == "wow_ncaaf_source_snapshots"
        return FakeQuery(self)


def test_prop_history_maintenance_builds_corpus_but_never_grants_model_authority(monkeypatch):
    client = FakeCFBD()
    monkeypatch.setattr(maintenance.CFBDClient, "from_environment", lambda: client)

    payload = maintenance.run_ncaaf_prop_history_maintenance(
        FakeDB(), seasons=[2024, 2025], weeks=[1, 2]
    )

    assert payload["status"] == "CORPUS_UPDATED"
    assert payload["source_snapshot_n"] == 4
    assert payload["source_snapshot_persisted_n"] == 4
    assert payload["available_snapshot_key_n"] == 4
    assert payload["player_stat_profile"]["status"] == "READY_FOR_NORMALIZATION"
    assert payload["player_stat_profile"]["game_n"] == 4
    assert payload["normalization_ready"] is True
    assert payload["model_build_ready"] is False
    assert payload["next_gate"] == "NCAAF_PROP_CANONICAL_STAT_NORMALIZATION_AND_TRAINING_CORPUS"
    assert payload["automatic_certification"] is False
    assert payload["automatic_promotion"] is False
    assert payload["probability_publishable"] is False
    assert payload["can_execute"] is False
    assert tuple(payload["phase1_stat_types"]) == maintenance.PHASE1_STAT_TYPES


def test_prop_history_maintenance_fetches_only_missing_available_weeks(monkeypatch):
    client = FakeCFBD()
    monkeypatch.setattr(maintenance.CFBDClient, "from_environment", lambda: client)
    db = FakeDB(
        available_rows=[{"season": 2025, "week": 1, "acquisition_status": "AVAILABLE"}]
    )

    payload = maintenance.run_ncaaf_prop_history_maintenance(db, seasons=[2025], weeks=[1, 2])

    assert payload["status"] == "CORPUS_UPDATED"
    assert client.weeks == [(2025, 2)]
    assert payload["source_snapshot_n"] == 1
    assert payload["available_snapshot_key_n"] == 2


def test_prop_history_maintenance_skips_cfbd_when_corpus_is_current(monkeypatch):
    monkeypatch.setattr(
        maintenance.CFBDClient,
        "from_environment",
        lambda: (_ for _ in ()).throw(AssertionError("CFBD should not be called")),
    )
    db = FakeDB(
        available_rows=[
            {"season": 2025, "week": 1, "acquisition_status": "AVAILABLE"},
            {"season": 2025, "week": 2, "acquisition_status": "AVAILABLE"},
        ]
    )

    payload = maintenance.run_ncaaf_prop_history_maintenance(db, seasons=[2025], weeks=[1, 2])

    assert payload["status"] == "CORPUS_ALREADY_CURRENT"
    assert payload["source_snapshot_n"] == 0
    assert payload["source_snapshot_persisted_n"] == 0
    assert payload["available_snapshot_key_n"] == 2
    assert payload["model_build_ready"] is False
    assert payload["probability_publishable"] is False
    assert payload["can_execute"] is False


def test_prop_history_maintenance_preserves_typed_cfbd_failure(monkeypatch):
    def unavailable():
        raise CFBDUnavailable("CFBD_API_KEY_MISSING", "missing")

    monkeypatch.setattr(maintenance.CFBDClient, "from_environment", unavailable)
    payload = maintenance.run_ncaaf_prop_history_maintenance(FakeDB(), seasons=[2025], weeks=[1])

    assert payload["status"] == "BLOCKED"
    assert payload["code"] == "CFBD_API_KEY_MISSING"
    assert payload["blocked_stage"] == "CFBD_PLAYER_HISTORY_ACQUISITION"
    assert payload["probability_publishable"] is False
    assert payload["can_execute"] is False


def test_prop_history_maintenance_does_not_misclassify_schema_failure(monkeypatch):
    class BrokenCFBD(FakeCFBD):
        def player_game_stats(self, **kwargs):
            return CFBDResponse(
                endpoint="/games/players",
                params=kwargs,
                rows=[{"id": 9001, "teams": {}}],
            )

    monkeypatch.setattr(maintenance.CFBDClient, "from_environment", lambda: BrokenCFBD())
    payload = maintenance.run_ncaaf_prop_history_maintenance(FakeDB(), seasons=[2025], weeks=[1])

    assert payload["status"] == "BLOCKED"
    assert payload["code"] == "NCAAF_PROP_PLAYER_STATS_SCHEMA_INVALID"
    assert payload["blocked_stage"] == "PLAYER_HISTORY_SCHEMA_REVIEW"
    assert payload["probability_publishable"] is False
    assert payload["can_execute"] is False
