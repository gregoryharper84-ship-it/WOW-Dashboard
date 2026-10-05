"""Research-only V17 multilane lower-bound and eligibility experiment framework.

Issue #1391.

This module creates one semantic evaluation contract for governed lower bounds
without forcing moneylines, spreads, and player props to use the same
probability model or the same uncertainty estimator.

It is deliberately downstream of the controlling fitted specialist. It does
not fit sporting probabilities, does not ingest sportsbook implied
probability, does not mutate production calibration, cannot publish, cannot
promote, and cannot execute.

Lane-specific adapters remain responsible for sporting semantics such as exact
spread/prop line identity, role or starter state, push probability, settlement
rules, and final refresh. This framework fails closed unless those gates report
PASS before a research eligibility policy can admit a row.
"""
from __future__ import annotations

from dataclasses import dataclass
from datetime import datetime
from math import isfinite, log, sqrt
from typing import Any, Mapping, Sequence

EXPERIMENT_VERSION = "V17_MULTILANE_LOWER_BOUND_SEMANTICS_V1"
TERMINAL_AUTHORITY = "V17_TERMINAL_REDUCER"

CAN_EXECUTE = False
PROBABILITY_PUBLISHABLE = False
PROMOTION_AUTHORIZED = False
RANK_ELIGIBLE = False
MARKET_PROBABILITY_SUBSTITUTION_ALLOWED = False

SUPPORTED_FAMILIES = ("MONEYLINE", "SPREAD", "PLAYER_PROP")
OOD_STATES = ("IN_DISTRIBUTION", "NEAR_OOD", "OOD_BLOCKED")
PROBABILITY_SEMANTICS = (
    "BINARY_OUTCOME",
    "CONDITIONAL_ON_NO_PUSH",
    "UNCONDITIONAL_WITH_PUSH_MASS",
)

REQUIRED_LANE_EVIDENCE: dict[str, tuple[str, ...]] = {
    "MONEYLINE": (
        "pregame_identity",
        "controlling_specialist_certification",
        "calibration_certification",
        "ood_support",
        "material_status_refresh",
        "immutable_prediction_identity",
    ),
    "SPREAD": (
        "pregame_identity",
        "controlling_specialist_certification",
        "calibration_certification",
        "exact_line_identity",
        "margin_distribution_support",
        "push_semantics",
        "line_freshness",
        "material_line_change_rescore",
        "ood_support",
        "material_status_refresh",
        "immutable_prediction_identity",
    ),
    "PLAYER_PROP": (
        "pregame_identity",
        "controlling_specialist_certification",
        "calibration_certification",
        "player_identity",
        "stat_family_identity",
        "exact_line_identity",
        "distribution_support",
        "role_status",
        "push_semantics",
        "ood_support",
        "material_status_refresh",
        "immutable_prediction_identity",
    ),
}


class LowerBoundExperimentError(ValueError):
    """Typed fail-closed error for research evaluation input defects."""


@dataclass(frozen=True)
class ResearchEligibilityPolicy:
    """A challenger-only admission policy.

    None means the dimension is not used by that policy. The framework does
    not prescribe universal production thresholds; callers must pre-register
    lane-specific values before replaying outcomes.
    """

    name: str
    min_point_probability: float | None = None
    min_lower_bound: float | None = None
    max_interval_width: float | None = None
    min_support_n: int | None = None
    allow_near_ood: bool = False


@dataclass(frozen=True)
class LocalReliabilityBound:
    lane_key: str
    point_probability: float
    lower_bound: float
    successes: int
    sample_n: int
    max_probability_distance_used: float
    max_support_distance_used: float | None
    wilson_z: float
    included_ood_states: tuple[str, ...]


def _aware_datetime(value: Any, *, field: str) -> datetime:
    try:
        parsed = datetime.fromisoformat(str(value or "").strip().replace("Z", "+00:00"))
    except (TypeError, ValueError) as exc:
        raise LowerBoundExperimentError(f"LOWER_BOUND_{field}_TIMESTAMP_INVALID") from exc
    if parsed.tzinfo is None or parsed.utcoffset() is None:
        raise LowerBoundExperimentError(f"LOWER_BOUND_{field}_TIMESTAMP_INVALID")
    return parsed


def _probability(value: Any, *, field: str) -> float:
    try:
        parsed = float(value)
    except (TypeError, ValueError) as exc:
        raise LowerBoundExperimentError(f"MODEL_OUTPUT_INVALID:{field}_NON_NUMERIC") from exc
    if not isfinite(parsed) or not 0.0 <= parsed <= 1.0:
        raise LowerBoundExperimentError(f"MODEL_OUTPUT_INVALID:{field}_OUT_OF_RANGE")
    return parsed


def _family(value: Any) -> str:
    family = str(value or "").strip().upper()
    if family not in SUPPORTED_FAMILIES:
        raise LowerBoundExperimentError(f"LOWER_BOUND_FAMILY_UNSUPPORTED:{family or 'MISSING'}")
    return family


def _ood_state(value: Any) -> str:
    state = str(value or "").strip().upper()
    if state not in OOD_STATES:
        raise LowerBoundExperimentError(f"LOWER_BOUND_OOD_STATE_INVALID:{state or 'MISSING'}")
    return state


def _settlement(value: Any) -> str:
    if value is None:
        raise LowerBoundExperimentError("LOWER_BOUND_SETTLEMENT_MISSING")
    if value in (1, True):
        return "WIN"
    if value in (0, False):
        return "LOSS"
    text = str(value).strip().upper()
    if text in {"WIN", "HIT", "COVER"}:
        return "WIN"
    if text in {"LOSS", "MISS", "NO_COVER", "NOT_COVER"}:
        return "LOSS"
    if text == "PUSH":
        return "PUSH"
    raise LowerBoundExperimentError(f"LOWER_BOUND_SETTLEMENT_INVALID:{text}")


def _binary_outcome(parsed: Mapping[str, Any]) -> int | None:
    settlement = parsed.get("settlement")
    if settlement == "WIN":
        return 1
    if settlement == "LOSS":
        return 0
    if settlement == "PUSH":
        if parsed.get("probability_semantics") == "CONDITIONAL_ON_NO_PUSH":
            return None
        if parsed.get("probability_semantics") == "UNCONDITIONAL_WITH_PUSH_MASS":
            return 0
    raise LowerBoundExperimentError("LOWER_BOUND_BINARY_SCORING_SEMANTICS_INVALID")


def _validate_probability_row(row: Mapping[str, Any], *, require_settlement: bool) -> dict[str, Any]:
    family = _family(row.get("family"))
    lane_key = str(row.get("lane_key") or "").strip()
    if not lane_key:
        raise LowerBoundExperimentError("LOWER_BOUND_LANE_KEY_MISSING")

    point = _probability(row.get("calibrated_probability"), field="CALIBRATED_PROBABILITY")
    lower = _probability(row.get("calibrated_lower_bound"), field="CALIBRATED_LOWER_BOUND")
    if lower > point:
        raise LowerBoundExperimentError("MODEL_OUTPUT_INVALID:LOWER_BOUND_ABOVE_POINT")

    upper_raw = row.get("calibrated_upper_bound")
    upper = None if upper_raw is None else _probability(upper_raw, field="CALIBRATED_UPPER_BOUND")
    if upper is not None and upper < point:
        raise LowerBoundExperimentError("MODEL_OUTPUT_INVALID:UPPER_BOUND_BELOW_POINT")

    if bool(row.get("market_probability_substitution_used", False)):
        raise LowerBoundExperimentError("GOVERNANCE_MARKET_PROBABILITY_SUBSTITUTION_FORBIDDEN")

    ood_state = _ood_state(row.get("ood_state"))
    semantics = str(row.get("probability_semantics") or "BINARY_OUTCOME").strip().upper()
    if semantics not in PROBABILITY_SEMANTICS:
        raise LowerBoundExperimentError(f"LOWER_BOUND_PROBABILITY_SEMANTICS_INVALID:{semantics}")

    push_possible = bool(row.get("push_possible", False))
    if push_possible and semantics == "BINARY_OUTCOME":
        raise LowerBoundExperimentError(
            "LOWER_BOUND_PUSH_CAPABLE_ROW_REQUIRES_PUSH_AWARE_SEMANTICS"
        )

    push_probability_raw = row.get("push_probability")
    push_probability = None
    if semantics == "UNCONDITIONAL_WITH_PUSH_MASS":
        if push_probability_raw is None:
            raise LowerBoundExperimentError("LOWER_BOUND_PUSH_PROBABILITY_REQUIRED")
        push_probability = _probability(
            push_probability_raw,
            field="PUSH_PROBABILITY",
        )
        if point + push_probability > 1.0 + 1e-12:
            raise LowerBoundExperimentError(
                "MODEL_OUTPUT_INVALID:SELECTED_PLUS_PUSH_PROBABILITY_GT_ONE"
            )
    elif push_probability_raw is not None:
        push_probability = _probability(
            push_probability_raw,
            field="PUSH_PROBABILITY",
        )

    settlement = None
    has_settlement = "outcome" in row or "settlement" in row
    if require_settlement or has_settlement:
        settlement = _settlement(row.get("settlement", row.get("outcome")))
        if settlement == "PUSH" and semantics == "BINARY_OUTCOME":
            raise LowerBoundExperimentError(
                "LOWER_BOUND_PUSH_REQUIRES_PUSH_AWARE_SEMANTICS"
            )

    support_n_raw = row.get("support_n")
    support_n = None
    if support_n_raw is not None:
        try:
            support_n = int(support_n_raw)
        except (TypeError, ValueError) as exc:
            raise LowerBoundExperimentError("LOWER_BOUND_SUPPORT_N_INVALID") from exc
        if support_n < 0:
            raise LowerBoundExperimentError("LOWER_BOUND_SUPPORT_N_INVALID")

    support_distance_raw = row.get("support_distance")
    support_distance = None
    if support_distance_raw is not None:
        try:
            support_distance = float(support_distance_raw)
        except (TypeError, ValueError) as exc:
            raise LowerBoundExperimentError("LOWER_BOUND_SUPPORT_DISTANCE_INVALID") from exc
        if not isfinite(support_distance) or support_distance < 0.0:
            raise LowerBoundExperimentError("LOWER_BOUND_SUPPORT_DISTANCE_INVALID")

    return {
        "family": family,
        "lane_key": lane_key,
        "point": point,
        "lower": lower,
        "upper": upper,
        "ood_state": ood_state,
        "probability_semantics": semantics,
        "push_possible": push_possible,
        "push_probability": push_probability,
        "settlement": settlement,
        "support_n": support_n,
        "support_distance": support_distance,
    }


def required_evidence_for_family(family: str) -> tuple[str, ...]:
    return REQUIRED_LANE_EVIDENCE[_family(family)]


def wilson_lower(successes: int, n: int, *, z: float = 1.645) -> float:
    """One-sided Wilson lower bound used only as a research estimator component."""
    if n <= 0:
        raise LowerBoundExperimentError("LOWER_BOUND_WILSON_SAMPLE_EMPTY")
    if successes < 0 or successes > n:
        raise LowerBoundExperimentError("LOWER_BOUND_WILSON_SUCCESS_COUNT_INVALID")
    if not isfinite(float(z)) or float(z) <= 0.0:
        raise LowerBoundExperimentError("LOWER_BOUND_WILSON_Z_INVALID")
    p = successes / n
    zz = float(z) * float(z)
    denominator = 1.0 + zz / n
    center = p + zz / (2.0 * n)
    adjustment = float(z) * sqrt((p * (1.0 - p) + zz / (4.0 * n)) / n)
    return max(0.0, (center - adjustment) / denominator)


def local_reliability_bound(
    *,
    lane_key: str,
    point_probability: float,
    historical_rows: Sequence[Mapping[str, Any]],
    candidate_as_of: str,
    min_effective_n: int = 30,
    max_neighbors: int = 100,
    max_probability_distance: float = 0.08,
    max_support_distance: float | None = None,
    include_near_ood: bool = False,
    wilson_z: float = 1.645,
) -> LocalReliabilityBound:
    """Estimate a local empirical bound with both sample and locality constraints.

    Locality is never inferred across lanes. Rows must match the exact lane_key.
    Historical evidence is point-in-time safe: both the historical prediction
    and its outcome availability timestamp must be strictly earlier than the
    candidate as-of instant. Optional support_distance is computed upstream by
    the controlling lane and can represent lane-certified feature-space support
    or leverage.
    """
    lane_key = str(lane_key or "").strip()
    if not lane_key:
        raise LowerBoundExperimentError("LOWER_BOUND_LANE_KEY_MISSING")
    point = _probability(point_probability, field="CANDIDATE_POINT_PROBABILITY")
    as_of = _aware_datetime(candidate_as_of, field="CANDIDATE_AS_OF")
    if min_effective_n < 1 or max_neighbors < min_effective_n:
        raise LowerBoundExperimentError("LOWER_BOUND_LOCAL_SAMPLE_POLICY_INVALID")
    if not 0.0 < float(max_probability_distance) <= 1.0:
        raise LowerBoundExperimentError("LOWER_BOUND_LOCAL_PROBABILITY_DISTANCE_INVALID")
    if max_support_distance is not None and (
        not isfinite(float(max_support_distance)) or float(max_support_distance) < 0.0
    ):
        raise LowerBoundExperimentError("LOWER_BOUND_LOCAL_SUPPORT_DISTANCE_INVALID")

    allowed_ood = {"IN_DISTRIBUTION"}
    if include_near_ood:
        allowed_ood.add("NEAR_OOD")

    candidates: list[tuple[float, float, int, float | None, str]] = []
    for raw in historical_rows:
        prediction_at = _aware_datetime(
            raw.get("prediction_timestamp"),
            field="HISTORY_PREDICTION",
        )
        outcome_available_at = _aware_datetime(
            raw.get("outcome_available_at"),
            field="HISTORY_OUTCOME_AVAILABLE",
        )
        if prediction_at >= as_of or outcome_available_at >= as_of:
            continue
        if outcome_available_at < prediction_at:
            raise LowerBoundExperimentError(
                "LOWER_BOUND_HISTORY_OUTCOME_PRECEDES_PREDICTION"
            )

        parsed = _validate_probability_row(raw, require_settlement=True)
        if parsed["lane_key"] != lane_key:
            continue
        binary_outcome = _binary_outcome(parsed)
        if binary_outcome is None:
            continue
        if parsed["ood_state"] not in allowed_ood:
            continue
        probability_distance = abs(parsed["point"] - point)
        if probability_distance > float(max_probability_distance):
            continue
        support_distance = parsed["support_distance"]
        if max_support_distance is not None:
            if support_distance is None or support_distance > float(max_support_distance):
                continue
        candidates.append(
            (
                probability_distance,
                parsed["point"],
                int(binary_outcome),
                support_distance,
                parsed["ood_state"],
            )
        )

    candidates.sort(key=lambda item: (item[0], item[1], item[2]))
    selected = candidates[:max_neighbors]
    if len(selected) < min_effective_n:
        raise LowerBoundExperimentError(
            f"LOWER_BOUND_LOCAL_SUPPORT_INSUFFICIENT:{len(selected)}:{min_effective_n}"
        )

    successes = sum(item[2] for item in selected)
    raw_lower = wilson_lower(successes, len(selected), z=wilson_z)
    support_distances = [item[3] for item in selected if item[3] is not None]
    return LocalReliabilityBound(
        lane_key=lane_key,
        point_probability=point,
        lower_bound=min(point, raw_lower),
        successes=successes,
        sample_n=len(selected),
        max_probability_distance_used=max(item[0] for item in selected),
        max_support_distance_used=max(support_distances) if support_distances else None,
        wilson_z=float(wilson_z),
        included_ood_states=tuple(sorted({item[4] for item in selected})),
    )


def empirical_quantile(values: Sequence[float], *, quantile: float) -> float:
    if not values:
        raise LowerBoundExperimentError("LOWER_BOUND_EMPIRICAL_SAMPLE_EMPTY")
    if not 0.0 <= float(quantile) <= 1.0:
        raise LowerBoundExperimentError("LOWER_BOUND_EMPIRICAL_QUANTILE_INVALID")
    parsed = sorted(_probability(value, field="EMPIRICAL_BOUND_SAMPLE") for value in values)
    index = int(float(quantile) * (len(parsed) - 1))
    return parsed[index]


def composite_lower_bound(point_probability: float, components: Mapping[str, float]) -> dict[str, Any]:
    """Combine pre-registered research bounds conservatively without averaging them."""
    point = _probability(point_probability, field="CANDIDATE_POINT_PROBABILITY")
    if not components:
        raise LowerBoundExperimentError("LOWER_BOUND_COMPONENTS_EMPTY")
    parsed: dict[str, float] = {}
    for name, value in components.items():
        key = str(name or "").strip()
        if not key:
            raise LowerBoundExperimentError("LOWER_BOUND_COMPONENT_NAME_MISSING")
        component = _probability(value, field=f"BOUND_COMPONENT:{key}")
        if component > point:
            component = point
        parsed[key] = component
    lower = min([point, *parsed.values()])
    return {
        "point_probability": point,
        "components": parsed,
        "composite_lower_bound": lower,
        "combination_rule": "MIN_PRE_REGISTERED_COMPONENTS",
        "probability_publishable": False,
        "promotion_authorized": False,
        "can_execute": False,
    }


def _brier(probabilities: Sequence[float], outcomes: Sequence[int]) -> float:
    return sum((p - y) ** 2 for p, y in zip(probabilities, outcomes)) / len(outcomes)


def _log_loss(probabilities: Sequence[float], outcomes: Sequence[int]) -> float:
    eps = 1e-12
    total = 0.0
    for p, y in zip(probabilities, outcomes):
        clipped = min(max(p, eps), 1.0 - eps)
        total += -(y * log(clipped) + (1 - y) * log(1.0 - clipped))
    return total / len(outcomes)


def _ece(probabilities: Sequence[float], outcomes: Sequence[int], *, bins: int = 10) -> float:
    if bins < 2:
        raise LowerBoundExperimentError("LOWER_BOUND_ECE_BIN_COUNT_INVALID")
    total = len(outcomes)
    error = 0.0
    for index in range(bins):
        lo = index / bins
        hi = (index + 1) / bins
        members = [
            i
            for i, p in enumerate(probabilities)
            if (lo <= p < hi) or (index == bins - 1 and p == 1.0)
        ]
        if not members:
            continue
        mean_p = sum(probabilities[i] for i in members) / len(members)
        mean_y = sum(outcomes[i] for i in members) / len(members)
        error += (len(members) / total) * abs(mean_p - mean_y)
    return error


def evaluate_bound_reliability(
    rows: Sequence[Mapping[str, Any]],
    *,
    lane_key: str | None = None,
    lower_bound_thresholds: Sequence[float] = (0.50, 0.55, 0.60),
) -> dict[str, Any]:
    """Evaluate point calibration and empirical lower-bound reliability.

    Pushes are reported but excluded from binary proper scores. This is valid
    only for rows explicitly using CONDITIONAL_ON_NO_PUSH semantics.
    """
    parsed_rows: list[dict[str, Any]] = []
    unconditional_rows: list[dict[str, Any]] = []
    push_n = 0
    conditional_pushes_excluded_n = 0
    for raw in rows:
        parsed = _validate_probability_row(raw, require_settlement=True)
        if lane_key is not None and parsed["lane_key"] != str(lane_key):
            continue
        if parsed["settlement"] == "PUSH":
            push_n += 1
        if parsed["probability_semantics"] == "UNCONDITIONAL_WITH_PUSH_MASS":
            unconditional_rows.append(parsed)
        binary_outcome = _binary_outcome(parsed)
        if binary_outcome is None:
            conditional_pushes_excluded_n += 1
            continue
        parsed["binary_outcome"] = int(binary_outcome)
        parsed_rows.append(parsed)

    if not parsed_rows:
        raise LowerBoundExperimentError("INSUFFICIENT_OOS_EVIDENCE:NO_BINARY_SETTLED_ROWS")

    probabilities = [row["point"] for row in parsed_rows]
    lowers = [row["lower"] for row in parsed_rows]
    outcomes = [int(row["binary_outcome"]) for row in parsed_rows]
    hit_rate = sum(outcomes) / len(outcomes)
    mean_lower = sum(lowers) / len(lowers)

    threshold_rows: list[dict[str, Any]] = []
    for threshold_raw in lower_bound_thresholds:
        threshold = _probability(threshold_raw, field="LOWER_BOUND_THRESHOLD")
        indices = [i for i, lower in enumerate(lowers) if lower >= threshold]
        if not indices:
            threshold_rows.append(
                {
                    "threshold": threshold,
                    "n": 0,
                    "hit_rate": None,
                    "mean_lower_bound": None,
                    "reliability_margin": None,
                }
            )
            continue
        selected_hit = sum(outcomes[i] for i in indices) / len(indices)
        selected_lower = sum(lowers[i] for i in indices) / len(indices)
        threshold_rows.append(
            {
                "threshold": threshold,
                "n": len(indices),
                "hit_rate": selected_hit,
                "mean_lower_bound": selected_lower,
                "reliability_margin": selected_hit - selected_lower,
            }
        )

    ood_cohorts: dict[str, dict[str, Any]] = {}
    for state in OOD_STATES:
        indices = [i for i, row in enumerate(parsed_rows) if row["ood_state"] == state]
        if not indices:
            continue
        cohort_hit = sum(outcomes[i] for i in indices) / len(indices)
        cohort_lower = sum(lowers[i] for i in indices) / len(indices)
        ood_cohorts[state] = {
            "n": len(indices),
            "hit_rate": cohort_hit,
            "mean_lower_bound": cohort_lower,
            "reliability_margin": cohort_hit - cohort_lower,
        }

    multiclass_brier_score = None
    multiclass_log_loss = None
    if unconditional_rows:
        brier_total = 0.0
        log_total = 0.0
        eps = 1e-12
        for row in unconditional_rows:
            p_win = row["point"]
            p_push = row["push_probability"]
            p_loss = max(0.0, 1.0 - p_win - p_push)
            if row["settlement"] == "WIN":
                target = (1.0, 0.0, 0.0)
                observed_probability = p_win
            elif row["settlement"] == "PUSH":
                target = (0.0, 1.0, 0.0)
                observed_probability = p_push
            else:
                target = (0.0, 0.0, 1.0)
                observed_probability = p_loss
            brier_total += (
                (p_win - target[0]) ** 2
                + (p_push - target[1]) ** 2
                + (p_loss - target[2]) ** 2
            )
            log_total += -log(min(max(observed_probability, eps), 1.0))
        multiclass_brier_score = brier_total / len(unconditional_rows)
        multiclass_log_loss = log_total / len(unconditional_rows)

    return {
        "experiment_version": EXPERIMENT_VERSION,
        "lane_key": lane_key,
        "binary_settled_n": len(outcomes),
        "push_n": push_n,
        "conditional_pushes_excluded_n": conditional_pushes_excluded_n,
        "unconditional_three_way_n": len(unconditional_rows),
        "multiclass_brier_score": multiclass_brier_score,
        "multiclass_log_loss": multiclass_log_loss,
        "mean_calibrated_probability": sum(probabilities) / len(probabilities),
        "mean_calibrated_lower_bound": mean_lower,
        "observed_hit_rate": hit_rate,
        "lower_bound_reliability_margin": hit_rate - mean_lower,
        "brier_score": _brier(probabilities, outcomes),
        "log_loss": _log_loss(probabilities, outcomes),
        "ece_10": _ece(probabilities, outcomes, bins=10),
        "threshold_reliability": threshold_rows,
        "ood_cohorts": ood_cohorts,
        "probability_publishable": False,
        "promotion_authorized": False,
        "can_execute": False,
    }


def evaluate_research_eligibility(
    row: Mapping[str, Any],
    policy: ResearchEligibilityPolicy,
) -> dict[str, Any]:
    """Apply a pre-registered challenger policy after lane-specific gates."""
    parsed = _validate_probability_row(row, require_settlement=False)
    blockers: list[str] = []

    hard_blockers = tuple(str(value) for value in (row.get("upstream_hard_blockers") or ()))
    if hard_blockers:
        blockers.extend(f"UPSTREAM_HARD_BLOCKED:{value}" for value in hard_blockers)

    if row.get("lane_specific_gate_pass") is not True:
        blockers.append("LANE_SPECIFIC_GATE_NOT_PASS")

    if parsed["ood_state"] == "OOD_BLOCKED":
        blockers.append("OOD_BLOCKED")
    elif parsed["ood_state"] == "NEAR_OOD" and not policy.allow_near_ood:
        blockers.append("NEAR_OOD_NOT_ALLOWED_BY_POLICY")

    if policy.min_support_n is not None:
        if policy.min_support_n < 1:
            raise LowerBoundExperimentError("LOWER_BOUND_POLICY_MIN_SUPPORT_N_INVALID")
        if parsed["support_n"] is None:
            blockers.append("SUPPORT_N_MISSING")
        elif parsed["support_n"] < policy.min_support_n:
            blockers.append("SUPPORT_N_BELOW_POLICY_MINIMUM")

    if policy.min_point_probability is not None:
        threshold = _probability(policy.min_point_probability, field="POLICY_MIN_POINT_PROBABILITY")
        if parsed["point"] < threshold:
            blockers.append("POINT_PROBABILITY_BELOW_POLICY_MINIMUM")

    if policy.min_lower_bound is not None:
        threshold = _probability(policy.min_lower_bound, field="POLICY_MIN_LOWER_BOUND")
        if parsed["lower"] < threshold:
            blockers.append("LOWER_BOUND_BELOW_POLICY_MINIMUM")

    if policy.max_interval_width is not None:
        width_limit = _probability(policy.max_interval_width, field="POLICY_MAX_INTERVAL_WIDTH")
        if parsed["upper"] is None:
            blockers.append("UPPER_BOUND_MISSING_FOR_WIDTH_POLICY")
        else:
            width = parsed["upper"] - parsed["lower"]
            if width > width_limit:
                blockers.append("INTERVAL_WIDTH_ABOVE_POLICY_MAXIMUM")

    return {
        "policy": policy.name,
        "family": parsed["family"],
        "lane_key": parsed["lane_key"],
        "eligible": not blockers,
        "blockers": tuple(blockers),
        "calibrated_probability": parsed["point"],
        "calibrated_lower_bound": parsed["lower"],
        "ood_state": parsed["ood_state"],
        "support_n": parsed["support_n"],
        "probability_publishable": False,
        "promotion_authorized": False,
        "rank_eligible": False,
        "can_execute": False,
        "terminal_authority": TERMINAL_AUTHORITY,
    }


def run_policy_ablation(
    rows: Sequence[Mapping[str, Any]],
    policies: Sequence[ResearchEligibilityPolicy],
) -> dict[str, Any]:
    """Compare challenger eligibility policies without changing production gates."""
    if not policies:
        raise LowerBoundExperimentError("LOWER_BOUND_POLICY_ABLATION_EMPTY")
    results: list[dict[str, Any]] = []
    for policy in policies:
        admitted: list[tuple[dict[str, Any], dict[str, Any]]] = []
        for raw in rows:
            decision = evaluate_research_eligibility(raw, policy)
            if decision["eligible"]:
                admitted.append((decision, _validate_probability_row(raw, require_settlement=True)))

        settled: list[tuple[dict[str, Any], dict[str, Any], int]] = []
        push_n = 0
        for decision, parsed in admitted:
            if parsed["settlement"] == "PUSH":
                push_n += 1
            binary_outcome = _binary_outcome(parsed)
            if binary_outcome is not None:
                settled.append((decision, parsed, int(binary_outcome)))
        probabilities = [item[1]["point"] for item in settled]
        lowers = [item[1]["lower"] for item in settled]
        outcomes = [item[2] for item in settled]
        metrics: dict[str, Any] = {
            "policy": policy.name,
            "admitted_n": len(admitted),
            "binary_settled_n": len(settled),
            "push_n": push_n,
            "hit_rate": None,
            "mean_probability": None,
            "mean_lower_bound": None,
            "lower_bound_reliability_margin": None,
            "brier_score": None,
        }
        if outcomes:
            hit_rate = sum(outcomes) / len(outcomes)
            mean_lower = sum(lowers) / len(lowers)
            metrics.update(
                {
                    "hit_rate": hit_rate,
                    "mean_probability": sum(probabilities) / len(probabilities),
                    "mean_lower_bound": mean_lower,
                    "lower_bound_reliability_margin": hit_rate - mean_lower,
                    "brier_score": _brier(probabilities, outcomes),
                }
            )
        results.append(metrics)

    return {
        "experiment_version": EXPERIMENT_VERSION,
        "policy_results": results,
        "production_policy_mutated": False,
        "probability_publishable": False,
        "promotion_authorized": False,
        "can_execute": False,
    }


def research_manifest() -> dict[str, Any]:
    return {
        "experiment_version": EXPERIMENT_VERSION,
        "supported_families": list(SUPPORTED_FAMILIES),
        "required_lane_evidence": {
            family: list(evidence)
            for family, evidence in REQUIRED_LANE_EVIDENCE.items()
        },
        "lower_bound_semantic_target": (
            "SHARPEST_PRE_REGISTERED_ONE_SIDED_DECISION_BOUND_WITH_EMPIRICAL_"
            "OUT_OF_TIME_RELIABILITY"
        ),
        "lane_specific_probability_math_required": True,
        "market_probability_substitution_allowed": MARKET_PROBABILITY_SUBSTITUTION_ALLOWED,
        "probability_publishable": PROBABILITY_PUBLISHABLE,
        "promotion_authorized": PROMOTION_AUTHORIZED,
        "rank_eligible": RANK_ELIGIBLE,
        "terminal_authority": TERMINAL_AUTHORITY,
        "can_execute": CAN_EXECUTE,
    }


__all__ = [
    "CAN_EXECUTE",
    "EXPERIMENT_VERSION",
    "LocalReliabilityBound",
    "LowerBoundExperimentError",
    "MARKET_PROBABILITY_SUBSTITUTION_ALLOWED",
    "OOD_STATES",
    "PROBABILITY_PUBLISHABLE",
    "PROMOTION_AUTHORIZED",
    "RANK_ELIGIBLE",
    "REQUIRED_LANE_EVIDENCE",
    "ResearchEligibilityPolicy",
    "SUPPORTED_FAMILIES",
    "TERMINAL_AUTHORITY",
    "composite_lower_bound",
    "empirical_quantile",
    "evaluate_bound_reliability",
    "evaluate_research_eligibility",
    "local_reliability_bound",
    "required_evidence_for_family",
    "research_manifest",
    "run_policy_ablation",
    "wilson_lower",
]
