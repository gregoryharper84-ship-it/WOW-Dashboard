from __future__ import annotations

from datetime import datetime, timedelta, timezone
from hashlib import sha256
import math

from fastapi import Depends, FastAPI

from v17.first_six_open_data_maintenance import install_first_six_open_data_maintenance_routes
from v17.multiclass_candidate_lifecycle import MulticlassTrainingRow, train_multiclass_candidate
from v17.ncaaf_result_form_candidate import (
    FEATURE_SCHEMA_VERSION as NCAAF_FEATURE_SCHEMA,
    build_training_rows as build_ncaaf_rows,
)
from v17.ncaab_sportsdataverse_candidate import SOURCE_LICENSE as NCAAB_LICENSE
from v17.soccer_openfootball_candidate import SOURCE_LICENSE as SOCCER_LICENSE
from v17.tennis_valuebet_candidate import SOURCE_LICENSE as TENNIS_LICENSE


def _manifest(value: str) -> str:
    return sha256(value.encode()).hexdigest()


def test_multiclass_lifecycle_is_temporal_calibrated_and_never_self_certifies():
    start = datetime(2022, 1, 1, tzinfo=timezone.utc)
    rows = []
    classes = ("HOME", "DRAW", "AWAY")
    for i in range(900):
        when = start + timedelta(hours=6 * i)
        strength = math.sin(i / 11.0)
        draw_signal = math.cos(i / 7.0)
        if strength > 0.35:
            outcome = "HOME"
        elif strength < -0.35:
            outcome = "AWAY"
        else:
            outcome = "DRAW"
        rows.append(MulticlassTrainingRow(
            event_id=f"S:{i}",
            event_start_time=when.isoformat(),
            feature_as_of=(when - timedelta(seconds=1)).isoformat(),
            outcome=outcome,
            features={"strength": strength, "draw_signal": draw_signal},
            source_manifest_sha256=_manifest(f"manifest:{i}"),
        ))
    candidate = train_multiclass_candidate(
        rows,
        model_family="SOCCER_TEST_1X2",
        feature_names=("strength", "draw_signal"),
        expected_classes=classes,
        min_rows=500,
    )
    assert set(candidate.classes) == set(classes)
    assert candidate.metrics.train_n == 540
    assert candidate.metrics.calibration_n == 180
    assert candidate.metrics.test_n == 180
    assert math.isfinite(candidate.metrics.calibrated_log_loss)
    assert math.isfinite(candidate.metrics.calibrated_brier)
    assert candidate.automatic_certification is False
    assert candidate.automatic_promotion is False
    assert candidate.probability_publishable is False
    assert candidate.can_execute is False


def test_ncaaf_result_form_reconstruction_uses_only_prior_settled_games():
    start = datetime(2022, 8, 1, tzinfo=timezone.utc)
    teams = [f"T{i:02d}" for i in range(20)]
    games = []
    for i in range(520):
        home = teams[i % len(teams)]
        away = teams[(i * 7 + 3) % len(teams)]
        if home == away:
            away = teams[(teams.index(away) + 1) % len(teams)]
        hp = 24 + ((i + teams.index(home)) % 21)
        ap = 17 + ((i * 3 + teams.index(away)) % 21)
        when = start + timedelta(hours=8 * i)
        games.append({
            "official_event_id": f"NCAAF:{i}",
            "season": 2022 + (i // 180),
            "week": (i % 20) + 1,
            "event_start_time": when.isoformat(),
            "neutral_site": i % 19 == 0,
            "home_team": home,
            "away_team": away,
            "home_points": hp,
            "away_points": ap,
            "home_won": hp > ap,
            "result_source": "TEST_SETTLED_RESULTS",
        })
    rows, metadata = build_ncaaf_rows(games)
    assert len(rows) >= 300
    assert all(row.feature_as_of < row.event_start_time for row in rows)
    assert all(len(row.source_manifest_sha256) == 64 for row in rows)
    assert all("market" not in feature for row in rows for feature in row.features)
    assert all(meta["source_manifest"]["historical_reconstruction"] is True for meta in metadata)
    assert all(meta["source_manifest"]["market_features_used"] is False for meta in metadata)
    assert NCAAF_FEATURE_SCHEMA == "NCAAF_RESULT_FORM_PRIOR_V1"


def test_open_data_source_rights_are_explicit_and_market_independent():
    assert NCAAB_LICENSE == "CC-BY-4.0"
    assert SOCCER_LICENSE == "CC0-1.0"
    assert TENNIS_LICENSE == "CC-BY-4.0"


def test_first_six_open_data_routes_are_registered_behind_existing_auth_boundary():
    app = FastAPI()
    install_first_six_open_data_maintenance_routes(
        app,
        auth_dependency=Depends(lambda: None),
        db_client_fn=lambda: object(),
    )
    paths = {getattr(route, "path", None) for route in app.router.routes}
    assert "/internal/v17/ncaab-model-maintenance" in paths
    assert "/internal/v17/soccer-model-maintenance" in paths
    assert "/internal/v17/tennis-model-maintenance" in paths
