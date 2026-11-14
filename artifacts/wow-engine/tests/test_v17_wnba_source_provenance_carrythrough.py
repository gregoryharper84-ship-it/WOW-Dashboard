"""WNBA challenger inputs preserve actual original source rights and digests."""
from datetime import date
from types import SimpleNamespace
import sys

from v17 import team_state_challenger_maintenance as subject


class _Query:
    def __init__(self, rows):
        self.rows = rows
        self.data = []

    def select(self, *_):
        return self

    def order(self, *_):
        return self

    def range(self, first, last):
        self.data = self.rows[first:last + 1]
        return self

    def execute(self):
        return self


class _DB:
    def __init__(self, rows):
        self.rows = rows
        self.queries = []

    def table(self, name):
        self.queries.append(name)
        assert name == "wow_wnba_training_games"
        return _Query(self.rows)


def _game():
    return SimpleNamespace(
        game_id="espn-401918307", game_date=date(2026, 10, 21),
        season=2026, home_team_id="17", away_team_id="18",
        home_score=88, away_score=85,
    )


def _setup(monkeypatch):
    fake = SimpleNamespace(load_games=lambda client, sport: [_game()])
    monkeypatch.setitem(sys.modules, "basketball_specialist_pipeline", fake)


def test_wnba_challenger_preserves_actual_licensed_source_and_hash(monkeypatch):
    _setup(monkeypatch)
    digest = "a" * 64
    db = _DB([{
        "game_id": "espn-401918307",
        "source_provider": "SPORTSDATAVERSE_ESPN",
        "source_endpoint": "https://source.example/wnba",
        "source_retrieved_at": "2026-10-22T12:00:00Z",
        "source_payload_sha256": digest,
    }])
    rows = subject._basketball_events(db, "WNBA")
    assert len(rows) == 1
    assert db.queries == ["wow_wnba_training_games"]
    manifest = rows[0]["source_manifest"]
    assert manifest["source"] == "SPORTSDATAVERSE_ESPN"
    assert manifest["source_payload_sha256"] == digest
    assert manifest["game_id"] == "espn-401918307"


def test_missing_wnba_source_provenance_is_not_silently_approved(monkeypatch):
    _setup(monkeypatch)
    row = subject._basketball_events(_DB([]), "WNBA")[0]
    assert row["source_manifest"]["source"] == "wow_wnba_training_games"


def test_invalid_wnba_digest_remains_unverified(monkeypatch):
    _setup(monkeypatch)
    db = _DB([{
        "game_id": "espn-401918307", "source_provider": "SPORTSDATAVERSE_ESPN",
        "source_payload_sha256": "not-a-sha256",
    }])
    row = subject._basketball_events(db, "WNBA")[0]
    assert row["source_manifest"]["source"] == "wow_wnba_training_games"


def test_nba_training_path_remains_unchanged_without_extra_database_query(monkeypatch):
    _setup(monkeypatch)
    db = _DB([])
    row = subject._basketball_events(db, "NBA")[0]
    assert row["source_manifest"] == {
        "source": "wow_nba_training_games", "game_id": "espn-401918307",
    }
    assert db.queries == []
