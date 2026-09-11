from __future__ import annotations

import csv
import io
from datetime import datetime, timezone
from typing import Any, Mapping, Sequence
from urllib.parse import urlencode

from .source_adapters import ProviderSnapshot


USER_AGENT = "WOW-Kalshi-Weather/2.0 research-support"


class SupplementalSourceError(ValueError):
    def __init__(self, code: str, detail: str):
        self.code = code
        self.detail = detail
        super().__init__(f"{code}: {detail}")


class NwsGridDataAdapter:
    provider = "NWS_GRID"

    def __init__(self, get_json):
        self.get_json = get_json

    def grid_forecast(self, forecast_grid_url: str, *, retrieved_at: str) -> ProviderSnapshot:
        url = str(forecast_grid_url or "").strip()
        if not url.startswith("https://api.weather.gov/gridpoints/"):
            raise SupplementalSourceError("NWS_GRID_URL_INVALID", url)
        payload = self.get_json(url, {"User-Agent": USER_AGENT})
        props = payload.get("properties") if isinstance(payload, Mapping) else None
        update_time = str(props.get("updateTime")) if isinstance(props, Mapping) and props.get("updateTime") else None
        valid_times: list[str] = []
        temps = props.get("temperature") if isinstance(props, Mapping) else None
        values = temps.get("values") if isinstance(temps, Mapping) else None
        if isinstance(values, Sequence) and not isinstance(values, (str, bytes)):
            valid_times = [str(v.get("validTime")) for v in values if isinstance(v, Mapping) and v.get("validTime")]
        return ProviderSnapshot(
            self.provider,
            "PRIMARY_FORECAST_RAW_GRID",
            url,
            retrieved_at,
            update_time,
            tuple(valid_times),
            payload,
        )


class OpenMeteoEnsembleAdapter:
    provider = "OPEN_METEO_ENSEMBLE"

    def __init__(self, get_json):
        self.get_json = get_json

    def hourly_temperature_ensemble(
        self,
        lat: float,
        lon: float,
        start_date: str,
        end_date: str,
        *,
        retrieved_at: str,
        model: str = "icon_seamless_eps",
    ) -> ProviderSnapshot:
        model = str(model or "").strip()
        if not model:
            raise SupplementalSourceError("OPEN_METEO_ENSEMBLE_MODEL_MISSING", "model")
        params = urlencode(
            {
                "latitude": f"{float(lat):.4f}",
                "longitude": f"{float(lon):.4f}",
                "hourly": "temperature_2m",
                "temperature_unit": "fahrenheit",
                "timezone": "UTC",
                "start_date": start_date,
                "end_date": end_date,
                "models": model,
            }
        )
        url = f"https://ensemble-api.open-meteo.com/v1/ensemble?{params}"
        payload = self.get_json(url, None)
        hourly = payload.get("hourly") if isinstance(payload, Mapping) else None
        times = hourly.get("time", []) if isinstance(hourly, Mapping) else []
        return ProviderSnapshot(
            self.provider,
            "UNCERTAINTY_ENSEMBLE",
            url,
            retrieved_at,
            None,
            tuple(str(value) for value in times if value not in (None, "")),
            payload,
        )


class OpenMeteoArchiveAdapter:
    provider = "OPEN_METEO_ARCHIVE"

    def __init__(self, get_json):
        self.get_json = get_json

    def previous_run_temperatures(
        self,
        lat: float,
        lon: float,
        start_date: str,
        end_date: str,
        *,
        retrieved_at: str,
        model: str,
        previous_days: Sequence[int] = (1, 2, 3),
    ) -> ProviderSnapshot:
        offsets = tuple(sorted({int(day) for day in previous_days if 0 <= int(day) <= 7}))
        if not offsets:
            raise SupplementalSourceError("OPEN_METEO_PREVIOUS_RUN_OFFSETS_INVALID", repr(previous_days))
        variables = ",".join(f"temperature_2m_previous_day{day}" for day in offsets)
        params = urlencode(
            {
                "latitude": f"{float(lat):.4f}",
                "longitude": f"{float(lon):.4f}",
                "hourly": variables,
                "temperature_unit": "fahrenheit",
                "timezone": "UTC",
                "start_date": start_date,
                "end_date": end_date,
                "models": str(model).strip(),
            }
        )
        url = f"https://previous-runs-api.open-meteo.com/v1/forecast?{params}"
        payload = self.get_json(url, None)
        hourly = payload.get("hourly") if isinstance(payload, Mapping) else None
        times = hourly.get("time", []) if isinstance(hourly, Mapping) else []
        return ProviderSnapshot(
            self.provider,
            "HISTORICAL_FORECAST_REPLAY",
            url,
            retrieved_at,
            None,
            tuple(str(value) for value in times if value not in (None, "")),
            payload,
        )


class MetNorwayAdapter:
    provider = "MET_NORWAY"

    def __init__(self, get_json):
        self.get_json = get_json

    def compact_forecast(self, lat: float, lon: float, *, retrieved_at: str) -> ProviderSnapshot:
        params = urlencode({"lat": f"{float(lat):.4f}", "lon": f"{float(lon):.4f}"})
        url = f"https://api.met.no/weatherapi/locationforecast/2.0/compact?{params}"
        payload = self.get_json(url, {"User-Agent": USER_AGENT})
        props = payload.get("properties") if isinstance(payload, Mapping) else None
        meta = props.get("meta") if isinstance(props, Mapping) else None
        updated_at = str(meta.get("updated_at")) if isinstance(meta, Mapping) and meta.get("updated_at") else None
        timeseries = props.get("timeseries", []) if isinstance(props, Mapping) else []
        valid = tuple(
            str(item.get("time"))
            for item in timeseries
            if isinstance(item, Mapping) and item.get("time")
        )
        return ProviderSnapshot(
            self.provider,
            "TERTIARY_FORECAST_CORROBORATION",
            url,
            retrieved_at,
            updated_at,
            valid,
            payload,
        )


class NceiAccessDataAdapter:
    provider = "NOAA_NCEI"

    def __init__(self, get_json_array):
        self.get_json_array = get_json_array

    def daily_summaries(
        self,
        station_id: str,
        start_date: str,
        end_date: str,
        *,
        retrieved_at: str,
    ) -> ProviderSnapshot:
        station = str(station_id or "").strip()
        if not station:
            raise SupplementalSourceError("NCEI_STATION_ID_MISSING", "station_id")
        params = urlencode(
            {
                "dataset": "daily-summaries",
                "stations": station,
                "startDate": start_date,
                "endDate": end_date,
                "format": "json",
                "units": "standard",
                "includeAttributes": "true",
            }
        )
        url = f"https://www.ncei.noaa.gov/access/services/data/v1?{params}"
        rows = self.get_json_array(url, None)
        payload = {"station_id": station, "rows": [dict(row) if isinstance(row, Mapping) else row for row in rows]}
        valid = tuple(
            str(row.get("DATE"))
            for row in rows
            if isinstance(row, Mapping) and row.get("DATE")
        )
        return ProviderSnapshot(
            self.provider,
            "HISTORICAL_CALIBRATION",
            url,
            retrieved_at,
            None,
            valid,
            payload,
        )


class IemAsosArchiveAdapter:
    provider = "IEM_ASOS"

    def __init__(self, get_text):
        self.get_text = get_text

    def observations(
        self,
        *,
        network: str,
        station: str,
        start_date: str,
        end_date: str,
        retrieved_at: str,
    ) -> ProviderSnapshot:
        start = _date_parts(start_date)
        end = _date_parts(end_date)
        params = urlencode(
            {
                "network": str(network).strip(),
                "station": str(station).strip(),
                "data": "all",
                "year1": start[0],
                "month1": start[1],
                "day1": start[2],
                "year2": end[0],
                "month2": end[1],
                "day2": end[2],
                "tz": "Etc/UTC",
                "format": "onlycomma",
                "latlon": "no",
                "elev": "no",
                "missing": "M",
                "trace": "T",
                "direct": "no",
                "report_type": "3,4",
            }
        )
        url = f"https://mesonet.agron.iastate.edu/cgi-bin/request/asos.py?{params}"
        text = self.get_text(url, None)
        rows = list(csv.DictReader(io.StringIO(text)))
        valid = tuple(str(row.get("valid")) for row in rows if row.get("valid"))
        return ProviderSnapshot(
            self.provider,
            "ASOS_ARCHIVE_CORROBORATION",
            url,
            retrieved_at,
            None,
            valid,
            {"network": network, "station": station, "rows": rows},
        )


def _date_parts(value: str) -> tuple[int, int, int]:
    try:
        dt = datetime.strptime(str(value), "%Y-%m-%d").replace(tzinfo=timezone.utc)
    except ValueError as exc:
        raise SupplementalSourceError("DATE_INVALID", str(value)) from exc
    return dt.year, dt.month, dt.day
