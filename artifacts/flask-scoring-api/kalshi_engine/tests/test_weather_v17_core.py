from __future__ import annotations

from copy import deepcopy

import pytest

from kalshi_engine.weather_v17.core import WeatherV17Engine, gaussian_integer_pmf, project_contract
from kalshi_engine.weather_v17.adapter import package_to_legacy_candidate, assert_probability_unchanged
from kalshi_engine.weather_v17.learning import build_station_error_profiles, calibration_health, fit_isotonic_points
from kalshi_engine.weather_v17.snapshots import freeze_forecast_snapshot, freeze_observation_snapshot, verify_snapshot_digest
from kalshi_engine.weather_v17.replay import replay_rows


def payload():
    return {
        "settlement_identity": {"city": "Dallas", "station_id": "KDFW", "settlement_station_verified": True, "settlement_source": "OFFICIAL_CLIMATE_PRODUCT"},
        "forecast_snapshots": [
            {"station_id": "KDFW", "source_family": "OFFICIAL_GRIDPOINT", "model_name": "NWS", "forecast_high_f": 94.0, "source_quality": .95, "retrieved_at": "2026-09-06T16:00:00Z"},
            {"station_id": "KDFW", "source_family": "GLOBAL_ENSEMBLE", "model_name": "ENS_A", "forecast_high_f": 95.0, "source_quality": .9, "retrieved_at": "2026-09-06T16:00:00Z"},
        ],
        "station_error_profiles": [{"station_id": "KDFW", "model_name": "NWS", "mean_error": .5, "rmse": 1.7, "sample_size": 80}],
        "regimes": [{"name": "CLEAR_MIXING", "probability": .75, "delta_f": .5}, {"name": "CONVECTIVE_OUTFLOW_RISK", "probability": .25, "delta_f": -2.0, "extra_sigma_f": .8}],
        "contract": {"kind": "BRACKET", "lower_f": 94, "upper_f": 95, "side": "YES"},
        "calibration": {"method": "AFFINE", "sample_size": 120, "slope": .98, "intercept": .01, "lower_bound": .35, "upper_bound": .75},
        "scored_at": "2026-09-06T18:00:00Z",
    }


def test_completed_probability_package():
    out = WeatherV17Engine().score(payload())
    assert out["probability_status"] == "COMPLETED"
    assert out["model_status"] == "MODEL_QUALIFIED"
    assert out["can_execute"] is False
    assert out["terminal_authority"] == "V17_TERMINAL_REDUCER"


def test_pmf_normalizes():
    out = WeatherV17Engine().score(payload())
    assert abs(sum(out["final_high_pmf"].values()) - 1.0) < 1e-8


def test_regimes_normalize():
    out = WeatherV17Engine().score(payload())
    assert abs(sum(out["regime_probabilities"].values()) - 1.0) < 1e-12


def test_observed_max_truncates_support():
    p = payload(); p["maximum_observed_so_far_f"] = 96
    out = WeatherV17Engine().score(p)
    assert min(map(int, out["final_high_pmf"])) >= 96


def test_unverified_station_blocks():
    p = payload(); p["settlement_identity"]["settlement_station_verified"] = False
    assert "SETTLEMENT_STATION_UNVERIFIED" in WeatherV17Engine().score(p)["blockers"]

@pytest.mark.parametrize("city,station", [("Miami","KPBI"),("Los Angeles","KBUR"),("Chicago","KORD")])
def test_station_regression_bans(city, station):
    p = payload(); p["settlement_identity"].update({"city": city, "station_id": station})
    assert "SETTLEMENT_STATION_REGRESSION_BANNED" in WeatherV17Engine().score(p)["blockers"]

@pytest.mark.parametrize("key", ["market_price","yes_price","no_price","edge","payout","fee_adjusted_break_even"])
def test_market_data_cannot_enter_probability_model(key):
    p = payload(); p[key] = .50
    assert "MARKET_DATA_LEAKAGE_IN_WEATHER_MODEL_INPUT" in WeatherV17Engine().score(p)["blockers"]


def test_missing_market_data_does_not_block_probability():
    assert WeatherV17Engine().score(payload())["probability_status"] == "COMPLETED"


def test_missing_forecast_blocks_typed_input_failure():
    p = payload(); p["forecast_snapshots"] = []
    out = WeatherV17Engine().score(p)
    assert out["model_status"] == "MODEL_INPUTS_INSUFFICIENT"
    assert out["model_status"] != "MODEL_UNAVAILABLE"


def test_missing_contract_blocks():
    p = payload(); p["contract"] = {}
    assert "CONTRACT_DEFINITION_MISSING" in WeatherV17Engine().score(p)["blockers"]


def test_uncalibrated_keeps_completed_weather_probability_but_not_qualified():
    p = payload(); p.pop("calibration")
    out = WeatherV17Engine().score(p)
    assert out["probability_status"] == "COMPLETED"
    assert out["model_status"] == "RESEARCH_ONLY_CALIBRATION_REQUIRED"
    assert out["calibrated_lower_bound"] is None


def test_calibration_point_without_bounds_cannot_fake_lower_bound():
    p = payload(); p["calibration"].pop("lower_bound"); p["calibration"].pop("upper_bound")
    out = WeatherV17Engine().score(p)
    assert out["calibration_status"] == "CALIBRATION_POINT_ONLY_RESEARCH"
    assert out["calibrated_lower_bound"] is None


def test_invalid_calibration_bounds_do_not_qualify():
    p = payload(); p["calibration"].update({"lower_bound": .9, "upper_bound": .95})
    out = WeatherV17Engine().score(p)
    assert out["calibration_status"] == "CALIBRATION_BOUNDS_INVALID"
    assert out["model_status"] != "MODEL_QUALIFIED"


def test_same_family_models_do_not_get_independent_full_weight():
    p = payload(); p["forecast_snapshots"].append({"station_id":"KDFW","source_family":"OFFICIAL_GRIDPOINT","model_name":"NWS_ALT","forecast_high_f":94.2,"source_quality":.95,"retrieved_at":"2026-09-06T16:00:00Z"})
    out = WeatherV17Engine().score(p)
    nws = [x for x in out["component_models"] if x["source_family"] == "OFFICIAL_GRIDPOINT"]
    assert sum(x["weight"] for x in nws) < 1.0


def test_station_error_profile_numerically_debiases_forecast():
    out = WeatherV17Engine().score(payload())
    nws = next(x for x in out["component_models"] if x["model_name"] == "NWS")
    assert nws["mu_f"] == pytest.approx(94.5)


def test_yes_no_are_complements():
    p = payload(); yes = WeatherV17Engine().score(p)["raw_probability"]
    p2 = deepcopy(p); p2["contract"]["side"] = "NO"
    no = WeatherV17Engine().score(p2)["raw_probability"]
    assert yes + no == pytest.approx(1.0)

@pytest.mark.parametrize("kind,threshold", [("AT_LEAST",94),("ABOVE",94),("AT_MOST",95),("BELOW",95)])
def test_threshold_contracts(kind, threshold):
    pmf = {93:.2,94:.3,95:.5}
    value = project_contract(pmf, {"kind":kind,"threshold_f":threshold,"side":"YES"})
    assert 0 <= value <= 1


def test_gaussian_continuity_pmf_normalizes():
    assert sum(gaussian_integer_pmf(94.2, 2.0).values()) == pytest.approx(1.0)


def test_snapshot_freeze_and_digest():
    row = freeze_forecast_snapshot({"station_id":"KDFW","source_family":"OFFICIAL_GRIDPOINT","model_name":"NWS","forecast_high_f":94})
    assert verify_snapshot_digest(row)


def test_snapshot_tamper_is_detected():
    row = freeze_forecast_snapshot({"station_id":"KDFW","source_family":"OFFICIAL_GRIDPOINT","model_name":"NWS","forecast_high_f":94})
    row["forecast_high_f"] = 99
    assert not verify_snapshot_digest(row)


def test_observation_snapshot_contract():
    row = freeze_observation_snapshot({"station_id":"KDFW","observed_at":"2026-09-06T18:00:00Z","temperature_f":92,"maximum_observed_so_far_f":93})
    assert row["snapshot_type"] == "OFFICIAL_WEATHER_OBSERVATION"


def test_snapshot_rejects_price_leakage():
    with pytest.raises(ValueError):
        freeze_forecast_snapshot({"station_id":"KDFW","source_family":"NWS","model_name":"NWS","forecast_high_f":94,"market_price":.5})


def test_learning_builds_station_bias_profile():
    rows = [{"station_id":"KDFW","model_name":"NWS","forecast_high_f":90+i%3,"official_final_high_f":91+i%3,"forecast_horizon_hours":6} for i in range(40)]
    prof = build_station_error_profiles(rows)[0]
    assert prof["mean_error"] == pytest.approx(1.0)
    assert prof["profile_status"] == "STATION_SPECIFIC"


def test_calibration_health_metrics():
    health = calibration_health([{"calibrated_probability":.7,"outcome":"YES","calibrated_lower_bound":.6},{"calibrated_probability":.3,"outcome":"NO","calibrated_lower_bound":.2}])
    assert health["sample_size"] == 2
    assert health["brier_score"] >= 0


def test_isotonic_fit_needs_real_sample():
    out = fit_isotonic_points([{"raw_probability":.7,"outcome":"YES"}], min_samples=20)
    assert out["status"] == "CALIBRATION_SAMPLE_INSUFFICIENT"


def test_adapter_uses_model_pmf_normalization_not_kalshi_yes_prices():
    out = WeatherV17Engine().score(payload())
    legacy = package_to_legacy_candidate(out, {"yes_price": .99})
    assert legacy["probability_normalization_pass"] is True


def test_downstream_layers_cannot_mutate_probability():
    before = WeatherV17Engine().score(payload()); after = deepcopy(before); after["portfolio_status"] = "REJECT"
    assert_probability_unchanged(before, after)


def test_mutated_probability_is_detected():
    before = WeatherV17Engine().score(payload()); after = deepcopy(before); after["raw_probability"] += .01
    with pytest.raises(ValueError): assert_probability_unchanged(before, after)


def test_replay_produces_brier_and_temperature_mae():
    p = payload(); package = WeatherV17Engine().score(p)
    realized = int(round(package["distribution_summary"]["mean_f"]))
    replay = replay_rows([{"id":"x","input":p,"outcome":{"official_final_high_f":realized,"contract_yes":True}}])
    assert replay["summary"]["replay_count"] == 1
    assert replay["summary"]["mean_brier_score"] is not None
    assert replay["summary"]["can_execute"] is False


def test_probability_lane_does_not_evaluate_portfolio():
    out = WeatherV17Engine().score(payload())
    assert out["portfolio_status"] == "NOT_EVALUATED_IN_PROBABILITY_LANE"


def test_probability_lane_has_no_market_probability_or_edge():
    out = WeatherV17Engine().score(payload())
    assert out["market_probability"] is None and out["edge"] is None


def test_deterministic_given_fixed_inputs_and_scored_at():
    a, b = WeatherV17Engine().score(payload()), WeatherV17Engine().score(payload())
    assert a["final_high_pmf"] == b["final_high_pmf"]
    assert a["raw_probability"] == b["raw_probability"]
