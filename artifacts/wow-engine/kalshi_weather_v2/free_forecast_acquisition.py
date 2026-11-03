"""Official hourly forecasts with optional, nonblocking corroboration.

These are predictive feeds, never settlement authority. Missing secondary
guidance remains missing: the fusion layer keeps evidence incomplete.
"""
from dataclasses import dataclass
from datetime import datetime
from typing import Mapping, Sequence
from urllib.parse import urlsplit

from .http_client import HttpAcquisitionError
from .source_adapters import (
    JsonGetter, NwsAdapter, OpenMeteoAdapter, OpenMeteoSnapshotInvalid, ProviderSnapshot,
)
from .hourly_forecast_fusion import HourlyForecastFusionError, _parse_open_meteo_utc


@dataclass(frozen=True)
class HourlyForecastSources:
    point: ProviderSnapshot
    nws: ProviderSnapshot
    secondary: ProviderSnapshot | None
    warnings: tuple[str, ...]


class ForecastAcquisitionError(ValueError):
    def __init__(self, code: str, blockers: tuple[str, ...]):
        self.code = code
        self.blockers = blockers
        super().__init__(f"{code}: {', '.join(blockers)}")


def acquire_hourly_forecasts(
    get_json: JsonGetter, *, latitude: float, longitude: float,
    target_date: str, models: Sequence[str], retrieved_at: str,
    secondary_enabled: bool = False, target_time_utc: datetime | None = None,
) -> HourlyForecastSources:
    """Require NWS; preserve it when optional Open-Meteo is unavailable."""
    adapter = NwsAdapter(get_json)
    point = adapter.point_metadata(latitude, longitude, retrieved_at=retrieved_at)
    props = point.payload.get('properties')
    url = str(props.get('forecastHourly') or '') if isinstance(props, Mapping) else ''
    parsed = urlsplit(url)
    if (parsed.scheme != 'https' or parsed.netloc != 'api.weather.gov'
            or not parsed.path.startswith('/gridpoints/')
            or not parsed.path.endswith('/forecast/hourly')
            or parsed.query or parsed.fragment):
        raise ForecastAcquisitionError('NWS_POINT_METADATA_INVALID', ('NWS_FORECAST_URL_INVALID',))
    nws = adapter.hourly_forecast(url, retrieved_at=retrieved_at)
    if not secondary_enabled:
        return HourlyForecastSources(point, nws, None, ('SECONDARY_FORECAST_DISABLED',))
    if (
        not isinstance(target_time_utc, datetime)
        or target_time_utc.tzinfo is None
    ):
        return HourlyForecastSources(point, nws, None,
            ('SECONDARY_FORECAST_UNAVAILABLE:OPEN_METEO_TARGET_UNVERIFIED',))
    try:
        secondary = OpenMeteoAdapter(get_json).multi_model_hourly_temperatures(
            latitude, longitude, target_date, target_date, models,
            retrieved_at=retrieved_at,
        )
        # The exact UTC target is the official contract observation instant;
        # a same-day forecast at a different hour is not corroboration.
        matches = sum(
            _parse_open_meteo_utc(value) == target_time_utc
            for value in secondary.valid_times
        )
        if matches != 1:
            return HourlyForecastSources(point, nws, None, (
                'SECONDARY_FORECAST_UNAVAILABLE:'
                + ('OPEN_METEO_EXACT_TARGET_MISSING' if not matches
                   else 'OPEN_METEO_EXACT_TARGET_DUPLICATE'),
            ))
    except (HttpAcquisitionError, OpenMeteoSnapshotInvalid, HourlyForecastFusionError) as exc:
        code = (exc.code if isinstance(exc, HttpAcquisitionError)
                else 'OPEN_METEO_HOURLY_TIMES_INVALID')
        return HourlyForecastSources(point, nws, None,
            (f'SECONDARY_FORECAST_UNAVAILABLE:{code}',))
    return HourlyForecastSources(point, nws, secondary, ())
