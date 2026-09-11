from __future__ import annotations

from dataclasses import dataclass
from datetime import datetime
from statistics import pstdev
from typing import Any, Mapping, Sequence
from zoneinfo import ZoneInfo

from .hourly_forecast_fusion import source_snapshot_id
from .models import ContractSnapshot, WeatherEvidenceSnapshot
from .observation_reconstruction import ObservationPoint, reconstruct_extreme, reconstruct_temperature_series
from .source_adapters import ProviderSnapshot


class DailyHighForecastFusionError(ValueError):
    def __init__(self, code: str, blockers: tuple[str, ...]):
        self.code = code
        self.blockers = blockers
        super().__init__(f"{code}: {', '.join(blockers)}")


@dataclass(frozen=True)
class DailyHighForecastEstimate:
    provider: str
    model: str
    local_date: str
    temperature_f: float
    source_snapshot_id: str


@dataclass(frozen=True)
class DailyHighForecastFusion:
    local_date: str
    primary_estimate_f: float
    estimates: tuple[DailyHighForecastEstimate, ...]
    disagreement_f: float
    provider_count: int
    observed_max_so_far_f: float
    evidence_complete: bool


def build_daily_high_weather_evidence(
    *,
    contract: ContractSnapshot,
    analysis_time: str,
    local_date: str,
    nws_hourly_snapshot: ProviderSnapshot,
    nws_observation_snapshot: ProviderSnapshot,
    open_meteo_snapshot: ProviderSnapshot | None,
    settlement_source_verified: bool,
    settlement_location_verified: bool,
) -> WeatherEvidenceSnapshot:
    """Build market-blind evidence for one exact daily-high contract.

    The primary central estimate is the maximum NWS hourly forecast for the
    target local calendar day. Open-Meteo model highs are used to quantify
    disagreement, not to out-vote the higher-authority NWS forecast. The
    observed maximum is reconstructed from the official station observation
    series; a single daily-extreme field is never trusted.
    """
    if contract.lane != "DAILY_HIGH_TEMPERATURE":
        raise DailyHighForecastFusionError(
            "DAILY_HIGH_FORECAST_CONTRACT_INVALID", ("LANE_NOT_DAILY_HIGH_TEMPERATURE",)
        )
    if not contract.timezone:
        raise DailyHighForecastFusionError(
            "DAILY_HIGH_FORECAST_CONTRACT_INVALID", ("CONTRACT_TIMEZONE_MISSING",)
        )
    try:
        timezone = ZoneInfo(contract.timezone)
    except Exception as exc:
        raise DailyHighForecastFusionError(
            "DAILY_HIGH_FORECAST_CONTRACT_INVALID", ("CONTRACT_TIMEZONE_INVALID",)
        ) from exc

    analysis = _parse_aware(analysis_time, "ANALYSIS_TIME_INVALID")
    if analysis.astimezone(timezone).date().isoformat() > local_date:
        raise DailyHighForecastFusionError(
            "DAILY_HIGH_FORECAST_TEMPORAL_INVALID", ("ANALYSIS_AFTER_TARGET_LOCAL_DATE",)
        )

    snapshots = [nws_hourly_snapshot, nws_observation_snapshot]
    if open_meteo_snapshot is not None:
        snapshots.append(open_meteo_snapshot)
    _verify_snapshot_times(snapshots, analysis)

    nws_id = source_snapshot_id(nws_hourly_snapshot)
    obs_id = source_snapshot_id(nws_observation_snapshot)
    primary = _extract_nws_daily_high(
        nws_hourly_snapshot, local_date=local_date, timezone=timezone, snapshot_id=nws_id
    )

    observation_points = _target_date_observations(
        nws_observation_snapshot, local_date=local_date, timezone=timezone
    )
    reconstructed = reconstruct_extreme(observation_points, "MAX")
    if not reconstructed.complete or reconstructed.value_f is None:
        raise DailyHighForecastFusionError(
            "DAILY_HIGH_OFFICIAL_OBSERVATIONS_INSUFFICIENT",
            tuple(reconstructed.blockers or ("OFFICIAL_OBSERVATION_SERIES_EMPTY",)),
        )

    estimates: list[DailyHighForecastEstimate] = [primary]
    source_ids = [nws_id, obs_id]
    providers = {"NWS"}
    roles = {
        "NWS_PRIMARY_FORECAST": nws_hourly_snapshot.role,
        "NWS_OFFICIAL_OBSERVATION": nws_observation_snapshot.role,
    }
    model_cycles: list[str] = []
    if nws_hourly_snapshot.issued_at:
        model_cycles.append(nws_hourly_snapshot.issued_at)

    if open_meteo_snapshot is not None:
        om_id = source_snapshot_id(open_meteo_snapshot)
        source_ids.append(om_id)
        providers.add("OPEN_METEO")
        roles["OPEN_METEO"] = open_meteo_snapshot.role
        estimates.extend(_extract_open_meteo_daily_highs(open_meteo_snapshot, local_date, om_id))
        if open_meteo_snapshot.issued_at:
            model_cycles.append(open_meteo_snapshot.issued_at)

    values = [estimate.temperature_f for estimate in estimates]
    disagreement = pstdev(values) if len(values) >= 2 else 0.0
    evidence_complete = bool(
        settlement_source_verified
        and settlement_location_verified
        and reconstructed.complete
        and primary.temperature_f is not None
        and source_ids
    )

    return WeatherEvidenceSnapshot(
        analysis_time=analysis_time,
        latest_official_observation_time=reconstructed.observation_time,
        forecast_issue_time=nws_hourly_snapshot.issued_at,
        model_cycle_times=tuple(sorted(set(model_cycles))),
        providers=tuple(sorted(providers)),
        source_roles=roles,
        source_snapshot_ids=tuple(source_ids),
        observed_extreme_so_far=float(reconstructed.value_f),
        central_estimate=float(primary.temperature_f),
        disagreement_magnitude=float(disagreement),
        evidence_complete=evidence_complete,
        station_identity_verified=bool(contract.settlement_station_id and settlement_location_verified),
        settlement_location_verified=settlement_location_verified,
        settlement_source_verified=settlement_source_verified,
        temporal_provenance_verified=True,
        notes={
            "lane": "DAILY_HIGH_TEMPERATURE",
            "local_date": local_date,
            "central_estimate_method": "NWS_HOURLY_TARGET_LOCAL_DATE_MAX",
            "official_extreme_method": "RECONSTRUCTED_FROM_OBSERVATION_SERIES",
            "official_observation_points_used": reconstructed.points_used,
            "forecast_estimates": tuple(
                {
                    "provider": item.provider,
                    "model": item.model,
                    "temperature_f": item.temperature_f,
                    "source_snapshot_id": item.source_snapshot_id,
                }
                for item in estimates
            ),
            "disagreement_method": "POPULATION_STDDEV_F",
            "market_price_used_as_input": False,
        },
    )


def _extract_nws_daily_high(
    snapshot: ProviderSnapshot, *, local_date: str, timezone: ZoneInfo, snapshot_id: str
) -> DailyHighForecastEstimate:
    if snapshot.provider.strip().upper() != "NWS":
        raise DailyHighForecastFusionError(
            "DAILY_HIGH_FORECAST_PROVIDER_INVALID", ("NWS_SNAPSHOT_PROVIDER_MISMATCH",)
        )
    props = snapshot.payload.get("properties") if isinstance(snapshot.payload, Mapping) else None
    periods = props.get("periods") if isinstance(props, Mapping) else None
    if not isinstance(periods, Sequence) or isinstance(periods, (str, bytes)):
        raise DailyHighForecastFusionError(
            "DAILY_HIGH_FORECAST_PAYLOAD_INVALID", ("NWS_PERIODS_MISSING",)
        )

    values: list[float] = []
    for period in periods:
        if not isinstance(period, Mapping) or not period.get("startTime"):
            continue
        valid = _parse_aware(str(period["startTime"]), "NWS_PERIOD_TIME_INVALID")
        if valid.astimezone(timezone).date().isoformat() != local_date:
            continue
        unit = str(period.get("temperatureUnit") or "").strip().upper()
        if unit != "F":
            raise DailyHighForecastFusionError(
                "DAILY_HIGH_FORECAST_UNITS_INVALID", (f"NWS_TEMPERATURE_UNIT:{unit or 'missing'}",)
            )
        values.append(_number(period.get("temperature"), "NWS_TEMPERATURE_INVALID"))
    if not values:
        raise DailyHighForecastFusionError(
            "DAILY_HIGH_FORECAST_DATA_INSUFFICIENT", ("NWS_TARGET_LOCAL_DATE_EMPTY",)
        )
    return DailyHighForecastEstimate(
        provider="NWS",
        model="NWS_HOURLY_DAILY_MAX",
        local_date=local_date,
        temperature_f=max(values),
        source_snapshot_id=snapshot_id,
    )


def _extract_open_meteo_daily_highs(
    snapshot: ProviderSnapshot, local_date: str, snapshot_id: str
) -> list[DailyHighForecastEstimate]:
    if snapshot.provider.strip().upper() != "OPEN_METEO":
        raise DailyHighForecastFusionError(
            "DAILY_HIGH_FORECAST_PROVIDER_INVALID", ("OPEN_METEO_SNAPSHOT_PROVIDER_MISMATCH",)
        )
    daily = snapshot.payload.get("daily") if isinstance(snapshot.payload, Mapping) else None
    units = snapshot.payload.get("daily_units") if isinstance(snapshot.payload, Mapping) else None
    if not isinstance(daily, Mapping):
        raise DailyHighForecastFusionError(
            "DAILY_HIGH_FORECAST_PAYLOAD_INVALID", ("OPEN_METEO_DAILY_MISSING",)
        )
    times = daily.get("time")
    if not isinstance(times, Sequence) or isinstance(times, (str, bytes)):
        raise DailyHighForecastFusionError(
            "DAILY_HIGH_FORECAST_PAYLOAD_INVALID", ("OPEN_METEO_DAILY_TIMES_MISSING",)
        )
    matches = [index for index, value in enumerate(times) if str(value) == local_date]
    if len(matches) != 1:
        blocker = "OPEN_METEO_TARGET_DATE_MISSING" if not matches else "OPEN_METEO_TARGET_DATE_DUPLICATE"
        raise DailyHighForecastFusionError("DAILY_HIGH_FORECAST_DATA_INSUFFICIENT", (blocker,))
    target_index = matches[0]

    variables = sorted(
        key for key, value in daily.items()
        if str(key).startswith("temperature_2m_max")
        and isinstance(value, Sequence)
        and not isinstance(value, (str, bytes))
    )
    if not variables:
        raise DailyHighForecastFusionError(
            "DAILY_HIGH_FORECAST_PAYLOAD_INVALID", ("OPEN_METEO_DAILY_HIGH_SERIES_MISSING",)
        )

    estimates: list[DailyHighForecastEstimate] = []
    for variable in variables:
        values = daily[variable]
        if target_index >= len(values):
            raise DailyHighForecastFusionError(
                "DAILY_HIGH_FORECAST_PAYLOAD_INVALID", (f"OPEN_METEO_LENGTH_MISMATCH:{variable}",)
            )
        if isinstance(units, Mapping):
            unit = str(units.get(variable) or units.get("temperature_2m_max") or "").strip().upper()
            if unit and unit not in {"°F", "F"}:
                raise DailyHighForecastFusionError(
                    "DAILY_HIGH_FORECAST_UNITS_INVALID", (f"OPEN_METEO_TEMPERATURE_UNIT:{unit}",)
                )
        model = variable.removeprefix("temperature_2m_max_") or "OPEN_METEO_DEFAULT"
        estimates.append(
            DailyHighForecastEstimate(
                provider="OPEN_METEO",
                model=model.upper(),
                local_date=local_date,
                temperature_f=_number(values[target_index], f"OPEN_METEO_TEMPERATURE_INVALID:{variable}"),
                source_snapshot_id=snapshot_id,
            )
        )
    return estimates


def _target_date_observations(
    snapshot: ProviderSnapshot, *, local_date: str, timezone: ZoneInfo
) -> tuple[ObservationPoint, ...]:
    if snapshot.provider.strip().upper() != "NWS":
        raise DailyHighForecastFusionError(
            "DAILY_HIGH_OBSERVATION_PROVIDER_INVALID", ("NWS_OBSERVATION_PROVIDER_MISMATCH",)
        )
    features = snapshot.payload.get("features") if isinstance(snapshot.payload, Mapping) else None
    if not isinstance(features, Sequence) or isinstance(features, (str, bytes)):
        raise DailyHighForecastFusionError(
            "DAILY_HIGH_OBSERVATION_PAYLOAD_INVALID", ("NWS_OBSERVATION_FEATURES_MISSING",)
        )
    points = reconstruct_temperature_series(feature for feature in features if isinstance(feature, Mapping))
    return tuple(
        point for point in points
        if _parse_aware(point.timestamp, "NWS_OBSERVATION_TIME_INVALID").astimezone(timezone).date().isoformat() == local_date
    )


def _verify_snapshot_times(snapshots: Sequence[ProviderSnapshot], analysis: datetime) -> None:
    blockers: list[str] = []
    for snapshot in snapshots:
        retrieved = _parse_aware(snapshot.retrieved_at, f"{snapshot.provider}_RETRIEVED_AT_INVALID")
        if retrieved > analysis:
            blockers.append(f"{snapshot.provider}_RETRIEVED_AFTER_ANALYSIS")
        if snapshot.issued_at:
            issued = _parse_aware(snapshot.issued_at, f"{snapshot.provider}_ISSUED_AT_INVALID")
            if issued > analysis:
                blockers.append(f"{snapshot.provider}_ISSUED_AFTER_ANALYSIS")
    if blockers:
        raise DailyHighForecastFusionError(
            "DAILY_HIGH_FORECAST_TEMPORAL_INVALID", tuple(dict.fromkeys(blockers))
        )


def _parse_aware(value: str, blocker: str) -> datetime:
    text = str(value).strip().replace("Z", "+00:00")
    try:
        parsed = datetime.fromisoformat(text)
    except ValueError as exc:
        raise DailyHighForecastFusionError("DAILY_HIGH_FORECAST_TEMPORAL_INVALID", (blocker,)) from exc
    if parsed.tzinfo is None:
        raise DailyHighForecastFusionError("DAILY_HIGH_FORECAST_TEMPORAL_INVALID", (blocker,))
    return parsed


def _number(value: Any, blocker: str) -> float:
    try:
        number = float(value)
    except (TypeError, ValueError) as exc:
        raise DailyHighForecastFusionError("DAILY_HIGH_FORECAST_PAYLOAD_INVALID", (blocker,)) from exc
    if number != number or number in {float("inf"), float("-inf")}:
        raise DailyHighForecastFusionError("DAILY_HIGH_FORECAST_PAYLOAD_INVALID", (blocker,))
    return number
