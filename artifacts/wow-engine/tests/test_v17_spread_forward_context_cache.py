from __future__ import annotations

from datetime import datetime, timedelta, timezone
import threading
import time

import pytest

from v17 import spread_forward_shadow as shadow
from v17.spread_margin_challenger import (
    MarginDistributionArtifact,
    MarginTrainingRow,
    SpreadChallengerUnavailable,
)


def _artifact() -> MarginDistributionArtifact:
    return MarginDistributionArtifact(
        sport="NCAAF",
        model_family="NCAAF_SPREAD_MARGIN_RIDGE_EMPIRICAL_V1",
        feature_schema_version="NCAAF_SPREAD_MARGIN_TEAM_STATE_V1",
        feature_names=("x",),
        scaler_mean=(0.0,),
        scaler_scale=(1.0,),
        coefficients=(1.0,),
        intercept=0.0,
        calibration_residuals=(-1.0, 1.0),
        train_rows=300,
        calibration_rows=100,
        test_rows=100,
        training_dataset_hash="dataset-hash",
        ridge_alpha=4.0,
    )


def _training_row() -> MarginTrainingRow:
    start = datetime(2026, 9, 20, 18, 0, tzinfo=timezone.utc)
    return MarginTrainingRow(
        event_id="hist-1",
        event_start_time=start.isoformat(),
        feature_as_of=(start - timedelta(seconds=1)).isoformat(),
        margin=7,
        features={"x": 1.0},
        source_manifest_sha256="sha",
    )


def _install_scoring_stubs(monkeypatch, *, load_delay: float = 0.0):
    calls = {"load": 0, "fit": 0, "features": 0, "score": 0}
    row = _training_row()
    artifact = _artifact()

    def load(_client):
        calls["load"] += 1
        if load_delay:
            time.sleep(load_delay)
        return [row], [{"event_id": "hist-1", "event_start_time": row.event_start_time}]

    def fit(rows, *, sport, min_rows, ridge_alpha):
        calls["fit"] += 1
        assert rows == [row]
        assert sport == "NCAAF"
        assert min_rows == shadow.MIN_TRAIN_ROWS
        assert ridge_alpha == shadow.RIDGE_ALPHA
        return artifact

    def features(_settled, *, target_event, min_prior_games=shadow.MIN_PRIOR_GAMES):
        calls["features"] += 1
        return {"x": 1.0}, {
            "feature_family_version": "TEAM_STATE_INTELLIGENCE_V1",
            "feature_as_of": target_event["event_start_time"],
            "home_prior_events": 5,
            "away_prior_events": 5,
            "market_features_used": False,
            "moneyline_probability_used": False,
            "spread_line_used_as_feature": False,
            "manual_probability_adjustments": False,
            "can_execute": False,
        }

    def score(_artifact_value, _features, *, home_spread):
        calls["score"] += 1
        assert _artifact_value is artifact
        return {
            "predicted_home_margin_center": 3.0,
            "p_cover": 0.61,
            "p_push": 0.01,
            "p_not_cover": 0.38,
            "p_cover_given_no_push": 0.616161,
            "research_lower_bound_cover": 0.56,
            "distribution_sample_n": 100,
        }

    monkeypatch.setattr(shadow, "load_ncaaf_forward_context", load)
    monkeypatch.setattr(shadow, "fit_margin_distribution_artifact", fit)
    monkeypatch.setattr(shadow, "build_forward_matchup_features", features)
    monkeypatch.setattr(shadow, "score_home_spread", score)
    shadow._clear_forward_context_cache()
    return calls


def _run(event_id: str):
    return shadow.run_ncaaf_forward_shadow(
        object(),
        event_id=event_id,
        event_start_time="2026-09-27T18:00:00+00:00",
        home_team="HOME",
        away_team="AWAY",
        home_spread=-6.5,
        season=2026,
    )


def test_board_scan_reuses_one_immutable_fit(monkeypatch):
    calls = _install_scoring_stubs(monkeypatch)

    first = _run("game-1")
    second = _run("game-2")

    assert calls == {"load": 1, "fit": 1, "features": 2, "score": 2}
    assert first["forward_context_cache"]["status"] == "MISS_REBUILT"
    assert second["forward_context_cache"]["status"] == "HIT"
    assert first["training_dataset_hash"] == second["training_dataset_hash"] == "dataset-hash"
    assert first["probability_publishable"] is False
    assert first["automatic_certification"] is False
    assert first["automatic_promotion"] is False
    assert first["can_execute"] is False


def test_concurrent_cold_requests_never_duplicate_full_corpus_build(monkeypatch):
    calls = _install_scoring_stubs(monkeypatch, load_delay=0.08)
    results = []
    errors = []

    def worker(event_id):
        try:
            results.append(_run(event_id))
        except Exception as exc:  # pragma: no cover - assertion reports below
            errors.append(exc)

    first = threading.Thread(target=worker, args=("game-a",))
    second = threading.Thread(target=worker, args=("game-b",))
    first.start()
    time.sleep(0.01)
    second.start()
    first.join(timeout=2.0)
    second.join(timeout=2.0)

    assert not errors
    assert len(results) == 2
    assert calls["load"] == 1
    assert calls["fit"] == 1
    assert calls["features"] == 2
    assert calls["score"] == 2
    statuses = {result["forward_context_cache"]["status"] for result in results}
    assert "MISS_REBUILT" in statuses
    assert statuses <= {"MISS_REBUILT", "HIT", "HIT_AFTER_WAIT"}


def test_cold_build_contention_fails_typed_instead_of_spawning_parallel_load(monkeypatch):
    monkeypatch.setattr(shadow, "FORWARD_CONTEXT_BUILD_LOCK_TIMEOUT_SECONDS", 0.01)
    shadow._clear_forward_context_cache()
    assert shadow._FORWARD_CONTEXT_LOCK.acquire(timeout=0.1)
    try:
        with pytest.raises(SpreadChallengerUnavailable) as exc:
            shadow._cached_forward_context(object())
    finally:
        shadow._FORWARD_CONTEXT_LOCK.release()

    assert exc.value.code == "SPREAD_FORWARD_CONTEXT_BUILD_BUSY"
    assert exc.value.code != "MODEL_UNAVAILABLE"
