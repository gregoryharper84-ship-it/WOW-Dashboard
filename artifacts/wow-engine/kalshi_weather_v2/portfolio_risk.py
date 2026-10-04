from __future__ import annotations

from dataclasses import dataclass
from datetime import datetime, timezone
from enum import Enum
import hashlib
import json
import math
from typing import Mapping, Sequence


PORTFOLIO_RISK_ENGINE_VERSION = "KALSHI_WEATHER_V1_4_PORTFOLIO_SCENARIO_R1"


class PortfolioRiskError(ValueError):
    pass


class PositionSide(str, Enum):
    YES = "YES"
    NO = "NO"


class DependenceMode(str, Enum):
    SAME_EVENT_EXACT = "SAME_EVENT_EXACT"
    REGIONAL_FACTOR_SCENARIOS = "REGIONAL_FACTOR_SCENARIOS"


@dataclass(frozen=True)
class WeatherEventDescriptor:
    event_key: str
    lane: str
    settlement_source: str
    settlement_location_code: str
    observation_window: str
    metric: str
    units: str
    region_id: str | None = None
    factor_ids: tuple[str, ...] = ()
    can_execute: bool = False

    def __post_init__(self) -> None:
        for name in (
            "event_key",
            "lane",
            "settlement_source",
            "settlement_location_code",
            "observation_window",
            "metric",
            "units",
        ):
            if not str(getattr(self, name) or "").strip():
                raise PortfolioRiskError(f"WEATHER_EVENT_{name.upper()}_MISSING")
        if any(not str(item).strip() for item in self.factor_ids):
            raise PortfolioRiskError("WEATHER_EVENT_FACTOR_ID_INVALID")
        if len(set(self.factor_ids)) != len(self.factor_ids):
            raise PortfolioRiskError("WEATHER_EVENT_FACTOR_ID_DUPLICATE")
        expected = weather_event_key(
            lane=self.lane,
            settlement_source=self.settlement_source,
            settlement_location_code=self.settlement_location_code,
            observation_window=self.observation_window,
            metric=self.metric,
            units=self.units,
        )
        if self.event_key != expected:
            raise PortfolioRiskError("WEATHER_EVENT_IDENTITY_MISMATCH")
        if self.can_execute:
            raise PortfolioRiskError("WEATHER_EVENT_EXECUTION_PROHIBITED")

    def canonical(self) -> dict[str, object]:
        return {
            "event_key": self.event_key,
            "lane": self.lane,
            "settlement_source": self.settlement_source,
            "settlement_location_code": self.settlement_location_code,
            "observation_window": self.observation_window,
            "metric": self.metric,
            "units": self.units,
            "region_id": self.region_id,
            "factor_ids": sorted(self.factor_ids),
            "can_execute": False,
        }


@dataclass(frozen=True)
class PortfolioPosition:
    position_id: str
    ticker: str
    rule_snapshot_id: str
    prediction_id: str
    market_snapshot_id: str
    prediction_time: str
    market_time: str
    event: WeatherEventDescriptor
    side: PositionSide
    quantity: float
    entry_cost_per_contract: float
    threshold_lower: float | None = None
    threshold_upper: float | None = None
    lower_inclusive: bool = True
    upper_inclusive: bool = True
    model_p_yes: float | None = None
    market_price_used_as_weather_input: bool = False
    risk_state_used_as_weather_input: bool = False
    can_execute: bool = False

    def __post_init__(self) -> None:
        for name in (
            "position_id",
            "ticker",
            "rule_snapshot_id",
            "prediction_id",
            "market_snapshot_id",
        ):
            if not str(getattr(self, name) or "").strip():
                raise PortfolioRiskError(f"PORTFOLIO_POSITION_{name.upper()}_MISSING")
        try:
            normalized_side = PositionSide(self.side)
        except (TypeError, ValueError) as exc:
            raise PortfolioRiskError("PORTFOLIO_POSITION_SIDE_INVALID") from exc
        object.__setattr__(self, "side", normalized_side)
        _timestamp(self.prediction_time, "PORTFOLIO_POSITION_PREDICTION_TIME")
        _timestamp(self.market_time, "PORTFOLIO_POSITION_MARKET_TIME")
        _positive(self.quantity, "PORTFOLIO_POSITION_QUANTITY_INVALID")
        _strict_probability(self.entry_cost_per_contract, "PORTFOLIO_POSITION_ENTRY_COST")
        if self.model_p_yes is not None:
            _strict_probability(self.model_p_yes, "PORTFOLIO_POSITION_MODEL_P_YES")
        if self.threshold_lower is None and self.threshold_upper is None:
            raise PortfolioRiskError("PORTFOLIO_POSITION_THRESHOLD_MISSING")
        if self.threshold_lower is not None and not _finite_number(self.threshold_lower):
            raise PortfolioRiskError("PORTFOLIO_POSITION_THRESHOLD_INVALID")
        if self.threshold_upper is not None and not _finite_number(self.threshold_upper):
            raise PortfolioRiskError("PORTFOLIO_POSITION_THRESHOLD_INVALID")
        if (
            self.threshold_lower is not None
            and self.threshold_upper is not None
            and float(self.threshold_upper) < float(self.threshold_lower)
        ):
            raise PortfolioRiskError("PORTFOLIO_POSITION_THRESHOLD_ORDER_INVALID")
        if self.market_price_used_as_weather_input:
            raise PortfolioRiskError("MARKET_PRICE_WEATHER_INPUT_PROHIBITED")
        if self.risk_state_used_as_weather_input:
            raise PortfolioRiskError("PORTFOLIO_STATE_WEATHER_INPUT_PROHIBITED")
        if self.can_execute:
            raise PortfolioRiskError("PORTFOLIO_POSITION_EXECUTION_PROHIBITED")

    def yes_outcome(self, settled_value: float) -> bool:
        if not _finite_number(settled_value):
            raise PortfolioRiskError("SCENARIO_SETTLED_VALUE_INVALID")
        value = float(settled_value)
        lower_ok = True
        upper_ok = True
        if self.threshold_lower is not None:
            lower = float(self.threshold_lower)
            lower_ok = value >= lower if self.lower_inclusive else value > lower
        if self.threshold_upper is not None:
            upper = float(self.threshold_upper)
            upper_ok = value <= upper if self.upper_inclusive else value < upper
        return lower_ok and upper_ok

    def scenario_pnl(self, settled_value: float) -> float:
        yes = self.yes_outcome(settled_value)
        wins = yes if self.side is PositionSide.YES else not yes
        payout = float(self.quantity) * (1.0 if wins else 0.0)
        cost = float(self.quantity) * float(self.entry_cost_per_contract)
        return payout - cost

    def canonical(self) -> dict[str, object]:
        return {
            "position_id": self.position_id,
            "ticker": self.ticker,
            "rule_snapshot_id": self.rule_snapshot_id,
            "prediction_id": self.prediction_id,
            "market_snapshot_id": self.market_snapshot_id,
            "prediction_time": _format_utc(_timestamp(self.prediction_time, "PORTFOLIO_POSITION_PREDICTION_TIME")),
            "market_time": _format_utc(_timestamp(self.market_time, "PORTFOLIO_POSITION_MARKET_TIME")),
            "event": self.event.canonical(),
            "side": self.side.value,
            "quantity": float(self.quantity),
            "entry_cost_per_contract": float(self.entry_cost_per_contract),
            "threshold_lower": None if self.threshold_lower is None else float(self.threshold_lower),
            "threshold_upper": None if self.threshold_upper is None else float(self.threshold_upper),
            "lower_inclusive": bool(self.lower_inclusive),
            "upper_inclusive": bool(self.upper_inclusive),
            "model_p_yes": None if self.model_p_yes is None else float(self.model_p_yes),
            "market_price_used_as_weather_input": False,
            "risk_state_used_as_weather_input": False,
            "can_execute": False,
        }


@dataclass(frozen=True)
class WeatherScenario:
    scenario_id: str
    available_at: str
    weight: float
    event_values: Mapping[str, float]
    factor_states: Mapping[str, str] = None
    can_execute: bool = False

    def __post_init__(self) -> None:
        if not self.scenario_id.strip():
            raise PortfolioRiskError("WEATHER_SCENARIO_ID_MISSING")
        _timestamp(self.available_at, "WEATHER_SCENARIO_AVAILABLE_AT")
        _positive(self.weight, "WEATHER_SCENARIO_WEIGHT_INVALID")
        if not self.event_values:
            raise PortfolioRiskError("WEATHER_SCENARIO_EVENT_VALUES_MISSING")
        for key, value in self.event_values.items():
            if not str(key).strip() or not _finite_number(value):
                raise PortfolioRiskError("WEATHER_SCENARIO_EVENT_VALUE_INVALID")
        states = dict(self.factor_states or {})
        for key, value in states.items():
            if not str(key).strip() or not str(value).strip():
                raise PortfolioRiskError("WEATHER_SCENARIO_FACTOR_STATE_INVALID")
        object.__setattr__(self, "factor_states", states)
        if self.can_execute:
            raise PortfolioRiskError("WEATHER_SCENARIO_EXECUTION_PROHIBITED")

    def canonical(self) -> dict[str, object]:
        return {
            "scenario_id": self.scenario_id,
            "available_at": _format_utc(_timestamp(self.available_at, "WEATHER_SCENARIO_AVAILABLE_AT")),
            "weight": float(self.weight),
            "event_values": {key: float(self.event_values[key]) for key in sorted(self.event_values)},
            "factor_states": {key: str(self.factor_states[key]) for key in sorted(self.factor_states)},
            "can_execute": False,
        }


@dataclass(frozen=True)
class ScenarioLossResult:
    scenario_id: str
    normalized_weight: float
    pnl: float
    loss: float
    position_pnl: Mapping[str, float]
    factor_states: Mapping[str, str]


@dataclass(frozen=True)
class ExposureSummary:
    exposure_key: str
    gross_cost_at_risk: float
    gross_contracts: float
    portfolio_cost_share: float
    position_ids: tuple[str, ...]


@dataclass(frozen=True)
class FactorStateRisk:
    factor_id: str
    factor_state: str
    conditional_probability: float
    conditional_expected_pnl: float
    worst_case_pnl: float


@dataclass(frozen=True)
class KellyResearchSizing:
    position_id: str
    side_win_probability: float | None
    full_kelly_fraction: float | None
    fractional_kelly_multiplier: float | None
    research_fraction: float | None
    status: str
    can_execute: bool = False

    def __post_init__(self) -> None:
        if self.can_execute:
            raise PortfolioRiskError("KELLY_RESEARCH_EXECUTION_PROHIBITED")


@dataclass(frozen=True)
class PortfolioRiskSnapshot:
    risk_snapshot_id: str
    as_of_time: str
    dependence_mode: DependenceMode
    positions: tuple[PortfolioPosition, ...]
    scenarios: tuple[WeatherScenario, ...]
    scenario_results: tuple[ScenarioLossResult, ...]
    event_exposures: tuple[ExposureSummary, ...]
    region_exposures: tuple[ExposureSummary, ...]
    factor_exposures: tuple[ExposureSummary, ...]
    factor_state_risk: tuple[FactorStateRisk, ...]
    kelly_research: tuple[KellyResearchSizing, ...]
    gross_cost_at_risk: float
    gross_contracts: float
    expected_pnl: float
    expected_loss: float
    probability_of_loss: float
    worst_case_pnl: float
    max_loss: float
    max_event_concentration: float
    max_region_concentration: float
    fractional_kelly_multiplier: float | None
    warnings: tuple[str, ...]
    engine_version: str = PORTFOLIO_RISK_ENGINE_VERSION
    can_execute: bool = False

    def __post_init__(self) -> None:
        if self.can_execute:
            raise PortfolioRiskError("PORTFOLIO_RISK_EXECUTION_PROHIBITED")

    @property
    def prediction_ids(self) -> tuple[str, ...]:
        return tuple(sorted({item.prediction_id for item in self.positions}))

    @property
    def market_snapshot_ids(self) -> tuple[str, ...]:
        return tuple(sorted({item.market_snapshot_id for item in self.positions}))

    def persistence_row(self) -> dict[str, object]:
        return {
            "risk_snapshot_id": self.risk_snapshot_id,
            "as_of_time": _format_utc(_timestamp(self.as_of_time, "PORTFOLIO_RISK_AS_OF")),
            "dependence_mode": self.dependence_mode.value,
            "engine_version": self.engine_version,
            "position_count": len(self.positions),
            "scenario_count": len(self.scenarios),
            "gross_cost_at_risk": self.gross_cost_at_risk,
            "gross_contracts": self.gross_contracts,
            "expected_pnl": self.expected_pnl,
            "expected_loss": self.expected_loss,
            "probability_of_loss": self.probability_of_loss,
            "worst_case_pnl": self.worst_case_pnl,
            "max_loss": self.max_loss,
            "max_event_concentration": self.max_event_concentration,
            "max_region_concentration": self.max_region_concentration,
            "fractional_kelly_multiplier": self.fractional_kelly_multiplier,
            "prediction_ids": list(self.prediction_ids),
            "market_snapshot_ids": list(self.market_snapshot_ids),
            "positions": [item.canonical() for item in sorted(self.positions, key=lambda item: item.position_id)],
            "scenarios": [item.canonical() for item in sorted(self.scenarios, key=lambda item: item.scenario_id)],
            "metrics": {
                "scenario_results": [
                    {
                        "scenario_id": item.scenario_id,
                        "normalized_weight": item.normalized_weight,
                        "pnl": item.pnl,
                        "loss": item.loss,
                        "position_pnl": dict(sorted(item.position_pnl.items())),
                        "factor_states": dict(sorted(item.factor_states.items())),
                    }
                    for item in self.scenario_results
                ],
                "event_exposures": [_exposure_row(item) for item in self.event_exposures],
                "region_exposures": [_exposure_row(item) for item in self.region_exposures],
                "factor_exposures": [_exposure_row(item) for item in self.factor_exposures],
                "factor_state_risk": [
                    {
                        "factor_id": item.factor_id,
                        "factor_state": item.factor_state,
                        "conditional_probability": item.conditional_probability,
                        "conditional_expected_pnl": item.conditional_expected_pnl,
                        "worst_case_pnl": item.worst_case_pnl,
                    }
                    for item in self.factor_state_risk
                ],
                "kelly_research": [
                    {
                        "position_id": item.position_id,
                        "side_win_probability": item.side_win_probability,
                        "full_kelly_fraction": item.full_kelly_fraction,
                        "fractional_kelly_multiplier": item.fractional_kelly_multiplier,
                        "research_fraction": item.research_fraction,
                        "status": item.status,
                        "can_execute": False,
                    }
                    for item in self.kelly_research
                ],
                "warnings": list(self.warnings),
            },
            "market_price_used_as_weather_input": False,
            "risk_state_used_as_weather_input": False,
            "can_execute": False,
        }


class PortfolioScenarioEngine:
    def evaluate(
        self,
        *,
        as_of_time: str,
        positions: Sequence[PortfolioPosition],
        scenarios: Sequence[WeatherScenario],
        dependence_mode: DependenceMode,
        fractional_kelly_multiplier: float | None = None,
    ) -> PortfolioRiskSnapshot:
        as_of = _timestamp(as_of_time, "PORTFOLIO_RISK_AS_OF")
        try:
            mode = DependenceMode(dependence_mode)
        except (TypeError, ValueError) as exc:
            raise PortfolioRiskError("PORTFOLIO_DEPENDENCE_MODE_INVALID") from exc

        position_tuple = tuple(positions)
        scenario_tuple = tuple(scenarios)
        if not position_tuple:
            raise PortfolioRiskError("PORTFOLIO_POSITIONS_MISSING")
        if not scenario_tuple:
            raise PortfolioRiskError("PORTFOLIO_SCENARIOS_MISSING")
        _unique((item.position_id for item in position_tuple), "PORTFOLIO_POSITION_ID_DUPLICATE")
        _unique((item.scenario_id for item in scenario_tuple), "WEATHER_SCENARIO_ID_DUPLICATE")

        event_by_key: dict[str, WeatherEventDescriptor] = {}
        for item in position_tuple:
            if _timestamp(item.prediction_time, "PORTFOLIO_POSITION_PREDICTION_TIME") > as_of:
                raise PortfolioRiskError(f"PORTFOLIO_POSITION_FUTURE_PREDICTION:{item.position_id}")
            if _timestamp(item.market_time, "PORTFOLIO_POSITION_MARKET_TIME") > as_of:
                raise PortfolioRiskError(f"PORTFOLIO_POSITION_FUTURE_MARKET:{item.position_id}")
            existing = event_by_key.get(item.event.event_key)
            if existing is not None and existing.canonical() != item.event.canonical():
                raise PortfolioRiskError(f"WEATHER_EVENT_IDENTITY_COLLISION:{item.event.event_key}")
            event_by_key[item.event.event_key] = item.event

        event_keys = set(event_by_key)
        if len(event_keys) > 1 and mode is not DependenceMode.REGIONAL_FACTOR_SCENARIOS:
            raise PortfolioRiskError("CROSS_EVENT_DEPENDENCE_UNSUPPORTED")

        factor_ids = {factor for event in event_by_key.values() for factor in event.factor_ids}
        if mode is DependenceMode.REGIONAL_FACTOR_SCENARIOS:
            for event in event_by_key.values():
                if not str(event.region_id or "").strip():
                    raise PortfolioRiskError(f"REGIONAL_EVENT_REGION_ID_MISSING:{event.event_key}")
                if not event.factor_ids:
                    raise PortfolioRiskError(f"REGIONAL_EVENT_FACTOR_IDS_MISSING:{event.event_key}")

        weights: list[float] = []
        for scenario in scenario_tuple:
            if _timestamp(scenario.available_at, "WEATHER_SCENARIO_AVAILABLE_AT") > as_of:
                raise PortfolioRiskError(f"WEATHER_SCENARIO_FUTURE_EVIDENCE:{scenario.scenario_id}")
            scenario_keys = set(scenario.event_values)
            if scenario_keys != event_keys:
                missing = sorted(event_keys - scenario_keys)
                extra = sorted(scenario_keys - event_keys)
                raise PortfolioRiskError(
                    f"WEATHER_SCENARIO_EVENT_COVERAGE_MISMATCH:{scenario.scenario_id}:missing={missing}:extra={extra}"
                )
            if mode is DependenceMode.REGIONAL_FACTOR_SCENARIOS:
                missing_factors = sorted(factor_ids - set(scenario.factor_states))
                if missing_factors:
                    raise PortfolioRiskError(
                        f"REGIONAL_FACTOR_STATE_MISSING:{scenario.scenario_id}:{','.join(missing_factors)}"
                    )
            weights.append(float(scenario.weight))

        total_weight = sum(weights)
        if not math.isfinite(total_weight) or total_weight <= 0.0:
            raise PortfolioRiskError("WEATHER_SCENARIO_WEIGHT_TOTAL_INVALID")

        scenario_results: list[ScenarioLossResult] = []
        for scenario, raw_weight in zip(scenario_tuple, weights):
            position_pnl: dict[str, float] = {}
            for item in position_tuple:
                value = float(scenario.event_values[item.event.event_key])
                position_pnl[item.position_id] = item.scenario_pnl(value)
            pnl = sum(position_pnl.values())
            scenario_results.append(
                ScenarioLossResult(
                    scenario_id=scenario.scenario_id,
                    normalized_weight=raw_weight / total_weight,
                    pnl=pnl,
                    loss=max(-pnl, 0.0),
                    position_pnl=position_pnl,
                    factor_states=dict(scenario.factor_states),
                )
            )

        gross_cost = sum(float(item.quantity) * float(item.entry_cost_per_contract) for item in position_tuple)
        gross_contracts = sum(float(item.quantity) for item in position_tuple)
        if gross_cost <= 0.0:
            raise PortfolioRiskError("PORTFOLIO_GROSS_COST_INVALID")

        expected_pnl = sum(item.normalized_weight * item.pnl for item in scenario_results)
        expected_loss = sum(item.normalized_weight * item.loss for item in scenario_results)
        probability_of_loss = sum(item.normalized_weight for item in scenario_results if item.pnl < 0.0)
        worst_case_pnl = min(item.pnl for item in scenario_results)
        max_loss = max(-worst_case_pnl, 0.0)

        event_exposures = _group_exposure(
            positions=position_tuple,
            key_fn=lambda item: item.event.event_key,
            total_cost=gross_cost,
        )
        region_exposures = _group_exposure(
            positions=position_tuple,
            key_fn=lambda item: item.event.region_id or "UNASSIGNED",
            total_cost=gross_cost,
        )
        factor_exposures = _factor_exposures(position_tuple, gross_cost)

        max_event_concentration = max(item.portfolio_cost_share for item in event_exposures)
        max_region_concentration = max(item.portfolio_cost_share for item in region_exposures)
        factor_state_risk = _factor_state_risk(scenario_results)

        kelly_multiplier = None
        if fractional_kelly_multiplier is not None:
            kelly_multiplier = _kelly_multiplier(fractional_kelly_multiplier)
        kelly_research = tuple(
            _kelly_research(item, kelly_multiplier)
            for item in sorted(position_tuple, key=lambda item: item.position_id)
        )

        warnings: list[str] = []
        if mode is DependenceMode.REGIONAL_FACTOR_SCENARIOS:
            warnings.append("REGIONAL_DEPENDENCE_SCENARIO_BASED_NOT_COPULA_ASSUMED")
        if kelly_multiplier is not None:
            warnings.append("FRACTIONAL_KELLY_RESEARCH_ONLY_NOT_PORTFOLIO_OPTIMIZED")

        snapshot_id = portfolio_risk_snapshot_id(
            as_of_time=as_of_time,
            dependence_mode=mode,
            positions=position_tuple,
            scenarios=scenario_tuple,
            fractional_kelly_multiplier=kelly_multiplier,
        )
        return PortfolioRiskSnapshot(
            risk_snapshot_id=snapshot_id,
            as_of_time=_format_utc(as_of),
            dependence_mode=mode,
            positions=position_tuple,
            scenarios=scenario_tuple,
            scenario_results=tuple(sorted(scenario_results, key=lambda item: item.scenario_id)),
            event_exposures=event_exposures,
            region_exposures=region_exposures,
            factor_exposures=factor_exposures,
            factor_state_risk=factor_state_risk,
            kelly_research=kelly_research,
            gross_cost_at_risk=gross_cost,
            gross_contracts=gross_contracts,
            expected_pnl=expected_pnl,
            expected_loss=expected_loss,
            probability_of_loss=probability_of_loss,
            worst_case_pnl=worst_case_pnl,
            max_loss=max_loss,
            max_event_concentration=max_event_concentration,
            max_region_concentration=max_region_concentration,
            fractional_kelly_multiplier=kelly_multiplier,
            warnings=tuple(warnings),
        )


def build_weather_event(
    *,
    lane: str,
    settlement_source: str,
    settlement_location_code: str,
    observation_window: str,
    metric: str,
    units: str,
    region_id: str | None = None,
    factor_ids: Sequence[str] = (),
) -> WeatherEventDescriptor:
    return WeatherEventDescriptor(
        event_key=weather_event_key(
            lane=lane,
            settlement_source=settlement_source,
            settlement_location_code=settlement_location_code,
            observation_window=observation_window,
            metric=metric,
            units=units,
        ),
        lane=lane,
        settlement_source=settlement_source,
        settlement_location_code=settlement_location_code,
        observation_window=observation_window,
        metric=metric,
        units=units,
        region_id=region_id,
        factor_ids=tuple(factor_ids),
    )


def weather_event_key(
    *,
    lane: str,
    settlement_source: str,
    settlement_location_code: str,
    observation_window: str,
    metric: str,
    units: str,
) -> str:
    payload = {
        "lane": str(lane).strip().upper(),
        "settlement_source": str(settlement_source).strip(),
        "settlement_location_code": str(settlement_location_code).strip().upper(),
        "observation_window": str(observation_window).strip(),
        "metric": str(metric).strip().lower(),
        "units": str(units).strip().upper(),
    }
    if any(not value for value in payload.values()):
        raise PortfolioRiskError("WEATHER_EVENT_IDENTITY_INPUT_MISSING")
    digest = hashlib.sha256(
        json.dumps(payload, sort_keys=True, separators=(",", ":")).encode("utf-8")
    ).hexdigest()
    return f"kalshi-weather-event-{digest[:24]}"


def portfolio_risk_snapshot_id(
    *,
    as_of_time: str,
    dependence_mode: DependenceMode,
    positions: Sequence[PortfolioPosition],
    scenarios: Sequence[WeatherScenario],
    fractional_kelly_multiplier: float | None,
) -> str:
    payload = {
        "engine_version": PORTFOLIO_RISK_ENGINE_VERSION,
        "as_of_time": _format_utc(_timestamp(as_of_time, "PORTFOLIO_RISK_AS_OF")),
        "dependence_mode": DependenceMode(dependence_mode).value,
        "positions": [item.canonical() for item in sorted(tuple(positions), key=lambda item: item.position_id)],
        "scenarios": [item.canonical() for item in sorted(tuple(scenarios), key=lambda item: item.scenario_id)],
        "fractional_kelly_multiplier": fractional_kelly_multiplier,
    }
    digest = hashlib.sha256(
        json.dumps(payload, sort_keys=True, separators=(",", ":"), default=str).encode("utf-8")
    ).hexdigest()
    return f"kalshi-weather-portfolio-risk-{digest[:24]}"


def _group_exposure(
    *,
    positions: Sequence[PortfolioPosition],
    key_fn,
    total_cost: float,
) -> tuple[ExposureSummary, ...]:
    grouped: dict[str, list[PortfolioPosition]] = {}
    for item in positions:
        grouped.setdefault(str(key_fn(item)), []).append(item)
    out: list[ExposureSummary] = []
    for key in sorted(grouped):
        rows = grouped[key]
        cost = sum(float(item.quantity) * float(item.entry_cost_per_contract) for item in rows)
        contracts = sum(float(item.quantity) for item in rows)
        out.append(
            ExposureSummary(
                exposure_key=key,
                gross_cost_at_risk=cost,
                gross_contracts=contracts,
                portfolio_cost_share=cost / total_cost,
                position_ids=tuple(sorted(item.position_id for item in rows)),
            )
        )
    return tuple(out)


def _factor_exposures(
    positions: Sequence[PortfolioPosition],
    total_cost: float,
) -> tuple[ExposureSummary, ...]:
    grouped: dict[str, list[PortfolioPosition]] = {}
    for item in positions:
        for factor_id in item.event.factor_ids:
            grouped.setdefault(factor_id, []).append(item)
    out: list[ExposureSummary] = []
    for factor_id in sorted(grouped):
        rows = grouped[factor_id]
        cost = sum(float(item.quantity) * float(item.entry_cost_per_contract) for item in rows)
        contracts = sum(float(item.quantity) for item in rows)
        out.append(
            ExposureSummary(
                exposure_key=factor_id,
                gross_cost_at_risk=cost,
                gross_contracts=contracts,
                portfolio_cost_share=cost / total_cost,
                position_ids=tuple(sorted(item.position_id for item in rows)),
            )
        )
    return tuple(out)


def _factor_state_risk(
    scenario_results: Sequence[ScenarioLossResult],
) -> tuple[FactorStateRisk, ...]:
    grouped: dict[tuple[str, str], list[ScenarioLossResult]] = {}
    for scenario in scenario_results:
        for factor_id, factor_state in scenario.factor_states.items():
            grouped.setdefault((str(factor_id), str(factor_state)), []).append(scenario)

    out: list[FactorStateRisk] = []
    for (factor_id, factor_state), rows in sorted(grouped.items()):
        probability = sum(item.normalized_weight for item in rows)
        if probability <= 0.0:
            continue
        expected = sum(item.normalized_weight * item.pnl for item in rows) / probability
        out.append(
            FactorStateRisk(
                factor_id=factor_id,
                factor_state=factor_state,
                conditional_probability=probability,
                conditional_expected_pnl=expected,
                worst_case_pnl=min(item.pnl for item in rows),
            )
        )
    return tuple(out)


def _kelly_research(
    position: PortfolioPosition,
    multiplier: float | None,
) -> KellyResearchSizing:
    if multiplier is None:
        return KellyResearchSizing(
            position_id=position.position_id,
            side_win_probability=None,
            full_kelly_fraction=None,
            fractional_kelly_multiplier=None,
            research_fraction=None,
            status="NOT_REQUESTED",
        )
    if position.model_p_yes is None:
        return KellyResearchSizing(
            position_id=position.position_id,
            side_win_probability=None,
            full_kelly_fraction=None,
            fractional_kelly_multiplier=multiplier,
            research_fraction=None,
            status="MODEL_PROBABILITY_UNAVAILABLE",
        )
    p_win = float(position.model_p_yes)
    if position.side is PositionSide.NO:
        p_win = 1.0 - p_win
    cost = float(position.entry_cost_per_contract)
    full = max(0.0, (p_win - cost) / (1.0 - cost))
    return KellyResearchSizing(
        position_id=position.position_id,
        side_win_probability=p_win,
        full_kelly_fraction=full,
        fractional_kelly_multiplier=multiplier,
        research_fraction=full * multiplier,
        status="RESEARCH_ONLY",
    )


def _kelly_multiplier(value: float) -> float:
    if not isinstance(value, (int, float)) or isinstance(value, bool):
        raise PortfolioRiskError("FRACTIONAL_KELLY_MULTIPLIER_INVALID")
    number = float(value)
    if not math.isfinite(number) or not (0.0 < number <= 1.0):
        raise PortfolioRiskError("FRACTIONAL_KELLY_MULTIPLIER_INVALID")
    return number


def _exposure_row(item: ExposureSummary) -> dict[str, object]:
    return {
        "exposure_key": item.exposure_key,
        "gross_cost_at_risk": item.gross_cost_at_risk,
        "gross_contracts": item.gross_contracts,
        "portfolio_cost_share": item.portfolio_cost_share,
        "position_ids": list(item.position_ids),
    }


def _unique(values, code: str) -> None:
    items = list(values)
    if len(set(items)) != len(items):
        raise PortfolioRiskError(code)


def _strict_probability(value: float, code: str) -> float:
    if not isinstance(value, (int, float)) or isinstance(value, bool):
        raise PortfolioRiskError(f"{code}_INVALID")
    number = float(value)
    if not math.isfinite(number) or not (0.0 < number < 1.0):
        raise PortfolioRiskError(f"{code}_INVALID")
    return number


def _positive(value: float, code: str) -> float:
    if not isinstance(value, (int, float)) or isinstance(value, bool):
        raise PortfolioRiskError(code)
    number = float(value)
    if not math.isfinite(number) or number <= 0.0:
        raise PortfolioRiskError(code)
    return number


def _finite_number(value: object) -> bool:
    return isinstance(value, (int, float)) and not isinstance(value, bool) and math.isfinite(float(value))


def _timestamp(value: str, code: str) -> datetime:
    text = str(value or "").strip()
    if text.endswith("Z"):
        text = text[:-1] + "+00:00"
    try:
        parsed = datetime.fromisoformat(text)
    except ValueError as exc:
        raise PortfolioRiskError(f"{code}_INVALID") from exc
    if parsed.tzinfo is None:
        raise PortfolioRiskError(f"{code}_TIMEZONE_REQUIRED")
    return parsed.astimezone(timezone.utc)


def _format_utc(value: datetime) -> str:
    return value.astimezone(timezone.utc).isoformat().replace("+00:00", "Z")
