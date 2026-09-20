"""WOW V17 Core Intelligence: governed, advisory learning from frozen predictions.

The module is intentionally pure: it computes immutable learning observations,
cohort diagnostics, hypotheses, and challenger review evidence. It never updates
a source prediction, changes a probability, promotes a model, or executes a wager.
"""
from __future__ import annotations

from dataclasses import asdict, dataclass
from math import log
from typing import Any, Iterable, Mapping
from uuid import NAMESPACE_URL, uuid5

CAN_EXECUTE = False
AUTHORITY = "ADVISORY_ONLY"
SCHEMA_VERSION = "WOW17_CORE_INTELLIGENCE_V1"

_POSITIVE_RESULTS = {"HIT", "WIN", "WON", "SETTLED_WIN"}
_NEGATIVE_RESULTS = {"MISS", "LOSS", "LOST", "SETTLED_LOSS"}
_NON_BINARY_RESULTS = {"PUSH", "TIE", "VOID", "REFUND", "VOID_NOT_STARTER"}

_PROBABILITY_FIELDS = (
    "calibrated_probability",
    "raw_model_probability",
    "model_probability",
)


def _text(value: Any) -> str | None:
    value = str(value or "").strip()
    return value or None


def _norm(value: Any) -> str:
    return str(value or "").strip().upper()


def _probability(value: Any) -> float | None:
    if isinstance(value, bool) or not isinstance(value, (int, float)):
        return None
    value = float(value)
    if not 0.0 < value < 1.0:
        return None
    return value


def _outcome_target(official_result: Any, hit: Any = None) -> int | None:
    result = _norm(official_result)
    if result in _POSITIVE_RESULTS:
        return 1
    if result in _NEGATIVE_RESULTS:
        return 0
    if result in _NON_BINARY_RESULTS:
        return None
    if isinstance(hit, bool):
        return int(hit)
    return None


def _selected_probability(prediction: Mapping[str, Any]) -> tuple[float | None, str | None]:
    for field in _PROBABILITY_FIELDS:
        value = _probability(prediction.get(field))
        if value is not None:
            return value, field
    return None, None


def _calibration_bucket(probability: float | None) -> str | None:
    if probability is None:
        return None
    low = int(probability * 10) / 10
    if low >= 1.0:
        low = 0.9
    high = min(1.0, low + 0.1)
    return f"{low:.1f}-{high:.1f}"


@dataclass(frozen=True)
class LearningObservation:
    observation_id: str
    source_prediction_kind: str
    source_prediction_id: str
    sport: str | None
    league: str | None
    market_family: str | None
    stat_type: str | None
    direction: str | None
    model_family: str | None
    model_artifact_version: str | None
    calibration_version: str | None
    probability: float | None
    probability_source: str | None
    calibrated_lower_bound: float | None
    official_result: str
    outcome_target: int | None
    residual: float | None
    brier_score: float | None
    log_loss: float | None
    calibration_bucket: str | None
    actual_value: float | None
    signed_distance_to_threshold: float | None
    close_miss_flag: bool | None
    process_classification: str | None
    diagnostic_tags: tuple[str, ...]
    settlement_source: str | None
    settlement_timestamp: str | None
    schema_version: str = SCHEMA_VERSION
    authority: str = AUTHORITY
    can_execute: bool = CAN_EXECUTE

    def as_dict(self) -> dict[str, Any]:
        row = asdict(self)
        row["diagnostic_tags"] = list(self.diagnostic_tags)
        return row


@dataclass(frozen=True)
class CohortSummary:
    cohort_key: str
    sample_n: int
    scored_n: int
    wins: int
    losses: int
    pushes_or_voids: int
    mean_probability: float | None
    observed_rate: float | None
    calibration_bias: float | None
    mean_brier_score: float | None
    mean_log_loss: float | None
    close_miss_n: int
    diagnostic_tag_counts: tuple[tuple[str, int], ...]
    ready_for_hypothesis: bool
    min_samples: int
    authority: str = AUTHORITY
    can_execute: bool = CAN_EXECUTE

    def as_dict(self) -> dict[str, Any]:
        row = asdict(self)
        row["diagnostic_tag_counts"] = dict(self.diagnostic_tag_counts)
        return row


@dataclass(frozen=True)
class LearningHypothesis:
    hypothesis_id: str
    cohort_key: str
    hypothesis_type: str
    direction: str
    evidence_n: int
    metric_name: str
    metric_value: float
    threshold: float
    status: str = "OPEN_FOR_REVIEW"
    automatic_promotion_allowed: bool = False
    authority: str = AUTHORITY
    can_execute: bool = CAN_EXECUTE

    def as_dict(self) -> dict[str, Any]:
        return asdict(self)


@dataclass(frozen=True)
class ChallengerEvaluation:
    challenger_id: str
    cohort_key: str
    holdout_n: int
    champion_brier: float
    challenger_brier: float
    brier_improvement: float
    champion_log_loss: float | None
    challenger_log_loss: float | None
    eligible_for_review: bool
    review_status: str
    automatic_promotion_allowed: bool = False
    authority: str = AUTHORITY
    can_execute: bool = CAN_EXECUTE

    def as_dict(self) -> dict[str, Any]:
        return asdict(self)


def build_learning_observation(
    prediction: Mapping[str, Any],
    outcome: Mapping[str, Any] | Any,
    *,
    source_prediction_kind: str = "PROP",
) -> LearningObservation:
    """Build a frozen learning observation without mutating either input."""
    if hasattr(outcome, "as_dict"):
        outcome = outcome.as_dict()
    if not isinstance(outcome, Mapping):
        raise ValueError("OUTCOME_MAPPING_REQUIRED")

    prediction_id = _text(
        prediction.get("prediction_id")
        or prediction.get("event_prediction_id")
        or outcome.get("prediction_id")
        or outcome.get("event_prediction_id")
    )
    if not prediction_id:
        raise ValueError("SOURCE_PREDICTION_ID_REQUIRED")

    official_result = _norm(outcome.get("official_result"))
    if not official_result:
        raise ValueError("OFFICIAL_RESULT_REQUIRED")

    probability, probability_source = _selected_probability(prediction)
    target = _outcome_target(official_result, outcome.get("hit"))
    residual = None
    brier = None
    logloss = None
    if target is not None and probability is not None:
        residual = float(target) - probability
        brier = residual * residual
        clipped = min(max(probability, 1e-12), 1.0 - 1e-12)
        logloss = -(target * log(clipped) + (1 - target) * log(1.0 - clipped))

    tags = prediction.get("failure_cause_tags") or ()
    if isinstance(tags, str):
        tags = (tags,)
    process_classification = _text(outcome.get("process_classification"))
    diagnostic_tags = tuple(sorted({
        _norm(tag) for tag in tags if _text(tag)
    } | ({_norm(process_classification)} if process_classification else set())))

    actual = outcome.get("actual_value", outcome.get("actual_stat"))
    actual_value = float(actual) if isinstance(actual, (int, float)) and not isinstance(actual, bool) else None
    distance = outcome.get("signed_distance_to_threshold")
    signed_distance = (
        float(distance) if isinstance(distance, (int, float)) and not isinstance(distance, bool) else None
    )
    lower = _probability(
        prediction.get("calibrated_probability_lower_bound", prediction.get("calibrated_lower_bound"))
    )

    source_kind = _norm(source_prediction_kind)
    if source_kind not in {"PROP", "EVENT"}:
        raise ValueError("SOURCE_PREDICTION_KIND_UNSUPPORTED")

    observation_id = str(uuid5(
        NAMESPACE_URL,
        f"wow17-core-intelligence:{source_kind}:{prediction_id}:{SCHEMA_VERSION}",
    ))

    return LearningObservation(
        observation_id=observation_id,
        source_prediction_kind=source_kind,
        source_prediction_id=prediction_id,
        sport=_text(prediction.get("sport")),
        league=_text(prediction.get("league")),
        market_family=_text(prediction.get("market_family") or prediction.get("market_type")),
        stat_type=_text(prediction.get("stat_type") or prediction.get("prop_type")),
        direction=_text(prediction.get("direction") or prediction.get("side")),
        model_family=_text(prediction.get("model_family")),
        model_artifact_version=_text(
            prediction.get("model_artifact_version") or prediction.get("model_version")
        ),
        calibration_version=_text(
            prediction.get("calibration_version") or prediction.get("calibrator_version")
        ),
        probability=probability,
        probability_source=probability_source,
        calibrated_lower_bound=lower,
        official_result=official_result,
        outcome_target=target,
        residual=residual,
        brier_score=brier,
        log_loss=logloss,
        calibration_bucket=_calibration_bucket(probability),
        actual_value=actual_value,
        signed_distance_to_threshold=signed_distance,
        close_miss_flag=outcome.get("close_miss_flag")
        if isinstance(outcome.get("close_miss_flag"), bool)
        else None,
        process_classification=process_classification,
        diagnostic_tags=diagnostic_tags,
        settlement_source=_text(outcome.get("settlement_source")),
        settlement_timestamp=_text(outcome.get("settlement_timestamp")),
        can_execute=False,
    )


def cohort_key(observation: LearningObservation) -> str:
    parts = (
        observation.source_prediction_kind,
        observation.sport or "UNKNOWN_SPORT",
        observation.league or "UNKNOWN_LEAGUE",
        observation.market_family or "UNKNOWN_MARKET",
        observation.stat_type or "UNKNOWN_STAT",
        observation.model_family or "UNKNOWN_MODEL",
        observation.model_artifact_version or "UNKNOWN_ARTIFACT",
        observation.calibration_version or "NO_CALIBRATOR",
    )
    return "|".join(parts)


def summarize_cohort(
    observations: Iterable[LearningObservation],
    *,
    min_samples: int = 30,
) -> CohortSummary:
    rows = list(observations)
    if not rows:
        raise ValueError("EMPTY_COHORT")
    if min_samples < 1:
        raise ValueError("INVALID_MIN_SAMPLES")
    keys = {cohort_key(row) for row in rows}
    if len(keys) != 1:
        raise ValueError("MIXED_COHORT")

    scored = [row for row in rows if row.outcome_target is not None and row.probability is not None]
    wins = sum(1 for row in rows if row.outcome_target == 1)
    losses = sum(1 for row in rows if row.outcome_target == 0)
    non_binary = len(rows) - wins - losses

    mean_probability = (
        sum(row.probability for row in scored if row.probability is not None) / len(scored)
        if scored else None
    )
    observed_rate = (
        sum(row.outcome_target for row in scored if row.outcome_target is not None) / len(scored)
        if scored else None
    )
    calibration_bias = (
        observed_rate - mean_probability
        if observed_rate is not None and mean_probability is not None
        else None
    )
    briers = [row.brier_score for row in scored if row.brier_score is not None]
    losses_metric = [row.log_loss for row in scored if row.log_loss is not None]
    tag_counts: dict[str, int] = {}
    for row in rows:
        for tag in row.diagnostic_tags:
            tag_counts[tag] = tag_counts.get(tag, 0) + 1

    return CohortSummary(
        cohort_key=next(iter(keys)),
        sample_n=len(rows),
        scored_n=len(scored),
        wins=wins,
        losses=losses,
        pushes_or_voids=non_binary,
        mean_probability=mean_probability,
        observed_rate=observed_rate,
        calibration_bias=calibration_bias,
        mean_brier_score=sum(briers) / len(briers) if briers else None,
        mean_log_loss=sum(losses_metric) / len(losses_metric) if losses_metric else None,
        close_miss_n=sum(1 for row in rows if row.close_miss_flag is True),
        diagnostic_tag_counts=tuple(sorted(tag_counts.items())),
        ready_for_hypothesis=len(scored) >= min_samples,
        min_samples=min_samples,
        can_execute=False,
    )


def detect_learning_hypotheses(
    summary: CohortSummary,
    *,
    calibration_bias_threshold: float = 0.075,
    brier_threshold: float = 0.25,
) -> tuple[LearningHypothesis, ...]:
    """Generate review hypotheses only after the cohort evidence floor is met."""
    if not summary.ready_for_hypothesis:
        return ()
    hypotheses: list[LearningHypothesis] = []

    if (
        summary.calibration_bias is not None
        and abs(summary.calibration_bias) >= calibration_bias_threshold
    ):
        direction = "UNDERCONFIDENT" if summary.calibration_bias > 0 else "OVERCONFIDENT"
        hypotheses.append(LearningHypothesis(
            hypothesis_id=str(uuid5(
                NAMESPACE_URL,
                f"{SCHEMA_VERSION}:{summary.cohort_key}:CALIBRATION_BIAS:{direction}",
            )),
            cohort_key=summary.cohort_key,
            hypothesis_type="CALIBRATION_BIAS",
            direction=direction,
            evidence_n=summary.scored_n,
            metric_name="calibration_bias",
            metric_value=summary.calibration_bias,
            threshold=calibration_bias_threshold,
        ))

    if summary.mean_brier_score is not None and summary.mean_brier_score >= brier_threshold:
        hypotheses.append(LearningHypothesis(
            hypothesis_id=str(uuid5(
                NAMESPACE_URL,
                f"{SCHEMA_VERSION}:{summary.cohort_key}:BRIER_DEGRADATION",
            )),
            cohort_key=summary.cohort_key,
            hypothesis_type="PREDICTIVE_ACCURACY_DEGRADATION",
            direction="REVIEW_MODEL_OR_FEATURES",
            evidence_n=summary.scored_n,
            metric_name="mean_brier_score",
            metric_value=summary.mean_brier_score,
            threshold=brier_threshold,
        ))

    return tuple(hypotheses)


def evaluate_challenger(
    *,
    challenger_id: str,
    cohort_key_value: str,
    holdout_n: int,
    champion_brier: float,
    challenger_brier: float,
    champion_log_loss: float | None = None,
    challenger_log_loss: float | None = None,
    min_holdout_n: int = 100,
    min_brier_improvement: float = 0.01,
) -> ChallengerEvaluation:
    """Evaluate shadow evidence; the result can only become eligible for review."""
    if not _text(challenger_id):
        raise ValueError("CHALLENGER_ID_REQUIRED")
    if not _text(cohort_key_value):
        raise ValueError("COHORT_KEY_REQUIRED")
    if holdout_n < 0 or min_holdout_n < 1:
        raise ValueError("INVALID_HOLDOUT_N")
    improvement = float(champion_brier) - float(challenger_brier)
    logloss_not_worse = (
        champion_log_loss is None
        or challenger_log_loss is None
        or float(challenger_log_loss) <= float(champion_log_loss)
    )
    eligible = (
        holdout_n >= min_holdout_n
        and improvement >= min_brier_improvement
        and logloss_not_worse
    )
    return ChallengerEvaluation(
        challenger_id=str(challenger_id).strip(),
        cohort_key=str(cohort_key_value).strip(),
        holdout_n=int(holdout_n),
        champion_brier=float(champion_brier),
        challenger_brier=float(challenger_brier),
        brier_improvement=improvement,
        champion_log_loss=float(champion_log_loss) if champion_log_loss is not None else None,
        challenger_log_loss=float(challenger_log_loss) if challenger_log_loss is not None else None,
        eligible_for_review=eligible,
        review_status="ELIGIBLE_FOR_GOVERNED_REVIEW" if eligible else "SHADOW_VALIDATING",
        automatic_promotion_allowed=False,
        can_execute=False,
    )


__all__ = [
    "AUTHORITY",
    "CAN_EXECUTE",
    "SCHEMA_VERSION",
    "LearningObservation",
    "CohortSummary",
    "LearningHypothesis",
    "ChallengerEvaluation",
    "build_learning_observation",
    "cohort_key",
    "summarize_cohort",
    "detect_learning_hypotheses",
    "evaluate_challenger",
]
