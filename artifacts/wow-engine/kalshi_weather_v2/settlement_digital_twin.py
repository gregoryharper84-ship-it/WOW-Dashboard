from __future__ import annotations

from dataclasses import asdict, dataclass
from enum import Enum
from math import isfinite
from typing import Any, Mapping, Sequence

from .models import ContractSnapshot


class SettlementTwinError(ValueError):
    def __init__(self, code: str, detail: str):
        self.code = code
        self.detail = detail
        super().__init__(f"{code}: {detail}")


class PredicateKind(str, Enum):
    ABOVE = "ABOVE"
    BELOW = "BELOW"
    RANGE = "RANGE"
    OUTSIDE_RANGE = "OUTSIDE_RANGE"


class SettlementState(str, Enum):
    OPEN = "OPEN"
    LOCKED_YES = "LOCKED_YES"
    LOCKED_NO = "LOCKED_NO"
    SETTLED = "SETTLED"
    AMBIGUOUS = "AMBIGUOUS"


@dataclass(frozen=True)
class SettlementPredicate:
    kind: PredicateKind
    lower: float | None = None
    upper: float | None = None
    lower_inclusive: bool = True
    upper_inclusive: bool = True

    def __post_init__(self) -> None:
        if self.kind is PredicateKind.ABOVE and self.lower is None:
            raise SettlementTwinError("SETTLEMENT_PREDICATE_INVALID", "ABOVE requires lower")
        if self.kind is PredicateKind.BELOW and self.upper is None:
            raise SettlementTwinError("SETTLEMENT_PREDICATE_INVALID", "BELOW requires upper")
        if self.kind in {PredicateKind.RANGE, PredicateKind.OUTSIDE_RANGE}:
            if self.lower is None or self.upper is None:
                raise SettlementTwinError("SETTLEMENT_PREDICATE_INVALID", f"{self.kind.value} requires lower and upper")
            if self.lower > self.upper:
                raise SettlementTwinError("SETTLEMENT_PREDICATE_INVALID", "lower exceeds upper")

    def matches(self, value: float) -> bool:
        if not isfinite(float(value)):
            raise SettlementTwinError("SETTLEMENT_VALUE_INVALID", "non-finite value")
        value = float(value)
        if self.kind is PredicateKind.ABOVE:
            return value >= float(self.lower) if self.lower_inclusive else value > float(self.lower)
        if self.kind is PredicateKind.BELOW:
            return value <= float(self.upper) if self.upper_inclusive else value < float(self.upper)
        lower_ok = value >= float(self.lower) if self.lower_inclusive else value > float(self.lower)
        upper_ok = value <= float(self.upper) if self.upper_inclusive else value < float(self.upper)
        inside = lower_ok and upper_ok
        return not inside if self.kind is PredicateKind.OUTSIDE_RANGE else inside

    def payload(self) -> Mapping[str, Any]:
        out = asdict(self)
        out["kind"] = self.kind.value
        return out


@dataclass(frozen=True)
class SettlementDigitalTwin:
    twin_snapshot_id: str
    rule_snapshot_id: str
    ticker: str
    built_at: str
    state_as_of: str
    settlement_source_name: str
    settlement_source_url: str | None
    settlement_location_code: str | None
    settlement_station_id: str | None
    station_timezone: str
    observation_window: str
    metric: str
    units: str
    rounding_convention: str
    yes_predicate: SettlementPredicate
    no_predicate: SettlementPredicate
    possible_outcomes: tuple[str, ...]
    impossible_outcomes: tuple[str, ...]
    remaining_outcomes: tuple[str, ...]
    observed_value: float | None
    observed_extreme: float | None
    settlement_state: SettlementState
    source_snapshot_ids: tuple[str, ...]
    blockers: tuple[str, ...]
    warnings: tuple[str, ...]
    method_version: str = "SETTLEMENT_DIGITAL_TWIN_V1"
    market_data_used_as_weather_probability_input: bool = False
    can_execute: bool = False

    def __post_init__(self) -> None:
        required = {
            "twin_snapshot_id": self.twin_snapshot_id,
            "rule_snapshot_id": self.rule_snapshot_id,
            "ticker": self.ticker,
            "built_at": self.built_at,
            "state_as_of": self.state_as_of,
            "settlement_source_name": self.settlement_source_name,
            "station_timezone": self.station_timezone,
            "observation_window": self.observation_window,
            "metric": self.metric,
            "units": self.units,
            "rounding_convention": self.rounding_convention,
        }
        missing = [name for name, value in required.items() if not str(value or "").strip()]
        if missing:
            raise SettlementTwinError("SETTLEMENT_TWIN_REQUIRED_FIELD_MISSING", ",".join(missing))
        if not (self.settlement_location_code or self.settlement_station_id):
            raise SettlementTwinError("SETTLEMENT_TWIN_LOCATION_MISSING", self.ticker)
        if self.market_data_used_as_weather_probability_input:
            raise SettlementTwinError("MARKET_DATA_PROBABILITY_CONTAMINATION", self.ticker)
        if self.can_execute:
            raise SettlementTwinError("KALSHI_WEATHER_EXECUTION_FORBIDDEN", self.ticker)

    def persistence_row(self) -> Mapping[str, Any]:
        return {
            "twin_snapshot_id": self.twin_snapshot_id,
            "rule_snapshot_id": self.rule_snapshot_id,
            "ticker": self.ticker,
            "built_at": self.built_at,
            "state_as_of": self.state_as_of,
            "settlement_source_name": self.settlement_source_name,
            "settlement_source_url": self.settlement_source_url,
            "settlement_location_code": self.settlement_location_code,
            "settlement_station_id": self.settlement_station_id,
            "station_timezone": self.station_timezone,
            "observation_window": self.observation_window,
            "metric": self.metric,
            "units": self.units,
            "rounding_convention": self.rounding_convention,
            "yes_predicate": dict(self.yes_predicate.payload()),
            "no_predicate": dict(self.no_predicate.payload()),
            "possible_outcomes": list(self.possible_outcomes),
            "impossible_outcomes": list(self.impossible_outcomes),
            "remaining_outcomes": list(self.remaining_outcomes),
            "observed_value": self.observed_value,
            "observed_extreme": self.observed_extreme,
            "settlement_state": self.settlement_state.value,
            "source_snapshot_ids": list(self.source_snapshot_ids),
            "blockers": list(self.blockers),
            "warnings": list(self.warnings),
            "method_version": self.method_version,
            "market_data_used_as_weather_probability_input": False,
            "can_execute": False,
        }


def predicate_from_contract(contract: ContractSnapshot) -> SettlementPredicate:
    if contract.threshold_lower is not None and contract.threshold_upper is not None:
        return SettlementPredicate(
            kind=PredicateKind.RANGE,
            lower=float(contract.threshold_lower),
            upper=float(contract.threshold_upper),
            lower_inclusive=bool(contract.lower_inclusive),
            upper_inclusive=bool(contract.upper_inclusive),
        )
    if contract.threshold_lower is not None:
        return SettlementPredicate(
            kind=PredicateKind.ABOVE,
            lower=float(contract.threshold_lower),
            lower_inclusive=bool(contract.lower_inclusive),
        )
    if contract.threshold_upper is not None:
        return SettlementPredicate(
            kind=PredicateKind.BELOW,
            upper=float(contract.threshold_upper),
            upper_inclusive=bool(contract.upper_inclusive),
        )
    raise SettlementTwinError("SETTLEMENT_PREDICATE_UNRESOLVED", contract.ticker)


def complement_predicate(predicate: SettlementPredicate) -> SettlementPredicate:
    if predicate.kind is PredicateKind.ABOVE:
        return SettlementPredicate(
            kind=PredicateKind.BELOW,
            upper=predicate.lower,
            upper_inclusive=not predicate.lower_inclusive,
        )
    if predicate.kind is PredicateKind.BELOW:
        return SettlementPredicate(
            kind=PredicateKind.ABOVE,
            lower=predicate.upper,
            lower_inclusive=not predicate.upper_inclusive,
        )
    if predicate.kind is PredicateKind.RANGE:
        return SettlementPredicate(
            kind=PredicateKind.OUTSIDE_RANGE,
            lower=predicate.lower,
            upper=predicate.upper,
            lower_inclusive=predicate.lower_inclusive,
            upper_inclusive=predicate.upper_inclusive,
        )
    if predicate.kind is PredicateKind.OUTSIDE_RANGE:
        return SettlementPredicate(
            kind=PredicateKind.RANGE,
            lower=predicate.lower,
            upper=predicate.upper,
            lower_inclusive=predicate.lower_inclusive,
            upper_inclusive=predicate.upper_inclusive,
        )
    raise SettlementTwinError("SETTLEMENT_COMPLEMENT_UNSUPPORTED", predicate.kind.value)


def build_atomic_temperature_twin(
    *,
    twin_snapshot_id: str,
    contract: ContractSnapshot,
    built_at: str,
    state_as_of: str,
    settlement_source_url: str | None,
    source_snapshot_ids: Sequence[str],
    observed_value: float | None = None,
    observed_extreme: float | None = None,
    settled: bool = False,
) -> SettlementDigitalTwin:
    yes = predicate_from_contract(contract)
    no = complement_predicate(yes)
    state = SettlementState.OPEN

    if settled and observed_value is not None:
        state = SettlementState.SETTLED
    elif contract.lane == "DAILY_HIGH_TEMPERATURE" and observed_extreme is not None:
        # A daily high can only stay flat or increase. Only irreversible locks are
        # encoded here; ordinary inside-bracket states remain OPEN.
        if yes.kind is PredicateKind.ABOVE and yes.matches(observed_extreme):
            state = SettlementState.LOCKED_YES
        elif yes.kind is PredicateKind.BELOW and not yes.matches(observed_extreme):
            state = SettlementState.LOCKED_NO
        elif yes.kind is PredicateKind.RANGE and yes.upper is not None:
            upper_crossed = (
                observed_extreme > float(yes.upper)
                if yes.upper_inclusive
                else observed_extreme >= float(yes.upper)
            )
            if upper_crossed:
                state = SettlementState.LOCKED_NO

    return SettlementDigitalTwin(
        twin_snapshot_id=twin_snapshot_id,
        rule_snapshot_id=contract.rule_snapshot_id,
        ticker=contract.ticker,
        built_at=built_at,
        state_as_of=state_as_of,
        settlement_source_name=contract.settlement_source,
        settlement_source_url=settlement_source_url,
        settlement_location_code=contract.settlement_location_code,
        settlement_station_id=contract.settlement_station_id,
        station_timezone=contract.timezone,
        observation_window=contract.observation_window,
        metric=contract.metric,
        units=contract.units,
        rounding_convention=contract.rounding_convention,
        yes_predicate=yes,
        no_predicate=no,
        possible_outcomes=("YES", "NO"),
        impossible_outcomes=("NO",) if state is SettlementState.LOCKED_YES else (("YES",) if state is SettlementState.LOCKED_NO else ()),
        remaining_outcomes=("YES",) if state is SettlementState.LOCKED_YES else (("NO",) if state is SettlementState.LOCKED_NO else ("YES", "NO")),
        observed_value=observed_value,
        observed_extreme=observed_extreme,
        settlement_state=state,
        source_snapshot_ids=tuple(str(x) for x in source_snapshot_ids if str(x)),
        blockers=(),
        warnings=(),
    )
