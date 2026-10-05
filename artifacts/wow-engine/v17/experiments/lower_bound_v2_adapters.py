"""Adapters from existing WOW V17 lane artifacts into lower-bound V2 research rows.

Research-only. These adapters do not score sporting probabilities and do not
change any production route. They preserve the controlling specialist outputs
and only normalize already-computed probabilities/bounds for #1391 replay and
forward-shadow evaluation.
"""
from __future__ import annotations

from math import isfinite
from typing import Any, Mapping, Sequence

CAN_EXECUTE = False
PROBABILITY_PUBLISHABLE = False
PROMOTION_AUTHORIZED = False
RANK_ELIGIBLE = False


def _text(value: Any, *, field: str) -> str:
    text = str(value or "").strip()
    if not text:
        raise ValueError(f"LOWER_BOUND_ADAPTER_{field}_MISSING")
    return text


def _number(value: Any, *, field: str) -> float:
    try:
        parsed = float(value)
    except (TypeError, ValueError) as exc:
        raise ValueError(f"LOWER_BOUND_ADAPTER_{field}_INVALID") from exc
    if not isfinite(parsed):
        raise ValueError(f"LOWER_BOUND_ADAPTER_{field}_INVALID")
    return parsed


def _optional_int(value: Any, *, field: str) -> int | None:
    if value is None:
        return None
    try:
        parsed = int(value)
    except (TypeError, ValueError) as exc:
        raise ValueError(f"LOWER_BOUND_ADAPTER_{field}_INVALID") from exc
    if parsed < 0:
        raise ValueError(f"LOWER_BOUND_ADAPTER_{field}_INVALID")
    return parsed


def _base(
    *,
    family: str,
    lane_key: str,
    calibrated_probability: Any,
    calibrated_lower_bound: Any,
    calibrated_upper_bound: Any,
    settlement: Any,
    ood_state: str,
    support_n: int | None,
    support_distance: float | None,
    lane_specific_gate_pass: bool,
    upstream_hard_blockers: Sequence[str],
    market_probability_substitution_used: bool,
    push_possible: bool,
    probability_semantics: str,
    push_probability: float | None,
) -> dict[str, Any]:
    return {
        "family": family,
        "lane_key": _text(lane_key, field="LANE_KEY"),
        "calibrated_probability": _number(
            calibrated_probability,
            field="CALIBRATED_PROBABILITY",
        ),
        "calibrated_lower_bound": _number(
            calibrated_lower_bound,
            field="CALIBRATED_LOWER_BOUND",
        ),
        "calibrated_upper_bound": (
            None
            if calibrated_upper_bound is None
            else _number(calibrated_upper_bound, field="CALIBRATED_UPPER_BOUND")
        ),
        "settlement": settlement,
        "ood_state": _text(ood_state, field="OOD_STATE").upper(),
        "support_n": _optional_int(support_n, field="SUPPORT_N"),
        "support_distance": (
            None
            if support_distance is None
            else _number(support_distance, field="SUPPORT_DISTANCE")
        ),
        "push_possible": bool(push_possible),
        "push_probability": push_probability,
        "probability_semantics": probability_semantics,
        "lane_specific_gate_pass": bool(lane_specific_gate_pass),
        "upstream_hard_blockers": [str(value) for value in upstream_hard_blockers],
        "market_probability_substitution_used": bool(
            market_probability_substitution_used
        ),
        "probability_publishable": False,
        "promotion_authorized": False,
        "rank_eligible": False,
        "can_execute": False,
    }


def adapt_moneyline_shadow(
    record: Mapping[str, Any],
    *,
    lane_key: str,
    settlement: Any,
    ood_state: str,
    support_n: int | None = None,
    local_support_n: int | None = None,
    support_distance: float | None = None,
    lane_specific_gate_pass: bool = True,
    upstream_hard_blockers: Sequence[str] = (),
) -> dict[str, Any]:
    """Normalize a team/event moneyline challenger or governed prediction."""
    lower = record.get("composite_lower_bound")
    if lower is None:
        lower = record.get("calibrated_lower_bound")
    if lower is None:
        lower = record.get("calibrated_probability_lower_bound")

    upper = record.get("calibrated_upper_bound")
    if upper is None:
        upper = record.get("calibrated_probability_upper_bound")

    row = _base(
        family="MONEYLINE",
        lane_key=lane_key,
        calibrated_probability=record.get("calibrated_probability"),
        calibrated_lower_bound=lower,
        calibrated_upper_bound=upper,
        settlement=settlement,
        ood_state=ood_state,
        support_n=local_support_n,
        support_distance=support_distance,
        lane_specific_gate_pass=lane_specific_gate_pass,
        upstream_hard_blockers=upstream_hard_blockers,
        market_probability_substitution_used=bool(
            record.get("market_probability_substitution_used", False)
        ),
        push_possible=False,
        probability_semantics="BINARY_OUTCOME",
        push_probability=0.0,
    )
    row.update(
        {
            "official_event_id": record.get("official_event_id"),
            "selected_participant": record.get("selected_participant"),
            "model_family": record.get("model_family"),
            "model_artifact_version": record.get("model_artifact_version"),
            "calibration_method": record.get("calibration_method"),
            "lower_bound_method": (
                record.get("lower_bound_method")
                or record.get("bounds_method_version")
            ),
        }
    )
    return row


def adapt_spread_shadow(
    record: Mapping[str, Any],
    *,
    lane_key: str,
    settlement: Any,
    ood_state: str,
    support_distance: float | None = None,
    lane_specific_gate_pass: bool = True,
    upstream_hard_blockers: Sequence[str] = (),
) -> dict[str, Any]:
    """Normalize a spread shadow record using its no-push cover probability.

    Existing spread challenger output exposes p_cover, p_push, p_not_cover and
    p_cover_given_no_push. The current research lower bound is defined against
    the no-push cover decision, so pushes must be excluded from its binary
    proper-score evaluation rather than silently counted as losses.
    """
    p_push = _number(record.get("p_push", 0.0), field="SPREAD_PUSH_PROBABILITY")
    distribution_sample_n = _optional_int(
        record.get("distribution_sample_n"),
        field="SPREAD_DISTRIBUTION_SAMPLE_N",
    )
    row = _base(
        family="SPREAD",
        lane_key=lane_key,
        calibrated_probability=record.get("p_cover_given_no_push"),
        calibrated_lower_bound=record.get("research_lower_bound_cover"),
        calibrated_upper_bound=None,
        settlement=settlement,
        ood_state=ood_state,
        support_n=support_n,
        support_distance=support_distance,
        lane_specific_gate_pass=lane_specific_gate_pass,
        upstream_hard_blockers=upstream_hard_blockers,
        market_probability_substitution_used=bool(
            record.get("market_probability_substitution_used", False)
        ),
        push_possible=p_push > 0.0,
        probability_semantics="CONDITIONAL_ON_NO_PUSH",
        push_probability=p_push,
    )
    row.update(
        {
            "sport": record.get("sport"),
            "official_event_id": record.get("official_event_id"),
            "selected_participant": record.get("selected_participant"),
            "spread_line": record.get("spread_line", record.get("home_spread")),
            "p_cover": record.get("p_cover"),
            "p_push": p_push,
            "p_not_cover": record.get("p_not_cover"),
            "distribution_sample_n_source_only": distribution_sample_n,
            "model_program": record.get("model_program"),
            "model_family": record.get("model_family"),
        }
    )
    return row


def adapt_prop_prediction(
    record: Mapping[str, Any],
    *,
    lane_key: str,
    settlement: Any,
    ood_state: str,
    local_support_n: int | None = None,
    support_distance: float | None = None,
    lane_specific_gate_pass: bool,
    upstream_hard_blockers: Sequence[str] = (),
) -> dict[str, Any]:
    """Normalize an immutable player-prop prediction.

    IMPORTANT: effective_sample_size, calibration_training_n, and simulation
    draws are deliberately *not* re-labeled as local empirical support. The
    local_support_n input must come from the separately constructed
    point-in-time reliability neighborhood used by the lower-bound experiment.

    Discrete PMF props expose selected-side probability plus explicit PUSH mass.
    When PUSH mass is positive the selected sporting outcome is evaluated as an
    unconditional three-way WIN/PUSH/LOSS distribution.
    """
    push_probability = _number(
        record.get("push_probability", 0.0),
        field="PROP_PUSH_PROBABILITY",
    )
    market_weight = _number(
        record.get("market_prior_weight", 0.0),
        field="PROP_MARKET_PRIOR_WEIGHT",
    )
    market_substitution = bool(
        record.get("market_probability_substitution_used", False)
    ) or market_weight > 0.0

    row = _base(
        family="PLAYER_PROP",
        lane_key=lane_key,
        calibrated_probability=record.get("calibrated_probability"),
        calibrated_lower_bound=record.get(
            "calibrated_probability_lower_bound",
            record.get("calibrated_lower_bound"),
        ),
        calibrated_upper_bound=record.get(
            "calibrated_probability_upper_bound",
            record.get("calibrated_upper_bound"),
        ),
        settlement=settlement,
        ood_state=ood_state,
        support_n=local_support_n,
        support_distance=support_distance,
        lane_specific_gate_pass=lane_specific_gate_pass,
        upstream_hard_blockers=upstream_hard_blockers,
        market_probability_substitution_used=market_substitution,
        push_possible=push_probability > 0.0,
        probability_semantics=(
            "UNCONDITIONAL_WITH_PUSH_MASS"
            if push_probability > 0.0
            else "BINARY_OUTCOME"
        ),
        push_probability=push_probability,
    )
    row.update(
        {
            "prediction_id": record.get("prediction_id"),
            "event_id": record.get("event_id"),
            "sport": record.get("sport"),
            "player": record.get("player"),
            "team": record.get("team"),
            "opponent": record.get("opponent"),
            "stat_type": record.get("stat_type"),
            "line": record.get("line"),
            "direction": record.get("direction"),
            "controlling_specialist": record.get("controlling_specialist"),
            "model_family": record.get("model_family"),
            "model_artifact_version": record.get("model_artifact_version"),
            "calibration_status": record.get("calibration_status"),
            "bounds_method_version": record.get("bounds_method_version"),
            "effective_sample_size_source_only": record.get("effective_sample_size"),
            "calibration_training_n_source_only": record.get("calibration_training_n"),
        }
    )
    return row


__all__ = [
    "CAN_EXECUTE",
    "PROBABILITY_PUBLISHABLE",
    "PROMOTION_AUTHORIZED",
    "RANK_ELIGIBLE",
    "adapt_moneyline_shadow",
    "adapt_prop_prediction",
    "adapt_spread_shadow",
]
