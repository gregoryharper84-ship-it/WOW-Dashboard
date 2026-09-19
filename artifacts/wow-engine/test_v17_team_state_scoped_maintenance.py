from v17 import team_state_challenger_maintenance as maintenance
from v17 import team_state_scoped_maintenance as scoped


class DummyClient:
    pass


class ChainClient:
    def table(self, name):
        assert name == "wow_mlb_team_games_multiseason"
        return self

    def select(self, *args, **kwargs):
        return self

    def eq(self, *args, **kwargs):
        return self

    def order(self, *args, **kwargs):
        return self


def test_supported_scopes_include_team_sports_and_each_soccer_competition():
    scopes = set(scoped.supported_scopes())
    assert {"NFL", "MLB", "NBA", "WNBA", "NCAAF", "NCAAB"} <= scopes
    assert {f"SOCCER_{name}" for name in scoped.COMPETITIONS} <= scopes


def test_scoped_maintenance_runs_only_requested_lane(monkeypatch):
    calls = []

    def fake_job(client, scope, code):
        assert isinstance(client, DummyClient)
        assert scope == "NFL"
        assert code == "abcdef123456"

        def run():
            calls.append(scope)
            return {
                "sport": "NFL",
                "model_artifact_version": "candidate-v1",
                "automatic_certification": False,
                "automatic_promotion": False,
                "probability_publishable": False,
                "can_execute": False,
            }

        return run

    monkeypatch.setattr(scoped, "_job", fake_job)
    result = scoped.run_team_state_scope(
        DummyClient(), scope="nfl", training_code_sha="abcdef123456"
    )

    assert calls == ["NFL"]
    assert result["program"] == "LLP_DYNAMIC_TEAM_STATE_CHALLENGER_V1"
    assert result["scope"] == "NFL"
    assert result["candidate_rows_updated"] == 1
    assert result["candidate_rows_blocked"] == 0
    assert result["rows"][0]["status"] == "CANDIDATE_EVIDENCE_UPDATED"
    assert result["probability_publishable"] is False
    assert result["can_execute"] is False


def test_unknown_scope_fails_closed_without_calling_training(monkeypatch):
    monkeypatch.setattr(scoped, "_job", lambda *args, **kwargs: None)
    result = scoped.run_team_state_scope(
        DummyClient(), scope="UNKNOWN", training_code_sha="abcdef123456"
    )

    assert result["status"] == "BLOCKED"
    assert result["candidate_rows_updated"] == 0
    assert result["candidate_rows_blocked"] == 1
    assert result["rows"][0]["code"] == "TEAM_STATE_SCOPE_UNSUPPORTED"
    assert result["probability_publishable"] is False
    assert result["can_execute"] is False


def test_scope_exception_is_typed_blocked_candidate_evidence(monkeypatch):
    def fake_job(*args, **kwargs):
        def run():
            raise RuntimeError("source down")
        return run

    monkeypatch.setattr(scoped, "_job", fake_job)
    result = scoped.run_team_state_scope(
        DummyClient(), scope="NBA", training_code_sha="abcdef123456"
    )

    assert result["status"] == "COMPLETED_NO_CANDIDATE_SURVIVORS"
    assert result["candidate_rows_updated"] == 0
    assert result["candidate_rows_blocked"] == 1
    assert result["rows"][0]["status"] == "BLOCKED"
    assert result["rows"][0]["code"] == "NBA_TEAM_STATE_MAINTENANCE_FAILED"
    assert result["probability_publishable"] is False
    assert result["can_execute"] is False


def test_mlb_persisted_events_pairs_home_and_away(monkeypatch):
    rows = [
        {
            "game_key": "TEX202609170",
            "game_date": "2026-09-17",
            "site_key": "TEX01",
            "team_alignment": 0,
            "team_key": "SEA",
            "opponent_key": "TEX",
            "team_runs": 2,
            "team_runs_allowed": 5,
            "season_year": 2026,
            "season_phase": "R",
        },
        {
            "game_key": "TEX202609170",
            "game_date": "2026-09-17",
            "site_key": "TEX01",
            "team_alignment": 1,
            "team_key": "TEX",
            "opponent_key": "SEA",
            "team_runs": 5,
            "team_runs_allowed": 2,
            "season_year": 2026,
            "season_phase": "R",
        },
    ]
    monkeypatch.setattr(scoped, "_paginate", lambda query: rows)
    events = scoped._mlb_persisted_events(ChainClient())

    assert len(events) == 1
    assert events[0]["event_id"] == "MLB:HIST:TEX202609170"
    assert events[0]["home_team"] == "TEX"
    assert events[0]["away_team"] == "SEA"
    assert events[0]["home_score"] == 5.0
    assert events[0]["away_score"] == 2.0
    assert events[0]["source_manifest"]["source"] == "WOW_MLB_TEAM_GAMES_MULTI_SEASON"


def test_mlb_team_state_events_fetch_only_current_season(monkeypatch):
    historical = [{"event_id": "MLB:HIST:one", "event_start_time": "2025-09-01T12:00:00+00:00"}]
    current = [{"event_id": "MLB:999", "event_start_time": "2026-09-01T12:00:00+00:00"}]
    calls = []
    monkeypatch.setattr(scoped, "_mlb_persisted_events", lambda client: historical)

    def current_fetch(*, seasons):
        calls.append(seasons)
        return current

    monkeypatch.setattr(scoped, "_mlb_official_events", current_fetch)
    rows = scoped._mlb_team_state_events(DummyClient(), current_season=2026)

    assert calls == [(2026,)]
    assert [row["event_id"] for row in rows] == ["MLB:HIST:one", "MLB:999"]


def test_mlb_official_events_deduplicate_repeated_gamepk(monkeypatch):
    game = {
        "gamePk": 123,
        "gameDate": "2026-04-01T19:05:00Z",
        "status": {"abstractGameState": "Final"},
        "teams": {
            "home": {"team": {"id": 1}, "score": 4},
            "away": {"team": {"id": 2}, "score": 3},
        },
    }

    class Response:
        status_code = 200
        content = b"same-source-payload"

        @staticmethod
        def json():
            return {"dates": [{"games": [game]}, {"games": [game]}]}

    monkeypatch.setattr(maintenance.requests, "get", lambda *args, **kwargs: Response())
    rows = maintenance._mlb_official_events(seasons=(2026,))

    assert [row["event_id"] for row in rows] == ["MLB:123"]
    assert rows[0]["home_score"] == 4.0
    assert rows[0]["away_score"] == 3.0
