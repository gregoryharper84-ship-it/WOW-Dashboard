from pathlib import Path

import pytest

from kalshi_weather_v2.calibration_fit import ForecastResidual
from kalshi_weather_v2.forward_validation import (
    MAXIMUM_HOLDOUT_ABSOLUTE_MEAN_ERROR_F,
    MAXIMUM_HOLDOUT_MAE_F,
    MAXIMUM_ONE_SIGMA_COVERAGE,
    MINIMUM_HOLDOUT_RESIDUALS,
    MINIMUM_ONE_SIGMA_COVERAGE,
    MINIMUM_TOTAL_RESIDUALS,
    MINIMUM_TRAIN_RESIDUALS,
    build_forward_validation_report,
    build_forward_validation_scorecard,
)

STATION = "KALSHI_WEATHER_INDEX:MIAMI"
LANE = "HOURLY_TEMPERATURE"
BUCKET = "H0"


def _residual(value: float, *, settle_day: int, decision_day: int | None = None) -> ForecastResidual:
    decision = settle_day - 1 if decision_day is None else decision_day
    return ForecastResidual(
        forecast_value_f=value,
        settled_value_f=0.0,
        forecast_as_of=f"2026-10-{decision:02d}T00:00:00Z",
        settlement_time=f"2026-10-{settle_day:02d}T00:00:00Z",
    )


def _scorecard(residuals):
    return build_forward_validation_scorecard(
        residuals,
        station_id=STATION,
        lane=LANE,
        lead_time_bucket=BUCKET,
    )


def _passing_residuals() -> list[ForecastResidual]:
    # Train: mean 0, sigma ~0.5085. Holdout: 13/20 within 1 sigma, MAE 0.85.
    train = [_residual(0.5, settle_day=d) for d in range(1, 16)]
    train += [_residual(-0.5, settle_day=d) for d in range(16, 31)]
    holdout = [_residual(0.5, settle_day=d) for d in range(31, 44)]
    holdout += [_residual(-1.5, settle_day=d) for d in range(44, 51)]
    return train + holdout


def _gate(card, name):
    return next(gate for gate in card["gates"] if gate["gate"] == name)


def test_below_total_minimum_is_insufficient_and_never_passes():
    card = _scorecard(_passing_residuals()[: MINIMUM_TOTAL_RESIDUALS - 1])
    assert card["status"] == "FORWARD_VALIDATION_INSUFFICIENT"
    assert card["residual_gates_passed"] is False
    assert card["holdout"]["mae_f"] is None
    assert _gate(card, "TOTAL_RESIDUALS")["passed"] is False


def test_precommitted_thresholds_pass_exactly_at_the_boundary():
    card = _scorecard(_passing_residuals())
    assert card["counts"]["total"] == MINIMUM_TOTAL_RESIDUALS
    assert card["counts"]["train"] >= MINIMUM_TRAIN_RESIDUALS
    assert card["counts"]["holdout"] >= MINIMUM_HOLDOUT_RESIDUALS
    assert card["status"] == "FORWARD_VALIDATION_PASS_RESEARCH_ONLY"
    assert card["residual_gates_passed"] is True
    assert card["holdout"]["absolute_mean_error_f"] <= MAXIMUM_HOLDOUT_ABSOLUTE_MEAN_ERROR_F
    assert card["holdout"]["mae_f"] <= MAXIMUM_HOLDOUT_MAE_F
    assert MINIMUM_ONE_SIGMA_COVERAGE <= card["holdout"]["one_sigma_coverage"] <= MAXIMUM_ONE_SIGMA_COVERAGE


def test_chronological_split_ignores_input_order():
    residuals = _passing_residuals()
    shuffled = list(reversed(residuals))
    ordered = _scorecard(residuals)
    reversed_card = _scorecard(shuffled)
    assert reversed_card["train"]["bias_f"] == pytest.approx(ordered["train"]["bias_f"])
    assert reversed_card["holdout"]["mae_f"] == pytest.approx(ordered["holdout"]["mae_f"])
    assert reversed_card["counts"] == ordered["counts"]


def test_boundary_timestamp_ties_cannot_leak_into_training():
    train = [_residual(0.5, settle_day=d) for d in range(1, 16)]
    train += [_residual(-0.5, settle_day=d) for d in range(16, 31)]
    # Every holdout residual settles on the same day: a single boundary group.
    holdout = [_residual(0.5, settle_day=31) for _ in range(20)]
    card = _scorecard(train + holdout)
    assert card["counts"]["train"] == 30
    assert card["counts"]["holdout"] == 20
    ordered = card["train"]["bias_f"]
    assert ordered == pytest.approx(0.0)


def test_mae_gate_fails_above_the_precommitted_limit():
    train = [_residual(0.5, settle_day=d) for d in range(1, 16)]
    train += [_residual(-0.5, settle_day=d) for d in range(16, 31)]
    holdout = [_residual(3.0, settle_day=d) for d in range(31, 41)]
    holdout += [_residual(-3.0, settle_day=d) for d in range(41, 51)]
    card = _scorecard(train + holdout)
    assert card["status"] == "FORWARD_VALIDATION_FAILED"
    assert _gate(card, "HOLDOUT_MAE_F")["passed"] is False
    assert card["holdout"]["mae_f"] > MAXIMUM_HOLDOUT_MAE_F


def test_bias_gate_fails_above_the_precommitted_limit():
    train = [_residual(0.5, settle_day=d) for d in range(1, 16)]
    train += [_residual(-0.5, settle_day=d) for d in range(16, 31)]
    holdout = [_residual(1.2, settle_day=d) for d in range(31, 51)]
    card = _scorecard(train + holdout)
    assert _gate(card, "HOLDOUT_ABSOLUTE_MEAN_ERROR_F")["passed"] is False
    assert card["holdout"]["absolute_mean_error_f"] > MAXIMUM_HOLDOUT_ABSOLUTE_MEAN_ERROR_F


def test_one_sigma_coverage_rejects_too_narrow_an_interval():
    train = [_residual(0.5, settle_day=d) for d in range(1, 16)]
    train += [_residual(-0.5, settle_day=d) for d in range(16, 31)]
    holdout = [_residual(2.0, settle_day=d) for d in range(31, 51)]
    card = _scorecard(train + holdout)
    assert card["holdout"]["one_sigma_coverage"] == 0.0
    assert _gate(card, "ONE_SIGMA_COVERAGE")["passed"] is False


def test_one_sigma_coverage_rejects_too_wide_an_interval():
    train = [_residual(0.5, settle_day=d) for d in range(1, 16)]
    train += [_residual(-0.5, settle_day=d) for d in range(16, 31)]
    holdout = [_residual(0.1, settle_day=d) for d in range(31, 51)]
    card = _scorecard(train + holdout)
    assert card["holdout"]["one_sigma_coverage"] == 1.0
    assert _gate(card, "ONE_SIGMA_COVERAGE")["passed"] is False


def test_lookahead_rows_are_excluded_and_block_the_scorecard():
    rows = _passing_residuals()
    rows.append(
        ForecastResidual(
            forecast_value_f=0.5,
            settled_value_f=0.0,
            forecast_as_of="2026-10-05T00:00:00Z",
            settlement_time="2026-10-01T00:00:00Z",
        )
    )
    card = _scorecard(rows)
    assert card["counts"]["excluded_lookahead"] == 1
    assert card["counts"]["total"] == MINIMUM_TOTAL_RESIDUALS
    assert card["status"] == "FORWARD_VALIDATION_BLOCKED"
    assert "LOOKAHEAD_RESIDUALS_EXCLUDED:1" in card["blockers"]


def test_zero_train_variance_fails_closed():
    residuals = [_residual(0.0, settle_day=d) for d in range(1, 51)]
    card = _scorecard(residuals)
    assert card["status"] == "FORWARD_VALIDATION_BLOCKED"
    assert "TRAIN_RESIDUAL_VARIANCE_ZERO" in card["blockers"]
    assert card["holdout"]["mae_f"] is None


def test_a_passing_scorecard_still_cannot_certify_or_publish():
    card = _scorecard(_passing_residuals())
    assert card["residual_gates_passed"] is True
    assert card["certification_eligible"] is False
    assert "PROBABILITY_LEVEL_ACCEPTANCE_REQUIRED" in card["certification_blockers"]
    assert card["probability_publishable"] is False
    assert card["capability_promotion_allowed"] is False
    assert card["can_execute"] is False


def test_thresholds_are_the_precommitted_documented_values():
    text = Path(__file__).with_name("EMPIRICAL_ACCEPTANCE_THRESHOLDS.md").read_text(encoding="utf-8")
    assert f"{MINIMUM_TOTAL_RESIDUALS} unique weather-target residuals total" in text
    assert f"{MINIMUM_TRAIN_RESIDUALS} chronologically earlier training residuals" in text
    assert f"{MINIMUM_HOLDOUT_RESIDUALS} chronologically later holdout residuals" in text
    assert f"<= {MAXIMUM_HOLDOUT_ABSOLUTE_MEAN_ERROR_F}°F" in text
    assert f"<= {MAXIMUM_HOLDOUT_MAE_F}°F" in text
    assert (
        f"between {int(MINIMUM_ONE_SIGMA_COVERAGE * 100)}% and "
        f"{int(MAXIMUM_ONE_SIGMA_COVERAGE * 100)}%"
    ) in text


def _prediction(prediction_id: str, decided: str, target: str, *, threshold: int, residual_value: float):
    return {
        "prediction_id": prediction_id,
        "central_estimate_f": residual_value,
        "decision_time": decided,
        "model_payload": {
            "lead_time_bucket": BUCKET,
            "contract": {
                "lane": LANE,
                "settlement_location_code": STATION,
                "observation_window": f"POINT_IN_TIME:{target}",
            },
        },
    }


def _outcome(prediction_id: str, settled: str):
    return {
        "prediction_id": prediction_id,
        "settled_value": 0.0,
        "settled_at": settled,
    }


def test_report_scores_one_group_per_station_lane_and_bucket():
    predictions = []
    outcomes = []
    for index in range(MINIMUM_TOTAL_RESIDUALS):
        day = index // 2 + 1
        target = f"2026-10-{day:02d}T{12 if index % 2 else 13:02d}:00:00Z"
        prediction_id = f"pred-{index}"
        predictions.append(
            _prediction(prediction_id, f"2026-10-{day:02d}T00:00:00Z", target, threshold=70, residual_value=0.0)
        )
        outcomes.append(_outcome(prediction_id, f"2026-10-{day:02d}T23:00:00Z"))
    report = build_forward_validation_report(predictions, outcomes)
    assert report["schema"] == "KALSHI_WEATHER_FORWARD_VALIDATION_REPORT_V1"
    assert len(report["groups"]) == 1
    assert report["groups"][0]["counts"]["total"] == MINIMUM_TOTAL_RESIDUALS
    assert report["certification_eligible"] is False
    assert report["probability_publishable"] is False
    assert report["can_execute"] is False


def test_report_with_no_settled_rows_reports_nothing_and_publishes_nothing():
    report = build_forward_validation_report([], [])
    assert report["groups"] == []
    assert report["groups_passing_residual_gates"] == 0
    assert report["capability_promotion_allowed"] is False


def test_forward_validation_route_is_installed_on_the_authenticated_surface():
    from fastapi import Depends, FastAPI

    from kalshi_weather_v2.routes import install_kalshi_weather_v2_routes

    def _auth() -> None:
        return None

    app = FastAPI()
    install_kalshi_weather_v2_routes(
        app,
        auth_dependency=Depends(_auth),
        db_client_fn=lambda: object(),
    )
    paths = {route.path for route in app.routes}
    assert "/internal/kalshi-weather/v17/forward-validation" in paths
    assert "/internal/kalshi-weather/v17/calibration-candidates" in paths
    assert "/internal/kalshi-weather/v17/probability-scoring" in paths


def test_city_index_identity_is_not_treated_as_an_exact_station():
    card = _scorecard(_passing_residuals())
    assert card["residual_gates_passed"] is True
    assert card["settlement_identity"]["basis"] == "UNVERIFIED"
    assert card["settlement_identity"]["exact_station_verified"] is False
    assert "STATION_IDENTITY_UNVERIFIED" in card["settlement_identity"]["promotion_blockers"]
    assert "STATION_IDENTITY_UNVERIFIED" in card["certification_blockers"]
    assert card["probability_publishable"] is False


def test_explicit_settlement_station_clears_the_identity_blocker():
    card = build_forward_validation_scorecard(
        _passing_residuals(),
        station_id=STATION,
        lane=LANE,
        lead_time_bucket=BUCKET,
        settlement_identity_basis="SETTLEMENT_STATION",
    )
    assert card["settlement_identity"]["exact_station_verified"] is True
    assert card["settlement_identity"]["promotion_blockers"] == []
    assert "STATION_IDENTITY_UNVERIFIED" not in card["certification_blockers"]


def test_report_labels_city_index_contracts_without_a_physical_station():
    report = build_forward_validation_report(
        [_prediction("p1", "2026-10-01T00:00:00Z", "2026-10-01T12:00:00Z", threshold=70, residual_value=0.0)],
        [_outcome("p1", "2026-10-01T23:00:00Z")],
    )
    group = report["groups"][0]
    assert group["settlement_identity"]["basis"] == "INDEX_CITY"
    assert group["settlement_identity"]["exact_station_verified"] is False
    assert report["station_identity_verified_groups"] == 0
    assert report["station_identity_blocked_groups"] == 1


def test_settlement_identity_distinguishes_index_city_from_station():
    from kalshi_weather_v2.calibration_registry import settlement_identity

    city, city_basis = settlement_identity({"settlement_location_code": "KALSHI_WEATHER_INDEX:MIAMI"})
    assert city == "KALSHI_WEATHER_INDEX:MIAMI"
    assert city_basis == "INDEX_CITY"

    station, station_basis = settlement_identity(
        {"settlement_location_code": "KALSHI_WEATHER_INDEX:MIAMI", "settlement_station_id": "KMIA"}
    )
    assert station == "KALSHI_WEATHER_INDEX:MIAMI"
    assert station_basis == "SETTLEMENT_STATION"
