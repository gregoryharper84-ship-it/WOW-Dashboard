from datetime import datetime, timedelta, timezone

import pytest

import ncaaf_trainer as trainer


def make_rows(n=240):
    start = datetime(2022, 8, 1, tzinfo=timezone.utc)
    rows = []
    for i in range(n):
        kickoff = start + timedelta(days=i)
        signal = 1.0 if i % 4 in (0, 1, 2) else -1.0
        rows.append(
            trainer.TrainingRow(
                event_start_time=kickoff.isoformat(),
                feature_as_of=(kickoff - timedelta(hours=12)).isoformat(),
                home_won=(signal > 0),
                features={
                    "power_delta": signal,
                    "off_epa_delta": signal * 0.8,
                    "def_epa_delta": signal * 0.6,
                    "success_rate_delta": signal * 0.5,
                    "explosiveness_delta": signal * 0.4,
                    "qb_value_delta": signal * 0.9,
                    "qb_certainty_delta": signal * 0.2,
                    "ol_health_delta": signal * 0.3,
                    "def_front_health_delta": signal * 0.2,
                    "skill_availability_delta": signal * 0.3,
                    "rest_days_delta": 0.0,
                    "tempo_delta": signal * 0.1,
                    "turnover_volatility_delta": -signal * 0.1,
                    "special_teams_delta": signal * 0.2,
                    "travel_distance_miles": float(i % 500),
                    "weather_wind_mph": 8.0,
                    "weather_precip_probability": 0.1,
                    "neutral_site": 0.0,
                },
            )
        )
    return rows


def test_insufficient_rows_fail_closed():
    with pytest.raises(trainer.NCAAFTrainingError) as exc:
        trainer.train_candidate(make_rows(50))
    assert exc.value.code == "NCAAF_TRAINING_SAMPLE_INSUFFICIENT"


def test_non_chronological_rows_fail_closed():
    rows = make_rows()
    rows[0], rows[1] = rows[1], rows[0]
    with pytest.raises(trainer.NCAAFTrainingError) as exc:
        trainer.train_candidate(rows)
    assert exc.value.code == "NCAAF_TRAINING_ROWS_NOT_CHRONOLOGICAL"


def test_post_kickoff_feature_snapshot_is_rejected():
    rows = make_rows()
    bad = rows[10]
    rows[10] = trainer.TrainingRow(
        event_start_time=bad.event_start_time,
        feature_as_of=bad.event_start_time,
        home_won=bad.home_won,
        features=bad.features,
    )
    with pytest.raises(trainer.NCAAFTrainingError) as exc:
        trainer.train_candidate(rows)
    assert exc.value.code == "NCAAF_TRAINING_LEAKAGE_DETECTED"


def test_candidate_artifact_is_research_only_and_beats_naive_on_clear_signal():
    artifact = trainer.train_candidate(make_rows())
    assert artifact.model_family == "NCAAF_LOGISTIC_V1"
    assert artifact.probability_publishable is False
    assert artifact.can_execute is False
    assert len(artifact.dataset_hash) == 64
    assert artifact.validation_metrics.validation_n >= 40
    assert trainer.candidate_beats_baseline(artifact) is True
    assert trainer.CAN_EXECUTE is False
