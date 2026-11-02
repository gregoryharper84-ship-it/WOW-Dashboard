"""Dataset-integrity regressions: exclusions are not weather duplicates."""
import kalshi_weather_v2.shadow_cohort as cohort


def prediction(pid, estimate, decision):
    return {
        "prediction_id": pid,
        "central_estimate_f": estimate,
        "decision_time": decision,
        "model_payload": {
            "lead_time_bucket": "H0",
            "contract": {
                "lane": "HOURLY_TEMPERATURE",
                "settlement_location_code": "KALSHI_WEATHER_INDEX:miami",
                "observation_window": "POINT_IN_TIME:2026-11-02T14:00:00Z",
            },
        },
    }


def check(monkeypatch, predictions):
    outcomes = [
        {
            "prediction_id": p["prediction_id"],
            "settled_value": 82.0,
            "settled_at": "2026-11-02T16:00:00Z",
        }
        for p in predictions
    ]
    monkeypatch.setattr(
        cohort,
        "_strict_calibration_rows",
        lambda client, table, *, limit: predictions
        if table.endswith("predictions") else outcomes,
    )
    return cohort.build_calibration_report(client=object())["dataset_integrity"]


def test_settled_prediction_without_forecast_is_excluded_not_duplicate(monkeypatch):
    rows = [
        prediction("usable", 80.0, "2026-11-02T13:00:00Z"),
        prediction("missing", None, "2026-11-02T13:05:00Z"),
    ]
    result = check(monkeypatch, rows)
    assert result["unique_calibration_samples"] == 1
    assert result["duplicate_predictions_collapsed"] == 0
    assert result["exclusion_reasons"]["FORECAST_CENTRAL_ESTIMATE_MISSING"] == 1


def test_two_valid_siblings_are_one_unique_weather_sample(monkeypatch):
    rows = [
        prediction("first", 80.0, "2026-11-02T13:00:00Z"),
        prediction("second", 81.0, "2026-11-02T13:05:00Z"),
    ]
    result = check(monkeypatch, rows)
    assert result["unique_calibration_samples"] == 1
    assert result["duplicate_predictions_collapsed"] == 1
