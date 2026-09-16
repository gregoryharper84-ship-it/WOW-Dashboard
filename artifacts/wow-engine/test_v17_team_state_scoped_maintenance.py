from v17 import team_state_scoped_maintenance as scoped


class DummyClient:
    pass


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
