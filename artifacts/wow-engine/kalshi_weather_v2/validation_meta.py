from __future__ import annotations

from dataclasses import dataclass
from datetime import datetime, timezone
from enum import Enum
import hashlib
import json
import math
from statistics import mean
from types import MappingProxyType
from typing import Mapping, Sequence

from .decision_policy import DecisionAction


VALIDATION_META_VERSION = "KALSHI_WEATHER_V1_4_VALIDATION_META_R1"


class ValidationMetaError(ValueError):
    pass


class BaselineKind(str, Enum):
    NBM = "NBM"
    NWS = "NWS"
    CLIMATOLOGY = "CLIMATOLOGY"
    MARKET = "MARKET"


class SettlementRedTeamCategory(str, Enum):
    SETTLEMENT_SOURCE_CONFLICT = "SETTLEMENT_SOURCE_CONFLICT"
    STATION_IDENTITY_MISMATCH = "STATION_IDENTITY_MISMATCH"
    TIMEZONE_AMBIGUITY = "TIMEZONE_AMBIGUITY"
    ROUNDING_BOUNDARY = "ROUNDING_BOUNDARY"
    PRELIMINARY_VS_FINAL = "PRELIMINARY_VS_FINAL"
    HOURLY_VS_DAILY_SEMANTICS = "HOURLY_VS_DAILY_SEMANTICS"


class ComplexityRecommendation(str, Enum):
    HOLD = "HOLD"
    ELIGIBLE_FOR_GOVERNED_REVIEW = "ELIGIBLE_FOR_GOVERNED_REVIEW"


@dataclass(frozen=True)
class BaselinePrediction:
    baseline_id: str
    kind: BaselineKind
    version: str
    p_yes: float
    available_at: str
    evidence_ids: tuple[str, ...]
    market_observational_only: bool = False
    market_price_used_as_weather_input: bool = False
    can_execute: bool = False

    def __post_init__(self) -> None:
        if not self.baseline_id.strip():
            raise ValidationMetaError("BASELINE_ID_MISSING")
        try:
            normalized = BaselineKind(self.kind)
        except (TypeError, ValueError) as exc:
            raise ValidationMetaError("BASELINE_KIND_INVALID") from exc
        object.__setattr__(self, "kind", normalized)
        if not self.version.strip():
            raise ValidationMetaError("BASELINE_VERSION_MISSING")
        _strict_probability(self.p_yes, "BASELINE_P_YES")
        _parse_utc(self.available_at, "BASELINE_AVAILABLE_AT")
        evidence = tuple(str(item).strip() for item in self.evidence_ids)
        if not evidence or any(not item for item in evidence):
            raise ValidationMetaError("BASELINE_EVIDENCE_MISSING")
        if len(set(evidence)) != len(evidence):
            raise ValidationMetaError("BASELINE_EVIDENCE_DUPLICATE")
        object.__setattr__(self, "evidence_ids", evidence)
        if normalized is BaselineKind.MARKET and not self.market_observational_only:
            raise ValidationMetaError("MARKET_BASELINE_MUST_BE_OBSERVATIONAL_ONLY")
        if normalized is not BaselineKind.MARKET and self.market_observational_only:
            raise ValidationMetaError("NONMARKET_BASELINE_OBSERVATIONAL_FLAG_INVALID")
        if self.market_price_used_as_weather_input:
            raise ValidationMetaError("MARKET_PRICE_WEATHER_INPUT_PROHIBITED")
        if self.can_execute:
            raise ValidationMetaError("BASELINE_EXECUTION_PROHIBITED")

    @property
    def identity(self) -> str:
        return f"{self.kind.value}:{self.baseline_id}@{self.version}"

    def canonical(self) -> dict[str, object]:
        return {
            "baseline_id": self.baseline_id,
            "kind": self.kind.value,
            "version": self.version,
            "p_yes": float(self.p_yes),
            "available_at": _iso(_parse_utc(self.available_at, "BASELINE_AVAILABLE_AT")),
            "evidence_ids": sorted(self.evidence_ids),
            "market_observational_only": bool(self.market_observational_only),
            "market_price_used_as_weather_input": False,
            "can_execute": False,
        }


@dataclass(frozen=True)
class ProbabilityValidationSample:
    sample_id: str
    prediction_id: str
    decision_time: str
    settled_at: str
    champion_p_yes: float
    yes_outcome: bool
    confidence_score: float
    champion_evidence_ids: tuple[str, ...]
    baselines: tuple[BaselinePrediction, ...]
    lane: str
    market_price_used_as_weather_input: bool = False
    can_execute: bool = False

    def __post_init__(self) -> None:
        for name in ("sample_id", "prediction_id", "lane"):
            if not str(getattr(self, name) or "").strip():
                raise ValidationMetaError(f"{name.upper()}_MISSING")
        decision = _parse_utc(self.decision_time, "VALIDATION_DECISION_TIME")
        settled = _parse_utc(self.settled_at, "VALIDATION_SETTLED_AT")
        if settled <= decision:
            raise ValidationMetaError("VALIDATION_SETTLEMENT_TIME_ORDER_INVALID")
        _strict_probability(self.champion_p_yes, "CHAMPION_P_YES")
        _bounded(self.confidence_score, "CONFIDENCE_SCORE")
        evidence = tuple(str(item).strip() for item in self.champion_evidence_ids)
        if not evidence or any(not item for item in evidence):
            raise ValidationMetaError("CHAMPION_EVIDENCE_MISSING")
        if len(set(evidence)) != len(evidence):
            raise ValidationMetaError("CHAMPION_EVIDENCE_DUPLICATE")
        object.__setattr__(self, "champion_evidence_ids", evidence)
        baselines = tuple(self.baselines)
        identities = [item.identity for item in baselines]
        if len(set(identities)) != len(identities):
            raise ValidationMetaError("BASELINE_IDENTITY_DUPLICATE")
        for item in baselines:
            if _parse_utc(item.available_at, "BASELINE_AVAILABLE_AT") > decision:
                raise ValidationMetaError(f"BASELINE_FUTURE_EVIDENCE:{item.identity}")
        if self.market_price_used_as_weather_input:
            raise ValidationMetaError("MARKET_PRICE_WEATHER_INPUT_PROHIBITED")
        if self.can_execute:
            raise ValidationMetaError("VALIDATION_SAMPLE_EXECUTION_PROHIBITED")

    def canonical(self) -> dict[str, object]:
        return {
            "sample_id": self.sample_id,
            "prediction_id": self.prediction_id,
            "decision_time": _iso(_parse_utc(self.decision_time, "VALIDATION_DECISION_TIME")),
            "settled_at": _iso(_parse_utc(self.settled_at, "VALIDATION_SETTLED_AT")),
            "champion_p_yes": float(self.champion_p_yes),
            "yes_outcome": bool(self.yes_outcome),
            "confidence_score": float(self.confidence_score),
            "champion_evidence_ids": sorted(self.champion_evidence_ids),
            "baselines": [item.canonical() for item in sorted(self.baselines, key=lambda item: item.identity)],
            "lane": self.lane,
            "market_price_used_as_weather_input": False,
            "can_execute": False,
        }


@dataclass(frozen=True)
class ModelScore:
    model_id: str
    kind: str
    sample_n: int
    brier_score: float
    log_loss: float
    calibration_gap: float


@dataclass(frozen=True)
class BaselineDelta:
    baseline_identity: str
    baseline_kind: str
    paired_n: int
    champion_brier_advantage: float
    champion_log_loss_advantage: float


@dataclass(frozen=True)
class SelectiveScorePoint:
    minimum_confidence: float
    selected_n: int
    coverage: float
    brier_score: float | None
    log_loss: float | None
    calibration_gap: float | None


@dataclass(frozen=True)
class CounterfactualDecisionSample:
    sample_id: str
    policy_id: str
    policy_version: str
    decision_time: str
    realized_at: str
    recommended_action: DecisionAction
    action_utilities: Mapping[str, float]
    evidence_ids: tuple[str, ...]
    market_price_used_as_weather_input: bool = False
    can_execute: bool = False

    def __post_init__(self) -> None:
        if not self.sample_id.strip():
            raise ValidationMetaError("COUNTERFACTUAL_SAMPLE_ID_MISSING")
        if not self.policy_id.strip() or not self.policy_version.strip():
            raise ValidationMetaError("COUNTERFACTUAL_POLICY_IDENTITY_MISSING")
        decision = _parse_utc(self.decision_time, "COUNTERFACTUAL_DECISION_TIME")
        realized = _parse_utc(self.realized_at, "COUNTERFACTUAL_REALIZED_AT")
        if realized <= decision:
            raise ValidationMetaError("COUNTERFACTUAL_TIME_ORDER_INVALID")
        try:
            action = DecisionAction(self.recommended_action)
        except (TypeError, ValueError) as exc:
            raise ValidationMetaError("COUNTERFACTUAL_ACTION_INVALID") from exc
        object.__setattr__(self, "recommended_action", action)
        utilities: dict[str, float] = {}
        for required in DecisionAction:
            raw = self.action_utilities.get(required.value)
            if raw is None or not _finite(raw):
                raise ValidationMetaError(f"COUNTERFACTUAL_UTILITY_MISSING:{required.value}")
            utilities[required.value] = float(raw)
        if set(self.action_utilities) != set(utilities):
            raise ValidationMetaError("COUNTERFACTUAL_UTILITY_ACTION_UNSUPPORTED")
        object.__setattr__(self, "action_utilities", MappingProxyType(utilities))
        evidence = tuple(str(item).strip() for item in self.evidence_ids)
        if not evidence or any(not item for item in evidence):
            raise ValidationMetaError("COUNTERFACTUAL_EVIDENCE_MISSING")
        if len(set(evidence)) != len(evidence):
            raise ValidationMetaError("COUNTERFACTUAL_EVIDENCE_DUPLICATE")
        object.__setattr__(self, "evidence_ids", evidence)
        if self.market_price_used_as_weather_input:
            raise ValidationMetaError("MARKET_PRICE_WEATHER_INPUT_PROHIBITED")
        if self.can_execute:
            raise ValidationMetaError("COUNTERFACTUAL_EXECUTION_PROHIBITED")

    @property
    def recommended_utility(self) -> float:
        return float(self.action_utilities[self.recommended_action.value])

    @property
    def oracle_utility(self) -> float:
        return max(float(value) for value in self.action_utilities.values())

    @property
    def regret(self) -> float:
        return self.oracle_utility - self.recommended_utility

    def canonical(self) -> dict[str, object]:
        return {
            "sample_id": self.sample_id,
            "policy_id": self.policy_id,
            "policy_version": self.policy_version,
            "decision_time": _iso(_parse_utc(self.decision_time, "COUNTERFACTUAL_DECISION_TIME")),
            "realized_at": _iso(_parse_utc(self.realized_at, "COUNTERFACTUAL_REALIZED_AT")),
            "recommended_action": self.recommended_action.value,
            "action_utilities": dict(sorted(self.action_utilities.items())),
            "evidence_ids": sorted(self.evidence_ids),
            "market_price_used_as_weather_input": False,
            "can_execute": False,
        }


@dataclass(frozen=True)
class CounterfactualPolicyScore:
    policy_identity: str
    sample_n: int
    recommended_utility_mean: float
    oracle_utility_mean: float
    regret_mean: float
    positive_utility_rate: float
    abstention_rate: float


@dataclass(frozen=True)
class SettlementRedTeamFixture:
    fixture_id: str
    version: str
    category: SettlementRedTeamCategory
    expected_terminal_code: str
    evidence_ids: tuple[str, ...]
    description: str
    can_execute: bool = False

    def __post_init__(self) -> None:
        for name in ("fixture_id", "version", "expected_terminal_code", "description"):
            if not str(getattr(self, name) or "").strip():
                raise ValidationMetaError(f"RED_TEAM_{name.upper()}_MISSING")
        try:
            category = SettlementRedTeamCategory(self.category)
        except (TypeError, ValueError) as exc:
            raise ValidationMetaError("RED_TEAM_CATEGORY_INVALID") from exc
        object.__setattr__(self, "category", category)
        evidence = tuple(str(item).strip() for item in self.evidence_ids)
        if not evidence or any(not item for item in evidence):
            raise ValidationMetaError("RED_TEAM_EVIDENCE_MISSING")
        if len(set(evidence)) != len(evidence):
            raise ValidationMetaError("RED_TEAM_EVIDENCE_DUPLICATE")
        object.__setattr__(self, "evidence_ids", evidence)
        if self.can_execute:
            raise ValidationMetaError("RED_TEAM_EXECUTION_PROHIBITED")

    @property
    def identity(self) -> str:
        return f"{self.fixture_id}@{self.version}"

    def canonical(self) -> dict[str, object]:
        return {
            "fixture_id": self.fixture_id,
            "version": self.version,
            "category": self.category.value,
            "expected_terminal_code": self.expected_terminal_code,
            "evidence_ids": sorted(self.evidence_ids),
            "description": self.description,
            "can_execute": False,
        }


class SettlementRedTeamRegistry:
    def __init__(self) -> None:
        self._fixtures: dict[str, SettlementRedTeamFixture] = {}

    def register(self, fixture: SettlementRedTeamFixture) -> SettlementRedTeamFixture:
        existing = self._fixtures.get(fixture.identity)
        if existing is not None and existing != fixture:
            raise ValidationMetaError("RED_TEAM_FIXTURE_VERSION_COLLISION")
        self._fixtures[fixture.identity] = fixture
        return fixture

    def register_many(self, fixtures: Sequence[SettlementRedTeamFixture]) -> None:
        for fixture in fixtures:
            self.register(fixture)

    def snapshot(self) -> tuple[SettlementRedTeamFixture, ...]:
        return tuple(self._fixtures[key] for key in sorted(self._fixtures))


@dataclass(frozen=True)
class SettlementRedTeamObservation:
    fixture_identity: str
    observed_terminal_code: str
    observed_at: str
    evidence_ids: tuple[str, ...]
    can_execute: bool = False

    def __post_init__(self) -> None:
        if not self.fixture_identity.strip():
            raise ValidationMetaError("RED_TEAM_OBSERVATION_FIXTURE_ID_MISSING")
        if not self.observed_terminal_code.strip():
            raise ValidationMetaError("RED_TEAM_OBSERVED_TERMINAL_CODE_MISSING")
        _parse_utc(self.observed_at, "RED_TEAM_OBSERVED_AT")
        evidence = tuple(str(item).strip() for item in self.evidence_ids)
        if not evidence or any(not item for item in evidence):
            raise ValidationMetaError("RED_TEAM_OBSERVATION_EVIDENCE_MISSING")
        if len(set(evidence)) != len(evidence):
            raise ValidationMetaError("RED_TEAM_OBSERVATION_EVIDENCE_DUPLICATE")
        object.__setattr__(self, "evidence_ids", evidence)
        if self.can_execute:
            raise ValidationMetaError("RED_TEAM_OBSERVATION_EXECUTION_PROHIBITED")


@dataclass(frozen=True)
class SettlementRedTeamResult:
    fixture_identity: str
    category: str
    expected_terminal_code: str
    observed_terminal_code: str | None
    passed: bool
    status: str


@dataclass(frozen=True)
class ComplexityGatePolicy:
    policy_id: str
    version: str
    evidence_id: str
    minimum_holdout_n: int
    minimum_brier_advantage: float
    minimum_log_loss_advantage: float
    minimum_red_team_pass_rate: float
    minimum_selected_coverage: float
    maximum_selected_brier: float
    research_only: bool = True
    can_execute: bool = False

    def __post_init__(self) -> None:
        for name in ("policy_id", "version", "evidence_id"):
            if not str(getattr(self, name) or "").strip():
                raise ValidationMetaError(f"COMPLEXITY_{name.upper()}_MISSING")
        if not isinstance(self.minimum_holdout_n, int) or isinstance(self.minimum_holdout_n, bool) or self.minimum_holdout_n <= 0:
            raise ValidationMetaError("COMPLEXITY_MINIMUM_HOLDOUT_N_INVALID")
        for name in ("minimum_brier_advantage", "minimum_log_loss_advantage"):
            value = getattr(self, name)
            if not _finite(value):
                raise ValidationMetaError(f"COMPLEXITY_{name.upper()}_INVALID")
        for name in ("minimum_red_team_pass_rate", "minimum_selected_coverage", "maximum_selected_brier"):
            _bounded(getattr(self, name), f"COMPLEXITY_{name.upper()}")
        if self.can_execute:
            raise ValidationMetaError("COMPLEXITY_GATE_EXECUTION_PROHIBITED")


@dataclass(frozen=True)
class ComplexityGateResult:
    policy_identity: str
    recommendation: ComplexityRecommendation
    blockers: tuple[str, ...]
    holdout_n: int
    worst_brier_advantage: float | None
    worst_log_loss_advantage: float | None
    red_team_pass_rate: float
    selected_coverage: float | None
    selected_brier: float | None
    can_execute: bool = False

    def __post_init__(self) -> None:
        if self.can_execute:
            raise ValidationMetaError("COMPLEXITY_RESULT_EXECUTION_PROHIBITED")


@dataclass(frozen=True)
class ValidationMetaReport:
    report_id: str
    as_of_time: str
    input_manifest_json: str
    model_scores: tuple[ModelScore, ...]
    baseline_deltas: tuple[BaselineDelta, ...]
    selective_curve: tuple[SelectiveScorePoint, ...]
    counterfactual_scores: tuple[CounterfactualPolicyScore, ...]
    red_team_results: tuple[SettlementRedTeamResult, ...]
    complexity_gate: ComplexityGateResult
    sample_ids: tuple[str, ...]
    counterfactual_sample_ids: tuple[str, ...]
    validation_meta_version: str = VALIDATION_META_VERSION
    market_price_used_as_weather_input: bool = False
    can_execute: bool = False

    def __post_init__(self) -> None:
        try:
            manifest = json.loads(self.input_manifest_json)
        except (TypeError, json.JSONDecodeError) as exc:
            raise ValidationMetaError("VALIDATION_INPUT_MANIFEST_INVALID") from exc
        if not isinstance(manifest, dict):
            raise ValidationMetaError("VALIDATION_INPUT_MANIFEST_INVALID")
        canonical = _canonical_json(manifest)
        object.__setattr__(self, "input_manifest_json", canonical)
        expected = validation_report_id_from_manifest(manifest)
        if self.report_id != expected:
            raise ValidationMetaError("VALIDATION_REPORT_IDENTITY_MISMATCH")
        if self.market_price_used_as_weather_input:
            raise ValidationMetaError("MARKET_PRICE_WEATHER_INPUT_PROHIBITED")
        if self.can_execute:
            raise ValidationMetaError("VALIDATION_REPORT_EXECUTION_PROHIBITED")

    @property
    def input_manifest(self) -> dict[str, object]:
        return json.loads(self.input_manifest_json)

    def persistence_row(self) -> dict[str, object]:
        return {
            "report_id": self.report_id,
            "as_of_time": _iso(_parse_utc(self.as_of_time, "VALIDATION_REPORT_AS_OF")),
            "validation_meta_version": self.validation_meta_version,
            "input_manifest": self.input_manifest,
            "sample_ids": list(self.sample_ids),
            "counterfactual_sample_ids": list(self.counterfactual_sample_ids),
            "model_scores": [_jsonable(item) for item in self.model_scores],
            "baseline_deltas": [_jsonable(item) for item in self.baseline_deltas],
            "selective_curve": [_jsonable(item) for item in self.selective_curve],
            "counterfactual_scores": [_jsonable(item) for item in self.counterfactual_scores],
            "red_team_results": [_jsonable(item) for item in self.red_team_results],
            "complexity_gate": {
                **_jsonable(self.complexity_gate),
                "recommendation": self.complexity_gate.recommendation.value,
                "can_execute": False,
            },
            "market_price_used_as_weather_input": False,
            "can_execute": False,
        }


class ValidationMetaLayer:
    def evaluate(
        self,
        *,
        as_of_time: str,
        probability_samples: Sequence[ProbabilityValidationSample],
        selective_confidence_thresholds: Sequence[float],
        counterfactual_samples: Sequence[CounterfactualDecisionSample],
        red_team_fixtures: Sequence[SettlementRedTeamFixture],
        red_team_observations: Sequence[SettlementRedTeamObservation],
        complexity_policy: ComplexityGatePolicy,
    ) -> ValidationMetaReport:
        as_of = _parse_utc(as_of_time, "VALIDATION_REPORT_AS_OF")
        samples = tuple(probability_samples)
        if not samples:
            raise ValidationMetaError("VALIDATION_PROBABILITY_SAMPLES_MISSING")
        _unique((item.sample_id for item in samples), "VALIDATION_SAMPLE_ID_DUPLICATE")
        for item in samples:
            if _parse_utc(item.settled_at, "VALIDATION_SETTLED_AT") > as_of:
                raise ValidationMetaError(f"VALIDATION_FUTURE_OUTCOME:{item.sample_id}")

        thresholds = tuple(float(_bounded(value, "SELECTIVE_CONFIDENCE_THRESHOLD")) for value in selective_confidence_thresholds)
        if not thresholds:
            raise ValidationMetaError("SELECTIVE_CONFIDENCE_THRESHOLDS_MISSING")
        if len(set(thresholds)) != len(thresholds):
            raise ValidationMetaError("SELECTIVE_CONFIDENCE_THRESHOLD_DUPLICATE")
        thresholds = tuple(sorted(thresholds))

        counterfactual = tuple(counterfactual_samples)
        _unique((item.sample_id for item in counterfactual), "COUNTERFACTUAL_SAMPLE_ID_DUPLICATE")
        for item in counterfactual:
            if _parse_utc(item.realized_at, "COUNTERFACTUAL_REALIZED_AT") > as_of:
                raise ValidationMetaError(f"COUNTERFACTUAL_FUTURE_OUTCOME:{item.sample_id}")

        fixtures = tuple(red_team_fixtures)
        _unique((item.identity for item in fixtures), "RED_TEAM_FIXTURE_IDENTITY_DUPLICATE")
        observations = tuple(red_team_observations)
        _unique((item.fixture_identity for item in observations), "RED_TEAM_OBSERVATION_FIXTURE_DUPLICATE")
        for item in observations:
            if _parse_utc(item.observed_at, "RED_TEAM_OBSERVED_AT") > as_of:
                raise ValidationMetaError(f"RED_TEAM_FUTURE_OBSERVATION:{item.fixture_identity}")

        model_scores, baseline_deltas = _score_probability_models(samples)
        selective_curve = _selective_curve(samples, thresholds)
        counterfactual_scores = _counterfactual_scores(counterfactual)
        red_team_results = _red_team_results(fixtures, observations)
        complexity = _complexity_gate(
            policy=complexity_policy,
            holdout_n=len(samples),
            baseline_deltas=baseline_deltas,
            red_team_results=red_team_results,
            selective_curve=selective_curve,
        )

        input_manifest = _input_manifest(
            as_of_time=as_of_time,
            probability_samples=samples,
            selective_confidence_thresholds=thresholds,
            counterfactual_samples=counterfactual,
            red_team_fixtures=fixtures,
            red_team_observations=observations,
            complexity_policy=complexity_policy,
        )
        report_id = validation_report_id_from_manifest(input_manifest)
        return ValidationMetaReport(
            report_id=report_id,
            as_of_time=_iso(as_of),
            input_manifest_json=_canonical_json(input_manifest),
            model_scores=model_scores,
            baseline_deltas=baseline_deltas,
            selective_curve=selective_curve,
            counterfactual_scores=counterfactual_scores,
            red_team_results=red_team_results,
            complexity_gate=complexity,
            sample_ids=tuple(sorted(item.sample_id for item in samples)),
            counterfactual_sample_ids=tuple(sorted(item.sample_id for item in counterfactual)),
        )


def _input_manifest(
    *,
    as_of_time: str,
    probability_samples: Sequence[ProbabilityValidationSample],
    selective_confidence_thresholds: Sequence[float],
    counterfactual_samples: Sequence[CounterfactualDecisionSample],
    red_team_fixtures: Sequence[SettlementRedTeamFixture],
    red_team_observations: Sequence[SettlementRedTeamObservation],
    complexity_policy: ComplexityGatePolicy,
) -> dict[str, object]:
    return {
        "version": VALIDATION_META_VERSION,
        "as_of_time": _iso(_parse_utc(as_of_time, "VALIDATION_REPORT_AS_OF")),
        "probability_samples": [item.canonical() for item in sorted(probability_samples, key=lambda item: item.sample_id)],
        "selective_confidence_thresholds": sorted(float(value) for value in selective_confidence_thresholds),
        "counterfactual_samples": [item.canonical() for item in sorted(counterfactual_samples, key=lambda item: item.sample_id)],
        "red_team_fixtures": [item.canonical() for item in sorted(red_team_fixtures, key=lambda item: item.identity)],
        "red_team_observations": [
            {
                "fixture_identity": item.fixture_identity,
                "observed_terminal_code": item.observed_terminal_code,
                "observed_at": _iso(_parse_utc(item.observed_at, "RED_TEAM_OBSERVED_AT")),
                "evidence_ids": sorted(item.evidence_ids),
                "can_execute": False,
            }
            for item in sorted(red_team_observations, key=lambda item: item.fixture_identity)
        ],
        "complexity_policy": {
            "policy_id": complexity_policy.policy_id,
            "version": complexity_policy.version,
            "evidence_id": complexity_policy.evidence_id,
            "minimum_holdout_n": complexity_policy.minimum_holdout_n,
            "minimum_brier_advantage": complexity_policy.minimum_brier_advantage,
            "minimum_log_loss_advantage": complexity_policy.minimum_log_loss_advantage,
            "minimum_red_team_pass_rate": complexity_policy.minimum_red_team_pass_rate,
            "minimum_selected_coverage": complexity_policy.minimum_selected_coverage,
            "maximum_selected_brier": complexity_policy.maximum_selected_brier,
            "research_only": complexity_policy.research_only,
            "can_execute": False,
        },
    }


def validation_report_id_from_manifest(manifest: Mapping[str, object]) -> str:
    digest = hashlib.sha256(_canonical_json(dict(manifest)).encode("utf-8")).hexdigest()
    return f"kalshi-weather-validation-meta-{digest[:24]}"


def _score_probability_models(
    samples: Sequence[ProbabilityValidationSample],
) -> tuple[tuple[ModelScore, ...], tuple[BaselineDelta, ...]]:
    champion_probs = [float(item.champion_p_yes) for item in samples]
    outcomes = [1.0 if item.yes_outcome else 0.0 for item in samples]
    scores: list[ModelScore] = [
        _model_score("CHAMPION", "CHAMPION", champion_probs, outcomes)
    ]

    baseline_rows: dict[str, list[tuple[float, float, float]]] = {}
    for item in samples:
        outcome = 1.0 if item.yes_outcome else 0.0
        for baseline in item.baselines:
            baseline_rows.setdefault(baseline.identity, []).append(
                (float(item.champion_p_yes), float(baseline.p_yes), outcome)
            )

    deltas: list[BaselineDelta] = []
    for identity in sorted(baseline_rows):
        rows = baseline_rows[identity]
        baseline_probs = [row[1] for row in rows]
        paired_outcomes = [row[2] for row in rows]
        kind = identity.split(":", 1)[0]
        scores.append(_model_score(identity, kind, baseline_probs, paired_outcomes))
        champion_brier = mean((row[0] - row[2]) ** 2 for row in rows)
        baseline_brier = mean((row[1] - row[2]) ** 2 for row in rows)
        champion_log = mean(_log_loss(row[0], row[2]) for row in rows)
        baseline_log = mean(_log_loss(row[1], row[2]) for row in rows)
        deltas.append(
            BaselineDelta(
                baseline_identity=identity,
                baseline_kind=kind,
                paired_n=len(rows),
                champion_brier_advantage=baseline_brier - champion_brier,
                champion_log_loss_advantage=baseline_log - champion_log,
            )
        )
    return tuple(scores), tuple(deltas)


def _model_score(model_id: str, kind: str, probabilities: Sequence[float], outcomes: Sequence[float]) -> ModelScore:
    if not probabilities or len(probabilities) != len(outcomes):
        raise ValidationMetaError("MODEL_SCORE_SAMPLE_ALIGNMENT_INVALID")
    return ModelScore(
        model_id=model_id,
        kind=kind,
        sample_n=len(probabilities),
        brier_score=mean((p - y) ** 2 for p, y in zip(probabilities, outcomes)),
        log_loss=mean(_log_loss(p, y) for p, y in zip(probabilities, outcomes)),
        calibration_gap=abs(mean(probabilities) - mean(outcomes)),
    )


def _selective_curve(
    samples: Sequence[ProbabilityValidationSample],
    thresholds: Sequence[float],
) -> tuple[SelectiveScorePoint, ...]:
    out: list[SelectiveScorePoint] = []
    total = len(samples)
    for threshold in thresholds:
        chosen = [item for item in samples if float(item.confidence_score) >= float(threshold)]
        if not chosen:
            out.append(
                SelectiveScorePoint(
                    minimum_confidence=float(threshold),
                    selected_n=0,
                    coverage=0.0,
                    brier_score=None,
                    log_loss=None,
                    calibration_gap=None,
                )
            )
            continue
        probs = [float(item.champion_p_yes) for item in chosen]
        outcomes = [1.0 if item.yes_outcome else 0.0 for item in chosen]
        score = _model_score("CHAMPION", "CHAMPION", probs, outcomes)
        out.append(
            SelectiveScorePoint(
                minimum_confidence=float(threshold),
                selected_n=len(chosen),
                coverage=len(chosen) / total,
                brier_score=score.brier_score,
                log_loss=score.log_loss,
                calibration_gap=score.calibration_gap,
            )
        )
    return tuple(out)


def _counterfactual_scores(
    samples: Sequence[CounterfactualDecisionSample],
) -> tuple[CounterfactualPolicyScore, ...]:
    grouped: dict[str, list[CounterfactualDecisionSample]] = {}
    for item in samples:
        grouped.setdefault(f"{item.policy_id}@{item.policy_version}", []).append(item)
    out: list[CounterfactualPolicyScore] = []
    for identity in sorted(grouped):
        rows = grouped[identity]
        out.append(
            CounterfactualPolicyScore(
                policy_identity=identity,
                sample_n=len(rows),
                recommended_utility_mean=mean(item.recommended_utility for item in rows),
                oracle_utility_mean=mean(item.oracle_utility for item in rows),
                regret_mean=mean(item.regret for item in rows),
                positive_utility_rate=mean(1.0 if item.recommended_utility > 0.0 else 0.0 for item in rows),
                abstention_rate=mean(1.0 if item.recommended_action is DecisionAction.ABSTAIN else 0.0 for item in rows),
            )
        )
    return tuple(out)


def _red_team_results(
    fixtures: Sequence[SettlementRedTeamFixture],
    observations: Sequence[SettlementRedTeamObservation],
) -> tuple[SettlementRedTeamResult, ...]:
    fixture_by_id = {item.identity: item for item in fixtures}
    observation_by_id = {item.fixture_identity: item for item in observations}
    unknown = sorted(set(observation_by_id) - set(fixture_by_id))
    if unknown:
        raise ValidationMetaError(f"RED_TEAM_OBSERVATION_FIXTURE_UNKNOWN:{','.join(unknown)}")
    out: list[SettlementRedTeamResult] = []
    for identity in sorted(fixture_by_id):
        fixture = fixture_by_id[identity]
        observation = observation_by_id.get(identity)
        if observation is None:
            out.append(
                SettlementRedTeamResult(
                    fixture_identity=identity,
                    category=fixture.category.value,
                    expected_terminal_code=fixture.expected_terminal_code,
                    observed_terminal_code=None,
                    passed=False,
                    status="NOT_OBSERVED",
                )
            )
            continue
        passed = observation.observed_terminal_code == fixture.expected_terminal_code
        out.append(
            SettlementRedTeamResult(
                fixture_identity=identity,
                category=fixture.category.value,
                expected_terminal_code=fixture.expected_terminal_code,
                observed_terminal_code=observation.observed_terminal_code,
                passed=passed,
                status="PASS" if passed else "FAIL",
            )
        )
    return tuple(out)


def _complexity_gate(
    *,
    policy: ComplexityGatePolicy,
    holdout_n: int,
    baseline_deltas: Sequence[BaselineDelta],
    red_team_results: Sequence[SettlementRedTeamResult],
    selective_curve: Sequence[SelectiveScorePoint],
) -> ComplexityGateResult:
    blockers: list[str] = []
    if holdout_n < policy.minimum_holdout_n:
        blockers.append("HOLDOUT_N_BELOW_POLICY_MINIMUM")

    meteorological_deltas = [
        item for item in baseline_deltas
        if item.baseline_kind in {BaselineKind.NBM.value, BaselineKind.NWS.value, BaselineKind.CLIMATOLOGY.value}
    ]
    worst_brier = min((item.champion_brier_advantage for item in meteorological_deltas), default=None)
    worst_log = min((item.champion_log_loss_advantage for item in meteorological_deltas), default=None)
    if worst_brier is None:
        blockers.append("BASELINE_COMPARISON_MISSING")
    elif worst_brier < policy.minimum_brier_advantage:
        blockers.append("BRIER_ADVANTAGE_BELOW_POLICY_MINIMUM")
    if worst_log is None:
        blockers.append("BASELINE_LOG_LOSS_COMPARISON_MISSING")
    elif worst_log < policy.minimum_log_loss_advantage:
        blockers.append("LOG_LOSS_ADVANTAGE_BELOW_POLICY_MINIMUM")

    red_team_pass_rate = (
        mean(1.0 if item.passed else 0.0 for item in red_team_results)
        if red_team_results
        else 0.0
    )
    if red_team_pass_rate < policy.minimum_red_team_pass_rate:
        blockers.append("RED_TEAM_PASS_RATE_BELOW_POLICY_MINIMUM")

    eligible_selective = [
        item
        for item in selective_curve
        if item.coverage >= policy.minimum_selected_coverage and item.brier_score is not None
    ]
    if not eligible_selective:
        selected_coverage = None
        selected_brier = None
        blockers.append("SELECTIVE_COVERAGE_POLICY_UNSATISFIED")
    else:
        best = min(eligible_selective, key=lambda item: float(item.brier_score))
        selected_coverage = best.coverage
        selected_brier = best.brier_score
        if float(best.brier_score) > policy.maximum_selected_brier:
            blockers.append("SELECTIVE_BRIER_ABOVE_POLICY_MAXIMUM")

    recommendation = (
        ComplexityRecommendation.ELIGIBLE_FOR_GOVERNED_REVIEW
        if not blockers
        else ComplexityRecommendation.HOLD
    )
    return ComplexityGateResult(
        policy_identity=f"{policy.policy_id}@{policy.version}",
        recommendation=recommendation,
        blockers=tuple(blockers),
        holdout_n=holdout_n,
        worst_brier_advantage=worst_brier,
        worst_log_loss_advantage=worst_log,
        red_team_pass_rate=red_team_pass_rate,
        selected_coverage=selected_coverage,
        selected_brier=selected_brier,
    )


def _log_loss(probability: float, outcome: float) -> float:
    p = _strict_probability(probability, "LOG_LOSS_PROBABILITY")
    y = 1.0 if outcome >= 0.5 else 0.0
    return -(y * math.log(p) + (1.0 - y) * math.log(1.0 - p))


def _strict_probability(value: float, code: str) -> float:
    if not _finite(value):
        raise ValidationMetaError(f"{code}_INVALID")
    number = float(value)
    if not (0.0 < number < 1.0):
        raise ValidationMetaError(f"{code}_INVALID")
    return number


def _bounded(value: float, code: str) -> float:
    if not _finite(value):
        raise ValidationMetaError(f"{code}_INVALID")
    number = float(value)
    if not (0.0 <= number <= 1.0):
        raise ValidationMetaError(f"{code}_OUT_OF_RANGE")
    return number


def _finite(value: object) -> bool:
    return isinstance(value, (int, float)) and not isinstance(value, bool) and math.isfinite(float(value))


def _unique(values, code: str) -> None:
    items = list(values)
    if len(set(items)) != len(items):
        raise ValidationMetaError(code)


def _parse_utc(value: str, code: str) -> datetime:
    text = str(value or "").strip()
    if text.endswith("Z"):
        text = text[:-1] + "+00:00"
    try:
        parsed = datetime.fromisoformat(text)
    except ValueError as exc:
        raise ValidationMetaError(f"{code}_INVALID") from exc
    if parsed.tzinfo is None:
        raise ValidationMetaError(f"{code}_TIMEZONE_REQUIRED")
    return parsed.astimezone(timezone.utc)


def _iso(value: datetime) -> str:
    return value.astimezone(timezone.utc).isoformat().replace("+00:00", "Z")


def _canonical_json(value: object) -> str:
    return json.dumps(value, sort_keys=True, separators=(",", ":"), default=str)


def _jsonable(value) -> dict[str, object]:
    return {
        key: (item.value if isinstance(item, Enum) else item)
        for key, item in value.__dict__.items()
    }
