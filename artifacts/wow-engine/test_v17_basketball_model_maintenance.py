from datetime import datetime, timezone
from pathlib import Path
from types import SimpleNamespace

import pytest

from fastapi import Depends, FastAPI
from fastapi.testclient import TestClient

import v17.basketball_model_maintenance as maintenance


def test_default_maintenance_live_fetch_persists_but_explicit_replay_cannot_borrow_later_snapshot(monkeypatch):
    import basketball_event_hydration_runtime as hydration

    class Clock(datetime):
        calls = 0

        @classmethod
        def now(cls, tz=None):
            cls.calls += 1
            return cls(2026, 11, 6, 8, 0, cls.calls, tzinfo=timezone.utc)

    row = {
        "game_id": "401", "game_date": "2026-11-05",
        "game_date_time": "2026-11-05T23:30:00-05:00",
        "home_id": "7", "away_id": "8", "season": "2027", "season_type": "2",
        "home_score": "111", "away_score": "109",
        "status_type_completed": "true", "status_type_description": "Final",
        "_wow_source_observed_at": "2026-11-06T08:00:02Z",
    }
    monkeypatch.setattr(maintenance, "datetime", Clock)
    monkeypatch.setattr(hydration, "datetime", Clock)
    monkeypatch.setattr(maintenance, "hydrate", hydration.hydrate)
    monkeypatch.setattr(hydration, "fetch_sportsdataverse_games", lambda *_: [row])
    monkeypatch.setattr(hydration, "_latest_persisted_game_date", lambda *_: None)
    batches = []
    monkeypatch.setattr(hydration, "_persist_rows", lambda _db, _sport, rows: (batches.append(list(rows)), len(rows))[1])
    monkeypatch.setattr(maintenance, "run_training_replay", lambda *_args, **_kwargs: {"status": "TEST_ONLY"})
    live = maintenance.run_basketball_model_maintenance(object(), sports=("NBA",), seasons=(2027,))
    assert live["rows"][0]["hydration"]["settled_rows"] == 1
    assert batches[-1][0]["source_retrieved_at"] == row["_wow_source_observed_at"]
    replay = maintenance.run_basketball_model_maintenance(
        object(), sports=("NBA",), seasons=(2027,),
        now=Clock(2026, 11, 6, 8, 0, 1, tzinfo=timezone.utc),
    )
    assert replay["rows"][0]["hydration"]["settled_rows"] == 0
    assert batches[-1] == []
    assert live["probability_publishable"] is False


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
    # The default run lets hydration acquire its own post-fetch cutoff.
    # Explicit historical cutoffs are covered separately and remain fixed.
    assert seen[0][4] is None


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
            "latest_game_date_after": "2026-10-22",
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
    # Acquisition keeps the explicit clock and sport-specific season labels;
    # unbounded persisted replay cannot claim historical shadow evidence.
    assert outcome["rows_updated"] == 0
    assert all(row["code"] == "BASKETBALL_HISTORICAL_REPLAY_CUTOFF_UNSUPPORTED" for row in outcome["rows"])
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
    """A default live refresh still replays the current verified corpus."""
    class LiveClock(datetime):
        @classmethod
        def now(cls, tz=None):
            return cls(2026, 11, 4, 12, tzinfo=timezone.utc)

    monkeypatch.setattr(maintenance, "datetime", LiveClock)
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


def test_nba_current_season_hydration_failure_is_not_successful_replay(monkeypatch):
    def unavailable(*_args, **_kwargs):
        raise maintenance.BasketballHydrationError("NBA_SOURCE_HTTP_503")

    monkeypatch.setattr(maintenance, "hydrate", unavailable)
    monkeypatch.setattr(
        maintenance, "run_training_replay",
        lambda *_args, **_kwargs: (_ for _ in ()).throw(
            AssertionError("cannot replay without current-season settled evidence")
        ),
    )
    result = maintenance.run_basketball_model_maintenance(
        object(), sports=("NBA",),
        now=datetime(2026, 11, 4, tzinfo=timezone.utc),
    )
    assert result["status"] == "BLOCKED"
    assert result["rows_updated"] == 0
    assert result["rows"][0]["code"] == "NBA_CURRENT_SEASON_SETTLED_DATA_MISSING"
    assert result["rows"][0]["hydration_blocker"]["latest_persisted_game_date"] is None
    upstream = result["rows"][0]["hydration_blocker"]["upstream_hydration_blocker"]
    assert upstream["code"] == "NBA_SOURCE_HTTP_503"
    assert upstream["error_type"] == "BasketballHydrationError"
    assert upstream["stage"] == "FRESH_SOURCE_HYDRATION"
    assert upstream["can_execute"] is False
    assert result["rows"][0]["probability_publishable"] is False
    assert result["can_execute"] is False


def test_nba_current_season_gap_preserves_wnba_and_offseason(monkeypatch):
    assert maintenance._nba_current_season_data_gap(
        {"latest_game_date_after": "2026-06-13",
         "regular_season_start_by_season": {2027: "2026-10-20T23:30:00Z"},
         "source_by_season": {2027: "SPORTSDATAVERSE_ESPN"}},
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


def test_nba_current_season_recovery_target_rotates_with_year():
    gap_2027 = maintenance._nba_current_season_data_gap(
        {"latest_game_date_after": "2026-06-13"},
        datetime(2026, 11, 5, tzinfo=timezone.utc),
    )
    gap_2028 = maintenance._nba_current_season_data_gap(
        {"latest_game_date_after": "2027-06-18"},
        datetime(2027, 11, 5, tzinfo=timezone.utc),
    )
    assert gap_2027["required_season_label"] == 2027
    assert gap_2027["next_action"] == (
        "REACQUIRE_CANONICAL_2027_SDV_SEASON_AND_VERIFY_PERSISTED_SETTLEMENTS"
    )
    assert gap_2028["required_season_label"] == 2028
    assert gap_2028["next_action"] == (
        "REACQUIRE_CANONICAL_2028_SDV_SEASON_AND_VERIFY_PERSISTED_SETTLEMENTS"
    )
    assert gap_2028["can_execute"] is False


def test_failed_calibration_builder_persists_typed_nonpromotable_shadow(monkeypatch):
    """A non-promotable Platt fit must not disappear before review receipts."""
    from dataclasses import dataclass
    import basketball_specialist_pipeline as pipeline
    from v17.basketball_team_event_calibration_artifact import BasketballCalibrationArtifactError

    @dataclass
    class FakeArtifact:
        artifact_checksum: str = "a" * 64
        training_row_count: int = 300
        train_start_date: str = "2024-01-01"
        train_end_date: str = "2025-01-01"
        holdout_row_count: int = 80
        holdout_brier: float = 0.23
        holdout_log_loss: float = 0.65
        holdout_accuracy: float = 0.60

    @dataclass
    class FakeDecision:
        capability_status: str = "CERTIFIED"
        reason_code: str = "OK"

    writes = {}
    class FakeTable:
        def __init__(self, name):
            self.name = name
        def insert(self, row):
            writes[self.name] = row
            return self
        def upsert(self, row, **_kwargs):
            writes[self.name] = row
            return self
        def execute(self):
            return SimpleNamespace(data=[])

    class FakeDb:
        def table(self, name):
            return FakeTable(name)

    artifact = FakeArtifact()
    calibration = SimpleNamespace(
        result=None,
        coefficients=SimpleNamespace(a=0.1, b=0.9),
        metrics=SimpleNamespace(brier=0.24, log_loss=0.65, ece=0.05, calibration_bias=0.01),
    )
    monkeypatch.setattr(pipeline, "load_games", lambda *_args: [object()])
    monkeypatch.setattr(pipeline, "build_pregame_features", lambda *_args: [object()])
    monkeypatch.setattr(pipeline, "train_specialist", lambda *_args, **_kw: artifact)
    monkeypatch.setattr(pipeline, "disjoint_calibration_and_holdout",
                        lambda *_args: ([object()], [object()]))
    monkeypatch.setattr(pipeline, "_walk_forward_raw",
                        lambda *_args: ([0.4]*240, [0, 1]*120, [i//40 for i in range(240)],
                                        ["2025-01-01T00:00:00+00:00"]*240))
    monkeypatch.setattr(pipeline, "phase_b_platt", lambda *_args: calibration)
    monkeypatch.setattr(pipeline, "evaluate_untouched_calibrated_holdout",
                        lambda *_args: {"holdout_rows": 80, "publication_authorized": False})
    monkeypatch.setattr(pipeline, "certification_decision", lambda *_args, **_kw: FakeDecision())
    def reject_calibration_artifact(**_kw):
        raise BasketballCalibrationArtifactError("BASKETBALL_PLATT_CALIBRATION_NOT_PROMOTABLE")
    monkeypatch.setattr(pipeline, "build_shadow_binary_calibration_artifact",
                        reject_calibration_artifact)

    result = pipeline.fit_and_persist("NBA", client=FakeDb(), provenance_complete=True)
    assert result["persisted_status"] == "SHADOW"
    assert result["calibration_artifact_sha256"] is None
    assert result["calibration_artifact_certification_status"] is None
    assert result["calibration_artifact_block"] == "BASKETBALL_PLATT_CALIBRATION_NOT_PROMOTABLE"
    model = writes["wow_basketball_team_event_model_artifacts"]
    calibrator = writes["wow_basketball_team_event_calibrators"]
    certification = writes["wow_basketball_team_event_certification"]
    assert model["active"] is calibrator["active"] is False
    assert model["promoted"] is calibrator["promoted"] is False
    assert calibrator["calibration_artifact_payload"] is None
    assert calibrator["calibration_artifact_sha256"] is None
    assert certification["capability_status"] == "SHADOW"
    assert certification["reason_code"] == "BASKETBALL_CALIBRATION_ARTIFACT_BLOCKED"
    assert certification["certification_notes"]["calibration_artifact_block"] == (
        "BASKETBALL_PLATT_CALIBRATION_NOT_PROMOTABLE"
    )
    assert certification["certification_notes"]["promotion_attempted"] is False
    assert result["can_execute"] is False


@pytest.mark.parametrize("sport", ["NBA", "WNBA"])
@pytest.mark.parametrize("source_failed", [False, True])
def test_explicit_clock_never_replays_unbounded_persisted_corpus(monkeypatch, sport, source_failed):
    """A cutoff on hydration cannot authorize fitting a later persisted corpus."""
    def hydrate(*_args, **_kwargs):
        if source_failed:
            raise maintenance.BasketballHydrationError("SOURCE_HTTP_503")
        return {"settled_rows": 0, "latest_game_date_after": "2026-11-04"}

    replayed = []
    monkeypatch.setattr(maintenance, "hydrate", hydrate)
    monkeypatch.setattr(maintenance, "run_training_replay", lambda *a, **kw: replayed.append(sport) or {})
    cutoff = datetime(2026, 11, 5, 12, tzinfo=timezone.utc)
    result = maintenance.run_basketball_model_maintenance(
        object(), sports=(sport,), seasons=(2027,), now=cutoff,
    )
    assert replayed == []
    assert result["status"] == "BLOCKED"
    assert result["rows_updated"] == 0
    row = result["rows"][0]
    assert row["code"] == "BASKETBALL_HISTORICAL_REPLAY_CUTOFF_UNSUPPORTED"
    assert row["replay_cutoff"] == cutoff.isoformat()
    assert row["replay_attempted"] is False
    assert row["promotion_attempted"] is False
    assert row["probability_publishable"] is False
    assert row["can_execute"] is False
    if source_failed:
        assert row["hydration_blocker"]["code"] == "SOURCE_HTTP_503"
    else:
        assert row["hydration"]["settled_rows"] == 0


@pytest.mark.parametrize("latest", [None, "2026-06-13", "2026-10-24"])
def test_late_october_requires_current_season_persisted_settlement(latest):
    """A prior, absent or future corpus cannot pass after the acquired opening."""
    evidence = {
        "latest_game_date_after": latest,
        "regular_season_start_by_season": {2027: "2026-10-20T23:30:00Z"},
        "source_by_season": {2027: "SPORTSDATAVERSE_ESPN"},
    }
    gap = maintenance._nba_current_season_data_gap(evidence, datetime(2026, 10, 23, tzinfo=timezone.utc))
    assert gap["code"] == "NBA_CURRENT_SEASON_SETTLED_DATA_MISSING"
    assert gap["required_season_start"] == "2026-10-01"
    assert gap["required_season_label"] == 2027


@pytest.mark.parametrize("opening", [None, "bad", "2026-10-20T23:30:00", "2027-01-01T00:00:00Z"])
def test_october_preopening_exception_requires_valid_acquired_calendar(opening):
    """Unknown, naive or wrong-season opening evidence cannot waive the data hold."""
    evidence = {
        "latest_game_date_after": "2026-06-13",
        "regular_season_start_by_season": {2027: opening},
        "source_by_season": {2027: "SPORTSDATAVERSE_ESPN"},
    }
    gap = maintenance._nba_current_season_data_gap(evidence, datetime(2026, 10, 17, tzinfo=timezone.utc))
    assert gap["code"] == "NBA_CURRENT_SEASON_SETTLED_DATA_MISSING"


def test_late_october_source_failure_keeps_native_error_and_never_replays(monkeypatch):
    """Missing October acquisition cannot turn a June corpus into a successful refresh."""
    class OctoberClock(datetime):
        @classmethod
        def now(cls, tz=None):
            """Freeze the ambient live clock after the actual season opening."""
            return cls(2026, 10, 23, tzinfo=timezone.utc)

    monkeypatch.setattr(maintenance, "datetime", OctoberClock)
    def unavailable(*_args, **_kwargs):
        raise maintenance.BasketballHydrationError("NBA_SOURCE_HTTP_503")
    replayed = []
    monkeypatch.setattr(maintenance, "hydrate", unavailable)
    monkeypatch.setattr(maintenance, "run_training_replay", lambda *a, **kw: replayed.append(True) or {})
    result = maintenance.run_basketball_model_maintenance(object(), sports=("NBA",))
    assert replayed == []
    row = result["rows"][0]
    assert row["code"] == "NBA_CURRENT_SEASON_SETTLED_DATA_MISSING"
    assert row["hydration_blocker"]["upstream_hydration_blocker"]["code"] == "NBA_SOURCE_HTTP_503"
    assert result["rows_updated"] == 0


@pytest.mark.parametrize("clock", ["2026-10-17T12:00:00Z", "2026-10-20T23:29:59Z"])
def test_verified_acquired_opening_preserves_preseason_shadow_replay(clock):
    """Before canonical opening, a prior-season corpus remains a valid research input."""
    evidence = {
        "latest_game_date_after": "2026-06-13",
        "regular_season_start_by_season": {"2027": "2026-10-20T23:30:00Z"},
        "source_by_season": {"2027": "SPORTSDATAVERSE_ESPN"},
    }
    assert maintenance._nba_current_season_data_gap(evidence, datetime.fromisoformat(clock.replace("Z", "+00:00"))) is None


@pytest.mark.parametrize("change", [{}, {"season": "2026"}, {"game_date_time": "bad"}, {"game_date": "2026-11-01", "game_date_time": "2026-11-01T01:30:00"}])
def test_opening_calendar_uses_acquired_regular_schedule_and_rejects_ambiguity(change):
    """Season conflict, malformed start or ambiguous Eastern time cannot waive a hold."""
    import basketball_event_hydration_runtime as hydration
    first = {"game_id": "1", "game_date": "2026-10-20", "home_id": "7", "away_id": "8", "season": "2027", "season_type": "2", "game_date_time": "2026-10-20T19:30:00", **change}
    later = {"game_id": "2", "game_date": "2026-10-21", "home_id": "7", "away_id": "8", "season": "2027", "season_type": "2", "game_date_time": "2026-10-21T19:30:00"}
    opening = hydration._sportsdataverse_regular_season_start([later, first], 2027)
    assert opening == (None if change else "2026-10-20T23:30:00+00:00")


def test_hydrate_returns_opening_from_canonical_schedule_without_settling_future_game(monkeypatch):
    """Actual hydration exposes the acquired opening while a future game stays unsettled."""
    import basketball_event_hydration_runtime as hydration
    raw = {"game_id": "1", "game_date": "2026-10-20", "home_id": "7", "away_id": "8", "season": "2027", "season_type": "2", "game_date_time": "2026-10-20T19:30:00", "status_type_completed": "false"}
    monkeypatch.setattr(hydration, "fetch_sportsdataverse_games", lambda *_args: [raw])
    monkeypatch.setattr(hydration, "_latest_persisted_game_date", lambda *_args: None)
    written = []
    monkeypatch.setattr(hydration, "_persist_rows", lambda _db, _sport, rows: written.extend(rows) or 0)
    result = hydration.hydrate("NBA", (2027,), object(), now=datetime(2026, 10, 17, tzinfo=timezone.utc))
    assert result["regular_season_start_by_season"] == {2027: "2026-10-20T23:30:00+00:00"}
    assert result["source_by_season"] == {2027: "SPORTSDATAVERSE_ESPN"}
    assert result["settled_rows"] == 0
    assert written == []


@pytest.mark.parametrize("clock", ["2026-10-20T23:30:00Z", "2026-10-21T01:30:00+02:00"])
def test_opening_instant_already_requires_current_settlement(clock):
    """Equivalent UTC offsets cannot extend the pre-opening exception through tipoff."""
    evidence = {"latest_game_date_after": "2026-06-13", "regular_season_start_by_season": {2027: "2026-10-20T23:30:00Z"}, "source_by_season": {2027: "SPORTSDATAVERSE_ESPN"}}
    gap = maintenance._nba_current_season_data_gap(evidence, datetime.fromisoformat(clock.replace("Z", "+00:00")))
    assert gap["code"] == "NBA_CURRENT_SEASON_SETTLED_DATA_MISSING"


@pytest.mark.parametrize("provider", ["BALLDONTLIE", "ESPN_SCOREBOARD"])
def test_unverified_provider_cannot_claim_preopening_exception(provider):
    """Only the existing canonical acquired schedule can establish opening evidence."""
    evidence = {"latest_game_date_after": "2026-06-13", "regular_season_start_by_season": {2027: "2026-10-20T23:30:00Z"}, "source_by_season": {2027: provider}}
    assert maintenance._nba_current_season_data_gap(evidence, datetime(2026, 10, 17, tzinfo=timezone.utc))["code"] == "NBA_CURRENT_SEASON_SETTLED_DATA_MISSING"


def test_conflicting_same_game_calendar_cannot_prove_preopening():
    """Contradictory starts for one source ID invalidate its opening evidence."""
    import basketball_event_hydration_runtime as hydration
    row = {"game_id": "1", "game_date": "2026-10-20", "home_id": "7", "away_id": "8", "season_type": "2", "game_date_time": "2026-10-20T19:30:00"}
    assert hydration._sportsdataverse_regular_season_start([row, {**row, "game_date": "2026-10-21", "game_date_time": "2026-10-21T19:30:00"}], 2027) is None
    assert hydration._sportsdataverse_regular_season_start([{**row, "season_type": "1"}], 2027) is None


def test_current_october_settlement_preserves_live_replay_without_calendar_bypass():
    """Actual persisted current-season evidence passes even without an opening exception."""
    assert maintenance._nba_current_season_data_gap({"latest_game_date_after": "2026-10-22"}, datetime(2026, 10, 23, tzinfo=timezone.utc)) is None
