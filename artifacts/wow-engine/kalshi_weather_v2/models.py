from __future__ import annotations

from dataclasses import dataclass, field
from typing import Any, Mapping, Sequence


ALLOWED_STATUSES = {
    "STRONG_EDGE",
    "QUALIFIED_EDGE",
    "WATCH",
    "NO_EDGE",
    "NO_PLAY_DATA_INSUFFICIENT",
    "NO_PLAY_SETTLEMENT_AMBIGUITY",
}


@dataclass(frozen=True)
class ContractSnapshot:
    market_title: str
    contract_title: str
    ticker: str
    lane: str
    yes_condition: str
    no_condition: str
    location: str
    metric: str
    units: str
    observation_window: str
    timezone: str
    settlement_source: str
    settlement_station_id: str | None
    settlement_station_name: str | None
    rounding_convention: str
    trace_measurement_rules: str
    market_close_time: str
    rule_snapshot_id: str


@dataclass(frozen=True)
class WeatherEvidenceSnapshot:
    analysis_time: str
    latest_official_observation_time: str | None
    forecast_issue_time: str | None
    model_cycle_times: Sequence[str] = field(default_factory=tuple)
    providers: Sequence[str] = field(default_factory=tuple)
    source_roles: Mapping[str, str] = field(default_factory=dict)
    source_snapshot_ids: Sequence[str] = field(default_factory=tuple)
    observed_extreme_so_far: float | None = None
    central_estimate: float | None = None
    disagreement_magnitude: float | None = None
    evidence_complete: bool = False
    station_identity_verified: bool = False
    settlement_source_verified: bool = False
    temporal_provenance_verified: bool = False
    notes: Mapping[str, Any] = field(default_factory=dict)


@dataclass(frozen=True)
class ProbabilityPackage:
    p_yes: float | None
    p_no: float | None
    central_estimate: float | None
    lower_bound_yes: float | None
    upper_bound_yes: float | None
    threshold_distance: str | None
    calibration_method: str | None
    probability_source: str
    market_price_used_as_input: bool = False
    coherent: bool = False
    calibrated: bool = False


@dataclass(frozen=True)
class MarketSnapshot:
    yes_price: float | None
    no_price: float | None
    price_time: str | None
    market_open: bool
    orderbook_nonempty: bool
    executable_price_verified: bool
    fee_known: bool = False
    fee_per_share: float | None = None


@dataclass(frozen=True)
class AgentResult:
    agent: str
    ok: bool
    code: str
    blockers: tuple[str, ...] = ()
    warnings: tuple[str, ...] = ()
    payload: Mapping[str, Any] = field(default_factory=dict)
    can_execute: bool = False


@dataclass(frozen=True)
class TerminalDecision:
    status: str
    code: str
    rank_eligible: bool
    probability_publishable: bool
    edge_publishable: bool
    blockers: tuple[str, ...]
    warnings: tuple[str, ...]
    payload: Mapping[str, Any] = field(default_factory=dict)
    can_execute: bool = False

    def __post_init__(self) -> None:
        if self.status not in ALLOWED_STATUSES:
            raise ValueError(f"unsupported terminal status: {self.status}")
        if self.can_execute:
            raise ValueError("Kalshi Weather V2 can_execute must remain false")
