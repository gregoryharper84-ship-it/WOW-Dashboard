from datetime import datetime, timezone
from pathlib import Path
from types import SimpleNamespace

from fastapi import Depends, FastAPI
from fastapi.testclient import TestClient

import v17.basketball_model_maintenance as maintenance


def test_default_seasons_are_four_year_window():
    assert maintenance.default_seasons(datetime(2026, 9, 15, tzinfo=timezone.utc)) == (2023, 2024, 2025, 2026)


def test_maintenance_updates_shadow_evidence_without_promotion(monkeypatch):
    seen = []

    def fake_hydrate(sport, seasons, client=None, *, now=None):
        seen.append(("hydrate", sport, tuple(seasons), client, now))
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
    assert seen[0][4] is not None and seen[0][4].utcoffset() is not None


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


def test_partial_hydration_write_never_trains_a_half_persisted_corpus(monkeypatch):
    def partial_hydrate(*_args, **_kwargs):
        raise maintenance.BasketballHydrationPartialWriteError(
            "BASKETBALL_HYDRATION_PARTIAL_WRITE_REPLAY_FORBIDDEN"
        )

    def forbidden_replay(*_args, **_kwargs):
        raise AssertionError("partial persisted corpus must never reach replay")

    monkeypatch.setattr(maintenance, "hydrate", partial_hydrate)
    monkeypatch.setattr(maintenance, "run_training_replay", forbidden_replay)
    result = maintenance.run_basketball_model_maintenance(
        object(), sports=("NBA",), seasons=(2025, 2026)
    )
    row = result["rows"][0]
    assert result["status"] == "BLOCKED"
    assert result["rows_updated"] == 0
    assert row["status"] == "BLOCKED"
    assert row["code"] == "BASKETBALL_HYDRATION_PARTIAL_WRITE_REPLAY_FORBIDDEN"
    assert row["hydration_blocker"]["requires_corpus_reconciliation"] is True
    assert row["hydration_blocker"]["recoverable"] is False
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



def test_live_basketball_maintenance_never_runs_on_push():
    repo_root = Path(__file__).resolve().parents[2]
    workflow = (
        repo_root / ".github" / "workflows" / "wow-v17-basketball-model-maintenance.yml"
    ).read_text(encoding="utf-8")

    assert "if: github.event_name == 'schedule' || github.event_name == 'workflow_dispatch'" in workflow
    assert "(github.event_name == 'push' && github.ref == 'refs/heads/main')" not in workflow
    assert 'WOW_CAN_EXECUTE: "false"' in workflow
    assert 'WOW_DRY_RUN_ONLY: "true"' in workflow


def test_replay_success_cannot_disguise_stale_basketball_source(monkeypatch):
    monkeypatch.setattr(
        maintenance, "hydrate",
        lambda *_args, **_kwargs: {
            "sport": "NBA", "settled_rows": 0,
            "fresh_acquisition_new_settled_games": 0,
            "latest_game_date_before": "2026-06-13",
            "latest_game_date_after": "2026-06-13",
            "recent_supplement_status": {2026: "NO_NEW_SETTLEMENT"},
            "can_execute": False,
        },
    )
    monkeypatch.setattr(
        maintenance, "run_training_replay",
        lambda *_args, **_kwargs: {
            "sport": "NBA", "persisted_status": "SHADOW",
            "promotion_attempted": False, "can_execute": False,
        },
    )
    result = maintenance.run_basketball_model_maintenance(object(), sports=("NBA",), seasons=(2026,))
    assert result["status"] == "COMPLETE"  # Existing corpus was replayed, not hydrated
    assert result["rows_updated"] == 1
    assert result["rows_no_new_settlements"] == 1
    assert result["rows_fresh_acquisition_degraded"] == 0
    assert result["rows"][0]["hydration_status"] == "FRESH_ACQUISITION_NO_NEW_SETTLEMENTS_USING_PERSISTED_CORPUS"
    assert result["rows"][0]["status"] == "SHADOW_EVIDENCE_UPDATED"
    assert result["automatic_promotion"] is False
    assert result["probability_publishable"] is False


def test_provider_blocked_after_successful_csv_is_counted_as_degraded(monkeypatch):
    monkeypatch.setattr(
        maintenance, "hydrate",
        lambda *_args, **_kwargs: {
            "sport": "NBA",
            "settled_rows": 0, "fresh_acquisition_new_settled_games": 0,
            "recent_supplement_status": {2026: "SOURCE_BLOCKED:ESPN_RECENT_SOURCE_HTTP_403"},
            "can_execute": False,
        },
    )
    monkeypatch.setattr(
        maintenance, "run_training_replay",
        lambda *_args, **_kwargs: {"persisted_status": "SHADOW", "can_execute": False},
    )
    out = maintenance.run_basketball_model_maintenance(object(), sports=("NBA",), seasons=(2026,))
    assert out["status"] == "COMPLETE"  # Replay result independent of provider network
    assert out["rows_fresh_acquisition_degraded"] == 1
    assert out["rows"][0]["hydration_status"] == "FRESH_ACQUISITION_PARTIAL_SOURCE_BLOCKED_USING_PERSISTED_CORPUS"
    assert out["rows_no_new_settlements"] == 0
    assert out["probability_publishable"] is False
    assert out["can_execute"] is False


def test_missing_fresh_acquisition_counter_is_unverified_not_updated(monkeypatch):
    monkeypatch.setattr(
        maintenance, "hydrate",
        lambda *_args, **_kwargs: {"sport": "NBA", "can_execute": False},
    )
    monkeypatch.setattr(
        maintenance, "run_training_replay",
        lambda *_args, **_kwargs: {"persisted_status": "SHADOW", "can_execute": False},
    )
    out = maintenance.run_basketball_model_maintenance(object(), sports=("NBA",), seasons=(2026,))
    assert out["rows"][0]["hydration_status"] == "FRESH_ACQUISITION_NOT_VERIFIED"
    assert out["rows_fresh_acquisition_unverified"] == 1
    assert out["automatic_certification"] is False


def test_opening_ramp_uses_trusted_2027_espn_nba_release_but_2026_wnba(monkeypatch):
    calls = []
    def fake_hydrate(sport, seasons, client=None, *, now=None):
        calls.append((sport, tuple(seasons), now))
        return {
            "sport": sport, "settled_rows": 1,
            "fresh_acquisition_new_settled_games": 1,
            "can_execute": False,
        }
    monkeypatch.setattr(maintenance, "hydrate", fake_hydrate)
    monkeypatch.setattr(
        maintenance, "run_training_replay",
        lambda sport, client=None: {
            "persisted_status": "SHADOW", "can_execute": False,
        },
    )
    outcome = maintenance.run_basketball_model_maintenance(
        object(), sports=("NBA", "WNBA"),
        now=datetime(2026, 10, 23, 17, tzinfo=timezone.utc),
    )
    expected_clock = datetime(2026, 10, 23, 17, tzinfo=timezone.utc)
    assert calls == [
        ("NBA", (2024, 2025, 2026, 2027), expected_clock),
        ("WNBA", (2023, 2024, 2025, 2026), expected_clock),
    ]
    assert outcome["seasons_by_sport"] == {
        "NBA": [2024, 2025, 2026, 2027],
        "WNBA": [2023, 2024, 2025, 2026],
    }
    assert outcome["rows_updated"] == 2
    assert outcome["automatic_certification"] is False
    assert outcome["automatic_promotion"] is False
    assert outcome["probability_publishable"] is False
    assert outcome["can_execute"] is False


def test_nba_release_year_rotates_only_after_october_opening():
    assert maintenance.default_seasons(
        datetime(2026, 9, 30, tzinfo=timezone.utc), sport="NBA",
    ) == (2023, 2024, 2025, 2026)
    assert maintenance.default_seasons(
        datetime(2026, 10, 1, tzinfo=timezone.utc), sport="NBA",
    ) == (2024, 2025, 2026, 2027)
    assert maintenance.default_seasons(
        datetime(2026, 10, 23, tzinfo=timezone.utc), sport="WNBA",
    ) == (2023, 2024, 2025, 2026)
    assert maintenance.default_seasons(
        datetime(2027, 1, 1, tzinfo=timezone.utc), sport="NBA",
    ) == (2024, 2025, 2026, 2027)

def test_scheduled_nba_november_rejects_june_only_corpus_before_replay(monkeypatch):
    # Actual 2026-11-03 scheduler reported COMPLETE despite latest NBA
    # persisted settled game still being 2026-06-13. This must be a HOLD.
    monkeypatch.setattr(maintenance, "hydrate", lambda *_args, **_kwargs: {
        "sport": "NBA", "settled_rows": 8919,
        "latest_game_date_before": "2026-06-13",
        "latest_game_date_after": "2026-06-13",
        "fresh_acquisition_new_settled_games": 0,
        "can_execute": False,
    })
    monkeypatch.setattr(
        maintenance, "run_training_replay",
        lambda *_args, **_kwargs: (_ for _ in ()).throw(
            AssertionError("Never train on a stale current-season corpus")
        ),
    )
    result = maintenance.run_basketball_model_maintenance(
        object(), sports=("NBA",),
        now=datetime(2026, 11, 4, 12, tzinfo=timezone.utc),
    )
    assert result["status"] == "BLOCKED"
    assert result["rows_updated"] == 0
    assert result["rows_blocked"] == 1
    assert result["rows"][0]["code"] == "NBA_CURRENT_SEASON_SETTLED_DATA_MISSING"
    assert result["rows"][0]["hydration_blocker"]["required_season_start"] == "2026-10-01"
    assert result["rows"][0]["hydration_blocker"]["latest_persisted_game_date"] == "2026-06-13"
    assert result["can_execute"] is False
    assert result["probability_publishable"] is False


def test_scheduled_nba_replay_allowed_when_current_season_is_persisted(monkeypatch):
    called = []
    monkeypatch.setattr(maintenance, "hydrate", lambda *_args, **_kwargs: {
        "sport": "NBA", "settled_rows": 0,
        "latest_game_date_before": "2026-11-03",
        "latest_game_date_after": "2026-11-03",
        "fresh_acquisition_new_settled_games": 0,
        "can_execute": False,
    })
    monkeypatch.setattr(
        maintenance, "run_training_replay",
        lambda sport, client=None: (
            called.append(sport) or {
                "sport": sport, "persisted_status": "SHADOW",
                "promotion_attempted": False, "can_execute": False,
            }
        ),
    )
    result = maintenance.run_basketball_model_maintenance(
        object(), sports=("NBA",),
        now=datetime(2026, 11, 4, 12, tzinfo=timezone.utc),
    )
    assert result["status"] == "COMPLETE"
    assert result["rows"][0]["hydration_status"] == "FRESH_ACQUISITION_NO_NEW_SETTLEMENTS_USING_PERSISTED_CORPUS"
    assert called == ["NBA"]
    assert result["automatic_certification"] is False
    assert result["can_execute"] is False


def test_nba_scheduled_unverified_or_future_date_fails_closed(monkeypatch):
    for invalid in (None, "not-a-date", "2026-11-08"):
        monkeypatch.setattr(maintenance, "hydrate", lambda *_args, **_kwargs: {
            "sport": "NBA",
            "latest_game_date_after": invalid,
            "fresh_acquisition_new_settled_games": 0,
            "can_execute": False,
        })
        monkeypatch.setattr(
            maintenance, "run_training_replay",
            lambda *_args, **_kwargs: (_ for _ in ()).throw(
                AssertionError("unverified corpus cannot be replayed")
            ),
        )
        result = maintenance.run_basketball_model_maintenance(
            object(), sports=("NBA",),
            now=datetime(2026, 11, 4, tzinfo=timezone.utc),
        )
        assert result["rows"][0]["code"] == "NBA_CURRENT_SEASON_SETTLED_DATA_MISSING"
        assert result["rows_updated"] == 0
        assert result["can_execute"] is False


def test_nba_current_season_gap_preserves_wnba_and_offseason(monkeypatch):
    assert maintenance._nba_current_season_data_gap(
        {"latest_game_date_after": "2026-06-13"},
        datetime(2026, 10, 17, tzinfo=timezone.utc),
    ) is None
    assert maintenance._nba_current_season_data_gap(
        {"latest_game_date_after": "2026-12-31"},
        datetime(2027, 1, 3, tzinfo=timezone.utc),
    ) is None
    assert maintenance._nba_current_season_data_gap(
        {"latest_game_date_after": "2026-06-13"},
        datetime(2027, 1, 3, tzinfo=timezone.utc),
    )["code"] == "NBA_CURRENT_SEASON_SETTLED_DATA_MISSING"
