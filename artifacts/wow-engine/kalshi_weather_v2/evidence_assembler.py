from __future__ import annotations

from dataclasses import dataclass
from datetime import datetime
from statistics import mean, pstdev
from typing import Any, Iterable, Mapping, Sequence
from zoneinfo import ZoneInfo

from .models import WeatherEvidenceSnapshot
from .observation_reconstruction import reconstruct_extreme, reconstruct_temperature_series
from .source_adapters import ProviderSnapshot


@dataclass(frozen=True)
class ForecastPoint:
    source: str
    value_f: float
    valid_time: str


class WeatherEvidenceAssemblyError(ValueError):
    pass


def assemble_daily_high_evidence(
    *,
    analysis_time: str,
    local_date: str,
    timezone_name: str,
    nws_hourly: ProviderSnapshot,
    nws_observations: ProviderSnapshot,
    open_meteo: ProviderSnapshot | None = None,
    noaa_history: ProviderSnapshot | None = None,
    xweather: ProviderSnapshot | None = None,
    station_identity_verified: bool,
    settlement_source_verified: bool,
) -> WeatherEvidenceSnapshot:
    """Assemble a daily-high evidence snapshot without market information.

    Central estimate is the maximum NWS hourly forecast temperature for the
    requested local calendar day. Open-Meteo/Xweather are disagreement and
    corroboration inputs only in this slice. Official max-so-far is rebuilt
    from the NWS observation series instead of using a single daily-extreme
    field.
    """
    try:
        tz = ZoneInfo(timezone_name)
    except Exception as exc:  # pragma: no cover - platform timezone failure
        raise WeatherEvidenceAssemblyError("TIMEZONE_INVALID") from exc

    forecast_points = _nws_hourly_points(nws_hourly.payload, local_date=local_date, tz=tz)
    if not forecast_points:
        raise WeatherEvidenceAssemblyError("NWS_HOURLY_TARGET_DATE_EMPTY")
    central = max(point.value_f for point in forecast_points)

    features = nws_observations.payload.get("features") if isinstance(nws_observations.payload, Mapping) else None
    obs_points = reconstruct_temperature_series(features if isinstance(features, list) else [])
    target_obs = tuple(p for p in obs_points if _iso_local_date(p.timestamp, tz) == local_date)
    extreme = reconstruct_extreme(target_obs, "MAX")

    secondary_values: list[float] = []
    if open_meteo is not None:
        secondary_values.extend(_open_meteo_daily_highs(open_meteo.payload, local_date))
    if xweather is not None:
        secondary_values.extend(_xweather_temperature_candidates(xweather.payload))

    disagreement_values = [central, *secondary_values]
    disagreement = pstdev(disagreement_values) if len(disagreement_values) >= 2 else 0.0

    snapshots = [nws_hourly, nws_observations]
    if open_meteo is not None:
        snapshots.append(open_meteo)
    if noaa_history is not None:
        snapshots.append(noaa_history)
    if xweather is not None:
        snapshots.append(xweather)

    source_ids = tuple(snapshot.source_id for snapshot in snapshots if snapshot.source_id)
    providers = tuple(snapshot.provider for snapshot in snapshots)
    roles = {snapshot.provider: snapshot.role for snapshot in snapshots}
    issue_times = [snapshot.issued_at for snapshot in snapshots if snapshot.issued_at]
    model_cycle_times = tuple(issue_times)

    evidence_complete = bool(
        station_identity_verified
        and settlement_source_verified
        and source_ids
        and extreme.complete
        and central is not None
    )

    return WeatherEvidenceSnapshot(
        analysis_time=analysis_time,
        latest_official_observation_time=extreme.observation_time,
        forecast_issue_time=nws_hourly.issued_at,
        model_cycle_times=model_cycle_times,
        providers=providers,
        source_roles=roles,
        source_snapshot_ids=source_ids,
        observed_extreme_so_far=extreme.value_f,
        central_estimate=float(central),
        disagreement_magnitude=float(disagreement),
        evidence_complete=evidence_complete,
        station_identity_verified=station_identity_verified,
        settlement_source_verified=settlement_source_verified,
        temporal_provenance_verified=_temporal_provenance_ok(analysis_time, snapshots),
        notes={
            "nws_hourly_points_used": len(forecast_points),
            "official_observation_points_used": extreme.points_used,
            "secondary_forecast_values": tuple(round(v, 4) for v in secondary_values),
            "disagreement_method": "POPULATION_STDDEV_F",
            "central_estimate_source": "NWS_HOURLY_TARGET_DATE_MAX",
            "market_price_used": False,
        },
    )


def _nws_hourly_points(payload: Mapping[str, Any], *, local_date: str, tz: ZoneInfo) -> tuple[ForecastPoint, ...]:
    props = payload.get("properties") if isinstance(payload, Mapping) else None
    periods = props.get("periods") if isinstance(props, Mapping) else None
    if not isinstance(periods, list):
        return ()
    points: list[ForecastPoint] = []
    for period in periods:
        if not isinstance(period, Mapping):
            continue
        start = period.get("startTime")
        value = period.get("temperature")
        unit = str(period.get("temperatureUnit") or "F").upper()
        if not start or value is None:
            continue
        try:
            numeric = float(value)
        except (TypeError, ValueError):
            continue
        if unit == "C":
            numeric = numeric * 9.0 / 5.0 + 32.0
        elif unit != "F":
            continue
        if _iso_local_date(str(start), tz) != local_date:
            continue
        points.append(ForecastPoint(source="NWS", value_f=numeric, valid_time=str(start)))
    return tuple(points)


def _open_meteo_daily_highs(payload: Mapping[str, Any], local_date: str) -> list[float]:
    daily = payload.get("daily") if isinstance(payload, Mapping) else None
    if not isinstance(daily, Mapping):
        return []
    times = daily.get("time")
    if not isinstance(times, list) or local_date not in [str(x) for x in times]:
        return []
    index = [str(x) for x in times].index(local_date)
    values: list[float] = []
    for key, series in daily.items():
        if not str(key).startswith("temperature_2m_max") or not isinstance(series, list) or index >= len(series):
            continue
        try:
            value = float(series[index])
        except (TypeError, ValueError):
            continue
        values.append(value)
    return values


def _xweather_temperature_candidates(payload: Mapping[str, Any]) -> list[float]:
    # Xweather is optional corroboration only. Accept only explicit Fahrenheit
    # fields and never fail the core lane if its schema is absent/different.
    values: list[float] = []
    response = payload.get("response") if isinstance(payload, Mapping) else None
    rows: Iterable[Any]
    if isinstance(response, list):
        rows = response
    elif isinstance(response, Mapping):
        rows = [response]
    else:
        rows = []
    for row in rows:
        if not isinstance(row, Mapping):
            continue
        ob = row.get("ob") if isinstance(row.get("ob"), Mapping) else row
        for key in ("tempF", "maxTempF", "highF"):
            if key in ob:
                try:
                    values.append(float(ob[key]))
                except (TypeError, ValueError):
                    pass
    return values


def _temporal_provenance_ok(analysis_time: str, snapshots: Sequence[ProviderSnapshot]) -> bool:
    try:
        analysis = _parse_iso(analysis_time)
    except ValueError:
        return False
    for snapshot in snapshots:
        try:
            retrieved = _parse_iso(snapshot.retrieved_at)
        except ValueError:
            return False
        if retrieved > analysis:
            return False
        if snapshot.issued_at:
            try:
                issued = _parse_iso(snapshot.issued_at)
            except ValueError:
                return False
            if issued > analysis:
                return False
    return True


def _iso_local_date(value: str, tz: ZoneInfo) -> str:
    return _parse_iso(value).astimezone(tz).date().isoformat()


def _parse_iso(value: str) -> datetime:
    text = str(value).strip().replace("Z", "+00:00")
    dt = datetime.fromisoformat(text)
    if dt.tzinfo is None:
        raise ValueError("timezone-aware timestamp required")
    return dt
