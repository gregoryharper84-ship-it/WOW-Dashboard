"""V17 cross-workstream final integration acceptance contract.

This module is intentionally provider-neutral.  It validates the handoff among:
A) sporting-probability matchup/opponent-context adjustment,
B) exact-vs-adjacent market-evidence governance, and
C) duplicate-thesis portfolio construction.

It does not score bets and can never authorize execution.
"""
from __future__ import annotations

from dataclasses import dataclass
from typing import Any, Mapping

CAN_EXECUTE = False
EXECUTION_RULE = "DRY_RUN_ONLY_NO_LIVE_TRADING_NO_MARKET_ORDERS"


class V17IntegrationContractError(AssertionError):
    """Raised when a combined V17 result violates a cross-workstream invariant."""


@dataclass(frozen=True)
class IntegrationAcceptance:
    passed: bool
    checks: tuple[str, ...]
    can_execute: bool = False


def _float(payload: Mapping[str, Any], key: str) -> float | None:
    value = payload.get(key)
    if value is None:
        return None
    try:
        return float(value)
    except (TypeError, ValueError) as exc:
        raise V17IntegrationContractError(f"INVALID_NUMERIC_FIELD:{key}") from exc


def _assert_probability_tuple(payload: Mapping[str, Any], prefix: str) -> tuple[float, float, float]:
    raw = _float(payload, "model_probability")
    calibrated = _float(payload, "calibrated_probability")
    lower = _float(payload, "calibrated_lower_bound")
    if raw is None or calibrated is None or lower is None:
        raise V17IntegrationContractError(f"{prefix}:PROBABILITY_PACKAGE_INCOMPLETE")
    if not (0.0 <= lower <= calibrated <= 1.0 and 0.0 <= raw <= 1.0):
        raise V17IntegrationContractError(f"{prefix}:PROBABILITY_PACKAGE_INVALID")
    return raw, calibrated, lower


def validate_final_integration(
    *,
    baseline_probability: Mapping[str, Any],
    matchup_adjusted_probability: Mapping[str, Any],
    market_audit: Mapping[str, Any],
    portfolio_leg_before: Mapping[str, Any],
    portfolio_leg_after: Mapping[str, Any],
    require_material_suppression: bool = True,
) -> IntegrationAcceptance:
    """Validate the final A+B+C handoff without re-scoring anything.

    Required semantics:
    - opponent/matchup contradiction may reduce the sporting probability once;
    - adjacent-line evidence cannot claim exact-line confirmation;
    - portfolio duplicate-thesis handling cannot mutate sporting probability;
    - no layer may enable execution.
    """
    checks: list[str] = []

    base_raw, base_cal, base_lb = _assert_probability_tuple(baseline_probability, "BASELINE")
    adj_raw, adj_cal, adj_lb = _assert_probability_tuple(matchup_adjusted_probability, "MATCHUP")

    if require_material_suppression:
        if not (adj_cal < base_cal and adj_lb < base_lb):
            raise V17IntegrationContractError("MATCHUP_CONTRADICTION_NOT_NUMERICALLY_REFLECTED")
    if matchup_adjusted_probability.get("suppression_applied_count") not in (None, 1):
        raise V17IntegrationContractError("MATCHUP_SUPPRESSION_DOUBLE_COUNTED")
    checks.append("sporting_probability_adjusted_once")

    evidence_class = str(market_audit.get("evidence_class") or "").upper()
    exact_confirmed = market_audit.get("exact_line_confirmed")
    if evidence_class == "ADJACENT_LINE" and exact_confirmed is True:
        raise V17IntegrationContractError("ADJACENT_LINE_IMPROPERLY_GRANTED_EXACT_AUTHORITY")
    checks.append("adjacent_line_not_exact_authority")

    before = _assert_probability_tuple(portfolio_leg_before, "PORTFOLIO_BEFORE")
    after = _assert_probability_tuple(portfolio_leg_after, "PORTFOLIO_AFTER")
    if before != after:
        raise V17IntegrationContractError("PORTFOLIO_LAYER_MUTATED_SPORTING_PROBABILITY")
    checks.append("portfolio_probability_immutable")

    # The portfolio handoff must receive the already-adjusted model package.  If
    # caller provides the same leg as matchup_adjusted_probability, these values
    # must remain identical after construction; this is the double-penalty guard.
    adjusted_tuple = (adj_raw, adj_cal, adj_lb)
    if before != adjusted_tuple:
        raise V17IntegrationContractError("PORTFOLIO_INPUT_NOT_POST_MATCHUP_PROBABILITY_PACKAGE")
    checks.append("no_cross_layer_double_penalty")

    for payload_name, payload in (
        ("baseline", baseline_probability),
        ("matchup", matchup_adjusted_probability),
        ("market", market_audit),
        ("portfolio_before", portfolio_leg_before),
        ("portfolio_after", portfolio_leg_after),
    ):
        if payload.get("can_execute") is True:
            raise V17IntegrationContractError(f"EXECUTION_AUTHORITY_VIOLATION:{payload_name}")
    checks.append("can_execute_false")

    return IntegrationAcceptance(passed=True, checks=tuple(checks), can_execute=False)
