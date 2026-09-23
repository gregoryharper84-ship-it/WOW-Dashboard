from __future__ import annotations

from io import StringIO
from pathlib import Path
from types import SimpleNamespace

import pytest

from nfl_event_model_v17 import NFLModelInputsInsufficient
import v17.nfl_current_season_summary_refresh as refresh
import v17.nfl_team_event_specialist as specialist


def _req():
    return SimpleNamespace(
        sport_specific_evidence={
            "season": 2026,
            "week": 3,
            "gameday": "2026-09-24",
            "schedule_content_sha256": "schedule-sha",
            "canonical_game_id": "2026_03_ATL_GB",
            "canonical_home_team": "GB",
            "canonical_away_team": "ATL",
            "required_home_season_prior_games": 2,
            "required_away_season_prior_games": 2,
            "provider_event_id": "sharpapi-nfl_falcons_packers_2026-09-24_b3",
            "identity_resolution": "PROVIDER_ID_TO_CANONICAL_SCHEDULE_MATCH",
        },
        research_run_id="wow-scout-test",
        event_key="NFL:sharpapi-test",
        requested_slate_date="2026-09-24",
        requested_timezone="America/Chicago",
        event_start_time_utc="2026-09-25T00:15:00Z",
        home_team="Green Bay Packers",
        away_team="Atlanta Falcons",
        source_snapshot_id="source-1",
        latest_material_update_timestamp="2026-09-23T15:00:00Z",
    )


def _feature(home_seen: int, away_seen: int):
    return {
        "features": {
            "home_season_prior_games": home_seen,
            "away_season_prior_games": away_seen,
        },
        "row_inputs_hash": f"row-{home_seen}-{away_seen}",
        "feature_schema_version": "NFL_P2_FEATURES_V1",
        "max_prior_gameday": "2026-09-20",
        "source_content_sha256s": ["schedule-sha", "pbp-sha"],
    }


def _model_result():
    return {
        "model_artifact_id": "model-1",
        "model_version": "NFL_EVENT_V17_TEST",
        "model_timestamp": "2026-09-23T00:00:00Z",
        "raw_home_probability": 0.61,
        "raw_away_probability": 0.39,
        "calibrated_home_probability": 0.60,
        "calibrated_away_probability": 0.40,
        "calibrated_home_lower_bound": 0.55,
        "calibrated_home_upper_bound": 0.65,
        "calibrated_away_lower_bound": 0.35,
        "calibrated_away_upper_bound": 0.45,
        "calibration_method": "PLATT",
        "calibration_version": "test",
        "calibration_training_n": 100,
        "uncertainty_method": "test",
    }


class _PredictionInsert:
    def __init__(self):
        self.payload = None

    def insert(self, payload):
        self.payload = payload
        return self

    def execute(self):
        return SimpleNamespace(data=[{
            "score_snapshot_id": "score-1",
            "event_prediction_id": "prediction-1",
        }])


class _DB:
    def __init__(self):
        self.predictions = _PredictionInsert()

    def table(self, name):
        assert name == "wow_nfl_event_predictions"
        return self.predictions


def _patch_model(monkeypatch):
    monkeypatch.setattr(
        specialist,
        "load_champion_model",
        lambda db: SimpleNamespace(model_family="NFL_EVENT_V17_TEST"),
    )
    monkeypatch.setattr(specialist, "score_feature_row", lambda model, row: _model_result())
    monkeypatch.setattr(specialist, "feature_order_hash", lambda: "feature-order-hash")


def test_stale_current_season_history_refreshes_once_then_scores(monkeypatch):
    calls = {"features": 0, "refresh": 0}

    def feature_row(**kwargs):
        calls["features"] += 1
        return _feature(0, 1) if calls["features"] == 1 else _feature(2, 2)

    monkeypatch.setattr(specialist, "_prediction_feature_row", feature_row)
    monkeypatch.setattr(
        refresh,
        "refresh_current_season_summaries",
        lambda db, *, season: calls.__setitem__("refresh", calls["refresh"] + 1) or {
            "status": "COMPLETED",
            "season": season,
            "can_execute": False,
        },
    )
    _patch_model(monkeypatch)

    result = specialist.score_nfl_team_event(_req(), db=_DB())

    assert calls == {"features": 2, "refresh": 1}
    assert result["code"] == "NFL_FITTED_MODEL_PATH_PROVEN"
    assert result["probability_publishable"] is True
    assert result["can_execute"] is False


def test_refresh_must_close_exact_history_gap_or_route_remains_held(monkeypatch):
    calls = {"refresh": 0}
    monkeypatch.setattr(specialist, "_prediction_feature_row", lambda **kwargs: _feature(0, 1))
    monkeypatch.setattr(
        refresh,
        "refresh_current_season_summaries",
        lambda db, *, season: calls.__setitem__("refresh", calls["refresh"] + 1),
    )
    monkeypatch.setattr(
        specialist,
        "load_champion_model",
        lambda db: pytest.fail("model must not run with stale current-season history"),
    )

    with pytest.raises(NFLModelInputsInsufficient, match="NFL_CURRENT_SEASON_HISTORY_STALE:HOME=0/2:AWAY=1/2"):
        specialist.score_nfl_team_event(_req(), db=object())

    assert calls["refresh"] == 1


def test_refresh_source_failure_stays_typed_inputs_insufficient(monkeypatch):
    monkeypatch.setattr(specialist, "_prediction_feature_row", lambda **kwargs: _feature(0, 1))

    def fail_refresh(db, *, season):
        raise RuntimeError("provider unavailable")

    monkeypatch.setattr(refresh, "refresh_current_season_summaries", fail_refresh)

    with pytest.raises(NFLModelInputsInsufficient, match="NFL_CURRENT_SEASON_HISTORY_REFRESH_FAILED:RuntimeError"):
        specialist.score_nfl_team_event(_req(), db=object())


def test_overcount_never_triggers_repair_or_model(monkeypatch):
    monkeypatch.setattr(specialist, "_prediction_feature_row", lambda **kwargs: _feature(3, 2))
    monkeypatch.setattr(
        refresh,
        "refresh_current_season_summaries",
        lambda *args, **kwargs: pytest.fail("overcount must remain fail-closed without repair"),
    )
    monkeypatch.setattr(
        specialist,
        "load_champion_model",
        lambda db: pytest.fail("model must not run after history overcount"),
    )

    with pytest.raises(NFLModelInputsInsufficient, match="NFL_CURRENT_SEASON_HISTORY_STALE:HOME=3/2:AWAY=2/2"):
        specialist.score_nfl_team_event(_req(), db=object())


def test_summary_refresh_materializes_only_nonzero_pbp_rows(monkeypatch):
    writes = {}

    monkeypatch.setattr(
        refresh,
        "_load_season_training_games",
        lambda db, season: [{"game_id": "2026_02_GB_NYJ", "season": season}],
    )

    def fake_capture(db, asset, workdir: Path):
        path = workdir / "pbp.csv"
        path.write_text("game_id,play_id\n2026_02_GB_NYJ,1\n", encoding="utf-8")
        return (
            SimpleNamespace(
                local_path=str(path),
                content_sha256="pbp-sha",
                row_count=1,
            ),
            "snapshot-pbp-1",
        )

    monkeypatch.setattr(refresh, "_capture_and_preserve", fake_capture)
    monkeypatch.setattr(refresh, "_read_csv", lambda path: (StringIO(""), iter([{"game_id": "2026_02_GB_NYJ"}])))
    monkeypatch.setattr(
        refresh,
        "build_game_team_summaries",
        lambda reader, **kwargs: [
            {"game_id": "2026_02_GB_NYJ", "team": "GB", "offensive_plays": 60},
            {"game_id": "2026_02_GB_NYJ", "team": "NYJ", "offensive_plays": 0},
        ],
    )

    def fake_upsert(db, table, rows, conflict):
        writes.update({"table": table, "rows": rows, "conflict": conflict})
        return len(rows)

    monkeypatch.setattr(refresh, "_upsert_batches", fake_upsert)

    result = refresh.refresh_current_season_summaries(object(), season=2026)

    assert writes["table"] == "wow_nfl_game_team_summaries"
    assert writes["conflict"] == "game_id,team"
    assert writes["rows"] == [{"game_id": "2026_02_GB_NYJ", "team": "GB", "offensive_plays": 60}]
    assert result["team_game_summaries_materialized"] == 1
    assert result["probability_publishable"] is False
    assert result["can_execute"] is False
