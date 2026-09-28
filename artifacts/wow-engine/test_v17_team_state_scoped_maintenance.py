from v17 import team_state_challenger_maintenance as maintenance
from v17 import team_state_scoped_maintenance as scoped


class DummyClient:
    pass


def test_supported_scopes_include_team_sports_and_each_soccer_competition():
    scopes = set(scoped.supported_scopes())
    assert {"NFL", "NFL_EVENT_V2", "MLB", "NBA", "WNBA", "NCAAF", "NCAAB"} <= scopes
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


def test_nfl_event_v2_scope_uses_research_only_context_challenger(monkeypatch):
    from v17 import nfl_event_context_challenger as challenger

    monkeypatch.setattr(maintenance, "_nfl_events", lambda client: [{"event_id": "fixture"}])
    # team_state_scoped_maintenance imported the function directly.
    monkeypatch.setattr(scoped, "_nfl_events", lambda client: [{"event_id": "fixture"}])

    calls = []

    def fake_train(client, *, events, training_code_sha):
        calls.append((client, events, training_code_sha))
        return {
            "sport": "NFL",
            "model_family": "NFL_EVENT_CONTEXT_LOGIT_V2",
            "probability_publishable": False,
            "automatic_certification": False,
            "automatic_promotion": False,
            "can_execute": False,
        }

    monkeypatch.setattr(challenger, "train_and_persist", fake_train)
    result = scoped.run_team_state_scope(
        DummyClient(), scope="nfl_event_v2", training_code_sha="abcdef123456"
    )

    assert len(calls) == 1
    assert calls[0][1] == [{"event_id": "fixture"}]
    assert result["scope"] == "NFL_EVENT_V2"
    assert result["rows"][0]["model_family"] == "NFL_EVENT_CONTEXT_LOGIT_V2"
    assert result["rows"][0]["probability_publishable"] is False
    assert result["rows"][0]["can_execute"] is False


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