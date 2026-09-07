from __future__ import annotations

import os
import uuid

import pytest

from kalshi_engine import weather_gate
from kalshi_engine.weather_v17.ledger import append_outcome, append_prediction
from kalshi_engine.weather_v17.registry import resolve_station, supported_stations, validate_station


def _market_candidate():
    return {
        "confidence_tier": "WEATHER_MODEL_READY",
        "forecast_horizon_hours": 6,
        "sigma_f": 2.0,
        "settlement_station_verified": True,
        "nws_gridpoint_available": True,
        "bracket_coverage_complete": True,
        "probability_normalization_pass": True,
        "market_open": True,
        "orderbook_nonempty": True,
        "price_age_minutes": 2,
        "edge_lower_bound": .05,
        "portfolio_check_passed": True,
    }


def test_registry_contains_only_verified_seed_rows():
    rows = supported_stations()
    assert rows["CHI"]["station_id"] == "KMDW"
    assert rows["MIA"]["station_id"] == "KMIA"
    assert rows["LA"]["station_id"] == "KLAX"
    assert all(v["verified"] for v in rows.values())


@pytest.mark.parametrize("code,bad", [("CHI","KORD"),("MIA","KPBI"),("LA","KBUR")])
def test_registry_rejects_known_bad_station_substitutions(code, bad):
    with pytest.raises(ValueError, match="SETTLEMENT_STATION_REGRESSION_BANNED"):
        validate_station(code, bad)


def test_registry_rejects_series_station_mismatch():
    with pytest.raises(ValueError, match="WEATHER_SERIES_STATION_MISMATCH"):
        resolve_station("CHI", "KXHIGHMIA")


def test_legacy_weather_gate_is_explicitly_not_governed_probability():
    out = weather_gate.check(_market_candidate())
    assert out["passed"] is True
    assert out["probability_governance_status"] == "LEGACY_RESEARCH_ONLY"
    assert out["governed_probability_eligible"] is False


def test_v17_weather_gate_uses_model_pmf_not_yes_price_sum():
    c = _market_candidate()
    c["probability_normalization_pass"] = False  # legacy market-price flag must not control model normalization
    c["weather_v17_probability_package"] = {
        "probability_status": "COMPLETED",
        "calibration_status": "CALIBRATED",
        "calibrated_lower_bound": .60,
        "final_high_pmf": {"93": .2, "94": .3, "95": .5},
    }
    out = weather_gate.check(c)
    assert out["passed"] is True
    assert out["probability_governance_status"] == "V17_GOVERNED"
    assert out["governed_probability_eligible"] is True


def test_v17_weather_gate_rejects_bad_model_pmf_even_if_market_prices_look_normalized():
    c = _market_candidate()
    c["weather_v17_probability_package"] = {
        "probability_status": "COMPLETED",
        "calibration_status": "CALIBRATED",
        "calibrated_lower_bound": .60,
        "final_high_pmf": {"93": .2, "94": .3},
    }
    out = weather_gate.check(c)
    assert out["passed"] is False
    assert out["failure_category"] == "MODEL_PMF_NORMALIZATION_FAIL"


@pytest.mark.skipif(not os.environ.get("DATABASE_URL"), reason="DATABASE_URL required for immutable ledger integration")
def test_prediction_and_outcome_are_append_only():
    pid = f"weather-v17-test-{uuid.uuid4()}"
    package = {
        "probability_status": "COMPLETED",
        "can_execute": False,
        "station_id": "KMDW",
        "contract": {"kind": "BRACKET", "lower_f": 80, "upper_f": 81, "side": "YES"},
        "raw_probability": .62,
        "calibrated_probability": .60,
        "calibrated_lower_bound": .55,
        "calibrated_upper_bound": .66,
        "final_high_pmf": {"80": .4, "81": .6},
        "regime_probabilities": {"CLEAR_MIXING": 1.0},
        "component_models": [],
        "calibration_status": "CALIBRATED",
    }
    first = append_prediction({"prediction_id": pid, "event_key": "CHI-NHIGH-TEST", "city_code": "CHI", "series": "KXHIGHCHI", "settlement_date": "2026-09-07", "package": package})
    assert first["ok"] is True
    with pytest.raises(ValueError, match="WEATHER_PREDICTION_ID_ALREADY_EXISTS"):
        append_prediction({"prediction_id": pid, "event_key": "CHI-NHIGH-TEST", "city_code": "CHI", "series": "KXHIGHCHI", "settlement_date": "2026-09-07", "package": package})
    settled = append_outcome({"prediction_id": pid, "official_final_high_f": 81, "contract_result": "YES", "settlement_source": "OFFICIAL_CLIMATE_PRODUCT"})
    assert settled["ok"] is True
    assert settled["brier_score"] == pytest.approx((.60 - 1.0) ** 2)
    with pytest.raises(ValueError, match="WEATHER_OUTCOME_ALREADY_EXISTS"):
        append_outcome({"prediction_id": pid, "official_final_high_f": 81, "contract_result": "YES", "settlement_source": "OFFICIAL_CLIMATE_PRODUCT"})
