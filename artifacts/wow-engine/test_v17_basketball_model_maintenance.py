from datetime import datetime, timezone
from types import SimpleNamespace

from fastapi import Depends, FastAPI
from fastapi.testclient import TestClient

import v17.basketball_model_maintenance as maintenance


def test_default_seasons_are_four_year_window():
    assert maintenance.default_seasons(datetime(2026, 9, 15, tzinfo=timezone.utc)) == (2023, 2024, 2025, 2026)


def test_maintenance_updates_shadow_evidence_without_promotion(monkeypatch):
    seen = []

    def fake_hydrate(sport, seasons, client=None):
        seen.append(("hydrate", sport, tuple(seasons), client))
        return {"sport": sport, "settled_rows": 100, "can_execute": False}

    def fake_replay(sport, client=None):
        seen.append(("replay", sport, client))
        return {
            "sport": sport,
            "persisted_status": "SHADOW",
            "promotion_attempted": False,
            "can_execute": False,
        }

    monkeypatch.setattr(maintenance, "hydrate", fake_hydrate)
    monkeypatch.setattr(maintenance, "run_training_replay", fake_replay)
    db = object()
    result = maintenance.run_basketball_model_maintenance(db, seasons=(2025, 2026))

    assert result["status"] == "COMPLETE"
    assert result["rows_updated"] == 2
    assert result["rows_fresh_acquisition_degraded"] == 0
    assert result["automatic_certification"] is False
    assert result["automatic_promotion"] is False
    assert result["probability_publishable"] is False
    assert result["can_execute"] is False
    assert all(row["status"] == "SHADOW_EVIDENCE_UPDATED" for row in result["rows"])
    assert all(row["hydration_status"] == "FRESH_ACQUISITION_UPDATED" for row in result["rows"])
    assert all(row["promotion_attempted"] is False for row in result["rows"])
    assert seen[0][:3] == ("hydrate", "NBA", (2025, 2026))


def test_missing_provider_credential_replays_persisted_corpus(monkeypatch):
    def fail_hydration(*_args, **_kwargs):
        raise maintenance.BasketballHydrationError("BALLDONTLIE_API_KEY unavailable")

    replay_calls = []

    def replay(sport, client=None):
        replay_calls.append((sport, client))
        return {
            "sport": sport,
            "persisted_status": "SHADOW",
            "provenance_preflight": {"provenance_complete": True},
            "freshness_preflight": {"freshness_status": "PASS"},
            "promotion_attempted": False,
            "can_execute": False,
        }

    monkeypatch.setattr(maintenance, "hydrate", fail_hydration)
    monkeypatch.setattr(maintenance, "run_training_replay", replay)
    db = object()
    result = maintenance.run_basketball_model_maintenance(db, sports=("NBA",), seasons=(2026,))
    row = result["rows"][0]

    assert result["status"] == "COMPLETE"
    assert result["rows_updated"] == 1
    assert result["rows_fresh_acquisition_degraded"] == 1
    assert row["status"] == "SHADOW_EVIDENCE_UPDATED"
    assert row["hydration_status"] == "FRESH_ACQUISITION_BLOCKED_USING_PERSISTED_CORPUS"
    assert row["hydration_blocker"]["code"] == "BALLDONTLIE_API_KEY unavailable"
    assert row["hydration_blocker"]["stage"] == "FRESH_SOURCE_HYDRATION"
    assert replay_calls == [("NBA", db)]
    assert row["probability_publishable"] is False
    assert row["can_execute"] is False


def test_hydration_and_persisted_replay_failure_remain_blocked(monkeypatch):
    def fail_hydration(*_args, **_kwargs):
        raise maintenance.BasketballHydrationError("BALLDONTLIE_API_KEY unavailable")

    def fail_replay(*_args, **_kwargs):
        raise RuntimeError("WNBA_TRAINING_CORPUS_EMPTY")

    monkeypatch.setattr(maintenance, "hydrate", fail_hydration)
    monkeypatch.setattr(maintenance, "run_training_replay", fail_replay)
    result = maintenance.run_basketball_model_maintenance(object(), sports=("WNBA",), seasons=(2026,))
    row = result["rows"][0]

    assert result["status"] == "BLOCKED"
    assert row["code"] == "WNBA_TRAINING_CORPUS_EMPTY"
    assert row["hydration_blocker"]["code"] == "BALLDONTLIE_API_KEY unavailable"
    assert row["probability_publishable"] is False
    assert row["can_execute"] is False


def test_successful_noop_hydration_receipt_survives_stale_replay_block(monkeypatch):
    hydration_receipt = {
        "sport": "NBA",
        "settled_rows": 0,
        "latest_game_date_before": "2023-04-02",
        "latest_game_date_after": "2023-04-02",
        "source_by_season": {"2026": "BALLDONTLIE"},
        "can_execute": False,
    }

    monkeypatch.setattr(maintenance, "hydrate", lambda *_args, **_kwargs: hydration_receipt)
    monkeypatch.setattr(
        maintenance,
        "run_training_replay",
        lambda *_args, **_kwargs: (_ for _ in ()).throw(
            RuntimeError("NBA_TRAINING_CORPUS_STALE latest=2023-04-02 age_days=1270 max_age_days=400")
        ),
    )

    result = maintenance.run_basketball_model_maintenance(
        object(), sports=("NBA",), seasons=(2026,)
    )
    row = result["rows"][0]

    assert result["status"] == "BLOCKED"
    assert row["code"].startswith("NBA_TRAINING_CORPUS_STALE")
    assert row["hydration"] == hydration_receipt
    assert row.get("hydration_blocker") is None
    assert row["probability_publishable"] is False
    assert row["can_execute"] is False


def test_maintenance_route_is_internal_and_auth_wrapped(monkeypatch):
    app = FastAPI()
    monkeypatch.setattr(maintenance, "scout_route_auth_dependency", lambda dep: dep)
    monkeypatch.setattr(
        maintenance,
        "run_basketball_model_maintenance",
        lambda db: {
            "status": "BLOCKED",
            "rows": [],
            "automatic_certification": False,
            "automatic_promotion": False,
            "probability_publishable": False,
            "can_execute": False,
        },
    )
    maintenance.install_basketball_model_maintenance_route(
        app,
        auth_dependency=Depends(lambda: None),
        db_client_fn=lambda: SimpleNamespace(),
    )
    client = TestClient(app)
    response = client.post("/internal/v17/basketball-model-maintenance")
    assert response.status_code == 200
    assert response.json()["can_execute"] is False
    assert sum(route.path == "/internal/v17/basketball-model-maintenance" for route in app.routes) == 1

    maintenance.install_basketball_model_maintenance_route(
        app,
        auth_dependency=Depends(lambda: None),
        db_client_fn=lambda: SimpleNamespace(),
    )
    assert sum(route.path == "/internal/v17/basketball-model-maintenance" for route in app.routes) == 1
