from __future__ import annotations

from dataclasses import dataclass
from typing import Any, Callable, Mapping, Sequence


JsonGetter = Callable[[str, Mapping[str, str] | None], Mapping[str, Any]]


@dataclass(frozen=True)
class ProviderSnapshot:
    provider: str
    role: str
    source_id: str
    retrieved_at: str
    issued_at: str | None
    valid_times: tuple[str, ...]
    payload: Mapping[str, Any]


class NwsAdapter:
    provider = "NWS"

    def __init__(self, get_json: JsonGetter):
        self.get_json = get_json

    def point_metadata(self, lat: float, lon: float, *, retrieved_at: str) -> ProviderSnapshot:
        url = f"https://api.weather.gov/points/{lat:.4f},{lon:.4f}"
        data = self.get_json(url, {"User-Agent": "WOW-Kalshi-Weather/2.0"})
        return ProviderSnapshot(self.provider, "PRIMARY_FORECAST", url, retrieved_at, None, (), data)

    def hourly_forecast(self, forecast_hourly_url: str, *, retrieved_at: str) -> ProviderSnapshot:
        data = self.get_json(forecast_hourly_url, {"User-Agent": "WOW-Kalshi-Weather/2.0"})
        props = data.get("properties") if isinstance(data, Mapping) else None
        periods = props.get("periods", []) if isinstance(props, Mapping) else []
        valid = tuple(str(p.get("startTime")) for p in periods if isinstance(p, Mapping) and p.get("startTime"))
        issued = str(props.get("generatedAt")) if isinstance(props, Mapping) and props.get("generatedAt") else None
        return ProviderSnapshot(self.provider, "PRIMARY_FORECAST", forecast_hourly_url, retrieved_at, issued, valid, data)

    def station_observations(self, station_id: str, start: str, end: str, *, retrieved_at: str) -> ProviderSnapshot:
        url = f"https://api.weather.gov/stations/{station_id}/observations?start={start}&end={end}"
        data = self.get_json(url, {"User-Agent": "WOW-Kalshi-Weather/2.0"})
        features = data.get("features", []) if isinstance(data, Mapping) else []
        valid = tuple(
            str(f.get("properties", {}).get("timestamp"))
            for f in features
            if isinstance(f, Mapping) and isinstance(f.get("properties"), Mapping) and f["properties"].get("timestamp")
        )
        return ProviderSnapshot(self.provider, "OFFICIAL_OBSERVATION", url, retrieved_at, None, valid, data)


class OpenMeteoAdapter:
    provider = "OPEN_METEO"

    def __init__(self, get_json: JsonGetter):
        self.get_json = get_json

    def multi_model_daily_highs(
        self,
        lat: float,
        lon: float,
        date: str,
        models: Sequence[str],
        *,
        retrieved_at: str,
    ) -> ProviderSnapshot:
        model_arg = ",".join(models)
        url = (
            "https://api.open-meteo.com/v1/forecast"
            f"?latitude={lat:.4f}&longitude={lon:.4f}"
            "&daily=temperature_2m_max"
            f"&start_date={date}&end_date={date}&models={model_arg}&temperature_unit=fahrenheit&timezone=UTC"
        )
        data = self.get_json(url, None)
        return ProviderSnapshot(self.provider, "SECONDARY_FORECAST", url, retrieved_at, None, (date,), data)


class NoaaNceiAdapter:
    provider = "NOAA_NCEI"

    def __init__(self, get_json: JsonGetter, token: str | None = None):
        self.get_json = get_json
        self.token = token

    def daily_station_history(self, dataset_id: str, station_id: str, start: str, end: str, *, retrieved_at: str) -> ProviderSnapshot:
        url = (
            "https://www.ncei.noaa.gov/cdo-web/api/v2/data"
            f"?datasetid={dataset_id}&stationid={station_id}&startdate={start}&enddate={end}&limit=1000"
        )
        headers = {"token": self.token} if self.token else None
        data = self.get_json(url, headers)
        return ProviderSnapshot(self.provider, "HISTORICAL_CALIBRATION", url, retrieved_at, None, (start, end), data)


class XweatherAdapter:
    provider = "XWEATHER"

    def __init__(self, get_json: JsonGetter, client_id: str | None = None, client_secret: str | None = None):
        self.get_json = get_json
        self.client_id = client_id
        self.client_secret = client_secret

    def conditions(self, place: str, *, retrieved_at: str) -> ProviderSnapshot:
        auth = ""
        if self.client_id and self.client_secret:
            auth = f"?client_id={self.client_id}&client_secret={self.client_secret}"
        url = f"https://api.aerisapi.com/observations/{place}{auth}"
        data = self.get_json(url, None)
        return ProviderSnapshot(self.provider, "CORROBORATION_ONLY", url, retrieved_at, None, (), data)
