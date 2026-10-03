from __future__ import annotations

from pathlib import Path

import pytest

from agent_runtime.celery_app import celery_app
from v17 import spread_forward_serving_cache as serving
from v17 import spread_forward_shadow as shadow
from v17.spread_margin_challenger import MarginDistributionArtifact, SpreadChallengerUnavailable


def _artifact() -> MarginDistributionArtifact:
    return MarginDistributionArtifact(
        sport="NCAAF",
        model_family="NCAAF_SPREAD_MARGIN_RIDGE_EMPIRICAL_V1",
        feature_schema_version="NCAAF_SPREAD_MARGIN_TEAM_STATE_V1",
        feature_names=("a", "b"),
        scaler_mean=(1.0, 2.0),
        scaler_scale=(3.0, 4.0),
        coefficients=(0.25, -0.5),
        intercept=1.25,
        calibration_residuals=(-7.0, 0.0, 7.0),
        train_rows=600,
        calibration_rows=200,
        test_rows=200,
        training_dataset_hash="dataset-hash",
        ridge_alpha=4.0,
    )


def _fingerprint():
    return ("feature-9", "2026-09-28T18:00:00+00:00", "game-9", "2026-09-28T18:00:00+00:00")


def _settled():
    return [
        {
            "event_id": "game-9",
            "event_start_time": "2026-09-28T18:00:00+00:00",
            "season": 2026,
            "home_team": "HOME",
            "away_team": "AWAY",
            "home_score": 31,
            "away_score": 21,
        }
    ]


def test_serving_cache_round_trip_preserves_exact_artifact_and_governance():
    artifact = _artifact()
    raw = serving.encode_context(
        artifact=artifact,
        settled_events=_settled(),
        source_fingerprint=_fingerprint(),
        latest_training_event="2026-09-28T18:00:00+00:00",
    )

    decoded = serving.decode_context(raw, expected_source_fingerprint=_fingerprint())

    assert decoded["artifact"] == artifact
    assert decoded["settled_events"] == tuple(_settled())
    assert decoded["latest_training_event"] == "2026-09-28T18:00:00+00:00"
    assert decoded["source_fingerprint"] == _fingerprint()
    assert decoded["can_execute"] is False


def test_serving_cache_rejects_stale_source_fingerprint():
    raw = serving.encode_context(
        artifact=_artifact(),
        settled_events=_settled(),
        source_fingerprint=_fingerprint(),
        latest_training_event="2026-09-28T18:00:00+00:00",
    )
    with pytest.raises(SpreadChallengerUnavailable) as exc:
        serving.decode_context(
            raw,
            expected_source_fingerprint=("new", "2026-09-29T18:00:00+00:00", "new", "2026-09-29T18:00:00+00:00"),
        )
    assert exc.value.code == "SPREAD_FORWARD_SERVING_CACHE_SOURCE_MISMATCH"


def test_production_cache_mode_never_runs_web_process_fit(monkeypatch):
    artifact = _artifact()
    fingerprint = _fingerprint()
    monkeypatch.setenv("WOW_NCAAF_SPREAD_FORWARD_SERVING_CACHE_REQUIRED", "1")
    monkeypatch.setattr(shadow, "_ncaaf_forward_source_fingerprint", lambda _client: fingerprint)
    monkeypatch.setattr(
        shadow,
        "load_ncaaf_forward_context",
        lambda _client: (_ for _ in ()).throw(AssertionError("web process must not load full spread corpus")),
    )
    monkeypatch.setattr(
        shadow,
        "fit_margin_distribution_artifact",
        lambda *_a, **_k: (_ for _ in ()).throw(AssertionError("web process must not fit spread artifact")),
    )
    monkeypatch.setattr(
        serving,
        "load_ready_context",
        lambda _fingerprint: {
            "artifact": artifact,
            "settled_events": tuple(_settled()),
            "latest_training_event": "2026-09-28T18:00:00+00:00",
            "source_fingerprint": fingerprint,
            "can_execute": False,
        },
    )
    shadow._clear_forward_context_cache()

    context, status, age = shadow._cached_forward_context(object())

    assert context.artifact == artifact
    assert context.source_fingerprint == fingerprint
    assert status == "MISS_WORKER_SERVING_CACHE"
    assert age == 0.0


def test_production_cache_miss_dispatches_worker_and_fails_closed(monkeypatch):
    fingerprint = _fingerprint()
    dispatched = []
    monkeypatch.setenv("WOW_NCAAF_SPREAD_FORWARD_SERVING_CACHE_REQUIRED", "1")
    monkeypatch.setattr(shadow, "_ncaaf_forward_source_fingerprint", lambda _client: fingerprint)
    monkeypatch.setattr(
        shadow,
        "load_ncaaf_forward_context",
        lambda _client: (_ for _ in ()).throw(AssertionError("web process must not load full spread corpus")),
    )
    monkeypatch.setattr(
        shadow,
        "fit_margin_distribution_artifact",
        lambda *_a, **_k: (_ for _ in ()).throw(AssertionError("web process must not fit spread artifact")),
    )

    def missing(_fingerprint):
        raise SpreadChallengerUnavailable(
            "SPREAD_FORWARD_SERVING_CACHE_BUILD_PENDING",
            "cache is pending",
        )

    monkeypatch.setattr(serving, "load_ready_context", missing)
    monkeypatch.setattr(serving, "dispatch_build", lambda: dispatched.append(True) or "task-1")
    shadow._clear_forward_context_cache()

    with pytest.raises(SpreadChallengerUnavailable) as exc:
        shadow._cached_forward_context(object())

    assert exc.value.code == "SPREAD_FORWARD_SERVING_CACHE_BUILD_PENDING"
    assert dispatched == [True]


def test_worker_task_is_registered_and_production_contract_requires_cache():
    assert "wow.v17.build_ncaaf_spread_serving_cache" in celery_app.tasks

    render = (Path(__file__).resolve().parents[3] / "render.yaml").read_text(encoding="utf-8")
    service = render[render.index("name: wow-governed-probability-engine"):render.index("name: wow-agent-worker")]
    assert "WOW_NCAAF_SPREAD_FORWARD_SERVING_CACHE_REQUIRED" in service
    assert 'value: "1"' in service
    assert "plan: free" in service
