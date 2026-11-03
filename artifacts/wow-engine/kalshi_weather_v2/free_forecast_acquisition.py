"""Official hourly forecasts with optional, nonblocking corroboration.

These are predictive feeds, never settlement authority. Missing secondary
guidance remains missing: the fusion layer keeps evidence incomplete.
"""
from dataclasses import dataclass
from typing import Sequence
from urllib.parse import urlsplit

from .http_client import HttpAcquisitionError
from .source_adapters import JsonGetter, NwsAdapter, OpenMeteoAdapter, ProviderSnapshot


@dataclass(frozen=True)
class HourlyForecastSources:
    point: ProviderSnapshot
    nws: ProviderSnapshot
    secondary: ProviderSnapshot | None
    warnings: tuple[str, ...]


def acquire_hourly_forecasts(
    get_json: JsonGetter, *, latitude: float, longitude: float,
    target_date: str, models: Sequence[str], retrieved_at: str,
    secondary_enabled: bool = False,
) -> HourlyForecastSources:
    """Require NWS; preserve it when optional Open-Meteo is unavailable."""
    adapter = NwsAdapter(get_json)
    point = adapter.point_metadata(latitude, longitude, retrieved_at=retrieved_at)
    props = point.payload.get('properties')
    url = str(props.get('forecastHourly') or '') if isinstance(props, dict) else ''
    parsed = urlsplit(url)
    if (parsed.scheme != 'https' or parsed.netloc != 'api.weather.gov'
            or not parsed.path.startswith('/gridpoints/')
            or not parsed.path.endswith('/forecast/hourly')
            or parsed.query or parsed.fragment):
        raise ValueError('NWS_FORECAST_URL_INVALID')
    nws = adapter.hourly_forecast(url, retrieved_at=retrieved_at)
    if not secondary_enabled:
        return HourlyForecastSources(point, nws, None, ('SECONDARY_FORECAST_DISABLED',))
    try:
        secondary = OpenMeteoAdapter(get_json).multi_model_hourly_temperatures(
            latitude, longitude, target_date, target_date, models,
            retrieved_at=retrieved_at,
        )
    except HttpAcquisitionError as exc:
        return HourlyForecastSources(point, nws, None,
            (f'SECONDARY_FORECAST_UNAVAILABLE:{exc.code}',))
    return HourlyForecastSources(point, nws, secondary, ())
