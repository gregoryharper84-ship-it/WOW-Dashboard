from types import SimpleNamespace

from ncaaf_cfbd_hydrator import SourceSnapshot
from ncaaf_training_materializer import materialize_training_games


def _game(**overrides):
    row = {
        "id": 401,
        "completed": True,
        "homePoints": 21,
        "awayPoints": 14,
        "startDate": "2026-09-20T17:00:00Z",
        "homeTeam": "Texas",
        "awayTeam": "Oklahoma",
        "week": 4,
        "season": 2026,
        "seasonType": "regular",
        "neutralSite": False,
    }
    row.update(overrides)
    return row


def _snapshot(rows, *, retrieved="2026-10-01T00:00:00+00:00"):
    return SourceSnapshot(
        provider="CFBD",
        endpoint="/games",
        season=2026,
        week=None,
        requested_at=retrieved,
        retrieved_at=retrieved,
        request_params={"year": 2026},
        response_rows=rows,
        response_row_count=len(rows),
        payload_sha256="a" * 64,
        acquisition_status="AVAILABLE",
        blocker_codes=[],
    )


class _DB:
    def __init__(self, rows=None):
        self.rows = [dict(row) for row in (rows or [])]
        self.inserted = []

    def table(self, name):
        assert name == "wow_ncaaf_training_games"
        return _Query(self)


class _Query:
    def __init__(self, db):
        self.db = db
        self.ids = []
        self.pending = None
        self.mode = None

    def select(self, _columns):
        self.mode = "select"
        return self

    def in_(self, _field, values):
        self.ids = list(values)
        return self

    def insert(self, rows):
        self.mode = "insert"
        self.pending = list(rows)
        return self

    def execute(self):
        if self.mode == "select":
            return SimpleNamespace(data=[
                row for row in self.db.rows if row["official_event_id"] in self.ids
            ])
        self.db.inserted.extend(self.pending)
        self.db.rows.extend(self.pending)
        return SimpleNamespace(data=list(self.pending))


def test_settlement_before_kickoff_is_rejected():
    result = materialize_training_games(
        _DB(),
        [_snapshot([_game()], retrieved="2026-09-01T00:00:00+00:00")],
    )
    assert result.persisted_rows == 0
    assert "NCAAF_SETTLEMENT_EVIDENCE_NOT_POST_START" in result.blocker_codes
    assert result.can_execute is False


def test_conflicting_team_identity_is_not_persisted():
    db = _DB()
    result = materialize_training_games(db, [
        _snapshot([_game()]),
        _snapshot([_game(awayTeam="Texas Tech", awayPoints=10)]),
    ])
    assert result.persisted_rows == 0
    assert db.inserted == []
    assert "NCAAF_TRAINING_GAME_IDENTITY_CONFLICT" in result.blocker_codes


def test_existing_settlement_is_immutable():
    stored = {
        "official_event_id": "401",
        "season": 2026,
        "week": 4,
        "season_type": "regular",
        "event_start_time": "2026-09-20T17:00:00+00:00",
        "neutral_site": False,
        "home_team": "Texas",
        "away_team": "Oklahoma",
        "home_points": 28,
        "away_points": 14,
        "home_won": True,
        "result_source": "CFBD:/games",
    }
    db = _DB([stored])
    result = materialize_training_games(db, [_snapshot([_game()])])
    assert result.persisted_rows == 0
    assert db.inserted == []
    assert "NCAAF_TRAINING_GAME_IDENTITY_CONFLICT" in result.blocker_codes
    assert db.rows[0]["home_points"] == 28


def test_matching_existing_settlement_is_not_rewritten():
    stored = {
        "official_event_id": "401",
        "season": 2026,
        "week": 4,
        "season_type": "regular",
        "event_start_time": "2026-09-20T17:00:00+00:00",
        "neutral_site": False,
        "home_team": "Texas",
        "away_team": "Oklahoma",
        "home_points": 21,
        "away_points": 14,
        "home_won": True,
        "result_source": "CFBD:/games",
    }
    db = _DB([stored])
    result = materialize_training_games(db, [_snapshot([_game()])])
    assert result.persisted_rows == 0
    assert db.inserted == []
    assert "NCAAF_TRAINING_GAME_IDENTITY_CONFLICT" not in result.blocker_codes
    assert db.rows == [stored]


def test_new_settled_identity_inserts_once():
    db = _DB()
    result = materialize_training_games(db, [_snapshot([_game(), _game()])])
    assert result.persisted_rows == 1
    assert len(db.inserted) == 1
    assert db.inserted[0]["official_event_id"] == "401"
    assert db.inserted[0]["home_won"] is True
    assert db.inserted[0]["can_execute"] is False
