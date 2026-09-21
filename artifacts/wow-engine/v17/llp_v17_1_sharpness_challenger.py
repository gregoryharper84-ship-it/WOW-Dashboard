"""V17.1 LLP winner-ranking sharpness challenger.

Research/shadow only. This module tests ways to make team/event winner ranking
sharper without mutating production probabilities, calibration, admission
policy, V17 terminal authority, or execution posture.

It deliberately separates four questions:
1. Which side has the highest calibrated P(win)?
2. Which side has the strongest calibrated lower bound?
3. Which side has the strongest evidence-tuned uncertainty-adjusted score?
4. Is a candidate structurally invalid (hard block) or merely uncertain (soft)?

No function in this module places, approves, routes, or modifies a wager.
"""
from __future__ import annotations

from dataclasses import dataclass
from math import isfinite, log
from typing import Any, Iterable, Mapping, Sequence

SERVING_MODE = "SHADOW_ONLY"
CAN_EXECUTE = False
AUTOMATIC_PROMOTION_ALLOWED = False
PRODUCTION_RANKING_MUTATION_ALLOWED = False
PRODUCTION_CALIBRATION_MUTATION_ALLOWED = False
PRODUCTION_MARKET_PRIOR_MUTATION_ALLOWED = False
MARKET_PRIOR_WEIGHT = 0.0
CHALLENGER_VERSION = "LLP_V17_1_SHARPNESS_CHALLENGER_R1"

# Structural failures remain fail-closed. This set is intentionally narrow and
# may be extended only by the canonical failure registry / governed review.
HARD_BLOCK_CODES = frozenset(
    {
        "EVENT_ALREADY_STARTED",
        "EVENT_FINISHED",
        "EVENT_POSTPONED",
        "EVENT_CANCELED",
        "EVENT_NOT_FOUND",
        "WRONG_DATE",
        "WRONG_YEAR",
        "TIMEZONE_DATE_MISMATCH",
        "PARTICIPANT_IDENTITY_CONFLICT",
        "IDENTITY_UNRESOLVED",
        "SETTLEMENT_IDENTITY_UNRESOLVED",
        "MODEL_ROUTE_UNSUPPORTED",
        "MODEL_UNAVAILABLE",
        "MODEL_SCORER_FAILED",
        "MODEL_OUTPUT_INVALID",
        "MODEL_INPUTS_INSUFFICIENT",
        "PROBABILITY_PACKAGE_INVALID",
        "PROBABILITY_NORMALIZATION_FAILED",
        "STALE_PROBABILITY_AFTER_MATERIAL_UPDATE",
    }
)

# These are uncertainty states, not structural invalidity, when a valid fitted
# probability package still exists. They may widen uncertainty or trigger a
# refresh/recheck in the challenger, but do not automatically erase P(win).
SOFT_UNCERTAINTY_CODES = frozenset(
    {
        "LINEUP_UNCERTAINTY",
        "STARTER_UNCERTAINTY",
        "GOALIE_UNCERTAINTY",
        "QB_UNCERTAINTY",
        "INJURY_UNCERTAINTY",
        "WEATHER_UNCERTAINTY",
        "WORKLOAD_UNCERTAINTY",
        "SOURCE_CONFLICT_BOUNDED",
        "THIN_EFFECTIVE_SAMPLE",
        "MODEL_DISAGREEMENT_HIGH",
        "CALIBRATION_SAMPLE_THIN",
        "LATE_NEWS_RISK",
    }
)


class SharpnessChallengerError(ValueError):
    pass


@dataclass(frozen=True)
class CandidateProbability:
    candidate_id: str
    calibrated_probability: float
    calibrated_lower_bound: float
    calibrated_upper_bound: float | None = None
    hard_blockers: tuple[str, ...] = ()
    soft_uncertainties: tuple[str, ...] = ()


@dataclass(frozen=True)
class CandidateRanking:
    candidate_id: str
    calibrated_probability: float
    calibrated_lower_bound: float
    uncertainty_width_to_lower: float
    winner_likelihood_score: float
    confidence_floor_score: float
    uncertainty_adjusted_score: float
    lambda_penalty: float
    governance_class: str
    rank_eligible_shadow: bool


@dataclass(frozen=True)
class UncertaintyConsumption:
    source: str
    consumed_by_core_model: bool = False
    consumed_by_failure_path: bool = False
    consumed_by_calibration: bool = False
    consumed_by_bound: bool = False

    @property
    def consumed_layers(self) -> tuple[str, ...]:
        pairs = (
            ("core_model", self.consumed_by_core_model),
            ("failure_path", self.consumed_by_failure_path),
            ("calibration", self.consumed_by_calibration),
            ("bound", self.consumed_by_bound),
        )
        return tuple(name for name, active in pairs if active)

    @property
    def layer_count(self) -> int:
        return len(self.consumed_layers)

    @property
    def redundancy_review_required(self) -> bool:
        # Multiple layers are not automatically wrong; they require evidence
        # that each layer is addressing a distinct residual uncertainty.
        return self.layer_count >= 2


@dataclass(frozen=True)
class MarketDivergenceDiagnostic:
    calibrated_probability: float
    market_no_vig_probability: float
    absolute_divergence: float
    signed_divergence: float
    threshold: float
    status: str
    market_prior_weight: float = MARKET_PRIOR_WEIGHT
    probability_mutated: bool = False


@dataclass(frozen=True)
class SensitivityEffect:
    factor: str
    baseline_probability: float
    scenario_probability: float
    delta_probability: float
    delta_percentage_points: float


@dataclass(frozen=True)
class CohortCalibrationTarget:
    n: int
    mean_predicted_probability: float
    observed_rate: float
    global_observed_rate: float
    prior_strength: float
    local_weight: float
    shrunk_observed_target: float


@dataclass(frozen=True)
class RankingStrategyMetrics:
    strategy: str
    selected_rows: int
    slates: int
    top1_win_rate: float
    selected_hit_rate: float
    selected_brier: float
    selected_log_loss: float


def _probability(value: Any, *, field: str) -> float:
    if isinstance(value, bool):
        raise SharpnessChallengerError(f"MODEL_OUTPUT_INVALID: {field}_boolean")
    try:
        parsed = float(value)
    except (TypeError, ValueError) as exc:
        raise SharpnessChallengerError(f"MODEL_OUTPUT_INVALID: {field}_non_numeric") from exc
    if not isfinite(parsed) or not 0.0 < parsed < 1.0:
        raise SharpnessChallengerError(f"MODEL_OUTPUT_INVALID: {field}_out_of_range")
    return parsed


def _unit_interval(value: Any, *, field: str) -> float:
    try:
        parsed = float(value)
    except (TypeError, ValueError) as exc:
        raise SharpnessChallengerError(f"MODEL_INPUTS_INSUFFICIENT: {field}_non_numeric") from exc
    if not isfinite(parsed) or not 0.0 <= parsed <= 1.0:
        raise SharpnessChallengerError(f"MODEL_INPUTS_INSUFFICIENT: {field}_out_of_range")
    return parsed


def governance_classification(
    hard_blockers: Iterable[str] = (),
    soft_uncertainties: Iterable[str] = (),
) -> str:
    hard = tuple(str(code) for code in hard_blockers if code)
    soft = tuple(str(code) for code in soft_uncertainties if code)
    if hard:
        return "HARD_BLOCK"
    if soft:
        return "SOFT_UNCERTAINTY"
    return "CLEAR"


def score_candidate(candidate: CandidateProbability, *, lambda_penalty: float) -> CandidateRanking:
    """Create three parallel ranking views without mutating the probability.

    lambda_penalty=0 reproduces pure calibrated-probability ranking.
    lambda_penalty=1 reproduces pure calibrated-lower-bound ranking.
    Intermediate values are challenger-only and must be learned/validated OOS.
    """
    p = _probability(candidate.calibrated_probability, field="calibrated_probability")
    lb = _probability(candidate.calibrated_lower_bound, field="calibrated_lower_bound")
    if lb > p:
        raise SharpnessChallengerError("MODEL_OUTPUT_INVALID: lower_bound_above_point_probability")
    if candidate.calibrated_upper_bound is not None:
        ub = _probability(candidate.calibrated_upper_bound, field="calibrated_upper_bound")
        if ub < p:
            raise SharpnessChallengerError("MODEL_OUTPUT_INVALID: upper_bound_below_point_probability")
    lam = _unit_interval(lambda_penalty, field="lambda_penalty")
    width = p - lb
    adjusted = p - lam * width
    gclass = governance_classification(candidate.hard_blockers, candidate.soft_uncertainties)
    return CandidateRanking(
        candidate_id=candidate.candidate_id,
        calibrated_probability=p,
        calibrated_lower_bound=lb,
        uncertainty_width_to_lower=width,
        winner_likelihood_score=p,
        confidence_floor_score=lb,
        uncertainty_adjusted_score=adjusted,
        lambda_penalty=lam,
        governance_class=gclass,
        rank_eligible_shadow=gclass != "HARD_BLOCK",
    )


def rank_candidates(
    candidates: Sequence[CandidateProbability],
    *,
    strategy: str,
    lambda_penalty: float = 0.5,
) -> list[CandidateRanking]:
    scored = [score_candidate(candidate, lambda_penalty=lambda_penalty) for candidate in candidates]
    eligible = [row for row in scored if row.rank_eligible_shadow]
    key_by_strategy = {
        "CALIBRATED_PROBABILITY": lambda row: row.winner_likelihood_score,
        "CALIBRATED_LOWER_BOUND": lambda row: row.confidence_floor_score,
        "UNCERTAINTY_ADJUSTED": lambda row: row.uncertainty_adjusted_score,
    }
    try:
        key = key_by_strategy[str(strategy).upper()]
    except KeyError as exc:
        raise SharpnessChallengerError(f"MODEL_INPUTS_INSUFFICIENT: unknown_strategy={strategy}") from exc
    return sorted(eligible, key=lambda row: (key(row), row.calibrated_probability), reverse=True)


def uncertainty_consumption_audit(
    rows: Sequence[UncertaintyConsumption],
) -> dict[str, Any]:
    duplicate_risk = [row for row in rows if row.redundancy_review_required]
    return {
        "sources": tuple(row.source for row in rows),
        "redundancy_review_required": bool(duplicate_risk),
        "duplicate_consumption_sources": tuple(row.source for row in duplicate_risk),
        "details": tuple(
            {
                "source": row.source,
                "consumed_layers": row.consumed_layers,
                "layer_count": row.layer_count,
            }
            for row in rows
        ),
        "probability_mutated": False,
        "production_policy_mutated": False,
    }


def market_divergence_diagnostic(
    calibrated_probability: float,
    market_no_vig_probability: float,
    *,
    threshold: float = 0.08,
) -> MarketDivergenceDiagnostic:
    """Use market information as a recheck signal, never as the forecast."""
    p = _probability(calibrated_probability, field="calibrated_probability")
    market = _probability(market_no_vig_probability, field="market_no_vig_probability")
    t = _unit_interval(threshold, field="threshold")
    signed = p - market
    absolute = abs(signed)
    return MarketDivergenceDiagnostic(
        calibrated_probability=p,
        market_no_vig_probability=market,
        absolute_divergence=absolute,
        signed_divergence=signed,
        threshold=t,
        status="RECHECK_INPUTS" if absolute >= t else "NORMAL",
    )


def sensitivity_attribution(
    baseline_probability: float,
    scenario_probabilities: Mapping[str, float],
) -> list[SensitivityEffect]:
    """Return counterfactual sensitivity diagnostics sorted by absolute impact."""
    baseline = _probability(baseline_probability, field="baseline_probability")
    effects: list[SensitivityEffect] = []
    for factor, scenario_value in scenario_probabilities.items():
        scenario = _probability(scenario_value, field=f"scenario_probability:{factor}")
        delta = scenario - baseline
        effects.append(
            SensitivityEffect(
                factor=str(factor),
                baseline_probability=baseline,
                scenario_probability=scenario,
                delta_probability=delta,
                delta_percentage_points=delta * 100.0,
            )
        )
    return sorted(effects, key=lambda row: abs(row.delta_probability), reverse=True)


def hierarchical_cohort_target(
    *,
    n: int,
    mean_predicted_probability: float,
    observed_rate: float,
    global_observed_rate: float,
    prior_strength: float = 30.0,
) -> CohortCalibrationTarget:
    """Create a shrinkage target for challenger calibration diagnostics.

    This is not a production calibrator. Small cohorts borrow heavily from the
    global observed rate; large cohorts increasingly express their local rate.
    """
    if int(n) < 0:
        raise SharpnessChallengerError("MODEL_INPUTS_INSUFFICIENT: negative_cohort_n")
    predicted = _unit_interval(mean_predicted_probability, field="mean_predicted_probability")
    observed = _unit_interval(observed_rate, field="observed_rate")
    global_rate = _unit_interval(global_observed_rate, field="global_observed_rate")
    try:
        strength = float(prior_strength)
    except (TypeError, ValueError) as exc:
        raise SharpnessChallengerError("MODEL_INPUTS_INSUFFICIENT: prior_strength_non_numeric") from exc
    if not isfinite(strength) or strength < 0.0:
        raise SharpnessChallengerError("MODEL_INPUTS_INSUFFICIENT: prior_strength_out_of_range")
    local_weight = (float(n) / (float(n) + strength)) if (n or strength) else 0.0
    target = local_weight * observed + (1.0 - local_weight) * global_rate
    return CohortCalibrationTarget(
        n=int(n),
        mean_predicted_probability=predicted,
        observed_rate=observed,
        global_observed_rate=global_rate,
        prior_strength=strength,
        local_weight=local_weight,
        shrunk_observed_target=target,
    )


def _brier(probabilities: Sequence[float], outcomes: Sequence[int]) -> float:
    return sum((p - y) ** 2 for p, y in zip(probabilities, outcomes)) / len(outcomes)


def _log_loss(probabilities: Sequence[float], outcomes: Sequence[int]) -> float:
    eps = 1e-12
    total = 0.0
    for p, y in zip(probabilities, outcomes):
        clipped = min(max(p, eps), 1.0 - eps)
        total += -(y * log(clipped) + (1 - y) * log(1.0 - clipped))
    return total / len(outcomes)


def evaluate_ranking_strategy(
    rows: Sequence[Mapping[str, Any]],
    *,
    strategy: str,
    top_n: int = 3,
    lambda_penalty: float = 0.5,
) -> RankingStrategyMetrics:
    """Evaluate a ranking strategy across historical slates.

    Required row fields: slate_id, candidate_id, calibrated_probability,
    calibrated_lower_bound, outcome (0/1). Optional hard_blockers and
    soft_uncertainties are honored by the shadow governance classifier.
    """
    if int(top_n) < 1:
        raise SharpnessChallengerError("MODEL_INPUTS_INSUFFICIENT: top_n_must_be_positive")
    grouped: dict[str, list[tuple[CandidateProbability, int]]] = {}
    for raw in rows:
        slate_id = str(raw.get("slate_id") or "").strip()
        candidate_id = str(raw.get("candidate_id") or "").strip()
        if not slate_id or not candidate_id:
            raise SharpnessChallengerError("MODEL_INPUTS_INSUFFICIENT: missing_slate_or_candidate_id")
        outcome_raw = raw.get("outcome")
        if outcome_raw not in (0, 1, False, True):
            raise SharpnessChallengerError("MODEL_INPUTS_INSUFFICIENT: outcome_must_be_binary")
        candidate = CandidateProbability(
            candidate_id=candidate_id,
            calibrated_probability=raw.get("calibrated_probability"),
            calibrated_lower_bound=raw.get("calibrated_lower_bound"),
            calibrated_upper_bound=raw.get("calibrated_upper_bound"),
            hard_blockers=tuple(raw.get("hard_blockers") or ()),
            soft_uncertainties=tuple(raw.get("soft_uncertainties") or ()),
        )
        grouped.setdefault(slate_id, []).append((candidate, int(bool(outcome_raw))))

    if not grouped:
        raise SharpnessChallengerError("MODEL_INPUTS_INSUFFICIENT: no_ranking_rows")

    selected_probabilities: list[float] = []
    selected_outcomes: list[int] = []
    top1_wins = 0
    slates_with_selection = 0
    outcome_lookup: dict[tuple[str, str], int] = {
        (slate_id, candidate.candidate_id): outcome
        for slate_id, entries in grouped.items()
        for candidate, outcome in entries
    }

    for slate_id, entries in grouped.items():
        ranked = rank_candidates(
            [candidate for candidate, _ in entries],
            strategy=strategy,
            lambda_penalty=lambda_penalty,
        )
        if not ranked:
            continue
        slates_with_selection += 1
        top1_wins += outcome_lookup[(slate_id, ranked[0].candidate_id)]
        for ranking in ranked[: int(top_n)]:
            selected_probabilities.append(ranking.calibrated_probability)
            selected_outcomes.append(outcome_lookup[(slate_id, ranking.candidate_id)])

    if not selected_outcomes:
        raise SharpnessChallengerError("INSUFFICIENT_OOS_EVIDENCE: no_eligible_selected_rows")

    return RankingStrategyMetrics(
        strategy=str(strategy).upper(),
        selected_rows=len(selected_outcomes),
        slates=slates_with_selection,
        top1_win_rate=top1_wins / slates_with_selection,
        selected_hit_rate=sum(selected_outcomes) / len(selected_outcomes),
        selected_brier=_brier(selected_probabilities, selected_outcomes),
        selected_log_loss=_log_loss(selected_probabilities, selected_outcomes),
    )


def challenger_manifest() -> dict[str, Any]:
    return {
        "challenger_version": CHALLENGER_VERSION,
        "serving_mode": SERVING_MODE,
        "automatic_promotion_allowed": AUTOMATIC_PROMOTION_ALLOWED,
        "production_ranking_mutation_allowed": PRODUCTION_RANKING_MUTATION_ALLOWED,
        "production_calibration_mutation_allowed": PRODUCTION_CALIBRATION_MUTATION_ALLOWED,
        "production_market_prior_mutation_allowed": PRODUCTION_MARKET_PRIOR_MUTATION_ALLOWED,
        "market_prior_weight": MARKET_PRIOR_WEIGHT,
        "can_execute": CAN_EXECUTE,
    }


__all__ = [
    "AUTOMATIC_PROMOTION_ALLOWED",
    "CAN_EXECUTE",
    "CHALLENGER_VERSION",
    "CandidateProbability",
    "CandidateRanking",
    "CohortCalibrationTarget",
    "HARD_BLOCK_CODES",
    "MARKET_PRIOR_WEIGHT",
    "MarketDivergenceDiagnostic",
    "PRODUCTION_CALIBRATION_MUTATION_ALLOWED",
    "PRODUCTION_MARKET_PRIOR_MUTATION_ALLOWED",
    "PRODUCTION_RANKING_MUTATION_ALLOWED",
    "RankingStrategyMetrics",
    "SERVING_MODE",
    "SOFT_UNCERTAINTY_CODES",
    "SensitivityEffect",
    "SharpnessChallengerError",
    "UncertaintyConsumption",
    "challenger_manifest",
    "evaluate_ranking_strategy",
    "governance_classification",
    "hierarchical_cohort_target",
    "market_divergence_diagnostic",
    "rank_candidates",
    "score_candidate",
    "sensitivity_attribution",
    "uncertainty_consumption_audit",
]
