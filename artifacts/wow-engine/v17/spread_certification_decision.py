"""Fail-closed certification evidence packet for V17 spread/run-line challengers.

This module does not certify or publish a model.  It only determines whether the
sport-specific evidence package is structurally complete enough for independent
certification review under an explicitly ratified policy.

No default numerical promotion thresholds are invented here.  A policy must be
provided and must be RATIFIED before metric gates can be evaluated.
"""
from __future__ import annotations

from math import isfinite
from typing import Any, Mapping

CAN_EXECUTE = False
PROBABILITY_PUBLISHABLE = False
AUTOMATIC_CERTIFICATION = False
AUTOMATIC_PROMOTION = False
GLOBAL_TERMINAL_REDUCER = "V17_TERMINAL_REDUCER"

READY = "CERTIFICATION_EVIDENCE_READY_FOR_REVIEW"
REMAIN_SHADOW = "REMAIN_SHADOW_MORE_EVIDENCE_REQUIRED"

_REQUIRED_REVIEW_GATES = (
    "leakage_source_manifest_audit_passed",
    "counterexample_review_complete",
    "season_regime_review_complete",
    "calibration_reliability_review_complete",
    "tail_review_complete",
    "push_review_complete",
    "holdout_validation_complete",
    "regression_complete",
)

_REQUIRED_POLICY_THRESHOLDS = (
    "min_exact_line_n",
    "min_exact_line_coverage",
    "max_cover_brier",
    "max_cover_log_loss",
    "max_cover_ece",
    "max_three_way_brier",
)


def _number(value: Any) -> float | None:
    if isinstance(value, bool):
        return None
    try:
        out = float(value)
    except (TypeError, ValueError):
        return None
    return out if isfinite(out) else None


def _boolean_false(receipt: Mapping[str, Any], key: str) -> bool:
    return receipt.get(key) is False


def _normalized_triplet(receipt: Mapping[str, Any]) -> bool:
    values = [_number(receipt.get(key)) for key in ("p_cover", "p_push", "p_not_cover")]
    return all(value is not None and 0.0 <= value <= 1.0 for value in values) and abs(sum(values) - 1.0) <= 1e-9


def build_spread_certification_decision_packet(
    *,
    sport: str,
    historical_receipt: Mapping[str, Any],
    forward_receipt: Mapping[str, Any],
    review_evidence: Mapping[str, Any],
    policy: Mapping[str, Any] | None,
) -> dict[str, Any]:
    """Return evidence readiness only; never change certification/publication state."""
    normalized_sport = str(sport or "").strip().upper()
    blockers: list[str] = []

    if normalized_sport not in {"MLB", "NFL", "WNBA"}:
        blockers.append("SPREAD_CERTIFICATION:SPORT_UNSUPPORTED")

    if not policy or str(policy.get("status") or "").upper() != "RATIFIED":
        blockers.append("SPREAD_CERTIFICATION:POLICY_UNRATIFIED")
        policy = policy or {}
    else:
        policy_sport = str(policy.get("sport") or "ALL").upper()
        if policy_sport not in {"ALL", normalized_sport}:
            blockers.append("SPREAD_CERTIFICATION:POLICY_SPORT_MISMATCH")
        for key in _REQUIRED_POLICY_THRESHOLDS:
            if _number(policy.get(key)) is None:
                blockers.append(f"SPREAD_CERTIFICATION:POLICY_THRESHOLD_MISSING:{key}")

    metrics = historical_receipt.get("exact_line_metrics")
    if not isinstance(metrics, Mapping):
        metrics = historical_receipt.get("historical_line_metrics")
    if not isinstance(metrics, Mapping):
        blockers.append("SPREAD_CERTIFICATION:HISTORICAL_EXACT_LINE_METRICS_REQUIRED")
        metrics = {}

    evidence_n = _number(metrics.get("evidence_row_n"))
    coverage = _number(metrics.get("exact_line_coverage"))
    cover_brier = _number(metrics.get("cover_brier"))
    cover_log_loss = _number(metrics.get("cover_log_loss"))
    cover_ece = _number(metrics.get("cover_ece"))
    three_way_brier = _number(metrics.get("three_way_brier"))

    required_metrics = {
        "evidence_row_n": evidence_n,
        "exact_line_coverage": coverage,
        "cover_brier": cover_brier,
        "cover_log_loss": cover_log_loss,
        "cover_ece": cover_ece,
        "three_way_brier": three_way_brier,
    }
    for key, value in required_metrics.items():
        if value is None:
            blockers.append(f"SPREAD_CERTIFICATION:HISTORICAL_METRIC_MISSING:{key}")

    # Historical close proxies are allowed as certification/replay evidence only.
    # They never become immutable live-card quote receipts.
    evidence_class = str(
        historical_receipt.get("evidence_class")
        or metrics.get("evaluation_mode")
        or ""
    ).upper()
    allowed_classes = {
        "PROVIDER_BOUND_EXACT_SPREAD",
        "NFLVERSE_HISTORICAL_CLOSE_PROXY",
        "ESPN_HISTORICAL_CLOSE_PROXY",
        "ESPN_HISTORICAL_RUN_LINE_CLOSE_PROXY",
    }
    if evidence_class not in allowed_classes:
        blockers.append("SPREAD_CERTIFICATION:HISTORICAL_EVIDENCE_CLASS_UNREVIEWED")

    for key in ("market_features_used", "spread_line_used_as_feature", "market_probability_substitution_used", "moneyline_probability_used"):
        value = historical_receipt.get(key, metrics.get(key))
        if value is not False:
            blockers.append(f"SPREAD_CERTIFICATION:INVARIANT_VIOLATION:{key}")

    if not _normalized_triplet(forward_receipt):
        blockers.append("SPREAD_CERTIFICATION:FORWARD_PROBABILITY_TRIPLET_INVALID")
    if forward_receipt.get("can_execute") is not False:
        blockers.append("SPREAD_CERTIFICATION:CAN_EXECUTE_INVARIANT_VIOLATION")
    if forward_receipt.get("probability_publishable") is not False:
        blockers.append("SPREAD_CERTIFICATION:FORWARD_RECEIPT_PREMATURELY_PUBLISHABLE")
    if forward_receipt.get("automatic_certification") is not False:
        blockers.append("SPREAD_CERTIFICATION:AUTOMATIC_CERTIFICATION_MUST_REMAIN_FALSE")
    if forward_receipt.get("automatic_promotion") is not False:
        blockers.append("SPREAD_CERTIFICATION:AUTOMATIC_PROMOTION_MUST_REMAIN_FALSE")

    for key in _REQUIRED_REVIEW_GATES:
        if review_evidence.get(key) is not True:
            blockers.append(f"SPREAD_CERTIFICATION:REVIEW_GATE_INCOMPLETE:{key}")

    # Numerical acceptance is evaluated only against a ratified policy.
    if not blockers or all(not blocker.startswith("SPREAD_CERTIFICATION:POLICY_") for blocker in blockers):
        if str(policy.get("status") or "").upper() == "RATIFIED":
            comparisons = (
                (evidence_n, _number(policy.get("min_exact_line_n")), ">=", "exact_line_n"),
                (coverage, _number(policy.get("min_exact_line_coverage")), ">=", "exact_line_coverage"),
                (cover_brier, _number(policy.get("max_cover_brier")), "<=", "cover_brier"),
                (cover_log_loss, _number(policy.get("max_cover_log_loss")), "<=", "cover_log_loss"),
                (cover_ece, _number(policy.get("max_cover_ece")), "<=", "cover_ece"),
                (three_way_brier, _number(policy.get("max_three_way_brier")), "<=", "three_way_brier"),
            )
            for observed, threshold, op, name in comparisons:
                if observed is None or threshold is None:
                    continue
                passed = observed >= threshold if op == ">=" else observed <= threshold
                if not passed:
                    blockers.append(f"SPREAD_CERTIFICATION:POLICY_GATE_FAILED:{name}")

    blockers = list(dict.fromkeys(blockers))
    ready = not blockers
    return {
        "status": READY if ready else REMAIN_SHADOW,
        "sport": normalized_sport or None,
        "policy_id": policy.get("policy_id"),
        "policy_status": policy.get("status"),
        "historical_evidence_class": evidence_class or None,
        "historical_metrics": required_metrics,
        "forward_canary_status": forward_receipt.get("status"),
        "independent_review_required": True,
        "publication_decision": "UNDECIDED_REQUIRES_INDEPENDENT_REVIEW" if ready else "REMAIN_SHADOW",
        "blockers": blockers,
        "automatic_certification": False,
        "automatic_promotion": False,
        "probability_publishable": False,
        "rank_eligible": False,
        "global_terminal_reducer": GLOBAL_TERMINAL_REDUCER,
        "can_execute": False,
    }


__all__ = [
    "AUTOMATIC_CERTIFICATION",
    "AUTOMATIC_PROMOTION",
    "CAN_EXECUTE",
    "GLOBAL_TERMINAL_REDUCER",
    "PROBABILITY_PUBLISHABLE",
    "READY",
    "REMAIN_SHADOW",
    "build_spread_certification_decision_packet",
]
