from types import SimpleNamespace

import team_event_request_runtime as request_runtime
from v17.multiscout_auto_advance import build_dispatch


def _nfl_handoff():
    return {
        "schema_version": "wow.v17.nightly_multiscout.v1",
        "status": "DISCOVERY_COMPLETE",
        "generated_at": "2026-09-13T21:20:00Z",
        "run_id": "wow-scout-nfl-bridge-test",
        "research_run_id": "wow-scout-nfl-bridge-test",
        "model_handoff_ready": True,
        "model_handoff": {
            "prop_candidates": [],
            "team_event_candidates": [{
                "official_event_id": "2026_01_DAL_NYG",
                "sport_key": "americanfootball_nfl",
                "commence_time": "2026-09-14T00:20:00Z",
                "home_team": "NYG",
                "away_team": "DAL",
                "route": "LLP_TEAM_BETTING_ENGINE",
                "discovery_status": "DISCOVERY_ONLY",
                "research_ceiling": "RESEARCH_INTEREST",
                "market_evidence": [],
            }],
        },
        "governance": {"can_execute": False},
    }


def test_nfl_scout_identity_survives_auto_advance_mapping():
    dispatch = build_dispatch(_nfl_handoff())
    rows = dispatch["team_event_batches"][0]["rows"]
    assert len(rows) == 2
    assert {row["objective_lane"] for row in rows} == {
        "OUTRIGHT_WIN_PROBABILITY",
        "UPSET_PROBABILITY",
    }
    for row in rows:
        assert row["sport"] == "NFL"
        assert row["league"] == "NFL"
        assert row["event_key"] == "NFL:2026_01_DAL_NYG"
        assert row["event_start_time_utc"] == "2026-09-14T00:20:00Z"
        assert row["home_team"] == "NYG"
        assert row["away_team"] == "DAL"
        assert row["price_required_for_objective"] is (row["objective_lane"] == "UPSET_PROBABILITY")


def test_nfl_objective_dispatch_calls_governed_publication(monkeypatch):
    calls = []

    monkeypatch.setattr(request_runtime, "install_nfl_hydration_startup", lambda app, *, db_client_fn: None)
    monkeypatch.setattr(request_runtime, "install_nfl_model_startup", lambda app, *, db_client_fn: None)
    monkeypatch.setattr(request_runtime, "install_nfl_team_event_publication", lambda module: True)
    monkeypatch.setattr(request_runtime, "scout_route_auth_dependency", lambda dependency: dependency)
    monkeypatch.setenv("WOW_V17_ACTIVE", "1")

    def fake_score(req, *, event_api, canonical_hydration_required=False):
        calls.append((req, event_api, canonical_hydration_required))
        return {
            "code": "GOVERNED_PROBABILITY_PUBLISHED",
            "terminal_label": "FINAL_APPROVED",
            "selected_participant": "DAL",
            "calibrated_selection_probability": 0.612,
            "rank_calibrated_lower_bound": 0.556,
            "ranked_probability": 0.556,
            "calibrated_home_upper_bound": 0.444,
            "calibrated_away_upper_bound": 0.668,
            "llp_probability_audit_result": "PASS_PROBABILITY_AUDIT",
            "llp_event_decision": "PASS",
            "score_snapshot_id": "00000000-0000-0000-0000-000000000001",
            "event_prediction_id": "00000000-0000-0000-0000-000000000002",
            "probability_publishable": True,
            "can_execute": False,
        }

    monkeypatch.setattr(request_runtime.v17_team_event_base, "score_team_event_request", fake_score)

    class FakeApp:
        def __init__(self):
            self.router = SimpleNamespace(routes=[])
            self.handler = None

        def post(self, path, **kwargs):
            def decorator(fn):
                self.router.routes.append(SimpleNamespace(path=path))
                self.handler = fn
                return fn
            return decorator

    app = FakeApp()
    event_api = object()
    request_runtime.install_team_event_request_routes(
        app,
        auth_dependency=object(),
        db_client_fn=lambda: object(),
        event_api=event_api,
    )

    batch = request_runtime.TeamEventRequestBatch(rows=[{
        "research_run_id": "wow-scout-nfl-bridge-test",
        "objective_lane": "OUTRIGHT_WIN_PROBABILITY",
        "sport": "NFL",
        "league": "NFL",
        "event_key": "NFL:2026_01_DAL_NYG",
        "event_state": "PREGAME",
        "event_date": "2026-09-13",
        "timezone": "America/Chicago",
        "price_required_for_objective": False,
        "event_start_time_utc": "2026-09-14T00:20:00Z",
        "home_team": "NYG",
        "away_team": "DAL",
    }])
    response = app.handler(batch)

    assert response["run_status"] == "COMPLETE"
    assert response["rows_completed"] == 1
    assert response["can_execute"] is False
    row = response["rows"][0]
    assert row["terminal_status"] == "COMPLETED"
    assert row["governed_publication_code"] == "GOVERNED_PROBABILITY_PUBLISHED"
    assert row["terminal_label"] == "FINAL_APPROVED"
    assert row["selected_team"] == "DAL"
    assert row["calibrated_probability"] == 0.612
    assert row["calibrated_lower_bound"] == 0.556
    assert row["calibrated_upper_bound"] == 0.668
    assert row["probability_publishable"] is True
    assert row["can_execute"] is False

    assert len(calls) == 1
    req, seen_event_api, canonical = calls[0]
    assert seen_event_api is event_api
    assert canonical is True
    assert req.requester_host_identity == "WOW_BETTING_ENGINE"
    assert req.sport == "NFL"
    assert req.league == "NFL"
    assert req.official_event_id == "2026_01_DAL_NYG"
    assert req.home_team == "NYG"
    assert req.away_team == "DAL"
    assert req.decision_intent == "WINNER"
    assert req.source_snapshot_id == "SCOUT_CANONICALIZATION_PENDING"
    assert req.sport_specific_evidence == {}


def test_nfl_governance_hold_does_not_publish():
    row = request_runtime.TeamEventRequestRow(
        research_run_id="hold-test",
        objective_lane="OUTRIGHT_WIN_PROBABILITY",
        sport="NFL",
        league="NFL",
        event_key="NFL:2026_01_DAL_NYG",
        event_state="PREGAME",
        event_date="2026-09-13",
        timezone="America/Chicago",
        price_required_for_objective=False,
        event_start_time_utc="2026-09-14T00:20:00Z",
        home_team="NYG",
        away_team="DAL",
    )
    result = request_runtime._completed_nfl(row, {
        "code": "LLP_EVENT_GOVERNANCE_NOT_PROVEN",
        "terminal_label": "MODEL_QUALIFIED_HOLD",
        "blockers": ["NFL_GOVERNED_PUBLICATION_NOT_PROVEN"],
        "probability_publishable": False,
        "can_execute": False,
    })
    assert result["terminal_status"] == "HELD"
    assert result["probability_publishable"] is False
    assert result["can_execute"] is False
