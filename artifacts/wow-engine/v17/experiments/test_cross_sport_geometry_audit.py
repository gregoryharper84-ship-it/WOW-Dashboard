from __future__ import annotations

from datetime import datetime, timezone

from v17.binary_candidate_lifecycle import BinaryTrainingRow
from v17.experiments.cross_sport_geometry_audit import (
    _feature_geometry,
    _transform_binary,
)
from v17.experiments.cross_sport_geometry_audit_bundle import run_bundle


def _candidate():
    return {
        "artifact_payload": {
            "feature_names": [
                "home_games_prior",
                "away_games_prior",
                "home_season_games_prior",
                "away_season_games_prior",
                "home_rest_days",
                "away_rest_days",
            ],
            "scaler_mean": [20.0, 20.0, 8.0, 8.0, 7.0, 7.0],
            "scaler_scale": [10.0, 10.0, 5.0, 5.0, 2.0, 2.0],
        }
    }


def _rows():
    now = datetime(2026, 10, 5, tzinfo=timezone.utc)
    rows = []
    for i in range(100):
        current = i >= 80
        count = 60.0 if current else 20.0
        rows.append(
            {
                "features": {
                    "home_games_prior": count,
                    "away_games_prior": count + (1 if current else 0),
                    "home_season_games_prior": 12.0,
                    "away_season_games_prior": 12.0,
                    "home_rest_days": 7.0,
                    "away_rest_days": 7.0,
                },
                "event_start_time": now,
            }
        )
    return rows


def test_geometry_flags_only_severe_raw_cumulative_features():
    geometry = _feature_geometry(_candidate(), _rows())
    assert geometry["status"] == "PASS"

    by_name = {row["feature"]: row for row in geometry["features"]}
    assert by_name["home_games_prior"]["gt3_pct"] == 1.0
    assert by_name["home_games_prior"]["severe"] is True
    assert by_name["away_games_prior"]["severe"] is True

    # Season-to-date counts are inspected but are not automatically classified
    # as the raw cross-season cumulative defect.
    assert by_name["home_season_games_prior"]["severe"] is False


def test_stationary_transform_preserves_schema_and_caps_drift():
    rows = [
        BinaryTrainingRow(
            event_id="a",
            event_start_time="2026-10-05T12:00:00+00:00",
            feature_as_of="2026-10-05T11:59:59+00:00",
            positive_outcome=True,
            features={
                "home_games_prior": 88.0,
                "away_games_prior": 92.0,
                "home_games_since_structural_change": 41.0,
                "away_games_since_structural_change": 3.0,
                "home_rest_days": 8.0,
            },
            source_manifest_sha256="a" * 64,
        )
    ]
    severe = {
        "home_games_prior",
        "away_games_prior",
        "home_games_since_structural_change",
        "away_games_since_structural_change",
    }

    transformed, names = _transform_binary(rows, severe, "stationary")
    features = transformed[0].features

    assert names == tuple(sorted(rows[0].features))
    assert features["home_games_prior"] == 1.0
    assert features["away_games_prior"] == 1.0
    assert features["home_games_since_structural_change"] == 10.0
    assert features["away_games_since_structural_change"] == 3.0
    assert features["home_rest_days"] == 8.0
    assert rows[0].features["home_games_prior"] == 88.0


def test_drop_transform_removes_only_named_severe_features():
    rows = [
        BinaryTrainingRow(
            event_id="a",
            event_start_time="2026-10-05T12:00:00+00:00",
            feature_as_of="2026-10-05T11:59:59+00:00",
            positive_outcome=False,
            features={
                "away_games_prior": 90.0,
                "home_games_prior": 90.0,
                "home_rest_days": 7.0,
            },
            source_manifest_sha256="b" * 64,
        )
    ]

    transformed, names = _transform_binary(
        rows,
        {"home_games_prior", "away_games_prior"},
        "drop",
    )

    assert transformed == rows
    assert names == ("home_rest_days",)


def test_oidc_bundle_rejects_executable_payload():
    import pytest

    with pytest.raises(RuntimeError, match="BUNDLE_GOVERNANCE_INVALID"):
        run_bundle({"lanes": [], "can_execute": True})


def test_empty_oidc_bundle_fails_closed_for_every_cataloged_sport():
    report = run_bundle(
        {
            "source": "TEST",
            "lanes": [],
            "probability_publishable": False,
            "automatic_promotion": False,
            "can_execute": False,
        }
    )

    assert report["can_execute"] is False
    assert report["probability_publishable"] is False
    assert report["automatic_promotion"] is False
    assert len(report["sports"]) == 13
    assert all(
        row["status"] == "BLOCKED_WITH_EXACT_REASON"
        and row["blocker"] == "D1_CANDIDATE_ARTIFACT_MISSING"
        and row["can_execute"] is False
        for row in report["sports"].values()
    )
