from types import SimpleNamespace

from wnba_history_ingestion import normalize_raw_game_log_row, persist_raw_game_logs


def _row(**overrides):
    row = {
        "PLAYER_ID": 123,
        "PLAYER_NAME": "Player A",
        "TEAM_ABBREVIATION": "DAL",
        "GAME_ID": "2026050101",
        "GAME_DATE": "MAY 01, 2026",
        "MATCHUP": "DAL vs. NYL",
        "MIN": 31,
        "PTS": 18,
        "REB": 7,
        "AST": 5,
        "FG3M": 2,
    }
    row.update(overrides)
    return row


class _Mutation:
    def __init__(self, sink, payload, conflict):
        self.sink = sink
        self.payload = payload
        self.conflict = conflict

    def execute(self):
        self.sink.append((self.payload, self.conflict))
        return SimpleNamespace(data=[self.payload])


class _Table:
    def __init__(self, sink):
        self.sink = sink

    def upsert(self, payload, on_conflict=None):
        return _Mutation(self.sink, payload, on_conflict)


class _Client:
    def __init__(self):
        self.rows = []

    def table(self, name):
        assert name == "wow_wnba_player_game_logs"
        return _Table(self.rows)


def test_raw_row_never_infers_starter_or_role_from_minutes():
    payload = normalize_raw_game_log_row(
        _row(MIN=40),
        season=2026,
        season_type="Regular Season",
        source_retrieved_at="2026-05-02T00:00:00+00:00",
    )
    assert "starter" not in payload
    assert "role_status" not in payload
    assert payload["role_evidence_status"] == "UNRESOLVED"
    assert payload["training_materialization_status"] == "BLOCKED_ROLE_EVIDENCE"
    assert payload["can_execute"] is False


def test_persistence_uses_deterministic_source_identity_conflict_key():
    client = _Client()
    result = persist_raw_game_logs(
        client,
        [_row()],
        season=2026,
        season_type="Regular Season",
        source_retrieved_at="2026-05-02T00:00:00+00:00",
    )
    assert result.persisted_n == 1
    payload, conflict = client.rows[0]
    assert conflict == "season,season_type,game_id,player_id,source_identity"
    assert payload["source_identity"] == "WNBA_STATS_LEAGUE_GAME_LOG"
    assert len(payload["source_payload_hash"]) == 64
    assert result.runtime_model_status == "MODEL_UNAVAILABLE"
    assert result.probability_publishable is False
    assert result.can_execute is False


def test_any_bad_official_row_prevents_partial_persistence():
    client = _Client()
    result = persist_raw_game_logs(
        client,
        [_row(), _row(GAME_ID="bad2", PLAYER_NAME="")],
        season=2026,
        season_type="Regular Season",
        source_retrieved_at="2026-05-02T00:00:00+00:00",
    )
    assert result.fetched_n == 2
    assert result.accepted_n == 1
    assert result.rejected_n == 1
    assert result.persisted_n == 0
    assert client.rows == []
    assert "WNBA_HISTORY_REQUIRED_FIELD_MISSING" in result.rejected_codes
