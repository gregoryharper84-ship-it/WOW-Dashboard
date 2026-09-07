from __future__ import annotations

from copy import deepcopy

import pytest

from kalshi_engine.weather_v17.adapter import package_to_legacy_candidate
from kalshi_engine.weather_v17.core import WeatherV17Engine


def _payload():
    return {
        "settlement_identity": {
            "city": "Dallas",
            "station_id": "KDFW",
            "settlement_station_verified": True,
            "settlement_source": "OFFICIAL_CLIMATE_PRODUCT",
        },
        "forecast_snapshots": [
            {
                "station_id": "KDFW",
                "source_family": "OFFICIAL_GRIDPOINT",
                "model_name": "NWS",
                "forecast_high_f": 94.0,
                "source_quality": .95,
                "retrieved_at": "2026-09-06T16:00:00Z",
            }
        ],
        "station_error_profiles": [],
        "regimes": [],
        "contract": {"kind": "AT_LEAST", "threshold_f": 94, "side": "YES"},
        # Synthetic unit-test calibration only. This is not replay evidence and
        # must not be interpreted as production certification evidence.
        "calibration": {
            "method": "AFFINE",
            "sample_size": 120,
            "slope": 1.0,
            "intercept": 0.0,
            "lower_bound": 0.0,
            "upper_bound": 1.0,
        },
        "scored_at": "2026-09-06T18:00:00Z",
    }


def test_adapter_overwrites_stale_legacy_execution_flag():
    package = WeatherV17Engine().score(_payload())
    candidate = package_to_legacy_candidate(package, {"can_execute": True, "yes_price": .55})
    assert candidate["can_execute"] is False
    assert candidate["DRY_RUN_ONLY_NO_LIVE_TRADING_NO_MARKET_ORDERS"] is True
    assert candidate["yes_price"] == .55
    assert candidate["weather_v17_probability_package"] is package


def test_adapter_rejects_package_with_execution_enabled():
    package = WeatherV17Engine().score(_payload())
    malformed = deepcopy(package)
    malformed["can_execute"] = True
    with pytest.raises(ValueError, match="WEATHER_V17_EXECUTION_GUARD_VIOLATION"):
        package_to_legacy_candidate(malformed, {})


def test_adapter_rejects_package_missing_execution_guard():
    package = WeatherV17Engine().score(_payload())
    malformed = deepcopy(package)
    malformed.pop("can_execute")
    with pytest.raises(ValueError, match="WEATHER_V17_EXECUTION_GUARD_VIOLATION"):
        package_to_legacy_candidate(malformed, {})
