from __future__ import annotations

import re
from dataclasses import dataclass
from datetime import datetime, timezone
from typing import Any, Mapping

from .contract_rule_acquisition import ContractRuleAcquisitionError, FrozenContractRulePackage
from .contract_resolver import resolve_weather_contract
from .models import ContractSnapshot


# Hourly Weather Index contracts are point-in-time index contracts, not daily
# extrema. The parser therefore requires an exact structured occurrence time,
# an explicit timezone token in the immutable market/event text, and a source
# match to the Series settlement metadata. It never borrows a station, daily
# window, or another exchange's weather rules.
_TZ_OFFSETS = {
    "EST": -5,
    "EDT": -4,
    "CST": -6,
    "CDT": -5,
    "MST": -7,
    "MDT": -6,
    "PST": -8,
    "PDT": -7,
}
_TZ_RE = re.compile(r"\b(EST|EDT|CST|CDT|MST|MDT|PST|PDT)\b", re.IGNORECASE)
_CLOCK_RE = re.compile(r"\b(?P<hour>1[0-2]|0?[1-9])(?::(?P<minute>[0-5]\d))?\s*(?P<ampm>am|pm)\b", re.IGNORECASE)


@dataclass(frozen=True)
class ParsedHourlyTemperatureRule:
    lane: str
    metric: str
    location: str
    settlement_location_code: str
    index_city: str
    observation_time_utc: str
    observation_timestamp_ms: int
    timezone: str
    units: str
    settlement_source: str
    yes_condition: str
    no_condition: str
    threshold_lower: float | None
    threshold_upper: float | None
    lower_inclusive: bool
    upper_inclusive: bool
    rounding_convention: str
    trace_measurement_rules: str
    market_close_time: str
    rule_snapshot_id: str
    ticker: str
    can_execute: bool = False

    def to_contract_snapshot(self, package: FrozenContractRulePackage) -> ContractSnapshot:
        market = package.market_rules
        raw = {
            "market_title": market.title,
            "contract_title": market.subtitle or market.title,
            "ticker": self.ticker,
            "lane": self.lane,
            "yes_condition": self.yes_condition,
            "no_condition": self.no_condition,
            "location": self.location,
            "metric": self.metric,
            "units": self.units,
            "observation_window": f"POINT_IN_TIME:{self.observation_time_utc}",
            "timezone": self.timezone,
            "settlement_source": self.settlement_source,
            "settlement_location_type": "SOURCE_LOCATION_CODE",
            "settlement_location_code": self.settlement_location_code,
            "settlement_station_id": None,
            "settlement_station_name": None,
            "rounding_convention": self.rounding_convention,
            "trace_measurement_rules": self.trace_measurement_rules,
            "market_close_time": self.market_close_time,
            "rule_snapshot_id": self.rule_snapshot_id,
            "threshold_lower": self.threshold_lower,
            "threshold_upper": self.threshold_upper,
            "lower_inclusive": self.lower_inclusive,
            "upper_inclusive": self.upper_inclusive,
        }
        return resolve_weather_contract(raw)


def parse_hourly_temperature_rule(
    package: FrozenContractRulePackage,
    *,
    index_city: str,
    expected_location: str | None = None,
) -> ParsedHourlyTemperatureRule:
    """Parse an hourly Kalshi Weather Index contract without inference.

    The exact market rule text remains controlling. Structured strike metadata
    defines the machine predicate only after the rule text, Series source, and
    point-in-time event identity are mutually consistent.

    `index_city` is an explicit caller-supplied key that must be the exact city
    used by the Kalshi weather-index endpoint. It is never derived from a nearby
    station or city-name heuristic.
    """
    index_city = _clean_city(index_city)
    market = package.market_rules
    raw_market = market.raw_market
    raw_event = package.raw_event
    combined = market.combined_rule_text

    if not combined:
        raise _ambiguous("HOURLY_RULE_TEXT_MISSING")

    source = package.settlement_source.name.strip()
    if not source or source.casefold() not in combined.casefold():
        raise _ambiguous("HOURLY_SETTLEMENT_SOURCE_NOT_NAMED_IN_RULE")

    occurrence = _resolve_occurrence_time(raw_market, raw_event)
    timezone_token = _resolve_timezone_token(combined, market.title, market.subtitle, raw_event)
    _cross_check_clock_text(occurrence, timezone_token, combined, market.title, raw_event)

    location = _resolve_location(raw_market, raw_event, expected_location=expected_location)
    strike = _resolve_strike(raw_market, combined)

    close_time = _required_timestamp(market.close_time, "HOURLY_MARKET_CLOSE_TIME_MISSING")
    observation_utc = occurrence.astimezone(timezone.utc)
    observation_iso = observation_utc.isoformat().replace("+00:00", "Z")
    observation_ms = int(observation_utc.timestamp() * 1000)

    offset = _TZ_OFFSETS[timezone_token]
    sign = "+" if offset >= 0 else "-"
    timezone_text = f"{timezone_token}(UTC{sign}{abs(offset):02d}:00)"

    # The engine applies no second rounding layer to the official published
    # Kalshi Weather Index. Any source/index construction happens upstream and
    # is preserved as evidence; absent explicit rule text we do not invent a
    # station-level rounding rule.
    rounding = "USE_OFFICIAL_KALSHI_WEATHER_INDEX_AS_PUBLISHED;NO_ENGINE_ROUNDING"
    trace = "POINT_INDEX_VALUE;PRESERVE_INCOMPLETE_MINUTES;NO_STATION_SUBSTITUTION"

    return ParsedHourlyTemperatureRule(
        lane="HOURLY_TEMPERATURE",
        metric="kalshi_weather_index_point_temperature",
        location=location,
        settlement_location_code=f"KALSHI_WEATHER_INDEX:{index_city}",
        index_city=index_city,
        observation_time_utc=observation_iso,
        observation_timestamp_ms=observation_ms,
        timezone=timezone_text,
        units="F",
        settlement_source=source,
        yes_condition=strike[0],
        no_condition=f"NOT ({strike[0]})",
        threshold_lower=strike[1],
        threshold_upper=strike[2],
        lower_inclusive=strike[3],
        upper_inclusive=strike[4],
        rounding_convention=rounding,
        trace_measurement_rules=trace,
        market_close_time=close_time,
        rule_snapshot_id=market.rule_snapshot_id,
        ticker=market.ticker,
        can_execute=False,
    )


def _resolve_occurrence_time(market: Mapping[str, Any], event: Mapping[str, Any]) -> datetime:
    values: list[datetime] = []
    for obj, field in ((market, "occurrence_datetime"), (event, "occurrence_datetime")):
        value = obj.get(field)
        if value not in (None, ""):
            values.append(_parse_timestamp(str(value), f"HOURLY_{field.upper()}_INVALID"))
    if not values:
        raise _ambiguous("HOURLY_OCCURRENCE_TIME_MISSING")
    first = values[0]
    if any(value != first for value in values[1:]):
        raise _ambiguous("HOURLY_OCCURRENCE_TIME_CONFLICT")
    return first


def _resolve_timezone_token(rule_text: str, title: str, subtitle: str, event: Mapping[str, Any]) -> str:
    blobs = [rule_text, title, subtitle, str(event.get("title") or ""), str(event.get("sub_title") or "")]
    tokens = {match.group(1).upper() for blob in blobs for match in _TZ_RE.finditer(blob or "")}
    if not tokens:
        raise _ambiguous("HOURLY_TIMEZONE_TOKEN_MISSING")
    if len(tokens) != 1:
        raise _ambiguous("HOURLY_TIMEZONE_TOKEN_CONFLICT")
    return next(iter(tokens))


def _cross_check_clock_text(
    occurrence: datetime,
    timezone_token: str,
    rule_text: str,
    title: str,
    event: Mapping[str, Any],
) -> None:
    blobs = [rule_text, title, str(event.get("title") or "")]
    clock_values: set[tuple[int, int]] = set()
    for blob in blobs:
        for match in _CLOCK_RE.finditer(blob or ""):
            hour = int(match.group("hour")) % 12
            if match.group("ampm").lower() == "pm":
                hour += 12
            clock_values.add((hour, int(match.group("minute") or 0)))
    if not clock_values:
        raise _ambiguous("HOURLY_LOCAL_CLOCK_MISSING")
    if len(clock_values) != 1:
        raise _ambiguous("HOURLY_LOCAL_CLOCK_CONFLICT")

    local_hour, local_minute = next(iter(clock_values))
    offset = _TZ_OFFSETS[timezone_token]
    expected_utc_hour = (local_hour - offset) % 24
    if occurrence.astimezone(timezone.utc).hour != expected_utc_hour or occurrence.astimezone(timezone.utc).minute != local_minute:
        raise _ambiguous("HOURLY_OCCURRENCE_TIMEZONE_MISMATCH")


def _resolve_location(
    market: Mapping[str, Any], event: Mapping[str, Any], *, expected_location: str | None
) -> str:
    candidates = []
    for value in (
        event.get("title"),
        market.get("title"),
        market.get("subtitle"),
    ):
        text = str(value or "").strip()
        if text:
            candidates.append(text)

    if expected_location:
        expected = expected_location.strip()
        if not expected:
            raise _ambiguous("HOURLY_EXPECTED_LOCATION_EMPTY")
        if not any(expected.casefold() in text.casefold() for text in candidates):
            raise _ambiguous("HOURLY_EXPECTED_LOCATION_NOT_PRESENT")
        return expected

    # Without an explicit expected location, only accept the canonical product
    # phrases that identify the KEX weather-index geography exactly. Do not map
    # arbitrary titles to stations or nearby cities.
    canonical = (
        "New York City",
        "Chicago Metro Area",
        "Coastal Los Angeles",
        "Miami",
        "Dallas Fort-Worth",
        "Houston",
        "Greater Boston",
        "Kansas City",
        "Minneapolis-St. Paul",
        "Southeast Michigan",
        "Philadelphia & Delaware Valley",
        "Puget Sound",
        "San Francisco Bay Area",
    )
    matches = {name for name in canonical if any(name.casefold() in text.casefold() for text in candidates)}
    if len(matches) != 1:
        raise _ambiguous("HOURLY_LOCATION_UNRESOLVED")
    return next(iter(matches))


def _resolve_strike(market: Mapping[str, Any], rule_text: str) -> tuple[str, float | None, float | None, bool, bool]:
    strike_type = str(market.get("strike_type") or "").strip().lower()
    floor = _float_or_none(market.get("floor_strike"))
    cap = _float_or_none(market.get("cap_strike"))
    text = rule_text.casefold()

    if strike_type == "greater":
        if floor is None:
            raise _ambiguous("HOURLY_GREATER_FLOOR_MISSING")
        if not _rule_contains_number(rule_text, floor):
            raise _ambiguous("HOURLY_RULE_STRIKE_VALUE_MISMATCH")
        if not any(word in text for word in ("above", "greater than", "more than")):
            raise _ambiguous("HOURLY_RULE_STRIKE_OPERATOR_MISMATCH")
        return f"index > {floor:g}°F", floor, None, False, True

    if strike_type == "less":
        if cap is None:
            raise _ambiguous("HOURLY_LESS_CAP_MISSING")
        if not _rule_contains_number(rule_text, cap):
            raise _ambiguous("HOURLY_RULE_STRIKE_VALUE_MISMATCH")
        if not any(word in text for word in ("below", "less than")):
            raise _ambiguous("HOURLY_RULE_STRIKE_OPERATOR_MISMATCH")
        return f"index < {cap:g}°F", None, cap, True, False

    if strike_type == "between":
        if floor is None or cap is None or cap < floor:
            raise _ambiguous("HOURLY_BETWEEN_BOUNDS_INVALID")
        if not _rule_contains_number(rule_text, floor) or not _rule_contains_number(rule_text, cap):
            raise _ambiguous("HOURLY_RULE_STRIKE_VALUE_MISMATCH")
        if "between" not in text:
            raise _ambiguous("HOURLY_RULE_STRIKE_OPERATOR_MISMATCH")
        return f"{floor:g}°F <= index <= {cap:g}°F", floor, cap, True, True

    # Equality / at-least / at-most require explicit structured semantics from
    # the live API before enabling. They must not be approximated as a nearby
    # greater/less strike.
    raise _ambiguous(f"HOURLY_STRIKE_TYPE_UNSUPPORTED:{strike_type or 'missing'}")


def _rule_contains_number(text: str, value: float) -> bool:
    target = f"{value:g}"
    return re.search(rf"(?<![\d.]){re.escape(target)}(?:0+)?(?![\d.])", text) is not None


def _clean_city(value: str) -> str:
    text = str(value or "").strip().lower()
    if not text or not re.fullmatch(r"[a-z0-9-]+", text):
        raise _ambiguous("HOURLY_INDEX_CITY_INVALID")
    return text


def _required_timestamp(value: str | None, blocker: str) -> str:
    if not value:
        raise _ambiguous(blocker)
    parsed = _parse_timestamp(value, blocker)
    return parsed.astimezone(timezone.utc).isoformat().replace("+00:00", "Z")


def _parse_timestamp(value: str, blocker: str) -> datetime:
    text = value.strip()
    if text.endswith("Z"):
        text = text[:-1] + "+00:00"
    try:
        parsed = datetime.fromisoformat(text)
    except ValueError as exc:
        raise _ambiguous(blocker) from exc
    if parsed.tzinfo is None:
        raise _ambiguous(blocker)
    return parsed.astimezone(timezone.utc)


def _float_or_none(value: Any) -> float | None:
    if value in (None, ""):
        return None
    try:
        return float(value)
    except (TypeError, ValueError):
        return None


def _ambiguous(*blockers: str) -> ContractRuleAcquisitionError:
    return ContractRuleAcquisitionError("NO_PLAY_SETTLEMENT_AMBIGUITY", tuple(blockers))
