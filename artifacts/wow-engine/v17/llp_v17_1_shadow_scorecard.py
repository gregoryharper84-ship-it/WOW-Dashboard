"""Read-only evaluation for persistent LLP V17.1 shadow ranking evidence.

This module compares the same immutable governed predictions under multiple
ranking objectives. It never changes a probability, model, calibration object,
market prior, production rank, terminal status, or execution posture.
"""
from __future__ import annotations

from dataclasses import asdict, dataclass
from math import isfinite, log
from typing import Any, Mapping, Sequence

CAN_EXECUTE = False
SERVING_MODE = "SHADOW_EVALUATION_ONLY"
AUTOMATIC_PROMOTION_ALLOWED = False
PRODUCTION_MUTATION_ALLOWED = False
BASELINE_LAMBDA = 1.0
EPSILON = 1e-12


class ShadowScorecardError(ValueError):
    pass


@dataclass(frozen=True)
class RankedSelection:
    slate_id: str
    lambda_penalty: float
    rank: int
    official_event_id: str
    selection: str
    calibrated_probability: float
    calibrated_lower_bound: float
    ranking_score: float
    outcome_target: int
    sport: str
    league: str | None
    market_role: str | None
    governance_class: str | None
    lower_bound_width: float

    @property
    def thesis_key(self) -> str:
        return f"{self.official_event_id}::{self.selection}"


@dataclass(frozen=True)
class LambdaScorecard:
    lambda_penalty: float
    strategy_label: str
    slate_n: int
    event_n: int
    top1_win_rate: float
    top3_hit_rate: float
    top5_hit_rate: float
    top3_brier: float
    top3_log_loss: float
    top3_calibration_bias: float
    top3_ece: float
    top3_mean_probability: float
    top3_observed_rate: float
    top3_mean_lower_bound: float
    top3_lower_bound_margin: float
    can_execute: bool = CAN_EXECUTE

    def as_dict(self) -> dict[str, Any]:
        return asdict(self)


@dataclass(frozen=True)
class LambdaComparison:
    lambda_penalty: float
    baseline_lambda: float
    common_slate_n: int
    top1_selection_flips: int
    topn_order_changes: int
    baseline_correct_to_challenger_wrong: int
    baseline_wrong_to_challenger_correct: int
    automatic_promotion_allowed: bool = AUTOMATIC_PROMOTION_ALLOWED
    can_execute: bool = CAN_EXECUTE

    def as_dict(self) -> dict[str, Any]:
        return asdict(self)


def _number(value: Any, *, field: str) -> float:
    if isinstance(value, bool):
        raise ShadowScorecardError(f"INVALID_{field.upper()}:boolean")
    try:
        parsed = float(value)
    except (TypeError, ValueError) as exc:
        raise ShadowScorecardError(f"INVALID_{field.upper()}:non_numeric") from exc
    if not isfinite(parsed):
        raise ShadowScorecardError(f"INVALID_{field.upper()}:non_finite")
    return parsed


def _probability(value: Any, *, field: str) -> float:
    parsed = _number(value, field=field)
    if not 0.0 <= parsed <= 1.0:
        raise ShadowScorecardError(f"INVALID_{field.upper()}:out_of_range")
    return parsed


def _binary(value: Any) -> int:
    if value in (0, False):
        return 0
    if value in (1, True):
        return 1
    raise ShadowScorecardError("INVALID_OUTCOME_TARGET")


def _text(value: Any) -> str | None:
    token = str(value or "").strip()
    return token or None


def strategy_label(lambda_penalty: float) -> str:
    lam = float(lambda_penalty)
    if abs(lam) <= 1e-12:
        return "CALIBRATED_PROBABILITY"
    if abs(lam - 1.0) <= 1e-12:
        return "CALIBRATED_LOWER_BOUND"
    return "UNCERTAINTY_ADJUSTED"


def _slate_id(row: Mapping[str, Any]) -> str | None:
    return (
        _text(row.get("requested_slate_date"))
        or _text(row.get("research_run_id"))
        or _text(row.get("slate_id"))
    )


def _width_band(width: float) -> str:
    if width < 0.05:
        return "WIDTH_LT_5PP"
    if width < 0.10:
        return "WIDTH_5_TO_10PP"
    if width < 0.15:
        return "WIDTH_10_TO_15PP"
    return "WIDTH_GE_15PP"


def _unique_event_count(rows: Sequence[Mapping[str, Any]]) -> int:
    keys: set[tuple[str, str]] = set()
    for row in rows:
        slate = _slate_id(row)
        event = _text(row.get("official_event_id"))
        if slate and event:
            keys.add((slate, event))
    return len(keys)


def select_ranked_rows(rows: Sequence[Mapping[str, Any]], *, lambda_penalty: float) -> list[RankedSelection]:
    """Select at most one side per event, then rank events within each slate."""
    lam = _number(lambda_penalty, field="lambda_penalty")
    by_slate_event: dict[tuple[str, str], list[Mapping[str, Any]]] = {}
    for row in rows:
        try:
            row_lambda = _number(row.get("lambda_penalty"), field="lambda_penalty")
        except ShadowScorecardError:
            continue
        if abs(row_lambda - lam) > 1e-9:
            continue
        if row.get("rank_eligible_shadow") is not True:
            continue
        slate_id = _slate_id(row)
        event_id = _text(row.get("official_event_id"))
        selection = _text(row.get("selection"))
        if slate_id is None or event_id is None or selection is None:
            continue
        by_slate_event.setdefault((slate_id, event_id), []).append(row)

    event_winners: dict[str, list[Mapping[str, Any]]] = {}
    for (slate_id, _), candidates in by_slate_event.items():
        best = max(
            candidates,
            key=lambda row: (
                _number(row.get("uncertainty_adjusted_score"), field="ranking_score"),
                _probability(row.get("calibrated_probability"), field="calibrated_probability"),
                _text(row.get("selection")) or "",
            ),
        )
        event_winners.setdefault(slate_id, []).append(best)

    ranked: list[RankedSelection] = []
    for slate_id, candidates in event_winners.items():
        ordered = sorted(
            candidates,
            key=lambda row: (
                _number(row.get("uncertainty_adjusted_score"), field="ranking_score"),
                _probability(row.get("calibrated_probability"), field="calibrated_probability"),
            ),
            reverse=True,
        )
        for index, row in enumerate(ordered, start=1):
            p = _probability(row.get("calibrated_probability"), field="calibrated_probability")
            lb = _probability(row.get("calibrated_lower_bound"), field="calibrated_lower_bound")
            width = _number(row.get("lower_bound_width", p - lb), field="lower_bound_width")
            ranked.append(
                RankedSelection(
                    slate_id=slate_id,
                    lambda_penalty=lam,
                    rank=index,
                    official_event_id=str(row.get("official_event_id")),
                    selection=str(row.get("selection")),
                    calibrated_probability=p,
                    calibrated_lower_bound=lb,
                    ranking_score=_number(row.get("uncertainty_adjusted_score"), field="ranking_score"),
                    outcome_target=_binary(row.get("outcome_target")),
                    sport=str(row.get("sport") or "UNKNOWN").upper(),
                    league=_text(row.get("league")),
                    market_role=_text(row.get("market_role")),
                    governance_class=_text(row.get("governance_class")),
                    lower_bound_width=width,
                )
            )
    return ranked


def _mean(values: Sequence[float]) -> float:
    if not values:
        raise ShadowScorecardError("EMPTY_METRIC_SAMPLE")
    return sum(values) / len(values)


def _brier(rows: Sequence[RankedSelection]) -> float:
    return _mean([(row.calibrated_probability - row.outcome_target) ** 2 for row in rows])


def _log_loss(rows: Sequence[RankedSelection]) -> float:
    losses: list[float] = []
    for row in rows:
        p = min(max(row.calibrated_probability, EPSILON), 1.0 - EPSILON)
        y = row.outcome_target
        losses.append(-(y * log(p) + (1 - y) * log(1.0 - p)))
    return _mean(losses)


def expected_calibration_error(rows: Sequence[RankedSelection], *, bins: int = 10) -> float:
    if bins < 2:
        raise ShadowScorecardError("INVALID_ECE_BINS")
    if not rows:
        raise ShadowScorecardError("EMPTY_METRIC_SAMPLE")
    bucketed: dict[int, list[RankedSelection]] = {}
    for row in rows:
        index = min(int(row.calibrated_probability * bins), bins - 1)
        bucketed.setdefault(index, []).append(row)
    total = len(rows)
    ece = 0.0
    for members in bucketed.values():
        mean_p = _mean([row.calibrated_probability for row in members])
        hit_rate = _mean([float(row.outcome_target) for row in members])
        ece += len(members) / total * abs(hit_rate - mean_p)
    return ece


def evaluate_lambda(rows: Sequence[Mapping[str, Any]], *, lambda_penalty: float) -> LambdaScorecard:
    ranked = select_ranked_rows(rows, lambda_penalty=lambda_penalty)
    if not ranked:
        raise ShadowScorecardError("NO_ELIGIBLE_RANKED_ROWS")
    by_slate: dict[str, list[RankedSelection]] = {}
    for row in ranked:
        by_slate.setdefault(row.slate_id, []).append(row)
    for members in by_slate.values():
        members.sort(key=lambda row: row.rank)

    top1 = [members[0] for members in by_slate.values() if members]
    top3 = [row for members in by_slate.values() for row in members[:3]]
    top5 = [row for members in by_slate.values() for row in members[:5]]
    mean_p = _mean([row.calibrated_probability for row in top3])
    observed = _mean([float(row.outcome_target) for row in top3])
    mean_lb = _mean([row.calibrated_lower_bound for row in top3])
    return LambdaScorecard(
        lambda_penalty=float(lambda_penalty),
        strategy_label=strategy_label(float(lambda_penalty)),
        slate_n=len(by_slate),
        event_n=len(ranked),
        top1_win_rate=_mean([float(row.outcome_target) for row in top1]),
        top3_hit_rate=observed,
        top5_hit_rate=_mean([float(row.outcome_target) for row in top5]),
        top3_brier=_brier(top3),
        top3_log_loss=_log_loss(top3),
        top3_calibration_bias=observed - mean_p,
        top3_ece=expected_calibration_error(top3),
        top3_mean_probability=mean_p,
        top3_observed_rate=observed,
        top3_mean_lower_bound=mean_lb,
        top3_lower_bound_margin=observed - mean_lb,
    )


def compare_to_baseline(
    rows: Sequence[Mapping[str, Any]],
    *,
    lambda_penalty: float,
    baseline_lambda: float = BASELINE_LAMBDA,
    top_n: int = 5,
) -> LambdaComparison:
    if top_n < 1:
        raise ShadowScorecardError("INVALID_TOP_N")
    challenger = select_ranked_rows(rows, lambda_penalty=lambda_penalty)
    baseline = select_ranked_rows(rows, lambda_penalty=baseline_lambda)
    chal_by_slate: dict[str, list[RankedSelection]] = {}
    base_by_slate: dict[str, list[RankedSelection]] = {}
    for row in challenger:
        chal_by_slate.setdefault(row.slate_id, []).append(row)
    for row in baseline:
        base_by_slate.setdefault(row.slate_id, []).append(row)
    common = sorted(set(chal_by_slate).intersection(base_by_slate))
    top1_flips = 0
    order_changes = 0
    corrected = 0
    degraded = 0
    for slate_id in common:
        chal = sorted(chal_by_slate[slate_id], key=lambda row: row.rank)
        base = sorted(base_by_slate[slate_id], key=lambda row: row.rank)
        if not chal or not base:
            continue
        if chal[0].thesis_key != base[0].thesis_key:
            top1_flips += 1
            if base[0].outcome_target == 1 and chal[0].outcome_target == 0:
                degraded += 1
            elif base[0].outcome_target == 0 and chal[0].outcome_target == 1:
                corrected += 1
        if [row.thesis_key for row in chal[:top_n]] != [row.thesis_key for row in base[:top_n]]:
            order_changes += 1
    return LambdaComparison(
        lambda_penalty=float(lambda_penalty),
        baseline_lambda=float(baseline_lambda),
        common_slate_n=len(common),
        top1_selection_flips=top1_flips,
        topn_order_changes=order_changes,
        baseline_correct_to_challenger_wrong=degraded,
        baseline_wrong_to_challenger_correct=corrected,
    )


def _cohort_payload(rows: Sequence[Mapping[str, Any]], lambdas: Sequence[float]) -> dict[str, Any]:
    scorecards: list[dict[str, Any]] = []
    comparisons: list[dict[str, Any]] = []
    for lam in lambdas:
        try:
            scorecards.append(evaluate_lambda(rows, lambda_penalty=float(lam)).as_dict())
            if abs(float(lam) - BASELINE_LAMBDA) > 1e-12:
                comparisons.append(compare_to_baseline(rows, lambda_penalty=float(lam)).as_dict())
        except ShadowScorecardError:
            continue
    return {"scorecards": scorecards, "comparisons_to_lower_bound_baseline": comparisons}


def _supported_cohorts(
    grouped: Mapping[str, list[Mapping[str, Any]]],
    *,
    lambdas: Sequence[float],
    min_cohort_events: int,
) -> dict[str, Any]:
    out: dict[str, Any] = {}
    for key, members in sorted(grouped.items()):
        event_n = _unique_event_count(members)
        if event_n < min_cohort_events:
            continue
        out[key] = {
            "raw_row_n": len(members),
            "unique_slate_event_n": event_n,
            **_cohort_payload(members, lambdas),
        }
    return out


def evaluate_shadow_rankings(
    rows: Sequence[Mapping[str, Any]],
    *,
    lambdas: Sequence[float] | None = None,
    min_cohort_events: int = 30,
) -> dict[str, Any]:
    """Evaluate overall and supported cohorts using independent event counts.

    Cohort eligibility is based on unique (slate,event) observations, never raw
    row count, so two sides times multiple lambda copies cannot inflate n.
    """
    if min_cohort_events < 1:
        raise ShadowScorecardError("INVALID_MIN_COHORT_EVENTS")
    if not rows:
        return {
            "status": "NO_GRADED_SHADOW_ROWS",
            "serving_mode": SERVING_MODE,
            "automatic_promotion_allowed": False,
            "production_mutation_allowed": False,
            "can_execute": False,
            "overall": {"scorecards": [], "comparisons_to_lower_bound_baseline": []},
            "by_sport": {},
            "by_league": {},
            "by_market_role": {},
            "by_governance_class": {},
            "by_uncertainty_width": {},
        }

    resolved_lambdas = sorted(
        {float(value) for value in (lambdas or [row.get("lambda_penalty") for row in rows if row.get("lambda_penalty") is not None])}
    )
    overall = _cohort_payload(rows, resolved_lambdas)

    by_sport_rows: dict[str, list[Mapping[str, Any]]] = {}
    by_league_rows: dict[str, list[Mapping[str, Any]]] = {}
    by_market_role_rows: dict[str, list[Mapping[str, Any]]] = {}
    by_governance_rows: dict[str, list[Mapping[str, Any]]] = {}
    by_width_rows: dict[str, list[Mapping[str, Any]]] = {}
    for row in rows:
        sport = str(row.get("sport") or "UNKNOWN").upper()
        league = str(row.get("league") or "UNKNOWN").upper()
        market_role = str(row.get("market_role") or "UNKNOWN").upper()
        governance = str(row.get("governance_class") or "UNKNOWN").upper()
        try:
            p = _probability(row.get("calibrated_probability"), field="calibrated_probability")
            lb = _probability(row.get("calibrated_lower_bound"), field="calibrated_lower_bound")
            width = _number(row.get("lower_bound_width", p - lb), field="lower_bound_width")
            width_band = _width_band(width)
        except ShadowScorecardError:
            width_band = "WIDTH_UNKNOWN"
        by_sport_rows.setdefault(sport, []).append(row)
        by_league_rows.setdefault(league, []).append(row)
        by_market_role_rows.setdefault(market_role, []).append(row)
        by_governance_rows.setdefault(governance, []).append(row)
        by_width_rows.setdefault(width_band, []).append(row)

    return {
        "status": "PASS",
        "serving_mode": SERVING_MODE,
        "automatic_promotion_allowed": AUTOMATIC_PROMOTION_ALLOWED,
        "production_mutation_allowed": PRODUCTION_MUTATION_ALLOWED,
        "baseline_lambda": BASELINE_LAMBDA,
        "lambda_grid": resolved_lambdas,
        "graded_raw_row_n": len(rows),
        "graded_unique_slate_event_n": _unique_event_count(rows),
        "min_cohort_events": min_cohort_events,
        "overall": overall,
        "by_sport": _supported_cohorts(by_sport_rows, lambdas=resolved_lambdas, min_cohort_events=min_cohort_events),
        "by_league": _supported_cohorts(by_league_rows, lambdas=resolved_lambdas, min_cohort_events=min_cohort_events),
        "by_market_role": _supported_cohorts(by_market_role_rows, lambdas=resolved_lambdas, min_cohort_events=min_cohort_events),
        "by_governance_class": _supported_cohorts(by_governance_rows, lambdas=resolved_lambdas, min_cohort_events=min_cohort_events),
        "by_uncertainty_width": _supported_cohorts(by_width_rows, lambdas=resolved_lambdas, min_cohort_events=min_cohort_events),
        "can_execute": CAN_EXECUTE,
    }


__all__ = [
    "AUTOMATIC_PROMOTION_ALLOWED",
    "BASELINE_LAMBDA",
    "CAN_EXECUTE",
    "LambdaComparison",
    "LambdaScorecard",
    "PRODUCTION_MUTATION_ALLOWED",
    "RankedSelection",
    "SERVING_MODE",
    "ShadowScorecardError",
    "compare_to_baseline",
    "evaluate_lambda",
    "evaluate_shadow_rankings",
    "expected_calibration_error",
    "select_ranked_rows",
    "strategy_label",
]
