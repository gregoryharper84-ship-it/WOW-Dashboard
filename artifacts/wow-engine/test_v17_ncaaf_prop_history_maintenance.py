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
    def player_game_stats(self, *, year, week=None, team=None, conference=None, classification=None, season_type=None, category=None):
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
    def __init__(self):
        self.rows = []

    def upsert(self, rows, on_conflict=None):
        self.rows = list(rows)
        return self

    def execute(self):
        return SimpleNamespace(data=[{} for _ in self.rows])


class FakeDB:
    def table(self, name):
        assert name == "wow_ncaaf_source_snapshots"
        return FakeQuery()


def test_prop_history_maintenance_builds_corpus_but_never_grants_model_authority(monkeypatch):
    monkeypatch.setattr(maintenance.CFBDClient, "from_environment", lambda: FakeCFBD())

    payload = maintenance.run_ncaaf_prop_history_maintenance(
        FakeDB(), seasons=[2024, 2025], weeks=[1, 2]
    )

    assert payload["status"] == "CORPUS_UPDATED"
    assert payload["source_snapshot_n"] == 4
    assert payload["source_snapshot_persisted_n"] == 4
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
