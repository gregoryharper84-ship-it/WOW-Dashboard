from __future__ import annotations

from dataclasses import dataclass
from typing import Any, Mapping

from .models import ContractSnapshot


class SettlementResolutionError(ValueError):
    """Raised when the exact contract rules do not resolve settlement identity."""


@dataclass(frozen=True)
class ResolvedSettlement:
    source: str
    location_type: str
    station_id: str | None
    station_name: str | None
    latitude: float | None
    longitude: float | None
    timezone: str
    units: str
    rounding_convention: str
    observation_window: str


def _text(rules: Mapping[str, Any], key: str) -> str:
    value = rules.get(key)
    if not isinstance(value, str) or not value.strip():
        raise SettlementResolutionError(f"CONTRACT_RULE_MISSING:{key}")
    return value.strip()


def resolve_settlement(rules: Mapping[str, Any]) -> ResolvedSettlement:
    """Resolve settlement only from a frozen exact-contract rule snapshot.

    No historical city table, nearby station, forecast provider, or market title
    may fill a missing settlement field. Missing identity fails closed.
    """
    source = _text(rules, "settlement_source")
    location_type = _text(rules, "settlement_location_type").upper()
    timezone = _text(rules, "timezone")
    units = _text(rules, "units")
    rounding = _text(rules, "rounding_convention")
    observation_window = _text(rules, "observation_window")

    station_id = station_name = None
    latitude = longitude = None

    if location_type == "STATION":
        station_id = _text(rules, "settlement_station_id")
        station_name = _text(rules, "settlement_station_name")
    elif location_type == "COORDINATE":
        try:
            latitude = float(rules["settlement_latitude"])
            longitude = float(rules["settlement_longitude"])
        except (KeyError, TypeError, ValueError) as exc:
            raise SettlementResolutionError("CONTRACT_RULE_MISSING:settlement_coordinate") from exc
        if not (-90.0 <= latitude <= 90.0 and -180.0 <= longitude <= 180.0):
            raise SettlementResolutionError("CONTRACT_RULE_INVALID:settlement_coordinate")
    else:
        raise SettlementResolutionError("SETTLEMENT_LOCATION_TYPE_UNSUPPORTED")

    return ResolvedSettlement(
        source=source,
        location_type=location_type,
        station_id=station_id,
        station_name=station_name,
        latitude=latitude,
        longitude=longitude,
        timezone=timezone,
        units=units,
        rounding_convention=rounding,
        observation_window=observation_window,
    )


def apply_resolved_settlement(contract: ContractSnapshot, resolved: ResolvedSettlement) -> ContractSnapshot:
    """Return a copy whose settlement identity is populated from frozen rules."""
    from dataclasses import replace

    return replace(
        contract,
        settlement_source=resolved.source,
        settlement_location_type=resolved.location_type,
        settlement_station_id=resolved.station_id,
        settlement_station_name=resolved.station_name,
        settlement_latitude=resolved.latitude,
        settlement_longitude=resolved.longitude,
        timezone=resolved.timezone,
        units=resolved.units,
        rounding_convention=resolved.rounding_convention,
        observation_window=resolved.observation_window,
    )
