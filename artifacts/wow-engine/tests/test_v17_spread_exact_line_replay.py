from datetime import datetime, timedelta, timezone

import pytest

from v17 import spread_exact_line_replay as replay
from v17.spread_exact_line_evidence import ExactSpreadEvidence
from v17.spread_margin_challenger import MarginDistributionArtifact, MarginTrainingRow, SpreadChallengerUnavailable


def _artifact():
    return MarginDistributionArtifact(
        sport="NFL",
        model_family="NFL_SPREAD_MARGIN_RIDGE_EMPIRICAL_V1",
        feature_schema_version="NFL_SPREAD_MARGIN_TEAM_STATE_V1",
        feature_names=("x",),
        scaler_mean=(0.0,),
        scaler_scale=(1.0,),
        coefficients=(0.0,),
        intercept=0.0,
        calibration_residuals=(-3.0, 0.0, 3.0),
        train_rows=6,
        calibration_rows=2,
        test_rows=2,
        training_dataset_hash="dataset",
        ridge_alpha=4.0,
    )


def _row(index):
    start = datetime(2026, 9, 1, tzinfo=timezone.utc) + timedelta(days=index)
    return MarginTrainingRow(
        event_id=f"e{index}",
        event_start_time=start.isoformat(),
        feature_as_of=(start - timedelta(seconds=1)).isoformat(),
        margin=index - 3,
        features={"x": 0.0},
        source_manifest_sha256=f"sha-{index}",
    )


def test_exact_line_replay_composes_fit_binding_and_exact_evaluation(monkeypatch):
    rows = [_row(i) for i in range(10)]
    artifact = _artifact()
    synthetic = {"margin_mae": 9.0, "margin_rmse": 11.0, "cover_brier": 0.24, "cover_log_loss": 0.68, "cover_ece": 0.05}
    test_rows = rows[-2:]
    monkeypatch.setattr(replay, "load_replay_rows", lambda *_args, **_kwargs: rows)
    monkeypatch.setattr(replay, "train_margin_distribution_candidate", lambda *_args, **_kwargs: (artifact, synthetic))
    monkeypatch.setattr(replay, "_chronological_split", lambda *_args, **_kwargs: (rows[:6], rows[6:8], test_rows))
    monkeypatch.setattr(
        replay,
        "load_event_identities",
        lambda *_args, **_kwargs: [
            {"event_id": row.event_id, "event_start_time": row.event_start_time, "home_team": "DAL", "away_team": "PHI"}
            for row in test_rows
        ],
    )
    monkeypatch.setattr(replay, "load_spread_market_rows", lambda *_args, **_kwargs: [{"provider_event_id": "rd"}])
    evidence = {
        row.event_id: ExactSpreadEvidence(
            event_id=row.event_id,
            provider_event_id=f"rd-{row.event_id}",
            sport="NFL",
            event_start_time=row.event_start_time,
            sportsbook="Pinnacle",
            quote_timestamp=(datetime.fromisoformat(row.event_start_time) - timedelta(hours=1)).isoformat(),
            home_team="DAL",
            away_team="PHI",
            home_spread=-3.5,
            market_id="2",
            snapshot_kind="CURRENT",
        )
        for row in test_rows
    }
    monkeypatch.setattr(replay, "bind_exact_home_spreads", lambda **_kwargs: (evidence, {"bound_event_n": 2, "coverage": 1.0}))
    monkeypatch.setattr(
        replay,
        "evaluate_exact_line_candidate",
        lambda *_args, **_kwargs: {
            "evaluation_mode": "PROVIDER_BOUND_EXACT_SPREAD",
            "evidence_row_n": 2,
            "cover_brier": 0.23,
            "cover_log_loss": 0.66,
            "cover_ece": 0.04,
            "spread_line_used_as_feature": False,
            "probability_publishable": False,
            "can_execute": False,
        },
    )

    result = replay.run_exact_line_replay(sport="NFL", client=object(), min_rows=10)
    assert result["status"] == "EXPERIMENT_CREATED"
    assert result["exact_line_metrics"]["evaluation_mode"] == "PROVIDER_BOUND_EXACT_SPREAD"
    assert result["binding_audit"]["coverage"] == 1.0
    assert result["market_features_used"] is False
    assert result["spread_line_used_as_feature"] is False
    assert result["market_probability_substitution_used"] is False
    assert result["database_mutated"] is False
    assert result["production_registry_mutated"] is False
    assert result["automatic_certification"] is False
    assert result["probability_publishable"] is False
    assert result["can_execute"] is False


def test_basketball_identity_gap_is_typed_not_model_unavailable():
    with pytest.raises(SpreadChallengerUnavailable) as exc:
        replay.load_event_identities(object(), sport="NBA")
    assert exc.value.code == "SPREAD_EXACT_LINE_EVENT_IDENTITY_UNAVAILABLE"
    assert exc.value.code != "MODEL_UNAVAILABLE"


def test_ncaab_margin_label_gap_remains_typed():
    with pytest.raises(SpreadChallengerUnavailable) as exc:
        replay.load_event_identities(object(), sport="NCAAB")
    assert exc.value.code == "SPREAD_REPLAY_DATASET_UNAVAILABLE"
