import inspect

import kalshi_weather_v2.market_microstructure_recorder as recorder
from kalshi_weather_v2.runtime import capture_hourly_shadow


def test_settlement_twin_is_persisted_before_prediction_write():
    source = inspect.getsource(capture_hourly_shadow)
    assert "persist_settlement_twin" in source
    assert "persist_prediction" in source
    assert source.index("persist_settlement_twin") < source.index("persist_prediction")


def test_market_recorder_has_no_weather_probability_dependency():
    source = inspect.getsource(recorder)
    prohibited = (
        "WeatherProbabilityCore",
        "probability_core",
        "persist_prediction",
        "probability_publishable=True",
        "can_execute=True",
    )
    assert not any(token in source for token in prohibited)
