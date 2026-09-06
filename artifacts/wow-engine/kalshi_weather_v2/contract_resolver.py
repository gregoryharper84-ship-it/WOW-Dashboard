from __future__ import annotations

from dataclasses import dataclass
from typing import Any, Mapping

from .models import ContractSnapshot


SUPPORTED_LANES = {
    "DAILY_HIGH_TEMPERATURE",
    "DAILY_LOW_TEMPERATURE",
    "HOURLY_TEMPERATURE",
}


@dataclass(frozen=True)
class ContractResolutionError(ValueError):
    code: str
    blockers: tuple[str, ...]

    def __str__(self) -> str:
        return f"{self.code}: {', '.join(self.blockers)}"


def resolve_weather_contract(raw: Mapping[str, Any]) -> ContractSnapshot:
    """Normalize already-fetched Kalshi rule metadata into an exact weather contract.

    This function never guesses a settlement station, coordinate, source,
    threshold, timezone, or rounding rule. Those facts must come from the
    live/frozen contract-rule acquisition layer.
    """
    blockers: list[str] = []

    required_text = (
        "market_title", "contract_title", "ticker", "lane", "yes_condition",
        "no_condition", "location", "metric", "units", "observation_window",
        "timezone", "settlement_source", "rounding_convention",
        "trace_measurement_rules", "market_close_time", "rule_snapshot_id",
    )
    values: dict[str, Any] = {}
    for key in required_text:
        value = raw.get(key)
        if not isinstance(value, str) or not value.strip():
            blockers.append(f"CONTRACT_FIELD_MISSING:{key}")
        else:
            values[key] = value.strip()

    lane = str(raw.get("lane") or "").strip().upper()
    if lane and lane not in SUPPORTED_LANES:
        blockers.append(f"WEATHER_LANE_UNSUPPORTED:{lane}")

    units = str(raw.get("units") or "").strip().upper().replace("°", "")
    # V2's first calibrated core is Fahrenheit-native. Celsius support should
    # be added with an explicit contract conversion/audit layer, not silently.
    if units and units not in {"F", "FAHRENHEIT"}:
        blockers.append(f"WEATHER_UNITS_UNSUPPORTED:{units}")

    lower = _optional_float(raw.get("threshold_lower"), "threshold_lower", blockers)
    upper = _optional_float(raw.get("threshold_upper"), "threshold_upper", blockers)
    if lower is None and upper is None:
        blockers.append("CONTRACT_THRESHOLD_MISSING")
    if lower is not None and upper is not None and upper < lower:
        blockers.append("CONTRACT_THRESHOLD_ORDER_INVALID")

    station_id = _clean_optional(raw.get("settlement_station_id"))
    station_name = _clean_optional(raw.get("settlement_station_name"))
    latitude = _optional_float(raw.get("settlement_latitude"), "settlement_latitude", blockers)
    longitude = _optional_float(raw.get("settlement_longitude"), "settlement_longitude", blockers)
    explicit_type = _clean_optional(raw.get("settlement_location_type"))

    station_present = bool(station_id or station_name)
    coordinate_present = latitude is not None or longitude is not None

    if station_present and coordinate_present and not explicit_type:
        blockers.append("SETTLEMENT_LOCATION_TYPE_REQUIRED_WHEN_MULTIPLE_IDENTITIES_PRESENT")
        location_type = ""
    elif explicit_type:
        location_type = explicit_type.upper()
    elif station_present:
        location_type = "STATION"
    elif coordinate_present:
        location_type = "COORDINATE"
    else:
        blockers.append("SETTLEMENT_LOCATION_UNRESOLVED")
        location_type = ""

    if location_type == "STATION":
        if not station_id or not station_name:
            blockers.append("SETTLEMENT_STATION_UNRESOLVED")
    elif location_type == "COORDINATE":
        if latitude is None or longitude is None:
            blockers.append("SETTLEMENT_COORDINATE_UNRESOLVED")
        else:
            if not (-90.0 <= latitude <= 90.0):
                blockers.append("SETTLEMENT_LATITUDE_INVALID")
            if not (-180.0 <= longitude <= 180.0):
                blockers.append("SETTLEMENT_LONGITUDE_INVALID")
    elif location_type:
        blockers.append(f"SETTLEMENT_LOCATION_TYPE_UNSUPPORTED:{location_type}")

    if blockers:
        settlement_block = any(
            b.startswith("SETTLEMENT_") or b.startswith("CONTRACT_FIELD_MISSING:settlement")
            for b in blockers
        )
        code = "NO_PLAY_SETTLEMENT_AMBIGUITY" if settlement_block else "NO_PLAY_DATA_INSUFFICIENT"
        raise ContractResolutionError(code=code, blockers=tuple(dict.fromkeys(blockers)))

    return ContractSnapshot(
        market_title=values["market_title"],
        contract_title=values["contract_title"],
        ticker=values["ticker"],
        lane=lane,
        yes_condition=values["yes_condition"],
        no_condition=values["no_condition"],
        location=values["location"],
        metric=values["metric"],
        units="F",
        observation_window=values["observation_window"],
        timezone=values["timezone"],
        settlement_source=values["settlement_source"],
        settlement_station_id=station_id,
        settlement_station_name=station_name,
        rounding_convention=values["rounding_convention"],
        trace_measurement_rules=values["trace_measurement_rules"],
        market_close_time=values["market_close_time"],
        rule_snapshot_id=values["rule_snapshot_id"],
        threshold_lower=lower,
        threshold_upper=upper,
        lower_inclusive=bool(raw.get("lower_inclusive", True)),
        upper_inclusive=bool(raw.get("upper_inclusive", True)),
        settlement_location_type=location_type,
        settlement_latitude=latitude,
        settlement_longitude=longitude,
    )


def _clean_optional(value: Any) -> str | None:
    if value is None:
        return None
    text = str(value).strip()
    return text or None


def _optional_float(value: Any, field: str, blockers: list[str]) -> float | None:
    if value is None or value == "":
        return None
    try:
        return float(value)
    except (TypeError, ValueError):
        blockers.append(f"CONTRACT_NUMERIC_FIELD_INVALID:{field}")
        return None
