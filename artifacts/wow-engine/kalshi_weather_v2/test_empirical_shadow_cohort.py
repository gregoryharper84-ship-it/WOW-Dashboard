from __future__ import annotations

from types import SimpleNamespace

from kalshi_weather_v2.free_public_sources import source_registry_snapshot
from kalshi_weather_v2.market_discovery import KalshiWeatherMarketDiscovery
from kalshi_weather_v2.shadow_cohort import build_calibration_report, cohort_sample_key
from kalshi_weather_v2.supplemental_source_adapters import (
    MetNorwayAdapter,
    NceiAccessDataAdapter,
    OpenMeteoEnsembleAdapter,
)


class _Query:
    def __init__(self, rows):
        self.rows = rows

    def select(self, *_args, **_kwargs):
        return self

    def limit(self, *_args, **_kwargs):
        return self

    def execute(self):
        return SimpleNamespace(data=self.rows)


class _Client:
    def __init__(self, tables):
        self.tables = tables

    def table(self, name):
        return _Query(self.tables.get(name, []))


def test_free_source_registry_preserves_authority_roles():
    registry = source_registry_snapshot()
    assert registry["KALSHI_PUBLIC"]["role"] == "CONTRACT_MARKET_AND_SETTLEMENT_AUTHORITY"
    assert registry["NWS_API"]["priority"] == "B"
    assert registry["OPEN_METEO_ENSEMBLE"]["role"] == "UNCERTAINTY_ENSEMBLE"
    assert registry["NOAA_NCEI_ADS"]["role"] == "HISTORICAL_STATION_CALIBRATION"
    assert registry["IEM_ASOS"]["role"] == "ASOS_ARCHIVE_CORROBORATION"
    assert registry["NOAA_NOMADS"]["live_default"] is False


def test_cohort_sample_key_deduplicates_threshold_siblings():
    a = cohort_sample_key("miami", "2026-09-12T18:00:00Z", "H3_5")
    b = cohort_sample_key("MIAMI", "2026-09-12T18:00:00+00:00", "h3_5")
    c = cohort_sample_key("miami", "2026-09-12T18:00:00Z", "H2")
    assert a == b
    assert a != c


def test_calibration_report_counts_one_residual_per_weather_target_bucket():
    predictions = []
    outcomes = []
    for index in range(40):
        target = f"2026-08-{(index % 28) + 1:02d}T{(index % 20):02d}:00:00Z"
        decision = f"2026-07-{(index % 28) + 1:02d}T00:00:00Z"
        settled = f"2026-09-{(index % 9) + 1:02d}T00:00:00Z"
        settled_value = 80.0 + (index % 7)
        forecast_value = settled_value + (-1.0 if index % 2 else 1.5)
        for sibling in range(2):
            prediction_id = f"p-{index}-{sibling}"
            predictions.append(
                {
                    "prediction_id": prediction_id,
                    "decision_time": decision,
                    "central_estimate_f": forecast_value,
                    "model_payload": {
                        "lead_time_bucket": "H6_12",
                        "contract": {
                            "lane": "HOURLY_TEMPERATURE",
                            "settlement_location_code": "KALSHI_WEATHER_INDEX:miami",
                            "observation_window": f"POINT_IN_TIME:{target}",
                        },
                    },
                }
            )
            outcomes.append(
                {
                    "prediction_id": prediction_id,
                    "settled_at": settled,
                    "settled_value": settled_value,
                }
            )

    report = build_calibration_report(
        client=_Client(
            {
                "wow_kalshi_weather_predictions": predictions,
                "wow_kalshi_weather_outcomes": outcomes,
            }
        )
    )
    assert len(report["groups"]) == 1
    group = report["groups"][0]
    assert group["unique_sample_n"] == 40
    assert group["candidate_profile"] is not None
    assert group["forward_validation"]["status"] == "FORWARD_VALIDATION_REPORTED"
    assert group["certification_ready"] is False
    assert report["capability_promotion_allowed"] is False


def test_market_discovery_filters_location_and_temperature_then_paginates_markets():
    calls = []

    def get_json(url, _headers):
        calls.append(url)
        if "/series?" in url:
            return {
                "series": [
                    {"ticker": "TEMP-MIA", "title": "Hourly temperature in Miami", "category": "Climate", "tags": ["weather"], "product_metadata": {}},
                    {"ticker": "RAIN-MIA", "title": "Rain in Miami", "category": "Climate", "tags": ["weather"], "product_metadata": {}},
                    {"ticker": "TEMP-NYC", "title": "Hourly temperature in New York City", "category": "Climate", "tags": ["weather"], "product_metadata": {}},
                ]
            }
        return {"markets": [{"ticker": "TEMP-MIA-1"}], "cursor": ""}

    discovery = KalshiWeatherMarketDiscovery(get_json)
    series = discovery.candidate_series(expected_location="Miami")
    assert [row.ticker for row in series] == ["TEMP-MIA"]
    markets = discovery.open_markets(series_ticker="TEMP-MIA")
    assert markets[0]["ticker"] == "TEMP-MIA-1"
    assert any("status=open" in url for url in calls)


def test_supplemental_adapters_remain_read_only_and_tagged_noncontrolling():
    captured = []

    def get_json(url, headers):
        captured.append((url, headers))
        if "ensemble-api" in url:
            return {"hourly": {"time": ["2026-09-12T18:00"], "temperature_2m_member01": [82.0]}}
        return {
            "properties": {
                "meta": {"updated_at": "2026-09-11T18:00:00Z"},
                "timeseries": [{"time": "2026-09-12T18:00:00Z"}],
            }
        }

    ensemble = OpenMeteoEnsembleAdapter(get_json).hourly_temperature_ensemble(
        25.7617, -80.1918, "2026-09-12", "2026-09-12", retrieved_at="2026-09-11T18:00:00Z"
    )
    metno = MetNorwayAdapter(get_json).compact_forecast(
        25.7617, -80.1918, retrieved_at="2026-09-11T18:00:00Z"
    )
    assert ensemble.role == "UNCERTAINTY_ENSEMBLE"
    assert metno.role == "TERTIARY_FORECAST_CORROBORATION"
    assert all(url.startswith("https://") for url, _ in captured)

    ncei = NceiAccessDataAdapter(lambda _url, _headers: [{"DATE": "2026-09-01", "TMAX": "91"}]).daily_summaries(
        "USW00012839", "2026-09-01", "2026-09-02", retrieved_at="2026-09-11T18:00:00Z"
    )
    assert ncei.role == "HISTORICAL_CALIBRATION"
    assert ncei.valid_times == ("2026-09-01",)
