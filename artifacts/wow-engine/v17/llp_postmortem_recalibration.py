"""V17 LLP postmortem learning and recalibration diagnostics.

This module learns from immutable pregame team/event probability records without
mutating the sporting model, rewriting settlements, or allowing market evidence
to masquerade as governed probability.

It is deliberately diagnostic: it may recommend calibration review after enough
out-of-sample evidence accumulates, but it never changes model coefficients,
calibrated probabilities, lower bounds, terminal labels, or execution state.
"""
from __future__ import annotations

from collections import Counter, defaultdict
from dataclasses import asdict, dataclass
from math import isfinite, log
from statistics import mean
from typing import Any, Iterable, Mapping, Sequence

CAN_EXECUTE = False
EPSILON = 1e-12

WIN_RESULTS = {"WIN", "WON", "SETTLED_WIN"}
LOSS_RESULTS = {"LOSS", "LOST", "SETTLED_LOSS"}
PUSH_RESULTS = {"PUSH", "VOID", "REFUND", "TIE"}
FAVORITE_ROLES = {"FAVORITE", "WINNER", "FAVORITE_WINNER"}
UPSET_ROLES = {"UNDERDOG", "UPSET", "UNDERDOG_UPSET"}
FINAL_SNAPSHOT_STATES = {
    "FINAL",
    "FINAL_IMMUTABLE",
    "FINAL_IMMUTABLE_PREGAME",
    "PREGAME_FINAL",
    "STAGE20_FINAL",
}


def _norm(value: Any) -> str:
    return str(value or "").strip().upper()


def _number(value: Any) -> float | None:
    if isinstance(value, bool) or not isinstance(value, (int, float)):
        return None
    value = float(value)
    return value if isfinite(value) else None


def _prob(value: Any) -> float | None:
    value = _number(value)
    if value is None or not 0.0 <= value <= 1.0:
        return None
    return value


def _result_binary(value: Any) -> float | None:
    token = _norm(value)
    if token in WIN_RESULTS:
        return 1.0
    if token in LOSS_RESULTS:
        return 0.0
    if token in PUSH_RESULTS or not token or token == "UNKNOWN":
        return None
    return None


def _market_role(row: Mapping[str, Any]) -> str:
    token = _norm(row.get("market_role") or row.get("role") or row.get("lane"))
    if token in FAVORITE_ROLES:
        return "FAVORITE"
    if token in UPSET_ROLES:
        return "UPSET"
    return token or "UNKNOWN"


def _snapshot_stage(row: Mapping[str, Any]) -> str:
    return _norm(row.get("snapshot_stage") or row.get("prediction_stage"))


def _is_official_immutable_snapshot(row: Mapping[str, Any]) -> bool:
    explicit = row.get("immutable_pregame")
    if isinstance(explicit, bool):
        return explicit
    stage = _snapshot_stage(row)
    if stage:
        return stage in FINAL_SNAPSHOT_STATES
    # Stage-20 rows historically did not carry a snapshot-stage field. In that
    # legacy shape, a settled row with a model timestamp is treated as the
    # immutable pregame record supplied by the caller, not reconstructed here.
    return bool(str(row.get("model_timestamp") or row.get("created_at") or "").strip())


def _prediction_probability(row: Mapping[str, Any]) -> float | None:
    for key in ("calibrated_probability", "calibrated_point", "probability"):
        value = _prob(row.get(key))
        if value is not None:
            return value
    return None


def _lower_bound(row: Mapping[str, Any]) -> float | None:
    for key in ("lower_bound", "calibrated_probability_lower_bound", "calibrated_lower_bound"):
        value = _prob(row.get(key))
        if value is not None:
            return value
    return None


def _upper_bound(row: Mapping[str, Any]) -> float | None:
    for key in ("upper_bound", "calibrated_probability_upper_bound", "calibrated_upper_bound"):
        value = _prob(row.get(key))
        if value is not None:
            return value
    return None


def _tags(value: Any) -> tuple[str, ...]:
    if value is None:
        return ()
    if isinstance(value, str):
        raw = value.replace(";", ",").split(",")
    elif isinstance(value, Iterable) and not isinstance(value, (bytes, Mapping)):
        raw = list(value)
    else:
        raw = [value]
    return tuple(dict.fromkeys(_norm(item) for item in raw if _norm(item)))


def brier_score(probability: float, outcome: float) -> float:
    """Binary Brier score. Lower is better."""
    p = _prob(probability)
    if p is None or outcome not in (0.0, 1.0):
        raise ValueError("INVALID_BRIER_INPUT")
    return (p - outcome) ** 2


def log_loss(probability: float, outcome: float) -> float:
    """Binary log loss with clipping only for numerical stability.

    The clipped value is never written back to the underlying prediction.
    """
    p = _prob(probability)
    if p is None or outcome not in (0.0, 1.0):
        raise ValueError("INVALID_LOG_LOSS_INPUT")
    safe = min(max(p, EPSILON), 1.0 - EPSILON)
    return -(outcome * log(safe) + (1.0 - outcome) * log(1.0 - safe))


@dataclass(frozen=True)
class LearningThresholds:
    """Review thresholds are analytical configuration, not probability haircuts."""

    min_sample_size: int = 30
    bin_width: float = 0.05
    max_abs_calibration_bias: float | None = None
    max_ece: float | None = None
    max_mean_brier: float | None = None
    max_lower_bound_overstatement: float | None = None

    def validate(self) -> None:
        if isinstance(self.min_sample_size, bool) or self.min_sample_size < 1:
            raise ValueError("INVALID_MIN_SAMPLE_SIZE")
        if not 0.0 < float(self.bin_width) <= 1.0:
            raise ValueError("INVALID_BIN_WIDTH")
        for value in (
            self.max_abs_calibration_bias,
            self.max_ece,
            self.max_mean_brier,
            self.max_lower_bound_overstatement,
        ):
            if value is not None and (not isfinite(float(value)) or float(value) < 0.0):
                raise ValueError("INVALID_REVIEW_THRESHOLD")


@dataclass(frozen=True)
class ScoredPrediction:
    prediction_id: str
    date: str
    sport: str
    league: str
    event_id: str
    market_role: str
    selection: str
    calibrated_probability: float
    lower_bound: float | None
    upper_bound: float | None
    market_prior_weight: float | None
    independent_probability: float | None
    official_result: str
    binary_outcome: float
    brier_score: float
    log_loss: float
    interval_width: float | None
    market_dependent_model: bool
    predicted_failure_tags: tuple[str, ...]
    realized_failure_tags: tuple[str, ...]
    final_refresh_status: str
    model_timestamp: str
    can_execute: bool = CAN_EXECUTE

    def as_dict(self) -> dict[str, Any]:
        return asdict(self)


def score_prediction(row: Mapping[str, Any]) -> ScoredPrediction | None:
    """Score one immutable settled prediction, or return None when ungradeable."""
    if not _is_official_immutable_snapshot(row):
        return None
    outcome = _result_binary(row.get("result") or row.get("official_result"))
    probability = _prediction_probability(row)
    if outcome is None or probability is None:
        return None
    lower = _lower_bound(row)
    upper = _upper_bound(row)
    if lower is not None and lower > probability:
        raise ValueError("LOWER_BOUND_ABOVE_CALIBRATED_PROBABILITY")
    if upper is not None and upper < probability:
        raise ValueError("UPPER_BOUND_BELOW_CALIBRATED_PROBABILITY")
    market_weight = _prob(row.get("market_prior_weight"))
    independent = _prob(row.get("independent_probability"))
    prediction_id = str(row.get("prediction_id") or row.get("candidate_id") or "").strip()
    if not prediction_id:
        raise ValueError("PREDICTION_ID_REQUIRED")
    width = upper - lower if upper is not None and lower is not None else None
    return ScoredPrediction(
        prediction_id=prediction_id,
        date=str(row.get("date") or "").strip(),
        sport=_norm(row.get("sport")),
        league=_norm(row.get("league")),
        event_id=str(row.get("event_id") or row.get("event_key") or "").strip(),
        market_role=_market_role(row),
        selection=str(row.get("selection") or row.get("team") or "").strip(),
        calibrated_probability=probability,
        lower_bound=lower,
        upper_bound=upper,
        market_prior_weight=market_weight,
        independent_probability=independent,
        official_result=_norm(row.get("result") or row.get("official_result")),
        binary_outcome=outcome,
        brier_score=brier_score(probability, outcome),
        log_loss=log_loss(probability, outcome),
        interval_width=width,
        market_dependent_model=bool(market_weight is not None and market_weight > 0.50),
        predicted_failure_tags=_tags(row.get("failure_tags") or row.get("predicted_failure_tags") or row.get("main_failure_path")),
        realized_failure_tags=_tags(row.get("realized_failure_tags") or row.get("observed_path") or row.get("realized_failure_path")),
        final_refresh_status=_norm(row.get("final_refresh_status") or row.get("final_refresh")),
        model_timestamp=str(row.get("model_timestamp") or row.get("created_at") or "").strip(),
        can_execute=False,
    )


def _bucket_index(probability: float, width: float) -> int:
    return min(int(probability / width), max(int(1.0 / width) - 1, 0))


def _bucket_label(index: int, width: float) -> str:
    low = index * width
    high = min(1.0, low + width)
    return f"[{low:.3f},{high:.3f}{']' if high >= 1.0 else ')'}"


def _reliability_rows(rows: Sequence[ScoredPrediction], width: float) -> tuple[list[dict[str, Any]], float]:
    buckets: dict[int, list[ScoredPrediction]] = defaultdict(list)
    for row in rows:
        buckets[_bucket_index(row.calibrated_probability, width)].append(row)
    output: list[dict[str, Any]] = []
    total = len(rows)
    ece = 0.0
    for index in sorted(buckets):
        bucket = buckets[index]
        forecast = mean(row.calibrated_probability for row in bucket)
        observed = mean(row.binary_outcome for row in bucket)
        gap = forecast - observed
        ece += len(bucket) / total * abs(gap)
        output.append(
            {
                "bucket": _bucket_label(index, width),
                "n": len(bucket),
                "mean_forecast": forecast,
                "observed_win_rate": observed,
                "calibration_bias": gap,
            }
        )
    return output, ece


def _review_status(
    *,
    n: int,
    calibration_bias: float,
    ece: float,
    mean_brier: float,
    lower_bound_overstatement: float | None,
    config: LearningThresholds,
) -> tuple[str, tuple[str, ...]]:
    if n < config.min_sample_size:
        return "INSUFFICIENT_SAMPLE", (f"N_{n}_LT_MIN_{config.min_sample_size}",)

    checks: list[tuple[str, float, float | None]] = [
        ("ABS_CALIBRATION_BIAS", abs(calibration_bias), config.max_abs_calibration_bias),
        ("ECE", ece, config.max_ece),
        ("MEAN_BRIER", mean_brier, config.max_mean_brier),
    ]
    if lower_bound_overstatement is not None:
        checks.append(("LOWER_BOUND_OVERSTATEMENT", lower_bound_overstatement, config.max_lower_bound_overstatement))
    active = [(name, value, threshold) for name, value, threshold in checks if threshold is not None]
    if not active:
        return "DIAGNOSTIC_ONLY_NO_RECALIBRATION_THRESHOLDS", ()
    breaches = tuple(name for name, value, threshold in active if threshold is not None and value > threshold)
    if breaches:
        return "RECALIBRATION_REVIEW_RECOMMENDED", breaches
    return "PRESERVE_CURRENT_CALIBRATION", ()


def calibration_report(
    predictions: Iterable[Mapping[str, Any]],
    *,
    config: LearningThresholds | None = None,
) -> dict[str, Any]:
    """Produce lane- and sport-specific calibration diagnostics.

    Favorites and upsets are never pooled into one calibration curve by default.
    """
    config = config or LearningThresholds()
    config.validate()
    scored = [row for item in predictions if (row := score_prediction(item)) is not None]
    groups: dict[tuple[str, str], list[ScoredPrediction]] = defaultdict(list)
    for row in scored:
        groups[(row.sport or "UNKNOWN", row.market_role or "UNKNOWN")].append(row)

    reports: list[dict[str, Any]] = []
    for (sport, role), rows in sorted(groups.items()):
        observed = mean(row.binary_outcome for row in rows)
        forecast = mean(row.calibrated_probability for row in rows)
        bias = forecast - observed
        reliability, ece = _reliability_rows(rows, float(config.bin_width))
        brier = mean(row.brier_score for row in rows)
        losses = mean(row.log_loss for row in rows)
        lower_rows = [row for row in rows if row.lower_bound is not None]
        lower_mean = mean(row.lower_bound for row in lower_rows) if lower_rows else None
        lower_gap = observed - lower_mean if lower_mean is not None else None
        lower_overstatement = max(0.0, -lower_gap) if lower_gap is not None else None
        widths = [row.interval_width for row in rows if row.interval_width is not None]
        status, reasons = _review_status(
            n=len(rows),
            calibration_bias=bias,
            ece=ece,
            mean_brier=brier,
            lower_bound_overstatement=lower_overstatement,
            config=config,
        )
        reports.append(
            {
                "sport": sport,
                "market_role": role,
                "n": len(rows),
                "observed_win_rate": observed,
                "mean_calibrated_probability": forecast,
                "calibration_bias": bias,
                "ece": ece,
                "mean_brier_score": brier,
                "mean_log_loss": losses,
                "mean_lower_bound": lower_mean,
                "lower_bound_empirical_gap": lower_gap,
                "mean_interval_width": mean(widths) if widths else None,
                "reliability_buckets": reliability,
                "review_status": status,
                "review_reasons": list(reasons),
                "automatic_parameter_mutation": False,
                "can_execute": False,
            }
        )
    return {
        "rows_received": len(list(predictions)) if isinstance(predictions, Sequence) else None,
        "rows_scored": len(scored),
        "groups": reports,
        "automatic_parameter_mutation": False,
        "can_execute": False,
    }


def market_prior_diagnostics(predictions: Iterable[Mapping[str, Any]]) -> dict[str, Any]:
    scored = [row for item in predictions if (row := score_prediction(item)) is not None]
    bins: dict[str, list[ScoredPrediction]] = defaultdict(list)
    flagged: list[str] = []
    for row in scored:
        weight = row.market_prior_weight
        if weight is None:
            label = "UNKNOWN"
        elif weight <= 0.25:
            label = "LE_0_25"
        elif weight <= 0.50:
            label = "GT_0_25_LE_0_50"
        else:
            label = "GT_0_50_MARKET_DEPENDENT"
            flagged.append(row.prediction_id)
        bins[label].append(row)
    summaries = []
    for label, rows in sorted(bins.items()):
        summaries.append(
            {
                "market_prior_band": label,
                "n": len(rows),
                "observed_win_rate": mean(row.binary_outcome for row in rows),
                "mean_calibrated_probability": mean(row.calibrated_probability for row in rows),
                "mean_brier_score": mean(row.brier_score for row in rows),
            }
        )
    return {
        "bands": summaries,
        "market_dependent_prediction_ids": flagged,
        "market_dependency_threshold": 0.50,
        "probability_fields_mutated": False,
        "can_execute": False,
    }


def failure_path_diagnostics(predictions: Iterable[Mapping[str, Any]]) -> dict[str, Any]:
    scored = [row for item in predictions if (row := score_prediction(item)) is not None]
    losses = [row for row in scored if row.binary_outcome == 0.0]
    predicted = Counter(tag for row in losses for tag in row.predicted_failure_tags)
    realized = Counter(tag for row in losses for tag in row.realized_failure_tags)
    matched = 0
    attributable = 0
    miss_rows: list[str] = []
    for row in losses:
        if not row.realized_failure_tags:
            continue
        attributable += 1
        if set(row.predicted_failure_tags).intersection(row.realized_failure_tags):
            matched += 1
        else:
            miss_rows.append(row.prediction_id)
    return {
        "losses": len(losses),
        "losses_with_realized_path": attributable,
        "predicted_failure_tag_counts": dict(predicted),
        "realized_failure_tag_counts": dict(realized),
        "realized_path_match_rate": matched / attributable if attributable else None,
        "unanticipated_failure_prediction_ids": miss_rows,
        "probability_fields_mutated": False,
        "can_execute": False,
    }


def ranking_regret_diagnostics(predictions: Iterable[Mapping[str, Any]]) -> dict[str, Any]:
    """Measure lower-bound rank inversions using only immutable pregame ranks."""
    scored = [row for item in predictions if (row := score_prediction(item)) is not None and row.lower_bound is not None]
    slates: dict[tuple[str, str, str], list[ScoredPrediction]] = defaultdict(list)
    for row in scored:
        slate = (row.date or "UNKNOWN", row.sport or "UNKNOWN", row.market_role or "UNKNOWN")
        slates[slate].append(row)
    inversions: list[dict[str, Any]] = []
    top_rank_losses = 0
    for (date, sport, role), rows in sorted(slates.items()):
        if len(rows) < 2:
            continue
        ordered = sorted(rows, key=lambda row: row.lower_bound if row.lower_bound is not None else -1.0, reverse=True)
        top = ordered[0]
        if top.binary_outcome == 0.0:
            top_rank_losses += 1
            lower_winners = [row for row in ordered[1:] if row.binary_outcome == 1.0]
            if lower_winners:
                best_lower_winner = lower_winners[0]
                inversions.append(
                    {
                        "date": date,
                        "sport": sport,
                        "market_role": role,
                        "top_rank_prediction_id": top.prediction_id,
                        "top_rank_lower_bound": top.lower_bound,
                        "lower_rank_winner_prediction_id": best_lower_winner.prediction_id,
                        "lower_rank_winner_lower_bound": best_lower_winner.lower_bound,
                        "historical_rank_rewritten": False,
                    }
                )
    return {
        "slates_evaluated": sum(1 for rows in slates.values() if len(rows) >= 2),
        "top_rank_losses": top_rank_losses,
        "rank_inversions": inversions,
        "historical_rank_rewritten": False,
        "can_execute": False,
    }


def final_refresh_diagnostics(predictions: Iterable[Mapping[str, Any]]) -> dict[str, Any]:
    """Compare diagnostic pre-refresh snapshots with the immutable final snapshot.

    Only final immutable pregame rows are eligible for official Brier/log-loss grading.
    Earlier snapshots are used solely to determine whether refresh moved the forecast
    closer to or farther from the eventually observed outcome.
    """
    rows = list(predictions)
    groups: dict[str, list[Mapping[str, Any]]] = defaultdict(list)
    for row in rows:
        key = str(row.get("prediction_id") or row.get("candidate_id") or "").strip()
        if key:
            groups[key].append(row)
    comparisons: list[dict[str, Any]] = []
    improved = worsened = unchanged = 0
    for prediction_id, snapshots in groups.items():
        final_rows = [row for row in snapshots if _is_official_immutable_snapshot(row)]
        prior_rows = [row for row in snapshots if not _is_official_immutable_snapshot(row)]
        if len(final_rows) != 1 or not prior_rows:
            continue
        final = final_rows[0]
        outcome = _result_binary(final.get("result") or final.get("official_result"))
        final_p = _prediction_probability(final)
        if outcome is None or final_p is None:
            continue
        prior = prior_rows[-1]
        prior_p = _prediction_probability(prior)
        if prior_p is None:
            continue
        before_error = abs(prior_p - outcome)
        after_error = abs(final_p - outcome)
        delta = before_error - after_error
        if delta > 1e-12:
            status = "IMPROVED"
            improved += 1
        elif delta < -1e-12:
            status = "WORSENED"
            worsened += 1
        else:
            status = "UNCHANGED"
            unchanged += 1
        comparisons.append(
            {
                "prediction_id": prediction_id,
                "pre_refresh_probability": prior_p,
                "final_immutable_probability": final_p,
                "absolute_error_improvement": delta,
                "refresh_effect": status,
                "official_grade_uses_final_snapshot_only": True,
            }
        )
    return {
        "comparisons": comparisons,
        "improved": improved,
        "worsened": worsened,
        "unchanged": unchanged,
        "official_grade_uses_final_snapshot_only": True,
        "can_execute": False,
    }


def build_llp_learning_report(
    predictions: Iterable[Mapping[str, Any]],
    *,
    config: LearningThresholds | None = None,
) -> dict[str, Any]:
    """Build the complete non-mutating LLP learning report."""
    rows = list(predictions)
    before = [dict(row) for row in rows]
    calibration = calibration_report(rows, config=config)
    market_prior = market_prior_diagnostics(rows)
    failure_paths = failure_path_diagnostics(rows)
    ranking_regret = ranking_regret_diagnostics(rows)
    refresh = final_refresh_diagnostics(rows)
    after = [dict(row) for row in rows]
    return {
        "calibration": calibration,
        "market_prior": market_prior,
        "failure_paths": failure_paths,
        "ranking_regret": ranking_regret,
        "final_refresh": refresh,
        "probability_or_prediction_rows_mutated": before != after,
        "automatic_parameter_mutation": False,
        "terminal_authority": "V17_TERMINAL_REDUCER",
        "can_execute": False,
    }


__all__ = [
    "CAN_EXECUTE",
    "LearningThresholds",
    "ScoredPrediction",
    "brier_score",
    "build_llp_learning_report",
    "calibration_report",
    "failure_path_diagnostics",
    "final_refresh_diagnostics",
    "log_loss",
    "market_prior_diagnostics",
    "ranking_regret_diagnostics",
    "score_prediction",
]
