from __future__ import annotations

from dataclasses import dataclass
import math
from typing import Mapping, Sequence

from .decision_policy import DecisionAction, DecisionPolicy
from .information_events import InformationEvent, InformationEventType, event_sort_key


class VoIEngineError(ValueError):
    pass


@dataclass(frozen=True)
class PosteriorShiftScenario:
    delta_probability: float
    weight: float

    def __post_init__(self) -> None:
        if not math.isfinite(float(self.delta_probability)):
            raise VoIEngineError("POSTERIOR_SHIFT_DELTA_INVALID")
        if not math.isfinite(float(self.weight)) or float(self.weight) <= 0.0:
            raise VoIEngineError("POSTERIOR_SHIFT_WEIGHT_INVALID")


@dataclass(frozen=True)
class PosteriorShiftDistribution:
    event_type: InformationEventType
    lane: str
    evidence_id: str
    method: str
    scenarios: tuple[PosteriorShiftScenario, ...]
    lead_time_bucket: str | None = None
    regime: str | None = None
    threshold_distance: str | None = None
    current_state: str | None = None
    market_price_used_as_weather_input: bool = False
    can_execute: bool = False

    def __post_init__(self) -> None:
        if not self.lane.strip():
            raise VoIEngineError("POSTERIOR_SHIFT_LANE_MISSING")
        if not self.evidence_id.strip():
            raise VoIEngineError("POSTERIOR_SHIFT_EVIDENCE_MISSING")
        if not self.method.strip():
            raise VoIEngineError("POSTERIOR_SHIFT_METHOD_MISSING")
        if not self.scenarios:
            raise VoIEngineError("POSTERIOR_SHIFT_SCENARIOS_MISSING")
        if self.market_price_used_as_weather_input:
            raise VoIEngineError("MARKET_PRICE_WEATHER_INPUT_PROHIBITED")
        if self.can_execute:
            raise VoIEngineError("POSTERIOR_SHIFT_EXECUTION_PROHIBITED")

    def normalized(self) -> tuple[tuple[float, float], ...]:
        total = sum(float(item.weight) for item in self.scenarios)
        if not math.isfinite(total) or total <= 0.0:
            raise VoIEngineError("POSTERIOR_SHIFT_WEIGHT_TOTAL_INVALID")
        return tuple((float(item.delta_probability), float(item.weight) / total) for item in self.scenarios)


@dataclass(frozen=True)
class DecisionGateState:
    settlement_ready: bool
    calibration_ready: bool
    execution_quality_ready: bool

    @property
    def ready(self) -> bool:
        return self.settlement_ready and self.calibration_ready and self.execution_quality_ready

    @property
    def blockers(self) -> tuple[str, ...]:
        out: list[str] = []
        if not self.settlement_ready:
            out.append("SETTLEMENT_GATE_INCOMPLETE")
        if not self.calibration_ready:
            out.append("CALIBRATION_GATE_INCOMPLETE")
        if not self.execution_quality_ready:
            out.append("EXECUTION_QUALITY_GATE_INCOMPLETE")
        return tuple(out)


@dataclass(frozen=True)
class EdgeSurvivalMetrics:
    event_id: str
    expected_probability: float
    expected_raw_edge: float
    expected_effective_edge: float
    probability_raw_edge_positive: float
    probability_effective_edge_positive: float
    probability_sign_reversal: float


@dataclass(frozen=True)
class VoIDecision:
    action: DecisionAction
    current_probability: float
    current_raw_edge: float
    current_effective_edge: float
    trade_now_value: float
    wait_value: float
    voi_gain: float
    best_wait_event_id: str | None
    edge_survival: tuple[EdgeSurvivalMetrics, ...]
    blockers: tuple[str, ...] = ()
    policy_id: str | None = None
    policy_version: str | None = None
    can_execute: bool = False

    def __post_init__(self) -> None:
        if self.can_execute:
            raise VoIEngineError("VOI_EXECUTION_PROHIBITED")


class VoIEngineV2:
    """Decision-timing layer downstream of independent weather probability.

    Posterior weather revisions are supplied as governed event-conditioned
    empirical distributions. Market state affects edge/value only; it never
    feeds back into the current or posterior weather probability.
    """

    def evaluate(
        self,
        *,
        decision_time: str,
        current_probability: float,
        raw_market_probability: float,
        effective_break_even_probability: float,
        events: Sequence[InformationEvent],
        distributions: Mapping[str, PosteriorShiftDistribution],
        gates: DecisionGateState,
        wait_costs: Mapping[str, float] | None = None,
        policy: DecisionPolicy | None = None,
        max_paths: int = 4096,
    ) -> VoIDecision:
        p0 = _probability(current_probability, "CURRENT_PROBABILITY")
        raw_market = _probability(raw_market_probability, "RAW_MARKET_PROBABILITY")
        effective_break_even = _probability(effective_break_even_probability, "EFFECTIVE_BREAK_EVEN_PROBABILITY")
        if max_paths < 1:
            raise VoIEngineError("VOI_MAX_PATHS_INVALID")

        current_raw_edge = p0 - raw_market
        current_effective_edge = p0 - effective_break_even
        immediate = max(current_effective_edge, 0.0)

        # Preserve upstream typed blockers before touching optional future-event
        # inputs. A row already blocked by settlement/calibration/execution
        # quality must remain ABSTAIN rather than raising on downstream VoI data.
        if not gates.ready:
            return VoIDecision(
                action=DecisionAction.ABSTAIN,
                current_probability=p0,
                current_raw_edge=current_raw_edge,
                current_effective_edge=current_effective_edge,
                trade_now_value=immediate,
                wait_value=0.0,
                voi_gain=-immediate,
                best_wait_event_id=None,
                edge_survival=(),
                blockers=gates.blockers,
                policy_id=policy.policy_id if policy else None,
                policy_version=policy.version if policy else None,
            )

        ordered = tuple(sorted(events, key=event_sort_key))
        for event in ordered:
            if not event.is_future_as_of(decision_time):
                raise VoIEngineError(f"INFORMATION_EVENT_NOT_FUTURE_AS_OF_DECISION:{event.event_id}")

        costs = {str(k): _nonnegative(v, "WAIT_COST_INVALID") for k, v in dict(wait_costs or {}).items()}
        prepared = tuple((event, self._distribution_for(event, distributions)) for event in ordered)
        self._enforce_path_bound(prepared, max_paths)

        edge_survival = self._edge_survival_series(
            p0=p0,
            raw_market=raw_market,
            effective_break_even=effective_break_even,
            prepared=prepared,
        )

        root = self._optimal_value(
            stage=0,
            probability=p0,
            effective_break_even=effective_break_even,
            prepared=prepared,
            wait_costs=costs,
        )
        wait_value = root[2]
        action = root[1]
        best_wait_event_id = ordered[0].event_id if action is DecisionAction.WAIT and ordered else None
        voi_gain = wait_value - immediate

        action = self._apply_policy(
            action=action,
            current_effective_edge=current_effective_edge,
            voi_gain=voi_gain,
            edge_survival=edge_survival,
            policy=policy,
        )

        return VoIDecision(
            action=action,
            current_probability=p0,
            current_raw_edge=current_raw_edge,
            current_effective_edge=current_effective_edge,
            trade_now_value=immediate,
            wait_value=wait_value,
            voi_gain=voi_gain,
            best_wait_event_id=best_wait_event_id if action is DecisionAction.WAIT else None,
            edge_survival=edge_survival,
            policy_id=policy.policy_id if policy else None,
            policy_version=policy.version if policy else None,
        )

    @staticmethod
    def _distribution_for(
        event: InformationEvent,
        distributions: Mapping[str, PosteriorShiftDistribution],
    ) -> PosteriorShiftDistribution:
        distribution = distributions.get(event.event_id)
        if distribution is None:
            raise VoIEngineError(f"POSTERIOR_SHIFT_DISTRIBUTION_MISSING:{event.event_id}")
        if distribution.event_type != event.event_type:
            raise VoIEngineError(f"POSTERIOR_SHIFT_EVENT_TYPE_MISMATCH:{event.event_id}")
        if distribution.lane != event.lane:
            raise VoIEngineError(f"POSTERIOR_SHIFT_LANE_MISMATCH:{event.event_id}")
        for field in ("lead_time_bucket", "regime", "threshold_distance"):
            expected = getattr(event, field)
            actual = getattr(distribution, field)
            if expected is not None and actual is not None and expected != actual:
                raise VoIEngineError(f"POSTERIOR_SHIFT_CONDITION_MISMATCH:{event.event_id}:{field}")
        return distribution

    @staticmethod
    def _enforce_path_bound(
        prepared: Sequence[tuple[InformationEvent, PosteriorShiftDistribution]],
        max_paths: int,
    ) -> None:
        paths = 1
        for event, distribution in prepared:
            paths *= len(distribution.scenarios)
            if paths > max_paths:
                raise VoIEngineError(f"VOI_SCENARIO_TREE_TOO_LARGE:{event.event_id}:{paths}")

    def _optimal_value(
        self,
        *,
        stage: int,
        probability: float,
        effective_break_even: float,
        prepared: Sequence[tuple[InformationEvent, PosteriorShiftDistribution]],
        wait_costs: Mapping[str, float],
    ) -> tuple[float, DecisionAction, float]:
        immediate = max(probability - effective_break_even, 0.0)
        if stage >= len(prepared):
            if immediate > 0.0:
                return immediate, DecisionAction.TRADE_NOW, 0.0
            return 0.0, DecisionAction.ABSTAIN, 0.0

        event, distribution = prepared[stage]
        expected_future = 0.0
        for delta, weight in distribution.normalized():
            posterior = probability + delta
            if not (0.0 <= posterior <= 1.0):
                raise VoIEngineError(f"POSTERIOR_SHIFT_OUT_OF_BOUNDS:{event.event_id}:{posterior:.12f}")
            child_value, _, _ = self._optimal_value(
                stage=stage + 1,
                probability=posterior,
                effective_break_even=effective_break_even,
                prepared=prepared,
                wait_costs=wait_costs,
            )
            expected_future += weight * child_value
        expected_future -= wait_costs.get(event.event_id, 0.0)

        if expected_future > immediate and expected_future > 0.0:
            return expected_future, DecisionAction.WAIT, expected_future
        if immediate > 0.0:
            return immediate, DecisionAction.TRADE_NOW, expected_future
        return 0.0, DecisionAction.ABSTAIN, expected_future

    def _edge_survival_series(
        self,
        *,
        p0: float,
        raw_market: float,
        effective_break_even: float,
        prepared: Sequence[tuple[InformationEvent, PosteriorShiftDistribution]],
    ) -> tuple[EdgeSurvivalMetrics, ...]:
        paths: list[tuple[float, float]] = [(p0, 1.0)]
        current_sign = _sign(p0 - effective_break_even)
        out: list[EdgeSurvivalMetrics] = []

        for event, distribution in prepared:
            expanded: list[tuple[float, float]] = []
            for probability, path_weight in paths:
                for delta, scenario_weight in distribution.normalized():
                    posterior = probability + delta
                    if not (0.0 <= posterior <= 1.0):
                        raise VoIEngineError(f"POSTERIOR_SHIFT_OUT_OF_BOUNDS:{event.event_id}:{posterior:.12f}")
                    expanded.append((posterior, path_weight * scenario_weight))
            total = sum(weight for _, weight in expanded)
            if total <= 0.0:
                raise VoIEngineError("VOI_PATH_WEIGHT_TOTAL_INVALID")
            paths = [(probability, weight / total) for probability, weight in expanded]

            expected_probability = sum(probability * weight for probability, weight in paths)
            expected_raw_edge = sum((probability - raw_market) * weight for probability, weight in paths)
            expected_effective_edge = sum((probability - effective_break_even) * weight for probability, weight in paths)
            raw_positive = sum(weight for probability, weight in paths if probability - raw_market > 0.0)
            effective_positive = sum(weight for probability, weight in paths if probability - effective_break_even > 0.0)
            sign_reversal = sum(
                weight
                for probability, weight in paths
                if current_sign != 0 and _sign(probability - effective_break_even) not in (0, current_sign)
            )
            out.append(
                EdgeSurvivalMetrics(
                    event_id=event.event_id,
                    expected_probability=expected_probability,
                    expected_raw_edge=expected_raw_edge,
                    expected_effective_edge=expected_effective_edge,
                    probability_raw_edge_positive=raw_positive,
                    probability_effective_edge_positive=effective_positive,
                    probability_sign_reversal=sign_reversal,
                )
            )
        return tuple(out)

    @classmethod
    def _apply_policy(
        cls,
        *,
        action: DecisionAction,
        current_effective_edge: float,
        voi_gain: float,
        edge_survival: Sequence[EdgeSurvivalMetrics],
        policy: DecisionPolicy | None,
    ) -> DecisionAction:
        if policy is None:
            return action

        survival_ok = cls._survival_policy_passes(edge_survival=edge_survival, policy=policy)

        if action is DecisionAction.WAIT:
            if policy.minimum_voi_gain is not None and voi_gain < float(policy.minimum_voi_gain):
                return (
                    DecisionAction.TRADE_NOW
                    if current_effective_edge > 0.0
                    and cls._trade_now_policy_passes(
                        current_effective_edge=current_effective_edge,
                        survival_ok=survival_ok,
                        policy=policy,
                    )
                    else DecisionAction.ABSTAIN
                )
            if not survival_ok:
                return DecisionAction.ABSTAIN
            return DecisionAction.WAIT

        if action is DecisionAction.TRADE_NOW:
            return (
                DecisionAction.TRADE_NOW
                if cls._trade_now_policy_passes(
                    current_effective_edge=current_effective_edge,
                    survival_ok=survival_ok,
                    policy=policy,
                )
                else DecisionAction.ABSTAIN
            )

        return DecisionAction.ABSTAIN

    @staticmethod
    def _trade_now_policy_passes(
        *,
        current_effective_edge: float,
        survival_ok: bool,
        policy: DecisionPolicy,
    ) -> bool:
        if policy.minimum_edge is not None and current_effective_edge < float(policy.minimum_edge):
            return False
        return survival_ok

    @staticmethod
    def _survival_policy_passes(
        *,
        edge_survival: Sequence[EdgeSurvivalMetrics],
        policy: DecisionPolicy,
    ) -> bool:
        if policy.minimum_edge_survival_probability is None:
            return True
        if not edge_survival:
            return False
        threshold = float(policy.minimum_edge_survival_probability)
        # Policy is horizon-conservative: every modeled future information
        # horizon must retain at least the configured survival probability.
        return min(item.probability_effective_edge_positive for item in edge_survival) >= threshold


def _probability(value: float, code: str) -> float:
    if not isinstance(value, (int, float)) or isinstance(value, bool):
        raise VoIEngineError(f"{code}_INVALID")
    number = float(value)
    if not math.isfinite(number) or not (0.0 <= number <= 1.0):
        raise VoIEngineError(f"{code}_INVALID")
    return number


def _nonnegative(value: float, code: str) -> float:
    if not isinstance(value, (int, float)) or isinstance(value, bool):
        raise VoIEngineError(code)
    number = float(value)
    if not math.isfinite(number) or number < 0.0:
        raise VoIEngineError(code)
    return number


def _sign(value: float) -> int:
    if value > 0.0:
        return 1
    if value < 0.0:
        return -1
    return 0
