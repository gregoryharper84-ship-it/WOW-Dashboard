from __future__ import annotations

import hashlib
import json
from dataclasses import dataclass
from datetime import datetime, timezone
from statistics import median
from typing import Any, Mapping, Sequence

from .models import ContractSnapshot, WeatherEvidenceSnapshot
from .source_adapters import ProviderSnapshot


class HourlyForecastFusionError(ValueError):
    def __init__(self, code: str, blockers: tuple[str, ...]):
        self.code = code
        self.blockers = blockers
        super().__init__(f"{code}: {', '.join(blockers)}")


@dataclass(frozen=True)
class HourlyForecastEstimate:
    provider: str
    model: str
    target_time_utc: str
    temperature_f: float
    source_snapshot_id: str


@dataclass(frozen=True)
class HourlyForecastFusion:
    target_time_utc: str
    estimates: tuple[HourlyForecastEstimate, ...]
    central_estimate_f: float
    disagreement_f: float
    provider_count: int
    evidence_complete: bool


def build_hourly_weather_evidence(
    *,
    contract: ContractSnapshot,
    analysis_time: str,
    nws_snapshot: ProviderSnapshot | None,
    open_meteo_snapshot: ProviderSnapshot | None,
    settlement_source_verified: bool,
    settlement_location_verified: bool,
) -> WeatherEvidenceSnapshot:
    """Fuse independent point forecasts for one exact hourly settlement instant.

    The fusion is deterministic and market-blind. It never reads Kalshi price,
    orderbook, displayed probability, or the settlement index itself. The
    settlement-source/location booleans are supplied by the contract layer and
    are not inferred from forecast providers.

    Evidence is considered complete only when at least two independent provider
    families contribute the exact target instant. Multiple Open-Meteo model
    members improve disagreement measurement but do not count as independent
    providers for completeness.
    """
    if contract.lane != "HOURLY_TEMPERATURE":
        raise HourlyForecastFusionError("HOURLY_FORECAST_CONTRACT_INVALID", ("LANE_NOT_HOURLY_TEMPERATURE",))
    target = _target_from_contract(contract)
    analysis = _parse_aware(analysis_time, "ANALYSIS_TIME_INVALID")
    if analysis > target:
        raise HourlyForecastFusionError("HOURLY_FORECAST_TEMPORAL_INVALID", ("ANALYSIS_AFTER_TARGET",))

    estimates: list[HourlyForecastEstimate] = []
    source_ids: list[str] = []
    providers: list[str] = []
    source_roles: dict[str, str] = {}
    cycle_times: list[str] = []
    issue_times: list[datetime] = []

    if nws_snapshot is not None:
        nws_id = source_snapshot_id(nws_snapshot)
        source_ids.append(nws_id)
        providers.append("NWS")
        source_roles["NWS"] = nws_snapshot.role
        estimates.append(_extract_nws(nws_snapshot, target, nws_id))
        if nws_snapshot.issued_at:
            issued = _parse_aware(nws_snapshot.issued_at, "NWS_ISSUE_TIME_INVALID")
            if issued > analysis:
                raise HourlyForecastFusionError("HOURLY_FORECAST_TEMPORAL_INVALID", ("NWS_ISSUED_AFTER_ANALYSIS",))
            issue_times.append(issued)
            cycle_times.append(_iso(issued))

    if open_meteo_snapshot is not None:
        om_id = source_snapshot_id(open_meteo_snapshot)
        source_ids.append(om_id)
        providers.append("OPEN_METEO")
        source_roles["OPEN_METEO"] = open_meteo_snapshot.role
        estimates.extend(_extract_open_meteo(open_meteo_snapshot, target, om_id))
        if open_meteo_snapshot.issued_at:
            issued = _parse_aware(open_meteo_snapshot.issued_at, "OPEN_METEO_ISSUE_TIME_INVALID")
            if issued > analysis:
                raise HourlyForecastFusionError("HOURLY_FORECAST_TEMPORAL_INVALID", ("OPEN_METEO_ISSUED_AFTER_ANALYSIS",))
            issue_times.append(issued)
            cycle_times.append(_iso(issued))

    if not estimates:
        raise HourlyForecastFusionError("HOURLY_FORECAST_DATA_INSUFFICIENT", ("NO_EXACT_TARGET_FORECAST",))

    distinct_providers = {estimate.provider for estimate in estimates}
    values = [estimate.temperature_f for estimate in estimates]
    fused = HourlyForecastFusion(
        target_time_utc=_iso(target),
        estimates=tuple(estimates),
        central_estimate_f=float(median(values)),
        disagreement_f=max(values) - min(values),
        provider_count=len(distinct_providers),
        evidence_complete=len(distinct_providers) >= 2,
    )

    forecast_issue = max(issue_times) if issue_times else None
    return WeatherEvidenceSnapshot(
        analysis_time=_iso(analysis),
        latest_official_observation_time=None,
        forecast_issue_time=_iso(forecast_issue) if forecast_issue else None,
        model_cycle_times=tuple(sorted(set(cycle_times))),
        providers=tuple(sorted(distinct_providers)),
        source_roles=source_roles,
        source_snapshot_ids=tuple(source_ids),
        observed_extreme_so_far=None,
        central_estimate=fused.central_estimate_f,
        disagreement_magnitude=fused.disagreement_f,
        evidence_complete=fused.evidence_complete,
        station_identity_verified=False,
        settlement_location_verified=settlement_location_verified,
        settlement_source_verified=settlement_source_verified,
        temporal_provenance_verified=True,
        notes={
            "lane": "HOURLY_TEMPERATURE",
            "target_time_utc": fused.target_time_utc,
            "fusion_method": "DETERMINISTIC_MEDIAN_EXACT_TARGET",
            "provider_count": fused.provider_count,
            "estimates": [
                {
                    "provider": item.provider,
                    "model": item.model,
                    "temperature_f": item.temperature_f,
                    "target_time_utc": item.target_time_utc,
                    "source_snapshot_id": item.source_snapshot_id,
                }
                for item in fused.estimates
            ],
            "market_price_used_as_input": False,
        },
    )


def source_snapshot_id(snapshot: ProviderSnapshot) -> str:
    canonical = {
        "provider": snapshot.provider,
        "role": snapshot.role,
        "source_id": snapshot.source_id,
        "retrieved_at": snapshot.retrieved_at,
        "issued_at": snapshot.issued_at,
        "valid_times": list(snapshot.valid_times),
        "payload": snapshot.payload,
    }
    digest = hashlib.sha256(
        json.dumps(canonical, sort_keys=True, separators=(",", ":"), default=str).encode("utf-8")
    ).hexdigest()
    return f"kalshi-weather-source-{digest[:24]}"


def _extract_nws(snapshot: ProviderSnapshot, target: datetime, snapshot_id: str) -> HourlyForecastEstimate:
    if snapshot.provider.strip().upper() != "NWS":
        raise HourlyForecastFusionError("HOURLY_FORECAST_PROVIDER_INVALID", ("NWS_SNAPSHOT_PROVIDER_MISMATCH",))
    props = snapshot.payload.get("properties") if isinstance(snapshot.payload, Mapping) else None
    periods = props.get("periods") if isinstance(props, Mapping) else None
    if not isinstance(periods, Sequence) or isinstance(periods, (str, bytes)):
        raise HourlyForecastFusionError("HOURLY_FORECAST_PAYLOAD_INVALID", ("NWS_PERIODS_MISSING",))

    matches: list[HourlyForecastEstimate] = []
    for period in periods:
        if not isinstance(period, Mapping) or not period.get("startTime"):
            continue
        start = _parse_aware(str(period["startTime"]), "NWS_PERIOD_TIME_INVALID")
        if start != target:
            continue
        unit = str(period.get("temperatureUnit") or "").strip().upper()
        if unit != "F":
            raise HourlyForecastFusionError("HOURLY_FORECAST_UNITS_INVALID", (f"NWS_TEMPERATURE_UNIT:{unit or 'missing'}",))
        value = _number(period.get("temperature"), "NWS_TEMPERATURE_INVALID")
        matches.append(
            HourlyForecastEstimate(
                provider="NWS",
                model="NWS_HOURLY",
                target_time_utc=_iso(target),
                temperature_f=value,
                source_snapshot_id=snapshot_id,
            )
        )
    if len(matches) != 1:
        blocker = "NWS_EXACT_TARGET_MISSING" if not matches else "NWS_EXACT_TARGET_DUPLICATE"
        raise HourlyForecastFusionError("HOURLY_FORECAST_DATA_INSUFFICIENT", (blocker,))
    return matches[0]


def _extract_open_meteo(
    snapshot: ProviderSnapshot, target: datetime, snapshot_id: str
) -> list[HourlyForecastEstimate]:
    if snapshot.provider.strip().upper() != "OPEN_METEO":
        raise HourlyForecastFusionError("HOURLY_FORECAST_PROVIDER_INVALID", ("OPEN_METEO_SNAPSHOT_PROVIDER_MISMATCH",))
    payload = snapshot.payload
    offset = payload.get("utc_offset_seconds") if isinstance(payload, Mapping) else None
    timezone_name = str(payload.get("timezone") or "").strip().upper() if isinstance(payload, Mapping) else ""
    if offset not in (0, "0", 0.0) or timezone_name not in {"UTC", "GMT"}:
        raise HourlyForecastFusionError("HOURLY_FORECAST_TIMEZONE_INVALID", ("OPEN_METEO_NOT_UTC",))

    hourly = payload.get("hourly") if isinstance(payload, Mapping) else None
    units = payload.get("hourly_units") if isinstance(payload, Mapping) else None
    if not isinstance(hourly, Mapping) or not isinstance(units, Mapping):
        raise HourlyForecastFusionError("HOURLY_FORECAST_PAYLOAD_INVALID", ("OPEN_METEO_HOURLY_MISSING",))
    times = hourly.get("time")
    if not isinstance(times, Sequence) or isinstance(times, (str, bytes)):
        raise HourlyForecastFusionError("HOURLY_FORECAST_PAYLOAD_INVALID", ("OPEN_METEO_TIMES_MISSING",))

    target_index: int | None = None
    for index, value in enumerate(times):
        parsed = _parse_open_meteo_utc(str(value))
        if parsed == target:
            if target_index is not None:
                raise HourlyForecastFusionError("HOURLY_FORECAST_DATA_INSUFFICIENT", ("OPEN_METEO_EXACT_TARGET_DUPLICATE",))
            target_index = index
    if target_index is None:
        raise HourlyForecastFusionError("HOURLY_FORECAST_DATA_INSUFFICIENT", ("OPEN_METEO_EXACT_TARGET_MISSING",))

    variable_names = sorted(
        key for key, value in hourly.items()
        if key.startswith("temperature_2m") and isinstance(value, Sequence) and not isinstance(value, (str, bytes))
    )
    if not variable_names:
        raise HourlyForecastFusionError("HOURLY_FORECAST_PAYLOAD_INVALID", ("OPEN_METEO_TEMPERATURE_SERIES_MISSING",))

    estimates: list[HourlyForecastEstimate] = []
    for variable in variable_names:
        unit = str(units.get(variable) or units.get("temperature_2m") or "").strip().upper()
        if unit not in {"°F", "F"}:
            raise HourlyForecastFusionError("HOURLY_FORECAST_UNITS_INVALID", (f"OPEN_METEO_TEMPERATURE_UNIT:{unit or 'missing'}",))
        values = hourly[variable]
        if len(values) != len(times) or target_index >= len(values):
            raise HourlyForecastFusionError("HOURLY_FORECAST_PAYLOAD_INVALID", (f"OPEN_METEO_LENGTH_MISMATCH:{variable}",))
        value = _number(values[target_index], f"OPEN_METEO_TEMPERATURE_INVALID:{variable}")
        model = variable.removeprefix("temperature_2m_") or "OPEN_METEO_DEFAULT"
        estimates.append(
            HourlyForecastEstimate(
                provider="OPEN_METEO",
                model=model.upper(),
                target_time_utc=_iso(target),
                temperature_f=value,
                source_snapshot_id=snapshot_id,
            )
        )
    return estimates


def _target_from_contract(contract: ContractSnapshot) -> datetime:
    prefix = "POINT_IN_TIME:"
    if not contract.observation_window.startswith(prefix):
        raise HourlyForecastFusionError("HOURLY_FORECAST_CONTRACT_INVALID", ("POINT_IN_TIME_WINDOW_REQUIRED",))
    return _parse_aware(contract.observation_window[len(prefix):], "TARGET_TIME_INVALID")


def _parse_open_meteo_utc(value: str) -> datetime:
    text = value.strip()
    if not text:
        raise HourlyForecastFusionError("HOURLY_FORECAST_TEMPORAL_INVALID", ("OPEN_METEO_TIME_INVALID",))
    if text.endswith("Z") or "+" in text[10:]:
        return _parse_aware(text, "OPEN_METEO_TIME_INVALID")
    try:
        parsed = datetime.fromisoformat(text)
    except ValueError as exc:
        raise HourlyForecastFusionError("HOURLY_FORECAST_TEMPORAL_INVALID", ("OPEN_METEO_TIME_INVALID",)) from exc
    return parsed.replace(tzinfo=timezone.utc)


def _parse_aware(value: str, blocker: str) -> datetime:
    text = str(value).strip()
    if text.endswith("Z"):
        text = text[:-1] + "+00:00"
    try:
        parsed = datetime.fromisoformat(text)
    except ValueError as exc:
        raise HourlyForecastFusionError("HOURLY_FORECAST_TEMPORAL_INVALID", (blocker,)) from exc
    if parsed.tzinfo is None:
        raise HourlyForecastFusionError("HOURLY_FORECAST_TEMPORAL_INVALID", (blocker,))
    return parsed.astimezone(timezone.utc)


def _number(value: Any, blocker: str) -> float:
    try:
        number = float(value)
    except (TypeError, ValueError) as exc:
        raise HourlyForecastFusionError("HOURLY_FORECAST_PAYLOAD_INVALID", (blocker,)) from exc
    if number != number or number in {float("inf"), float("-inf")}:
        raise HourlyForecastFusionError("HOURLY_FORECAST_PAYLOAD_INVALID", (blocker,))
    return number


def _iso(value: datetime) -> str:
    return value.astimezone(timezone.utc).isoformat().replace("+00:00", "Z")
