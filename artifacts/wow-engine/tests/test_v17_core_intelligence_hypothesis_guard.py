"""Regression for science-only canonical identity and fail-closed hypothesis authority."""
from __future__ import annotations

from datetime import datetime, timedelta, timezone
import pytest

from v17 import core_intelligence_runtime as runtime
from v17.core_intelligence import build_learning_observation


class _Response:
    def __init__(self, rows):
        self.data = rows


class _ReadQuery:
    def __init__(self, rows):
        self.rows = rows

    def select(self, _fields):
        return self

    def in_(self, key, values):
        self.rows = [row for row in self.rows if str(row.get(key)) in set(values)]
        return self

    def execute(self):
        return _Response(self.rows)


class _ReadOnlyDB:
    def __init__(self, source_rows):
        self.source_rows = source_rows
        self.tables = []

    def table(self, name):
        self.tables.append(name)
        assert name == "wow_predictions", f"unexpected DB operation on {name}"
        return _ReadQuery(list(self.source_rows))


def _cohort(*, n=40, games=1, model="MLB_PITCHER_SO_FAILURE_PATH_NB_V1"):
    observations, source_rows = [], []
    event_start = datetime(2026, 9, 20, 20, 0, tzinfo=timezone.utc)
    for i in range(n):
        event_key = str(823333 + i % games)
        prediction = {
            "prediction_id": f"00000000-0000-0000-0000-{i + 1:012d}",
            "event_id": f"MLB:{event_key}",
            "sport": "MLB",
            "league": "MLB",
            "market_type": "PLAYER_PROP",
            "stat_type": "PITCHER_STRIKEOUTS",
            "player": "Test Pitcher",
            "line": "4.5",
            "direction": "MORE",
            "model_family": model,
            "model_artifact_version": "fixed-model-v1",
            "calibration_version": "phase-a",
            "calibration_status": "PRECALIBRATION_SHRINKAGE",
            "probability_publishable": True,
            "calibrated_probability": 0.8,
            "calibrated_probability_lower_bound": 0.7,
            "created_at": (event_start - timedelta(hours=2)).isoformat(),
            "model_timestamp": (event_start - timedelta(hours=3)).isoformat(),
            "locked_at": (event_start - timedelta(hours=1)).isoformat(),
            "event_start_time": event_start.isoformat(),
        }
        # A repeated same-game thesis must carry ONE coherent settlement.
        # Distinct games can legitimately have different realized results.
        hit = (i % games) < (games // 3)
        outcome = {
            "prediction_id": prediction["prediction_id"],
            "official_result": "HIT" if hit else "MISS",
            "hit": hit,
            "actual_stat": 6 if hit else 3,
            "settlement_source": f"https://statsapi.mlb.com/api/v1.1/game/{event_key}/feed/live",
            "settlement_timestamp": (event_start + timedelta(hours=4)).isoformat(),
        }
        observations.append(build_learning_observation(prediction, outcome))
        source_rows.append(prediction)
    return observations, source_rows


def test_repeated_40_forecasts_are_one_game_no_hypotheses(monkeypatch):
    rows, sources = _cohort()
    monkeypatch.setattr(runtime, "load_observations", lambda _db, **kw: rows)
    db = _ReadOnlyDB(sources)
    summary = runtime.intelligence_summary(db, min_samples=30, persist_hypotheses=True)
    assert summary["observation_n"] == 40
    assert summary["hypothesis_n"] == 0
    assert summary["hypotheses"] == []
    assert summary["hypothesis_persistence"] == "HELD_EFFECTIVE_SAMPLE_AND_HOLDOUT_UNVERIFIED"
    assert summary["can_execute"] is False
    cohort = summary["cohorts"][0]
    assert cohort["raw_observation_hypothesis_threshold_met"] is True
    assert cohort["ready_for_hypothesis"] is False
    assert cohort["metric_population"] == "RAW_OBSERVATIONS_DIAGNOSTIC_ONLY"
    assert cohort["hypothesis_blocker"] == "CANONICAL_EFFECTIVE_SAMPLE_AND_HOLDOUT_UNVERIFIED"
    research = cohort["retrospective_research_audit"]
    assert research["eligible_forecast_rows"] == 40
    assert research["official_game_clusters"] == 1
    assert research["canonical_directional_theses"] == 1
    assert research["status"] == "RESEARCH_ONLY"
    assert research["certified"] is False
    assert research["calibration_status_counts"] == {"PRECALIBRATION_SHRINKAGE": 40}
    assert "wow_intelligence_hypotheses" not in db.tables


def test_even_40_separate_games_without_certification_do_not_authorize_learning(monkeypatch):
    rows, sources = _cohort(n=40, games=40)
    monkeypatch.setattr(runtime, "load_observations", lambda _db, **kw: rows)
    result = runtime.intelligence_summary(_ReadOnlyDB(sources), min_samples=30, persist_hypotheses=True)
    cohort = result["cohorts"][0]
    assert cohort["retrospective_research_audit"]["official_game_clusters"] == 40
    assert cohort["ready_for_hypothesis"] is False
    assert result["hypothesis_n"] == 0


def test_conflicting_official_results_across_artifact_cohorts_hold_all(monkeypatch):
    observations, sources = _cohort(n=2, games=1)
    sources[1]["model_artifact_version"] = "fixed-model-v2"
    sources[1]["calibration_version"] = "phase-b"
    observations[1] = build_learning_observation(sources[1], {
        "prediction_id": sources[1]["prediction_id"],
        "official_result": "HIT",
        "hit": True,
        "actual_stat": 6,
        "settlement_source": "https://statsapi.mlb.com/api/v1.1/game/823333/feed/live",
        "settlement_timestamp": "2026-09-21T00:00:00+00:00",
    })
    for ordering in (observations, list(reversed(observations))):
        monkeypatch.setattr(runtime, "load_observations", lambda _db, **kw: ordering)
        result = runtime.intelligence_summary(_ReadOnlyDB(sources))
        assert result["cohort_n"] == 2
        for cohort in result["cohorts"]:
            report = cohort["retrospective_research_audit"]
            assert report["status"] == "HOLD"
            assert report["blocker"] == "OFFICIAL_SETTLEMENT_CONFLICT"
            assert report["cross_cohort_conflicting_game_player_stats"] == 1
            assert "brier_score" not in report
            assert "log_loss" not in report
            assert report["can_execute"] is False
        assert result["hypotheses"] == []


def test_other_models_do_not_get_proxy_official_game_identity(monkeypatch):
    rows, sources = _cohort(n=40, model="OTHER_MODEL")
    monkeypatch.setattr(runtime, "load_observations", lambda _db, **kw: rows)
    db = _ReadOnlyDB(sources)
    result = runtime.intelligence_summary(db, persist_hypotheses=True)
    assert result["hypothesis_n"] == 0
    assert "retrospective_research_audit" not in result["cohorts"][0]
    assert result["hypothesis_persistence"] == "HELD_EFFECTIVE_SAMPLE_AND_HOLDOUT_UNVERIFIED"
    assert db.tables == []


def test_corrupt_prediction_probability_cannot_be_used_as_verified_research(monkeypatch):
    rows, sources = _cohort(n=1)
    sources[0]["calibrated_probability"] = 0.95
    monkeypatch.setattr(runtime, "load_observations", lambda _db, **kw: rows)
    result = runtime.intelligence_summary(_ReadOnlyDB(sources))
    research = result["cohorts"][0]["retrospective_research_audit"]
    assert research["status"] == "HOLD"
    assert research["blocker"] == "NO_VERIFIED_ROWS"
    assert research["observation_source_probability_conflict_n"] == 1
    assert result["hypothesis_n"] == 0


def test_missing_source_is_typed_diagnostic_not_a_fake_independent_sample(monkeypatch):
    rows, _ = _cohort(n=1)
    monkeypatch.setattr(runtime, "load_observations", lambda _db, **kw: rows)
    result = runtime.intelligence_summary(_ReadOnlyDB([]))
    research = result["cohorts"][0]["retrospective_research_audit"]
    assert research["source_prediction_missing_n"] == 1
    assert research["status"] == "HOLD"
    assert result["hypothesis_n"] == 0


def test_storage_transport_failure_remains_typed_boundary_error(monkeypatch):
    rows, _ = _cohort(n=1)
    monkeypatch.setattr(runtime, "load_observations", lambda _db, **kw: rows)
    class _Broken:
        def table(self, name):
            raise RuntimeError("db unavailable")
    with pytest.raises(runtime.CoreIntelligenceBoundaryError) as error:
        runtime.intelligence_summary(_Broken())
    assert error.value.boundary == "wow_predictions.select_core_intelligence_research_identity"
    assert error.value.error_type == "RuntimeError"


def test_summary_read_path_cannot_persist_hypotheses(monkeypatch):
    rows, sources = _cohort(n=1)
    monkeypatch.setattr(runtime, "load_observations", lambda _db, **kw: rows)
    db = _ReadOnlyDB(sources)
    result = runtime.intelligence_summary(db, persist_hypotheses=False)
    assert result["hypothesis_persistence"] == "NOT_REQUESTED"
    assert result["hypothesis_n"] == 0 and result["hypotheses"] == []
    assert "wow_intelligence_hypotheses" not in db.tables
