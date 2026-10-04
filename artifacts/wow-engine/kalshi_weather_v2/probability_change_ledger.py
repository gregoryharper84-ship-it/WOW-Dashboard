from __future__ import annotations

from dataclasses import dataclass
from datetime import datetime, timezone
from enum import Enum
import hashlib
import json
import math
from typing import Sequence


class ProbabilityChangeLedgerError(ValueError):
    pass


class AttributionDomain(str, Enum):
    STATION_TRAJECTORY = "STATION_TRAJECTORY"
    WEATHER_EVIDENCE = "WEATHER_EVIDENCE"
    FORECAST_MODEL_UPDATE = "FORECAST_MODEL_UPDATE"
    CALIBRATION = "CALIBRATION"
    SETTLEMENT_SEMANTICS = "SETTLEMENT_SEMANTICS"
    OTHER_AUTHORITATIVE_WEATHER = "OTHER_AUTHORITATIVE_WEATHER"
    MARKET_STATE = "MARKET_STATE"
    EXECUTION_FRICTION = "EXECUTION_FRICTION"


_PROHIBITED_DOMAINS = {
    AttributionDomain.MARKET_STATE,
    AttributionDomain.EXECUTION_FRICTION,
}


@dataclass(frozen=True)
class ProbabilityAttributionComponent:
    component_id: str
    domain: AttributionDomain
    label: str
    delta_probability: float
    evidence_ids: tuple[str, ...]
    available_at: str
    method: str
    market_price_used_as_weather_input: bool = False
    can_execute: bool = False

    def __post_init__(self) -> None:
        if not self.component_id.strip():
            raise ProbabilityChangeLedgerError("ATTRIBUTION_COMPONENT_ID_MISSING")
        if not self.label.strip():
            raise ProbabilityChangeLedgerError("ATTRIBUTION_LABEL_MISSING")
        if not self.method.strip():
            raise ProbabilityChangeLedgerError("ATTRIBUTION_METHOD_MISSING")
        if self.domain in _PROHIBITED_DOMAINS:
            raise ProbabilityChangeLedgerError(f"ATTRIBUTION_DOMAIN_PROHIBITED:{self.domain.value}")
        if not self.evidence_ids or any(not str(item).strip() for item in self.evidence_ids):
            raise ProbabilityChangeLedgerError("ATTRIBUTION_EVIDENCE_MISSING")
        if len(set(self.evidence_ids)) != len(self.evidence_ids):
            raise ProbabilityChangeLedgerError("ATTRIBUTION_EVIDENCE_DUPLICATE")
        if not isinstance(self.delta_probability, (int, float)) or isinstance(self.delta_probability, bool):
            raise ProbabilityChangeLedgerError("ATTRIBUTION_DELTA_INVALID")
        if not math.isfinite(float(self.delta_probability)):
            raise ProbabilityChangeLedgerError("ATTRIBUTION_DELTA_INVALID")
        _parse_utc(self.available_at, "ATTRIBUTION_AVAILABLE_AT")
        if self.market_price_used_as_weather_input:
            raise ProbabilityChangeLedgerError("MARKET_PRICE_WEATHER_INPUT_PROHIBITED")
        if self.can_execute:
            raise ProbabilityChangeLedgerError("ATTRIBUTION_EXECUTION_PROHIBITED")

    def canonical(self) -> dict[str, object]:
        return {
            "component_id": self.component_id,
            "domain": self.domain.value,
            "label": self.label,
            "delta_probability": float(self.delta_probability),
            "evidence_ids": sorted(self.evidence_ids),
            "available_at": _format_utc(_parse_utc(self.available_at, "ATTRIBUTION_AVAILABLE_AT")),
            "method": self.method,
            "market_price_used_as_weather_input": False,
            "can_execute": False,
        }


@dataclass(frozen=True)
class ProbabilityChangeRecord:
    probability_change_id: str
    ticker: str
    previous_prediction_id: str
    current_prediction_id: str
    before_decision_time: str
    after_decision_time: str
    p_yes_before: float
    p_yes_after: float
    components: tuple[ProbabilityAttributionComponent, ...]
    market_context_snapshot_ids: tuple[str, ...] = ()
    reconciliation_tolerance: float = 1e-9
    can_execute: bool = False

    def __post_init__(self) -> None:
        for name in ("probability_change_id", "ticker", "previous_prediction_id", "current_prediction_id"):
            if not str(getattr(self, name) or "").strip():
                raise ProbabilityChangeLedgerError(f"{name.upper()}_MISSING")
        if self.previous_prediction_id == self.current_prediction_id:
            raise ProbabilityChangeLedgerError("PROBABILITY_CHANGE_PREDICTION_IDENTITY_CONFLICT")
        before_time = _parse_utc(self.before_decision_time, "PROBABILITY_CHANGE_BEFORE_TIME")
        after_time = _parse_utc(self.after_decision_time, "PROBABILITY_CHANGE_AFTER_TIME")
        if after_time <= before_time:
            raise ProbabilityChangeLedgerError("PROBABILITY_CHANGE_TIME_ORDER_INVALID")
        before = _strict_probability(self.p_yes_before, "PROBABILITY_CHANGE_P_BEFORE")
        after = _strict_probability(self.p_yes_after, "PROBABILITY_CHANGE_P_AFTER")
        tolerance = _nonnegative(self.reconciliation_tolerance, "PROBABILITY_CHANGE_TOLERANCE_INVALID")
        if not self.components:
            raise ProbabilityChangeLedgerError("PROBABILITY_CHANGE_ATTRIBUTION_MISSING")
        ids = [item.component_id for item in self.components]
        if len(set(ids)) != len(ids):
            raise ProbabilityChangeLedgerError("PROBABILITY_CHANGE_COMPONENT_ID_DUPLICATE")
        for item in self.components:
            if _parse_utc(item.available_at, "ATTRIBUTION_AVAILABLE_AT") > after_time:
                raise ProbabilityChangeLedgerError(f"ATTRIBUTION_FUTURE_EVIDENCE:{item.component_id}")
        if any(not str(item).strip() for item in self.market_context_snapshot_ids):
            raise ProbabilityChangeLedgerError("MARKET_CONTEXT_ID_INVALID")
        if len(set(self.market_context_snapshot_ids)) != len(self.market_context_snapshot_ids):
            raise ProbabilityChangeLedgerError("MARKET_CONTEXT_ID_DUPLICATE")
        total_delta = after - before
        attribution_total = sum(float(item.delta_probability) for item in self.components)
        if abs(attribution_total - total_delta) > tolerance:
            raise ProbabilityChangeLedgerError(
                f"PROBABILITY_CHANGE_ATTRIBUTION_MISMATCH:{attribution_total:.12f}:{total_delta:.12f}"
            )
        if self.can_execute:
            raise ProbabilityChangeLedgerError("PROBABILITY_CHANGE_EXECUTION_PROHIBITED")
        expected = probability_change_id(
            ticker=self.ticker,
            previous_prediction_id=self.previous_prediction_id,
            current_prediction_id=self.current_prediction_id,
            before_decision_time=self.before_decision_time,
            after_decision_time=self.after_decision_time,
            p_yes_before=before,
            p_yes_after=after,
            components=self.components,
            market_context_snapshot_ids=self.market_context_snapshot_ids,
        )
        if self.probability_change_id != expected:
            raise ProbabilityChangeLedgerError("PROBABILITY_CHANGE_IDENTITY_MISMATCH")

    @property
    def total_delta(self) -> float:
        return float(self.p_yes_after) - float(self.p_yes_before)

    @property
    def attribution_total(self) -> float:
        return sum(float(item.delta_probability) for item in self.components)

    @property
    def attribution_evidence_ids(self) -> tuple[str, ...]:
        return tuple(sorted({evidence_id for item in self.components for evidence_id in item.evidence_ids}))

    def persistence_row(self) -> dict[str, object]:
        return {
            "probability_change_id": self.probability_change_id,
            "ticker": self.ticker,
            "previous_prediction_id": self.previous_prediction_id,
            "current_prediction_id": self.current_prediction_id,
            "before_decision_time": _format_utc(_parse_utc(self.before_decision_time, "PROBABILITY_CHANGE_BEFORE_TIME")),
            "after_decision_time": _format_utc(_parse_utc(self.after_decision_time, "PROBABILITY_CHANGE_AFTER_TIME")),
            "p_yes_before": float(self.p_yes_before),
            "p_yes_after": float(self.p_yes_after),
            "total_delta": self.total_delta,
            "attribution_total": self.attribution_total,
            "reconciliation_tolerance": float(self.reconciliation_tolerance),
            "attribution_components": [item.canonical() for item in sorted(self.components, key=lambda item: item.component_id)],
            "attribution_evidence_ids": list(self.attribution_evidence_ids),
            "market_context_snapshot_ids": sorted(self.market_context_snapshot_ids),
            "can_execute": False,
        }


def build_probability_change_record(
    *,
    ticker: str,
    previous_prediction_id: str,
    current_prediction_id: str,
    before_decision_time: str,
    after_decision_time: str,
    p_yes_before: float,
    p_yes_after: float,
    components: Sequence[ProbabilityAttributionComponent],
    market_context_snapshot_ids: Sequence[str] = (),
    reconciliation_tolerance: float = 1e-9,
) -> ProbabilityChangeRecord:
    component_tuple = tuple(components)
    market_ids = tuple(market_context_snapshot_ids)
    identity = probability_change_id(
        ticker=ticker,
        previous_prediction_id=previous_prediction_id,
        current_prediction_id=current_prediction_id,
        before_decision_time=before_decision_time,
        after_decision_time=after_decision_time,
        p_yes_before=p_yes_before,
        p_yes_after=p_yes_after,
        components=component_tuple,
        market_context_snapshot_ids=market_ids,
    )
    return ProbabilityChangeRecord(
        probability_change_id=identity,
        ticker=ticker,
        previous_prediction_id=previous_prediction_id,
        current_prediction_id=current_prediction_id,
        before_decision_time=before_decision_time,
        after_decision_time=after_decision_time,
        p_yes_before=p_yes_before,
        p_yes_after=p_yes_after,
        components=component_tuple,
        market_context_snapshot_ids=market_ids,
        reconciliation_tolerance=reconciliation_tolerance,
    )


def probability_change_id(
    *,
    ticker: str,
    previous_prediction_id: str,
    current_prediction_id: str,
    before_decision_time: str,
    after_decision_time: str,
    p_yes_before: float,
    p_yes_after: float,
    components: Sequence[ProbabilityAttributionComponent],
    market_context_snapshot_ids: Sequence[str] = (),
) -> str:
    payload = {
        "ticker": ticker,
        "previous_prediction_id": previous_prediction_id,
        "current_prediction_id": current_prediction_id,
        "before_decision_time": _format_utc(_parse_utc(before_decision_time, "PROBABILITY_CHANGE_BEFORE_TIME")),
        "after_decision_time": _format_utc(_parse_utc(after_decision_time, "PROBABILITY_CHANGE_AFTER_TIME")),
        "p_yes_before": _strict_probability(p_yes_before, "PROBABILITY_CHANGE_P_BEFORE"),
        "p_yes_after": _strict_probability(p_yes_after, "PROBABILITY_CHANGE_P_AFTER"),
        "components": [item.canonical() for item in sorted(tuple(components), key=lambda item: item.component_id)],
        "market_context_snapshot_ids": sorted(tuple(market_context_snapshot_ids)),
    }
    digest = hashlib.sha256(
        json.dumps(payload, sort_keys=True, separators=(",", ":"), default=str).encode("utf-8")
    ).hexdigest()
    return f"kalshi-weather-probability-change-{digest[:24]}"


def _strict_probability(value: float, code: str) -> float:
    if not isinstance(value, (int, float)) or isinstance(value, bool):
        raise ProbabilityChangeLedgerError(f"{code}_INVALID")
    number = float(value)
    if not math.isfinite(number) or not (0.0 < number < 1.0):
        raise ProbabilityChangeLedgerError(f"{code}_INVALID")
    return number


def _nonnegative(value: float, code: str) -> float:
    if not isinstance(value, (int, float)) or isinstance(value, bool):
        raise ProbabilityChangeLedgerError(code)
    number = float(value)
    if not math.isfinite(number) or number < 0.0:
        raise ProbabilityChangeLedgerError(code)
    return number


def _parse_utc(value: str, code: str) -> datetime:
    text = str(value or "").strip()
    if text.endswith("Z"):
        text = text[:-1] + "+00:00"
    try:
        parsed = datetime.fromisoformat(text)
    except ValueError as exc:
        raise ProbabilityChangeLedgerError(f"{code}_INVALID") from exc
    if parsed.tzinfo is None:
        raise ProbabilityChangeLedgerError(f"{code}_TIMEZONE_REQUIRED")
    return parsed.astimezone(timezone.utc)


def _format_utc(value: datetime) -> str:
    return value.astimezone(timezone.utc).isoformat().replace("+00:00", "Z")
