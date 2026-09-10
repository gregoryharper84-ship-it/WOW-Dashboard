from __future__ import annotations

import re
from dataclasses import dataclass
from typing import Any, Mapping

from .contract_rule_acquisition import ContractRuleAcquisitionError, FrozenContractRulePackage


_RULE_RE = re.compile(
    r"^If the (?P<metric>maximum|minimum) temperature recorded at "
    r"(?P<location>.+?) \((?P<location_code>[A-Z0-9_-]+)\) for "
    r"(?P<date>[A-Z][a-z]{2} \d{1,2}, \d{4}), is "
    r"(?P<predicate>.+?)° fahrenheit according to (?P<source>.+?), then the market resolves to Yes\.?$",
    re.IGNORECASE,
)

_BETWEEN_RE = re.compile(r"^between\s+(-?\d+(?:\.\d+)?)-(-?\d+(?:\.\d+)?)$", re.IGNORECASE)
_GREATER_RE = re.compile(r"^greater than\s+(-?\d+(?:\.\d+)?)$", re.IGNORECASE)
_LESS_RE = re.compile(r"^less than\s+(-?\d+(?:\.\d+)?)$", re.IGNORECASE)


@dataclass(frozen=True)
class ParsedTemperatureRule:
    lane: str
    metric: str
    location: str
    settlement_location_code: str
    observation_date_text: str
    units: str
    settlement_source: str
    yes_condition: str
    no_condition: str
    threshold_lower: float | None
    threshold_upper: float | None
    lower_inclusive: bool
    upper_inclusive: bool
    rule_snapshot_id: str
    ticker: str
    can_execute: bool = False


def parse_temperature_rule(package: FrozenContractRulePackage) -> ParsedTemperatureRule:
    """Parse only the exact rule syntax presently emitted by Kalshi temperature markets.

    This parser intentionally does not invent timezone, rounding convention or
    observation-window semantics. Those must be resolved from controlling terms
    before a full ContractSnapshot may be published.
    """
    rule = package.market_rules.rules_primary.strip()
    match = _RULE_RE.match(rule)
    if not match:
        raise ContractRuleAcquisitionError(
            "NO_PLAY_SETTLEMENT_AMBIGUITY", ("TEMPERATURE_RULE_SYNTAX_UNRECOGNIZED",)
        )

    groups = match.groupdict()
    metric_word = groups["metric"].lower()
    lane = "DAILY_HIGH_TEMPERATURE" if metric_word == "maximum" else "DAILY_LOW_TEMPERATURE"
    metric = "daily_max_temperature" if metric_word == "maximum" else "daily_min_temperature"
    source_from_rule = groups["source"].strip().rstrip(".")
    if source_from_rule.casefold() != package.settlement_source.name.casefold():
        raise ContractRuleAcquisitionError(
            "NO_PLAY_SETTLEMENT_AMBIGUITY", ("SETTLEMENT_SOURCE_RULE_SERIES_MISMATCH",)
        )

    predicate = groups["predicate"].strip()
    lower, upper, lower_inclusive, upper_inclusive, normalized_condition = _parse_predicate(predicate)
    _cross_check_strikes(
        package.market_rules.raw_market,
        lower=lower,
        upper=upper,
        lower_inclusive=lower_inclusive,
        upper_inclusive=upper_inclusive,
    )

    return ParsedTemperatureRule(
        lane=lane,
        metric=metric,
        location=groups["location"].strip(),
        settlement_location_code=groups["location_code"].upper(),
        observation_date_text=groups["date"],
        units="F",
        settlement_source=package.settlement_source.name,
        yes_condition=normalized_condition,
        no_condition=f"NOT ({normalized_condition})",
        threshold_lower=lower,
        threshold_upper=upper,
        lower_inclusive=lower_inclusive,
        upper_inclusive=upper_inclusive,
        rule_snapshot_id=package.market_rules.rule_snapshot_id,
        ticker=package.market_rules.ticker,
        can_execute=False,
    )


def _parse_predicate(predicate: str) -> tuple[float | None, float | None, bool, bool, str]:
    between = _BETWEEN_RE.match(predicate)
    if between:
        lower = float(between.group(1))
        upper = float(between.group(2))
        if upper < lower:
            raise ContractRuleAcquisitionError("NO_PLAY_SETTLEMENT_AMBIGUITY", ("TEMPERATURE_RANGE_ORDER_INVALID",))
        return lower, upper, True, True, f"temperature is between {lower:g}°F and {upper:g}°F inclusive"

    greater = _GREATER_RE.match(predicate)
    if greater:
        boundary = float(greater.group(1))
        return boundary, None, False, True, f"temperature is greater than {boundary:g}°F"

    less = _LESS_RE.match(predicate)
    if less:
        boundary = float(less.group(1))
        return None, boundary, True, False, f"temperature is less than {boundary:g}°F"

    raise ContractRuleAcquisitionError(
        "NO_PLAY_SETTLEMENT_AMBIGUITY", ("TEMPERATURE_PREDICATE_UNRECOGNIZED",)
    )


def _cross_check_strikes(
    market: Mapping[str, Any],
    *,
    lower: float | None,
    upper: float | None,
    lower_inclusive: bool,
    upper_inclusive: bool,
) -> None:
    strike_type = str(market.get("strike_type") or "").strip().lower()
    floor = _float_or_none(market.get("floor_strike"))
    cap = _float_or_none(market.get("cap_strike"))

    blockers: list[str] = []
    if lower is not None and upper is not None:
        if strike_type != "between":
            blockers.append("STRIKE_TYPE_RULE_MISMATCH")
        if floor != lower or cap != upper:
            blockers.append("STRIKE_BOUNDS_RULE_MISMATCH")
    elif lower is not None and not lower_inclusive:
        if strike_type != "greater" or floor != lower:
            blockers.append("GREATER_STRIKE_RULE_MISMATCH")
    elif upper is not None and not upper_inclusive:
        if strike_type != "less" or cap != upper:
            blockers.append("LESS_STRIKE_RULE_MISMATCH")
    else:
        blockers.append("STRIKE_SEMANTICS_UNSUPPORTED")

    if blockers:
        raise ContractRuleAcquisitionError(
            "NO_PLAY_SETTLEMENT_AMBIGUITY", tuple(dict.fromkeys(blockers))
        )


def _float_or_none(value: Any) -> float | None:
    if value in (None, ""):
        return None
    try:
        return float(value)
    except (TypeError, ValueError):
        return None
